# -*- coding: utf-8 -*-
"""GM-FUNDING-ACCOUNTING-REWORK-03 · 资金费会计隔离验收套件。

设计原则
────────
1. **每条断言都能失败**：所有断言都对着引擎真实输出，不做「跑通即通过」。
2. **零外部依赖**：不读行情库、不读生产账本、不联网；全部为合成场景。
3. **正反双向**：既验证「已确认口径不受假设影响」，也验证「假设部分确实被记录、
   没有被悄悄丢掉」——否则「隔离」会退化成「忽略」。

运行（工程根）：
    /usr/bin/python3 -m pytest 策略/WyckoffMaster/test_funding_accounting_isolation_03.py -q
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "策略") not in sys.path:
    sys.path.insert(0, str(ROOT / "策略"))

from WyckoffMaster.trading_core import engine as ENG          # noqa: E402
from WyckoffMaster.trading_core import fill as FILL           # noqa: E402
from WyckoffMaster.trading_core import funding as FND         # noqa: E402
from WyckoffMaster.trading_core import ledger as LED          # noqa: E402
from WyckoffMaster.trading_core import policy as POL          # noqa: E402
from WyckoffMaster.trading_core.contracts import (            # noqa: E402
    FUNDING_BUCKET_CONFIRMED, FUNDING_BUCKET_HYPOTHETICAL,
    INTRABAR_ORDER_CONFIRMED, INTRABAR_ORDER_UNKNOWN)
from WyckoffMaster.trading_core.position import Position      # noqa: E402

M15 = 900_000
H = 3_600_000
# 8 小时整点（1792800000 % 28800 == 0），用于假设模型的时间网格
T8 = 1_792_800_000_000
# D3 场景的原始时间基（与隔离候选夹具一致，便于逐位对照 0.09532840755563438）
T0 = 1_792_798_200_000


@dataclass
class ExecParams:
    fee_rate: float = 0.0005
    slippage_bps: float = 5.0
    funding_model: str = "settlement_events"
    initial_equity: float = 5000.0
    enable_trailing_exit: bool = False
    risk_per_trade: float = 0.01
    max_portfolio_risk: float = 0.05
    max_portfolio_margin_pct: float = 0.40
    max_concurrent_trades: int = 10
    max_leverage_per_symbol: float = 3.0


class SignalParams:
    price_step = 0.1
    fee_rate = 0.0005
    slippage_bps = 5.0
    stoploss_s = 0.05
    trail_d = 0.05


def _frames(t0: int, n: int, step: int, stop_low_bar=None, stop_low=94.0,
            stop_price=95.0, sig_bar=1):
    """构造 n 根 bar 的合成行情；`stop_low_bar` 指定哪根 bar 的 low 跌破止损。"""
    ts = [t0 + i * step for i in range(n)]
    df = pd.DataFrame({
        "timestamp": ts,
        "open": [100.0] * n, "high": [101.0] * n,
        "low": [99.0] * n, "close": [100.0] * n,
        "volume": [1000.0] * n, "quote_volume": [100_000.0] * n,
        "funding_rate": [0.0001] * n,
    })
    if stop_low_bar is not None:
        df.loc[stop_low_bar, "low"] = stop_low
    sig = pd.DataFrame({
        "timestamp": [ts[sig_bar]], "confirm_time": [ts[sig_bar] // 1000],
        "signal": [1], "direction": [1], "stop_price": [stop_price],
        "exit_low_curr": [stop_price], "exit_high_curr": [105.0],
        "signal_id": [f"sig_{sig_bar}"],
    })
    return df, sig


def _run(frames, events, *, model="settlement_events", close_at_end=True,
         initial_equity=5000.0):
    df, sig = frames
    pol = POL.ExecutionPolicy(
        signal_params=SignalParams(),
        exec_params=ExecParams(funding_model=model, initial_equity=initial_equity),
        strategy_key="acct03")
    adapter = FILL.SimulatedBarFillAdapter(slippage_bps=5.0, maker_fee_rate=0.0002,
                                           taker_fee_rate=0.0005)
    core = ENG.TradingCore(
        policy=pol, fill_adapter=adapter, signal_fn=lambda *a, **k: sig,
        to_epoch_seconds=lambda ms, u: (ms // 1000).astype(np.int64),
        strategy_key="acct03", strategy_id="ACCT03", strategy_version="V1",
        ledger=LED.InMemoryLedger())
    market = {"TESTUSDT": {"signal_bars": df, "intrabar_bars": df,
                           "funding_events": events, "time_unit": "ms"}}
    return core.run(market, close_open_positions_at_end=close_at_end)


# ══════════════════ 1. 已确认口径：支出方向 ══════════════════

def test_confirmed_expense_reduces_cash_by_exact_amount():
    """真实事件落在持仓期内且先后可证明 ⇒ 进已确认口径，现金按 cost 精确减少。"""
    frames = _frames(T0, 5, M15, stop_low_bar=3)
    # 入场 bar 开盘 +1ms：bar2 相位 A 结算时持仓仍在 ⇒ confirmed
    ev = [{"time_ms": T0 + M15 + 1, "rate": 0.0001, "mark_price": 100.0}]
    res = _run(frames, ev)

    tr = res["trades"][0]
    qty = tr["qty"]
    cost_expected = 100.0 * qty * 0.0001
    assert tr["funding_fee"] == pytest.approx(cost_expected, abs=1e-12), \
        "已确认资金费必须等于 mark × qty × rate"
    assert tr["funding_fee_hypothetical"] == 0.0, "该场景不存在假设部分"
    # 平仓本身是盘中止损（成交时刻不可精确到毫秒），但资金费在**平仓前一根 bar**
    # 的相位 A 就已确认结算，与本 bar 的先后无关 ⇒ 归属仍是 confirmed。
    #
    # REWORK-04 / D3：此断言原为 `INTRABAR_ORDER_UNKNOWN`，与上一行注释自相矛盾
    # （注释说「与本 bar 的先后无关 ⇒ 归属 confirmed」，断言却说顺序不可判）。
    # 该字段的语义是「本笔资金费的会计归属是否有顺序依据」，不是「成交时刻能否
    # 精确到毫秒」。本 bar 内**没有任何结算事件**落进不可判定窗口 ⇒ 无可判之事，
    # 归属依据是相位 A 的已确认结算 ⇒ 必须是 CONFIRMED。
    assert tr["intrabar_order_status"] == INTRABAR_ORDER_CONFIRMED
    assert tr["funding_is_hypothetical"] is False

    # 现金恒等式：初始 + 毛盈亏 − 双边费用 − 已确认资金费
    cash_expected = (5000.0 + tr["gross_pnl"] - tr["entry_fee"] - tr["exit_fee"]
                     - cost_expected)
    assert res["final_cash"] == pytest.approx(cash_expected, abs=1e-9)
    assert res["reconcile"]["cash_identity_ok"] is True


# ══════════════════ 2. 已确认口径：收入方向 ══════════════════

def test_confirmed_income_increases_cash():
    """负费率 ⇒ 账户收取资金费，现金**增加**；符号不得反扣。"""
    frames = _frames(T0, 5, M15, stop_low_bar=3)
    ev = [{"time_ms": T0 + M15 + 1, "rate": -0.0001, "mark_price": 100.0}]
    res = _run(frames, ev)
    tr = res["trades"][0]

    assert tr["funding_fee"] < 0.0, "收入方向必须为负（支出口径为正）"
    assert tr["funding_fee"] == pytest.approx(-100.0 * tr["qty"] * 0.0001, abs=1e-12)
    cash_expected = (5000.0 + tr["gross_pnl"] - tr["entry_fee"] - tr["exit_fee"]
                     - tr["funding_fee"])
    assert res["final_cash"] == pytest.approx(cash_expected, abs=1e-9)
    # 本场景没有假设部分 ⇒ 两条曲线重合；若重合失败说明有费用被误分到假设桶
    assert res["final_cash_hypothetical"] == pytest.approx(res["final_cash"], abs=1e-12)
    assert res["reconcile"]["sum_funding_fee_hypothetical"] == 0.0


# ══════════════════ 3. 缺数据：记缺口，不补 0 ══════════════════

def test_missing_rate_records_gap_and_charges_nothing():
    """事件缺 rate ⇒ 记 funding_gap；**不得**按 0 计费冒充已结算。"""
    frames = _frames(T0, 5, M15, stop_low_bar=3)
    ev = [{"time_ms": T0 + M15 + 1, "rate": None, "mark_price": 100.0}]
    res = _run(frames, ev)
    tr = res["trades"][0]

    assert tr["funding_fee"] == 0.0
    assert tr["funding_fee_hypothetical"] == 0.0
    gaps = [g for g in (res.get("funding_gaps") or [])
            if str(g.get("reason", "")).startswith("funding_event_missing_fields")]
    assert len(gaps) == 1, f"必须记录且只记录 1 条缺口，实际 {len(gaps)}"
    assert "rate" in gaps[0]["reason"]


def test_missing_mark_records_gap_and_charges_nothing():
    frames = _frames(T0, 5, M15, stop_low_bar=3)
    ev = [{"time_ms": T0 + M15 + 1, "rate": 0.0001, "mark_price": None}]
    res = _run(frames, ev)
    tr = res["trades"][0]
    assert tr["funding_fee"] == 0.0
    gaps = [g for g in (res.get("funding_gaps") or [])
            if "mark" in str(g.get("reason", ""))]
    assert len(gaps) == 1


# ══════════════════ 4. 幂等：重复事件只结算一次 ══════════════════

def test_duplicate_event_charged_once():
    """同一事件在列表里出现两次 ⇒ 只结算一次（游标 + 事件身份双重防重）。"""
    frames = _frames(T0, 5, M15, stop_low_bar=3)
    one = {"time_ms": T0 + M15 + 1, "rate": 0.0001, "mark_price": 100.0}
    res_dup = _run(frames, [dict(one), dict(one)])
    res_one = _run(frames, [dict(one)])

    assert res_dup["trades"][0]["funding_fee"] == pytest.approx(
        res_one["trades"][0]["funding_fee"], abs=1e-12)
    assert res_dup["trades"][0]["funding_fee"] > 0.0


def test_event_before_entry_not_charged():
    """事件早于入场 ⇒ 不属于本仓，不得结算。"""
    frames = _frames(T0, 5, M15, stop_low_bar=3)
    ev = [{"time_ms": T0 - M15, "rate": 0.0001, "mark_price": 100.0}]
    res = _run(frames, ev)
    assert res["trades"][0]["funding_fee"] == 0.0
    assert res["trades"][0]["funding_fee_hypothetical"] == 0.0


def test_event_after_exit_not_charged():
    """事件晚于平仓 ⇒ 不在持仓期，不得结算，也不得进假设桶。"""
    frames = _frames(T0, 5, M15, stop_low_bar=2)
    ev = [{"time_ms": T0 + 3 * M15 + 1, "rate": 0.0001, "mark_price": 100.0}]
    res = _run(frames, ev)
    tr = res["trades"][0]
    assert tr["funding_fee"] == 0.0
    assert tr["funding_fee_hypothetical"] == 0.0


# ══════════════════ 5. 迟到/乱序事件按经济时间结算 ══════════════════

def test_out_of_order_events_settle_by_economic_time():
    """事件列表乱序 ⇒ 按**经济时间**判定，不按列表顺序；两笔都必须在持仓期内结算。"""
    frames = _frames(T0, 6, M15, stop_low_bar=4)
    early = {"time_ms": T0 + M15 + 1, "rate": 0.0001, "mark_price": 100.0}
    late = {"time_ms": T0 + 2 * M15 + 1, "rate": 0.0001, "mark_price": 100.0}
    res = _run(frames, [dict(late), dict(early)])       # 故意倒序
    tr = res["trades"][0]
    expected = 100.0 * tr["qty"] * 0.0001 * 2
    assert tr["funding_fee"] == pytest.approx(expected, abs=1e-12), \
        "乱序输入下两笔事件都必须按经济时间结算"


# ══════════════════ 6. 盘中顺序未知：只进假设桶 ══════════════════

def test_intrabar_unknown_never_touches_confirmed():
    """止损与结算先后不可证明 ⇒ 已确认为 0，假设桶记录该笔，现金不受影响。"""
    frames = _frames(T0, 5, M15, stop_low_bar=2)
    ev = [{"time_ms": T0 + 2 * M15 + 1, "rate": 0.0001, "mark_price": 100.0}]
    res = _run(frames, ev)
    tr = res["trades"][0]

    assert tr["intrabar_order_status"] == INTRABAR_ORDER_UNKNOWN
    assert tr["funding_fee"] == 0.0, "已确认资金费必须为 0"
    assert tr["funding_fee_hypothetical"] > 0.0, "假设桶必须记录该笔，不能悄悄丢掉"
    assert tr["net_pnl"] == pytest.approx(
        tr["gross_pnl"] - tr["entry_fee"] - tr["exit_fee"], abs=1e-12), \
        "已实现净盈亏不得扣假设资金费"
    assert tr["net_pnl_hypothetical"] == pytest.approx(
        tr["net_pnl"] - tr["funding_fee_hypothetical"], abs=1e-12)


def test_d3_gap_equals_reference_value():
    """D3 场景差额必须逐位等于 0.09532840755563438 USDT。"""
    frames = _frames(T0, 5, M15, stop_low_bar=2)
    ev = [{"time_ms": T0 + 2 * M15 + 1, "rate": 0.0001, "mark_price": 100.0}]
    res = _run(frames, ev)
    tr = res["trades"][0]

    assert tr["qty"] == pytest.approx(9.532840755563438, rel=0, abs=1e-12)
    assert tr["funding_fee_hypothetical"] == pytest.approx(0.09532840755563438,
                                                           rel=0, abs=1e-15)
    assert res["final_cash"] == pytest.approx(4949.499776097403, abs=1e-9)
    assert res["final_cash_hypothetical"] == pytest.approx(4949.404447689847, abs=1e-9)
    assert (res["final_cash"] - res["final_cash_hypothetical"]) == pytest.approx(
        0.09532840755563438, abs=1e-9)


def test_confirmed_cash_unaffected_when_only_hypothetical_exists():
    """只有假设资金费时，已确认现金必须与「完全没有资金费」完全一致。"""
    frames = _frames(T0, 5, M15, stop_low_bar=2)
    ev = [{"time_ms": T0 + 2 * M15 + 1, "rate": 0.0001, "mark_price": 100.0}]
    with_hyp = _run(frames, ev)
    without = _run(frames, [])
    assert with_hyp["final_cash"] == pytest.approx(without["final_cash"], abs=1e-12)
    assert with_hyp["final_equity"] == pytest.approx(without["final_equity"], abs=1e-12)
    # 但假设情景必须不同 —— 否则「隔离」变成了「丢弃」
    assert with_hyp["final_equity_hypothetical"] < without["final_equity"]


# ══════════════════ 7. 假设模型：一律不进已确认 ══════════════════

def test_assumption_model_never_touches_confirmed_cash():
    """assumption_fixed_8h ⇒ 费用只进假设桶，现金与已确认权益完全不变。"""
    # 入场在 8h 整点 bar（T8），第 9 根 bar（T8+8h）再次落在 8h 整点 ⇒ 触发一次假设计提
    frames = _frames(T8, 9, H, stop_low_bar=None, stop_price=90.0, sig_bar=0)
    res = _run(frames, [], model="assumption_fixed_8h")
    tr = res["trades"][0]

    assert res["funding_is_assumption"] is True
    assert tr["funding_fee"] == 0.0, "假设模型不得进已确认累计"
    assert tr["funding_fee_hypothetical"] != 0.0, "假设模型必须被记录到假设桶"
    assert tr["net_pnl"] == pytest.approx(
        tr["gross_pnl"] - tr["entry_fee"] - tr["exit_fee"], abs=1e-12)
    # 现金恒等式里不含任何资金费
    assert res["final_cash"] == pytest.approx(
        5000.0 + tr["gross_pnl"] - tr["entry_fee"] - tr["exit_fee"], abs=1e-9)


# ══════════════════ 8. 双情景权益对账 ══════════════════

def test_both_scenarios_reconcile():
    """已确认与假设两套恒等式都必须平账，且曲线末点与汇总一致。"""
    frames = _frames(T0, 5, M15, stop_low_bar=2)
    ev = [{"time_ms": T0 + 2 * M15 + 1, "rate": 0.0001, "mark_price": 100.0}]
    res = _run(frames, ev)
    rc = res["reconcile"]

    assert rc["cash_identity_ok"] is True
    assert rc["equity_identity_ok"] is True
    assert rc["cash_identity_hypothetical_ok"] is True
    assert rc["equity_identity_hypothetical_ok"] is True
    assert rc["equity_curve_tail_matches_final"] is True
    assert rc["equity_curve_hypothetical_tail_matches"] is True
    assert rc["sum_funding_fee"] == 0.0
    assert rc["sum_funding_fee_hypothetical"] == pytest.approx(0.09532840755563438,
                                                               abs=1e-15)
    assert rc["sum_funding_fee_conservative"] == pytest.approx(0.09532840755563438,
                                                               abs=1e-15)


def test_two_equity_curves_differ_by_hypothetical_total():
    """两条权益曲线逐点相减，必须恒等于当时的假设资金费累计。"""
    frames = _frames(T0, 5, M15, stop_low_bar=2)
    ev = [{"time_ms": T0 + 2 * M15 + 1, "rate": 0.0001, "mark_price": 100.0}]
    res = _run(frames, ev)
    a, b = res["equity_curve"], res["equity_curve_hypothetical"]

    assert len(a) == len(b) > 0
    for x, y in zip(a, b):
        assert x["timestamp"] == y["timestamp"]
        assert (x["total_equity"] - y["total_equity"]) == pytest.approx(
            y["hypothetical_funding_total"], abs=1e-12)


# ══════════════════ 9. 底层函数级契约 ══════════════════

def _pos(qty=1.0, direction=1, last_funding_ms=0):
    return Position(symbol="X", direction=direction, signal_type=1, entry_time=0,
                    entry_time_sec=0, entry_price=100.0, qty=qty, initial_stop=95.0,
                    current_stop=95.0, entry_fee=0.0, last_mark_price=100.0,
                    last_funding_ms=last_funding_ms)


def test_settle_funding_bucket_is_explicit_and_respected():
    """显式指定 hypothetical ⇒ 现金不动、已确认累计不变、假设桶增加。"""
    pos = _pos(qty=2.0)
    ev = FND.funding_events_ms([{"time_ms": 1000, "rate": 0.001, "mark_price": 100.0}])
    cash = FND.settle_funding(pos, 1000.0, model="settlement_events", t_ms=2000,
                              open_px=100.0, rate=None, events=ev,
                              funding_is_assumption=False,
                              bucket=FUNDING_BUCKET_HYPOTHETICAL)
    assert cash == 1000.0
    assert pos.cum_funding_fee == 0.0
    assert pos.funding_hypothetical == pytest.approx(0.2, abs=1e-12)


def test_settle_funding_confirmed_moves_cash():
    pos = _pos(qty=2.0)
    ev = FND.funding_events_ms([{"time_ms": 1000, "rate": 0.001, "mark_price": 100.0}])
    cash = FND.settle_funding(pos, 1000.0, model="settlement_events", t_ms=2000,
                              open_px=100.0, rate=None, events=ev,
                              funding_is_assumption=False,
                              bucket=FUNDING_BUCKET_CONFIRMED)
    assert cash == pytest.approx(999.8, abs=1e-12)
    assert pos.cum_funding_fee == pytest.approx(0.2, abs=1e-12)
    assert pos.funding_hypothetical == 0.0


def test_assumption_model_forces_hypothetical_even_if_asked_confirmed():
    """即使调用方硬传 confirmed，假设模型也必须落进假设桶（防绕过）。"""
    pos = _pos(qty=1.0, last_funding_ms=0)
    cash = FND.settle_funding(pos, 1000.0, model="assumption_fixed_8h",
                              t_ms=28_800_000, open_px=100.0, rate=0.001,
                              events=[], funding_is_assumption=True,
                              bucket=FUNDING_BUCKET_CONFIRMED)
    assert cash == 1000.0
    assert pos.cum_funding_fee == 0.0
    assert pos.funding_hypothetical == pytest.approx(0.1, abs=1e-12)


def test_intrabar_helper_confirmed_path_uses_execution_time():
    """有精确成交时刻 ⇒ 事件早于成交才算 confirmed；晚于成交则忽略。"""
    pos = _pos(qty=1.0, last_funding_ms=0)
    ev = FND.funding_events_ms([
        {"time_ms": 1500, "rate": 0.001, "mark_price": 100.0},
        {"time_ms": 2500, "rate": 0.001, "mark_price": 100.0},
    ])
    hyp = FND.settle_intrabar_ambiguous(pos, events=ev, bar_open_ms=1000,
                                        bar_close_ms=3000, execution_time_ms=2000,
                                        status=INTRABAR_ORDER_CONFIRMED)
    assert hyp == 0.0
    assert pos.cum_funding_fee == pytest.approx(0.1, abs=1e-12)
    assert pos.funding_hypothetical == 0.0


def test_intrabar_helper_unknown_path_books_hypothetical_only():
    pos = _pos(qty=1.0, last_funding_ms=0)
    ev = FND.funding_events_ms([{"time_ms": 1500, "rate": 0.001, "mark_price": 100.0}])
    hyp = FND.settle_intrabar_ambiguous(pos, events=ev, bar_open_ms=1000,
                                        bar_close_ms=3000, execution_time_ms=None,
                                        status=INTRABAR_ORDER_UNKNOWN)
    assert hyp == pytest.approx(0.1, abs=1e-12)
    assert pos.cum_funding_fee == 0.0
    assert pos.funding_hypothetical == pytest.approx(0.1, abs=1e-12)


# ══════════════ 10. 迟到追补 + 幂等（工单第 6 条硬要求） ══════════════
#
# 「迟到追补」= 结算事件在 E 时刻真实发生，但数据到 T2 才拿到（T2 > E）。
# 要求：既**不能丢**（下一轮观察必须补扣），也**不能重复扣**（幂等）。
# 现有实现靠 `pos.last_funding_ms` 游标实现这两点，故用游标推进来验证。


def test_late_backfill_is_caught_up_on_next_observation():
    """第 1 轮数据未到 ⇒ 不扣；第 2 轮事件到达 ⇒ 补扣一次，且记在已确认口径。"""
    pos = _pos(qty=2.0, last_funding_ms=0)
    ev_full = FND.funding_events_ms(
        [{"time_ms": 1500, "rate": 0.001, "mark_price": 100.0}])

    # 第 1 轮：观察时刻 2000，但该事件的数据尚未到（events 为空）
    cash = FND.settle_funding(pos, 1000.0, model="settlement_events", t_ms=2000,
                              open_px=100.0, rate=None, events=[],
                              funding_is_assumption=False)
    assert cash == 1000.0, "数据未到不得凭空计费"
    assert pos.cum_funding_fee == 0.0
    assert pos.last_funding_ms == 0, "没有事件就不得推进游标（否则追补会丢）"

    # 第 2 轮：观察时刻 4000，事件（时刻 1500）补到 ⇒ 必须追补
    cash = FND.settle_funding(pos, cash, model="settlement_events", t_ms=4000,
                              open_px=100.0, rate=None, events=ev_full,
                              funding_is_assumption=False)
    assert cash == pytest.approx(999.8, abs=1e-12), "迟到事件必须补扣现金"
    assert pos.cum_funding_fee == pytest.approx(0.2, abs=1e-12)
    assert pos.funding_hypothetical == 0.0, "补扣属已确认口径，不得落进假设桶"
    assert pos.last_funding_confirmed_ms == 1500, "游标必须停在事件真实时刻，不是观察时刻"
    assert pos.last_funding_ms == 1500


def test_late_backfill_is_idempotent_across_repeated_observations():
    """同一批事件被反复喂进来 ⇒ 只扣一次（幂等），游标不回退。"""
    pos = _pos(qty=2.0, last_funding_ms=0)
    ev_full = FND.funding_events_ms(
        [{"time_ms": 1500, "rate": 0.001, "mark_price": 100.0}])

    cash = FND.settle_funding(pos, 1000.0, model="settlement_events", t_ms=4000,
                              open_px=100.0, rate=None, events=ev_full,
                              funding_is_assumption=False)
    first_cash, first_cum = cash, pos.cum_funding_fee
    assert first_cash == pytest.approx(999.8, abs=1e-12)

    # 之后连续三轮都拿到同一批事件（数据源重复投递 / 重放）
    for t in (5000, 6000, 7000):
        cash = FND.settle_funding(pos, cash, model="settlement_events", t_ms=t,
                                  open_px=100.0, rate=None, events=ev_full,
                                  funding_is_assumption=False)
        assert cash == pytest.approx(first_cash, abs=1e-12), f"t={t} 发生重复扣费"
        assert pos.cum_funding_fee == pytest.approx(first_cum, abs=1e-12)
    assert pos.last_funding_ms == 1500

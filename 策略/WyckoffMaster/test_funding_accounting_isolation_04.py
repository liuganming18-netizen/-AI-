# -*- coding: utf-8 -*-
"""GM-FUNDING-ACCOUNTING-REWORK-04 · D1 / D2 / D3 定点修复验收套件。

与 `test_funding_accounting_isolation_03.py` 的分工
────────────────────────────────────────────────────
03 号文件验收「已确认 / 假设两桶隔离」，共 21 项，本单不改其会计断言
（仅 1 项 `intrabar_order_status` 期望值按 D3 修正，见该处注释）。
本文件只验收独立审核发现的三个缺陷：

  D1 期末假设现金：`final_cash_hypothetical` 必须扣**全部**假设费（含未平仓）
  D2 假设权益曲线：`cash_equity` 与 `total_equity` 必须同口径，逐点
     `total = cash + unrealized`
  D3 盘中顺序状态：`intrabar_order_status` 不得无条件标 UNKNOWN

设计原则
────────
1. **每条断言都能失败**：均对着引擎真实输出；修复前必须失败（变异检验见
   `scripts/04_d3_mutation_probe.py` 的 D1/D2/D3 三组）。
2. **零外部依赖**：合成行情，不读库、不联网。
3. **不靠改对账标志过关**：断言直接算期望值，不读 `cash_identity_hypothetical_ok`
   作为唯一依据（该标志本身也断言，但只是辅助）。

运行（工程根）：
    /usr/bin/python3 -m pytest 策略/WyckoffMaster/test_funding_accounting_isolation_04.py -q
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
    INTRABAR_ORDER_CONFIRMED, INTRABAR_ORDER_UNKNOWN)
from WyckoffMaster.trading_core.position import Position      # noqa: E402

M15 = 900_000
# 8 小时整点（1792800000 % 28800 == 0）⇒ 假设模型在此刻计提
T8 = 1_792_800_000_000
# D3 场景时间基（与 03 号文件一致，便于逐位对照 0.09532840755563438）
T0 = 1_792_798_200_000
# 40 根 15m = 10 小时，必然跨过 1 个 8h 计提点（第 32 根）
N40 = 40


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


def _run(frames, events, *, model="settlement_events", close_at_end=True):
    df, sig = frames
    pol = POL.ExecutionPolicy(
        signal_params=SignalParams(),
        exec_params=ExecParams(funding_model=model),
        strategy_key="acct04")
    adapter = FILL.SimulatedBarFillAdapter(slippage_bps=5.0,
                                           maker_fee_rate=0.0002,
                                           taker_fee_rate=0.0005)
    core = ENG.TradingCore(
        policy=pol, fill_adapter=adapter, signal_fn=lambda *a, **k: sig,
        to_epoch_seconds=lambda ms, u: (ms // 1000).astype(np.int64),
        strategy_key="acct04", strategy_id="ACCT04", strategy_version="V1",
        ledger=LED.InMemoryLedger())
    market = {"TESTUSDT": {"signal_bars": df, "intrabar_bars": df,
                           "funding_events": events, "time_unit": "ms"}}
    return core.run(market, close_open_positions_at_end=close_at_end)


def _curve_violations(curve, tol=1e-9):
    """逐点检查 total_equity == cash_equity + unrealized_pnl。"""
    return [(i, p["total_equity"], p["cash_equity"] + p["unrealized_pnl"],
             p.get("hypothetical_funding_total"), p["open_positions_count"])
            for i, p in enumerate(curve)
            if abs(p["total_equity"]
                   - (p["cash_equity"] + p["unrealized_pnl"])) > tol]


# ══════════════════ D1 · 期末假设现金 ══════════════════
# 会计定义（与期末假设权益同口径）：
#     假设现金 = 已确认现金 − 假设资金费累计（已平仓 + 未平仓已计提）
#     假设权益 = 已确认权益 − 同上累计
# 旧实现现金侧只减「已平仓」部分，期末留仓时偏大 `hyp_open`。

def test_d1_hypothetical_cash_closed_position():
    """D1-a：期末**已平仓**且存在假设资金费 ⇒ 假设现金 = 已确认现金 − 假设累计。"""
    res = _run(_frames(T8, N40, M15, sig_bar=0), [],
               model="assumption_fixed_8h", close_at_end=True)
    rc = res["reconcile"]

    assert res["open_positions_remaining"] == [], "该场景期末不应留仓"
    hyp = res["funding_hypothetical_total"]
    assert hyp > 0.0, "该场景必须产生假设资金费，否则本测试无意义"

    assert res["final_cash"] - res["final_cash_hypothetical"] == pytest.approx(
        hyp, abs=1e-9), "假设现金必须等于已确认现金扣掉全部假设费"
    assert rc["cash_identity_hypothetical_ok"] is True
    assert rc["equity_identity_hypothetical_ok"] is True


def test_d1_hypothetical_cash_open_position():
    """D1-b：期末**未平仓**且存在假设资金费 ⇒ 未平仓已计提部分也必须扣掉。

    这是 D1 缺陷的暴露场景：旧实现 `final_cash_hyp = cash − sum_hyp_closed`
    在留仓时完全没扣，导致假设现金与假设权益不对称、自检误报 False。
    """
    res = _run(_frames(T8, N40, M15, sig_bar=0), [],
               model="assumption_fixed_8h", close_at_end=False)
    rc = res["reconcile"]

    assert res["open_positions_remaining"], "该场景期末必须留仓，否则测不到缺陷"
    hyp = res["funding_hypothetical_total"]
    assert hyp > 0.0, "该场景必须产生假设资金费"

    # 核心断言：现金侧必须扣全部（含未平仓）
    assert res["final_cash"] - res["final_cash_hypothetical"] == pytest.approx(
        hyp, abs=1e-9), \
        f"假设现金只扣了已平仓部分（现金差 {res['final_cash'] - res['final_cash_hypothetical']!r} " \
        f"≠ 假设累计 {hyp!r}）"

    # 与权益侧对称：两侧未实现盈亏必须相等
    assert (res["final_equity_hypothetical"] - res["final_cash_hypothetical"]) \
        == pytest.approx(res["final_equity"] - res["final_cash"], abs=1e-9), \
        "假设情景与已确认情景的未实现盈亏必须相等（同一持仓、同一价格）"

    assert rc["cash_identity_hypothetical_ok"] is True, \
        "假设现金恒等式必须成立——不得靠改标志掩盖不一致"
    assert rc["equity_identity_hypothetical_ok"] is True


def test_d1_hypothetical_cash_equals_equity_side_definition():
    """D1-c：现金侧与权益侧必须用**同一个** `hyp_total` 定义。

    直接验算：`final_equity_hypothetical − final_cash_hypothetical`
    与 `final_equity − final_cash` 相等（期末未平仓场景），
    等价于两侧扣的是同一个数。
    """
    closed = _run(_frames(T8, N40, M15, sig_bar=0), [],
                  model="assumption_fixed_8h", close_at_end=True)
    open_ = _run(_frames(T8, N40, M15, sig_bar=0), [],
                 model="assumption_fixed_8h", close_at_end=False)

    for tag, res in (("期末已平仓", closed), ("期末未平仓", open_)):
        hyp = res["funding_hypothetical_total"]
        assert res["final_cash"] - res["final_cash_hypothetical"] \
            == pytest.approx(hyp, abs=1e-9), f"{tag}：现金侧口径错"
        assert res["final_equity"] - res["final_equity_hypothetical"] \
            == pytest.approx(hyp, abs=1e-9), f"{tag}：权益侧口径错"


# ══════════════════ D2 · 假设权益曲线 ══════════════════
# 逐点必须满足：total_equity == cash_equity + unrealized_pnl
# 旧实现 cash_equity 只减已平仓、total_equity 减全部 ⇒ 有未平仓假设费时破恒等式。

def test_d2_hypothetical_curve_identity_pointwise_40_of_40():
    """D2-a：40 个采样点**全部**满足 `total = cash + unrealized`（修复前 7/40 违规）。"""
    res = _run(_frames(T8, N40, M15, sig_bar=0), [],
               model="assumption_fixed_8h", close_at_end=True)
    curve = res["equity_curve_hypothetical"]

    assert len(curve) == N40, f"应有 {N40} 个采样点，实际 {len(curve)}"
    bad = _curve_violations(curve)
    assert not bad, \
        f"假设曲线逐点恒等式违规 {len(bad)}/{len(curve)} 点；前 3 例 {bad[:3]}"


def test_d2_hypothetical_curve_identity_pointwise_open_position():
    """D2-b：期末留仓场景同样要求逐点成立（未平仓假设费是缺陷触发条件）。"""
    res = _run(_frames(T8, N40, M15, sig_bar=0), [],
               model="assumption_fixed_8h", close_at_end=False)
    curve = res["equity_curve_hypothetical"]

    assert res["open_positions_remaining"], "该场景必须留仓"
    bad = _curve_violations(curve)
    assert not bad, \
        f"留仓场景假设曲线违规 {len(bad)}/{len(curve)} 点；前 3 例 {bad[:3]}"


def test_d2_hypothetical_curve_identity_across_five_phases():
    """D2-c：五个相位点逐点验证 —— 结算前 / 结算时刻 / 结算后继续持仓 /
    最终平仓 / 期末仍未平仓。
    """
    res_closed = _run(_frames(T8, N40, M15, sig_bar=0), [],
                      model="assumption_fixed_8h", close_at_end=True)
    curve = res_closed["equity_curve_hypothetical"]

    # 定位第一次计提点（假设累计从 0 变正）
    idx = next((i for i, p in enumerate(curve)
                if p["hypothetical_funding_total"] > 0.0), None)
    assert idx is not None and idx > 0, "必须存在「计提前」与「计提时刻」两个相位"

    phases = {
        "结算前": idx - 1,
        "结算时刻": idx,
        "结算后继续持仓": min(idx + 1, len(curve) - 1),
        "最终平仓": len(curve) - 1,
    }
    for name, i in phases.items():
        p = curve[i]
        assert p["total_equity"] == pytest.approx(
            p["cash_equity"] + p["unrealized_pnl"], abs=1e-9), \
            f"相位「{name}」（第 {i} 点）恒等式不成立"

    # 计提前假设累计必须为 0，计提时刻必须转正 —— 否则上面的「结算时刻」无意义
    assert curve[idx - 1]["hypothetical_funding_total"] == pytest.approx(0.0, abs=1e-12)
    assert curve[idx]["hypothetical_funding_total"] > 0.0

    # 第五个相位：期末仍未平仓
    res_open = _run(_frames(T8, N40, M15, sig_bar=0), [],
                    model="assumption_fixed_8h", close_at_end=False)
    last = res_open["equity_curve_hypothetical"][-1]
    assert last["open_positions_count"] > 0, "期末未平仓相位必须仍有持仓"
    assert last["total_equity"] == pytest.approx(
        last["cash_equity"] + last["unrealized_pnl"], abs=1e-9), \
        "相位「期末仍未平仓」恒等式不成立"


def test_d2_two_curves_still_differ_by_hypothetical_total():
    """D2-d：改 D2 不得破坏「双曲线逐点差 = 当时假设累计」这一原有性质。"""
    for close_at_end in (True, False):
        res = _run(_frames(T8, N40, M15, sig_bar=0), [],
                   model="assumption_fixed_8h", close_at_end=close_at_end)
        for i, (a, b) in enumerate(zip(res["equity_curve"],
                                       res["equity_curve_hypothetical"])):
            assert a["total_equity"] - b["total_equity"] == pytest.approx(
                b["hypothetical_funding_total"], abs=1e-9), \
                f"第 {i} 点：双曲线差与假设累计不符"


def test_d2_confirmed_curve_unaffected():
    """D2-e：已确认曲线不得被改动（D2 只能动假设曲线）。"""
    for close_at_end in (True, False):
        res = _run(_frames(T8, N40, M15, sig_bar=0), [],
                   model="assumption_fixed_8h", close_at_end=close_at_end)
        bad = _curve_violations(res["equity_curve"])
        assert not bad, f"已确认曲线被破坏：{bad[:3]}"


# ══════════════════ D3 · 盘中顺序状态 ══════════════════
# 语义：`intrabar_order_status` 描述「本笔资金费的会计归属是否有顺序依据」，
# 不是「成交时刻能否精确到毫秒」。判据 = 本 bar 的不可判定窗口内是否真有事件。

def test_d3_event_at_bar_open_is_confirmed():
    """情形①：事件恰在 bar 开盘（相位 A 已按已确认结算）、该 bar 内触发止损
    ⇒ 顺序可确定 ⇒ CONFIRMED，且已确认金额非零。
    """
    frames = _frames(T0, 5, M15, stop_low_bar=2)
    ev = [{"time_ms": T0 + 2 * M15, "rate": 0.0001, "mark_price": 100.0}]
    tr = _run(frames, ev)["trades"][0]

    assert tr["exit_reason"] == "intrabar_stop", "本场景必须是盘中止损"
    assert tr["intrabar_order_status"] == INTRABAR_ORDER_CONFIRMED, \
        f"事件在 bar 开盘、顺序可确定，不得标 UNKNOWN（实际 {tr['intrabar_order_status']}）"
    assert tr["funding_fee"] > 0.0, "该事件必须进已确认口径"
    assert tr["funding_fee_hypothetical"] == 0.0


def test_d3_event_inside_bar_is_unknown():
    """情形②：事件在 bar 内（开盘 +1ms）、该 bar 内触发止损
    ⇒ 先后不可证明 ⇒ UNKNOWN，且只进假设桶。
    """
    frames = _frames(T0, 5, M15, stop_low_bar=2)
    ev = [{"time_ms": T0 + 2 * M15 + 1, "rate": 0.0001, "mark_price": 100.0}]
    tr = _run(frames, ev)["trades"][0]

    assert tr["intrabar_order_status"] == INTRABAR_ORDER_UNKNOWN, \
        f"事件在 bar 内、先后不可判，必须标 UNKNOWN（实际 {tr['intrabar_order_status']}）"
    assert tr["funding_fee"] == 0.0, "顺序不可判 ⇒ 绝不进已确认"
    assert tr["funding_fee_hypothetical"] > 0.0, "必须进假设桶，不得悄悄丢掉"


def test_d3_event_in_earlier_bar_is_confirmed():
    """情形③：事件在更早 bar 已确认结算、本 bar 内无事件
    ⇒ 本 bar 无可判之事 ⇒ CONFIRMED。

    旧实现「盘中止损即无条件标 UNKNOWN」，本场景会被误标。
    """
    frames = _frames(T0, 5, M15, stop_low_bar=3)
    ev = [{"time_ms": T0 + M15 + 1, "rate": 0.0001, "mark_price": 100.0}]
    tr = _run(frames, ev)["trades"][0]

    assert tr["exit_reason"] == "intrabar_stop"
    assert tr["intrabar_order_status"] == INTRABAR_ORDER_CONFIRMED, \
        f"本 bar 无事件、资金费已在前序相位确认，不得标 UNKNOWN" \
        f"（实际 {tr['intrabar_order_status']}）"
    assert tr["funding_fee"] > 0.0


def test_d3_event_inside_bar_missing_rate_still_unknown():
    """情形④：事件在 bar 内但缺 rate ⇒ 不记账（记 gap），
    但「先后不可判」这一事实不变 ⇒ 仍须 UNKNOWN。

    这条区分「有没有金额」与「顺序可不可判」——状态必须跟随后者。
    """
    frames = _frames(T0, 5, M15, stop_low_bar=2)
    ev = [{"time_ms": T0 + 2 * M15 + 1, "rate": None, "mark_price": 100.0}]
    res = _run(frames, ev)
    tr = res["trades"][0]

    assert tr["funding_fee"] == 0.0, "缺 rate ⇒ 不得计费"
    assert tr["funding_fee_hypothetical"] == 0.0, "缺 rate ⇒ 假设桶也不得记账"
    assert tr["intrabar_order_status"] == INTRABAR_ORDER_UNKNOWN, \
        "有事件落窗即顺序不可判，与是否记账无关"

    gaps = res.get("funding_gaps") or []
    assert any(g.get("reason", "").startswith("funding_event_missing_fields")
               for g in gaps), "缺 rate 必须记 funding_gap，不得静默吞掉"


def test_d3_non_intrabar_exit_keeps_confirmed():
    """情形⑤：非盘中止损（期末平仓）⇒ 不涉及盘中顺序，保持 CONFIRMED。"""
    frames = _frames(T0, 5, M15)                       # 不设止损 bar
    ev = [{"time_ms": T0 + 2 * M15 + 1, "rate": 0.0001, "mark_price": 100.0}]
    tr = _run(frames, ev)["trades"][0]

    assert tr["exit_reason"] == "force_close_eod"
    assert tr["intrabar_order_status"] == INTRABAR_ORDER_CONFIRMED


# ══════════════════ D3 · 底层出参口径 ══════════════════

def _mkpos():
    return Position(symbol="X", direction=1, signal_type=1,
                    entry_time=T0, entry_time_sec=T0 // 1000,
                    entry_price=100.0, qty=10.0,
                    initial_stop=95.0, current_stop=95.0,
                    entry_fee=0.0, last_mark_price=100.0)


def test_d3_helper_reports_ambiguous_event_count():
    """`settle_intrabar_ambiguous` 的 `ambiguous_out` 必须如实报告落窗事件数。"""
    pos = _mkpos()
    out = {}
    ev = [{"time_ms": 1500, "rate": 0.0001, "mark_price": 100.0}]
    hyp = FND.settle_intrabar_ambiguous(
        pos, events=ev, bar_open_ms=1000, bar_close_ms=2000,
        execution_time_ms=None, status=INTRABAR_ORDER_UNKNOWN,
        ambiguous_out=out)

    assert hyp > 0.0
    assert out["n_hypothetical"] == 1, f"落窗事件数应为 1，实际 {out.get('n_hypothetical')}"
    assert out["n_confirmed"] == 0
    assert out["window_open_ms"] == 1000 and out["window_close_ms"] == 2000


def test_d3_helper_counts_missing_field_event_as_ambiguous():
    """缺 rate/mark 的落窗事件**也要计入**不可判定数（只是不记账）。"""
    pos = _mkpos()
    out = {}
    ev = [{"time_ms": 1500, "rate": None, "mark_price": None}]
    hyp = FND.settle_intrabar_ambiguous(
        pos, events=ev, bar_open_ms=1000, bar_close_ms=2000,
        execution_time_ms=None, status=INTRABAR_ORDER_UNKNOWN,
        ambiguous_out=out)

    assert hyp == 0.0, "缺字段不得记账"
    assert out["n_hypothetical"] == 1, \
        "缺字段事件同样「先后不可判」，必须计入，否则状态会被误判为 CONFIRMED"


def test_d3_helper_ignores_event_outside_bar():
    """bar 之外的事件不得计入不可判定数（否则状态会被误标 UNKNOWN）。"""
    pos = _mkpos()
    out = {}
    ev = [{"time_ms": 5000, "rate": 0.0001, "mark_price": 100.0}]
    hyp = FND.settle_intrabar_ambiguous(
        pos, events=ev, bar_open_ms=1000, bar_close_ms=2000,
        execution_time_ms=None, status=INTRABAR_ORDER_UNKNOWN,
        ambiguous_out=out)

    assert hyp == 0.0
    assert out["n_hypothetical"] == 0, "bar 之外的事件不属于本 bar"


def test_d3_helper_confirmed_branch_counts_separately():
    """可证明分支（有精确成交时刻）计入 `n_confirmed`，不计入 `n_hypothetical`。"""
    pos = _mkpos()
    out = {}
    ev = [{"time_ms": 1500, "rate": 0.0001, "mark_price": 100.0}]
    FND.settle_intrabar_ambiguous(
        pos, events=ev, bar_open_ms=1000, bar_close_ms=2000,
        execution_time_ms=1800, status=INTRABAR_ORDER_CONFIRMED,
        ambiguous_out=out)

    assert out["n_confirmed"] == 1
    assert out["n_hypothetical"] == 0


def test_d3_helper_out_param_is_optional():
    """不传 `ambiguous_out` 时行为完全不变（向后兼容）。"""
    pos = _mkpos()
    ev = [{"time_ms": 1500, "rate": 0.0001, "mark_price": 100.0}]
    hyp = FND.settle_intrabar_ambiguous(
        pos, events=ev, bar_open_ms=1000, bar_close_ms=2000,
        execution_time_ms=None, status=INTRABAR_ORDER_UNKNOWN)
    assert hyp == pytest.approx(100.0 * 10.0 * 0.0001 * 1, abs=1e-12)

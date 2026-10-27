# -*- coding: utf-8 -*-
"""成交精度修复 · 专项单元测试（2026-10-17）。

覆盖用户要求的七类检查：
  1. 价格低于一美分（SPACE 类标的）
  2. 恰好落在步长边界
  3. 边界附近浮点误差
  4. 多空开平仓四个方向
  5. 数量向下取整 / 最小量 / 最小名义额
  6. 规格缺失（不得冒充正式回测通过）
  7. 零价格 / 非有限价格拒绝；出场失败不得把仓位当作已平仓

设计原则：**不猜 tick**。所有 tick 都来自 `数据/contracts/旧标的池/exchangeInfo_*.json`，
测试只断言「给定规格下的确定结果」。
"""
from __future__ import annotations

import math
import os
import sys
from decimal import Decimal

import pandas as pd
import pytest

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)
if os.path.join(base_dir, "策略") not in sys.path:
    sys.path.insert(0, os.path.join(base_dir, "策略"))

from WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
from WyckoffMaster.event_trend_v0_1.instrument_spec import (
    APPLICABILITY_MISSING, APPLICABILITY_UNVERIFIED_HISTORICAL,
    InstrumentSpec, ceil_to_step, floor_to_step, resolve_instrument_spec,
    round_price_to_tick,
)
from WyckoffMaster.trading_core.fill import (
    FillRejected, SimulatedBarFillAdapter, price_breakdown,
    round_buy_price, round_qty_down, round_sell_price,
)
from WyckoffMaster.trading_core.rejects import (
    EXECUTION_VALIDITY_REASONS, REJECT_CLASS_EXECUTION, REJECT_CLASS_STRATEGY,
    REJECT_CLASS_UNREGISTERED, STAGE_ENTRY_FILL, STAGE_ENTRY_NOTIONAL,
    STAGE_ENTRY_QTY, STAGE_EXIT_EOD, STAGE_EXIT_GAP, STAGE_EXIT_INTRABAR,
    classify_reject, is_execution_validity, mark_if_execution_invalid,
)
from WyckoffMaster.event_trend_v0_1.tests.test_event_trend_closure import (
    HOUR_MS, MIN15_MS, _bars_15m, _candidate,
)


# ══════════════════════════ 夹具 ══════════════════════════

def _adapter(strict: bool = True, slippage_bps: float = 3.0) -> SimulatedBarFillAdapter:
    return SimulatedBarFillAdapter(slippage_bps=slippage_bps, maker_fee_rate=2e-4,
                                   taker_fee_rate=5e-4, strict=strict)


def _spec(**over) -> InstrumentSpec:
    base = dict(
        symbol="SPACEUSDT", venue="binance", market="um",
        price_step="0.0000010", qty_step="1", min_qty="1", max_qty="50000000",
        min_notional="5", price_precision=7, quantity_precision=0,
        order_type="MARKET", source_url="test", source_kind="test",
        fetched_at_ms=1790941589240, evidence_path=None, evidence_sha256=None,
        onboard_ms=None, snapshot_server_ms=1790941589240,
        applicability=APPLICABILITY_UNVERIFIED_HISTORICAL,
        applicability_note="test", formal_pass_allowed=False,
    )
    base.update(over)
    return InstrumentSpec(**base)


# ══════════════════════════ 1. 价格低于一美分 ══════════════════════════

def test_price_below_one_cent_with_real_tick_is_preserved():
    """SPACE 真实价 0.004076 + 真实 tick 1e-6 ⇒ 成交价必须保留 6 位有效精度。"""
    a = _adapter()
    buy = a.quote_entry(raw_price=0.004076, direction=1, price_step="0.0000010",
                        stop_price=0.003)
    assert buy.entry_fill == pytest.approx(0.004078, abs=1e-12)
    sell = a.quote_exit(target_px=0.004076, direction=1, price_step="0.0000010",
                        reason="intrabar_stop")
    assert sell == pytest.approx(0.004074, abs=1e-12)
    # 三段审计齐全：原价 / 滑点后 / 取整后
    assert set(buy.audit) >= {"raw_price", "post_slip_price", "post_round_price",
                              "price_step", "slip_delta", "round_delta"}


def test_price_below_one_cent_with_cent_step_must_be_rejected():
    """修复前的真凶：0.004076 用 0.01 步长，卖出向更便宜取整 = **0.00**。

    现在不允许「成交价为 0」，必须拒绝并留下四件套证据。
    """
    bd = price_breakdown(raw_price=0.004076, is_buy=False, price_step=0.01,
                         slippage_rate=3e-4)
    assert Decimal(bd["post_round_price"]) == 0      # 复现旧行为：零元卖出
    a = _adapter()
    with pytest.raises(FillRejected) as ei:
        a.quote_exit(target_px=0.004076, direction=1, price_step=0.01,
                     reason="intrabar_stop")
    d = ei.value.detail
    assert ei.value.reason == "nonpositive_or_nonfinite_exit_fill"
    assert d["raw_price"] == "0.004076"
    assert d["post_slip_price"].startswith("0.00407")
    assert Decimal(d["post_round_price"]) == 0
    assert d["price_step"] == "0.01"


# ══════════════════════════ 2. 恰好落在步长边界 ══════════════════════════

def test_exact_step_boundary_does_not_jump_one_extra_step():
    """100.03 恰好是 0.01 的整数倍 ⇒ 买入取整后仍是 100.03，不得变成 100.04。"""
    assert round_buy_price(100.03, 0.01) == pytest.approx(100.03, abs=1e-12)
    assert round_sell_price(100.03, 0.01) == pytest.approx(100.03, abs=1e-12)
    assert round_buy_price(0.07, 0.01) == pytest.approx(0.07, abs=1e-12)
    assert round_sell_price(0.07, 0.01) == pytest.approx(0.07, abs=1e-12)


def test_quantity_exact_boundary_does_not_lose_one_step():
    """0.29 是 0.01 的整数倍。旧实现 `0.29/0.01 = 28.999999999999996` ⇒ 向下取整成 0.28。"""
    assert round_qty_down(0.29, 0.01) == pytest.approx(0.29, abs=1e-12)
    assert round_qty_down(4.43, 0.01) == pytest.approx(4.43, abs=1e-12)
    assert round_qty_down(3095.0, 1) == pytest.approx(3095.0, abs=1e-12)


# ══════════════════════════ 3. 边界附近浮点误差 ══════════════════════════

@pytest.mark.parametrize("px,step", [
    (0.29, 0.01), (1.13, 0.01), (2.675, 0.001), (317.78, 0.01),
    (0.000123, 0.000001), (47.79, 0.001),
])
def test_boundary_float_noise_does_not_shift_step(px, step):
    """二进制浮点在边界附近的抖动不得改变取整结果。

    `floor_to_step` / `ceil_to_step` 返回 Decimal（精确），这里统一转 float 再比。
    """
    # Decimal 路径：整数倍必须原样保留
    assert float(floor_to_step(px, step)) == pytest.approx(px, abs=1e-12)
    assert float(ceil_to_step(px, step)) == pytest.approx(px, abs=1e-12)
    # 并证明这不是「恒等」：加半个 tick 必然跳一档
    half = step / 2
    assert float(ceil_to_step(px + half, step)) == pytest.approx(px + step, abs=1e-12)
    assert float(floor_to_step(px - half, step)) == pytest.approx(px - step, abs=1e-12)
    # 旧 float 实现在这些值上会多退一档，这里留作反证
    assert round_qty_down(px, step) == pytest.approx(px, abs=1e-12)


# ══════════════════════════ 4. 多空开平仓四方向 ══════════════════════════

def test_long_and_short_entry_exit_directions():
    """买向更贵（ceil）、卖向更便宜（floor）—— 四个方向逐一对齐。

    这里把滑点置 0，只为隔离「取整方向」；滑点与取整的先后顺序另有用例。
    """
    a = _adapter(slippage_bps=0.0)
    raw = 100.001
    step = 0.01
    # 多头入场 = 买 ⇒ 向上
    long_in = a.quote_entry(raw_price=raw, direction=1, price_step=step, stop_price=99.0)
    assert long_in.entry_fill == pytest.approx(100.01, abs=1e-9)
    assert long_in.audit["is_buy"] is True
    # 空头入场 = 卖 ⇒ 向下
    short_in = a.quote_entry(raw_price=raw, direction=-1, price_step=step, stop_price=101.0)
    assert short_in.entry_fill == pytest.approx(100.00, abs=1e-9)
    assert short_in.audit["is_buy"] is False
    # 多头出场 = 卖 ⇒ 向下
    long_out = a.quote_exit(target_px=raw, direction=1, price_step=step, reason="x")
    assert long_out == pytest.approx(100.00, abs=1e-9)
    assert a.last_exit_audit["is_buy"] is False
    # 空头出场 = 买 ⇒ 向上
    short_out = a.quote_exit(target_px=raw, direction=-1, price_step=step, reason="x")
    assert short_out == pytest.approx(100.01, abs=1e-9)
    assert a.last_exit_audit["is_buy"] is True


def test_slippage_is_applied_before_rounding():
    """顺序必须「原价 → 滑点 → 取整」，可验证：3bps 下 100.001 买向得 100.04。"""
    a = _adapter(slippage_bps=3.0)
    q = a.quote_entry(raw_price=100.001, direction=1, price_step=0.01, stop_price=99.0)
    assert q.audit["raw_price"] == "100.001"
    assert q.audit["post_slip_price"] == "100.0310003"
    assert q.audit["post_round_price"] == "100.04"
    assert q.entry_fill == pytest.approx(100.04, abs=1e-9)
    # 卖出方向：100.001 → 99.9709997 → 向下取整 99.97
    x = a.quote_exit(target_px=100.001, direction=1, price_step=0.01, reason="x")
    assert a.last_exit_audit["post_slip_price"] == "99.9709997"
    assert a.last_exit_audit["post_round_price"] == "99.97"
    assert x == pytest.approx(99.97, abs=1e-9)


def test_round_price_to_tick_matches_adapter():
    """`instrument_spec.round_price_to_tick` 与撮合器的买/卖取整方向必须一致。"""
    for px in (0.004076, 100.001, 47.7891, 1e-6):
        for tick in ("0.000001", "0.01", "0.001"):
            assert round_price_to_tick(px, tick, is_buy=True) == ceil_to_step(px, tick)
            assert round_price_to_tick(px, tick, is_buy=False) == floor_to_step(px, tick)
    # 网格上的值两个方向都不动
    assert round_price_to_tick(0.07, "0.01", is_buy=True) == Decimal("0.07")
    assert round_price_to_tick(0.07, "0.01", is_buy=False) == Decimal("0.07")


# ══════════════════════════ 5. 数量取整 / 最小量 / 最小名义额 ══════════════════════════

def _entry_scenario(open_px: float, stop_px: float, *, min_entry_mult: float = 0.5,
                    high=None, low=None, close=None):
    """入场场景夹具。默认与既有用例逐位一致（flat bar / 入场闸门 0.5H~2H）。

    可选参数只为 PRECISION-09 新增：改 bar 形状、改入场闸门（构造正常策略取消）。
    """
    t0 = 1788652800000
    t_1h_open = t0 + 24 * HOUR_MS
    t_1h_close = t_1h_open + HOUR_MS
    t_exec = t_1h_close + MIN15_MS
    cand = _candidate(candidate_id="cand_prec", event_id="ev_prec",
                      snapshot_time_ms=t_1h_close, available_at_ms=t_1h_close,
                      execution_earliest_ms=t_exec,
                      box_high_h=open_px * 0.9, box_low_l=open_px * 0.8,
                      breakout_close=open_px,
                      initial_stop_price=stop_px,
                      max_entry_price=open_px * 2.0,
                      min_entry_price=open_px * min_entry_mult)
    df_1h = pd.DataFrame([{"timestamp": t_1h_open, "open": open_px,
                           "high": open_px, "low": open_px, "close": open_px,
                           "volume": 1e6}])
    df_15m = _bars_15m(t_exec - MIN15_MS, 21, open_px=open_px,
                       quote_volume=5_000_000.0)
    if high is not None:
        df_15m["high"] = high
    if low is not None:
        df_15m["low"] = low
    if close is not None:
        df_15m["close"] = close
    return cand, df_1h, df_15m


def test_qty_floor_to_step_and_min_notional_reject():
    """数量必须向下取整到 stepSize；低于最小量 / 最小名义额必须拒绝。"""
    cand, df_1h, df_15m = _entry_scenario(100.0, 99.0)
    # ① stepSize=0.01 ⇒ 数量必须是 0.01 的整数倍
    spec = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                 min_qty="0.01", min_notional="5")
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", instrument_spec=spec)
    tr = res["engine_result"]["trades"]
    assert tr, "应当成交"
    qty = tr[0]["qty"]
    assert abs(qty / 0.01 - round(qty / 0.01)) < 1e-9
    assert qty >= 0.01
    assert tr[0]["entry_price"] * qty >= 5.0

    # ② minNotional 抬到 1e6 ⇒ 必须拒绝，且原因可追溯
    spec2 = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                  min_qty="0.01", min_notional="1000000")
    res2 = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                    variant="A", instrument_spec=spec2)
    assert res2["engine_result"]["trades_count"] == 0
    reasons = [r["reason"] for r in res2["engine_result"]["rejected_orders"]]
    assert "notional_below_min" in reasons
    assert res2["precision"]["rejects_count"] >= 1

    # ③ minQty 抬到 1e6 ⇒ 必须拒绝
    spec3 = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                  min_qty="1000000", min_notional="5")
    res3 = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                    variant="A", instrument_spec=spec3)
    assert res3["engine_result"]["trades_count"] == 0
    assert "qty_below_min_step" in [r["reason"] for r in res3["engine_result"]["rejected_orders"]]


# ══════════════════════════ 6. 规格缺失 ══════════════════════════

def test_missing_spec_blocks_formal_pass():
    """快照里没有该合约 ⇒ missing，且不得声明正式回测通过。"""
    sp = resolve_instrument_spec("NOT_A_REAL_SYMBOL_USDT",
                                 window_start_ms=1704067200000,
                                 window_end_ms=1719705600000)
    assert sp.applicability == APPLICABILITY_MISSING
    assert sp.formal_pass_allowed is False
    assert sp.blocking is True
    with pytest.raises(Exception):
        sp.validate()          # tick=0 ⇒ 自检必须炸，不能当「无精度约束」


def test_single_snapshot_is_unverified_historical_not_verified():
    """只有单一时点快照时，历史适用性**无法证明** ⇒ 不得 formal_pass。"""
    sp = resolve_instrument_spec("BNBUSDT", window_start_ms=1704067200000,
                                 window_end_ms=1719705600000)
    assert sp.applicability == APPLICABILITY_UNVERIFIED_HISTORICAL
    assert sp.formal_pass_allowed is False
    assert sp.price_step == "0.01"      # 规格本身是真实的
    assert sp.evidence_sha256 and len(sp.evidence_sha256) == 64


def test_observed_price_off_tick_grid_is_contradicted():
    """真实价格不在 tick 网格上 ⇒ 该快照对窗口**确定不适用**（只用于证伪）。"""
    sp = resolve_instrument_spec("BNBUSDT", window_start_ms=1704067200000,
                                 window_end_ms=1719705600000,
                                 observed_prices=[285.381234])   # 0.01 网格放不下
    assert sp.applicability == "contradicted"
    assert sp.formal_pass_allowed is False
    assert sp.contradiction_samples


def test_run_without_spec_or_step_is_refused():
    """既无规格也无显式步长 ⇒ 适配器必须拒绝运行（不得回退 0.01）。"""
    cand, df_1h, df_15m = _entry_scenario(100.0, 99.0)
    with pytest.raises(Exception) as ei:
        run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [], variant="A")
    assert "instrument_spec_required" in str(ei.value)


# ══════════════════════════ 7. 零价格 / 非有限 / 出场失败 ══════════════════════════

@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_zero_or_nonfinite_price_is_rejected(bad):
    a = _adapter()
    with pytest.raises(FillRejected):
        a.quote_entry(raw_price=bad, direction=1, price_step=0.01, stop_price=99.0)
    with pytest.raises(FillRejected):
        a.quote_exit(target_px=bad, direction=1, price_step=0.01, reason="x")


def test_zero_or_negative_step_is_rejected():
    """`price_step=0` 不得被当成「跳过精度约束」。"""
    a = _adapter()
    for step in (0, 0.0, -0.01):
        with pytest.raises(FillRejected) as ei:
            a.quote_entry(raw_price=100.0, direction=1, price_step=step, stop_price=99.0)
        assert ei.value.reason in ("invalid_price_step_nonpositive",
                                   "non_finite_or_invalid_price_step")


def test_exit_failure_keeps_position_open_and_marks_run_invalid():
    """出场取整后为 0 ⇒ **不得**把仓位当作已平仓；运行标记为无效/未完成。"""
    # 价格 0.004 + 步长 0.01：入场（买，向上）能成 0.01，出场（卖，向下）必成 0.00
    cand, df_1h, df_15m = _entry_scenario(0.004, 0.003)
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", price_step="0.01")
    eng = res["engine_result"]
    assert eng["trades_count"] == 0
    assert eng["open_positions_remaining"], "出场失败时仓位必须仍未平"
    assert res["precision"]["run_valid"] is False
    assert any("exit" in r for r in res["precision"]["invalid_reasons"])
    assert res["precision"]["unclosed_positions"]
    # 账本恒等式仍必须成立（不能因为拒绝出场就把现金记成已回笼）
    assert eng["reconcile"]["cash_identity_ok"] is True


# ══════════════════════════ 8. 成本分账 ══════════════════════════

def test_slippage_and_rounding_are_split_and_reconcile():
    """`slippage_loss`（原有总口径）== 纯滑点 + 取整；不把旧 slippage 整体加回利润。"""
    cand, df_1h, df_15m = _entry_scenario(0.004, 0.003)
    spec = _spec(symbol="SOLUSDT", price_step="0.0000010", qty_step="1",
                 min_qty="1", min_notional="5")
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", instrument_spec=spec)
    tr = res["engine_result"]["trades"]
    assert tr, "真实 tick 下应当成交"
    t = tr[0]
    assert t["slippage_loss"] == pytest.approx(
        t["slippage_loss_pure"] + t["rounding_loss"], abs=1e-12)
    assert res["engine_result"]["reconcile"]["slippage_split_ok"] is True
    assert res["summary"]["slippage_split_ok"] is True
    # 三段审计落在每笔成交上
    assert t["price_audit"]["entry"]["raw_price"]
    assert t["price_audit"]["exit"]["post_round_price"]


# ══════════════════════ 9. 拒单分类：执行有效性 vs 策略取消（PRECISION-09）══════════════════════

def _assert_no_fill_side_effects(res):
    """「没成交」必须是干净的：无持仓、无费用、现金原封不动、账本恒等。"""
    eng = res["engine_result"]
    assert eng["trades_count"] == 0
    assert eng["open_positions_remaining"] == []
    assert eng["final_cash"] == pytest.approx(eng["initial_equity"], abs=1e-9)
    assert res["summary"]["fee_total"] == pytest.approx(0.0, abs=1e-12)
    assert eng["reconcile"]["cash_identity_ok"] is True
    assert eng["reconcile"]["equity_identity_ok"] is True


def test_entry_notional_reject_marks_run_invalid():
    """入场因 minNotional 被拒 ⇒ 运行无效（PRECISION-08 复现的缺陷）。

    修复前：`invalid_reasons == []` 且 `run_valid is True` —— 0 成交却被当成「跑完了」。
    """
    cand, df_1h, df_15m = _entry_scenario(100.0, 99.0)
    spec = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                 min_qty="0.01", min_notional="1000000")
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", instrument_spec=spec)
    _assert_no_fill_side_effects(res)
    assert "notional_below_min" in [r["reason"]
                                    for r in res["engine_result"]["rejected_orders"]]
    assert res["precision"]["invalid_reasons"] == [
        "entry_notional_rejected:notional_below_min"]
    assert res["precision"]["run_valid"] is False
    # 正式放行保持不通过（今天所有规格都还是 unverified_historical，
    # 该字段恒 False，因此这条只证明「没有被本次修复反向放开」）
    assert res["summary"]["formal_pass_allowed"] is False


def test_entry_qty_reject_marks_run_invalid():
    """入场因 minQty 被拒 ⇒ 同样必须记入 invalid_reasons。"""
    cand, df_1h, df_15m = _entry_scenario(100.0, 99.0)
    spec = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                 min_qty="1000000", min_notional="5")
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", instrument_spec=spec)
    _assert_no_fill_side_effects(res)
    assert res["precision"]["invalid_reasons"] == [
        "entry_qty_rejected:qty_below_min_step"]
    assert res["precision"]["run_valid"] is False


def test_entry_price_reject_marks_run_invalid():
    """入场原价非正（坏数据）⇒ 走 FillRejected 分支，同样必须判无效。"""
    cand, df_1h, df_15m = _entry_scenario(0.0, 0.0)
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", price_step="0.01")
    _assert_no_fill_side_effects(res)
    assert res["precision"]["invalid_reasons"] == [
        "entry_fill_rejected:nonpositive_raw_price"]
    assert res["precision"]["run_valid"] is False


def test_strategy_cancel_does_not_invalidate_run():
    """正常策略取消（开盘跌回箱体）⇒ 0 成交但**不得**判无效。

    这是本次修复的边界：不能把「所有拒单」一律标成失败。
    """
    cand, df_1h, df_15m = _entry_scenario(100.0, 99.0, min_entry_mult=1.01)
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", price_step="0.01")
    _assert_no_fill_side_effects(res)
    reasons = [r["reason"] for r in res["engine_result"]["rejected_orders"]]
    assert any(r.startswith("canceled_fell_back_into_box") for r in reasons)
    assert res["precision"]["invalid_reasons"] == []
    assert res["precision"]["run_valid"] is True
    assert res["precision"]["rejects_count"] == 0      # 策略取消不进精度拒绝表


def test_every_precision_gate_reason_is_registered():
    """精度闸门实际会发出的 reason 必须全部登记为「执行有效性错误」。

    反向也查：表里不许有闸门根本不会发出的死条目（防止表与实际脱节）。
    """
    gate_reasons = {
        "non_finite_or_invalid_price", "nonpositive_raw_price",
        "non_finite_or_invalid_price_step", "invalid_price_step_nonpositive",
        "nonpositive_or_nonfinite_entry_fill", "nonpositive_or_nonfinite_exit_fill",
        "qty_below_min_step", "notional_below_min",
    }
    for r in gate_reasons:
        assert is_execution_validity(r), r
        assert classify_reject(r) == REJECT_CLASS_EXECUTION
    assert gate_reasons == set(EXECUTION_VALIDITY_REASONS)


def test_all_fill_rejected_literals_are_registered():
    """`fill.py` 里每个 `FillRejected("...")` 字面量都必须在分类表内。

    新增闸门却忘记登记 ⇒ 这里先红，而不是等到某天「0 成交但 run_valid=True」。
    """
    import inspect
    import re
    from WyckoffMaster.trading_core import fill as fill_mod
    src = inspect.getsource(fill_mod)
    found = set(re.findall(r'FillRejected\(\s*"([A-Za-z0-9_]+)"', src))
    assert found, "正则没解析到任何 raise 点 ⇒ 测试自身失效"
    assert found <= EXECUTION_VALIDITY_REASONS, found - EXECUTION_VALIDITY_REASONS


def test_strategy_reasons_are_never_classified_as_execution_errors():
    """策略/风控取消逐个抽查：分类必须是 strategy_cancel（含带括号实参的写法）。"""
    for r in ("canceled_fell_back_into_box (open=100.0 < H=101.0)",
              "canceled_gap_above_0.5a (open=105.0 > max=102.0)",
              "candidate_identity_unresolved(signal_id=x)",
              "invalid_signal_stop", "nonpositive_qty",
              "notional_cap_exceeded_zero_qty",
              "duplicate_symbol_active", "max_concurrent_trades_reached",
              "portfolio_risk_cap_exceeded"):
        assert classify_reject(r) == REJECT_CLASS_STRATEGY, r
        assert is_execution_validity(r) is False


def test_mark_helper_never_writes_strategy_or_unknown_reasons():
    """`mark_if_execution_invalid` 是唯一写入口：策略取消/未登记一律不写、且幂等。"""
    buf = []
    assert mark_if_execution_invalid(buf, STAGE_ENTRY_FILL, "nonpositive_raw_price") is True
    assert mark_if_execution_invalid(buf, STAGE_ENTRY_FILL, "nonpositive_raw_price") is True
    assert buf == ["entry_fill_rejected:nonpositive_raw_price"]     # 幂等，不堆叠
    assert mark_if_execution_invalid(buf, STAGE_ENTRY_FILL,
                                     "canceled_fell_back_into_box (x)") is False
    assert mark_if_execution_invalid(buf, STAGE_ENTRY_FILL, "some_new_reason") is False
    assert classify_reject("some_new_reason") == REJECT_CLASS_UNREGISTERED
    assert len(buf) == 1


def test_every_precision_reject_carries_class_and_is_traceable():
    """每条精度拒绝都必须带 class，且其 reason 能在 invalid_reasons 里找到。"""
    cand, df_1h, df_15m = _entry_scenario(100.0, 99.0)
    spec = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                 min_qty="0.01", min_notional="1000000")
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", instrument_spec=spec)
    rej = res["precision"]["rejects"]
    assert rej, "应当至少有一条精度拒绝"
    joined = " ".join(res["precision"]["invalid_reasons"])
    for r in rej:
        assert r["class"] == REJECT_CLASS_EXECUTION
        assert r["reason"] in joined
    assert res["precision"]["unclosed_positions"] == []   # 入场拒绝不产生未平仓


def test_stage_prefixes_are_byte_identical_to_pre_fix_literals():
    """三处出场前缀必须与修复前的硬编码字符串**逐字相同**。

    PRECISION-09 只是把散落的字符串收进分类表，没有改口径。这条用例把
    「收进表」这件事钉死：一旦有人改了前缀，既有 run 产物里的 invalid_reasons
    就会静默变形。
    """
    assert STAGE_EXIT_GAP == "exit_gap_fill_rejected"
    assert STAGE_EXIT_INTRABAR == "exit_intrabar_fill_rejected"
    assert STAGE_EXIT_EOD == "exit_eod_fill_rejected"
    # 入场三处是本轮新增（修复前根本不写），前缀按「阶段_动作_rejected」同构
    assert STAGE_ENTRY_FILL == "entry_fill_rejected"
    assert STAGE_ENTRY_QTY == "entry_qty_rejected"
    assert STAGE_ENTRY_NOTIONAL == "entry_notional_rejected"

# -*- coding: utf-8 -*-
"""[FIX-002] 成交精度校验与历史合约规格门禁专项回归测试。

覆盖三大核心场景与整改要求：
1. 低价币（高小数位精度）场景：真实合约规格生效，验证精度无损，严禁退化为 0.01 默认网格；
2. 合约规格缺失场景：彻底移除 0.01 tick 回退，强门禁拦截并记录标准原因码 MISSING_CONTRACT_SPEC / FALLBACK_TICK_BLOCKED；
3. 人工构造价格越界场景：撮合成交价超出 K 线 [low, high] 范围时严密拦截，记录标准原因码 EXEC_PRICE_OUT_OF_RANGE；
4. 数量精度与合法性校验：数量低于最小步进或异常时准确拦截，记录 INVALID_QTY_PRECISION；
5. 账本隔离标记校验：历史旧回测与未通过门禁运行打上 historical_research_only 标记。
"""
import math
import os
import sys
from decimal import Decimal
from pathlib import Path
import pytest

# 加入策略与工程路径
BASE_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "策略"))
sys.path.insert(0, str(BASE_DIR / "框架" / "backend"))

from 策略.WyckoffMaster.trading_core.fill import (
    SimulatedBarFillAdapter, price_breakdown, round_buy_price, round_sell_price
)
from 策略.WyckoffMaster.trading_core.rejects import (
    REJECT_MISSING_CONTRACT_SPEC, REJECT_FALLBACK_TICK_BLOCKED,
    REJECT_EXEC_PRICE_OUT_OF_RANGE, REJECT_INVALID_QTY_PRECISION,
    is_execution_validity, classify_reject, REJECT_CLASS_EXECUTION
)
from 策略.WyckoffMaster.trading_core.contracts import ExecutionInputError
from 策略.WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
from 框架.backend import replay_calc
from 框架.backend import paper_engine
from 框架.backend import backtest_batches


# ══════════════════════════ 1. 低价币（高小数位精度）场景 ══════════════════════════

def test_low_price_symbol_precision_preservation():
    """低价币（如 SPACEUSDT / AIGENSYNUSDT）在高精度 tick 下成交无损，禁止粗暴套用 0.01。"""
    raw_price = 0.004076
    real_tick = "0.0000010"
    wrong_tick = "0.01"

    # 在真实合约规格下，卖出价向更便宜取整，保留 6 位小数
    bd_real = price_breakdown(raw_price=raw_price, is_buy=False,
                              price_step=real_tick, slippage_rate=3e-4)
    assert bd_real["post_round_price"] != "0"
    assert Decimal(bd_real["post_round_price"]) > Decimal("0.004")

    # 在错误固定 0.01 步长下，向下取整直接变成 0.0（BACKTEST-LOSS-03 核心痛点）
    bd_wrong = price_breakdown(raw_price=raw_price, is_buy=False,
                               price_step=wrong_tick, slippage_rate=3e-4)
    assert Decimal(bd_wrong["post_round_price"]) == Decimal("0")


def test_low_price_entry_and_rounding():
    """低价币在获批的 round_buy_price 与 round_sell_price 下运算准确。"""
    step = 0.00001
    buy_rounded = round_buy_price(0.021271, step)
    assert buy_rounded == pytest.approx(0.02128, abs=1e-8)

    sell_rounded = round_sell_price(0.021279, step)
    assert sell_rounded == pytest.approx(0.02127, abs=1e-8)


# ══════════════════════════ 2. 合约规格缺失场景 ══════════════════════════

def test_paper_engine_fallback_tick_completely_removed(monkeypatch):
    """paper_engine 必须彻底移除 0.01 tick 回退，在跟踪推进中遇到缺失规格显式拒绝。"""
    class MockCursor:
        def fetchone(self):
            return {
                "position_id": 1,
                "account_id": "paper43_3a",
                "symbol": "UNKNOWN_COIN",
                "status": "open",
                "entry_price": 10.0,
                "stop_price": 9.5,
                "entry_ts": 1000000,
                "trail_d": 0.05,
            }

    class MockConn:
        def execute(self, *args, **kwargs):
            return MockCursor()
        def close(self):
            pass

    monkeypatch.setattr(paper_engine.L, "paper_conn", lambda arm: MockConn())
    monkeypatch.setattr(paper_engine, "contract_limits", lambda sym: None)

    candle_4h = {
        "symbol": "UNKNOWN_COIN",
        "open_time": 10000000,
        "close_time": 14400000,
        "open": 9.5,
        "high": 9.8,
        "low": 9.0,
        "close": 9.2,
        "available_at": 14400000,
    }

    res = paper_engine.advance_position_trailing(
        arm="3a", position_id=1, candle_4h=candle_4h, now_ms=15000000, trail_d=0.05
    )
    assert res["ok"] is False
    assert res["reason"] == REJECT_FALLBACK_TICK_BLOCKED
    assert res["spec_status"] == REJECT_MISSING_CONTRACT_SPEC


def test_replay_calc_missing_contract_spec_gate(monkeypatch):
    """replay_calc.plan_entry 在缺失合约规格时强门禁拦截并记录原因码。"""
    monkeypatch.setattr(replay_calc, "_limits_for", lambda sym: None)

    caliber = replay_calc.CALIBERS["策略包_3bps"]
    res = replay_calc.plan_entry(
        symbol="COIN_WITHOUT_SPEC",
        raw_open=100.0,
        prev_quote_volume=1_000_000.0,
        equity=2000.0,
        gross_open_notional=0.0,
        free_cash=2000.0,
        portfolio_risk_open=0.0,
        caliber=caliber,
    )
    assert res["status"] == "rejected"
    assert res["reject"] == REJECT_MISSING_CONTRACT_SPEC
    assert res["code"] == REJECT_MISSING_CONTRACT_SPEC


def test_engine_adapter_requires_instrument_spec():
    """回测执行适配器缺少合约规格且无显式步长时，直接抛错中止，严禁静默回退。"""
    with pytest.raises(ExecutionInputError) as exc_info:
        run_event_trend_backtest(
            symbol="TESTUSDT",
            df_1h=None,
            df_15m=None,
            candidates=[],
            funding_events=[],
            instrument_spec=None,
            price_step=None,
        )
    assert "instrument_spec_required" in str(exc_info.value)


# ══════════════════════════ 3. 人工构造价格越界场景 ══════════════════════════

def test_validate_execution_price_in_bar():
    """人工构造价格越界，validate_execution_price_in_bar 能准确捕获。"""
    bar_low = 0.02095
    bar_high = 0.02127

    # 正常价格：在 [low, high] 之间
    valid, dev, code = replay_calc.validate_execution_price_in_bar(
        0.02110, bar_low, bar_high
    )
    assert valid is True
    assert dev == 0.0
    assert code is None

    # 严重越界：如 BACKTEST-LOSS-03 中的 AIGENSYN（最高 0.02127，成交价 0.03）
    valid_out, dev_out, code_out = replay_calc.validate_execution_price_in_bar(
        0.03000, bar_low, bar_high
    )
    assert valid_out is False
    assert dev_out > 0.0
    assert code_out == REJECT_EXEC_PRICE_OUT_OF_RANGE

    # 向下越界
    valid_low, dev_low, code_low = replay_calc.validate_execution_price_in_bar(
        0.01500, bar_low, bar_high
    )
    assert valid_low is False
    assert dev_low < 0.0
    assert code_low == REJECT_EXEC_PRICE_OUT_OF_RANGE


def test_plan_entry_with_out_of_range_bar(monkeypatch):
    """plan_entry 在成交价越出执行 K 线范围时，强门禁拦截。"""
    # 模拟合约规格：price_step 为 0.01
    mock_limits = {
        "price_step": 0.01,
        "quantity_step": 1.0,
        "min_quantity": 1.0,
        "max_quantity": 100000.0,
        "min_notional": 5.0,
    }
    monkeypatch.setattr(replay_calc, "_limits_for", lambda sym: mock_limits)

    caliber = replay_calc.CALIBERS["策略包_3bps"]
    # raw_open=0.021，按 step=0.01 取整会变成 0.03
    # 执行 K 线 [bar_low=0.02095, bar_high=0.02127]
    res = replay_calc.plan_entry(
        symbol="OUT_OF_RANGE_COIN",
        raw_open=0.021,
        prev_quote_volume=1_000_000.0,
        equity=2000.0,
        gross_open_notional=0.0,
        free_cash=2000.0,
        portfolio_risk_open=0.0,
        caliber=caliber,
        bar_low=0.02095,
        bar_high=0.02127,
    )
    assert res["status"] == "rejected"
    assert res["reject"] == REJECT_EXEC_PRICE_OUT_OF_RANGE
    assert res["code"] == REJECT_EXEC_PRICE_OUT_OF_RANGE


def test_scan_exit_price_out_of_range_flag():
    """scan_exit 在退出成交价超出当根 bar 范围时，标记 price_valid=False 并输出原因码。"""
    bars_15m = [{
        "open_time": 1000000,
        "open": 10.0,
        "low": 9.8,
        "high": 10.2,
    }]
    stop_path = [{"effective_ms": 1000000, "stop": 9.0}]

    # 盘中打穿但退出价格被人工设到异常范围之外
    # 当 stop=9.0 时，由于 low=9.8 > 9.0，正常不退出
    bars_hit = [{
        "open_time": 1000000,
        "open": 9.5,
        "low": 8.0,
        "high": 8.5,  # 异常 bar：最高价 8.5 远低于 stop 9.0
    }]
    res = replay_calc.scan_exit(
        bars_15m=bars_hit,
        entry_ms=500000,
        entry_bar_ms=500000,
        stop_path=stop_path,
        initial_stop=9.0,
        gap_model="stop_price"
    )
    assert res["hit"] is True
    assert res["price_valid"] is False
    assert res["reject_code"] == REJECT_EXEC_PRICE_OUT_OF_RANGE


# ══════════════════════════ 4. 下单数量与精度异常场景 ══════════════════════════

def test_invalid_qty_precision_handling(monkeypatch):
    """下单数量不满足最小步进时，拒绝并落盘 INVALID_QTY_PRECISION。"""
    mock_limits = {
        "price_step": 0.01,
        "quantity_step": 100.0,  # 步进非常大
        "min_quantity": 100.0,
        "max_quantity": 10000.0,
        "min_notional": 5.0,
    }
    monkeypatch.setattr(replay_calc, "_limits_for", lambda sym: mock_limits)

    caliber = replay_calc.CALIBERS["策略包_3bps"]
    res = replay_calc.plan_entry(
        symbol="HIGH_STEP_COIN",
        raw_open=100.0,
        prev_quote_volume=1_000_000.0,
        equity=2000.0,
        gross_open_notional=0.0,
        free_cash=2000.0,
        portfolio_risk_open=0.0,
        caliber=caliber,
    )
    assert res["status"] == "rejected"
    assert res["reject"] == REJECT_INVALID_QTY_PRECISION
    assert res["code"] == REJECT_INVALID_QTY_PRECISION


# ══════════════════════════ 5. 拒单原因码分类校验 ══════════════════════════

def test_reject_codes_registered_in_execution_validity():
    """新增的四大标准化原因码必须被正确识别为执行有效性错误。"""
    for code in (
        REJECT_MISSING_CONTRACT_SPEC,
        REJECT_FALLBACK_TICK_BLOCKED,
        REJECT_EXEC_PRICE_OUT_OF_RANGE,
        REJECT_INVALID_QTY_PRECISION,
    ):
        assert is_execution_validity(code) is True
        assert classify_reject(code) == REJECT_CLASS_EXECUTION


# ══════════════════════════ 6. 账本隔离标记校验 ══════════════════════════

def test_backtest_batches_marks_historical_research_only(tmp_path):
    """旧回测或未获正式放行的运行统一标记为 historical_research_only。"""
    run_dir = tmp_path / "run_sample_01"
    run_dir.mkdir()
    # 模拟未正式放行的旧 summary.json
    summary_file = run_dir / "summary.json"
    summary_file.write_text('{"symbol": "BTCUSDT", "formal_pass_allowed": false, "summary_variant_a": {"formal_pass_allowed": false}}', encoding="utf-8")

    facts = backtest_batches._run_facts(run_dir)
    assert facts["historical_research_only"] is True

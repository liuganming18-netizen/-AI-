# -*- coding: utf-8 -*-
"""FT-BASELINE-04 单元测试：01_test_p0_and_stoploss_unit.py
验证：
1. confirm_trade_entry 在缺少有效候选/P0时直接拒单；
2. custom_stoploss 在缺少有效候选/P0时抛出 RuntimeError，严禁假止损；
3. 有效候选时，首次回调精确锁定真实 P0；
4. 2R 动态跟踪止损逻辑与只升不降单调性；
5. 跌破跟踪止损位返回 0.0 触发出场。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
# 不依赖 pytest，使用原生 math.isclose
def approx_equal(a, b, tol=1e-5):
    return math.isclose(a, b, abs_tol=tol)

import os
import sys

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import importlib.util

strat_path = os.path.join(BASE_DIR, "evidence/FT-BASELINE-04/user_data/strategies/WyckoffEventTrendBaseline.py")
spec = importlib.util.spec_from_file_location("WyckoffEventTrendBaseline", strat_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
WyckoffEventTrendBaseline = mod.WyckoffEventTrendBaseline


class DummyCandidate:
    def __init__(self, candidate_id: str, p0: float, h: float, max_p: float):
        self.candidate_id = candidate_id
        self.initial_stop_price = p0
        self.box_high_h = h
        self.max_entry_price = max_p
        self.snapshot_time_ms = 1755324000000


def test_confirm_trade_entry():
    strat = WyckoffEventTrendBaseline(config={"datadir": "dummy", "exchange": {"pair_whitelist": []}})
    pair = "TEST/USDT:USDT"
    
    # 场景 1：无候选 -> 拒单
    strat.pair_candidates[pair] = []
    assert not strat.confirm_trade_entry(
        pair=pair, order_type="limit", amount=100.0, rate=1.0,
        time_in_force="GTC", current_time=datetime(2025, 8, 16, 6, 15, tzinfo=timezone.utc),
        entry_tag="cand_none", side="long"
    )
    
    # 场景 2：有候选但 P0 无效 (None 或 <= 0) -> 拒单
    strat.pair_candidates[pair] = [DummyCandidate("cand_bad_p0", None, 0.9, 1.2)]
    assert not strat.confirm_trade_entry(
        pair=pair, order_type="limit", amount=100.0, rate=1.0,
        time_in_force="GTC", current_time=datetime(2025, 8, 16, 6, 15, tzinfo=timezone.utc),
        entry_tag="cand_bad_p0", side="long"
    )

    strat.pair_candidates[pair] = [DummyCandidate("cand_neg_p0", -0.5, 0.9, 1.2)]
    assert not strat.confirm_trade_entry(
        pair=pair, order_type="limit", amount=100.0, rate=1.0,
        time_in_force="GTC", current_time=datetime(2025, 8, 16, 6, 15, tzinfo=timezone.utc),
        entry_tag="cand_neg_p0", side="long"
    )

    # 场景 3：P0 >= rate -> 拒单
    strat.pair_candidates[pair] = [DummyCandidate("cand_high_p0", 1.1, 0.9, 1.2)]
    assert not strat.confirm_trade_entry(
        pair=pair, order_type="limit", amount=100.0, rate=1.0,
        time_in_force="GTC", current_time=datetime(2025, 8, 16, 6, 15, tzinfo=timezone.utc),
        entry_tag="cand_high_p0", side="long"
    )

    # 场景 4：入场价超过 max_entry_price -> 拒单
    strat.pair_candidates[pair] = [DummyCandidate("cand_ok", 0.8, 0.9, 1.05)]
    assert not strat.confirm_trade_entry(
        pair=pair, order_type="limit", amount=100.0, rate=1.10,
        time_in_force="GTC", current_time=datetime(2025, 8, 16, 6, 15, tzinfo=timezone.utc),
        entry_tag="cand_ok", side="long"
    )

    # 场景 5：正常有效候选 -> 放行
    assert strat.confirm_trade_entry(
        pair=pair, order_type="limit", amount=100.0, rate=1.0,
        time_in_force="GTC", current_time=datetime(2025, 8, 16, 6, 15, tzinfo=timezone.utc),
        entry_tag="cand_ok", side="long"
    )
    print("test_confirm_trade_entry: 全部 5 项边界校验通过！")


def test_custom_stoploss_exception_on_missing():
    strat = WyckoffEventTrendBaseline(config={"datadir": "dummy", "exchange": {"pair_whitelist": []}})
    pair = "TEST/USDT:USDT"
    strat.pair_candidates[pair] = []
    
    trade = MagicMock()
    trade.id = 1
    trade.enter_tag = "cand_missing"
    trade.open_rate = 1.0
    trade.open_date_utc = datetime(2025, 8, 16, 6, 15, tzinfo=timezone.utc)
    
    # 必须抛出 RuntimeError，绝不能返回 0.90 假止损
    try:
        strat.custom_stoploss(
            pair=pair, trade=trade, current_time=datetime(2025, 8, 16, 6, 30, tzinfo=timezone.utc),
            current_rate=1.0, current_profit=0.0, after_fill=False
        )
        assert False, "未能成功拦截缺失候选！"
    except RuntimeError as e:
        assert "严禁静默使用假止损" in str(e)
        print("test_custom_stoploss_exception_on_missing: 成功捕获并拦截假止损！")


def test_custom_stoploss_valid_flow():
    strat = WyckoffEventTrendBaseline(config={"datadir": "dummy", "exchange": {"pair_whitelist": []}})
    pair = "TEST/USDT:USDT"
    cand = DummyCandidate("cand_valid", p0=0.80, h=0.90, max_p=1.10)
    strat.pair_candidates[pair] = [cand]
    
    # 模拟 4h 序列
    df_4h = pd.DataFrame([
        {"close_ms": 1755345600000, "close": 1.20, "low": 0.85, "l10": 0.85}, # 2R阈值: 1.0 + 2*0.2 = 1.4, 未达
        {"close_ms": 1755360000000, "close": 1.45, "low": 1.10, "l10": 1.10}, # 达到 1.45 >= 1.4, 激活！止损抬升至 1.10
        {"close_ms": 1755374400000, "close": 1.30, "low": 1.05, "l10": 1.05}, # 回调，L10为1.05，但单调性保持 1.10
    ])
    strat.pair_4h_df[pair] = df_4h
    
    trade = MagicMock()
    trade.id = 100
    trade.enter_tag = "cand_valid"
    trade.open_rate = 1.0
    trade.open_date_utc = datetime(2025, 8, 16, 6, 15, tzinfo=timezone.utc)
    
    # 1. 首次调用，4h未闭合，返回真实 P0 (0.80 -> 相对 1.0 为 -0.20)
    r1 = strat.custom_stoploss(pair, trade, datetime(2025, 8, 16, 6, 30, tzinfo=timezone.utc), 1.0, 0.0, False)
    assert approx_equal(r1, -0.20)
    
    # 2. 第二根 4h 闭合，突破 2R，止损抬升至 1.10 (现价 1.45，止损相对比例: (1.10 - 1.45)/1.45)
    t_post_2r = datetime.fromtimestamp(1755360000000/1000, tz=timezone.utc)
    r2 = strat.custom_stoploss(pair, trade, t_post_2r, 1.45, 0.45, False)
    expected_ratio = (1.10 - 1.45) / 1.45
    assert approx_equal(r2, expected_ratio)
    
    # 3. 第三根 4h 回调，L10 为 1.05，但止损维持 1.10 (只升不降)
    t_callback = datetime.fromtimestamp(1755374400000/1000, tz=timezone.utc)
    r3 = strat.custom_stoploss(pair, trade, t_callback, 1.30, 0.30, False)
    expected_ratio_3 = (1.10 - 1.30) / 1.30
    assert approx_equal(r3, expected_ratio_3)
    
    # 4. 价格跌破 1.10 (如 1.09)，立即返回 0.0 触发出场
    r4 = strat.custom_stoploss(pair, trade, t_callback, 1.09, 0.09, False)
    assert r4 == 0.0
    print("test_custom_stoploss_valid_flow: 真实P0、2R激活、只升不降及出场校验全部通过！")


if __name__ == "__main__":
    test_confirm_trade_entry()
    test_custom_stoploss_exception_on_missing()
    test_custom_stoploss_valid_flow()
    print("全部单元测试 100% 通过！")

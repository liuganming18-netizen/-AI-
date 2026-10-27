# -*- coding: utf-8 -*-
"""FT-BASELINE-03 单元测试：03_test_strategy_unit.py

验证：
1. 策略成功被 StrategyResolver 加载；
2. 初始建仓时，custom_stoploss 返回的止损位精准对应 P0；
3. 开仓后首次回调时，未触及 P0 不出场，触及 P0 立即出场；
4. 4h 收盘达到 2R 阈值后，状态正确置为 is_2r_activated = True，并更新止损位至 L10；
5. 验证 trade.max_rate 为 None 时 0 报错、0 崩溃。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
import pandas as pd
import numpy as np

from freqtrade.configuration import Configuration
from freqtrade.resolvers import StrategyResolver
from freqtrade.persistence import Trade

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"

def main():
    config = Configuration.from_files(["evidence/FT-BASELINE-03/user_data/config.json"])
    config["strategy"] = "WyckoffEventTrendBaseline"
    config["strategy_path"] = "evidence/FT-BASELINE-03/user_data/strategies"
    strategy = StrategyResolver.load_strategy(config)
    print("策略加载成功:", strategy.__class__.__name__)

    # 构造模拟候选与 4h 数据
    pair = "PUMPBTC/USDT:USDT"
    from 策略.WyckoffMaster.event_trend_v0_1.contracts import CandidateSnapshot
    mock_cand = CandidateSnapshot(
        candidate_id="mock_pump_cand",
        event_id="ev_mock",
        strategy_version="v0.1.0",
        available_at_ms=1757116800000,
        symbol="PUMPBTCUSDT",
        venue="binance",
        snapshot_time_ms=1757116800000, # 2025-09-06 00:00:00
        execution_earliest_ms=1757117700000, # 2025-09-06 00:15:00
        box_high_h=0.04224,
        box_low_l=0.034144,
        box_atr_a=0.0017,
        box_er=0.15,
        retest_low_lr=0.035,
        sweep_low_s=0.034,
        breakout_close=0.04251,
        breakout_volume=100000.0,
        v20_median_volume=50000.0,
        volume_ratio=2.0,
        oi_breakout=200000.0,
        oi_6h_ago=150000.0,
        oi_growth_6h=0.33,
        initial_stop_price=0.034144,
        initial_risk_r=0.04227 - 0.034144, # 0.008126
        min_entry_price=0.04224,
        max_entry_price=0.043088
    )
    strategy.pair_candidates[pair] = [mock_cand]

    # 构造模拟已收盘 4h 数据
    # entry_price = 0.04227, R = 0.008126, 2R trigger = 0.058522
    # 模拟 3 根 4h：
    # bar 1: close = 0.045 (未触发 2R), low = 0.041, l10 = 0.035
    # bar 2: close = 0.060 (触发 2R!), low = 0.050, l10 = 0.048
    # bar 3: close = 0.070 (继续上升), low = 0.062, l10 = 0.055
    df_4h_mock = pd.DataFrame([
        {"close_ms": 1757131200000, "close": 0.045, "low": 0.041, "l10": 0.035}, # 09-06 04:00
        {"close_ms": 1757145600000, "close": 0.060, "low": 0.050, "l10": 0.048}, # 09-06 08:00
        {"close_ms": 1757160000000, "close": 0.070, "low": 0.062, "l10": 0.055}, # 09-06 12:00
    ])
    strategy.pair_4h_df[pair] = df_4h_mock

    # 模拟 Trade 对象
    trade = Trade(
        id=1,
        pair=pair,
        open_rate=0.04227,
        open_date=datetime(2025, 9, 6, 0, 15, tzinfo=timezone.utc),
        amount=1223.0,
        fee_open=0.0005,
        fee_close=0.0005,
        enter_tag="mock_pump_cand"
    )
    trade.max_rate = None # 模拟真实开仓瞬时 max_rate 为 None 的极端情况！

    # 1. 验证开仓首次回调（未收盘 4h）
    t_curr = datetime(2025, 9, 6, 0, 30, tzinfo=timezone.utc)
    sl_ratio = strategy.custom_stoploss(pair, trade, t_curr, current_rate=0.04200, current_profit=-0.006, after_fill=False)
    # 预期止损价应为 P0 (0.034144)
    expected_stop = 0.034144
    calc_stop = 0.04200 * (1.0 + sl_ratio)
    print(f"首次调用 (trade.max_rate=None): sl_ratio={sl_ratio:.6f}, 换算止损价={calc_stop:.6f} (预期 P0={expected_stop:.6f})")
    assert abs(calc_stop - expected_stop) < 1e-6, "首次调用止损价未能锚定 P0！"

    # 2. 验证 4h 收盘未达 2R 时（09-06 05:00）
    t_curr2 = datetime(2025, 9, 6, 5, 0, tzinfo=timezone.utc)
    sl_ratio2 = strategy.custom_stoploss(pair, trade, t_curr2, current_rate=0.04600, current_profit=0.088, after_fill=False)
    calc_stop2 = 0.04600 * (1.0 + sl_ratio2)
    print(f"bar 1 (未触发2R): 换算止损价={calc_stop2:.6f} (应维持 P0={expected_stop:.6f})")
    assert abs(calc_stop2 - expected_stop) < 1e-6, "未达 2R 前不应抬升止损！"
    assert not strategy.active_trade_stop_state[1]["is_2r_activated"]

    # 3. 验证 4h 收盘达到 2R 时（09-06 09:00，bar 2 close = 0.060 >= 0.058522）
    t_curr3 = datetime(2025, 9, 6, 9, 0, tzinfo=timezone.utc)
    sl_ratio3 = strategy.custom_stoploss(pair, trade, t_curr3, current_rate=0.06200, current_profit=0.466, after_fill=False)
    calc_stop3 = 0.06200 * (1.0 + sl_ratio3)
    print(f"bar 2 (已触发2R): 换算止损价={calc_stop3:.6f} (应抬升至 L10=0.048000)")
    assert strategy.active_trade_stop_state[1]["is_2r_activated"], "2R 应当已被激活！"
    assert abs(calc_stop3 - 0.048000) < 1e-6, "触发 2R 后未能正确抬升至 L10！"

    # 4. 验证跟踪止损只上调不下调
    # 模拟下一根 4h L10 出现回调（例如 0.045 < 0.048）
    strategy.pair_4h_df[pair] = pd.DataFrame([
        {"close_ms": 1757131200000, "close": 0.045, "low": 0.041, "l10": 0.035},
        {"close_ms": 1757145600000, "close": 0.060, "low": 0.050, "l10": 0.048},
        {"close_ms": 1757160000000, "close": 0.065, "low": 0.040, "l10": 0.045}, # 回调的 L10
    ])
    t_curr4 = datetime(2025, 9, 6, 13, 0, tzinfo=timezone.utc)
    sl_ratio4 = strategy.custom_stoploss(pair, trade, t_curr4, current_rate=0.06300, current_profit=0.49, after_fill=False)
    calc_stop4 = 0.06300 * (1.0 + sl_ratio4)
    print(f"L10 回调时: 换算止损价={calc_stop4:.6f} (应保持原高位 0.048000)")
    assert abs(calc_stop4 - 0.048000) < 1e-6, "跟踪止损发生错误下调！"

    # 5. 验证触及止损立即出场（current_rate <= stop）
    t_curr5 = datetime(2025, 9, 6, 14, 0, tzinfo=timezone.utc)
    sl_ratio5 = strategy.custom_stoploss(pair, trade, t_curr5, current_rate=0.04700, current_profit=0.11, after_fill=False)
    print(f"跌破止损位 (current_rate=0.04700 < stop=0.04800): 返回 sl_ratio={sl_ratio5} (应返回 0.0 触发市价出场)")
    assert sl_ratio5 == 0.0, "跌破止损位时应返回 0.0 立即市价出场！"

    print("\n>>> 全部 5 项单元测试 100% 通过！止损适配逻辑完美闭环！<<<")

if __name__ == "__main__":
    main()

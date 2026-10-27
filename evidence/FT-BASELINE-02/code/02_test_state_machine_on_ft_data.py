"""
测试已有 EventStateMachine 在 FT-DATA-01 数据上的运行
验证是否能精确产出与旧引擎基准完全一致的候选 (时间、价格、P0、R)
"""

import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
sys.path.insert(0, BASE_DIR)

from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine
from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series

DATA_DIR = os.path.join(BASE_DIR, "evidence/FT-DATA-01/data/freqtrade/futures")

def test_symbol_candidates(symbol_clean: str):
    prefix = f"{symbol_clean}_USDT_USDT"
    
    # 1. 加载 4h, 1h, 5m OI
    f_4h = os.path.join(DATA_DIR, f"{prefix}-4h-futures.feather")
    f_1h = os.path.join(DATA_DIR, f"{prefix}-1h-futures.feather")
    f_oi = os.path.join(DATA_DIR, f"{prefix}-5m-open_interest.feather")
    
    df_4h = pd.read_feather(f_4h)
    df_1h = pd.read_feather(f_1h)
    df_oi = pd.read_feather(f_oi)
    
    # 转换时间戳为 ms
    df_4h["open_time_ms"] = df_4h["date"].astype("int64")
    df_4h["close_time_ms"] = df_4h["open_time_ms"] + 4 * 3600 * 1000
    
    df_1h["open_time_ms"] = df_1h["date"].astype("int64")
    df_1h["close_time_ms"] = df_1h["open_time_ms"] + 1 * 3600 * 1000
    
    oi_ts = df_oi["date"].astype("int64").to_numpy()
    oi_vals = df_oi["open_interest_amount"].to_numpy(dtype=np.float64)
    
    # 2. 计算 4h ATR(14)
    highs_4h = df_4h["high"].to_numpy(dtype=np.float64)
    lows_4h = df_4h["low"].to_numpy(dtype=np.float64)
    closes_4h = df_4h["close"].to_numpy(dtype=np.float64)
    df_4h["atr"] = compute_wilder_atr_series(highs_4h, lows_4h, closes_4h, period=14)
    
    # 测试两种模式：A) 从 2025-08-01 开始 1h (与回测窗口一致)；B) 从全部可用 1h 开始
    for start_from_aug in [True, False]:
        sm = EventStateMachine(config=EventTrendConfig(), symbol=f"{symbol_clean}USDT", enable_oi_filter=True)
        sm.pit.register_oi(oi_ts, oi_vals)
        
        target_start_ms = int(pd.to_datetime("2025-08-01 00:00:00", utc=True).value // 10**6)
        
        if start_from_aug:
            sub_1h = df_1h[df_1h["open_time_ms"] >= target_start_ms].copy()
        else:
            sub_1h = df_1h.copy()
            
        records_1h = sub_1h.to_dict("records")
        for r in records_1h:
            t_open = int(r["open_time_ms"])
            t_close = int(r["close_time_ms"])
            
            # 4h 节点检查建箱体
            if t_close % (4 * 3600 * 1000) == 0:
                sub_4h = df_4h[df_4h["close_time_ms"] <= t_close]
                if len(sub_4h) >= 42:
                    last_42 = sub_4h.tail(42)
                    atr_val = float(sub_4h["atr"].iloc[-1])
                    sm.check_and_form_box(
                        bar_4h_close_ms=t_close,
                        history_42_highs=last_42["high"].to_numpy(np.float64),
                        history_42_lows=last_42["low"].to_numpy(np.float64),
                        history_42_closes=last_42["close"].to_numpy(np.float64),
                        current_atr_a=atr_val
                    )
                    
            # 推进 1h bar
            sm.process_1h_bar(
                open_time_ms=t_open,
                open_p=float(r["open"]),
                high_p=float(r["high"]),
                low_p=float(r["low"]),
                close_p=float(r["close"]),
                volume_v=float(r["volume"]),
                oi_timestamps=oi_ts,
                oi_values=oi_vals
            )
            
        print(f"[{symbol_clean}] (start_from_aug={start_from_aug}) 生成候选数: {len(sm.candidates)}")
        for c in sm.candidates:
            dt_cand = pd.to_datetime(c.snapshot_time_ms, unit="ms", utc=True)
            dt_exec = pd.to_datetime(c.execution_earliest_ms, unit="ms", utc=True)
            print(f"  -> ID: {c.candidate_id}")
            print(f"     突破确认 (1h收盘): {dt_cand}")
            print(f"     最早执行 (15m开盘): {dt_exec}")
            print(f"     突破收盘价: {c.breakout_close}, 初始止损 P0: {c.initial_stop_price}")
            print(f"     箱体高 H: {c.box_high_h}, 最大买价: {c.max_entry_price}")
            print(f"     6h OI 增幅: {c.oi_growth_6h:.4f}")

if __name__ == "__main__":
    symbols = ["AR", "BANANA", "BAT", "CTSI", "PUMPBTC"]
    for s in symbols:
        test_symbol_candidates(s)

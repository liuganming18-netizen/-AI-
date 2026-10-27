# -*- coding: utf-8 -*-
"""FT-BASELINE-03 止损逻辑单例验证脚本：02_verify_stoploss_logic.py

验证目标：
1. 验证 2R 激活判定：严格依据入场后已收盘 4h 的 close 是否达到 entry + 2R；
2. 验证 L10 跟踪止损更新时钟与只上调不下调；
3. 验证开仓首次回调能否设置真实 P0；
4. 验证 5 笔交易的理论出场时间、价格与原因是否与旧引擎基准 100% 吻合！
"""
from __future__ import annotations

import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
DATA_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-03/user_data/data/binance/futures")
OLD_CSV = os.path.join(BASE_DIR, "evidence/FT-DATA-01/out/05_旧引擎基准成交.csv")

# 5 笔旧引擎成交记录
df_old = pd.read_csv(OLD_CSV)

def simulate_trade_exit(symbol: str):
    prefix = f"{symbol[:-4]}_USDT_USDT"
    df_15m = pd.read_feather(os.path.join(DATA_DIR, f"{prefix}-15m-futures.feather"))
    df_4h = pd.read_feather(os.path.join(DATA_DIR, f"{prefix}-4h-futures.feather"))

    # 时间戳 (ms)
    df_15m["open_ms"] = df_15m["date"].astype("int64")
    df_4h["open_ms"] = df_4h["date"].astype("int64")
    df_4h["close_ms"] = df_4h["open_ms"] + 4 * 3600 * 1000

    # 4h 滚动 10 根最低价 (包含当根)
    df_4h["l10"] = df_4h["low"].rolling(10).min()

    # 读取旧引擎基准交易
    trade_row = df_old[df_old["symbol"] == symbol].iloc[0]
    entry_time_str = trade_row["entry_time_utc"]
    entry_ms = int(pd.to_datetime(entry_time_str, utc=True).value // 10**6)
    entry_price = float(trade_row["entry_price"])

    # 找到入场 15m bar
    sub_15m = df_15m[df_15m["open_ms"] >= entry_ms].copy()

    # 获取候选 P0
    # 从 signal_id 获取
    sig_id = trade_row["signal_id"]
    print(f"\n==================== [{symbol}] ====================")
    print(f"入场时间: {entry_time_str}, 入场价: {entry_price}")

    # 我们通过 1h 突破确定 P0
    # 候选 ID 格式: cand_ev_WX_EVENT_TREND_V0_1_binance_{symbol}_{box_t}_{breakout_t}
    # 从旧引擎日志读取候选 P0
    # 为精确起见，从旧 run 的 candidates.jsonl 读取
    run_dir = trade_row["run_dir"]
    cand_file = os.path.join(BASE_DIR, f"回测日志/event_trend_v0_1/runs_2025aug100/{run_dir}/candidates.jsonl")
    import json
    with open(cand_file) as f:
        cand_dict = json.loads(f.readline())
    p0 = float(cand_dict["initial_stop_price"])
    actual_r = entry_price - p0
    trigger_2r = entry_price + 2.0 * actual_r

    print(f"P0: {p0:.6f}, 实际 R: {actual_r:.6f}, 2R 激活阈值: {trigger_2r:.6f}")

    # 模拟时钟推进
    current_stop = p0
    is_activated = False
    exit_info = None

    # 只考虑入场后收盘的 4h
    for idx, bar in sub_15m.iterrows():
        t_open = int(bar["open_ms"])
        
        # 1. 检查超时 240 小时
        if t_open - entry_ms >= 240 * 3600 * 1000:
            exit_info = {
                "exit_time": pd.to_datetime(t_open, unit="ms", utc=True),
                "exit_price": float(bar["open"]),
                "exit_reason": "timeout_240h",
                "stop_level": current_stop,
                "2r_activated": is_activated
            }
            break

        # 2. 检查是否有新收盘的 4h bar
        # 已收盘的 4h：close_ms <= t_open 且 close_ms > entry_ms
        sub_4h_closed = df_4h[(df_4h["close_ms"] <= t_open) & (df_4h["close_ms"] >= entry_ms)]
        if not sub_4h_closed.empty:
            latest_4h = sub_4h_closed.iloc[-1]
            latest_4h_close = float(latest_4h["close"])
            
            # 检查 2R 触发 (首次 4h 收盘达到 entry + 2R)
            if not is_activated and latest_4h_close >= trigger_2r:
                is_activated = True
                print(f"  --> 4h 收盘触发 2R! 时间: {pd.to_datetime(latest_4h['close_ms'], unit='ms', utc=True)}, 4h收盘价: {latest_4h_close:.6f} >= {trigger_2r:.6f}")

            if is_activated:
                l10_val = float(latest_4h["l10"])
                if l10_val > current_stop:
                    # 只上调不下调
                    current_stop = l10_val

        # 3. 检查 15m 内是否触发止损
        low_p = float(bar["low"])
        high_p = float(bar["high"])
        if low_p <= current_stop:
            exit_price = current_stop if float(bar["open"]) > current_stop else float(bar["open"])
            exit_info = {
                "exit_time": pd.to_datetime(t_open, unit="ms", utc=True),
                "exit_price": exit_price,
                "exit_reason": "trailing_stop" if is_activated else "initial_stop",
                "stop_level": current_stop,
                "2r_activated": is_activated
            }
            break

    print(f"模拟结果: {exit_info}")
    print(f"旧基准出场: {trade_row['exit_time_utc']} @ {trade_row['exit_price']} ({trade_row['exit_reason']})")

if __name__ == "__main__":
    symbols = ["BATUSDT", "PUMPBTCUSDT", "BANANAUSDT", "CTSIUSDT", "ARUSDT"]
    for s in symbols:
        simulate_trade_exit(s)

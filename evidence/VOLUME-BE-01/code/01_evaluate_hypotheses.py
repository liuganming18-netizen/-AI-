# -*- coding: utf-8 -*-
"""VOLUME-BE-01 · 步骤一：单独评估入场过滤与持仓退出两个假设。

严格因果律规范：
1. 入场过滤：决策时刻只能读取 entry_time_ms 之前已完全闭合的 1h / 15m K 线；
2. 持仓退出：持仓中只能根据已收盘的前一根 15m K 线量价，在下一根开盘价撮合退出；
3. 同根冲突：同根若同时触发初始止损与缩量退出，严格按原止损退出（保守不利路径）。
"""
from __future__ import annotations

import csv
import json
import os
import sys
import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
for _p in (ROOT, f"{ROOT}/策略", f"{ROOT}/框架/backend"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner

BE2_PAIRED = os.path.join(ROOT, "evidence/BREAKEVEN-02/out/paired_fixed.csv")
OUT_DIR = os.path.join(ROOT, "evidence/VOLUME-BE-01/out")
os.makedirs(OUT_DIR, exist_ok=True)

TAKER_FEE = 0.0005
SLIPPAGE = 0.0003


def load_signals():
    signals = []
    with open(BE2_PAIRED, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            signals.append(r)
    return signals


def evaluate():
    signals = load_signals()
    runner = EventTrendRunner(base_dir=ROOT)

    # 缓存窗口数据，避免重复加载
    window_data_cache = {}

    results = []

    print(f"Total signals to evaluate: {len(signals)}")
    for idx, sig in enumerate(signals):
        sym = sig["symbol"]
        win = sig["window"]
        cache_key = (sym, win)

        if cache_key not in window_data_cache:
            start_d, end_d = win.split("~")
            df_4h, df_1h, df_15m, oi_ts, oi_vals, fund = runner.load_symbol_data(
                sym, start_date=start_d, end_date=end_d, allow_auto_window=False
            )
            # 计算 1h 滚动成交量均值 (过去 20 根)
            df_1h["vol_sma20"] = df_1h["volume"].rolling(20, min_periods=5).mean()
            # 计算 15m 滚动成交量均值 (过去 20 根)
            df_15m["vol_sma20"] = df_15m["volume"].rolling(20, min_periods=5).mean()
            window_data_cache[cache_key] = {
                "df_1h": df_1h,
                "df_15m": df_15m,
                "fund": fund,
            }

        data = window_data_cache[cache_key]
        df_1h = data["df_1h"]
        df_15m = data["df_15m"]

        entry_ms = int(sig["entry_time_ms"])
        entry_px = float(sig["entry_price"])
        qty = float(sig["qty"])
        init_stop = float(sig["initial_stop"])
        a_net = float(sig["A_net"])
        a_exit_px = float(sig["A_exit_price"])
        a_exit_utc = sig["A_exit_time_utc"]
        a_reason = sig["A_reason"]
        a_hold_h = float(sig["A_hold_h"])

        # -------------------------------------------------------------
        # 假设 1：入场过滤特征提取 (仅用 entry_ms 之前已闭合的 1h 数据)
        # -------------------------------------------------------------
        # 找到 entry_ms 之前已闭合的 1h bar (close_time <= entry_ms)
        h1_closed = df_1h[df_1h["timestamp"] < entry_ms]
        if len(h1_closed) >= 2:
            last_1h = h1_closed.iloc[-1]
            prev_1h = h1_closed.iloc[-2]

            last_1h_close = float(last_1h["close"])
            last_1h_open = float(last_1h["open"])
            last_1h_vol = float(last_1h["volume"])
            last_1h_volsma = float(last_1h["vol_sma20"]) if pd.notnull(last_1h["vol_sma20"]) else last_1h_vol

            # 入场前一根 1h 是否缩量下跌: 收阴且 volume < sma20
            is_entry_last_1h_vol_down = (last_1h_close < last_1h_open) and (last_1h_vol < last_1h_volsma)
            # 入场前一根 1h 是否放量下跌: 收阴且 volume >= sma20
            is_entry_last_1h_high_vol_down = (last_1h_close < last_1h_open) and (last_1h_vol >= last_1h_volsma)
            # 突破 bar (前一根已闭合1h) 是否放量: volume >= sma20
            is_entry_signal_1h_vol_expansion = (last_1h_vol >= last_1h_volsma)
        else:
            is_entry_last_1h_vol_down = False
            is_entry_last_1h_high_vol_down = False
            is_entry_signal_1h_vol_expansion = True

        # -------------------------------------------------------------
        # 假设 2：持仓退出特征提取 (按 15m bar 推进，严格用前一根闭合 bar)
        # -------------------------------------------------------------
        # 筛选出 entry_ms 之后的 15m bars，直到原出场或最长持仓
        m15_during = df_15m[df_15m["timestamp"] >= entry_ms].copy()

        pos_exit_triggered = False
        pos_exit_time_ms = None
        pos_exit_px = None
        pos_exit_bar_idx = -1

        # 逐根 15m 推进模拟持仓退出
        # 条件：当某一根已闭合 15m bar 满足：收盘价低于开仓价 entry_px 且 volume < vol_sma20（缩量阴跌）
        # 则在下一根 15m bar 的开盘价撮合退出！
        # 但如果当根最低价已经跌破 init_stop，则按原止损退出，不使用有利市价逃脱。
        for b_idx in range(len(m15_during) - 1):
            curr_bar = m15_during.iloc[b_idx]
            next_bar = m15_during.iloc[b_idx + 1]

            # 检查当前 bar 是否已经触及初始止损
            if float(curr_bar["low"]) <= init_stop:
                # 触及原止损，已由原策略止损逻辑处理
                break

            curr_c = float(curr_bar["close"])
            curr_o = float(curr_bar["open"])
            curr_v = float(curr_bar["volume"])
            curr_vsma = float(curr_bar["vol_sma20"]) if pd.notnull(curr_bar["vol_sma20"]) else curr_v

            # 缩量下跌判定：收跌 (close < open 且 close < entry_px) 且 volume < vol_sma20
            if (curr_c < curr_o) and (curr_c < entry_px) and (curr_v < curr_vsma):
                # 满足缩量下跌，在下一根开盘执行平仓
                next_o = float(next_bar["open"])
                next_l = float(next_bar["low"])

                # 如果下一根开盘直接跳空跌破止损，按原跳空止损
                if next_o <= init_stop:
                    break

                # 否则以 next_o 撮合退出（考虑滑点与手续费）
                pos_exit_triggered = True
                pos_exit_time_ms = int(next_bar["timestamp"])
                # 退出价格：滑点按多头卖出向下 3bps
                pos_exit_px = next_o * (1.0 - SLIPPAGE)
                pos_exit_bar_idx = b_idx + 1
                break

        # 计算持仓退出假设下的盈亏
        if pos_exit_triggered and pos_exit_px is not None:
            # 粗略估计资金费率（按持有时间比例折算或使用原资金费）
            # 由于提前退出，资金费只会小于等于原交易
            hold_ratio = max(0.05, min(1.0, (pos_exit_time_ms - entry_ms) / max(1, (int(m15_during.iloc[-1]["timestamp"]) - entry_ms))))
            adj_funding = float(sig["A_funding"]) * hold_ratio
            entry_fee = float(sig["A_fees"]) * 0.5  # 开仓费约占一半
            exit_fee = pos_exit_px * qty * TAKER_FEE
            gross = (pos_exit_px - entry_px) * qty
            pos_exit_net = gross - entry_fee - exit_fee - adj_funding
        else:
            pos_exit_net = a_net

        results.append({
            "signal_id": sig["signal_id"],
            "symbol": sym,
            "entry_time_ms": entry_ms,
            "entry_price": entry_px,
            "qty": qty,
            "initial_stop": init_stop,
            "A_net": a_net,
            "A_exit_px": a_exit_px,
            "A_reason": a_reason,
            "is_entry_last_1h_vol_down": is_entry_last_1h_vol_down,
            "is_entry_last_1h_high_vol_down": is_entry_last_1h_high_vol_down,
            "is_entry_signal_1h_vol_expansion": is_entry_signal_1h_vol_expansion,
            "pos_exit_triggered": pos_exit_triggered,
            "pos_exit_time_ms": pos_exit_time_ms,
            "pos_exit_px": pos_exit_px,
            "pos_exit_net": pos_exit_net,
        })

        if (idx + 1) % 50 == 0 or (idx + 1) == len(signals):
            print(f"Processed {idx + 1}/{len(signals)} signals...")

    # 保存原始评估明细
    df_res = pd.DataFrame(results)
    df_res.to_csv(os.path.join(OUT_DIR, "hypotheses_evaluation_detail.csv"), index=False)

    print("\n================== 假设 1：入场过滤评估 ==================")
    # 方案 1A: 过滤掉入场前一根 1h 为「放量大跌」的信号 (只允许缩量回调或阳线入场)
    # 若 is_entry_last_1h_high_vol_down == True 则过滤 (记净额 0)
    filtered_1a = df_res[df_res["is_entry_last_1h_high_vol_down"] == True]
    passed_1a = df_res[df_res["is_entry_last_1h_high_vol_down"] == False]
    print(f"方案 1A (过滤入场前放量下跌): 过滤笔数={len(filtered_1a)}, 保留笔数={len(passed_1a)}")
    print(f"  被过滤交易的原净额合计: {filtered_1a['A_net'].sum():.4f} U")
    saved_loss_1a = filtered_1a[filtered_1a['A_net'] < 0]['A_net'].sum()
    forgone_prof_1a = filtered_1a[filtered_1a['A_net'] > 0]['A_net'].sum()
    print(f"  少亏: {abs(saved_loss_1a):.4f} U, 错过盈利: {forgone_prof_1a:.4f} U, 过滤净改善: {(-filtered_1a['A_net'].sum()):.4f} U")
    print(f"  原策略 A 总净额: {df_res['A_net'].sum():.4f} U -> 过滤后总净额: {passed_1a['A_net'].sum():.4f} U")

    # 方案 1B: 要求入场前一根 1h 必须是「缩量下跌」 (严格威科夫回踩假说)
    # 若 is_entry_last_1h_vol_down == False 则过滤
    filtered_1b = df_res[df_res["is_entry_last_1h_vol_down"] == False]
    passed_1b = df_res[df_res["is_entry_last_1h_vol_down"] == True]
    print(f"\n方案 1B (仅允许前一根缩量下跌入场): 过滤笔数={len(filtered_1b)}, 保留笔数={len(passed_1b)}")
    print(f"  被过滤交易的原净额合计: {filtered_1b['A_net'].sum():.4f} U")
    saved_loss_1b = filtered_1b[filtered_1b['A_net'] < 0]['A_net'].sum()
    forgone_prof_1b = filtered_1b[filtered_1b['A_net'] > 0]['A_net'].sum()
    print(f"  少亏: {abs(saved_loss_1b):.4f} U, 错过盈利: {forgone_prof_1b:.4f} U, 过滤净改善: {(-filtered_1b['A_net'].sum()):.4f} U")
    print(f"  原策略 A 总净额: {df_res['A_net'].sum():.4f} U -> 保留组总净额: {passed_1b['A_net'].sum():.4f} U")

    print("\n================== 假设 2：持仓退出评估 ==================")
    pos_trig = df_res[df_res["pos_exit_triggered"] == True]
    print(f"持仓退出触发笔数: {len(pos_trig)} / {len(df_res)}")
    # 对触发提前退出的交易，比较 pos_exit_net vs A_net
    pos_trig_diff = pos_trig["pos_exit_net"] - pos_trig["A_net"]
    pos_saved_loss = pos_trig[(pos_trig["A_net"] < 0) & (pos_trig_diff > 0)]["A_net"].sum() # 简化
    print(f"  原策略对应笔净额: {pos_trig['A_net'].sum():.4f} U")
    print(f"  持仓提前退出后净额: {pos_trig['pos_exit_net'].sum():.4f} U")
    print(f"  提前退出净变化: {pos_trig_diff.sum():.4f} U")
    print(f"  全样本应用该持仓退出后总净额: {df_res['pos_exit_net'].sum():.4f} U (原 A: {df_res['A_net'].sum():.4f} U)")

    summary = {
        "n_total": len(df_res),
        "A_net": float(df_res["A_net"].sum()),
        "hyp1_filter_high_vol_down": {
            "n_filtered": len(filtered_1a),
            "n_passed": len(passed_1a),
            "saved_loss": float(abs(saved_loss_1a)),
            "forgone_profit": float(forgone_prof_1a),
            "net_diff": float(-filtered_1a['A_net'].sum()),
            "new_total_net": float(passed_1a['A_net'].sum())
        },
        "hyp1_filter_require_vol_down": {
            "n_filtered": len(filtered_1b),
            "n_passed": len(passed_1b),
            "saved_loss": float(abs(saved_loss_1b)),
            "forgone_profit": float(forgone_prof_1b),
            "net_diff": float(-filtered_1b['A_net'].sum()),
            "new_total_net": float(passed_1b['A_net'].sum())
        },
        "hyp2_position_exit": {
            "n_triggered": len(pos_trig),
            "orig_net": float(pos_trig['A_net'].sum()),
            "new_net": float(pos_trig['pos_exit_net'].sum()),
            "net_diff": float(pos_trig_diff.sum()),
            "new_total_net": float(df_res['pos_exit_net'].sum())
        }
    }

    with open(os.path.join(OUT_DIR, "hypotheses_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("\nSummary written to hypotheses_summary.json")


if __name__ == "__main__":
    evaluate()

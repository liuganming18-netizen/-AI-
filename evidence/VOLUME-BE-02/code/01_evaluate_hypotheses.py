# -*- coding: utf-8 -*-
"""VOLUME-BE-02 · 步骤一：严格因果律下的多视角假设评估与三分类统计。

直接复用 vbe2_core 核心逻辑。
"""
from __future__ import annotations

import csv
import json
import os
import sys
import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
for _p in (ROOT, f"{ROOT}/策略", f"{ROOT}/框架/backend", f"{ROOT}/evidence/VOLUME-BE-02/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
import vbe2_core

BE2_PAIRED = os.path.join(ROOT, "evidence/BREAKEVEN-02/out/paired_fixed.csv")
BE2_RAW = os.path.join(ROOT, "evidence/BREAKEVEN-02/out/raw_fixed.jsonl")
OUT_DIR = os.path.join(ROOT, "evidence/VOLUME-BE-02/out")
os.makedirs(OUT_DIR, exist_ok=True)


def load_raw_a_map():
    a_map = {}
    with open(BE2_RAW, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            for r in rec["rows"]:
                if r["variant"] == "A":
                    a_map[r["signal_id"]] = r
    return a_map


def load_signals():
    signals = []
    with open(BE2_PAIRED, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            signals.append(r)
    return signals


def evaluate():
    signals = load_signals()
    a_map = load_raw_a_map()
    runner = EventTrendRunner(base_dir=ROOT)

    window_data_cache = {}
    results = []

    print(f"Total signals to evaluate: {len(signals)}")
    for idx, sig in enumerate(signals):
        sid = sig["signal_id"]
        sym = sig["symbol"]
        win = sig["window"]
        a_rec = a_map[sid]

        cache_key = (sym, win)
        if cache_key not in window_data_cache:
            start_d, end_d = win.split("~")
            df_4h, df_1h, df_15m, oi_ts, oi_vals, fund = runner.load_symbol_data(
                sym, start_date=start_d, end_date=end_d, allow_auto_window=False
            )
            df_1h = df_1h.sort_values("timestamp").reset_index(drop=True)
            df_15m = df_15m.sort_values("timestamp").reset_index(drop=True)

            window_data_cache[cache_key] = {
                "df_1h": df_1h,
                "df_15m": df_15m,
                "fund": fund,
            }

        data = window_data_cache[cache_key]
        df_1h = data["df_1h"]
        df_15m = data["df_15m"]
        fund = data["fund"]

        entry_ms = int(sig["entry_time_ms"])
        a_exit_ms = int(a_rec["exit_time"])
        entry_px = float(sig["entry_price"])
        qty = float(sig["qty"])
        init_stop = float(sig["initial_stop"])
        a_net = float(sig["A_net"])
        a_reason = sig["A_reason"]
        a_entry_fee = float(a_rec["entry_fee"])

        # 1. 真实入场特征提取（调用 vbe2_core）
        feat = vbe2_core.extract_entry_features(df_1h, df_15m, entry_ms)

        # 2. 真实持仓退出模拟（调用 vbe2_core）
        exit_res = vbe2_core.simulate_position_exit(
            df_15m,
            entry_ms=entry_ms,
            a_exit_ms=a_exit_ms,
            entry_px=entry_px,
            qty=qty,
            init_stop=init_stop,
            a_entry_fee=a_entry_fee,
            fund_events=fund
        )

        pos_exit_net = exit_res["pos_exit_net"] if exit_res["pos_exit_triggered"] else a_net

        results.append({
            "signal_id": sid,
            "symbol": sym,
            "entry_time_ms": entry_ms,
            "A_exit_time_ms": a_exit_ms,
            "entry_price": entry_px,
            "qty": qty,
            "initial_stop": init_stop,
            "A_net": a_net,
            "A_reason": a_reason,
            # 突破 1h
            "is_breakout_vol_dryup": feat["is_breakout_vol_dryup"],
            # 突破前一根 1h (prev_1h)
            "prev_1h_category": feat["prev_1h_category"],
            "prev_1h_low_vol_drop": feat["prev_1h_low_vol_drop"],
            "prev_1h_high_vol_drop": feat["prev_1h_high_vol_drop"],
            "prev_1h_non_drop": feat["prev_1h_non_drop"],
            # 入场前最后一根 15m
            "m15_category": feat["m15_category"],
            "m15_low_vol_drop": feat["m15_low_vol_drop"],
            "m15_high_vol_drop": feat["m15_high_vol_drop"],
            "m15_non_drop": feat["m15_non_drop"],
            # 持仓退出
            "pos_exit_triggered": exit_res["pos_exit_triggered"],
            "pos_exit_time_ms": exit_res["pos_exit_time_ms"],
            "pos_exit_px": exit_res["pos_exit_px"],
            "pos_exit_reason": exit_res["pos_exit_reason"],
            "pos_exit_net": pos_exit_net,
            "same_bar_conflict_occurred": exit_res["same_bar_conflict_occurred"]
        })

    df_res = pd.DataFrame(results)
    detail_csv = os.path.join(OUT_DIR, "hypotheses_evaluation_detail.csv")
    df_res.to_csv(detail_csv, index=False)
    print(f"Detail written to {detail_csv}")

    def compute_stats(sub: pd.DataFrame, total_len: int):
        n = len(sub)
        if n == 0:
            return {
                "n": 0, "ratio": 0.0, "net_sum": 0.0, "net_mean": 0.0,
                "win_rate": 0.0, "loss_rate": 0.0, "worst": 0.0, "best": 0.0,
                "saved_loss": 0.0, "forgone_profit": 0.0, "net_delta": 0.0
            }
        net_sum = float(sub["A_net"].sum())
        net_mean = float(sub["A_net"].mean())
        wins = sub[sub["A_net"] > 1e-6]
        losses = sub[sub["A_net"] < -1e-6]
        return {
            "n": n,
            "ratio": n / total_len,
            "net_sum": net_sum,
            "net_mean": net_mean,
            "win_rate": len(wins) / n,
            "loss_rate": len(losses) / n,
            "worst": float(sub["A_net"].min()),
            "best": float(sub["A_net"].max()),
            "saved_loss": float(abs(losses["A_net"].sum())),
            "forgone_profit": float(wins["A_net"].sum()),
            "net_delta": -net_sum
        }

    total_n = len(df_res)
    stats_breakout_dryup = compute_stats(df_res[df_res["is_breakout_vol_dryup"]], total_n)
    stats_breakout_exp = compute_stats(df_res[~df_res["is_breakout_vol_dryup"]], total_n)

    stats_p_low = compute_stats(df_res[df_res["prev_1h_category"] == "low_vol_drop"], total_n)
    stats_p_high = compute_stats(df_res[df_res["prev_1h_category"] == "high_vol_drop"], total_n)
    stats_p_non = compute_stats(df_res[df_res["prev_1h_category"] == "non_drop"], total_n)

    stats_m_low = compute_stats(df_res[df_res["m15_category"] == "low_vol_drop"], total_n)
    stats_m_high = compute_stats(df_res[df_res["m15_category"] == "high_vol_drop"], total_n)
    stats_m_non = compute_stats(df_res[df_res["m15_category"] == "non_drop"], total_n)

    pos_trig = df_res[df_res["pos_exit_triggered"] == True]
    pos_trig_diff = float((pos_trig["pos_exit_net"] - pos_trig["A_net"]).sum())
    total_pos_exit_net = float(df_res["pos_exit_net"].sum())

    summary = {
        "n_total": total_n,
        "A_net_total": float(df_res["A_net"].sum()),
        "perspective_1_breakout_1h_volume": {
            "dryup_volume_filter_candidate": stats_breakout_dryup,
            "expansion_volume": stats_breakout_exp
        },
        "perspective_2_prev_1h_retest_pattern": {
            "low_vol_drop": stats_p_low,
            "high_vol_drop": stats_p_high,
            "non_drop": stats_p_non
        },
        "perspective_3_entry_15m_pattern": {
            "low_vol_drop": stats_m_low,
            "high_vol_drop": stats_m_high,
            "non_drop": stats_m_non
        },
        "perspective_4_position_exit_hypothesis": {
            "n_triggered": len(pos_trig),
            "ratio_triggered": len(pos_trig) / total_n,
            "orig_net_of_triggered": float(pos_trig["A_net"].sum()) if len(pos_trig) > 0 else 0.0,
            "new_net_of_triggered": float(pos_trig["pos_exit_net"].sum()) if len(pos_trig) > 0 else 0.0,
            "net_diff": pos_trig_diff,
            "new_total_net": total_pos_exit_net
        }
    }

    sum_json = os.path.join(OUT_DIR, "hypotheses_summary.json")
    with open(sum_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary written to {sum_json}")


if __name__ == "__main__":
    evaluate()

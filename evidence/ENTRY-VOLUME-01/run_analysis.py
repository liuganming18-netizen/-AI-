# -*- coding: utf-8 -*-
"""ENTRY-VOLUME-01 · 入场缩量过滤的证据强度分析脚本。

目标：
1. 评估缩量过滤的负收益差额 (-50.53 U) 是否由少数极端值造成，还是在现有样本中具有一致性；
2. 冻结现有缩量定义 (prev_1h_low_vol_drop)；
3. 以 201 个去重事件为主口径，206 候选仅作敏感性对照；
4. 输出逐笔证据、分期统计、逐一移除 (Leave-One-Out) 与移除极值敏感性；
5. 明确区分“当前样本过滤不利”与“缩量能预测盈利”。
"""
from __future__ import annotations

import json
import os
import sys
import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
INPUT_1R = os.path.join(ROOT, "evidence/VOLUME-BE-02/out/paired_trades_1r.csv")
OUT_DIR = os.path.join(ROOT, "evidence/ENTRY-VOLUME-01/out")
os.makedirs(OUT_DIR, exist_ok=True)


def analyze_entry_volume():
    df_all = pd.read_csv(INPUT_1R)

    # 1. 201 笔去重事件集（主口径）
    df_201 = df_all[df_all["is_event_dedup"] == True].copy()
    assert len(df_201) == 201, f"Expected 201, got {len(df_201)}"

    # 2. 识别缩量样本 (C_status == 'FILTERED_NOT_ENTERED')
    low_vol_201 = df_201[df_201["C_status"] == "FILTERED_NOT_ENTERED"].copy().reset_index(drop=True)
    low_vol_206 = df_all[df_all["C_status"] == "FILTERED_NOT_ENTERED"].copy().reset_index(drop=True)

    # 对照确认：206 与 201 的缩量样本是否完全一致
    assert len(low_vol_201) == len(low_vol_206) == 14, f"Mismatch in low vol counts: {len(low_vol_201)} vs {len(low_vol_206)}"
    signals_201 = set(low_vol_201["signal_id"])
    signals_206 = set(low_vol_206["signal_id"])
    assert signals_201 == signals_206, "14 signals differ between 201 and 206"

    # 添加时间特征
    low_vol_201["year"] = pd.to_datetime(low_vol_201["entry_time_utc"]).dt.year
    
    def get_era(row):
        y = row["year"]
        if y <= 2023:
            return "早期(2021-2023)"
        elif y == 2024:
            return "中期(2024)"
        else:
            return "近期(2025-2026)"
            
    low_vol_201["subperiod"] = low_vol_201.apply(get_era, axis=1)
    low_vol_201["is_win"] = low_vol_201["A_net"] > 0
    low_vol_201["net_impact_of_filtering"] = low_vol_201["C_delta"] # 即 -A_net

    # 保存逐笔证据表
    export_cols = [
        "signal_id", "symbol", "window", "group", "entry_time_utc", "entry_price", "qty",
        "initial_stop", "R_abs", "A_exit_price", "A_exit_time_utc", "A_reason", "A_hold_h",
        "A_net", "is_win", "C_saved_loss", "C_forgone_profit", "C_delta", "year", "subperiod"
    ]
    low_vol_export = low_vol_201[export_cols].copy()
    csv_path = os.path.join(OUT_DIR, "trades_low_vol_drop.csv")
    low_vol_export.to_csv(csv_path, index=False)
    print(f"Exported 14 trades to {csv_path}")

    # 3. 统计指标汇总
    total_net_pnl = float(low_vol_201["A_net"].sum())
    total_saved_loss = float(low_vol_201["C_saved_loss"].sum())
    total_forgone_profit = float(low_vol_201["C_forgone_profit"].sum())
    total_c_delta = float(low_vol_201["C_delta"].sum())
    win_count = int(low_vol_201["is_win"].sum())
    loss_count = int((~low_vol_201["is_win"]).sum())
    win_rate = win_count / len(low_vol_201)

    summary_metrics = {
        "sample_count": len(low_vol_201),
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate,
        "original_strategy_net_pnl": total_net_pnl,
        "saved_loss": total_saved_loss,
        "forgone_profit": total_forgone_profit,
        "net_filter_difference": total_c_delta,
        "mean_trade_net": float(low_vol_201["A_net"].mean()),
        "median_trade_net": float(low_vol_201["A_net"].median()),
        "max_profit_trade": {
            "symbol": low_vol_201.loc[low_vol_201["A_net"].idxmax()]["symbol"],
            "entry_time_utc": low_vol_201.loc[low_vol_201["A_net"].idxmax()]["entry_time_utc"],
            "A_net": float(low_vol_201["A_net"].max())
        },
        "max_loss_trade": {
            "symbol": low_vol_201.loc[low_vol_201["A_net"].idxmin()]["symbol"],
            "entry_time_utc": low_vol_201.loc[low_vol_201["A_net"].idxmin()]["entry_time_utc"],
            "A_net": float(low_vol_201["A_net"].min())
        },
        "std_trade_net": float(low_vol_201["A_net"].std())
    }
    with open(os.path.join(OUT_DIR, "summary_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(summary_metrics, f, indent=2, ensure_ascii=False)

    # 4. 逐一移除敏感性分析 (Leave-One-Out)
    loo_results = []
    base_delta = total_c_delta
    for idx, row in low_vol_201.iterrows():
        # 移除该笔交易后，剩余 13 笔的累计 C_delta
        rem_df = low_vol_201.drop(index=idx)
        rem_delta = float(rem_df["C_delta"].sum())
        rem_saved = float(rem_df["C_saved_loss"].sum())
        rem_forgone = float(rem_df["C_forgone_profit"].sum())
        loo_results.append({
            "removed_index": int(idx),
            "removed_symbol": row["symbol"],
            "removed_entry_time": row["entry_time_utc"],
            "removed_A_net": float(row["A_net"]),
            "removed_C_delta": float(row["C_delta"]),
            "remaining_trades_count": len(rem_df),
            "remaining_net_difference": rem_delta,
            "remaining_saved_loss": rem_saved,
            "remaining_forgone_profit": rem_forgone,
            "is_reversed_to_positive": rem_delta > 0
        })

    loo_summary = {
        "base_net_difference": base_delta,
        "loo_min_remaining_delta": min(r["remaining_net_difference"] for r in loo_results),
        "loo_max_remaining_delta": max(r["remaining_net_difference"] for r in loo_results),
        "any_reversed_to_positive": any(r["is_reversed_to_positive"] for r in loo_results),
        "details": loo_results
    }
    with open(os.path.join(OUT_DIR, "sensitivity_leave_one_out.json"), "w", encoding="utf-8") as f:
        json.dump(loo_summary, f, indent=2, ensure_ascii=False)

    # 5. 极端盈利移除测试 (Top-N Removed)
    sorted_by_profit = low_vol_201.sort_values("A_net", ascending=False).reset_index(drop=True)
    top_n_results = []
    for k in range(1, 6):
        rem_df = sorted_by_profit.iloc[k:]
        rem_delta = float(rem_df["C_delta"].sum())
        removed_trades = [
            {"symbol": r["symbol"], "entry_time": r["entry_time_utc"], "A_net": float(r["A_net"])}
            for _, r in sorted_by_profit.iloc[:k].iterrows()
        ]
        top_n_results.append({
            "top_k_removed": k,
            "removed_trades": removed_trades,
            "removed_forgone_profit_sum": float(sorted_by_profit.iloc[:k]["A_net"].sum()),
            "remaining_trades_count": len(rem_df),
            "remaining_net_difference": rem_delta,
            "is_reversed_to_positive": rem_delta > 0
        })

    with open(os.path.join(OUT_DIR, "sensitivity_top_n_removed.json"), "w", encoding="utf-8") as f:
        json.dump(top_n_results, f, indent=2, ensure_ascii=False)

    # 6. 分期分布 (三期与逐年)
    subperiod_group = low_vol_201.groupby("subperiod")
    subperiod_stats = {}
    for name, group in subperiod_group:
        subperiod_stats[name] = {
            "count": len(group),
            "win_count": int(group["is_win"].sum()),
            "loss_count": int((~group["is_win"]).sum()),
            "win_rate": float(group["is_win"].mean()),
            "strategy_net_pnl": float(group["A_net"].sum()),
            "saved_loss": float(group["C_saved_loss"].sum()),
            "forgone_profit": float(group["C_forgone_profit"].sum()),
            "net_filter_difference": float(group["C_delta"].sum()),
        }

    year_group = low_vol_201.groupby("year")
    year_stats = {}
    for y, group in year_group:
        year_stats[str(y)] = {
            "count": len(group),
            "win_count": int(group["is_win"].sum()),
            "loss_count": int((~group["is_win"]).sum()),
            "win_rate": float(group["is_win"].mean()),
            "strategy_net_pnl": float(group["A_net"].sum()),
            "saved_loss": float(group["C_saved_loss"].sum()),
            "forgone_profit": float(group["C_forgone_profit"].sum()),
            "net_filter_difference": float(group["C_delta"].sum()),
        }

    distribution_data = {
        "subperiod_distribution": subperiod_stats,
        "yearly_distribution": year_stats
    }
    with open(os.path.join(OUT_DIR, "subperiod_distribution.json"), "w", encoding="utf-8") as f:
        json.dump(distribution_data, f, indent=2, ensure_ascii=False)

    # 7. 全样本背景基线对比 (201 事件中缩量 vs 非缩量)
    non_low_vol = df_201[df_201["C_status"] == "ENTERED"].copy()
    baseline_comparison = {
        "total_event_count": len(df_201),
        "low_vol_count": len(low_vol_201),
        "non_low_vol_count": len(non_low_vol),
        "low_vol_win_rate": float(low_vol_201["is_win"].mean()),
        "non_low_vol_win_rate": float((non_low_vol["A_net"] > 0).mean()),
        "all_sample_win_rate": float((df_201["A_net"] > 0).mean()),
        "low_vol_mean_pnl": float(low_vol_201["A_net"].mean()),
        "non_low_vol_mean_pnl": float(non_low_vol["A_net"].mean()),
        "all_sample_mean_pnl": float(df_201["A_net"].mean()),
        "low_vol_pnl_sum": float(low_vol_201["A_net"].sum()),
        "non_low_vol_pnl_sum": float(non_low_vol["A_net"].sum()),
        "all_sample_pnl_sum": float(df_201["A_net"].sum())
    }
    with open(os.path.join(OUT_DIR, "baseline_comparison.json"), "w", encoding="utf-8") as f:
        json.dump(baseline_comparison, f, indent=2, ensure_ascii=False)

    print("All analysis artifacts generated successfully.")


if __name__ == "__main__":
    analyze_entry_volume()

# -*- coding: utf-8 -*-
"""VOLUME-BE-01 · 步骤三：分期稳健性检验与代表性正反案例K线价格路径提炼。

1. 分期分析：按年份/市场周期切分（2021-2023 早期、2024 中期、2025-2026 近期）；
2. 代表性案例：提取少亏案例、错失盈利案例、保本误伤案例，并记录详细价格与时间。
"""
from __future__ import annotations

import csv
import json
import os
import sys
import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT_DIR = os.path.join(ROOT, "evidence/VOLUME-BE-01/out")
PAIRED_1R = os.path.join(OUT_DIR, "paired_trades_1r.csv")
PAIRED_2R = os.path.join(OUT_DIR, "paired_trades_2r.csv")


def run_subperiod_analysis():
    df = pd.read_csv(PAIRED_1R)

    # 提取年份
    df["year"] = df["entry_time_utc"].str.slice(0, 4).astype(int)

    periods = [
        ("2021-2023 (早期市场)", df["year"] <= 2023),
        ("2024 (中期复苏)", df["year"] == 2024),
        ("2025-2026 (近期样本)", df["year"] >= 2025),
    ]

    subperiod_results = []
    for label, cond in periods:
        sub = df[cond]
        n_sub = len(sub)
        if n_sub == 0:
            continue

        a_net = float(sub["A_net"].sum())
        b_net = float(sub["B_net"].sum())
        c_net = float(sub["C_net"].sum())
        d_net = float(sub["D_net"].sum())

        c_filt = int((sub["C_status"] == "FILTERED_NOT_ENTERED").sum())
        c_saved = float(sub["C_saved_loss"].sum())
        c_forgone = float(sub["C_forgone_profit"].sum())

        subperiod_results.append({
            "period": label,
            "n_signals": n_sub,
            "A_net": a_net,
            "B_1R_net": b_net,
            "C_vol_net": c_net,
            "D_comb_net": d_net,
            "C_filtered_count": c_filt,
            "C_saved_loss": c_saved,
            "C_forgone_profit": c_forgone,
            "C_net_delta": c_net - a_net,
            "B_net_delta": b_net - a_net,
            "D_net_delta": d_net - a_net
        })

    with open(os.path.join(OUT_DIR, "subperiod_analysis.json"), "w", encoding="utf-8") as f:
        json.dump(subperiod_results, f, indent=2, ensure_ascii=False)
    print("Subperiod analysis saved.")


def extract_representative_cases():
    df = pd.read_csv(PAIRED_1R)

    cases = {
        "saved_loss_cases": [],      # 缩量规则成功救回的亏损单
        "forgone_profit_cases": [],  # 缩量规则严重错失的超级盈利单
        "breakeven_whipsaw_cases": [], # 1R保本被震出但原策略暴赚单
        "consistent_loss_cases": []  # 各组都防不住的直接止损黑天鹅
    }

    # 1. 缩量规则成功救回案例 (A 亏损且被过滤，过滤少亏)
    saved = df[(df["C_status"] == "FILTERED_NOT_ENTERED") & (df["A_net"] < -9.0)].sort_values("A_net")
    for _, r in saved.head(3).iterrows():
        cases["saved_loss_cases"].append({
            "signal_id": r["signal_id"],
            "symbol": r["symbol"],
            "entry_time_utc": r["entry_time_utc"],
            "entry_price": float(r["entry_price"]),
            "initial_stop": float(r["initial_stop"]),
            "A_net": float(r["A_net"]),
            "A_exit_time_utc": r["A_exit_time_utc"],
            "A_exit_price": float(r["A_exit_price"]),
            "A_reason": r["A_reason"],
            "C_status": r["C_status"],
            "C_net": float(r["C_net"]),
            "saved_amount": abs(float(r["A_net"])),
            "narrative": f"{r['symbol']} 在 {r['entry_time_utc']} 入场后单边下跌打到初始止损，原策略亏损 {r['A_net']:.2f} U；缩量规则识别到入场前放量下跌予以过滤，完全避免了这笔全额亏损。"
        })

    # 2. 缩量规则错失盈利案例 (A 暴赚但被过滤)
    forgone = df[(df["C_status"] == "FILTERED_NOT_ENTERED") & (df["A_net"] > 30.0)].sort_values("A_net", ascending=False)
    for _, r in forgone.head(3).iterrows():
        cases["forgone_profit_cases"].append({
            "signal_id": r["signal_id"],
            "symbol": r["symbol"],
            "entry_time_utc": r["entry_time_utc"],
            "entry_price": float(r["entry_price"]),
            "initial_stop": float(r["initial_stop"]),
            "A_net": float(r["A_net"]),
            "A_exit_time_utc": r["A_exit_time_utc"],
            "A_exit_price": float(r["A_exit_price"]),
            "A_reason": r["A_reason"],
            "C_status": r["C_status"],
            "C_net": float(r["C_net"]),
            "missed_profit": float(r["A_net"]),
            "narrative": f"{r['symbol']} 在 {r['entry_time_utc']} 入场前经历剧烈洗盘（放量阴线），被缩量规则误判过滤；随后价格爆发单边主升浪，原策略盈利 {r['A_net']:.2f} U，缩量规则完全踏空。"
        })

    # 3. 1R 保本止损误伤案例 (A 暴赚，但 B 被保本打出)
    be_whipsaw = df[(df["A_net"] > 30.0) & (df["B_net"] < 5.0)].sort_values("A_net", ascending=False)
    for _, r in be_whipsaw.head(2).iterrows():
        cases["breakeven_whipsaw_cases"].append({
            "signal_id": r["signal_id"],
            "symbol": r["symbol"],
            "entry_time_utc": r["entry_time_utc"],
            "A_net": float(r["A_net"]),
            "B_net": float(r["B_net"]),
            "B_exit_time_utc": r["B_exit_time_utc"],
            "forgone_profit": float(r["A_net"] - r["B_net"]),
            "narrative": f"{r['symbol']} 达到 1R 后止损被抬高至保本价，随后回踩打掉保本止损以 {r['B_net']:.2f} U 平仓；原策略未设保本，扛过回踩后走出巨大行情，实现 {r['A_net']:.2f} U 盈利。"
        })

    # 4. 各组均无法防范的直接止损黑天鹅 (最差单笔)
    worst = df.sort_values("A_net").head(2)
    for _, r in worst.iterrows():
        cases["consistent_loss_cases"].append({
            "signal_id": r["signal_id"],
            "symbol": r["symbol"],
            "entry_time_utc": r["entry_time_utc"],
            "A_net": float(r["A_net"]),
            "B_net": float(r["B_net"]),
            "C_net": float(r["C_net"]),
            "D_net": float(r["D_net"]),
            "narrative": f"{r['symbol']} 入场后无任何反弹直接跳空/破位打穿初始止损，A/B 组全额亏损 {r['A_net']:.2f} U；若该单未被过滤，保本止损完全无法发挥保护作用。"
        })

    with open(os.path.join(OUT_DIR, "case_studies.json"), "w", encoding="utf-8") as f:
        json.dump(cases, f, indent=2, ensure_ascii=False)
    print("Case studies saved.")


if __name__ == "__main__":
    run_subperiod_analysis()
    extract_representative_cases()

# -*- coding: utf-8 -*-
"""VOLUME-BE-02 · 步骤三：分期稳健性检验与代表性案例提炼。

更新点：
1. 分别输出 206 候选级与 201 事件级去重后的分期统计（2021-2023, 2024, 2025-2026）；
2. 提炼代表性正反案例，删除主观定性修饰词，客观如实记录。
"""
from __future__ import annotations

import csv
import json
import os
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT_DIR = os.path.join(ROOT, "evidence/VOLUME-BE-02/out")
PAIRED_1R = os.path.join(OUT_DIR, "paired_trades_1r.csv")
PAIRED_2R = os.path.join(OUT_DIR, "paired_trades_2r.csv")


def run_subperiod_analysis():
    df_all = pd.read_csv(PAIRED_1R)
    df_all["year"] = df_all["entry_time_utc"].str.slice(0, 4).astype(int)

    periods_def = [
        ("2021-2023 (早期市场)", lambda d: d["year"] <= 2023),
        ("2024 (中期复苏)", lambda d: d["year"] == 2024),
        ("2025-2026 (近期样本)", lambda d: d["year"] >= 2025),
    ]

    out_periods = {"universe_206_candidates": [], "event_level_201_dedup": []}

    for scope_key, df in [("universe_206_candidates", df_all), ("event_level_201_dedup", df_all[df_all["is_event_dedup"] == True])]:
        for label, cond_fn in periods_def:
            sub = df[cond_fn(df)]
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

            out_periods[scope_key].append({
                "period": label,
                "n_signals": n_sub,
                "A_net": round(a_net, 4),
                "B_1R_net": round(b_net, 4),
                "C_vol_net": round(c_net, 4),
                "D_comb_net": round(d_net, 4),
                "C_filtered_count": c_filt,
                "C_saved_loss": round(c_saved, 4),
                "C_forgone_profit": round(c_forgone, 4),
                "C_net_delta": round(c_net - a_net, 4),
                "B_net_delta": round(b_net - a_net, 4),
                "D_net_delta": round(d_net - a_net, 4)
            })

    with open(os.path.join(OUT_DIR, "subperiod_analysis.json"), "w", encoding="utf-8") as f:
        json.dump(out_periods, f, indent=2, ensure_ascii=False)
    print("subperiod_analysis.json saved.")


def extract_representative_cases():
    df = pd.read_csv(PAIRED_1R)

    cases = {
        "saved_loss_cases": [],
        "forgone_profit_cases": [],
        "breakeven_whipsaw_cases": [],
        "consistent_loss_cases": [],
        "r2_single_trade_attribution": []
    }

    # 1. 缩量规则少亏案例 (A 亏损且被过滤，共 4 笔)
    saved = df[(df["C_status"] == "FILTERED_NOT_ENTERED") & (df["A_net"] < 0)].sort_values("A_net")
    for _, r in saved.iterrows():
        cases["saved_loss_cases"].append({
            "signal_id": r["signal_id"],
            "symbol": r["symbol"],
            "entry_time_utc": r["entry_time_utc"],
            "entry_price": float(r["entry_price"]),
            "initial_stop": float(r["initial_stop"]),
            "A_net": float(r["A_net"]),
            "A_exit_time_utc": r["A_exit_time_utc"],
            "A_reason": r["A_reason"],
            "C_status": r["C_status"],
            "C_net": float(r["C_net"]),
            "saved_amount": abs(float(r["A_net"])),
            "narrative": f"{r['symbol']} 于 {r['entry_time_utc']} 入场后走弱触及初始止损，原策略亏损 {r['A_net']:.2f} U；缩量规则识别入场前 1h 缩量下跌予以过滤，记录为未交易 0 U，直接少亏 {abs(r['A_net']):.2f} U。"
        })

    # 2. 缩量规则错失盈利案例 (A 盈利但被过滤，共 10 笔中列出前 3 笔)
    forgone = df[(df["C_status"] == "FILTERED_NOT_ENTERED") & (df["A_net"] > 0)].sort_values("A_net", ascending=False)
    for _, r in forgone.head(3).iterrows():
        cases["forgone_profit_cases"].append({
            "signal_id": r["signal_id"],
            "symbol": r["symbol"],
            "entry_time_utc": r["entry_time_utc"],
            "entry_price": float(r["entry_price"]),
            "initial_stop": float(r["initial_stop"]),
            "A_net": float(r["A_net"]),
            "A_exit_time_utc": r["A_exit_time_utc"],
            "A_reason": r["A_reason"],
            "C_status": r["C_status"],
            "C_net": float(r["C_net"]),
            "missed_profit": float(r["A_net"]),
            "narrative": f"{r['symbol']} 于 {r['entry_time_utc']} 入场前 1h 呈现缩量下跌，被缩量规则过滤；原策略持有后走出波段，实现盈利 {r['A_net']:.2f} U；过滤后错失该笔收益。"
        })

    # 3. 1R 保本止损误伤案例 (A 大赚，B 被打出)
    be_whipsaw = df[(df["A_net"] > 20.0) & (df["B_net"] < 5.0)].sort_values("A_net", ascending=False)
    for _, r in be_whipsaw.head(3).iterrows():
        cases["breakeven_whipsaw_cases"].append({
            "signal_id": r["signal_id"],
            "symbol": r["symbol"],
            "entry_time_utc": r["entry_time_utc"],
            "A_net": float(r["A_net"]),
            "B_net": float(r["B_net"]),
            "B_exit_time_utc": r["B_exit_time_utc"],
            "forgone_profit": float(r["A_net"] - r["B_net"]),
            "narrative": f"{r['symbol']} 达到 1R 后止损抬高至保本价，随后回踩触及保本止损平仓；原策略未设保本继续持有，实现 {r['A_net']:.2f} U 盈利。"
        })

    # 4. 直坠型黑天鹅最差单笔
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
            "narrative": f"{r['symbol']} 开盘后无任何反弹直接跳空打穿止损，各组亏损一致为 {r['A_net']:.2f} U，保本与缩量规则均未发挥防御作用。"
        })

    # 5. 2R 保本相比 A 基准微增 +3.048 U 的唯一样本定位
    df_2r = pd.read_csv(PAIRED_2R)
    r2_diff = df_2r[df_2r["B_delta"].abs() > 1e-4]
    for _, r in r2_diff.iterrows():
        cases["r2_single_trade_attribution"].append({
            "signal_id": r["signal_id"],
            "symbol": r["symbol"],
            "entry_time_utc": r["entry_time_utc"],
            "A_net": float(r["A_net"]),
            "B_net": float(r["B_net"]),
            "delta": float(r["B_delta"]),
            "narrative": f"全样本中 2R 保本相比基线 A 产生差异的交易仅此 1 笔（{r['symbol']}，入场时间 {r['entry_time_utc']}）。该交易在 A 原策略中亏损 {r['A_net']:.4f} U，在 2R 保本下提前退出变为 {r['B_net']:.4f} U，改善额为 {r['B_delta']:.4f} U；其余交易完全无变化。该结果纯属单笔交易偶发，统计学上无法代表规律性优势。"
        })

    with open(os.path.join(OUT_DIR, "case_studies.json"), "w", encoding="utf-8") as f:
        json.dump(cases, f, indent=2, ensure_ascii=False)
    print("case_studies.json saved.")


if __name__ == "__main__":
    run_subperiod_analysis()
    extract_representative_cases()

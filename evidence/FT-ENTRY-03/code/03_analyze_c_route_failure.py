# -*- coding: utf-8 -*-
"""FT-ENTRY-03 C路线失败归因分析脚本

基于 FT-ENTRY-02 冻结的 9 组 C 路线现有逐笔记录 (共近 3000 笔交易)，进行深度透视归因：
1. 退出原因归因 (exit_reason 透视)
2. 分时段/分年度透视 (2022, 2023, 2024)
3. 币种分布与亏损集中度透视
4. 极端损失依赖性分析 (剔除 Top 1 / Top 3 最大亏损单)
5. 盈亏比与持仓时长透视
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Dict, List, Any

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_ENTRY_02_OUT = BASE_DIR / "evidence/FT-ENTRY-02/out"
FT_ENTRY_03_DIR = BASE_DIR / "evidence/FT-ENTRY-03"
OUT_DIR = FT_ENTRY_03_DIR / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

C_FILES = sorted(glob.glob(str(FT_ENTRY_02_OUT / "trades_Wyckoff_C_*.csv")))

def analyze_c_failure():
    all_summaries = []
    exit_reason_breakdowns = []
    yearly_breakdowns = []
    symbol_breakdowns = []
    extreme_loss_analyses = []

    for fpath in C_FILES:
        fname = Path(fpath).stem
        strat_name = fname.replace("trades_", "")
        df = pd.read_csv(fpath)
        if df.empty:
            continue

        pnl = df["profit_abs"]
        trade_count = len(df)
        wins = df[pnl > 0]
        losses = df[pnl < 0]
        evens = df[pnl == 0]
        win_count = len(wins)
        loss_count = len(losses)
        even_count = len(evens)
        win_rate = win_count / trade_count * 100.0
        net_pnl = float(pnl.sum())
        gross_profit = float(wins["profit_abs"].sum()) if not wins.empty else 0.0
        gross_loss = abs(float(losses["profit_abs"].sum())) if not losses.empty else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        expectancy = net_pnl / trade_count
        avg_win = gross_profit / win_count if win_count > 0 else 0.0
        avg_loss = -gross_loss / loss_count if loss_count > 0 else 0.0

        all_summaries.append({
            "strategy": strat_name,
            "trade_count": trade_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "even_count": even_count,
            "win_rate": round(win_rate, 2),
            "net_pnl": round(net_pnl, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(profit_factor, 4),
            "expectancy_u": round(expectancy, 4),
            "avg_win_u": round(avg_win, 2),
            "avg_loss_u": round(avg_loss, 2),
            "win_loss_ratio": round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else 0.0
        })

        # 1. 退出原因透视
        for r, group in df.groupby("exit_reason"):
            grp_pnl = group["profit_abs"]
            exit_reason_breakdowns.append({
                "strategy": strat_name,
                "exit_reason": r,
                "trades": len(group),
                "trades_pct": round(len(group) / trade_count * 100, 2),
                "net_pnl": round(float(grp_pnl.sum()), 2),
                "win_rate": round((grp_pnl > 0).mean() * 100, 2),
                "avg_pnl": round(float(grp_pnl.mean()), 2)
            })

        # 2. 分时段透视 (按 close_date 年份)
        df["year"] = pd.to_datetime(df["close_date"]).dt.year
        for yr, group in df.groupby("year"):
            grp_pnl = group["profit_abs"]
            yearly_breakdowns.append({
                "strategy": strat_name,
                "year": yr,
                "trades": len(group),
                "net_pnl": round(float(grp_pnl.sum()), 2),
                "win_rate": round((grp_pnl > 0).mean() * 100, 2),
                "profit_factor": round(
                    float(group[grp_pnl > 0]["profit_abs"].sum()) / abs(float(group[grp_pnl < 0]["profit_abs"].sum()))
                    if abs(float(group[grp_pnl < 0]["profit_abs"].sum())) > 0 else 0.0, 4
                )
            })

        # 3. 币种表现透视
        for pair, group in df.groupby("pair"):
            grp_pnl = group["profit_abs"]
            symbol_breakdowns.append({
                "strategy": strat_name,
                "pair": pair,
                "trades": len(group),
                "net_pnl": round(float(grp_pnl.sum()), 2),
                "win_rate": round((grp_pnl > 0).mean() * 100, 2)
            })

        # 4. 极端损失依赖分析
        sorted_losses = losses.sort_values(by="profit_abs") # 最亏的在前面
        top1_loss = float(sorted_losses.iloc[0]["profit_abs"]) if len(sorted_losses) >= 1 else 0.0
        top3_loss = float(sorted_losses.iloc[:3]["profit_abs"].sum()) if len(sorted_losses) >= 3 else top1_loss
        top5_loss = float(sorted_losses.iloc[:5]["profit_abs"].sum()) if len(sorted_losses) >= 5 else top3_loss
        net_pnl_ex_top1 = net_pnl - top1_loss
        net_pnl_ex_top3 = net_pnl - top3_loss

        extreme_loss_analyses.append({
            "strategy": strat_name,
            "net_pnl": round(net_pnl, 2),
            "top1_loss_u": round(top1_loss, 2),
            "top1_loss_pair": sorted_losses.iloc[0]["pair"] if len(sorted_losses) >= 1 else "-",
            "top1_loss_pct_of_gross_loss": round(abs(top1_loss) / gross_loss * 100, 2) if gross_loss > 0 else 0.0,
            "top3_loss_u": round(top3_loss, 2),
            "top3_loss_pct_of_gross_loss": round(abs(top3_loss) / gross_loss * 100, 2) if gross_loss > 0 else 0.0,
            "pnl_ex_top1": round(net_pnl_ex_top1, 2),
            "pnl_ex_top3": round(net_pnl_ex_top3, 2),
            "still_negative_ex_top3": bool(net_pnl_ex_top3 < 0)
        })

    # 保存各表
    df_sum = pd.DataFrame(all_summaries)
    df_exit = pd.DataFrame(exit_reason_breakdowns)
    df_year = pd.DataFrame(yearly_breakdowns)
    df_sym = pd.DataFrame(symbol_breakdowns)
    df_ext = pd.DataFrame(extreme_loss_analyses)

    df_sum.to_csv(OUT_DIR / "c_route_01_summary.csv", index=False)
    df_exit.to_csv(OUT_DIR / "c_route_02_exit_reason_breakdown.csv", index=False)
    df_year.to_csv(OUT_DIR / "c_route_03_yearly_breakdown.csv", index=False)
    df_sym.to_csv(OUT_DIR / "c_route_04_symbol_breakdown.csv", index=False)
    df_ext.to_csv(OUT_DIR / "c_route_05_extreme_loss_analysis.csv", index=False)

    print("=== C 路线总体表现 ===")
    print(df_sum.to_string())
    print("\n=== C 路线极端亏损依赖检验 ===")
    print(df_ext.to_string())

if __name__ == "__main__":
    analyze_c_failure()

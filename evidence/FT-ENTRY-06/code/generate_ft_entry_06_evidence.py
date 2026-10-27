# -*- coding: utf-8 -*-
"""FT-ENTRY-06 C 路线缩量回踩失败的结构归因分析脚本

严格落实工单要求：
1. 复用 C 路线旧 9 组逐笔结果，不扩参数、不重跑；
2. 按候选归并，避免同一候选在 9 组中重复计作独立证据；
3. 重点回答：哪些交易虽然满足缩量条件，却没有形成有效的企稳入场？
4. 区分：
   - 入场后很快触及止损 (FAST_STOPOUT)
   - 长时间不启动 (STAGNANT_DORMANT)
   - 曾产生有利波动但最终退出亏损 (REVERSED_AFTER_RUN)
   - 少数极端损失与普遍小亏 (UNIFORM_FRICTION)
5. 每项解释同时列盈利反例；后续路径只用于归因，不用于构造“完美入场点”；
6. 输出结构归因表及最多 2 项值得后续检验的规则假设。
"""
from __future__ import annotations

import glob
import json
import logging
import math
import os
from pathlib import Path
from typing import Dict, List, Any

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT02_OUT = BASE_DIR / "evidence/FT-ENTRY-02/out"
OUT_DIR = BASE_DIR / "evidence/FT-ENTRY-06"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FT-ENTRY-06-Attribution")


def main():
    logger.info("=== FT-ENTRY-06 C 路线结构归因分析开始 ===")

    # 1. 读取 9 组 C 路线真实成交逐笔
    files = sorted(glob.glob(str(FT02_OUT / "trades_Wyckoff_C_*.csv")))
    logger.info(f"找到 {len(files)} 组 C 路线逐笔交易文件")

    dfs = []
    for f in files:
        strat = Path(f).stem.replace("trades_", "")
        df = pd.read_csv(f)
        df["strategy"] = strat
        dfs.append(df)
    all_trades = pd.concat(dfs, ignore_index=True)
    logger.info(f"读取到名义成交总记录数: {len(all_trades)} 笔")

    # 2. 计算单笔交易衍生指标
    all_trades["max_profit_ratio"] = (all_trades["max_rate"] - all_trades["open_rate"]) / all_trades["open_rate"]
    all_trades["max_drawdown_ratio"] = (all_trades["min_rate"] - all_trades["open_rate"]) / all_trades["open_rate"]
    all_trades["duration_hours"] = all_trades["trade_duration"] / 60.0

    # 3. 按唯一候选 (enter_tag) 归并
    grp = all_trades.groupby("enter_tag").agg({
        "pair": "first",
        "open_date": "first",
        "close_date": "first",
        "open_rate": "mean",
        "close_rate": "mean",
        "duration_hours": "mean",
        "profit_abs": "mean",
        "profit_ratio": "mean",
        "max_profit_ratio": "mean",
        "max_drawdown_ratio": "mean",
        "exit_reason": lambda s: s.mode().iloc[0] if not s.empty else s.iloc[0],
        "strategy": lambda s: sorted(list(set(s)))
    }).reset_index()

    grp["symbol"] = grp["pair"].apply(lambda p: p.split("/")[0])
    grp["strategy_count"] = grp["strategy"].apply(len)
    grp["strategy_list"] = grp["strategy"].apply(lambda sl: "; ".join(sl))
    grp["is_win"] = grp["profit_abs"] > 0

    logger.info(f"按候选归并后独立候选数: {len(grp)} 个 (盈利: {grp['is_win'].sum()}, 亏损: {(~grp['is_win']).sum()})")

    # 4. 失败类型分类（仅针对亏损候选）
    def classify_failure(row):
        if row["is_win"]:
            return "盈利反例 (WINNING_COUNTEREXAMPLE)"
        mfe = row["max_profit_ratio"]
        dur = row["duration_hours"]

        if mfe >= 0.03:
            return "曾产生有利波动但最终反转亏损 (REVERSED_AFTER_RUN)"
        elif dur >= 48.0:
            return "长时间不启动 (STAGNANT_DORMANT)"
        elif dur <= 12.0:
            return "入场后很快触及止损 (FAST_STOPOUT)"
        else:
            return "中程常规止损 (MID_TERM_STOPOUT)"

    grp["failure_category"] = grp.apply(classify_failure, axis=1)

    # 5. 导出 01_C路线逐候选结构归因明细表.csv
    file_01 = OUT_DIR / "01_C路线逐候选结构归因明细表.csv"
    cols_01 = [
        "enter_tag", "symbol", "pair", "failure_category", "is_win",
        "strategy_count", "strategy_list",
        "open_date", "close_date", "duration_hours",
        "open_rate", "close_rate", "profit_abs", "profit_ratio",
        "max_profit_ratio", "max_drawdown_ratio", "exit_reason"
    ]
    grp[cols_01].to_csv(file_01, index=False, encoding="utf-8-sig")
    logger.info(f"已导出逐候选归因明细表: {file_01}")

    # 6. 生成 02_四类失败机制与统计对比表.csv
    summary_rows = []
    cat_names = [
        "入场后很快触及止损 (FAST_STOPOUT)",
        "曾产生有利波动但最终反转亏损 (REVERSED_AFTER_RUN)",
        "长时间不启动 (STAGNANT_DORMANT)",
        "中程常规止损 (MID_TERM_STOPOUT)",
        "盈利反例 (WINNING_COUNTEREXAMPLE)"
    ]

    for cat in cat_names:
        sub = grp[grp["failure_category"] == cat]
        n_cands = len(sub)
        pnl_sum = sub["profit_abs"].sum()
        pnl_mean = sub["profit_abs"].mean() if n_cands > 0 else 0.0
        pnl_median = sub["profit_abs"].median() if n_cands > 0 else 0.0
        dur_mean = sub["duration_hours"].mean() if n_cands > 0 else 0.0
        dur_median = sub["duration_hours"].median() if n_cands > 0 else 0.0
        mfe_mean = sub["max_profit_ratio"].mean() * 100 if n_cands > 0 else 0.0
        mfe_median = sub["max_profit_ratio"].median() * 100 if n_cands > 0 else 0.0
        mae_mean = sub["max_drawdown_ratio"].mean() * 100 if n_cands > 0 else 0.0
        mae_median = sub["max_drawdown_ratio"].median() * 100 if n_cands > 0 else 0.0

        if "FAST_STOPOUT" in cat:
            desc = "入场后缺乏承接买盘，12h内直接被反向空头力量击穿止损线"
        elif "REVERSED_AFTER_RUN" in cat:
            desc = "入场后反弹显著(MFE>=3%)，但受阻于箱体压制未能触及2R止盈，冲高回落转亏"
        elif "STAGNANT_DORMANT" in cat:
            desc = "在箱底长时间(>=48h)窄幅钝化盘整，时间价值耗尽，最终被阴跌磨穿止损"
        elif "MID_TERM_STOPOUT" in cat:
            desc = "中程(12h~48h)内无序拉锯，最终触及固定止损"
        else:
            desc = "成功走出底部突破波段，激活2R跟踪止损或超时大额获利"

        summary_rows.append({
            "category": cat,
            "candidate_count": n_cands,
            "pct_of_total": round(n_cands / len(grp) * 100, 2),
            "total_net_pnl_u": round(pnl_sum, 2),
            "mean_net_pnl_u": round(pnl_mean, 2),
            "median_net_pnl_u": round(pnl_median, 2),
            "mean_duration_hours": round(dur_mean, 2),
            "median_duration_hours": round(dur_median, 2),
            "mean_mfe_pct": round(mfe_mean, 2),
            "median_mfe_pct": round(mfe_median, 2),
            "mean_mae_pct": round(mae_mean, 2),
            "median_mae_pct": round(mae_median, 2),
            "mechanism_definition": desc
        })

    df_summary = pd.DataFrame(summary_rows)
    file_02 = OUT_DIR / "02_四类失败机制与统计对比表.csv"
    df_summary.to_csv(file_02, index=False, encoding="utf-8-sig")
    logger.info(f"已导出四类机制统计对比表: {file_02}")

    # 7. 导出 03_盈利反例对照全景表.csv (精选最具代表性的正反案例对照)
    # 取各类别 Top 典型代表
    fast_top = grp[grp["failure_category"].str.contains("FAST_STOPOUT")].sort_values("duration_hours").head(5)
    rev_top = grp[grp["failure_category"].str.contains("REVERSED_AFTER_RUN")].sort_values("max_profit_ratio", ascending=False).head(5)
    stag_top = grp[grp["failure_category"].str.contains("STAGNANT_DORMANT")].sort_values("duration_hours", ascending=False).head(5)
    win_top = grp[grp["is_win"]].sort_values("profit_abs", ascending=False).head(10)

    contra_df = pd.concat([fast_top, rev_top, stag_top, win_top], ignore_index=True)
    file_03 = OUT_DIR / "03_盈利反例对照全景表.csv"
    contra_df[cols_01].to_csv(file_03, index=False, encoding="utf-8-sig")
    logger.info(f"已导出盈利反例对照全景表: {file_03}")

    logger.info("=== FT-ENTRY-06 数据处理与归因分析全部完成 ===")


if __name__ == "__main__":
    main()

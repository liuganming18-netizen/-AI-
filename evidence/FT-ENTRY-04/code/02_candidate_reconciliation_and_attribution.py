# -*- coding: utf-8 -*-
"""FT-ENTRY-04 逐候选对照与收益归因分析脚本

严格执行工单要求：
1. 逐候选记录：信号时间、H、原计划执行时间、执行价格、放行/拒绝原因，以及原对照(FT-ENTRY-03)交易结果；
2. 精确四分类：
   - 排除的亏损交易 (EXCLUDED_LOSS)
   - 错过的盈利交易 (MISSED_PROFIT)
   - 因资金或持仓释放而新增、变化的交易 (CAPACITY_RELEASED_NEW_TRADE)
   - 原样放行保留的交易 (RETAINED_TRADE)
3. 严格因果原则：后续收益只能用于归因分析，不能参与下单判断；
4. 无法匹配的交易单独列出 (UNMATCHED_TRADES)；
5. 输出完整的对账表、归因分解表与统计汇总。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT03_OUT = BASE_DIR / "evidence/FT-ENTRY-03/out"
FT04_DIR = BASE_DIR / "evidence/FT-ENTRY-04"
FT04_OUT = FT04_DIR / "out"
LOGS_DIR = FT04_DIR / "logs"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FT-ENTRY-04-Reconciliation")

A_STRATEGIES = [
    "Wyckoff_A_L06_V08", "Wyckoff_A_L06_V10", "Wyckoff_A_L06_V12",
    "Wyckoff_A_L04_V08", "Wyckoff_A_L04_V10", "Wyckoff_A_L04_V12",
    "Wyckoff_A_L02_V08", "Wyckoff_A_L02_V10", "Wyckoff_A_L02_V12",
]


def reconcile_strategy(strat_name: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """对单组 A 路线策略执行逐候选对照与对账。"""
    ft03_trades_file = FT03_OUT / f"trades_{strat_name}.csv"
    ft04_trades_file = FT04_OUT / f"trades_{strat_name}.csv"
    audit_file = FT04_OUT / f"audit_orders_{strat_name}.csv"

    df_ft03 = pd.read_csv(ft03_trades_file) if ft03_trades_file.exists() else pd.DataFrame()
    df_ft04 = pd.read_csv(ft04_trades_file) if ft04_trades_file.exists() else pd.DataFrame()
    df_audit = pd.read_csv(audit_file) if audit_file.exists() else pd.DataFrame()

    # 建立 FT-03 索引 (enter_tag)
    ft03_by_tag = {}
    if not df_ft03.empty:
        for _, r in df_ft03.iterrows():
            ft03_by_tag[r["enter_tag"]] = r

    # 建立 FT-04 索引 (enter_tag)
    ft04_by_tag = {}
    if not df_ft04.empty:
        for _, r in df_ft04.iterrows():
            ft04_by_tag[r["enter_tag"]] = r

    reconciliation_rows = []
    seen_tags = set()

    # 首先遍历所有在 FT-04 中进入过下单确认评估的 candidate
    if not df_audit.empty:
        # 去重：每个 candidate 仅取首次评估决定
        df_audit_first = df_audit.drop_duplicates(subset=["candidate_id"], keep="first")
        for _, a_row in df_audit_first.iterrows():
            cand_id = a_row["candidate_id"]
            seen_tags.add(cand_id)
            pair = a_row["pair"]
            sig_time = a_row["signal_time"]
            frozen_H = float(a_row["frozen_H"])
            plan_time = a_row["planned_execution_time"]
            fill_p = float(a_row["fill_price"])
            p_minus_h = float(a_row["price_minus_h"])
            decision = a_row["decision"]
            reason = a_row["reason"]

            ft03_trade = ft03_by_tag.get(cand_id)
            ft04_trade = ft04_by_tag.get(cand_id)

            category = "UNKNOWN"
            attribution_pnl = 0.0

            if decision == "REJECTED":
                if ft03_trade is not None:
                    pnl_03 = float(ft03_trade.get("profit_abs", ft03_trade.get("net_pnl", 0.0)))
                    if pnl_03 < 0:
                        category = "EXCLUDED_LOSS"  # 排除的亏损交易
                        attribution_pnl = -pnl_03  # 挽回金额 (>0)
                    elif pnl_03 > 0:
                        category = "MISSED_PROFIT"  # 错过的盈利交易
                        attribution_pnl = -pnl_03  # 损失金额 (<0)
                    else:
                        category = "EXCLUDED_EVEN"
                        attribution_pnl = 0.0
                else:
                    category = "REJECTED_NEITHER_FILLED"
                    attribution_pnl = 0.0
            elif decision == "PASSED":
                if ft04_trade is not None:
                    if ft03_trade is not None:
                        category = "RETAINED_TRADE"  # 保留交易
                        pnl_03 = float(ft03_trade.get("profit_abs", ft03_trade.get("net_pnl", 0.0)))
                        pnl_04 = float(ft04_trade.get("profit_abs", ft04_trade.get("net_pnl", 0.0)))
                        attribution_pnl = pnl_04 - pnl_03
                    else:
                        category = "CAPACITY_RELEASED_NEW_TRADE"  # 持仓/资金释放新增交易
                        pnl_04 = float(ft04_trade.get("profit_abs", ft04_trade.get("net_pnl", 0.0)))
                        attribution_pnl = pnl_04
                else:
                    category = "PASSED_BUT_NOT_FILLED"  # 可能由于钱包余额/滑点等未在回测中成交
                    attribution_pnl = 0.0

            reconciliation_rows.append({
                "strategy": strat_name,
                "candidate_id": cand_id,
                "pair": pair,
                "signal_time": sig_time,
                "frozen_H": frozen_H,
                "planned_execution_time": plan_time,
                "actual_fill_price": fill_p,
                "fill_price_minus_H": p_minus_h,
                "decision": decision,
                "decision_reason": reason,
                "category": category,
                "in_ft03": ft03_trade is not None,
                "in_ft04": ft04_trade is not None,
                "ft03_open_date": ft03_trade["open_date"] if ft03_trade is not None else None,
                "ft03_open_rate": float(ft03_trade["open_rate"]) if ft03_trade is not None else None,
                "ft03_profit_abs": float(ft03_trade.get("profit_abs", ft03_trade.get("net_pnl", 0.0))) if ft03_trade is not None else None,
                "ft04_open_date": ft04_trade["open_date"] if ft04_trade is not None else None,
                "ft04_open_rate": float(ft04_trade["open_rate"]) if ft04_trade is not None else None,
                "ft04_profit_abs": float(ft04_trade.get("profit_abs", ft04_trade.get("net_pnl", 0.0))) if ft04_trade is not None else None,
                "attribution_pnl_delta": round(attribution_pnl, 4)
            })

    # 检查 FT-03 中是否存在未在 audit 中记录的孤儿交易
    unmatched_ft03 = []
    for tag, r in ft03_by_tag.items():
        if tag not in seen_tags:
            unmatched_ft03.append(r)
            reconciliation_rows.append({
                "strategy": strat_name,
                "candidate_id": tag,
                "pair": r["pair"],
                "signal_time": None,
                "frozen_H": None,
                "planned_execution_time": r["open_date"],
                "actual_fill_price": float(r["open_rate"]),
                "fill_price_minus_H": None,
                "decision": "UNKNOWN_AUDIT_MISSING",
                "decision_reason": "not_in_ft04_audit",
                "category": "UNMATCHED_FT03_ORPHAN",
                "in_ft03": True,
                "in_ft04": False,
                "ft03_open_date": r["open_date"],
                "ft03_open_rate": float(r["open_rate"]),
                "ft03_profit_abs": float(r.get("profit_abs", r.get("net_pnl", 0.0))),
                "ft04_open_date": None,
                "ft04_open_rate": None,
                "ft04_profit_abs": None,
                "attribution_pnl_delta": -float(r.get("profit_abs", r.get("net_pnl", 0.0)))
            })

    # 检查 FT-04 中是否存在未在 audit 中记录的孤儿交易
    unmatched_ft04 = []
    for tag, r in ft04_by_tag.items():
        if tag not in seen_tags:
            unmatched_ft04.append(r)
            reconciliation_rows.append({
                "strategy": strat_name,
                "candidate_id": tag,
                "pair": r["pair"],
                "signal_time": None,
                "frozen_H": None,
                "planned_execution_time": r["open_date"],
                "actual_fill_price": float(r["open_rate"]),
                "fill_price_minus_H": None,
                "decision": "UNKNOWN_AUDIT_MISSING",
                "decision_reason": "not_in_ft04_audit",
                "category": "UNMATCHED_FT04_ORPHAN",
                "in_ft03": False,
                "in_ft04": True,
                "ft03_open_date": None,
                "ft03_open_rate": None,
                "ft03_profit_abs": None,
                "ft04_open_date": r["open_date"],
                "ft04_open_rate": float(r["open_rate"]),
                "ft04_profit_abs": float(r.get("profit_abs", r.get("net_pnl", 0.0))),
                "attribution_pnl_delta": float(r.get("profit_abs", r.get("net_pnl", 0.0)))
            })

    df_recon = pd.DataFrame(reconciliation_rows)

    # 归因汇总统计
    ex_loss = df_recon[df_recon["category"] == "EXCLUDED_LOSS"]
    miss_prof = df_recon[df_recon["category"] == "MISSED_PROFIT"]
    retained = df_recon[df_recon["category"] == "RETAINED_TRADE"]
    new_trades = df_recon[df_recon["category"] == "CAPACITY_RELEASED_NEW_TRADE"]

    pnl_03_total = float(df_ft03["profit_abs"].sum()) if not df_ft03.empty else 0.0
    pnl_04_total = float(df_ft04["profit_abs"].sum()) if not df_ft04.empty else 0.0

    recovered_loss_u = float(ex_loss["attribution_pnl_delta"].sum())
    missed_profit_u = -float(miss_prof["attribution_pnl_delta"].sum())
    new_trades_pnl_u = float(new_trades["attribution_pnl_delta"].sum())
    retained_drift_u = float(retained["attribution_pnl_delta"].sum())

    summary = {
        "strategy": strat_name,
        "ft03_trades": len(df_ft03),
        "ft04_trades": len(df_ft04),
        "ft03_net_pnl": round(pnl_03_total, 4),
        "ft04_net_pnl": round(pnl_04_total, 4),
        "net_pnl_delta": round(pnl_04_total - pnl_03_total, 4),
        "excluded_loss_count": len(ex_loss),
        "recovered_loss_u": round(recovered_loss_u, 4),
        "missed_profit_count": len(miss_prof),
        "missed_profit_u": round(missed_profit_u, 4),
        "capacity_new_trades_count": len(new_trades),
        "capacity_new_trades_pnl_u": round(new_trades_pnl_u, 4),
        "retained_trades_count": len(retained),
        "retained_drift_u": round(retained_drift_u, 4),
        "unmatched_count": len(unmatched_ft03) + len(unmatched_ft04)
    }

    return df_recon, summary


def main():
    logger.info("=== FT-ENTRY-04 逐候选对照与收益归因分析开始 ===")
    all_recon_dfs = []
    summary_rows = []

    for strat in A_STRATEGIES:
        df_recon, summ = reconcile_strategy(strat)
        all_recon_dfs.append(df_recon)
        summary_rows.append(summ)
        logger.info(
            f"[{strat}] 交易数: {summ['ft03_trades']} -> {summ['ft04_trades']} | "
            f"排除亏损: {summ['excluded_loss_count']}笔 (+{summ['recovered_loss_u']}U) | "
            f"错过盈利: {summ['missed_profit_count']}笔 (-{summ['missed_profit_u']}U) | "
            f"新增交易: {summ['capacity_new_trades_count']}笔 ({summ['capacity_new_trades_pnl_u']:+}U) | "
            f"净收益变动: {summ['net_pnl_delta']:+}U"
        )

    df_all_recon = pd.concat(all_recon_dfs, ignore_index=True)
    df_all_recon.to_csv(FT04_OUT / "06_逐候选全量对照与归因表.csv", index=False)

    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(FT04_OUT / "07_收益变动四分类归因汇总表.csv", index=False)

    # 导出无法匹配的孤儿单（若有）
    df_unmatched = df_all_recon[df_all_recon["category"].str.startswith("UNMATCHED")]
    df_unmatched.to_csv(FT04_OUT / "08_无法匹配交易单列表.csv", index=False)
    logger.info(f"无法匹配交易数: {len(df_unmatched)}")

    logger.info("\n=== 四分类归因汇总表 ===\n" + df_summary.to_string())
    logger.info("=== FT-ENTRY-04 逐候选对照与归因分析完成 ===")


if __name__ == "__main__":
    main()

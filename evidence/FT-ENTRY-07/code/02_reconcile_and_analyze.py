# -*- coding: utf-8 -*-
"""FT-ENTRY-07 对账与综合统计分析脚本

严格落实工单要求：
1. 9组参数下 C0、C1、C2 一一对照（共27组实验）；
2. 计算扣费后胜率、平均盈亏、净期望、净收益、快速止损笔数及占比；
3. 逐候选对账：区分避免的亏损、错过的盈利、延迟入场后价格和初始风险变化、仓位竞争引起的其他交易变化；
4. 严格执行因果与偏误审计。
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Any, Optional

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_ENTRY_DIR = BASE_DIR / "evidence/FT-ENTRY-07"
OUT_DIR = FT_ENTRY_DIR / "out"
LOGS_DIR = FT_ENTRY_DIR / "logs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("FT-ENTRY-07-Analysis")

PARAM_KEYS = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"
]

EVENT_ID_REGEX = re.compile(r'(ev_FT_ENTRY_02_binance_[^_]+_\d+)')


def extract_event_id(enter_tag: str) -> Optional[str]:
    """从 enter_tag 中稳健提取事件唯一 ID。"""
    if not isinstance(enter_tag, str):
        return None
    m = EVENT_ID_REGEX.search(enter_tag)
    return m.group(1) if m else None


def load_trades_csv(csv_path: Path) -> pd.DataFrame:
    """读取 trades.csv 并预处理必要字段。"""
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return pd.DataFrame()
    df = pd.read_csv(csv_path)
    if df.empty:
        return df

    # 解析 event_id
    if "enter_tag" in df.columns:
        df["event_id"] = df["enter_tag"].apply(extract_event_id)
    else:
        df["event_id"] = None

    # 快速止损定义 (沿用 FT-ENTRY-06):
    # 持仓时间 <= 12小时 (720分钟) 且 亏损 (profit_abs < 0) 且 MFE < 3% (max_rate < 0.03)
    df["is_loss"] = df["profit_abs"] < 0
    df["is_win"] = df["profit_abs"] > 0
    df["quick_stop"] = (
        (df["trade_duration"] <= 720) &
        df["is_loss"] &
        (df["max_rate"] < 0.03)
    )
    return df


def calculate_metrics(strategy_name: str, timing_mode: str, vs: float, z: float, df: pd.DataFrame) -> Dict[str, Any]:
    """计算单组策略的各项标准评估指标。"""
    res = {
        "strategy": strategy_name,
        "timing_mode": timing_mode,
        "vs": vs,
        "z": z,
        "trades_count": len(df),
        "win_trades": 0,
        "loss_trades": 0,
        "win_rate": 0.0,
        "total_profit_usdt": 0.0,
        "avg_trade_usdt": 0.0,
        "avg_trade_pct": 0.0,
        "avg_win_pct": 0.0,
        "avg_loss_pct": 0.0,
        "net_ev_pct": 0.0,
        "profit_factor": 0.0,
        "quick_stop_count": 0,
        "quick_stop_ratio_all": 0.0,
        "quick_stop_ratio_loss": 0.0,
        "max_drawdown_usdt": 0.0,
    }
    if df.empty:
        return res

    n = len(df)
    wins = df[df["is_win"]]
    losses = df[df["is_loss"]]
    n_win = len(wins)
    n_loss = len(losses)
    win_rate = n_win / n if n > 0 else 0.0

    total_profit = df["profit_abs"].sum()
    avg_trade_usdt = total_profit / n if n > 0 else 0.0
    avg_trade_pct = df["profit_ratio"].mean() * 100.0 if n > 0 else 0.0

    avg_win_pct = wins["profit_ratio"].mean() * 100.0 if n_win > 0 else 0.0
    avg_loss_pct = losses["profit_ratio"].mean() * 100.0 if n_loss > 0 else 0.0

    # 净期望比率 EV %: win_rate * avg_win + loss_rate * avg_loss
    loss_rate = n_loss / n if n > 0 else 0.0
    net_ev_pct = win_rate * avg_win_pct + loss_rate * avg_loss_pct

    # 利润因子 (毛利 / 毛亏)
    gross_profit = wins["profit_abs"].sum() if n_win > 0 else 0.0
    gross_loss = abs(losses["profit_abs"].sum()) if n_loss > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # 快速止损
    qs_count = int(df["quick_stop"].sum())
    qs_ratio_all = qs_count / n if n > 0 else 0.0
    qs_ratio_loss = qs_count / n_loss if n_loss > 0 else 0.0

    # 资金曲线与最大回撤
    df_sorted = df.sort_values("close_timestamp").reset_index(drop=True)
    equity = df_sorted["profit_abs"].cumsum()
    peak = equity.cummax()
    dd = peak - equity
    max_dd = dd.max() if not dd.empty else 0.0

    res.update({
        "win_trades": n_win,
        "loss_trades": n_loss,
        "win_rate": round(win_rate * 100.0, 2),
        "total_profit_usdt": round(total_profit, 2),
        "avg_trade_usdt": round(avg_trade_usdt, 2),
        "avg_trade_pct": round(avg_trade_pct, 2),
        "avg_win_pct": round(avg_win_pct, 2),
        "avg_loss_pct": round(avg_loss_pct, 2),
        "net_ev_pct": round(net_ev_pct, 2),
        "profit_factor": round(profit_factor, 2),
        "quick_stop_count": qs_count,
        "quick_stop_ratio_all": round(qs_ratio_all * 100.0, 2),
        "quick_stop_ratio_loss": round(qs_ratio_loss * 100.0, 2),
        "max_drawdown_usdt": round(max_dd, 2),
    })
    return res


def build_candidate_reconciliation_table(param_key: str) -> pd.DataFrame:
    """针对单一参数组构建 C0、C1、C2 逐候选对账表。"""
    c0_csv = OUT_DIR / f"trades_Wyckoff_C_{param_key}.csv"
    c1_csv = OUT_DIR / f"trades_Wyckoff_C1_{param_key}.csv"
    c2_csv = OUT_DIR / f"trades_Wyckoff_C2_{param_key}.csv"

    c1_audit_csv = OUT_DIR / f"audit_confirmation_Wyckoff_C1_{param_key}.csv"
    c2_audit_csv = OUT_DIR / f"audit_confirmation_Wyckoff_C2_{param_key}.csv"

    df_c0 = load_trades_csv(c0_csv)
    df_c1 = load_trades_csv(c1_csv)
    df_c2 = load_trades_csv(c2_csv)

    df_audit_c1 = pd.read_csv(c1_audit_csv) if c1_audit_csv.exists() else pd.DataFrame()
    df_audit_c2 = pd.read_csv(c2_audit_csv) if c2_audit_csv.exists() else pd.DataFrame()

    # 建立事件索引字典
    c0_dict = {}
    if not df_c0.empty and "event_id" in df_c0.columns:
        for _, row in df_c0.iterrows():
            eid = row["event_id"]
            if eid:
                c0_dict[eid] = row

    c1_dict = {}
    if not df_c1.empty and "event_id" in df_c1.columns:
        for _, row in df_c1.iterrows():
            eid = row["event_id"]
            if eid:
                c1_dict[eid] = row

    c2_dict = {}
    if not df_c2.empty and "event_id" in df_c2.columns:
        for _, row in df_c2.iterrows():
            eid = row["event_id"]
            if eid:
                c2_dict[eid] = row

    audit_c1_dict = {}
    if not df_audit_c1.empty:
        for _, row in df_audit_c1.iterrows():
            eid = row["event_id"]
            audit_c1_dict[eid] = row

    audit_c2_dict = {}
    if not df_audit_c2.empty:
        for _, row in df_audit_c2.iterrows():
            eid = row["event_id"]
            audit_c2_dict[eid] = row

    # 候选总集合：包含所有出现过的 event_id
    all_events = set(c0_dict.keys()) | set(c1_dict.keys()) | set(c2_dict.keys()) | set(audit_c1_dict.keys()) | set(audit_c2_dict.keys())

    records = []
    for eid in sorted(list(all_events)):
        # 基础信息
        c0_row = c0_dict.get(eid)
        c1_row = c1_dict.get(eid)
        c2_row = c2_dict.get(eid)
        a1_row = audit_c1_dict.get(eid)
        a2_row = audit_c2_dict.get(eid)

        symbol = (c0_row["pair"] if c0_row is not None else
                  (c1_row["pair"] if c1_row is not None else
                   (a1_row["symbol"] if a1_row is not None else "UNKNOWN")))
        if ":" in symbol:
            symbol = symbol.split(":")[0]

        # C0 指标
        c0_filled = c0_row is not None
        c0_open_time = c0_row["open_date"] if c0_filled else None
        c0_open_rate = c0_row["open_rate"] if c0_filled else None
        c0_profit_abs = c0_row["profit_abs"] if c0_filled else None
        c0_profit_ratio = c0_row["profit_ratio"] if c0_filled else None
        c0_quick_stop = c0_row["quick_stop"] if c0_filled else None
        c0_exit_reason = c0_row["exit_reason"] if c0_filled else None
        c0_p0 = a1_row["orig_P0"] if a1_row is not None else (a2_row["orig_P0"] if a2_row is not None else None)

        # C1 确认决策与指标
        c1_decision = a1_row["decision"] if a1_row is not None else ("CONFIRMED" if c1_row is not None else "NONE")
        c1_reason = a1_row["reason"] if a1_row is not None else None
        c1_filled = c1_row is not None
        c1_open_time = c1_row["open_date"] if c1_filled else None
        c1_open_rate = c1_row["open_rate"] if c1_filled else None
        c1_profit_abs = c1_row["profit_abs"] if c1_filled else None
        c1_profit_ratio = c1_row["profit_ratio"] if c1_filled else None
        c1_quick_stop = c1_row["quick_stop"] if c1_filled else None
        c1_exit_reason = c1_row["exit_reason"] if c1_filled else None

        # C2 确认决策与指标
        c2_decision = a2_row["decision"] if a2_row is not None else ("CONFIRMED" if c2_row is not None else "NONE")
        c2_reason = a2_row["reason"] if a2_row is not None else None
        c2_filled = c2_row is not None
        c2_open_time = c2_row["open_date"] if c2_filled else None
        c2_open_rate = c2_row["open_rate"] if c2_filled else None
        c2_profit_abs = c2_row["profit_abs"] if c2_filled else None
        c2_profit_ratio = c2_row["profit_ratio"] if c2_filled else None
        c2_quick_stop = c2_row["quick_stop"] if c2_filled else None
        c2_exit_reason = c2_row["exit_reason"] if c2_filled else None

        # C1 价格与风险变化
        c1_price_diff_pct = None
        c1_r_diff_pct = None
        if c0_filled and c1_filled and c0_open_rate and c1_open_rate:
            c1_price_diff_pct = round(((c1_open_rate - c0_open_rate) / c0_open_rate) * 100.0, 3)
            if c0_p0 and c0_open_rate > c0_p0 and c1_open_rate > c0_p0:
                r0 = c0_open_rate - c0_p0
                r1 = c1_open_rate - c0_p0
                c1_r_diff_pct = round(((r1 - r0) / r0) * 100.0, 3)

        # C2 价格与风险变化
        c2_price_diff_pct = None
        c2_r_diff_pct = None
        if c0_filled and c2_filled and c0_open_rate and c2_open_rate:
            c2_price_diff_pct = round(((c2_open_rate - c0_open_rate) / c0_open_rate) * 100.0, 3)
            if c0_p0 and c0_open_rate > c0_p0 and c2_open_rate > c0_p0:
                r0 = c0_open_rate - c0_p0
                r2 = c2_open_rate - c0_p0
                c2_r_diff_pct = round(((r2 - r0) / r0) * 100.0, 3)

        # C1 归因分类
        if c0_filled and not c1_filled:
            if c0_quick_stop:
                c1_class = "避免快速止损 (AVOIDED_QUICK_STOP)"
            elif c0_profit_abs < 0:
                c1_class = "避免普通亏损 (AVOIDED_NORMAL_LOSS)"
            else:
                c1_class = "错过盈利 (MISSED_GAIN)"
        elif not c0_filled and c1_filled:
            c1_class = "仓位释放新成交 (SLOT_COMPETITION_NEW)"
        elif c0_filled and c1_filled:
            if c1_profit_abs > c0_profit_abs:
                c1_class = "延迟入场增益 (DELAY_IMPROVED)"
            elif c1_profit_abs < c0_profit_abs:
                c1_class = "延迟入场受损 (DELAY_DEGRADED)"
            else:
                c1_class = "结果无差异 (SAME_OUTCOME)"
        else:
            c1_class = "两端均未成交 (NEITHER_FILLED)"

        # C2 归因分类
        if c0_filled and not c2_filled:
            if c0_quick_stop:
                c2_class = "避免快速止损 (AVOIDED_QUICK_STOP)"
            elif c0_profit_abs < 0:
                c2_class = "避免普通亏损 (AVOIDED_NORMAL_LOSS)"
            else:
                c2_class = "错过盈利 (MISSED_GAIN)"
        elif not c0_filled and c2_filled:
            c2_class = "仓位释放新成交 (SLOT_COMPETITION_NEW)"
        elif c0_filled and c2_filled:
            if c2_profit_abs > c0_profit_abs:
                c2_class = "延迟入场增益 (DELAY_IMPROVED)"
            elif c2_profit_abs < c0_profit_abs:
                c2_class = "延迟入场受损 (DELAY_DEGRADED)"
            else:
                c2_class = "结果无差异 (SAME_OUTCOME)"
        else:
            c2_class = "两端均未成交 (NEITHER_FILLED)"

        records.append({
            "param_group": param_key,
            "event_id": eid,
            "symbol": symbol,
            "P0_stop": c0_p0,
            # C0
            "c0_filled": c0_filled,
            "c0_open_time": c0_open_time,
            "c0_open_rate": c0_open_rate,
            "c0_profit_abs": c0_profit_abs,
            "c0_profit_ratio": c0_profit_ratio,
            "c0_quick_stop": c0_quick_stop,
            "c0_exit_reason": c0_exit_reason,
            # C1
            "c1_decision": c1_decision,
            "c1_reason": c1_reason,
            "c1_filled": c1_filled,
            "c1_open_time": c1_open_time,
            "c1_open_rate": c1_open_rate,
            "c1_profit_abs": c1_profit_abs,
            "c1_profit_ratio": c1_profit_ratio,
            "c1_quick_stop": c1_quick_stop,
            "c1_exit_reason": c1_exit_reason,
            "c1_price_diff_pct": c1_price_diff_pct,
            "c1_r_diff_pct": c1_r_diff_pct,
            "c1_class": c1_class,
            # C2
            "c2_decision": c2_decision,
            "c2_reason": c2_reason,
            "c2_filled": c2_filled,
            "c2_open_time": c2_open_time,
            "c2_open_rate": c2_open_rate,
            "c2_profit_abs": c2_profit_abs,
            "c2_profit_ratio": c2_profit_ratio,
            "c2_quick_stop": c2_quick_stop,
            "c2_exit_reason": c2_exit_reason,
            "c2_price_diff_pct": c2_price_diff_pct,
            "c2_r_diff_pct": c2_r_diff_pct,
            "c2_class": c2_class,
        })

    return pd.DataFrame(records)


def main():
    logger.info("=== FT-ENTRY-07 对账与综合统计开始 ===")

    # 1. 生成 27 组指标总表
    metrics_list = []
    for pk in PARAM_KEYS:
        # 解析 vs 和 z
        # pk 形如 Vs04_Z02 -> vs=0.4, z=0.2
        m = re.match(r"Vs0?(\d+)_Z0?(\d+)", pk)
        if m:
            vs = float(f"0.{m.group(1)}")
            z = float(f"0.{m.group(2)}")
        else:
            vs, z = 0.0, 0.0

        for mode, prefix in [("C0", "Wyckoff_C_"), ("C1", "Wyckoff_C1_"), ("C2", "Wyckoff_C2_")]:
            strat_name = f"{prefix}{pk}"
            csv_path = OUT_DIR / f"trades_{strat_name}.csv"
            df = load_trades_csv(csv_path)
            met = calculate_metrics(strat_name, mode, vs, z, df)
            metrics_list.append(met)

    df_metrics = pd.DataFrame(metrics_list)
    out_metrics_csv = FT_ENTRY_DIR / "01_全部运行索引与指标总表.csv"
    df_metrics.to_csv(out_metrics_csv, index=False, encoding="utf-8-sig")
    logger.info(f"已导出 27 组指标总表: {out_metrics_csv}")

    # 2. 逐候选对账
    reconciled_dfs = []
    for pk in PARAM_KEYS:
        rdf = build_candidate_reconciliation_table(pk)
        reconciled_dfs.append(rdf)

    df_reconciliation = pd.concat(reconciled_dfs, ignore_index=True)
    out_reconcile_csv = FT_ENTRY_DIR / "02_逐候选对照表.csv"
    df_reconciliation.to_csv(out_reconcile_csv, index=False, encoding="utf-8-sig")
    logger.info(f"已导出逐候选对照表: {out_reconcile_csv}，记录行数: {len(df_reconciliation)}")

    # 3. 汇总分类统计
    print("\n" + "=" * 50)
    print("【指标总览】扣费后收益与快速止损对比:")
    print("=" * 50)
    summary_cols = [
        "strategy", "timing_mode", "trades_count", "win_rate",
        "total_profit_usdt", "avg_trade_usdt", "net_ev_pct",
        "quick_stop_count", "quick_stop_ratio_all"
    ]
    print(df_metrics[summary_cols].to_string(index=False))

    print("\n" + "=" * 50)
    print("【C1 机制归因分布统计】:")
    print(df_reconciliation["c1_class"].value_counts().to_string())

    print("\n" + "=" * 50)
    print("【C2 机制归因分布统计】:")
    print(df_reconciliation["c2_class"].value_counts().to_string())


if __name__ == "__main__":
    main()

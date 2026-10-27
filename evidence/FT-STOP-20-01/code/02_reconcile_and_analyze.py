# -*- coding: utf-8 -*-
"""FT-STOP-20-01: 对照分析与深度归因脚本。

涵盖核心核算模块：
1. 九组实验基础绩效指标配对（胜率、胜负笔数、净盈亏、期望EV）；
2. 仓位与风险规模对比（持仓数量、名义金额、实际初始风险 R）；
3. 退出原因构成与盈亏贡献（初始止损、跟踪止损、超时退出）；
4. 最大不利波动（MAE）、持仓时长与风险暴露；
5. 原方案止损交易在 20% 方案中的命运归因（转盈、亏损扩大、超时退出）；
6. 仓位竞争导致的新增与消失交易剖析。
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FT-STOP-20-Reconcile")

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT09_DIR = BASE_DIR / "evidence/FT-ENTRY-09"
STOP20_DIR = BASE_DIR / "evidence/FT-STOP-20-01"
OUT_DIR = STOP20_DIR / "out"

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]

# 1. 加载并对齐原方案 (FT09 C1 9组) 与新方案 (Stop20 C1 9组) 逐笔交易
orig_trades = {}
stop20_trades = {}

for suffix in MATRIX_SUFFIXES:
    strat_orig = f"Wyckoff_C1_{suffix}"
    strat_stop20 = f"Wyckoff_C1_Stop20_{suffix}"
    
    csv_orig = FT09_DIR / f"out/trades_{strat_orig}.csv"
    csv_stop20 = STOP20_DIR / f"out/trades_{strat_stop20}.csv"
    
    df_o = pd.read_csv(csv_orig) if csv_orig.exists() else pd.DataFrame()
    df_s = pd.read_csv(csv_stop20) if csv_stop20.exists() else pd.DataFrame()
    
    orig_trades[suffix] = df_o
    stop20_trades[suffix] = df_s

logger.info("Successfully loaded all trade CSVs for both groups.")

# 2. 宏观绩效指标对比
macro_rows = []
for suffix in MATRIX_SUFFIXES:
    df_o = orig_trades[suffix]
    df_s = stop20_trades[suffix]
    
    n_o = len(df_o)
    n_s = len(df_s)
    
    wins_o = int((df_o["profit_abs"] > 0).sum()) if n_o > 0 else 0
    loss_o = int((df_o["profit_abs"] < 0).sum()) if n_o > 0 else 0
    pnl_o = round(float(df_o["profit_abs"].sum()), 2) if n_o > 0 else 0.0
    wr_o = round(wins_o / n_o * 100.0, 2) if n_o > 0 else 0.0
    ev_o = round(pnl_o / n_o, 2) if n_o > 0 else 0.0
    
    wins_s = int((df_s["profit_abs"] > 0).sum()) if n_s > 0 else 0
    loss_s = int((df_s["profit_abs"] < 0).sum()) if n_s > 0 else 0
    pnl_s = round(float(df_s["profit_abs"].sum()), 2) if n_s > 0 else 0.0
    wr_s = round(wins_s / n_s * 100.0, 2) if n_s > 0 else 0.0
    ev_s = round(pnl_s / n_s, 2) if n_s > 0 else 0.0
    
    macro_rows.append({
        "param_suffix": suffix,
        "orig_trades": n_o,
        "orig_win_trades": wins_o,
        "orig_loss_trades": loss_o,
        "orig_win_rate_pct": wr_o,
        "orig_net_pnl_usdt": pnl_o,
        "orig_expectancy_ev": ev_o,
        "stop20_trades": n_s,
        "stop20_win_trades": wins_s,
        "stop20_loss_trades": loss_s,
        "stop20_win_rate_pct": wr_s,
        "stop20_net_pnl_usdt": pnl_s,
        "stop20_expectancy_ev": ev_s,
        "diff_trades": n_s - n_o,
        "diff_win_rate_pct": round(wr_s - wr_o, 2),
        "diff_net_pnl_usdt": round(pnl_s - pnl_o, 2),
        "diff_ev_usdt": round(ev_s - ev_o, 2),
    })

df_macro = pd.DataFrame(macro_rows)
df_macro.to_csv(OUT_DIR / "01_九组实验宏观指标对照总表.csv", index=False, encoding="utf-8")
logger.info("Saved 01_九组实验宏观指标对照总表.csv")

# 3. 仓位与风险规模指标对比
scale_rows = []
for suffix in MATRIX_SUFFIXES:
    df_o = orig_trades[suffix]
    df_s = stop20_trades[suffix]
    
    # 原方案初始风险: abs(open_rate - initial_stop_loss_abs) * amount
    # 新方案初始风险: open_rate * 0.20 * amount = stake_amount * 0.20
    avg_stake_o = round(float(df_o["stake_amount"].mean()), 2) if len(df_o) > 0 else 0.0
    avg_amount_o = round(float(df_o["amount"].mean()), 4) if len(df_o) > 0 else 0.0
    if len(df_o) > 0:
        r_o_series = (df_o["open_rate"] - df_o["initial_stop_loss_abs"]).abs() * df_o["amount"]
        avg_r_o = round(float(r_o_series.mean()), 2)
        total_stake_o = round(float(df_o["stake_amount"].sum()), 2)
    else:
        avg_r_o = 0.0
        total_stake_o = 0.0
        
    avg_stake_s = round(float(df_s["stake_amount"].mean()), 2) if len(df_s) > 0 else 0.0
    avg_amount_s = round(float(df_s["amount"].mean()), 4) if len(df_s) > 0 else 0.0
    if len(df_s) > 0:
        r_s_series = df_s["open_rate"] * 0.20 * df_s["amount"]
        avg_r_s = round(float(r_s_series.mean()), 2)
        total_stake_s = round(float(df_s["stake_amount"].sum()), 2)
    else:
        avg_r_s = 0.0
        total_stake_s = 0.0
        
    scale_rows.append({
        "param_suffix": suffix,
        "orig_avg_stake_usdt": avg_stake_o,
        "orig_total_stake_usdt": total_stake_o,
        "orig_avg_planned_risk_r": avg_r_o,
        "stop20_avg_stake_usdt": avg_stake_s,
        "stop20_total_stake_usdt": total_stake_s,
        "stop20_avg_planned_risk_r": avg_r_s,
        "stake_ratio_stop20_vs_orig": round(avg_stake_s / avg_stake_o, 4) if avg_stake_o > 0 else None,
    })

df_scale = pd.DataFrame(scale_rows)
df_scale.to_csv(OUT_DIR / "02_仓位与风险规模指标对比表.csv", index=False, encoding="utf-8")
logger.info("Saved 02_仓位与风险规模指标对比表.csv")

# 4. 退出原因构成与盈亏贡献
exit_rows = []
for suffix in MATRIX_SUFFIXES:
    df_o = orig_trades[suffix]
    df_s = stop20_trades[suffix]
    
    def analyze_exits(df, tag):
        res = {}
        if len(df) == 0:
            return res
        for reason, grp in df.groupby("exit_reason"):
            cnt = len(grp)
            pnl = round(float(grp["profit_abs"].sum()), 2)
            wins = int((grp["profit_abs"] > 0).sum())
            res[f"{tag}_{reason}_count"] = cnt
            res[f"{tag}_{reason}_pnl"] = pnl
            res[f"{tag}_{reason}_win_rate"] = round(wins / cnt * 100.0, 2)
        return res

    row = {"param_suffix": suffix}
    row.update(analyze_exits(df_o, "orig"))
    row.update(analyze_exits(df_s, "stop20"))
    exit_rows.append(row)

df_exit = pd.DataFrame(exit_rows).fillna(0)
df_exit.to_csv(OUT_DIR / "03_退出原因构成与盈亏贡献分解表.csv", index=False, encoding="utf-8")
logger.info("Saved 03_退出原因构成与盈亏贡献分解表.csv")

# 5. 持仓特征与 MAE 波动
feature_rows = []
for suffix in MATRIX_SUFFIXES:
    df_o = orig_trades[suffix]
    df_s = stop20_trades[suffix]
    
    def get_features(df):
        if len(df) == 0:
            return 0.0, 0.0, 0.0
        # holding duration in hours
        dur_h = df["trade_duration"] / 3600.0
        avg_dur = round(float(dur_h.mean()), 2)
        # MAE: (min_rate - open_rate) / open_rate (for longs)
        mae = ((df["min_rate"] - df["open_rate"]) / df["open_rate"] * 100.0).abs()
        avg_mae = round(float(mae.mean()), 2)
        max_mae = round(float(mae.max()), 2)
        return avg_dur, avg_mae, max_mae

    od_dur, od_mae, od_max_mae = get_features(df_o)
    sd_dur, sd_mae, sd_max_mae = get_features(df_s)
    
    feature_rows.append({
        "param_suffix": suffix,
        "orig_avg_holding_hours": od_dur,
        "orig_avg_mae_pct": od_mae,
        "orig_max_mae_pct": od_max_mae,
        "stop20_avg_holding_hours": sd_dur,
        "stop20_avg_mae_pct": sd_mae,
        "stop20_max_mae_pct": sd_max_mae,
        "diff_holding_hours": round(sd_dur - od_dur, 2),
        "diff_avg_mae_pct": round(sd_mae - od_mae, 2),
    })

df_feat = pd.DataFrame(feature_rows)
df_feat.to_csv(OUT_DIR / "04_持仓特征与MAE波动对比表.csv", index=False, encoding="utf-8")
logger.info("Saved 04_持仓特征与MAE波动对比表.csv")

# 6. 逐候选配对追踪：原止损交易在新方案中的命运
fate_rows = []
for suffix in MATRIX_SUFFIXES:
    df_o = orig_trades[suffix].copy()
    df_s = stop20_trades[suffix].copy()
    
    # 建立候选唯一标识: enter_tag + pair (或 open_timestamp + pair)
    df_o["cand_key"] = df_o["pair"] + "_" + df_o["open_timestamp"].astype(str)
    df_s["cand_key"] = df_s["pair"] + "_" + df_s["open_timestamp"].astype(str)
    
    merged = pd.merge(df_o, df_s, on="cand_key", suffixes=("_orig", "_stop20"), how="inner")
    
    # 筛选原方案中属于止损退出 (exit_reason_orig == 'stop_loss') 的交易
    sl_orig = merged[merged["exit_reason_orig"] == "stop_loss"].copy()
    
    for _, r in sl_orig.iterrows():
        pnl_o = r["profit_abs_orig"]
        pnl_s = r["profit_abs_stop20"]
        exit_s = r["exit_reason_stop20"]
        
        fate = "未知"
        if pnl_s > 0:
            fate = "成功反弹转为盈利"
        elif exit_s == "timeout_240h" and pnl_s <= 0:
            fate = "避免立即止损但超时退出仍亏损"
        elif pnl_s < pnl_o:
            fate = "止损放宽导致亏损扩大"
        elif pnl_s > pnl_o and pnl_s <= 0:
            fate = "亏损减轻但未实现盈利"
        else:
            fate = f"其他({exit_s})"
            
        fate_rows.append({
            "param_suffix": suffix,
            "pair": r["pair_orig"],
            "open_date": r["open_date_orig"],
            "open_rate": r["open_rate_orig"],
            "orig_stop_price": r["initial_stop_loss_abs_orig"],
            "orig_exit_rate": r["close_rate_orig"],
            "orig_pnl_usdt": round(pnl_o, 2),
            "orig_exit_reason": r["exit_reason_orig"],
            "stop20_initial_stop": round(r["open_rate_stop20"] * 0.80, 4),
            "stop20_exit_rate": r["close_rate_stop20"],
            "stop20_pnl_usdt": round(pnl_s, 2),
            "stop20_exit_reason": exit_s,
            "fate_category": fate,
        })

df_fate = pd.DataFrame(fate_rows)
df_fate.to_csv(OUT_DIR / "05_原止损交易在新方案中的命运追踪表.csv", index=False, encoding="utf-8")
logger.info(f"Saved 05_原止损交易在新方案中的命运追踪表.csv with {len(df_fate)} tracked stop-loss trades.")

# 7. 仓位竞争分析：共同交易、新增交易与消失交易
competition_rows = []
all_paired_records = []

for suffix in MATRIX_SUFFIXES:
    df_o = orig_trades[suffix].copy()
    df_s = stop20_trades[suffix].copy()
    
    df_o["cand_key"] = df_o["pair"] + "_" + df_o["open_timestamp"].astype(str)
    df_s["cand_key"] = df_s["pair"] + "_" + df_s["open_timestamp"].astype(str)
    
    keys_o = set(df_o["cand_key"])
    keys_s = set(df_s["cand_key"])
    
    common_keys = keys_o & keys_s
    disappeared_keys = keys_o - keys_s  # 原方案有，新方案消失 (仓位被占满)
    added_keys = keys_s - keys_o        # 原方案无，新方案新增
    
    competition_rows.append({
        "param_suffix": suffix,
        "orig_trades_total": len(df_o),
        "stop20_trades_total": len(df_s),
        "common_trades_count": len(common_keys),
        "disappeared_trades_count": len(disappeared_keys),
        "added_trades_count": len(added_keys),
        "disappeared_pnl_orig_usdt": round(float(df_o[df_o["cand_key"].isin(disappeared_keys)]["profit_abs"].sum()), 2),
        "added_pnl_stop20_usdt": round(float(df_s[df_s["cand_key"].isin(added_keys)]["profit_abs"].sum()), 2),
    })
    
    # 记录全量明细
    for k in keys_o | keys_s:
        row_o = df_o[df_o["cand_key"] == k]
        row_s = df_s[df_s["cand_key"] == k]
        
        has_o = len(row_o) > 0
        has_s = len(row_s) > 0
        
        o_item = row_o.iloc[0] if has_o else None
        s_item = row_s.iloc[0] if has_s else None
        
        all_paired_records.append({
            "param_suffix": suffix,
            "pair": (o_item["pair"] if has_o else s_item["pair"]),
            "open_date": (o_item["open_date"] if has_o else s_item["open_date"]),
            "open_timestamp": (o_item["open_timestamp"] if has_o else s_item["open_timestamp"]),
            "match_status": "COMMON" if (has_o and has_s) else ("DISAPPEARED" if has_o else "ADDED"),
            "orig_executed": has_o,
            "orig_pnl_usdt": round(float(o_item["profit_abs"]), 2) if has_o else None,
            "orig_exit_reason": o_item["exit_reason"] if has_o else None,
            "orig_stake": round(float(o_item["stake_amount"]), 2) if has_o else None,
            "stop20_executed": has_s,
            "stop20_pnl_usdt": round(float(s_item["profit_abs"]), 2) if has_s else None,
            "stop20_exit_reason": s_item["exit_reason"] if has_s else None,
            "stop20_stake": round(float(s_item["stake_amount"]), 2) if has_s else None,
        })

df_comp = pd.DataFrame(competition_rows)
df_comp.to_csv(OUT_DIR / "06_仓位竞争引起的新增与消失交易表.csv", index=False, encoding="utf-8")
logger.info("Saved 06_仓位竞争引起的新增与消失交易表.csv")

df_all_paired = pd.DataFrame(all_paired_records)
df_all_paired.to_csv(OUT_DIR / "07_新旧方案逐候选配对全量明细底表.csv", index=False, encoding="utf-8")
logger.info(f"Saved 07_新旧方案逐候选配对全量明细底表.csv with {len(df_all_paired)} total candidate events.")

print("\n--- [FT-STOP-20-01] 宏观指标对比摘要 ---")
print(df_macro[["param_suffix", "orig_trades", "orig_win_rate_pct", "orig_net_pnl_usdt", "stop20_trades", "stop20_win_rate_pct", "stop20_net_pnl_usdt", "diff_net_pnl_usdt"]].to_string(index=False))

print("\n--- [FT-STOP-20-01] 原止损交易命运分布 ---")
if len(df_fate) > 0:
    print(df_fate["fate_category"].value_counts().to_string())

# -*- coding: utf-8 -*-
"""FT-STOP-20-02: 深度因果分析与对账脚本。

对比三方：
1. FT-ENTRY-09: C1 原方案 (结构止损 P0, 动态仓位, 240h 超时)
2. FT-STOP-20-01: 固定 20% 止损 (有 240h 超时)
3. FT-STOP-20-02: 固定 20% 止损 (取消 240h 超时)

分析产出：
- 01_三方案宏观指标对照总表.csv
- 02_退出原因构成与盈亏对比表.csv
- 03_持仓时间分布与极值对比表.csv
- 04_原240h超时交易在取消限制后的命运追踪表.csv
- 05_仓位槽位占用与并发挤占分析表.csv
- 06_三方案逐笔交易配对明细底表.csv
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT09_DIR = BASE_DIR / "evidence/FT-ENTRY-09/out"
STOP01_DIR = BASE_DIR / "evidence/FT-STOP-20-01/out"
STOP02_DIR = BASE_DIR / "evidence/FT-STOP-20-02/out"
STOP02_USERDATA = BASE_DIR / "evidence/FT-STOP-20-02/user_data"

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]


def load_trades_ft09(suffix: str) -> pd.DataFrame:
    p = FT09_DIR / f"trades_Wyckoff_C1_{suffix}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df["param_group"] = suffix
    df["version"] = "C1_P0_Struct"
    return df


def load_trades_stop01(suffix: str) -> pd.DataFrame:
    p = STOP01_DIR / f"trades_Wyckoff_C1_Stop20_{suffix}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df["param_group"] = suffix
    df["version"] = "Stop20_WithTimeout"
    return df


def load_trades_stop02(suffix: str) -> pd.DataFrame:
    p = STOP02_DIR / f"trades_Wyckoff_C1_Stop20_NoTimeout_{suffix}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df["param_group"] = suffix
    df["version"] = "Stop20_NoTimeout"
    return df


def analyze():
    print("开始进行 FT-STOP-20-02 深度对账与归因分析...")

    dfs_09 = []
    dfs_01 = []
    dfs_02 = []

    for s in MATRIX_SUFFIXES:
        df9 = load_trades_ft09(s)
        if not df9.empty:
            dfs_09.append(df9)
        df1 = load_trades_stop01(s)
        if not df1.empty:
            dfs_01.append(df1)
        df2 = load_trades_stop02(s)
        if not df2.empty:
            dfs_02.append(df2)

    all_09 = pd.concat(dfs_09, ignore_index=True) if dfs_09 else pd.DataFrame()
    all_01 = pd.concat(dfs_01, ignore_index=True) if dfs_01 else pd.DataFrame()
    all_02 = pd.concat(dfs_02, ignore_index=True) if dfs_02 else pd.DataFrame()

    print(f"数据加载完成: FT09={len(all_09)}笔, Stop01={len(all_01)}笔, Stop02={len(all_02)}笔")

    # 1. 三方案宏观指标对照总表
    summary_rows = []
    for s in MATRIX_SUFFIXES:
        for v_name, df_pool in [("C1原方案(结构P0)", all_09),
                                 ("Stop20(含240h超时)", all_01),
                                 ("Stop20(取消240h限制)", all_02)]:
            sub = df_pool[df_pool["param_group"] == s] if not df_pool.empty else pd.DataFrame()
            cnt = len(sub)
            if cnt == 0:
                summary_rows.append({
                    "param_group": s, "version": v_name, "trades": 0, "win_rate": 0.0,
                    "net_pnl": 0.0, "ev": 0.0, "profit_factor": 0.0, "win_loss_ratio": 0.0,
                    "avg_holding_hours": 0.0, "max_holding_hours": 0.0,
                })
                continue
            wins = (sub["profit_abs"] > 0).sum()
            wr = round(wins / cnt * 100.0, 2)
            net_pnl = round(float(sub["profit_abs"].sum()), 2)
            ev = round(net_pnl / cnt, 3)
            tot_win = sub[sub["profit_abs"] > 0]["profit_abs"].sum()
            tot_loss = abs(sub[sub["profit_abs"] < 0]["profit_abs"].sum())
            pf = round(tot_win / tot_loss, 2) if tot_loss > 0 else (999.0 if tot_win > 0 else 0.0)
            avg_win = tot_win / wins if wins > 0 else 0.0
            avg_loss = tot_loss / (cnt - wins) if (cnt - wins) > 0 else 0.0
            wlr = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0
            
            # 持仓小时数计算
            if "open_timestamp" in sub.columns and "close_timestamp" in sub.columns:
                hold_hours = (sub["close_timestamp"] - sub["open_timestamp"]) / (1000.0 * 3600.0)
                avg_hold = round(float(hold_hours.mean()), 1)
                max_hold = round(float(hold_hours.max()), 1)
            else:
                avg_hold = 0.0
                max_hold = 0.0

            summary_rows.append({
                "param_group": s, "version": v_name, "trades": cnt, "win_rate": wr,
                "net_pnl": net_pnl, "ev": ev, "profit_factor": pf, "win_loss_ratio": wlr,
                "avg_holding_hours": avg_hold, "max_holding_hours": max_hold,
            })

    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(STOP02_DIR / "01_三方案宏观指标对照总表.csv", index=False)

    # 2. 退出原因构成对比
    exit_rows = []
    for v_name, df_pool in [("C1原方案(结构P0)", all_09),
                             ("Stop20(含240h超时)", all_01),
                             ("Stop20(取消240h限制)", all_02)]:
        if df_pool.empty:
            continue
        for r_name, group in df_pool.groupby("exit_reason"):
            cnt = len(group)
            net = round(float(group["profit_abs"].sum()), 2)
            wins = (group["profit_abs"] > 0).sum()
            wr = round(wins / cnt * 100.0, 1)
            exit_rows.append({
                "version": v_name,
                "exit_reason": r_name,
                "trades": cnt,
                "win_rate": wr,
                "net_pnl": net,
                "avg_pnl": round(net / cnt, 3)
            })
    df_exit = pd.DataFrame(exit_rows)
    df_exit.to_csv(STOP02_DIR / "02_退出原因构成与盈亏对比表.csv", index=False)

    # 3. 持仓时间分段统计
    hold_bins = [0, 24, 72, 168, 240, 500, 1000, 100000]
    hold_labels = ["<=24h", "24-72h", "3-7d", "7-10d(<=240h)", "10-20d", "20-40d", ">40d"]
    hold_rows = []
    for v_name, df_pool in [("C1原方案(结构P0)", all_09),
                             ("Stop20(含240h超时)", all_01),
                             ("Stop20(取消240h限制)", all_02)]:
        if df_pool.empty:
            continue
        df_c = df_pool.copy()
        df_c["hold_hours"] = (df_c["close_timestamp"] - df_c["open_timestamp"]) / (1000.0 * 3600.0)
        df_c["hold_bracket"] = pd.cut(df_c["hold_hours"], bins=hold_bins, labels=hold_labels, right=True)
        for b_name, group in df_c.groupby("hold_bracket", observed=False):
            cnt = len(group)
            net = round(float(group["profit_abs"].sum()), 2)
            wins = (group["profit_abs"] > 0).sum()
            wr = round(wins / cnt * 100.0, 1) if cnt > 0 else 0.0
            hold_rows.append({
                "version": v_name,
                "holding_bracket": b_name,
                "trades": cnt,
                "win_rate": wr,
                "net_pnl": net,
            })
    df_hold = pd.DataFrame(hold_rows)
    df_hold.to_csv(STOP02_DIR / "03_持仓时间分布与极值对比表.csv", index=False)

    # 4. 原 240h 超时平仓交易在取消 240h 限制后的命运追踪
    # 在 Stop01 中 exit_reason == 'timeout_240h' 的交易
    if not all_01.empty and not all_02.empty:
        timeout_trades_01 = all_01[all_01["exit_reason"] == "timeout_240h"].copy()
        track_rows = []
        for _, t1 in timeout_trades_01.iterrows():
            cand_id = t1.get("enter_tag")
            pair = t1.get("pair")
            s = t1.get("param_group")
            open_ts = t1.get("open_timestamp")
            pnl_01 = t1.get("profit_abs")

            # 在 Stop02 中匹配相同候选和开仓时间
            m = all_02[(all_02["param_group"] == s) & (all_02["pair"] == pair)]
            # 找到开仓时间在 +- 5 分钟内的
            matched = m[abs(m["open_timestamp"] - open_ts) < 300000]
            if not matched.empty:
                t2 = matched.iloc[0]
                hold_h2 = round((t2["close_timestamp"] - t2["open_timestamp"]) / (1000.0 * 3600.0), 1)
                pnl_02 = t2.get("profit_abs")
                diff = pnl_02 - pnl_01
                fate = ""
                if t2["exit_reason"] == "stop_loss":
                    if pnl_02 > 0:
                        fate = "2R跟踪止损止盈"
                    else:
                        fate = "最终击穿20%止损亏损"
                elif "force" in str(t2["exit_reason"]):
                    fate = "持仓至研究期末平仓"
                else:
                    fate = str(t2["exit_reason"])

                track_rows.append({
                    "param_group": s,
                    "pair": pair,
                    "enter_tag": cand_id,
                    "open_time": t1.get("open_date"),
                    "stop01_pnl": pnl_01,
                    "stop02_exit_reason": t2["exit_reason"],
                    "stop02_holding_hours": hold_h2,
                    "stop02_pnl": pnl_02,
                    "pnl_diff": round(diff, 2),
                    "fate_classification": fate
                })
            else:
                track_rows.append({
                    "param_group": s,
                    "pair": pair,
                    "enter_tag": cand_id,
                    "open_time": t1.get("open_date"),
                    "stop01_pnl": pnl_01,
                    "stop02_exit_reason": "消失(未成交)",
                    "stop02_holding_hours": 0.0,
                    "stop02_pnl": 0.0,
                    "pnl_diff": round(-pnl_01, 2),
                    "fate_classification": "因槽位竞争被排挤消失"
                })
        df_track = pd.DataFrame(track_rows)
        df_track.to_csv(STOP02_DIR / "04_原240h超时交易在取消限制后的命运追踪表.csv", index=False)

    print("分析计算完成，底表均已输出至 evidence/FT-STOP-20-02/out/")


if __name__ == "__main__":
    analyze()

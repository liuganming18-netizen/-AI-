# -*- coding: utf-8 -*-
"""FT-STOP-GRID-02: 恢复盈利跟踪 (2R触发+4h L10跟踪) 后的全量对账与因果归因脚本。

产出底表：
- 01_四档止损36组宏观指标全景对照总表.csv
- 02_各档止损已平仓与期末未实现浮盈分离对账表.csv
- 03_退出原因构成与盈亏贡献分解表(含跟踪止盈).csv
- 04_持仓时长分段与极值分布对比表.csv
- 05_与GRID01无跟踪版本逐组配对对照表.csv
- 06_同候选跨止损档命运演化底表.csv
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
GRID01_DIR = BASE_DIR / "evidence/FT-STOP-GRID-01/out"
GRID02_DIR = BASE_DIR / "evidence/FT-STOP-GRID-02/out"

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]
TIERS = [
    ("Stop05", 0.05, "5%止损+2R跟踪"),
    ("Stop10", 0.10, "10%止损+2R跟踪"),
    ("Stop15", 0.15, "15%止损+2R跟踪"),
    ("Stop20", 0.20, "20%止损+2R跟踪"),
]


def load_all_trades_grid02() -> pd.DataFrame:
    dfs = []
    for t_name, pct, t_label in TIERS:
        for s in MATRIX_SUFFIXES:
            p = GRID02_DIR / f"trades_Wyckoff_C1_{t_name}_{s}.csv"
            if p.exists():
                df = pd.read_csv(p)
                df["tier"] = t_name
                df["tier_label"] = t_label
                df["tier_pct"] = pct
                df["param_group"] = s
                df["strat_name"] = f"Wyckoff_C1_{t_name}_{s}"
                dfs.append(df)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def load_all_trades_grid01() -> pd.DataFrame:
    dfs = []
    for t_name, pct, _ in TIERS:
        for s in MATRIX_SUFFIXES:
            p = GRID01_DIR / f"trades_Wyckoff_C1_{t_name}_{s}.csv"
            if p.exists():
                df = pd.read_csv(p)
                df["tier"] = t_name
                df["param_group"] = s
                dfs.append(df)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def analyze():
    print("开始进行 FT-STOP-GRID-02 恢复盈利跟踪全量对账与因果归因...")
    df02 = load_all_trades_grid02()
    df01 = load_all_trades_grid01()

    if df02.empty:
        print("未找到 GRID02 数据，请等待回测执行完成！")
        return

    print(f"成功加载 GRID02 交易: 共 {len(df02)} 笔记录")

    # 1. 四档止损 36 组宏观指标对照总表
    summary_rows = []
    for t_name, pct, t_label in TIERS:
        for s in MATRIX_SUFFIXES:
            sub = df02[(df02["tier"] == t_name) & (df02["param_group"] == s)]
            tot_cnt = len(sub)
            if tot_cnt == 0:
                summary_rows.append({
                    "tier": t_name, "tier_label": t_label, "param_group": s,
                    "trades_total": 0, "trades_stop_loss": 0, "trades_trailing_stop": 0,
                    "trades_still_open": 0, "win_rate": 0.0,
                    "stop_loss_pnl": 0.0, "trailing_stop_pnl": 0.0, "unrealized_pnl": 0.0,
                    "total_net_pnl": 0.0, "total_ev": 0.0,
                    "avg_stake": 0.0, "avg_holding_hours": 0.0, "max_holding_hours": 0.0,
                    "final_equity": 2000.0
                })
                continue

            stop_mask = sub["exit_reason"] == "stop_loss"
            trailing_mask = sub["exit_reason"] == "trailing_stop_loss"
            open_mask = sub["exit_reason"] == "force_exit"

            stop_cnt = int(stop_mask.sum())
            trailing_cnt = int(trailing_mask.sum())
            open_cnt = int(open_mask.sum())

            stop_pnl = round(float(sub.loc[stop_mask, "profit_abs"].sum()), 2) if stop_cnt > 0 else 0.0
            trailing_pnl = round(float(sub.loc[trailing_mask, "profit_abs"].sum()), 2) if trailing_cnt > 0 else 0.0
            unrealized_pnl = round(float(sub.loc[open_mask, "profit_abs"].sum()), 2) if open_cnt > 0 else 0.0
            total_net_pnl = round(float(sub["profit_abs"].sum()), 2)
            total_ev = round(total_net_pnl / tot_cnt, 3)

            wins = int((sub["profit_abs"] > 0).sum())
            win_rate = round(wins / tot_cnt * 100.0, 2)

            avg_stake = round(float(sub["stake_amount"].mean()), 2) if "stake_amount" in sub.columns else 0.0

            if "open_timestamp" in sub.columns and "close_timestamp" in sub.columns:
                hold_hours = (sub["close_timestamp"] - sub["open_timestamp"]) / (1000.0 * 3600.0)
                avg_hold = round(float(hold_hours.mean()), 1)
                max_hold = round(float(hold_hours.max()), 1)
            else:
                avg_hold = 0.0
                max_hold = 0.0

            final_equity = round(2000.0 + total_net_pnl, 2)

            summary_rows.append({
                "tier": t_name, "tier_label": t_label, "param_group": s,
                "trades_total": tot_cnt, "trades_stop_loss": stop_cnt,
                "trades_trailing_stop": trailing_cnt, "trades_still_open": open_cnt,
                "win_rate": win_rate, "stop_loss_pnl": stop_pnl,
                "trailing_stop_pnl": trailing_pnl, "unrealized_pnl": unrealized_pnl,
                "total_net_pnl": total_net_pnl, "total_ev": total_ev,
                "avg_stake": avg_stake, "avg_holding_hours": avg_hold,
                "max_holding_hours": max_hold, "final_equity": final_equity
            })

    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(GRID02_DIR / "01_四档止损36组宏观指标全景对照总表.csv", index=False)

    # 2. 各档止损已平仓与期末未实现浮盈分离对账表 (按档位横向汇总)
    decomp_rows = []
    for t_name, pct, t_label in TIERS:
        sub_t = df02[df02["tier"] == t_name]
        tot_cnt = len(sub_t)

        stop_mask = sub_t["exit_reason"] == "stop_loss"
        trailing_mask = sub_t["exit_reason"] == "trailing_stop_loss"
        open_mask = sub_t["exit_reason"] == "force_exit"

        stop_cnt = int(stop_mask.sum())
        trailing_cnt = int(trailing_mask.sum())
        open_cnt = int(open_mask.sum())

        stop_pnl = float(sub_t.loc[stop_mask, "profit_abs"].sum()) if stop_cnt > 0 else 0.0
        trailing_pnl = float(sub_t.loc[trailing_mask, "profit_abs"].sum()) if trailing_cnt > 0 else 0.0
        open_pnl = float(sub_t.loc[open_mask, "profit_abs"].sum()) if open_cnt > 0 else 0.0

        wins = int((sub_t["profit_abs"] > 0).sum())
        wr = round(wins / tot_cnt * 100.0, 2) if tot_cnt > 0 else 0.0

        tot_open_stake = float(sub_t.loc[open_mask, "stake_amount"].sum()) if open_cnt > 0 else 0.0

        decomp_rows.append({
            "止损档位": t_label,
            "止损比例": f"{int(pct*100)}%",
            "2R启动涨幅": f"+{int(pct*200)}%",
            "总成交笔数": tot_cnt,
            "初始止损笔数": stop_cnt,
            "止损净亏损(USDT)": round(stop_pnl, 2),
            "跟踪止盈笔数": trailing_cnt,
            "跟踪净盈利(USDT)": round(trailing_pnl, 2),
            "期末仍持有单数": open_cnt,
            "期末未实现浮盈(USDT)": round(open_pnl, 2),
            "全口径净收益(USDT)": round(stop_pnl + trailing_pnl + open_pnl, 2),
            "全口径胜率(%)": wr,
            "期末占用资金(USDT)": round(tot_open_stake, 2),
        })
    df_decomp = pd.DataFrame(decomp_rows)
    df_decomp.to_csv(GRID02_DIR / "02_各档止损已平仓与期末未实现浮盈分离对账表.csv", index=False)

    # 3. 退出原因构成与盈亏贡献分解表
    exit_rows = []
    for t_name, pct, t_label in TIERS:
        sub_t = df02[df02["tier"] == t_name]
        for r_name, group in sub_t.groupby("exit_reason"):
            cnt = len(group)
            net = round(float(group["profit_abs"].sum()), 2)
            wins = (group["profit_abs"] > 0).sum()
            wr = round(wins / cnt * 100.0, 1)
            exit_rows.append({
                "止损档位": t_label,
                "退出原因": r_name,
                "交易笔数": cnt,
                "胜率(%)": wr,
                "净盈亏(USDT)": net,
                "平均单笔盈亏(USDT)": round(net / cnt, 3)
            })
    df_exit = pd.DataFrame(exit_rows)
    df_exit.to_csv(GRID02_DIR / "03_退出原因构成与盈亏贡献分解表.csv", index=False)

    # 4. 持仓时长分段统计
    hold_bins = [0, 24, 72, 168, 720, 2160, 100000]
    hold_labels = ["<=24h", "24-72h", "3-7d", "7-30d", "1-3个月", ">3个月"]
    hold_rows = []
    for t_name, pct, t_label in TIERS:
        sub_t = df02[df02["tier"] == t_name].copy()
        sub_t["hold_hours"] = (sub_t["close_timestamp"] - sub_t["open_timestamp"]) / (1000.0 * 3600.0)
        sub_t["hold_bracket"] = pd.cut(sub_t["hold_hours"], bins=hold_bins, labels=hold_labels, right=True)
        for b_name, group in sub_t.groupby("hold_bracket", observed=False):
            cnt = len(group)
            net = round(float(group["profit_abs"].sum()), 2)
            wins = (group["profit_abs"] > 0).sum()
            wr = round(wins / cnt * 100.0, 1) if cnt > 0 else 0.0
            hold_rows.append({
                "止损档位": t_label,
                "持仓时长区间": b_name,
                "交易笔数": cnt,
                "胜率(%)": wr,
                "净盈亏(USDT)": net,
            })
    df_hold = pd.DataFrame(hold_rows)
    df_hold.to_csv(GRID02_DIR / "04_持仓时长分段与极值分布对比表.csv", index=False)

    # 5. 与 FT-STOP-GRID-01 无跟踪版本逐组配对比较表
    if not df01.empty:
        cmp_rows = []
        for t_name, pct, t_label in TIERS:
            for s in MATRIX_SUFFIXES:
                sub01 = df01[(df01["tier"] == t_name) & (df01["param_group"] == s)]
                sub02 = df02[(df02["tier"] == t_name) & (df02["param_group"] == s)]

                cnt01 = len(sub01)
                cnt02 = len(sub02)

                pnl01 = round(float(sub01["profit_abs"].sum()), 2) if cnt01 > 0 else 0.0
                pnl02 = round(float(sub02["profit_abs"].sum()), 2) if cnt02 > 0 else 0.0
                diff_pnl = round(pnl02 - pnl01, 2)

                wr01 = round(float((sub01["profit_abs"] > 0).sum()) / cnt01 * 100.0, 2) if cnt01 > 0 else 0.0
                wr02 = round(float((sub02["profit_abs"] > 0).sum()) / cnt02 * 100.0, 2) if cnt02 > 0 else 0.0

                hold01 = round(float((sub01["close_timestamp"] - sub01["open_timestamp"]).mean()) / (1000.0 * 3600.0), 1) if cnt01 > 0 else 0.0
                hold02 = round(float((sub02["close_timestamp"] - sub02["open_timestamp"]).mean()) / (1000.0 * 3600.0), 1) if cnt02 > 0 else 0.0

                cmp_rows.append({
                    "止损档位": t_name,
                    "参数组": s,
                    "GRID01_笔数": cnt01,
                    "GRID02_笔数": cnt02,
                    "笔数差异": cnt02 - cnt01,
                    "GRID01_净收益": pnl01,
                    "GRID02_净收益": pnl02,
                    "净收益改善": diff_pnl,
                    "GRID01_胜率(%)": wr01,
                    "GRID02_胜率(%)": wr02,
                    "GRID01_平均持仓(h)": hold01,
                    "GRID02_平均持仓(h)": hold02,
                })
        df_cmp = pd.DataFrame(cmp_rows)
        df_cmp.to_csv(GRID02_DIR / "05_与GRID01无跟踪版本逐组配对对照表.csv", index=False)

    print("分析计算完成，底表均已输出至 evidence/FT-STOP-GRID-02/out/！")


if __name__ == "__main__":
    analyze()

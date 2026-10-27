# -*- coding: utf-8 -*-
"""FT-STOP-GRID-01: 四档固定止损 (5%, 10%, 15%, 20%) 深度因果分析与全量对账脚本。

产出底表：
- 01_四档止损36组宏观指标全景对照总表.csv
- 02_各档止损已平仓与期末未实现浮盈分离对账表.csv
- 03_仓位规模_初始风险与槽位占用对比表.csv
- 04_持仓时长分段与极值分布对比表.csv
- 05_期末持仓盈利集中度与真实损益穿透表.csv
- 06_同候选跨止损档命运演化底表.csv
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP_DIR = BASE_DIR / "evidence/FT-STOP-GRID-01"
OUT_DIR = EXP_DIR / "out"

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]
TIERS = [
    ("Stop05", 0.05, "5%止损"),
    ("Stop10", 0.10, "10%止损"),
    ("Stop15", 0.15, "15%止损"),
    ("Stop20", 0.20, "20%止损"),
]


def load_all_trades() -> pd.DataFrame:
    dfs = []
    for t_name, pct, t_label in TIERS:
        for s in MATRIX_SUFFIXES:
            p = OUT_DIR / f"trades_Wyckoff_C1_{t_name}_{s}.csv"
            if p.exists():
                df = pd.read_csv(p)
                df["tier"] = t_name
                df["tier_label"] = t_label
                df["tier_pct"] = pct
                df["param_group"] = s
                df["strat_name"] = f"Wyckoff_C1_{t_name}_{s}"
                dfs.append(df)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def analyze():
    print("开始进行 FT-STOP-GRID-01 四档止损全量对账与因果归因...")
    all_df = load_all_trades()
    if all_df.empty:
        print("未找到 trades 数据，请等待回测执行完成！")
        return

    print(f"成功加载全部交易: 共 {len(all_df)} 笔记录")

    # 1. 四档止损 36 组宏观指标对照总表
    summary_rows = []
    for t_name, pct, t_label in TIERS:
        for s in MATRIX_SUFFIXES:
            sub = all_df[(all_df["tier"] == t_name) & (all_df["param_group"] == s)]
            tot_cnt = len(sub)
            if tot_cnt == 0:
                summary_rows.append({
                    "tier": t_name, "tier_label": t_label, "param_group": s,
                    "trades_total": 0, "trades_closed": 0, "trades_still_open": 0,
                    "closed_win_rate": 0.0, "closed_net_pnl": 0.0,
                    "unrealized_pnl": 0.0, "total_net_pnl": 0.0, "total_ev": 0.0,
                    "avg_stake": 0.0, "avg_planned_risk": 0.0,
                    "avg_holding_hours": 0.0, "max_holding_hours": 0.0, "final_equity": 2000.0
                })
                continue

            is_open = sub["exit_reason"] == "force_exit"
            is_closed = ~is_open

            closed_cnt = int(is_closed.sum())
            open_cnt = int(is_open.sum())

            closed_wins = int((sub.loc[is_closed, "profit_abs"] > 0).sum()) if closed_cnt > 0 else 0
            closed_wr = round(closed_wins / closed_cnt * 100.0, 2) if closed_cnt > 0 else 0.0
            closed_pnl = round(float(sub.loc[is_closed, "profit_abs"].sum()), 2) if closed_cnt > 0 else 0.0

            unrealized_pnl = round(float(sub.loc[is_open, "profit_abs"].sum()), 2) if open_cnt > 0 else 0.0
            total_net_pnl = round(float(sub["profit_abs"].sum()), 2)
            total_ev = round(total_net_pnl / tot_cnt, 3)

            avg_stake = round(float(sub["stake_amount"].mean()), 2) if "stake_amount" in sub.columns else 0.0
            avg_risk = round(avg_stake * pct, 2)

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
                "trades_total": tot_cnt, "trades_closed": closed_cnt, "trades_still_open": open_cnt,
                "closed_win_rate": closed_wr, "closed_net_pnl": closed_pnl,
                "unrealized_pnl": unrealized_pnl, "total_net_pnl": total_net_pnl, "total_ev": total_ev,
                "avg_stake": avg_stake, "avg_planned_risk": avg_risk,
                "avg_holding_hours": avg_hold, "max_holding_hours": max_hold, "final_equity": final_equity
            })

    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(OUT_DIR / "01_四档止损36组宏观指标全景对照总表.csv", index=False)

    # 2. 各档止损已平仓与期末未实现浮盈分离对账表 (按档位横向汇总)
    decomp_rows = []
    for t_name, pct, t_label in TIERS:
        sub_t = all_df[all_df["tier"] == t_name]
        tot_cnt = len(sub_t)
        closed_sub = sub_t[sub_t["exit_reason"] != "force_exit"]
        open_sub = sub_t[sub_t["exit_reason"] == "force_exit"]

        c_cnt = len(closed_sub)
        o_cnt = len(open_sub)

        c_pnl = float(closed_sub["profit_abs"].sum()) if c_cnt > 0 else 0.0
        o_pnl = float(open_sub["profit_abs"].sum()) if o_cnt > 0 else 0.0

        c_wins = int((closed_sub["profit_abs"] > 0).sum()) if c_cnt > 0 else 0
        o_wins = int((open_sub["profit_abs"] > 0).sum()) if o_cnt > 0 else 0

        avg_stake = float(sub_t["stake_amount"].mean()) if "stake_amount" in sub_t.columns else 0.0
        tot_open_stake = float(open_sub["stake_amount"].sum()) if o_cnt > 0 and "stake_amount" in open_sub.columns else 0.0

        decomp_rows.append({
            "止损档位": t_label,
            "止损比例": f"{int(pct*100)}%",
            "名义仓位占比": f"{round(0.005/pct*100, 1)}%",
            "平均名义开仓金额(USDT)": round(avg_stake, 2),
            "总成交笔数": tot_cnt,
            "已触止损平仓笔数": c_cnt,
            "已触止损胜率(%)": round(c_wins / c_cnt * 100.0, 2) if c_cnt > 0 else 0.0,
            "已实现净亏损(USDT)": round(c_pnl, 2),
            "期末仍持有单数": o_cnt,
            "期末仍持有浮盈占比(%)": round(o_wins / o_cnt * 100.0, 2) if o_cnt > 0 else 0.0,
            "期末未实现浮盈(USDT)": round(o_pnl, 2),
            "期末占用资金(USDT)": round(tot_open_stake, 2),
            "全口径净收益(USDT)": round(c_pnl + o_pnl, 2),
        })
    df_decomp = pd.DataFrame(decomp_rows)
    df_decomp.to_csv(OUT_DIR / "02_各档止损已平仓与期末未实现浮盈分离对账表.csv", index=False)

    # 3. 仓位规模、初始风险与持仓时长表
    risk_rows = []
    for t_name, pct, t_label in TIERS:
        sub_t = all_df[all_df["tier"] == t_name].copy()
        sub_t["hold_hours"] = (sub_t["close_timestamp"] - sub_t["open_timestamp"]) / (1000.0 * 3600.0)
        risk_rows.append({
            "止损档位": t_label,
            "理论名义仓位": f"{round(0.005/pct*100, 1)}%",
            "平均开仓金额(USDT)": round(float(sub_t["stake_amount"].mean()), 2),
            "平均实际单笔风险(USDT)": round(float(sub_t["stake_amount"].mean()) * pct, 2),
            "平均持仓时长(小时)": round(float(sub_t["hold_hours"].mean()), 1),
            "中位数持仓时长(小时)": round(float(sub_t["hold_hours"].median()), 1),
            "最长持仓时长(小时)": round(float(sub_t["hold_hours"].max()), 1),
            "最长持仓时长(天)": round(float(sub_t["hold_hours"].max()) / 24.0, 1),
        })
    df_risk = pd.DataFrame(risk_rows)
    df_risk.to_csv(OUT_DIR / "03_仓位规模_初始风险与槽位占用对比表.csv", index=False)

    # 4. 持仓时长分段统计
    hold_bins = [0, 24, 72, 168, 720, 2160, 100000]
    hold_labels = ["<=24h", "24-72h", "3-7d", "7-30d", "1-3个月", ">3个月"]
    hold_rows = []
    for t_name, pct, t_label in TIERS:
        sub_t = all_df[all_df["tier"] == t_name].copy()
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
    df_hold.to_csv(OUT_DIR / "04_持仓时长分段与极值分布对比表.csv", index=False)

    # 5. 期末持仓盈利集中度与穿透分析表
    conc_rows = []
    for t_name, pct, t_label in TIERS:
        sub_t = all_df[all_df["tier"] == t_name]
        open_sub = sub_t[sub_t["exit_reason"] == "force_exit"].sort_values(by="profit_abs", ascending=False)
        tot_open_pnl = float(open_sub["profit_abs"].sum()) if not open_sub.empty else 0.0
        top1_pnl = float(open_sub["profit_abs"].iloc[0]) if len(open_sub) >= 1 else 0.0
        top3_pnl = float(open_sub["profit_abs"].iloc[:3].sum()) if len(open_sub) >= 3 else top1_pnl
        top1_share = round(top1_pnl / tot_open_pnl * 100.0, 1) if tot_open_pnl > 0 else 0.0
        top3_share = round(top3_pnl / tot_open_pnl * 100.0, 1) if tot_open_pnl > 0 else 0.0

        conc_rows.append({
            "止损档位": t_label,
            "期末仍持有单数": len(open_sub),
            "期末总未实现浮盈(USDT)": round(tot_open_pnl, 2),
            "第一大浮盈交易盈利(USDT)": round(top1_pnl, 2),
            "Top1浮盈占比(%)": top1_share,
            "前三大浮盈合计盈利(USDT)": round(top3_pnl, 2),
            "Top3浮盈占比(%)": top3_share,
            "已实现净亏损(USDT)": round(float(sub_t[sub_t["exit_reason"] != "force_exit"]["profit_abs"].sum()), 2),
            "少数浮盈掩盖亏损倍数": round(tot_open_pnl / abs(float(sub_t[sub_t["exit_reason"] != "force_exit"]["profit_abs"].sum())), 2) if float(sub_t[sub_t["exit_reason"] != "force_exit"]["profit_abs"].sum()) != 0 else 0.0
        })
    df_conc = pd.DataFrame(conc_rows)
    df_conc.to_csv(OUT_DIR / "05_期末持仓盈利集中度与真实损益穿透表.csv", index=False)

    # 6. 同候选跨止损档命运演化底表
    # 以入场信号 enter_tag + pair 为唯一标识
    cands_p20 = all_df[all_df["tier"] == "Stop20"][["enter_tag", "pair", "param_group", "profit_abs", "exit_reason"]].copy()
    cands_p20.rename(columns={"profit_abs": "pnl_stop20", "exit_reason": "fate_stop20"}, inplace=True)

    cands_p15 = all_df[all_df["tier"] == "Stop15"][["enter_tag", "pair", "param_group", "profit_abs", "exit_reason"]].copy()
    cands_p15.rename(columns={"profit_abs": "pnl_stop15", "exit_reason": "fate_stop15"}, inplace=True)

    cands_p10 = all_df[all_df["tier"] == "Stop10"][["enter_tag", "pair", "param_group", "profit_abs", "exit_reason"]].copy()
    cands_p10.rename(columns={"profit_abs": "pnl_stop10", "exit_reason": "fate_stop10"}, inplace=True)

    cands_p05 = all_df[all_df["tier"] == "Stop05"][["enter_tag", "pair", "param_group", "profit_abs", "exit_reason"]].copy()
    cands_p05.rename(columns={"profit_abs": "pnl_stop05", "exit_reason": "fate_stop05"}, inplace=True)

    merged = pd.merge(cands_p20, cands_p15, on=["enter_tag", "pair", "param_group"], how="outer")
    merged = pd.merge(merged, cands_p10, on=["enter_tag", "pair", "param_group"], how="outer")
    merged = pd.merge(merged, cands_p05, on=["enter_tag", "pair", "param_group"], how="outer")

    merged.to_csv(OUT_DIR / "06_同候选跨止损档命运演化底表.csv", index=False)
    print("全部分析计算完成，底表均已输出至 evidence/FT-STOP-GRID-01/out/！")


if __name__ == "__main__":
    analyze()

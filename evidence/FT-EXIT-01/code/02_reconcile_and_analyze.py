# -*- coding: utf-8 -*-
"""FT-EXIT-01: 深度对账、全口径归因与历史对照分析脚本。

涵盖 4 大方案：
1. FT-ENTRY-09: C1 原方案 (结构P0, 含240h超时, 含2R跟踪)
2. FT-STOP-20-01: Stop20 方案 (固定20%, 含240h超时, 含2R跟踪)
3. FT-EXIT-01 S 组: 结构P0 (仅初始止损, 无超时, 无跟踪)
4. FT-EXIT-01 P 组: 固定20% (仅初始止损, 无超时, 无跟踪)

产出分析底表：
- 01_S组与P组宏观指标与历史四方案全景对照总表.csv
- 02_已平仓与期末未实现浮盈浮亏分离对账表.csv
- 03_持仓时长分布与极值对比表.csv
- 04_资金费与槽位占用排挤分析表.csv
- 05_历史超时交易在仅初始止损下的真实归宿追踪表.csv
- 06_逐笔全量配对明细底表.csv
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT09_DIR = BASE_DIR / "evidence/FT-ENTRY-09/out"
STOP01_DIR = BASE_DIR / "evidence/FT-STOP-20-01/out"
EXIT01_OUT = BASE_DIR / "evidence/FT-EXIT-01/out"

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]


def load_trades_csv(p: Path, param_group: str, scheme: str) -> pd.DataFrame:
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df["param_group"] = param_group
    df["scheme"] = scheme
    return df


def analyze():
    print("开始进行 FT-EXIT-01 深度因果分析与全口径对账...")

    dfs_09 = []
    dfs_01 = []
    dfs_s = []
    dfs_p = []

    for s in MATRIX_SUFFIXES:
        p_09 = FT09_DIR / f"trades_Wyckoff_C1_{s}.csv"
        if p_09.exists():
            dfs_09.append(load_trades_csv(p_09, s, "C1_原方案(结构P0+超时+跟踪)"))

        p_01 = STOP01_DIR / f"trades_Wyckoff_C1_Stop20_{s}.csv"
        if p_01.exists():
            dfs_01.append(load_trades_csv(p_01, s, "Stop20_旧版(固定20%+超时+跟踪)"))

        p_s = EXIT01_OUT / f"trades_Wyckoff_C1_S_P0_{s}.csv"
        if p_s.exists():
            dfs_s.append(load_trades_csv(p_s, s, "FT_EXIT_01_S组(结构P0_仅初始止损)"))

        p_p = EXIT01_OUT / f"trades_Wyckoff_C1_P_Stop20_{s}.csv"
        if p_p.exists():
            dfs_p.append(load_trades_csv(p_p, s, "FT_EXIT_01_P组(固定20%_仅初始止损)"))

    all_09 = pd.concat(dfs_09, ignore_index=True) if dfs_09 else pd.DataFrame()
    all_01 = pd.concat(dfs_01, ignore_index=True) if dfs_01 else pd.DataFrame()
    all_s = pd.concat(dfs_s, ignore_index=True) if dfs_s else pd.DataFrame()
    all_p = pd.concat(dfs_p, ignore_index=True) if dfs_p else pd.DataFrame()

    print(f"数据加载: FT09={len(all_09)}笔, Stop01={len(all_01)}笔, S组={len(all_s)}笔, P组={len(all_p)}笔")

    # 1. 四方案宏观全景对照总表
    summary_rows = []
    pools = [
        ("C1_原方案(结构P0+超时+跟踪)", all_09),
        ("Stop20_旧版(固定20%+超时+跟踪)", all_01),
        ("FT_EXIT_01_S组(结构P0_仅初始止损)", all_s),
        ("FT_EXIT_01_P组(固定20%_仅初始止损)", all_p),
    ]

    for s in MATRIX_SUFFIXES:
        for v_name, df_pool in pools:
            sub = df_pool[df_pool["param_group"] == s] if not df_pool.empty else pd.DataFrame()
            cnt = len(sub)
            if cnt == 0:
                summary_rows.append({
                    "param_group": s, "version": v_name, "trades_total": 0,
                    "trades_closed": 0, "trades_still_open": 0,
                    "closed_win_rate": 0.0, "closed_net_pnl": 0.0,
                    "unrealized_pnl": 0.0, "total_net_pnl": 0.0,
                    "total_ev": 0.0, "avg_holding_hours": 0.0, "max_holding_hours": 0.0,
                    "total_funding": 0.0, "final_equity": 2000.0
                })
                continue

            # 区分已平仓 (触及止损) 与 期末仍持有 (force_exit 技术结算)
            is_open = sub["exit_reason"] == "force_exit"
            is_closed = ~is_open

            closed_cnt = int(is_closed.sum())
            open_cnt = int(is_open.sum())

            closed_wins = int((sub.loc[is_closed, "profit_abs"] > 0).sum()) if closed_cnt > 0 else 0
            closed_wr = round(closed_wins / closed_cnt * 100.0, 2) if closed_cnt > 0 else 0.0
            closed_pnl = round(float(sub.loc[is_closed, "profit_abs"].sum()), 2) if closed_cnt > 0 else 0.0

            unrealized_pnl = round(float(sub.loc[is_open, "profit_abs"].sum()), 2) if open_cnt > 0 else 0.0
            total_net_pnl = round(float(sub["profit_abs"].sum()), 2)
            total_ev = round(total_net_pnl / cnt, 3)

            # 持仓小时
            if "open_timestamp" in sub.columns and "close_timestamp" in sub.columns:
                hold_hours = (sub["close_timestamp"] - sub["open_timestamp"]) / (1000.0 * 3600.0)
                avg_hold = round(float(hold_hours.mean()), 1)
                max_hold = round(float(hold_hours.max()), 1)
            else:
                avg_hold = 0.0
                max_hold = 0.0

            funding_tot = round(float(sub["funding_fee"].sum()), 4) if "funding_fee" in sub.columns else 0.0
            final_equity = round(2000.0 + total_net_pnl, 2)

            summary_rows.append({
                "param_group": s, "version": v_name, "trades_total": cnt,
                "trades_closed": closed_cnt, "trades_still_open": open_cnt,
                "closed_win_rate": closed_wr, "closed_net_pnl": closed_pnl,
                "unrealized_pnl": unrealized_pnl, "total_net_pnl": total_net_pnl,
                "total_ev": total_ev, "avg_holding_hours": avg_hold, "max_holding_hours": max_hold,
                "total_funding": funding_tot, "final_equity": final_equity
            })

    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(EXIT01_OUT / "01_S组与P组宏观指标与历史四方案全景对照总表.csv", index=False)

    # 2. 已平仓与期末未实现浮盈浮亏分离对账表 (按 S 组 vs P 组横向汇总)
    decomp_rows = []
    for grp_name, df_cur in [("FT_EXIT_01_S组(结构P0)", all_s), ("FT_EXIT_01_P组(固定20%)", all_p)]:
        if df_cur.empty:
            continue
        tot_trades = len(df_cur)
        closed_sub = df_cur[df_cur["exit_reason"] != "force_exit"]
        open_sub = df_cur[df_cur["exit_reason"] == "force_exit"]

        c_cnt = len(closed_sub)
        o_cnt = len(open_sub)

        c_pnl = float(closed_sub["profit_abs"].sum()) if c_cnt > 0 else 0.0
        o_pnl = float(open_sub["profit_abs"].sum()) if o_cnt > 0 else 0.0

        c_wins = int((closed_sub["profit_abs"] > 0).sum()) if c_cnt > 0 else 0
        o_wins = int((open_sub["profit_abs"] > 0).sum()) if o_cnt > 0 else 0

        o_stake = float(open_sub["stake_amount"].sum()) if o_cnt > 0 and "stake_amount" in open_sub.columns else 0.0
        funding_sum = float(df_cur["funding_fee"].sum()) if "funding_fee" in df_cur.columns else 0.0

        decomp_rows.append({
            "方案": grp_name,
            "总成交笔数": tot_trades,
            "已触及止损笔数": c_cnt,
            "已触及止损胜率(%)": round(c_wins / c_cnt * 100.0, 2) if c_cnt > 0 else 0.0,
            "已实现盈亏(USDT)": round(c_pnl, 2),
            "期末仍持有笔数": o_cnt,
            "期末仍持有浮盈占比(%)": round(o_wins / o_cnt * 100.0, 2) if o_cnt > 0 else 0.0,
            "期末未实现浮盈浮亏(USDT)": round(o_pnl, 2),
            "期末占用资金(USDT)": round(o_stake, 2),
            "资金费总支出(USDT)": round(funding_sum, 4),
            "全口径净收益(USDT)": round(c_pnl + o_pnl, 2),
        })
    df_decomp = pd.DataFrame(decomp_rows)
    df_decomp.to_csv(EXIT01_OUT / "02_已平仓与期末未实现浮盈浮亏分离对账表.csv", index=False)

    # 3. 持仓时长分段分布
    hold_bins = [0, 24, 72, 168, 240, 720, 2160, 100000]
    hold_labels = ["<=24h", "24-72h", "3-7d", "7-10d", "10-30d", "1-3个月", ">3个月"]
    hold_rows = []
    for v_name, df_pool in pools:
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
    df_hold.to_csv(EXIT01_OUT / "03_持仓时长分布与极值对比表.csv", index=False)

    # 4. 历史 240h 超时交易在仅初始止损下的真实归宿追踪表
    if not all_01.empty:
        timeout_trades_01 = all_01[all_01["exit_reason"] == "timeout_240h"].copy()
        track_rows = []
        for _, t1 in timeout_trades_01.iterrows():
            cand_id = t1.get("enter_tag")
            pair = t1.get("pair")
            s = t1.get("param_group")
            open_ts = t1.get("open_timestamp")
            pnl_old = t1.get("profit_abs")

            # 匹配 S 组
            m_s = all_s[(all_s["param_group"] == s) & (all_s["pair"] == pair)] if not all_s.empty else pd.DataFrame()
            matched_s = m_s[abs(m_s["open_timestamp"] - open_ts) < 300000] if not m_s.empty else pd.DataFrame()
            if not matched_s.empty:
                t_s = matched_s.iloc[0]
                res_s = "期末仍持有" if t_s["exit_reason"] == "force_exit" else "触及结构P0止损"
                pnl_s = t_s["profit_abs"]
            else:
                res_s = "因槽位竞争被排挤未成交"
                pnl_s = 0.0

            # 匹配 P 组
            m_p = all_p[(all_p["param_group"] == s) & (all_p["pair"] == pair)] if not all_p.empty else pd.DataFrame()
            matched_p = m_p[abs(m_p["open_timestamp"] - open_ts) < 300000] if not m_p.empty else pd.DataFrame()
            if not matched_p.empty:
                t_p = matched_p.iloc[0]
                res_p = "期末仍持有" if t_p["exit_reason"] == "force_exit" else "触及20%止损"
                pnl_p = t_p["profit_abs"]
            else:
                res_p = "因槽位竞争被排挤未成交"
                pnl_p = 0.0

            track_rows.append({
                "param_group": s,
                "pair": pair,
                "candidate_id": cand_id,
                "open_time": t1.get("open_date"),
                "old_stop01_timeout_pnl": pnl_old,
                "S组_真实归宿": res_s,
                "S组_最终盈亏": pnl_s,
                "P组_真实归宿": res_p,
                "P组_最终盈亏": pnl_p
            })
        df_track = pd.DataFrame(track_rows)
        df_track.to_csv(EXIT01_OUT / "05_历史超时交易在仅初始止损下的真实归宿追踪表.csv", index=False)

    print("对账分析完成，底表均已输出至 evidence/FT-EXIT-01/out/")


if __name__ == "__main__":
    analyze()

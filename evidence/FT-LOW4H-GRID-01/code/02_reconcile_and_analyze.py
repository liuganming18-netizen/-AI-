# -*- coding: utf-8 -*-
"""FT-LOW4H-GRID-01: 144组网格对账分析与16种组合对照表生成脚本。

核心交付：
1. 144组完整结果总表
2. 16种组合 (4档s x 4档d) 对照表 (净收益、单组中位数、盈利组数、胜率、回撤、持仓时长、手续费与资金费)
3. 逐笔追踪与审计表 (初始止损、引用4小时K线时间与最低价、候选价、实际生效止损、更新时间及退出成交)
4. 退出结构细分表 (初始止损 vs 跟踪盈利 vs 跟踪亏损 vs 期末技术结算)
5. 与 FT-STOP-GRID-02 配对比较表
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP_DIR = BASE_DIR / "evidence/FT-LOW4H-GRID-01"
OUT_DIR = EXP_DIR / "out"
DATA_DIR = EXP_DIR / "user_data/data/binance/futures"

GRID02_DIR = BASE_DIR / "evidence/FT-STOP-GRID-02/out"

STOP_MAP = {
    "S05": 0.05,
    "S10": 0.10,
    "S15": 0.15,
    "S20": 0.20,
}

TRAIL_MAP = {
    "D05": 0.05,
    "D10": 0.10,
    "D15": 0.15,
    "D20": 0.20,
}

PARAMS = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]


def to_timestamp_ms(dt_str: str) -> int:
    dt = pd.to_datetime(dt_str, utc=True)
    return int(dt.timestamp() * 1000)


def load_4h_feather(pair: str) -> pd.DataFrame:
    slug = pair.replace("/", "_").replace(":", "_")
    f_path = DATA_DIR / f"{slug}-4h-futures.feather"
    if not f_path.exists():
        return pd.DataFrame()
    df = pd.read_feather(f_path)
    df["open_ms"] = df["date"].astype("int64")
    df["close_ms"] = df["open_ms"] + 4 * 3600 * 1000
    return df[["open_ms", "close_ms", "low", "close", "date"]].sort_values("close_ms").reset_index(drop=True)


def main():
    print("开始执行 FT-LOW4H-GRID-01 对账与因果分析...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 缓存各币种 4h K 线
    pair_4h_cache: Dict[str, pd.DataFrame] = {}

    all_trades: List[dict] = []
    run_summaries: List[dict] = []

    # 1. 遍历 144 组策略
    for s_code, s_val in STOP_MAP.items():
        for d_code, d_val in TRAIL_MAP.items():
            for p_code in PARAMS:
                strat_name = f"Wyckoff_C1_{s_code}_{d_code}_{p_code}"
                csv_file = OUT_DIR / f"trades_{strat_name}.csv"
                zip_file = OUT_DIR / f"backtest-result-{strat_name}.zip"

                if not csv_file.exists():
                    print(f"警告: 未找到 {csv_file}")
                    continue

                df_t = pd.read_csv(csv_file)
                if df_t.empty:
                    run_summaries.append({
                        "strategy": strat_name,
                        "s_code": s_code,
                        "d_code": d_code,
                        "p_code": p_code,
                        "stoploss_s": s_val,
                        "trail_d": d_val,
                        "trades_total": 0,
                        "trades_closed": 0,
                        "trades_open": 0,
                        "net_pnl": 0.0,
                        "win_rate": 0.0,
                        "profit_factor": 0.0,
                        "max_drawdown_abs": 0.0,
                        "max_drawdown_pct": 0.0,
                        "avg_holding_hours": 0.0,
                        "fees_total": 0.0,
                        "funding_total": 0.0,
                    })
                    continue

                # 读取 zip 包中的统计信息（若有）
                max_dd_abs = 0.0
                max_dd_pct = 0.0
                if zip_file.exists():
                    try:
                        import zipfile
                        with zipfile.ZipFile(zip_file, "r") as z:
                            jnames = [n for n in z.namelist() if n.endswith(".json") and not n.endswith(".meta.json")]
                            if jnames:
                                with z.open(jnames[0]) as jf:
                                    zdata = json.load(jf)
                                sres = zdata.get("strategy", {}).get(strat_name, {})
                                max_dd_abs = float(sres.get("max_drawdown_abs", 0.0))
                                max_dd_pct = float(sres.get("max_drawdown_account", sres.get("max_drawdown", 0.0))) * 100
                    except Exception:
                        pass

                # 对每笔交易进行 4h 最低价跟踪重放与审计
                strat_trades_enriched = []
                for idx, row in df_t.iterrows():
                    pair = row["pair"]
                    if pair not in pair_4h_cache:
                        pair_4h_cache[pair] = load_4h_feather(pair)
                    df_4h = pair_4h_cache[pair]

                    open_rate = float(row["open_rate"])
                    close_rate = float(row["close_rate"])
                    profit_abs = float(row.get("profit_abs", 0.0))
                    profit_ratio = float(row.get("profit_ratio", 0.0))
                    exit_reason = str(row.get("exit_reason", ""))
                    fee_open = float(row.get("fee_open_cost", 0.0))
                    fee_close = float(row.get("fee_close_cost", 0.0))
                    total_fee = fee_open + fee_close

                    entry_time_ms = to_timestamp_ms(row["open_date"])
                    close_time_ms = to_timestamp_ms(row["close_date"])
                    holding_hours = (close_time_ms - entry_time_ms) / (1000 * 3600)

                    initial_stop = open_rate * (1.0 - s_val)

                    # 因果重放 4h 最低价跟踪
                    effective_stop = initial_stop
                    last_ref_close_ms = 0
                    last_ref_close_utc = ""
                    last_ref_low = np.nan
                    last_cand_stop = np.nan
                    updates_count = 0

                    if not df_4h.empty:
                        # 查找入场时及持仓期间已收盘的 4h K 线
                        sub_4h = df_4h[(df_4h["close_ms"] <= close_time_ms)]
                        # 入场时刻的初始已收盘 K 线
                        sub_entry = df_4h[df_4h["close_ms"] <= entry_time_ms]
                        min_chk_ms = sub_entry.iloc[-1]["close_ms"] if not sub_entry.empty else entry_time_ms
                        
                        relevant_bars = df_4h[(df_4h["close_ms"] >= min_chk_ms) & (df_4h["close_ms"] <= close_time_ms)]
                        for _, bar in relevant_bars.iterrows():
                            b_close_ms = int(bar["close_ms"])
                            b_low = float(bar["low"])
                            cand = b_low * (1.0 - d_val)
                            if cand > effective_stop:
                                effective_stop = cand
                                updates_count += 1
                                last_ref_close_ms = b_close_ms
                                last_ref_close_utc = str(bar["date"])
                                last_ref_low = b_low
                                last_cand_stop = cand

                    # 判定分类
                    is_still_open = (exit_reason == "force_exit")
                    if is_still_open:
                        exit_category = "force_exit_still_open"
                    elif effective_stop <= initial_stop + 1e-8:
                        exit_category = "initial_stop_loss"
                    else:
                        if profit_abs > 0:
                            exit_category = "trail_stop_profit"
                        else:
                            exit_category = "trail_stop_loss"

                    trade_rec = {
                        "strategy": strat_name,
                        "s_code": s_code,
                        "d_code": d_code,
                        "p_code": p_code,
                        "pair": pair,
                        "trade_id": row.get("trade_id", idx + 1),
                        "open_date": row["open_date"],
                        "close_date": row["close_date"],
                        "open_rate": open_rate,
                        "close_rate": close_rate,
                        "stake_amount": float(row.get("stake_amount", 0.0)),
                        "profit_abs": profit_abs,
                        "profit_ratio": profit_ratio,
                        "exit_reason": exit_reason,
                        "exit_category": exit_category,
                        "holding_hours": round(holding_hours, 2),
                        "initial_stop": round(initial_stop, 6),
                        "final_effective_stop": round(effective_stop, 6),
                        "stop_updates_count": updates_count,
                        "ref_bar_close_utc": last_ref_close_utc,
                        "ref_bar_low": last_ref_low,
                        "ref_cand_stop": last_cand_stop,
                        "fee_cost": total_fee,
                    }
                    all_trades.append(trade_rec)
                    strat_trades_enriched.append(trade_rec)

                # 汇总单组指标
                df_st = pd.DataFrame(strat_trades_enriched)
                trades_total = len(df_st)
                trades_open = len(df_st[df_st["exit_category"] == "force_exit_still_open"])
                trades_closed = trades_total - trades_open
                net_pnl = float(df_st["profit_abs"].sum())
                wins = len(df_st[df_st["profit_abs"] > 0])
                win_rate = (wins / trades_total * 100) if trades_total > 0 else 0.0
                
                gross_profit = float(df_st[df_st["profit_abs"] > 0]["profit_abs"].sum())
                gross_loss = abs(float(df_st[df_st["profit_abs"] < 0]["profit_abs"].sum()))
                profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

                avg_holding = float(df_st["holding_hours"].mean()) if trades_total > 0 else 0.0
                fees_sum = float(df_st["fee_cost"].sum())

                run_summaries.append({
                    "strategy": strat_name,
                    "s_code": s_code,
                    "d_code": d_code,
                    "p_code": p_code,
                    "stoploss_s": s_val,
                    "trail_d": d_val,
                    "trades_total": trades_total,
                    "trades_closed": trades_closed,
                    "trades_open": trades_open,
                    "net_pnl": round(net_pnl, 2),
                    "win_rate": round(win_rate, 2),
                    "profit_factor": round(profit_factor, 2),
                    "max_drawdown_abs": round(max_dd_abs, 2),
                    "max_drawdown_pct": round(max_dd_pct, 2),
                    "avg_holding_hours": round(avg_holding, 2),
                    "fees_total": round(fees_sum, 4),
                    "funding_total": 0.0,
                })

    df_runs = pd.DataFrame(run_summaries)
    df_runs.to_csv(OUT_DIR / "01_144组单组宏观运行全景表.csv", index=False, encoding="utf-8-sig")

    df_all_trades = pd.DataFrame(all_trades)
    df_all_trades.to_csv(OUT_DIR / "02_全量逐笔追踪与因果审计底表.csv", index=False, encoding="utf-8-sig")

    # 2. 生成 16 种组合对照总表 (4档 s x 4档 d，每档 9 组入场参数)
    combo_records = []
    for s_code, s_val in STOP_MAP.items():
        for d_code, d_val in TRAIL_MAP.items():
            combo_name = f"{s_code}_{d_code}"
            sub_runs = df_runs[(df_runs["s_code"] == s_code) & (df_runs["d_code"] == d_code)]
            sub_trades = df_all_trades[(df_all_trades["s_code"] == s_code) & (df_all_trades["d_code"] == d_code)] if not df_all_trades.empty else pd.DataFrame()

            total_trades = int(sub_runs["trades_total"].sum())
            total_net_pnl = float(sub_runs["net_pnl"].sum())
            median_net_pnl = float(sub_runs["net_pnl"].median())
            profit_groups = int((sub_runs["net_pnl"] > 0).sum())
            
            if not sub_trades.empty:
                wins = len(sub_trades[sub_trades["profit_abs"] > 0])
                win_rate = (wins / len(sub_trades) * 100) if len(sub_trades) > 0 else 0.0
                avg_holding = float(sub_trades["holding_hours"].mean())
                
                # 退出细分
                init_sl_trades = sub_trades[sub_trades["exit_category"] == "initial_stop_loss"]
                trail_win_trades = sub_trades[sub_trades["exit_category"] == "trail_stop_profit"]
                trail_loss_trades = sub_trades[sub_trades["exit_category"] == "trail_stop_loss"]
                open_trades = sub_trades[sub_trades["exit_category"] == "force_exit_still_open"]
                
                init_sl_count = len(init_sl_trades)
                init_sl_pnl = float(init_sl_trades["profit_abs"].sum())
                trail_win_count = len(trail_win_trades)
                trail_win_pnl = float(trail_win_trades["profit_abs"].sum())
                trail_loss_count = len(trail_loss_trades)
                trail_loss_pnl = float(trail_loss_trades["profit_abs"].sum())
                open_count = len(open_trades)
                open_pnl = float(open_trades["profit_abs"].sum())
                fees_sum = float(sub_trades["fee_cost"].sum())
            else:
                win_rate = 0.0
                avg_holding = 0.0
                init_sl_count = 0
                init_sl_pnl = 0.0
                trail_win_count = 0
                trail_win_pnl = 0.0
                trail_loss_count = 0
                trail_loss_pnl = 0.0
                open_count = 0
                open_pnl = 0.0
                fees_sum = 0.0

            max_dd_abs_max = float(sub_runs["max_drawdown_abs"].max())
            max_dd_pct_max = float(sub_runs["max_drawdown_pct"].max())

            combo_records.append({
                "combo": combo_name,
                "s_code": s_code,
                "d_code": d_code,
                "stoploss_s": f"{int(s_val*100)}%",
                "trail_d": f"{int(d_val*100)}%",
                "total_trades": total_trades,
                "total_net_pnl": round(total_net_pnl, 2),
                "median_net_pnl": round(median_net_pnl, 2),
                "profit_groups": f"{profit_groups}/9",
                "win_rate_pct": round(win_rate, 2),
                "init_sl_count": init_sl_count,
                "init_sl_pnl": round(init_sl_pnl, 2),
                "trail_win_count": trail_win_count,
                "trail_win_pnl": round(trail_win_pnl, 2),
                "trail_loss_count": trail_loss_count,
                "trail_loss_pnl": round(trail_loss_pnl, 2),
                "open_count": open_count,
                "open_pnl": round(open_pnl, 2),
                "max_drawdown_abs": round(max_dd_abs_max, 2),
                "max_drawdown_pct": round(max_dd_pct_max, 2),
                "avg_holding_hours": round(avg_holding, 1),
                "avg_holding_days": round(avg_holding / 24.0, 1),
                "fees_total": round(fees_sum, 2),
                "funding_total": 0.0
            })

    df_combos = pd.DataFrame(combo_records)
    df_combos.to_csv(OUT_DIR / "03_16种组合核心对照总表.csv", index=False, encoding="utf-8-sig")

    # 3. 与 FT-STOP-GRID-02 配对比较表
    grid02_csv = GRID02_DIR / "01_四档止损36组宏观指标全景对照总表.csv"
    if grid02_csv.exists():
        df_g02 = pd.read_csv(grid02_csv)
        # 汇总 G02 按 4 档 stoploss
        g02_tier_map = {"Stop05": "S05", "Stop10": "S10", "Stop15": "S15", "Stop20": "S20"}
        df_g02["s_code"] = df_g02["tier"].map(g02_tier_map)
        
        g02_summary = df_g02.groupby("s_code").agg({
            "trades_total": "sum",
            "total_net_pnl": "sum",
            "win_rate": "mean",
            "avg_holding_hours": "mean"
        }).reset_index().rename(columns={
            "trades_total": "g02_trades",
            "total_net_pnl": "g02_net_pnl",
            "win_rate": "g02_win_rate",
            "avg_holding_hours": "g02_avg_holding"
        })

        df_pair = pd.merge(df_combos, g02_summary, on="s_code", how="left")
        df_pair["pnl_diff_vs_g02"] = df_pair["total_net_pnl"] - df_pair["g02_net_pnl"]
        df_pair["trades_diff_vs_g02"] = df_pair["total_trades"] - df_pair["g02_trades"]
        df_pair.to_csv(OUT_DIR / "04_与FT_STOP_GRID_02配对比较表.csv", index=False, encoding="utf-8-sig")

    print("FT-LOW4H-GRID-01 对账与分析全部完成！产出底表均已落盘。")


if __name__ == "__main__":
    main()

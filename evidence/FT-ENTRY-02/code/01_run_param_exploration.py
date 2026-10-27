# -*- coding: utf-8 -*-
"""FT-ENTRY-02 有限阈值探索回测与稳健性分析脚本

功能：
1. 回测 19 组策略（1 组 Baseline + 9 组 A + 9 组 C）；
2. 提取逐笔并计算扣费后胜率 (Wilson 95% CI)、净期望、利润因子、回撤；
3. 拆分 2022、2023、2024 年分年度表现；
4. 检验剔除单笔最大盈利、剔除最大贡献币种后的极值敏感性；
5. 生成 3x3 相邻参数网格矩阵，检验平滑度与寻找稳定区域；
6. 自动归档并输出统计表与日志。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Any, Tuple

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_ENTRY_DIR = BASE_DIR / "evidence/FT-ENTRY-02"
USER_DATA_DIR = FT_ENTRY_DIR / "user_data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STRATEGY_PATH = USER_DATA_DIR / "strategies"
LOGS_DIR = FT_ENTRY_DIR / "logs"
OUT_DIR = FT_ENTRY_DIR / "out"
BT_RESULTS_DIR = USER_DATA_DIR / "backtest_results"

FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "param_exploration_run.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-ENTRY-02")

# 定义全部 19 组实验
EXPERIMENTS = [
    {
        "name": "WyckoffBaseline",
        "route": "Benchmark",
        "label": "基准 (原突破入场)",
        "param1_name": "-", "param1_val": "-",
        "param2_name": "-", "param2_val": "-"
    }
]

# A 组 9 组
for o_tag, o_val in [("L06", 0.6), ("L04", 0.4), ("L02", 0.2)]:
    for v_tag, v_val in [("V08", 0.8), ("V10", 1.0), ("V12", 1.2)]:
        sname = f"Wyckoff_A_{o_tag}_{v_tag}"
        EXPERIMENTS.append({
            "name": sname,
            "route": "Route A",
            "label": f"A: H-{o_val}A, Vol>={v_val}V20",
            "param1_name": "entry_offset_atr", "param1_val": o_val,
            "param2_name": "vol_mult_v20", "param2_val": v_val
        })

# C 组 9 组
for v_tag, v_val in [("Vs04", 0.4), ("Vs06", 0.6), ("Vs08", 0.8)]:
    for z_tag, z_val in [("Z02", 0.2), ("Z04", 0.4), ("Z06", 0.6)]:
        sname = f"Wyckoff_C_{v_tag}_{z_tag}"
        EXPERIMENTS.append({
            "name": sname,
            "route": "Route C",
            "label": f"C: Vol<={v_val}Vs, Zone<={z_val}A",
            "param1_name": "vol_ratio_vs", "param1_val": v_val,
            "param2_name": "retest_zone_atr", "param2_val": z_val
        })


def wilson_score_interval(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """计算 Wilson Score 95% 双尾置信区间 (百分比)。"""
    if n == 0:
        return 0.0, 0.0
    z = 1.95996
    p_hat = k / n
    denom = 1 + (z**2 / n)
    centre_adj = p_hat + (z**2 / (2 * n))
    radicand = (p_hat * (1 - p_hat) / n) + (z**2 / (4 * (n**2)))
    adj_sd = z * np.sqrt(max(0.0, radicand))
    lower = max(0.0, (centre_adj - adj_sd) / denom)
    upper = min(1.0, (centre_adj + adj_sd) / denom)
    return round(lower * 100.0, 2), round(upper * 100.0, 2)


def run_freqtrade_backtest(strategy_name: str) -> pd.DataFrame:
    """调用 Freqtrade 执行单组策略回测并提取 trades DataFrame。"""
    log_file = LOGS_DIR / f"{strategy_name}_backtest.log"
    if BT_RESULTS_DIR.exists():
        shutil.rmtree(BT_RESULTS_DIR)
    BT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(FT_BIN),
        "backtesting",
        "-c", str(CONFIG_PATH),
        "--strategy", strategy_name,
        "--strategy-path", str(STRATEGY_PATH),
        "--timerange", "20211201-20241231",
        "--cache", "none",
        "--export", "trades"
    ]

    logger.info(f"\n================ [FT-ENTRY-02] 启动回测: {strategy_name} ================")
    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    if proc.returncode != 0:
        raise RuntimeError(f"策略 {strategy_name} 回测异常退出 (code: {proc.returncode})，详见 {log_file}")

    zip_files = sorted(list(BT_RESULTS_DIR.glob("backtest-result*.zip")), key=os.path.getmtime, reverse=True)
    if not zip_files:
        raise FileNotFoundError(f"未在 {BT_RESULTS_DIR} 找到结果文件")

    latest_zip = zip_files[0]
    archive_zip = OUT_DIR / f"backtest-result-{strategy_name}.zip"
    shutil.copy2(latest_zip, archive_zip)

    data = None
    with zipfile.ZipFile(latest_zip, 'r') as z:
        for fname in z.namelist():
            if fname.endswith(".json") and not fname.endswith(".meta.json") and not fname.endswith("_config.json"):
                with z.open(fname) as f:
                    data = json.load(f)
                break

    if not data or "strategy" not in data or strategy_name not in data["strategy"]:
        raise ValueError(f"无法从 {latest_zip} 解析结果！")

    strat_res = data["strategy"][strategy_name]
    trades = strat_res.get("trades", [])
    df = pd.DataFrame(trades)
    return df


def calculate_metrics(df_trades: pd.DataFrame, initial_equity: float = 2000.0) -> Dict[str, Any]:
    """严格计算回测各项指标。"""
    if df_trades.empty:
        return {
            "trade_count": 0, "win_count": 0, "loss_count": 0, "win_rate": 0.0,
            "win_rate_ci95": "[0.0%, 0.0%]", "net_pnl": 0.0, "net_return_pct": 0.0,
            "profit_factor": 0.0, "expectancy_u": 0.0, "avg_profit_u": 0.0, "avg_loss_u": 0.0,
            "max_drawdown_closed_equity_u": 0.0, "max_drawdown_closed_equity_pct": 0.0
        }

    pnl_series = df_trades["profit_abs"] if "profit_abs" in df_trades.columns else df_trades["net_pnl"]
    trade_count = len(df_trades)
    wins = df_trades[pnl_series > 0]
    losses = df_trades[pnl_series < 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / trade_count) * 100.0 if trade_count > 0 else 0.0
    ci_low, ci_high = wilson_score_interval(win_count, trade_count)

    net_pnl = float(pnl_series.sum())
    net_return_pct = (net_pnl / initial_equity) * 100.0

    gross_profit = float(wins["profit_abs"].sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses["profit_abs"].sum())) if not losses.empty else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    expectancy_u = net_pnl / trade_count if trade_count > 0 else 0.0
    avg_profit_u = gross_profit / win_count if win_count > 0 else 0.0
    avg_loss_u = -gross_loss / loss_count if loss_count > 0 else 0.0

    df_sorted = df_trades.sort_values(by="close_timestamp" if "close_timestamp" in df_trades.columns else "close_date")
    equity_curve = [initial_equity]
    curr_eq = initial_equity
    for _, row in df_sorted.iterrows():
        p = float(row.get("profit_abs", row.get("net_pnl", 0.0)))
        curr_eq += p
        equity_curve.append(curr_eq)

    peak = equity_curve[0]
    max_dd_u = 0.0
    max_dd_pct = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd_u = peak - eq
        dd_pct = (dd_u / peak) * 100.0 if peak > 0 else 0.0
        if dd_u > max_dd_u:
            max_dd_u = dd_u
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    return {
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_rate, 2),
        "win_rate_ci95": f"[{ci_low}%, {ci_high}%]",
        "net_pnl": round(net_pnl, 4),
        "net_return_pct": round(net_return_pct, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "inf",
        "expectancy_u": round(expectancy_u, 4),
        "avg_profit_u": round(avg_profit_u, 4),
        "avg_loss_u": round(avg_loss_u, 4),
        "max_drawdown_closed_equity_u": round(max_dd_u, 4),
        "max_drawdown_closed_equity_pct": round(max_dd_pct, 4)
    }


def analyze_yearly_stability(df_trades: pd.DataFrame, strategy_name: str) -> List[Dict[str, Any]]:
    """拆分 2022、2023、2024 年分年度表现。"""
    records = []
    if df_trades.empty:
        for year in [2022, 2023, 2024]:
            records.append({
                "strategy_name": strategy_name, "year": year, "trade_count": 0,
                "win_rate": 0.0, "net_pnl": 0.0, "expectancy_u": 0.0
            })
        return records

    df = df_trades.copy()
    df["close_dt"] = pd.to_datetime(df["close_date"], utc=True)
    df["year"] = df["close_dt"].dt.year

    for year in [2022, 2023, 2024]:
        sub = df[df["year"] == year]
        m = calculate_metrics(sub)
        records.append({
            "strategy_name": strategy_name,
            "year": year,
            "trade_count": m["trade_count"],
            "win_rate": m["win_rate"],
            "net_pnl": m["net_pnl"],
            "expectancy_u": m["expectancy_u"]
        })
    return records


def analyze_sensitivity(df_trades: pd.DataFrame, strategy_name: str) -> Dict[str, Any]:
    """计算剔除单笔最大盈利、剔除最大贡献币种后的极值敏感性。"""
    if df_trades.empty or len(df_trades) < 2:
        return {
            "strategy_name": strategy_name,
            "raw_net_pnl": 0.0, "raw_expectancy": 0.0,
            "drop_max_trade_pnl": 0.0, "drop_max_trade_expectancy": 0.0,
            "max_trade_pnl": 0.0, "max_trade_pair": "-",
            "drop_max_symbol_pnl": 0.0, "drop_max_symbol_expectancy": 0.0,
            "max_symbol_pnl": 0.0, "max_symbol": "-"
        }

    pnl_col = "profit_abs" if "profit_abs" in df_trades.columns else "net_pnl"
    raw_pnl = float(df_trades[pnl_col].sum())
    raw_exp = raw_pnl / len(df_trades)

    # 1. 剔除单笔最大盈利交易
    max_idx = df_trades[pnl_col].idxmax()
    max_row = df_trades.loc[max_idx]
    max_trade_val = float(max_row[pnl_col])
    max_trade_pair = max_row.get("pair", "")

    df_drop_trade = df_trades.drop(index=max_idx)
    drop_tr_pnl = float(df_drop_trade[pnl_col].sum())
    drop_tr_exp = drop_tr_pnl / len(df_drop_trade) if len(df_drop_trade) > 0 else 0.0

    # 2. 剔除最大贡献币种
    sym_pnl = df_trades.groupby("pair")[pnl_col].sum()
    max_sym = sym_pnl.idxmax()
    max_sym_val = float(sym_pnl.max())

    df_drop_sym = df_trades[df_trades["pair"] != max_sym]
    drop_sym_pnl = float(df_drop_sym[pnl_col].sum())
    drop_sym_exp = drop_sym_pnl / len(df_drop_sym) if len(df_drop_sym) > 0 else 0.0

    return {
        "strategy_name": strategy_name,
        "raw_net_pnl": round(raw_pnl, 4),
        "raw_expectancy": round(raw_exp, 4),
        "drop_max_trade_pnl": round(drop_tr_pnl, 4),
        "drop_max_trade_expectancy": round(drop_tr_exp, 4),
        "max_trade_pnl": round(max_trade_val, 4),
        "max_trade_pair": max_trade_pair,
        "drop_max_symbol_pnl": round(drop_sym_pnl, 4),
        "drop_max_symbol_expectancy": round(drop_sym_exp, 4),
        "max_symbol_pnl": round(max_sym_val, 4),
        "max_symbol": max_sym
    }


def main():
    logger.info("================ 开始 FT-ENTRY-02 参数探索自动化流程 ================")
    
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    logger.info(f"合格标的数: {len(cfg['exchange']['pair_whitelist'])}")

    summary_list = []
    yearly_list = []
    sensitivity_list = []
    trades_dict = {}

    for exp in EXPERIMENTS:
        sname = exp["name"]
        df_trades = run_freqtrade_backtest(sname)
        trades_dict[sname] = df_trades

        # 保存逐笔
        csv_path = OUT_DIR / f"trades_{sname}.csv"
        df_trades.to_csv(csv_path, index=False, encoding="utf-8-sig")

        # 汇总
        m = calculate_metrics(df_trades)
        m["strategy_name"] = sname
        m["route"] = exp["route"]
        m["label"] = exp["label"]
        m["param1_name"] = exp["param1_name"]
        m["param1_val"] = exp["param1_val"]
        m["param2_name"] = exp["param2_name"]
        m["param2_val"] = exp["param2_val"]
        summary_list.append(m)

        # 年度
        y_records = analyze_yearly_stability(df_trades, sname)
        yearly_list.extend(y_records)

        # 敏感性
        sens = analyze_sensitivity(df_trades, sname)
        sensitivity_list.append(sens)

    # 1. 导出 01_参数探索总表.csv
    df_sum = pd.DataFrame(summary_list)
    cols = [
        "strategy_name", "route", "label", "param1_name", "param1_val", "param2_name", "param2_val",
        "trade_count", "win_count", "loss_count", "win_rate", "win_rate_ci95", "net_pnl", "net_return_pct",
        "profit_factor", "expectancy_u", "avg_profit_u", "avg_loss_u",
        "max_drawdown_closed_equity_u", "max_drawdown_closed_equity_pct"
    ]
    df_sum = df_sum[cols]
    df_sum.to_csv(OUT_DIR / "01_参数探索总表.csv", index=False, encoding="utf-8-sig")
    logger.info("已生成 01_参数探索总表.csv")

    # 2. 导出 02_分年度稳定性拆分表.csv
    df_year = pd.DataFrame(yearly_list)
    df_year.to_csv(OUT_DIR / "02_分年度稳定性拆分表.csv", index=False, encoding="utf-8-sig")
    logger.info("已生成 02_分年度稳定性拆分表.csv")

    # 3. 导出 03_极值敏感性检验表.csv
    df_sens = pd.DataFrame(sensitivity_list)
    df_sens.to_csv(OUT_DIR / "03_极值敏感性检验表.csv", index=False, encoding="utf-8-sig")
    logger.info("已生成 03_极值敏感性检验表.csv")

    # 4. 生成 3x3 平滑度网格矩阵
    grid_data = {"route_A": {}, "route_C": {}}

    # A 路线: y=offset (0.6, 0.4, 0.2), x=vol (0.8, 1.0, 1.2)
    offsets = [0.6, 0.4, 0.2]
    vols = [0.8, 1.0, 1.2]
    a_exp_matrix = []
    a_win_matrix = []
    a_cnt_matrix = []
    for o in offsets:
        row_exp = []
        row_win = []
        row_cnt = []
        for v in vols:
            match = df_sum[(df_sum["param1_val"] == o) & (df_sum["param2_val"] == v)]
            if not match.empty:
                r = match.iloc[0]
                row_exp.append(r["expectancy_u"])
                row_win.append(r["win_rate"])
                row_cnt.append(int(r["trade_count"]))
            else:
                row_exp.append(None)
                row_win.append(None)
                row_cnt.append(0)
        a_exp_matrix.append(row_exp)
        a_win_matrix.append(row_win)
        a_cnt_matrix.append(row_cnt)

    grid_data["route_A"] = {
        "param_y": "entry_offset_atr (0.6, 0.4, 0.2)",
        "param_x": "vol_mult_v20 (0.8, 1.0, 1.2)",
        "expectancy_matrix": a_exp_matrix,
        "win_rate_matrix": a_win_matrix,
        "trade_count_matrix": a_cnt_matrix
    }

    # C 路线: y=vol_vs (0.4, 0.6, 0.8), x=zone (0.2, 0.4, 0.6)
    c_vols = [0.4, 0.6, 0.8]
    c_zones = [0.2, 0.4, 0.6]
    c_exp_matrix = []
    c_win_matrix = []
    c_cnt_matrix = []
    for v in c_vols:
        row_exp = []
        row_win = []
        row_cnt = []
        for z in c_zones:
            match = df_sum[(df_sum["param1_val"] == v) & (df_sum["param2_val"] == z)]
            if not match.empty:
                r = match.iloc[0]
                row_exp.append(r["expectancy_u"])
                row_win.append(r["win_rate"])
                row_cnt.append(int(r["trade_count"]))
            else:
                row_exp.append(None)
                row_win.append(None)
                row_cnt.append(0)
        c_exp_matrix.append(row_exp)
        c_win_matrix.append(row_win)
        c_cnt_matrix.append(row_cnt)

    grid_data["route_C"] = {
        "param_y": "vol_ratio_vs (0.4, 0.6, 0.8)",
        "param_x": "retest_zone_atr (0.2, 0.4, 0.6)",
        "expectancy_matrix": c_exp_matrix,
        "win_rate_matrix": c_win_matrix,
        "trade_count_matrix": c_cnt_matrix
    }

    with open(OUT_DIR / "04_参数平滑度网格矩阵.json", "w", encoding="utf-8") as f:
        json.dump(grid_data, f, indent=2, ensure_ascii=False)
    logger.info("已生成 04_参数平滑度网格矩阵.json")

    logger.info("================ FT-ENTRY-02 全部回测与指标计算完成 ================")


if __name__ == "__main__":
    main()

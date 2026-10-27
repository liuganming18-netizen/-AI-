# -*- coding: utf-8 -*-
"""FT-FACTOR-01 因子消融实验运行与证据采集脚本

核心职责：
1. 直接复用 FT-BASELINE-05 / FT-BASELINE-06 已核验的 5 币基线结果，不重复重跑；
2. 在相同的 5 币、相同的时间区间（20250801-20251231）、相同的数据版本（WX-FT-IN-1.0）上，
   分别运行 4 个消融变体：
   - Ablation_No_OI (WyckoffAblationNoOI): 移除 6h OI 增幅 2% 过滤
   - Ablation_No_BreakoutVol (WyckoffAblationNoBreakoutVol): 移除突破 1.5x V20 放量过滤
   - Ablation_No_RetestVol (WyckoffAblationNoRetestVol): 移除回踩 0.8x Vs 缩量过滤
   - Ablation_No_GapFilter (WyckoffAblationNoGapFilter): 移除入场跳空上限过滤
3. 解析官方 backtest-result zip/json/csv，提取交易明细与回测指标；
4. 输出逐笔结果、对照表、单币与最大盈利单贡献拆解表。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any

import pandas as pd
import numpy as np

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_VENV_PY = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/python"
FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

EXP_DIR = BASE_DIR / "evidence/FT-FACTOR-01"
LOGS_DIR = EXP_DIR / "logs"
OUT_DIR = EXP_DIR / "out"
USER_DATA_DIR = EXP_DIR / "user_data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STRATEGY_PATH = USER_DATA_DIR / "strategies"
BT_RESULTS_DIR = USER_DATA_DIR / "backtest_results"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "ablation_run.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-FACTOR-01")


def get_baseline_verified_trades() -> pd.DataFrame:
    """直接复用 FT-BASELINE-05 已核验的 5 币结果，不重复重跑。"""
    baseline_trades_file = BASE_DIR / "evidence/FT-BASELINE-05/out/04_freqtrade_trades.csv"
    if not baseline_trades_file.exists():
        raise FileNotFoundError(f"基线交易文件不存在: {baseline_trades_file}")
    df = pd.read_csv(baseline_trades_file)
    logger.info(f"成功复用 FT-BASELINE-05 官方基线交易，共 {len(df)} 笔。")
    return df


def run_freqtrade_backtest(strategy_name: str) -> pd.DataFrame:
    """调用 Freqtrade 官方引擎执行单次回测并解析 trades。"""
    log_file = LOGS_DIR / f"{strategy_name}_backtest.log"

    # 清理旧的 backtest_results 目录以确保读取的是本次运行的最新产物
    if BT_RESULTS_DIR.exists():
        shutil.rmtree(BT_RESULTS_DIR)
    BT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(FT_BIN),
        "backtesting",
        "-c", str(CONFIG_PATH),
        "--strategy", strategy_name,
        "--strategy-path", str(STRATEGY_PATH),
        "--timerange", "20250801-20251231",
        "--cache", "none",
        "--export", "trades"
    ]

    logger.info(f"开始执行回测: {strategy_name}")
    logger.info(f"命令: {' '.join(cmd)}")

    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            stdout=lf,
            stderr=subprocess.STDOUT,
            text=True
        )

    if proc.returncode != 0:
        raise RuntimeError(f"回测失败，策略: {strategy_name}，退出码: {proc.returncode}，详见日志: {log_file}")

    logger.info(f"回测完成: {strategy_name}，日志已写入: {log_file}")

    # 提取 trades
    zip_files = sorted(list(BT_RESULTS_DIR.glob("backtest-result*.zip")), key=os.path.getmtime, reverse=True)
    if not zip_files:
        raise FileNotFoundError(f"未在 {BT_RESULTS_DIR} 找到回测结果 zip 文件！")

    latest_zip = zip_files[0]
    # 归档保存该策略的结果 zip
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
        raise ValueError(f"无法从 {latest_zip} 解析 {strategy_name} 的回测结果！")

    strat_res = data["strategy"][strategy_name]
    trades = strat_res.get("trades", [])
    df = pd.DataFrame(trades)
    return df


def calculate_metrics(df_trades: pd.DataFrame, initial_equity: float = 2000.0) -> Dict[str, Any]:
    """严格计算标准回测统计指标（净收益、交易数、每笔期望、利润因子、费用、平仓权益回撤）。"""
    if df_trades.empty:
        return {
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "net_return_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_u": 0.0,
            "expectancy_pct": 0.0,
            "total_fees": 0.0,
            "total_funding": 0.0,
            "max_drawdown_closed_equity_u": 0.0,
            "max_drawdown_closed_equity_pct": 0.0,
            "drawdown_type": "平仓权益回撤",
            "max_profit_trade_u": 0.0,
            "max_profit_trade_symbol": "None",
            "max_profit_trade_share_pct": 0.0
        }

    # 规范化列名
    pnl_series = df_trades["profit_abs"] if "profit_abs" in df_trades.columns else df_trades["net_pnl"]
    trade_count = len(df_trades)
    wins = df_trades[pnl_series > 0]
    losses = df_trades[pnl_series < 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / trade_count) * 100.0 if trade_count > 0 else 0.0

    net_pnl = float(pnl_series.sum())
    net_return_pct = (net_pnl / initial_equity) * 100.0

    gross_profit = float(wins["profit_abs"].sum()) if not wins.empty else (float(wins["net_pnl"].sum()) if not wins.empty else 0.0)
    gross_loss = abs(float(losses["profit_abs"].sum())) if not losses.empty else (abs(float(losses["net_pnl"].sum())) if not losses.empty else 0.0)

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    expectancy_u = net_pnl / trade_count if trade_count > 0 else 0.0
    expectancy_pct = (expectancy_u / initial_equity) * 100.0

    # 费用计算
    fee_open = df_trades["fee_open_cost"].sum() if "fee_open_cost" in df_trades.columns else (df_trades["fee_open"].sum() if "fee_open" in df_trades.columns else 0.0)
    fee_close = df_trades["fee_close_cost"].sum() if "fee_close_cost" in df_trades.columns else (df_trades["fee_close"].sum() if "fee_close" in df_trades.columns else 0.0)
    total_fees = float(fee_open + fee_close)
    total_funding = float(df_trades["funding_fees"].sum()) if "funding_fees" in df_trades.columns else 0.0

    # 平仓权益序列与平仓权益回撤
    df_sorted = df_trades.sort_values(by="close_timestamp" if "close_timestamp" in df_trades.columns else "close_date")
    equity_curve = [initial_equity]
    curr_eq = initial_equity
    for _, row in df_sorted.iterrows():
        p = float(row.get("profit_abs", row.get("net_pnl", 0.0)))
        curr_eq += p
        equity_curve.append(curr_eq)

    # 计算历史峰值与回撤
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

    # 最大盈利单贡献
    max_pnl = float(pnl_series.max())
    max_pnl_idx = pnl_series.idxmax()
    max_trade_row = df_trades.loc[max_pnl_idx]
    max_sym = str(max_trade_row.get("pair", ""))
    max_share_pct = (max_pnl / net_pnl * 100.0) if net_pnl > 0 and max_pnl > 0 else 0.0

    return {
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_rate, 2),
        "net_pnl": round(net_pnl, 4),
        "net_return_pct": round(net_return_pct, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "inf",
        "expectancy_u": round(expectancy_u, 4),
        "expectancy_pct": round(expectancy_pct, 4),
        "total_fees": round(total_fees, 4),
        "total_funding": round(total_funding, 4),
        "max_drawdown_closed_equity_u": round(max_dd_u, 4),
        "max_drawdown_closed_equity_pct": round(max_dd_pct, 4),
        "drawdown_type": "平仓权益回撤",
        "max_profit_trade_u": round(max_pnl, 4),
        "max_profit_trade_symbol": max_sym,
        "max_profit_trade_share_pct": round(max_share_pct, 2)
    }


def main():
    logger.info("================ 开始 FT-FACTOR-01 消融实验与对照分析 ================")
    
    # 1. 直接复用基线结果
    df_baseline = get_baseline_verified_trades()
    baseline_metrics = calculate_metrics(df_baseline)
    baseline_metrics["group_name"] = "Baseline (冻结基线: 复用 FT-BASELINE-05)"
    baseline_metrics["ablation_factor"] = "无消融 (完整现有规则)"
    baseline_metrics["status"] = "直接复用 (未重复重跑)"

    # 保存已核验 5 币基线明细
    df_baseline.to_csv(OUT_DIR / "01_已核验5币基线结果_直接复用.csv", index=False, encoding="utf-8-sig")

    # 2. 定义 4 个消融组
    ablation_experiments = [
        {
            "name": "WyckoffAblationNoOI",
            "group_name": "Ablation_No_OI (移除 6h OI 增幅 2% 过滤)",
            "ablation_factor": "F_OI (6h OI 增幅过滤)",
            "code_location": "state_machine.py:564-571"
        },
        {
            "name": "WyckoffAblationNoBreakoutVol",
            "group_name": "Ablation_No_BreakoutVol (移除突破 1.5x V20 放量过滤)",
            "ablation_factor": "F_VOL_BO (突破放量过滤)",
            "code_location": "state_machine.py:525-532"
        },
        {
            "name": "WyckoffAblationNoRetestVol",
            "group_name": "Ablation_No_RetestVol (移除回踩 0.8x Vs 缩量过滤)",
            "ablation_factor": "F_VOL_RT (回踩缩量过滤)",
            "code_location": "state_machine.py:457"
        },
        {
            "name": "WyckoffAblationNoGapFilter",
            "group_name": "Ablation_No_GapFilter (移除入场跳空上限过滤)",
            "ablation_factor": "F_GAP_IN (入场跳空上限过滤)",
            "code_location": "state_machine.py:577 & Baseline.py:299-300"
        }
    ]

    all_metrics = [baseline_metrics]
    all_trades_records = []

    # 记录基线 trades
    for _, tr in df_baseline.iterrows():
        all_trades_records.append({
            "group": "Baseline",
            "pair": tr.get("pair", ""),
            "open_date": tr.get("open_date", ""),
            "close_date": tr.get("close_date", ""),
            "open_rate": tr.get("open_rate", np.nan),
            "close_rate": tr.get("close_rate", np.nan),
            "amount": tr.get("amount", np.nan),
            "net_pnl": tr.get("profit_abs", tr.get("net_pnl", np.nan)),
            "profit_ratio": tr.get("profit_ratio", np.nan),
            "exit_reason": tr.get("exit_reason", "")
        })

    # 3. 逐个执行消融回测
    for exp in ablation_experiments:
        strat_name = exp["name"]
        logger.info(f"\n>>> 正在运行消融实验: {strat_name} ({exp['group_name']})")
        df_trades = run_freqtrade_backtest(strat_name)
        
        # 导出单组 trades
        df_trades.to_csv(OUT_DIR / f"trades_{strat_name}.csv", index=False, encoding="utf-8-sig")

        # 计算指标
        m = calculate_metrics(df_trades)
        m["group_name"] = exp["group_name"]
        m["ablation_factor"] = exp["ablation_factor"]
        m["status"] = "官方引擎完成"
        all_metrics.append(m)

        for _, tr in df_trades.iterrows():
            all_trades_records.append({
                "group": strat_name,
                "pair": tr.get("pair", ""),
                "open_date": tr.get("open_date", ""),
                "close_date": tr.get("close_date", ""),
                "open_rate": tr.get("open_rate", np.nan),
                "close_rate": tr.get("close_rate", np.nan),
                "amount": tr.get("amount", np.nan),
                "net_pnl": tr.get("profit_abs", np.nan),
                "profit_ratio": tr.get("profit_ratio", np.nan),
                "exit_reason": tr.get("exit_reason", "")
            })

    # 4. 生成横向对照表
    df_metrics = pd.DataFrame(all_metrics)
    cols_order = [
        "group_name", "ablation_factor", "status", "trade_count", "win_count", "loss_count", "win_rate",
        "net_pnl", "net_return_pct", "profit_factor", "expectancy_u", "expectancy_pct",
        "total_fees", "total_funding", "max_drawdown_closed_equity_u", "max_drawdown_closed_equity_pct",
        "drawdown_type", "max_profit_trade_u", "max_profit_trade_symbol", "max_profit_trade_share_pct"
    ]
    df_metrics = df_metrics[cols_order]
    df_metrics.to_csv(OUT_DIR / "02_因子消融对照结果表.csv", index=False, encoding="utf-8-sig")

    # 5. 生成所有逐笔明细汇总表
    df_all_trades = pd.DataFrame(all_trades_records)
    df_all_trades.to_csv(OUT_DIR / "03_逐笔交易对账明细.csv", index=False, encoding="utf-8-sig")

    # 6. 单币与最大盈利单贡献拆解表
    coin_attribution = []
    for grp in df_all_trades["group"].unique():
        sub = df_all_trades[df_all_trades["group"] == grp]
        for p in sub["pair"].unique():
            coin_sub = sub[sub["pair"] == p]
            coin_pnl = coin_sub["net_pnl"].sum()
            coin_attribution.append({
                "group": grp,
                "pair": p,
                "trade_count": len(coin_sub),
                "total_net_pnl": round(coin_pnl, 4),
                "avg_pnl_per_trade": round(coin_pnl / len(coin_sub), 4) if len(coin_sub) > 0 else 0.0
            })
    df_coin_att = pd.DataFrame(coin_attribution)
    df_coin_att.to_csv(OUT_DIR / "04_单币与最大盈利单贡献拆解.csv", index=False, encoding="utf-8-sig")

    logger.info("================ FT-FACTOR-01 消融实验运行完成！ ================")


if __name__ == "__main__":
    main()

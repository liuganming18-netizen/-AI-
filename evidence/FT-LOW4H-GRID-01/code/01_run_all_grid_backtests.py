# -*- coding: utf-8 -*-
"""FT-LOW4H-GRID-01: 运行4小时最低价跟踪退出的144组网格策略回测。"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP_DIR = BASE_DIR / "evidence/FT-LOW4H-GRID-01"
USER_DATA_DIR = EXP_DIR / "user_data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STRATEGY_PATH = USER_DATA_DIR / "strategies"
LOGS_DIR = EXP_DIR / "logs"
OUT_DIR = EXP_DIR / "out"

FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "run_all_grid_backtests.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-LOW4H-GRID-01-Run")

STOP_TIERS = ["S05", "S10", "S15", "S20"]
TRAIL_TIERS = ["D05", "D10", "D15", "D20"]
PARAMS = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]

ALL_STRATEGIES = [
    f"Wyckoff_C1_{s}_{d}_{p}"
    for s in STOP_TIERS
    for d in TRAIL_TIERS
    for p in PARAMS
]


def run_single(strategy_name: str, force: bool = False) -> dict:
    log_file = LOGS_DIR / f"{strategy_name}_backtest.log"
    out_csv = OUT_DIR / f"trades_{strategy_name}.csv"
    archive_zip = OUT_DIR / f"backtest-result-{strategy_name}.zip"

    if not force and archive_zip.exists() and out_csv.exists() and os.path.getsize(archive_zip) > 1000:
        try:
            df_existing = pd.read_csv(out_csv)
            logger.info(f"策略 {strategy_name} 已存在结果，跳过重跑 (trades: {len(df_existing)})")
            net_pnl = float(df_existing["profit_abs"].sum()) if not df_existing.empty and "profit_abs" in df_existing.columns else 0.0
            return {
                "strategy": strategy_name,
                "status": "cached",
                "trades": len(df_existing),
                "net_pnl": round(net_pnl, 2),
                "zip_path": str(archive_zip),
                "csv_path": str(out_csv)
            }
        except Exception:
            pass

    strat_results_dir = USER_DATA_DIR / f"backtest_results_{strategy_name}"
    if strat_results_dir.exists():
        shutil.rmtree(strat_results_dir)
    strat_results_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(FT_BIN),
        "backtesting",
        "-c", str(CONFIG_PATH),
        "--strategy", strategy_name,
        "--strategy-path", str(STRATEGY_PATH),
        "--export-directory", str(strat_results_dir),
        "--timerange", "20211201-20241231",
        "--cache", "none",
        "--export", "trades"
    ]

    logger.info(f"==> 启动回测: {strategy_name}")
    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    if proc.returncode != 0:
        logger.error(f"策略 {strategy_name} 回测异常退出 (code: {proc.returncode})，详见 {log_file}")
        return {"strategy": strategy_name, "status": "failed", "trades": 0, "net_pnl": 0.0}

    zip_files = sorted(list(strat_results_dir.glob("backtest-result*.zip")), key=os.path.getmtime, reverse=True)
    if not zip_files:
        logger.warning(f"策略 {strategy_name} 未找到 zip 结果包")
        pd.DataFrame().to_csv(out_csv, index=False)
        return {"strategy": strategy_name, "status": "no_zip", "trades": 0, "net_pnl": 0.0}

    latest_zip = zip_files[0]
    shutil.copyfile(latest_zip, archive_zip)

    # 提取逐笔 trades
    with zipfile.ZipFile(latest_zip, "r") as z:
        json_names = [n for n in z.namelist() if n.endswith(".json") and not n.endswith(".meta.json")]
        if not json_names:
            logger.warning(f"策略 {strategy_name} zip 包中没有 json 数据")
            pd.DataFrame().to_csv(out_csv, index=False)
            return {"strategy": strategy_name, "status": "no_json", "trades": 0, "net_pnl": 0.0}

        with z.open(json_names[0]) as jf:
            data = json.load(jf)

        strat_res = data.get("strategy", {}).get(strategy_name, {})
        trades_list = strat_res.get("trades", [])

    if trades_list:
        df_trades = pd.DataFrame(trades_list)
        df_trades["strategy"] = strategy_name
        df_trades.to_csv(out_csv, index=False, encoding="utf-8-sig")
        net_pnl = float(df_trades["profit_abs"].sum()) if "profit_abs" in df_trades.columns else 0.0
        n_trades = len(df_trades)
    else:
        pd.DataFrame().to_csv(out_csv, index=False)
        net_pnl = 0.0
        n_trades = 0

    # 清理临时子目录以节省磁盘
    shutil.rmtree(strat_results_dir, ignore_errors=True)

    logger.info(f"策略 {strategy_name} 回测完成! trades: {n_trades}, net_pnl: {net_pnl:.2f} USDT")
    return {
        "strategy": strategy_name,
        "status": "success",
        "trades": n_trades,
        "net_pnl": round(net_pnl, 2),
        "zip_path": str(archive_zip),
        "csv_path": str(out_csv)
    }


def main():
    parser = argparse.ArgumentParser(description="运行 FT-LOW4H-GRID-01 144 组策略回测")
    parser.add_argument("--workers", type=int, default=6, help="并行进程数 (默认 6)")
    parser.add_argument("--force", action="store_true", help="强制重新跑已存在结果的策略")
    args = parser.parse_args()

    logger.info(f"准备开始 FT-LOW4H-GRID-01 批量回测: 总计 {len(ALL_STRATEGIES)} 组策略, 并行进程数: {args.workers}")

    summary_records = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_single, s, args.force): s for s in ALL_STRATEGIES}
        for future in as_completed(futures):
            strat = futures[future]
            try:
                res = future.result()
                summary_records.append(res)
            except Exception as e:
                logger.error(f"策略 {strat} 执行遇到未捕获异常: {e}")
                summary_records.append({"strategy": strat, "status": "exception", "trades": 0, "net_pnl": 0.0})

    df_summary = pd.DataFrame(summary_records)
    summary_path = OUT_DIR / "00_execution_summary.csv"
    df_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    logger.info(f"全部 {len(ALL_STRATEGIES)} 组回测结束! 汇总已落盘: {summary_path}")


if __name__ == "__main__":
    main()

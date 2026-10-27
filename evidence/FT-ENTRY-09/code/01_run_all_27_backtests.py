# -*- coding: utf-8 -*-
"""FT-ENTRY-09 批量运行 27 组统一预热资格对照回测脚本

严格落实工单要求：
1. 复用 FT-ENTRY-07 的 C0/C1/C2 各 9 组，共 27 组实验；
2. 每币满足既定 200 根已收盘 4 小时 K 线预热及数据资格后准入；
3. 必须真实重跑全部 27 组账户、订单和持仓回测过程，严禁删除 115 笔后重新求和冒充重跑；
4. 自动保存原始回测日志 (logs/)、zip 结果包 (out/) 及逐笔 trades_{strategy}.csv；
5. 保证无交易或失败组也必须完整保留。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Any

import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_ENTRY_DIR = BASE_DIR / "evidence/FT-ENTRY-09"
USER_DATA_DIR = FT_ENTRY_DIR / "user_data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STRATEGY_PATH = USER_DATA_DIR / "strategies"
LOGS_DIR = FT_ENTRY_DIR / "logs"
OUT_DIR = FT_ENTRY_DIR / "out"
BT_RESULTS_DIR = USER_DATA_DIR / "backtest_results"

FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "unified_eligibility_backtest_run.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-ENTRY-09-Backtest")

# 27 组待运行实验矩阵
MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]

EXPERIMENTS_C0 = [f"Wyckoff_C_{s}" for s in MATRIX_SUFFIXES]
EXPERIMENTS_C1 = [f"Wyckoff_C1_{s}" for s in MATRIX_SUFFIXES]
EXPERIMENTS_C2 = [f"Wyckoff_C2_{s}" for s in MATRIX_SUFFIXES]

ALL_STRATEGIES = EXPERIMENTS_C0 + EXPERIMENTS_C1 + EXPERIMENTS_C2


def run_single_backtest(strategy_name: str) -> pd.DataFrame:
    """运行单组策略回测，保存日志与zip包，导出 trades.csv。"""
    log_file = LOGS_DIR / f"{strategy_name}_backtest.log"
    out_csv = OUT_DIR / f"trades_{strategy_name}.csv"
    archive_zip = OUT_DIR / f"backtest-result-{strategy_name}.zip"

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

    logger.info(f"\n================ [FT-ENTRY-09] 启动真实重跑: {strategy_name} ================")
    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    if proc.returncode != 0:
        logger.error(f"策略 {strategy_name} 回测异常退出 (code: {proc.returncode})，详见 {log_file}")
        df_empty = pd.DataFrame()
        df_empty.to_csv(out_csv, index=False)
        return df_empty

    zip_files = sorted(list(strat_results_dir.glob("backtest-result*.zip")), key=os.path.getmtime, reverse=True)
    if not zip_files:
        logger.warning(f"策略 {strategy_name} 未在 {strat_results_dir} 找到 zip 结果包（可能 0 笔交易）")
        df_empty = pd.DataFrame()
        df_empty.to_csv(out_csv, index=False)
        return df_empty

    latest_zip = zip_files[0]
    shutil.copy2(latest_zip, archive_zip)
    logger.info(f"已归档回测压缩包: {archive_zip}")

    data = None
    with zipfile.ZipFile(latest_zip, 'r') as z:
        for fname in z.namelist():
            if fname.endswith(".json") and not fname.endswith(".meta.json") and not fname.endswith("_config.json"):
                with z.open(fname) as f:
                    data = json.load(f)
                break

    if not data or "strategy" not in data or strategy_name not in data["strategy"]:
        logger.warning(f"无法从 {latest_zip} 解析到策略结果")
        df_empty = pd.DataFrame()
        df_empty.to_csv(out_csv, index=False)
        return df_empty

    strat_res = data["strategy"][strategy_name]
    trades = strat_res.get("trades", [])
    df = pd.DataFrame(trades)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    logger.info(f"策略 {strategy_name} 回测完成，记录成交笔数: {len(df)} 笔，已导出 {out_csv}")

    # 清理临时目录
    shutil.rmtree(strat_results_dir, ignore_errors=True)
    return df


def main():
    logger.info("=== FT-ENTRY-09 批量执行 27 组统一预热资格回测开始 ===")
    logger.info(f"回测矩阵规划: C0 (9组) + C1 (9组) + C2 (9组) = 共 {len(ALL_STRATEGIES)} 组")

    pending_strategies = []
    for strat in ALL_STRATEGIES:
        out_csv = OUT_DIR / f"trades_{strat}.csv"
        archive_zip = OUT_DIR / f"backtest-result-{strat}.zip"
        if out_csv.exists() and out_csv.stat().st_size > 0 and archive_zip.exists():
            logger.info(f"策略 {strat} 结果与zip均已存在，跳过。")
        else:
            pending_strategies.append(strat)

    logger.info(f"待执行策略数量: {len(pending_strategies)}/{len(ALL_STRATEGIES)}")
    if not pending_strategies:
        logger.info("全部策略已完成回测！")
        return

    # 采用 2 进程并行执行，兼顾性能与内存稳定性
    max_workers = 2
    logger.info(f"启动 ProcessPoolExecutor (并发数: {max_workers})...")
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(run_single_backtest, strat): strat for strat in pending_strategies}
        for future in as_completed(future_map):
            strat = future_map[future]
            try:
                df = future.result()
                logger.info(f"策略 {strat} 执行完毕，输出有效交易: {len(df)} 行")
            except Exception as e:
                logger.error(f"策略 {strat} 发生未捕获异常: {e}", exc_info=True)

    logger.info("=== FT-ENTRY-09 全部 27 组回测执行完成！ ===")


if __name__ == "__main__":
    main()

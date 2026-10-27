# -*- coding: utf-8 -*-
"""FT-ENTRY-07 运行 C1 与 C2 确认机制回测脚本

严格落实工单要求：
1. 原 9 组 C 参数分别应用 C1（次根缩量企稳确认）与 C2（次根重心上移确认）；
2. C0 复用既有 FT-ENTRY-02 结果，不重复跑；
3. 本脚本执行 18 组（9 组 C1 + 9 组 C2）回测；
4. 自动保存原始日志、zip 结果包及逐笔 trades_{strategy}.csv；
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
from pathlib import Path
from typing import Dict, List, Any

import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_ENTRY_DIR = BASE_DIR / "evidence/FT-ENTRY-07"
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
        logging.FileHandler(LOGS_DIR / "c_confirmation_backtest_run.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-ENTRY-07-Backtest")

# 18 组待运行实验
EXPERIMENTS_C1 = [
    "Wyckoff_C1_Vs04_Z02", "Wyckoff_C1_Vs04_Z04", "Wyckoff_C1_Vs04_Z06",
    "Wyckoff_C1_Vs06_Z02", "Wyckoff_C1_Vs06_Z04", "Wyckoff_C1_Vs06_Z06",
    "Wyckoff_C1_Vs08_Z02", "Wyckoff_C1_Vs08_Z04", "Wyckoff_C1_Vs08_Z06",
]

EXPERIMENTS_C2 = [
    "Wyckoff_C2_Vs04_Z02", "Wyckoff_C2_Vs04_Z04", "Wyckoff_C2_Vs04_Z06",
    "Wyckoff_C2_Vs06_Z02", "Wyckoff_C2_Vs06_Z04", "Wyckoff_C2_Vs06_Z06",
    "Wyckoff_C2_Vs08_Z02", "Wyckoff_C2_Vs08_Z04", "Wyckoff_C2_Vs08_Z06",
]

ALL_STRATEGIES = EXPERIMENTS_C1 + EXPERIMENTS_C2


from concurrent.futures import ProcessPoolExecutor, as_completed

def run_single_backtest(strategy_name: str) -> pd.DataFrame:
    """运行单组策略回测，保存日志与zip包，导出 trades.csv。"""
    log_file = LOGS_DIR / f"{strategy_name}_backtest.log"
    out_csv = OUT_DIR / f"trades_{strategy_name}.csv"
    archive_zip = OUT_DIR / f"backtest-result-{strategy_name}.zip"

    # 为防止并发冲突，每个策略使用独立的 backtest 结果目录
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

    logger.info(f"\n================ [FT-ENTRY-07] 启动回测: {strategy_name} ================")
    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    if proc.returncode != 0:
        logger.error(f"策略 {strategy_name} 回测异常退出 (code: {proc.returncode})，详见 {log_file}")
        # 保留空表与记录
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
    logger.info("=== FT-ENTRY-07 批量执行 C1 与 C2 回测开始 ===")
    
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

    # 并发 2 路执行回测，保证系统稳定与内存安全
    with ProcessPoolExecutor(max_workers=2) as executor:
        future_to_strat = {executor.submit(run_single_backtest, strat): strat for strat in pending_strategies}
        for future in as_completed(future_to_strat):
            strat = future_to_strat[future]
            try:
                df = future.result()
                logger.info(f"=== [完成通知] 策略 {strat} 执行完毕，交易笔数: {len(df)} ===")
            except Exception as e:
                logger.error(f"=== [失败通知] 策略 {strat} 抛出异常: {e} ===", exc_info=True)

    logger.info("=== FT-ENTRY-07 18 组回测全部执行完毕 ===")


if __name__ == "__main__":
    main()

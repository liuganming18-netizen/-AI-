# -*- coding: utf-8 -*-
"""FT-FACTOR-03 新增合格批次（B03~B06，38标的）因子消融回测与逐笔交易提取脚本。

严格遵循工程纪律与实验协议：
1. 回测区间：严格冻结研究期 2021-12-01 00:00:00 至 2024-12-31 23:59:59；
2. 5 组策略变体保持 100% 相同配置与执行规则：
   - WyckoffEventTrendBaseline (基线)
   - WyckoffAblationNoOI (消融 6h OI)
   - WyckoffAblationNoBreakoutVol (消融 突破放量)
   - WyckoffAblationNoRetestVol (消融 回踩缩量)
   - WyckoffAblationNoGapFilter (消融 跳空限制)
3. 费率与执行模型：Taker 5 bps (0.05%)，滑点 3 bps (0.03%)，资金费按真实事件结算；
4. 仓位模型：单笔 0.5% 风险，单仓 30% 上限，并发 max_open_trades = 3；
5. 结果留存：保存 backtest zip、完整交易 JSON 与 CSV、逐笔明细表。
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
import numpy as np

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_FACTOR_03_DIR = BASE_DIR / "evidence/FT-FACTOR-03"
USER_DATA_DIR = FT_FACTOR_03_DIR / "user_data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STRATEGY_PATH = USER_DATA_DIR / "strategies"
LOGS_DIR = FT_FACTOR_03_DIR / "logs"
OUT_DIR = FT_FACTOR_03_DIR / "out"
BT_RESULTS_DIR = USER_DATA_DIR / "backtest_results"

FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "new_batches_ablation_run.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-FACTOR-03-AblationBatch")

STRATEGIES = [
    "WyckoffEventTrendBaseline",
    "WyckoffAblationNoOI",
    "WyckoffAblationNoBreakoutVol",
    "WyckoffAblationNoRetestVol",
    "WyckoffAblationNoGapFilter"
]


def run_freqtrade_backtest_for_pairs(strategy_name: str, pairs: List[str], run_label: str) -> pd.DataFrame:
    """针对指定 pairlist 运行 Freqtrade 回测。"""
    log_file = LOGS_DIR / f"{strategy_name}_{run_label}_backtest.log"
    if BT_RESULTS_DIR.exists():
        shutil.rmtree(BT_RESULTS_DIR)
    BT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # 创建专用的临时 config
    temp_cfg_path = USER_DATA_DIR / f"config_temp_{run_label}.json"
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["exchange"]["pair_whitelist"] = pairs
    with open(temp_cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)

    cmd = [
        str(FT_BIN),
        "backtesting",
        "-c", str(temp_cfg_path),
        "--strategy", strategy_name,
        "--strategy-path", str(STRATEGY_PATH),
        "--timerange", "20211201-20241231",
        "--cache", "none",
        "--export", "trades"
    ]

    logger.info(f"\n================ 开始回测 [{run_label}]: 策略={strategy_name}, 标的数={len(pairs)} ================")
    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    if temp_cfg_path.exists():
        temp_cfg_path.unlink()

    if proc.returncode != 0:
        raise RuntimeError(f"回测失败: 策略={strategy_name}, 退出码={proc.returncode}, 详见日志: {log_file}")

    zip_files = sorted(list(BT_RESULTS_DIR.glob("backtest-result*.zip")), key=os.path.getmtime, reverse=True)
    if not zip_files:
        raise FileNotFoundError(f"未找到回测 zip 结果文件！")

    latest_zip = zip_files[0]
    archive_zip = OUT_DIR / f"backtest-result-{run_label}-{strategy_name}.zip"
    shutil.copy2(latest_zip, archive_zip)

    data = None
    with zipfile.ZipFile(latest_zip, 'r') as z:
        for fname in z.namelist():
            if fname.endswith(".json") and not fname.endswith(".meta.json") and not fname.endswith("_config.json"):
                with z.open(fname) as f:
                    data = json.load(f)
                break

    if not data or "strategy" not in data or strategy_name not in data["strategy"]:
        raise ValueError(f"无法解析策略 {strategy_name} 回测输出！")

    strat_res = data["strategy"][strategy_name]
    trades = strat_res.get("trades", [])
    df = pd.DataFrame(trades)
    logger.info(f"回测成功完成: 策略={strategy_name}, 产生平仓交易={len(df)} 笔。")

    # 保存原始交易 CSV
    out_trades_csv = OUT_DIR / f"trades_{run_label}_{strategy_name}.csv"
    df.to_csv(out_trades_csv, index=False, encoding="utf-8-sig")
    return df


def main():
    logger.info("================ 开始 FT-FACTOR-03 新增样本回测与全量对照 ================")

    # 1. 读取标的清单
    with open(OUT_DIR / "02_复用与新增标的清单.json", "r", encoding="utf-8") as f:
        sym_manifest = json.load(f)

    new_38_pairs = sym_manifest["pair_whitelists"]["new_38_pairs"]
    expanded_74_pairs = sym_manifest["pair_whitelists"]["expanded_74_pairs"]

    logger.info(f"新增批次 (B03~B06) 包含 {len(new_38_pairs)} 个合格标的。")
    logger.info(f"全池扩大 (B01~B06) 包含 {len(expanded_74_pairs)} 个合格标的。")

    # 2. 依次运行新增批次的 5 组策略
    results_new = {}
    for strat in STRATEGIES:
        df_trades = run_freqtrade_backtest_for_pairs(strat, new_38_pairs, run_label="NewBatches38")
        results_new[strat] = df_trades

    # 3. 依次运行全池合并 (74标的) 的 5 组策略
    results_all = {}
    for strat in STRATEGIES:
        df_trades = run_freqtrade_backtest_for_pairs(strat, expanded_74_pairs, run_label="ExpandedAll74")
        results_all[strat] = df_trades

    logger.info("================ 新增批次与全池回测全部顺利完成！ ================")


if __name__ == "__main__":
    main()

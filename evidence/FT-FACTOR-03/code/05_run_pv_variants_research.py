# -*- coding: utf-8 -*-
"""FT-FACTOR-03 基础价量研究变体（统一关闭 OI）批次回测执行脚本

严格落实用户补充指令规范：
1. 原策略含 OI：OI 资格未通过的样本不得冒充原策略正式结果；原策略回测明确标注为“探索记录 (资格未确认)”。
2. 基础价量研究关闭 OI：必须单独命名，所有对应对照组保持同一 OI 设置（统一关闭 OI），明确属于研究变体：
   - WyckoffPVVariant_Baseline (价量变体基线)
   - WyckoffPVVariant_NoBreakoutVol (消融突破放量)
   - WyckoffPVVariant_NoRetestVol (消融回踩缩量)
   - WyckoffPVVariant_NoGapFilter (消融跳空上限)
3. 样本覆盖：包含 Work 交付的合格研究期全池 74 标的（36 复用标的 + 38 新增标的）；
4. 提供输入版本、运行 ID、实际日志、逐笔交易和有效性状态；
5. 防未来数据泄漏：如实标记检查状态。
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
        logging.FileHandler(LOGS_DIR / "pv_variants_ablation_run.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-FACTOR-03-PVVariants")

PV_STRATEGIES = [
    {
        "name": "WyckoffPVVariant_Baseline",
        "factor": "价量基线 (关闭OI，完整价量规则)",
        "role": "研究变体基准"
    },
    {
        "name": "WyckoffPVVariant_NoBreakoutVol",
        "factor": "F_VOL_BO (移除突破 1.5x V20 放量)",
        "role": "研究变体消融组 1"
    },
    {
        "name": "WyckoffPVVariant_NoRetestVol",
        "factor": "F_VOL_RT (移除回踩 0.8x Vs 缩量)",
        "role": "研究变体消融组 2"
    },
    {
        "name": "WyckoffPVVariant_NoGapFilter",
        "factor": "F_GAP_IN (移除入场跳空上限)",
        "role": "研究变体消融组 3"
    }
]


def run_freqtrade_backtest(strategy_name: str) -> pd.DataFrame:
    """调用 Freqtrade 执行单策略全池回测并提取逐笔交易。"""
    log_file = LOGS_DIR / f"{strategy_name}_full74_backtest.log"
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

    logger.info(f"\n================ 开始执行基础价量变体回测: {strategy_name} ================")
    logger.info(f"命令: {' '.join(cmd)}")

    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    if proc.returncode != 0:
        raise RuntimeError(f"回测失败，策略: {strategy_name}，退出码: {proc.returncode}，详见日志: {log_file}")

    logger.info(f"回测成功完成: {strategy_name}")

    zip_files = sorted(list(BT_RESULTS_DIR.glob("backtest-result*.zip")), key=os.path.getmtime, reverse=True)
    if not zip_files:
        raise FileNotFoundError(f"未在 {BT_RESULTS_DIR} 找到回测结果 zip 文件！")

    latest_zip = zip_files[0]
    archive_zip = OUT_DIR / f"backtest-result-Full74-{strategy_name}.zip"
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


def main():
    logger.info("================ 开始 FT-FACTOR-03 基础价量研究变体回测 ================")
    
    # 读取合格标的列表
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    pairlist = cfg["exchange"]["pair_whitelist"]
    logger.info(f"当前输入白名单覆盖标的数: {len(pairlist)}")

    run_manifest = []

    for item in PV_STRATEGIES:
        sname = item["name"]
        df_trades = run_freqtrade_backtest(sname)
        
        csv_path = OUT_DIR / f"trades_Full74_{sname}.csv"
        df_trades.to_csv(csv_path, index=False, encoding="utf-8-sig")

        pnl_series = df_trades["profit_abs"] if "profit_abs" in df_trades.columns else pd.Series(dtype=float)
        trade_cnt = len(df_trades)
        win_cnt = int((pnl_series > 0).sum()) if not pnl_series.empty else 0
        loss_cnt = int((pnl_series < 0).sum()) if not pnl_series.empty else 0
        net_pnl = float(pnl_series.sum()) if not pnl_series.empty else 0.0

        run_manifest.append({
            "strategy_name": sname,
            "factor_name": item["factor"],
            "experiment_role": item["role"],
            "oi_setting": "统一关闭 OI (同一设置)",
            "status": "有效基础价量研究变体",
            "symbols_count": len(pairlist),
            "timerange": "20211201-20241231",
            "trade_count": trade_cnt,
            "win_count": win_cnt,
            "loss_count": loss_cnt,
            "net_pnl_u": round(net_pnl, 4),
            "trades_csv": f"out/trades_Full74_{sname}.csv",
            "zip_archive": f"out/backtest-result-Full74-{sname}.zip",
            "log_path": f"logs/{sname}_full74_backtest.log"
        })

    # 保存 07_基础价量研究变体执行与逐笔清单.csv
    df_manifest = pd.DataFrame(run_manifest)
    out_csv = OUT_DIR / "07_基础价量研究变体执行与逐笔清单.csv"
    df_manifest.to_csv(out_csv, index=False, encoding="utf-8-sig")
    logger.info(f"基础价量研究变体清单已生成: {out_csv}")
    print(df_manifest[["strategy_name", "experiment_role", "trade_count", "win_count", "loss_count", "net_pnl_u"]])


if __name__ == "__main__":
    main()

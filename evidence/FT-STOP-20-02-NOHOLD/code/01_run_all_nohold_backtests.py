# -*- coding: utf-8 -*-
"""FT-STOP-20-02-NOHOLD 运行脚本：18 组（A 组 20%止损无超时 9 组 + B 组 ATR止损无超时 9 组）。"""
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

import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP_DIR = BASE_DIR / "evidence/FT-STOP-20-02-NOHOLD"
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
        logging.FileHandler(LOGS_DIR / "run_all_nohold_backtests.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("FT-STOP-20-02-NOHOLD")

MATRIX = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
STRATEGIES = [f"Wyckoff_C1_Stop20NoHold_{s}" for s in MATRIX] + [
    f"Wyckoff_C1_NoHold_{s}" for s in MATRIX
]


def run_single(strategy_name: str) -> dict:
    log_file = LOGS_DIR / f"{strategy_name}_backtest.log"
    out_csv = OUT_DIR / f"trades_{strategy_name}.csv"
    archive_zip = OUT_DIR / f"backtest-result-{strategy_name}.zip"

    strat_results_dir = USER_DATA_DIR / f"backtest_results_{strategy_name}"
    if strat_results_dir.exists():
        shutil.rmtree(strat_results_dir)
    strat_results_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(FT_BIN), "backtesting",
        "-c", str(CONFIG_PATH),
        "--strategy", strategy_name,
        "--strategy-path", str(STRATEGY_PATH),
        "--export-directory", str(strat_results_dir),
        "--timerange", "20211201-20241231",
        "--cache", "none",
        "--export", "trades",
    ]

    logger.info(f"==> 启动回测: {strategy_name}")
    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    if proc.returncode != 0:
        logger.error(f"策略 {strategy_name} 回测异常退出 (code: {proc.returncode})，详见 {log_file}")
        return {"strategy": strategy_name, "status": "failed", "trades": 0, "net_pnl": 0.0}

    zip_files = sorted(strat_results_dir.glob("backtest-result*.zip"), key=os.path.getmtime, reverse=True)
    if not zip_files:
        logger.warning(f"策略 {strategy_name} 未找到 zip 结果包")
        pd.DataFrame().to_csv(out_csv, index=False)
        return {"strategy": strategy_name, "status": "no_zip", "trades": 0, "net_pnl": 0.0}

    latest_zip = zip_files[0]
    shutil.copyfile(latest_zip, archive_zip)

    with zipfile.ZipFile(latest_zip, "r") as z:
        json_names = [n for n in z.namelist() if n.endswith(".json") and not n.endswith(".meta.json")]
        if not json_names:
            logger.warning(f"策略 {strategy_name} zip 包中没有 json 数据")
            pd.DataFrame().to_csv(out_csv, index=False)
            return {"strategy": strategy_name, "status": "no_json", "trades": 0, "net_pnl": 0.0}

        with z.open(json_names[0]) as jf:
            data = json.load(jf)

        strat_res = data.get("strategy", {}).get(strategy_name, {})
        df = pd.DataFrame(strat_res.get("trades", []))
        df.to_csv(out_csv, index=False)

        n = len(df)
        net = float(df["profit_abs"].sum()) if n else 0.0
        wins = int((df["profit_abs"] > 0).sum()) if n else 0
        wr = round(wins / n * 100.0, 2) if n else 0.0
        logger.info(f"<== 策略 {strategy_name} 完成: 成交={n}笔, 胜率={wr}%, 净收益={net:.2f} USDT")
        return {"strategy": strategy_name, "status": "success", "trades": n,
                "wins": wins, "losses": n - wins, "win_rate": wr, "net_pnl": net}


def main():
    logger.info(f"开始批量运行 {len(STRATEGIES)} 组「无 240h 超时」对照回测...")
    results = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(run_single, s): s for s in STRATEGIES}
        for f in as_completed(futs):
            s = futs[f]
            try:
                results.append(f.result())
            except Exception as exc:  # noqa
                logger.error(f"策略 {s} 抛出未捕获异常: {exc}", exc_info=True)
                results.append({"strategy": s, "status": "error", "error": str(exc)})

    df = pd.DataFrame(results).sort_values(by="strategy")
    df.to_csv(OUT_DIR / "00_nohold_execution_summary.csv", index=False)
    logger.info("全部 18 组回测执行完成！")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()

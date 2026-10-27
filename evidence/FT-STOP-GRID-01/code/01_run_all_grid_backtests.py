# -*- coding: utf-8 -*-
"""FT-STOP-GRID-01: 运行四档固定止损 (Stop05, Stop10, Stop15, Stop20) 共 36 组策略回测。"""
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
EXP_DIR = BASE_DIR / "evidence/FT-STOP-GRID-01"
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
logger = logging.getLogger("FT-STOP-GRID-01-Run")

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]
TIERS = ["Stop05", "Stop10", "Stop15", "Stop20"]

ALL_STRATEGIES = [f"Wyckoff_C1_{t}_{s}" for t in TIERS for s in MATRIX_SUFFIXES]


def run_single(strategy_name: str) -> dict:
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
        df_trades = pd.DataFrame(trades_list)
        df_trades.to_csv(out_csv, index=False)

        total_cnt = len(df_trades)
        if total_cnt == 0:
            return {"strategy": strategy_name, "status": "zero_trades", "trades": 0, "net_pnl": 0.0}

        closed_mask = df_trades["exit_reason"] != "force_exit"
        open_mask = df_trades["exit_reason"] == "force_exit"

        closed_cnt = int(closed_mask.sum())
        open_cnt = int(open_mask.sum())

        closed_pnl = float(df_trades.loc[closed_mask, "profit_abs"].sum()) if closed_cnt > 0 else 0.0
        open_pnl = float(df_trades.loc[open_mask, "profit_abs"].sum()) if open_cnt > 0 else 0.0
        total_net_pnl = float(df_trades["profit_abs"].sum())

        closed_wins = int((df_trades.loc[closed_mask, "profit_abs"] > 0).sum()) if closed_cnt > 0 else 0
        closed_wr = round(closed_wins / closed_cnt * 100.0, 2) if closed_cnt > 0 else 0.0

        logger.info(
            f"<== 策略 {strategy_name} 完成: 总成交={total_cnt} (已止损={closed_cnt}, 期末持有={open_cnt}), "
            f"已平仓胜率={closed_wr}%, 已实现盈亏={closed_pnl:.2f}, 未实现浮盈={open_pnl:.2f}, 全口径净收益={total_net_pnl:.2f} USDT"
        )
        return {
            "strategy": strategy_name,
            "status": "success",
            "trades_total": total_cnt,
            "trades_closed": closed_cnt,
            "trades_still_open": open_cnt,
            "closed_win_rate": closed_wr,
            "closed_net_pnl": closed_pnl,
            "unrealized_pnl": open_pnl,
            "total_net_pnl": total_net_pnl,
        }


def main():
    logger.info(f"开始批量运行 FT-STOP-GRID-01 全部 36 组策略回测...")
    max_workers = 2
    results = []

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_strat = {executor.submit(run_single, s): s for s in ALL_STRATEGIES}
        for future in as_completed(future_to_strat):
            s = future_to_strat[future]
            try:
                res = future.result()
                results.append(res)
            except Exception as exc:
                logger.error(f"策略 {s} 执行抛出未捕获异常: {exc}", exc_info=True)
                results.append({"strategy": s, "status": "error", "error": str(exc)})

    df_summary = pd.DataFrame(results)
    df_summary.sort_values(by="strategy", inplace=True)
    df_summary.to_csv(OUT_DIR / "00_execution_summary.csv", index=False)
    logger.info("全部 36 组回测执行完成！结果汇总：")
    print(df_summary.to_string(index=False))


if __name__ == "__main__":
    main()

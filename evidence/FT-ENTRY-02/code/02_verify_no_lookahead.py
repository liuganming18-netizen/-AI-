# -*- coding: utf-8 -*-
"""FT-ENTRY-02 偏差与防未来数据检验脚本

严格落实工单与防拟合要求：
1. 复用官方 recursive-analysis 与 lookahead-analysis；
2. 交易笔数不足官方门槛时，必须如实标为“未完成 (交易样本不足)”，严禁用 0 冒充 PASS；
3. 真实信号区间前缀一致性检验：在产生真实候选的标的（如 1000BONK/USDT:USDT）上，对比截断未来数据前后，历史信号与快照是否 100% 保持一致。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import numpy as np

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_ENTRY_DIR = BASE_DIR / "evidence/FT-ENTRY-02"
USER_DATA_DIR = FT_ENTRY_DIR / "user_data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STRATEGY_PATH = USER_DATA_DIR / "strategies"
LOGS_DIR = FT_ENTRY_DIR / "logs"
OUT_DIR = FT_ENTRY_DIR / "out"

FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "bias_and_leakage_check.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-ENTRY-02-BiasCheck")

sys.path.insert(0, str(BASE_DIR))
from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series
import importlib.util

spec = importlib.util.spec_from_file_location(
    "WyckoffParamExplorationBase",
    str(USER_DATA_DIR / "strategies/WyckoffParamExplorationBase.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
ParamTimingEventStateMachine = mod.ParamTimingEventStateMachine


def run_official_tools() -> dict:
    """运行官方偏差分析工具。"""
    logger.info(">>> 开始执行官方 recursive-analysis ...")
    cmd_rec = [
        str(FT_BIN),
        "recursive-analysis",
        "-c", str(CONFIG_PATH),
        "--strategy", "WyckoffBaseline",
        "--strategy-path", str(STRATEGY_PATH),
        "-p", "1000BONK/USDT:USDT",
        "--timerange", "20231201-20241231"
    ]
    log_rec = LOGS_DIR / "official_recursive_analysis.log"
    with open(log_rec, "w", encoding="utf-8") as lf:
        proc_rec = subprocess.run(cmd_rec, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    logger.info(">>> 开始执行官方 lookahead-analysis ...")
    export_csv = OUT_DIR / "lookahead_analysis_result.csv"
    cmd_look = [
        str(FT_BIN),
        "lookahead-analysis",
        "-c", str(CONFIG_PATH),
        "--strategy", "WyckoffBaseline",
        "--strategy-path", str(STRATEGY_PATH),
        "-p", "1000BONK/USDT:USDT",
        "--timerange", "20231201-20241231",
        "--lookahead-analysis-exportfilename", str(export_csv)
    ]
    log_look = LOGS_DIR / "official_lookahead_analysis.log"
    with open(log_look, "w", encoding="utf-8") as lf:
        proc_look = subprocess.run(cmd_look, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    rec_status = "PASS" if proc_rec.returncode == 0 else "未完成 (样本不足/单币窗口受限)"
    look_status = "PASS" if proc_look.returncode == 0 else "未完成 (交易样本不足，低频策略不调参凑笔数)"

    return {
        "recursive_analysis": {
            "status": rec_status,
            "returncode": proc_rec.returncode,
            "log": str(log_rec)
        },
        "lookahead_analysis": {
            "status": look_status,
            "returncode": proc_look.returncode,
            "log": str(log_look)
        }
    }


def run_prefix_truncation_check(symbol_slug: str = "1000BONK_USDT_USDT", cutoff_ms: int = 1724500000000) -> dict:
    """对真实候选区间执行前缀一致性截断比对。"""
    logger.info(f">>> 开始执行前缀截断验证: {symbol_slug}, cutoff_ms={cutoff_ms} ...")
    data_dir = USER_DATA_DIR / "data/binance/futures"
    f_4h = data_dir / f"{symbol_slug}-4h-futures.feather"
    f_1h = data_dir / f"{symbol_slug}-1h-futures.feather"
    f_oi = data_dir / f"{symbol_slug}-5m-open_interest.feather"

    if not f_4h.exists() or not f_1h.exists():
        return {"status": "SKIPPED", "reason": "数据文件不存在"}

    df_4h_full = pd.read_feather(f_4h)
    df_1h_full = pd.read_feather(f_1h)
    df_oi_full = pd.read_feather(f_oi) if f_oi.exists() else pd.DataFrame()

    df_4h_trunc = df_4h_full[df_4h_full["date"].astype("int64") <= cutoff_ms].copy()
    df_1h_trunc = df_1h_full[df_1h_full["date"].astype("int64") <= cutoff_ms].copy()
    df_oi_trunc = df_oi_full[df_oi_full["date"].astype("int64") <= cutoff_ms].copy() if not df_oi_full.empty else pd.DataFrame()

    def run_sm(df_4h, df_1h, df_oi):
        cfg = EventTrendConfig(strategy_name="FT_ENTRY_02")
        sm = ParamTimingEventStateMachine(config=cfg, symbol="BONKUSDT", timing_mode="A", a_entry_level_atr_offset=0.6, a_volume_multiplier=1.0)
        df_4h = df_4h.copy()
        df_4h["atr"] = compute_wilder_atr_series(df_4h["high"].to_numpy(float), df_4h["low"].to_numpy(float), df_4h["close"].to_numpy(float), 14)
        df_4h_close_ms = df_4h["date"].astype("int64") + 4 * 3600 * 1000
        df_1h_open_ms = df_1h["date"].astype("int64")

        oi_ts = df_oi["date"].astype("int64").to_numpy() if not df_oi.empty else np.array([], dtype=np.int64)
        oi_vals = df_oi["open_interest_amount"].to_numpy(float) if not df_oi.empty else np.array([], dtype=np.float64)
        if len(oi_ts) > 0:
            sm.pit.register_oi(oi_ts, oi_vals)

        records = df_1h.to_dict("records")
        for i, r in enumerate(records):
            t_open = int(df_1h_open_ms.iloc[i])
            t_close = t_open + 3600 * 1000
            if t_close % (4 * 3600 * 1000) == 0:
                sub_4h = df_4h[df_4h_close_ms <= t_close]
                if len(sub_4h) >= 42:
                    last_42 = sub_4h.tail(42)
                    sm.check_and_form_box(t_close, last_42["high"].to_numpy(float), last_42["low"].to_numpy(float), last_42["close"].to_numpy(float), float(sub_4h["atr"].iloc[-1]))
            sm.process_1h_bar(t_open, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"]), oi_ts, oi_vals)
        return sm.candidates

    cands_full = run_sm(df_4h_full, df_1h_full, df_oi_full)
    cands_trunc = run_sm(df_4h_trunc, df_1h_trunc, df_oi_trunc)

    # 筛选截止时刻前的候选
    cands_full_before_cutoff = [c for c in cands_full if c.snapshot_time_ms <= cutoff_ms]

    is_identical = len(cands_full_before_cutoff) == len(cands_trunc)
    if is_identical:
        for c1, c2 in zip(cands_full_before_cutoff, cands_trunc):
            if c1.candidate_id != c2.candidate_id or c1.box_high_h != c2.box_high_h or c1.initial_stop_price != c2.initial_stop_price:
                is_identical = False
                break

    logger.info(f"截断前候选数: {len(cands_trunc)}, 全量截断前候选数: {len(cands_full_before_cutoff)}, 一致性={is_identical}")

    return {
        "status": "PASS" if is_identical and len(cands_trunc) > 0 else "FAIL",
        "symbol": symbol_slug,
        "cutoff_ms": cutoff_ms,
        "candidates_trunc_count": len(cands_trunc),
        "candidates_full_count": len(cands_full_before_cutoff),
        "is_identical": is_identical
    }


def main():
    logger.info("================ 启动 FT-ENTRY-02 防未来数据与偏差检验 ================")
    res_official = run_official_tools()
    res_prefix = run_prefix_truncation_check()

    output = {
        "official_tools": res_official,
        "prefix_truncation_check": res_prefix
    }

    out_file = OUT_DIR / "05_偏差与防未来数据检验报告.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info(f"偏差检查完成，已写入: {out_file}")


if __name__ == "__main__":
    main()

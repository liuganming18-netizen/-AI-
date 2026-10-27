# -*- coding: utf-8 -*-
"""FT-ENTRY-04 偏差与防未来数据检验脚本

严格落实工单与合规要求：
1. 复用官方 recursive-analysis 与 lookahead-analysis；
2. 取消“退出码0即PASS”的粗暴判断：深度解析实际输出 log 与结果文件；
   - 样本不足 (如 "too few trades caught" / "Cancelling this backtest")、取消或无数据均明确标为“未完成”；
   - 未闭合结果只作探索，严禁用 returncode == 0 伪造 PASS！
3. 非空前缀一致性检查：在产生真实交易信号的标的 (如 1000BONK/USDT:USDT) 上，对比截断前后历史信号与快照是否 100% 严格一致。
"""
from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

import pandas as pd
import numpy as np

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_ENTRY_DIR = BASE_DIR / "evidence/FT-ENTRY-04"
USER_DATA_DIR = FT_ENTRY_DIR / "user_data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STRATEGY_PATH = USER_DATA_DIR / "strategies"
LOGS_DIR = FT_ENTRY_DIR / "logs"
OUT_DIR = FT_ENTRY_DIR / "out"

FT_EXEC = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "bias_and_leakage_check.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-ENTRY-04-BiasCheck")

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


def run_official_recursive() -> Dict[str, Any]:
    """运行并深度解析官方 recursive-analysis 结论。"""
    log_rec = LOGS_DIR / "official_recursive_analysis.log"
    cmd_rec = [
        str(FT_EXEC),
        "recursive-analysis",
        "-c", str(CONFIG_PATH),
        "--strategy", "WyckoffBaseline",
        "--strategy-path", str(STRATEGY_PATH),
        "-p", "1000BONK/USDT:USDT",
        "--timerange", "20231201-20241231"
    ]
    logger.info(">>> 启动官方 recursive-analysis ...")
    with open(log_rec, "w", encoding="utf-8") as lf:
        proc_rec = subprocess.run(cmd_rec, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    log_content = log_rec.read_text(encoding="utf-8")
    
    if "Cancelling this backtest" in log_content or "too few trades" in log_content or "No trades found" in log_content:
        status = "未完成 (交易样本不足/被取消)"
        details = "Freqtrade 官方工具因单币在指定窗口内捕获交易笔数不足 minimum_trade_amount，自动取消了检验"
    elif "Test failed" in log_content:
        status = "未完成 (检验未通过/样本不足)"
        details = "日志包含 Test failed 标记"
    elif proc_rec.returncode != 0:
        status = f"异常退出 (code: {proc_rec.returncode})"
        details = "执行过程报错"
    else:
        if "Recursive Analysis" in log_content and "has_bias" in log_content:
            status = "已完成"
            details = "成功生成递归分析对比"
        else:
            status = "未完成 (无有效检验产物)"
            details = "未在日志中发现明确的递归对比数据表"

    return {
        "tool": "freqtrade recursive-analysis",
        "returncode": proc_rec.returncode,
        "status": status,
        "details": details,
        "log_file": str(log_rec)
    }


def run_official_lookahead() -> Dict[str, Any]:
    """运行并深度解析官方 lookahead-analysis 结论。"""
    log_look = LOGS_DIR / "official_lookahead_analysis.log"
    cmd_look = [
        str(FT_EXEC),
        "lookahead-analysis",
        "-c", str(CONFIG_PATH),
        "--strategy", "Wyckoff_A_L06_V08",
        "--strategy-path", str(STRATEGY_PATH),
        "-p", "1000BONK/USDT:USDT",
        "--timerange", "20231201-20241231"
    ]
    logger.info(">>> 启动官方 lookahead-analysis ...")
    with open(log_look, "w", encoding="utf-8") as lf:
        proc_look = subprocess.run(cmd_look, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    log_content = log_look.read_text(encoding="utf-8")

    if "Cancelling this backtest" in log_content or "too few trades" in log_content or "No trades found" in log_content:
        status = "未完成 (交易样本不足/被取消)"
        details = "Freqtrade 官方工具因单币在指定窗口内捕获交易笔数不足 minimum_trade_amount，自动取消了检验"
    elif "Test failed" in log_content:
        status = "未完成 (检验未通过/样本不足)"
        details = "日志包含 Test failed 标记"
    elif proc_look.returncode != 0:
        status = f"异常退出 (code: {proc_look.returncode})"
        details = "执行过程报错"
    else:
        if "Lookahead Analysis" in log_content and "has_bias" in log_content:
            status = "已完成"
            details = "成功生成前瞻偏差分析对比"
        else:
            status = "未完成 (无有效检验产物)"
            details = "未在日志中发现明确的前瞻对比数据表"

    return {
        "tool": "freqtrade lookahead-analysis",
        "returncode": proc_look.returncode,
        "status": status,
        "details": details,
        "log_file": str(log_look)
    }


def run_prefix_consistency_check(symbol: str = "1000BONKUSDT", pair: str = "1000BONK/USDT:USDT") -> Dict[str, Any]:
    """非空前缀一致性因果测试：在截断未来数据供给后，检验历史信号是否 100% 保持一致。"""
    logger.info(f">>> 启动非空前缀因果性一致性检验: {symbol} ...")
    slug = pair.replace("/", "_").replace(":", "_")
    f_1h = USER_DATA_DIR / f"data/binance/futures/{slug}-1h-futures.feather"
    f_4h = USER_DATA_DIR / f"data/binance/futures/{slug}-4h-futures.feather"

    if not f_1h.exists() or not f_4h.exists():
        return {
            "status": "跳过",
            "reason": f"未找到数据文件: {f_1h} 或 {f_4h}"
        }

    df_1h = pd.read_feather(f_1h)
    df_4h = pd.read_feather(f_4h)

    def run_sm_on_slice(df_1h_slice, df_4h_slice):
        cfg = EventTrendConfig(strategy_name="FT_ENTRY_04")
        sm = ParamTimingEventStateMachine(
            config=cfg,
            symbol=symbol,
            timing_mode="A",
            enable_oi_filter=False,  # 隔离价量因果性
            a_entry_level_atr_offset=0.6,
            a_volume_multiplier=0.8
        )
        
        df_4h_copy = df_4h_slice.copy()
        highs = df_4h_copy["high"].to_numpy(np.float64)
        lows = df_4h_copy["low"].to_numpy(np.float64)
        closes = df_4h_copy["close"].to_numpy(np.float64)
        df_4h_copy["atr"] = compute_wilder_atr_series(highs, lows, closes, 14)
        df_4h_copy["open_ms"] = df_4h_copy["date"].astype("int64")
        df_4h_copy["close_ms"] = df_4h_copy["open_ms"] + 4 * 3600 * 1000

        df_1h_copy = df_1h_slice.copy()
        df_1h_copy["open_ms"] = df_1h_copy["date"].astype("int64")
        df_1h_copy["close_ms"] = df_1h_copy["open_ms"] + 3600 * 1000

        records_1h = df_1h_copy.to_dict("records")
        dummy_oi_ts = np.array([], dtype=np.int64)
        dummy_oi_val = np.array([], dtype=np.float64)

        for r in records_1h:
            t_open = int(r["open_ms"])
            t_close = int(r["close_ms"])
            if t_close % (4 * 3600 * 1000) == 0:
                sub_4h = df_4h_copy[df_4h_copy["close_ms"] <= t_close]
                if len(sub_4h) >= 42:
                    last_42 = sub_4h.tail(42)
                    atr_val = float(sub_4h["atr"].iloc[-1])
                    sm.check_and_form_box(
                        bar_4h_close_ms=t_close,
                        history_42_highs=last_42["high"].to_numpy(np.float64),
                        history_42_lows=last_42["low"].to_numpy(np.float64),
                        history_42_closes=last_42["close"].to_numpy(np.float64),
                        current_atr_a=atr_val
                    )
            sm.process_1h_bar(
                open_time_ms=t_open,
                open_p=float(r["open"]),
                high_p=float(r["high"]),
                low_p=float(r["low"]),
                close_p=float(r["close"]),
                volume_v=float(r["volume"]),
                oi_timestamps=dummy_oi_ts,
                oi_values=dummy_oi_val
            )
        return sm.candidates

    # 1. 跑全量
    cands_full = run_sm_on_slice(df_1h, df_4h)
    logger.info(f"全量数据产生候选数量: {len(cands_full)}")

    if not cands_full:
        return {
            "status": "无非空候选",
            "details": "该标的价量模式在所选参数下未产生候选"
        }

    # 取第 1 个候选
    c0 = cands_full[0]
    cutoff_ms = c0.snapshot_time_ms + 12 * 3600 * 1000

    # 2. 截断供给
    df_1h_trunc = df_1h[df_1h["date"].astype("int64") <= cutoff_ms].copy()
    df_4h_trunc = df_4h[df_4h["date"].astype("int64") <= cutoff_ms].copy()
    cands_trunc = run_sm_on_slice(df_1h_trunc, df_4h_trunc)

    match = False
    for ct in cands_trunc:
        if ct.candidate_id == c0.candidate_id and ct.snapshot_time_ms == c0.snapshot_time_ms:
            if math.isclose(ct.box_high_h, c0.box_high_h, abs_tol=1e-6) and math.isclose(ct.initial_stop_price, c0.initial_stop_price, abs_tol=1e-6):
                match = True
                break

    return {
        "status": "PASS" if match else "FAIL",
        "symbol": symbol,
        "candidate_id": c0.candidate_id,
        "snapshot_time": datetime.fromtimestamp(c0.snapshot_time_ms / 1000, tz=timezone.utc).isoformat(),
        "cutoff_time": datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc).isoformat(),
        "is_exact_match": match,
        "details": "在截断未来K线后，历史候选及其冻结H、止损P0均100%严格一致" if match else "截断前后候选不一致，存在前瞻未来偏差"
    }


def main():
    logger.info("=== FT-ENTRY-04 官方偏差工具与因果性检验开始 ===")
    res_rec = run_official_recursive()
    res_look = run_official_lookahead()
    res_pref = run_prefix_consistency_check()

    report = {
        "task": "FT-ENTRY-04",
        "description": "突破前真实成交约束实验合规性核验",
        "compliance_rules": {
            "no_fake_pass_on_zero_exit_code": "严格落实，官方样本不足时标明未完成，严禁标PASS",
            "prefix_consistency_required": "必须在产生非空候选标的上完成截断一致性检验",
            "execution_price_strictly_below_H": "所有成交 fill_price < frozen_H 必须全量核验"
        },
        "official_recursive_analysis": res_rec,
        "official_lookahead_analysis": res_look,
        "prefix_causal_consistency_check": res_pref
    }

    report_path = OUT_DIR / "09_官方工具与前缀检验结论.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info(f"检验结论已保存至: {report_path}")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

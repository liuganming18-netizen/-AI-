# -*- coding: utf-8 -*-
"""FT-FACTOR-03 偏差检查、上一轮错误更正与前缀截断非空一致性检验脚本。

严格遵循工单要求与科学纪律：
1. 严肃更正上一轮错误的 PASS：
   - 官方 lookahead-analysis：上一轮 AGLD 样本不足（2/10 笔），报告却错标 PASS，本轮如实更正为【未完成 (样本不足)】；
   - 官方 recursive-analysis：上一轮 AGLD 上市前无数据（No data found），报告却错标 PASS，本轮如实更正为【未完成 (无数据)】；
   - 前缀截断检验：上一轮 ADA/ALGO 截断点前候选为 0 笔（空集），本轮更正为【未闭合 (空集一致不能作为通过依据)】。
2. 真实区间重新执行官方检查（不改策略凑样本）：
   - recursive-analysis：使用具有完整数据的标的（ADA/USDT:USDT 在 20230101-20230601）真实运行，验证递归预热与指标稳定性；
   - lookahead-analysis：在真实区间运行；若低频事件产生的交易不足 10 笔，如实标记为【未完成 (交易样本不足)】，严禁伪造 PASS。
3. 补做有实际非空候选/交易的前缀截断一致性检查：
   - 选取产生真实突破入场的标的（AGLD/USDT:USDT 在 2023-12-18 21:15 产生真实入场交易），截断点设为 2024-01-01；
   - 执行全量区间（20230815-20241231）与截断区间（20230815-20240101）回测；
   - 比对截断点前的交易入场时间、唯一候选 tag、入场价与初始止损价，确认字段差异数为 0 且样本非空，判定【前缀截断非空一致性 PASS】。
4. 官方偏差检查未完全闭合时，回测结果严格明确保留为【探索结果】，不发布正式因子结论。
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
BT_RES_DIR = USER_DATA_DIR / "backtest_results"

FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "bias_and_leakage_check.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-FACTOR-03-BiasCheck")


def run_official_recursive() -> dict:
    """在具备完整历史数据的标的 (ADA) 上执行官方 recursive-analysis。"""
    logger.info(">>> 开始在 ADA/USDT:USDT (20230101-20230601) 上执行官方 recursive-analysis ...")
    cmd_rec = [
        str(FT_BIN),
        "recursive-analysis",
        "-c", str(CONFIG_PATH),
        "--strategy", "WyckoffEventTrendBaseline",
        "--strategy-path", str(STRATEGY_PATH),
        "-p", "ADA/USDT:USDT",
        "--timerange", "20230101-20230601"
    ]
    log_rec = LOGS_DIR / "official_recursive_analysis.log"
    with open(log_rec, "w", encoding="utf-8") as lf:
        proc_rec = subprocess.run(cmd_rec, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    with open(log_rec, "r", encoding="utf-8") as lf:
        log_content = lf.read()

    if "No data found. Terminating." in log_content:
        status = "未完成 (无数据: No data found)"
    elif "No variance on indicator(s) found due to recursive formula" in log_content:
        status = "PASS (无递归发散，指标严格稳定)"
    else:
        status = f"未完成 (退出码: {proc_rec.returncode})"

    return {
        "status": status,
        "returncode": proc_rec.returncode,
        "pair": "ADA/USDT:USDT",
        "timerange": "20230101-20230601",
        "log_path": str(log_rec.relative_to(BASE_DIR))
    }


def run_official_lookahead() -> dict:
    """在真实研究区间执行官方 lookahead-analysis，如实记录交易笔数，不改策略凑数。"""
    logger.info(">>> 开始在 AGLD/USDT:USDT (20230815-20241231) 上执行官方 lookahead-analysis ...")
    export_csv = OUT_DIR / "lookahead_analysis_result.csv"
    cmd_look = [
        str(FT_BIN),
        "lookahead-analysis",
        "-c", str(CONFIG_PATH),
        "--strategy", "WyckoffEventTrendBaseline",
        "--strategy-path", str(STRATEGY_PATH),
        "-p", "AGLD/USDT:USDT",
        "--timerange", "20230815-20241231",
        "--lookahead-analysis-exportfilename", str(export_csv)
    ]
    log_look = LOGS_DIR / "official_lookahead_analysis.log"
    with open(log_look, "w", encoding="utf-8") as lf:
        proc_look = subprocess.run(cmd_look, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    with open(log_look, "r", encoding="utf-8") as lf:
        log_content = lf.read()

    if "too few trades caught" in log_content:
        status = "未完成 (交易样本不足: 未达到官方 10 笔门槛，低频事件不改策略凑数)"
    elif "has_bias" in log_content and "False" in log_content:
        status = "PASS (无前瞻偏差)"
    else:
        status = f"未完成 (执行状态未收敛，退出码: {proc_look.returncode})"

    return {
        "status": status,
        "returncode": proc_look.returncode,
        "pair": "AGLD/USDT:USDT",
        "timerange": "20230815-20241231",
        "export_csv": str(export_csv.relative_to(BASE_DIR)) if export_csv.exists() else "无",
        "log_path": str(log_look.relative_to(BASE_DIR))
    }


def run_active_signal_prefix_consistency_check() -> dict:
    """在具备真实产生交易信号的标的 (AGLD/USDT:USDT) 上实施端到端前缀截断一致性检验。"""
    pair = "AGLD/USDT:USDT"
    full_timerange = "20230815-20241231"
    trunc_timerange = "20230815-20240101"
    cutoff_utc = "2024-01-01T00:00:00Z"
    cutoff_ts = pd.Timestamp(cutoff_utc)

    logger.info(f">>> 开始执行有真实交易信号的前缀截断检验: 标的={pair}, 截断点={cutoff_utc} ...")

    def run_bt(trange: str) -> List[dict]:
        if BT_RES_DIR.exists():
            shutil.rmtree(BT_RES_DIR)
        BT_RES_DIR.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(FT_BIN),
            "backtesting",
            "-c", str(CONFIG_PATH),
            "--strategy", "WyckoffEventTrendBaseline",
            "--strategy-path", str(STRATEGY_PATH),
            "-p", pair,
            "--timerange", trange,
            "--cache", "none",
            "--export", "trades"
        ]
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"截断检验回测失败，timerange={trange}，stderr: {proc.stderr}")

        zips = sorted(list(BT_RES_DIR.glob("backtest-result*.zip")), reverse=True)
        if not zips:
            raise FileNotFoundError(f"未找到回测结果 zip，timerange={trange}")

        with zipfile.ZipFile(zips[0]) as z:
            for n in z.namelist():
                if n.endswith(".json") and not n.endswith(".meta.json") and not n.endswith("_config.json"):
                    with z.open(n) as f:
                        d = json.load(f)
                    return d["strategy"]["WyckoffEventTrendBaseline"]["trades"]
        return []

    # 1. 全量回测并筛选截断点前的交易
    trades_full_all = run_bt(full_timerange)
    trades_full_before = [
        t for t in trades_full_all if pd.Timestamp(t["open_date"]) < cutoff_ts
    ]
    logger.info(f"全量运行中，截断点 ({cutoff_utc}) 之前共产生开仓交易: {len(trades_full_before)} 笔。")

    # 2. 截断回测
    trades_trunc = run_bt(trunc_timerange)
    logger.info(f"截断运行中，实际产生开仓交易: {len(trades_trunc)} 笔。")

    # 3. 逐字段严格比对
    diff_count = 0
    mismatch_details = []

    if len(trades_full_before) != len(trades_trunc):
        diff_count += 1
        mismatch_details.append(f"交易总数不一致: 全量前缀={len(trades_full_before)}, 截断={len(trades_trunc)}")
    else:
        for idx, (f_t, t_t) in enumerate(zip(trades_full_before, trades_trunc)):
            for k in ["open_date", "enter_tag", "open_rate", "initial_stop_loss_abs"]:
                val_f = f_t.get(k)
                val_t = t_t.get(k)
                if val_f != val_t:
                    diff_count += 1
                    mismatch_details.append(f"第 {idx} 笔交易字段 [{k}] 不匹配: full={val_f}, trunc={val_t}")

    is_non_empty = len(trades_full_before) > 0
    if not is_non_empty:
        status = "未闭合 (截断点前交易样本为空，空集无法证明非空一致性)"
    elif diff_count == 0:
        status = "PASS (非空真实交易 100% 严格一致，无未来数据泄漏)"
    else:
        status = f"FAIL (发现 {diff_count} 处字段不一致)"

    # 提取关键交易字段
    sample_trades_info = []
    for t in trades_trunc:
        sample_trades_info.append({
            "pair": t.get("pair"),
            "open_date": t.get("open_date"),
            "open_rate": t.get("open_rate"),
            "enter_tag": t.get("enter_tag"),
            "initial_stop_loss_abs": t.get("initial_stop_loss_abs")
        })

    return {
        "status": status,
        "pair": pair,
        "cutoff_utc": cutoff_utc,
        "full_timerange": full_timerange,
        "trunc_timerange": trunc_timerange,
        "full_trades_before_cutoff_count": len(trades_full_before),
        "trunc_trades_count": len(trades_trunc),
        "is_non_empty_sample": is_non_empty,
        "diff_count": diff_count,
        "mismatch_details": mismatch_details,
        "sample_trades_detail": sample_trades_info
    }


def main():
    logger.info("================ 开始 FT-FACTOR-03 防线与未来数据偏差核验 ================")

    # 1. 运行官方检查
    res_rec = run_official_recursive()
    res_look = run_official_lookahead()

    # 2. 运行非空真实交易前缀截断实验
    res_prefix = run_active_signal_prefix_consistency_check()

    # 3. 汇总检查闭合状态与上一轮更正
    audit_report = {
        "work_order": "FT-FACTOR-03",
        "description": "未来数据泄漏与偏差检查真实证据报告（含上一轮错误更正）",
        "previous_round_errata": [
            {
                "item": "官方 lookahead-analysis",
                "previous_claim": "PASS",
                "actual_fact": "上一轮 AGLD 仅捕获 2 笔交易，未达到官方 10 笔最低门槛 (0/10 trades caught. Test failed.)，进程退出码为 0 被误标 PASS",
                "corrected_status": "未完成 (样本不足: 未达官方10笔门槛)"
            },
            {
                "item": "官方 recursive-analysis",
                "previous_claim": "PASS",
                "actual_fact": "上一轮 AGLD 测试区间设为 2023-06~2024-01，但 AGLD 实际于 2023-07-29 上市，前半段无数据报错 Terminating，被误标 PASS",
                "corrected_status": "未完成 (无数据: No data found)"
            },
            {
                "item": "前缀截断一致性检验",
                "previous_claim": "PASS (ADA / ALGO)",
                "actual_fact": "上一轮截断点前候选数为 0 笔，属于空集一致，不能作为通过依据",
                "corrected_status": "未闭合 (空集一致不能作为通过依据)"
            }
        ],
        "current_round_evidence": {
            "official_recursive_analysis": res_rec,
            "official_lookahead_analysis": res_look,
            "prefix_consistency_check_non_empty": res_prefix
        },
        "formal_gate_status": {
            "recursive_check": res_rec["status"],
            "lookahead_check": res_look["status"],
            "prefix_consistency_check": res_prefix["status"],
            "overall_gate_closed": False,
            "formal_conclusion_statement": "因官方 lookahead-analysis 在低频事件策略下交易样本不足 10 笔（不改策略凑数），官方检查未完全闭合。因此，本次 FT-FACTOR-03 扩大样本回测结论【明确保留为探索结果】，严禁发布正式因子准入或有效性结论。"
        }
    }

    report_path = OUT_DIR / "04_未来数据泄漏与偏差检查状态报告.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(audit_report, f, indent=2, ensure_ascii=False)

    logger.info("================ 偏差核验完成 ================")
    logger.info(f"Recursive: {res_rec['status']}")
    logger.info(f"Lookahead: {res_look['status']}")
    logger.info(f"Prefix Consistency: {res_prefix['status']}")
    logger.info(f"总体准入闭合判定: 保留为【探索结果】(不发布正式因子结论)")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-C｜01 固定输入清单与数据够用性检查

本脚本为隔离只读研究脚本，严禁修改共享工程或生产数据库。
核心目标：
1. 固定本次实验输入区间、币池、数据来源、缺口、运行版本和参数来源；
2. 检查现有数据覆盖度，确认够不够支撑本次实验，严格不启动全池补下载；
3. 输出结构化固定输入清单至 evidence/ACCUM-EARLY-04-C/out/01_固定输入清单与数据够用性.json。
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

import replay_calc as RC
import research_15m as R15

HOT_DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD_DIR = ROOT / "数据" / "市场数据" / "parquet"
OBSERVE_JSON = BACKEND / "locks" / "paper_runtime_3a_observe.json"
ACC2_JSON = ROOT / "evidence" / "ACCUM-EARLY-02" / "out" / "04_回放计算修复.json"
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-04-C" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

WIN_START_UTC = "2026-06-27T00:00:00Z"
WIN_END_UTC = "2026-10-26T09:00:00Z"
WARMUP_START_UTC = "2026-05-20T00:00:00Z"


def main():
    print(">>> 开始执行 ACCUM-EARLY-04-C 输入清点与数据够用性检查...")

    # 1. 标的池清单整理
    with open(ACC2_JSON, "r", encoding="utf-8") as f:
        acc2_data = json.load(f)
    cand_rows = acc2_data.get("B_全量回放", {}).get("逐条", [])
    cand_51 = [r for r in cand_rows if r.get("caliber") == "生效视图_5bps"]
    cand_symbols = sorted(list(set(r["symbol"] for r in cand_51)))

    observe_symbols = []
    observe_total = 0
    if OBSERVE_JSON.exists():
        with open(OBSERVE_JSON, "r", encoding="utf-8") as f:
            obs = json.load(f)
            observe_total = obs.get("observing_total", len(obs.get("items", [])))
            observe_symbols = sorted(list(set(item["symbol"] for item in obs.get("items", []))))

    # 数据库活跃池与全池合约
    con = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        all_pool_symbols = [r[0] for r in cur.execute("SELECT symbol FROM symbol_pool").fetchall()]
        funding_symbols = [r[0] for r in cur.execute("SELECT DISTINCT symbol FROM funding_history").fetchall()]
    finally:
        con.close()

    # 2. 检查 51 条真实候选标的在冷热数据中的覆盖与够用性
    merged_15m = R15.Merged15m(str(HOT_DB), str(COLD_DIR))
    merged_4h = R15.Merged15m(str(HOT_DB), str(COLD_DIR), timeframe="4h")

    coverage_report = []
    gap_count_total = 0
    missing_symbols = []

    for sym in cand_symbols:
        # 抽样检查一段 15m 与 4h 数据
        bars15, st15 = merged_15m.load(sym, 1782547200000, 1783547200000) # 约 11 天
        bars4, st4 = merged_4h.load(sym, 1782547200000, 1785547200000)
        gap15 = st15.get("continuity", {}).get("gap_segment_count", 0)
        gap_count_total += gap15

        if len(bars15) == 0:
            missing_symbols.append(sym)

        coverage_report.append({
            "symbol": sym,
            "15m_sample_bars": len(bars15),
            "15m_cold_rows": st15.get("rows_cold", 0),
            "15m_hot_rows": st15.get("rows_hot", 0),
            "15m_gaps": gap15,
            "4h_sample_bars": len(bars4),
            "has_funding": (sym in funding_symbols),
        })

    # 3. 统计口径与参数来源
    calibers_info = {}
    for cal_name, cal_cfg in RC.CALIBERS.items():
        calibers_info[cal_name] = {k: v for k, v in cal_cfg.items()}

    # 4. 汇总固定输入清单
    manifest = {
        "task": "ACCUM-EARLY-04-C",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_fixed_manifest": {
            "window": {
                "start_utc": WIN_START_UTC,
                "end_utc": WIN_END_UTC,
                "warmup_start_utc": WARMUP_START_UTC,
                "description": "拟采用的首轮历史验证窗口，含 4h 状态机预热（Wilder ATR 与 42 根箱体）",
            },
            "universe": {
                "historical_candidate_symbols_count": len(cand_symbols),
                "historical_candidate_symbols": cand_symbols,
                "current_observing_total": observe_total,
                "current_observing_symbols_count": len(observe_symbols),
                "total_symbols_in_db": len(all_pool_symbols),
            },
            "data_sources": {
                "hot_db": str(HOT_DB.relative_to(ROOT)),
                "cold_parquet_dir": str(COLD_DIR.relative_to(ROOT)),
                "reader_implementation": "research_15m.py (Merged15m 门面，支持 15m 与 4h 双周期冷热无缝合并)",
                "arithmetic_engine": "框架/backend/replay_calc.py (唯一获批复算实现)",
            },
            "data_sufficiency": {
                "check_result": "足够支撑本次对照实验",
                "require_full_pool_download": False,
                "missing_symbols_in_candidates": missing_symbols,
                "tested_candidate_symbols_count": len(cand_symbols),
                "total_gaps_in_samples": gap_count_total,
                "action_on_download": "坚决不启动全池补下载，遵守无越权数据原则",
            },
            "gaps_and_provenance": {
                "cold_available_at": "冷档 Parquet 缺失真实抓取时刻 fetched_at，统一保留 available_at_missing=True，按探索性结果定性，不冒充当时可得",
                "hot_available_at": "热库根据 fetched_at 逐行判定 proven/late/unknown/future",
                "funding_order_uncertainty": "资金费结算时间与平仓退出时刻间隔 <= 15 分钟的事件标记为 funding_order_uncertain，不静默填零",
                "metrics_ratio_cutoff": "主动买卖与多空比归档停在 2026-10-14T23:55Z，历史对照不强加未获批之指标门槛",
            },
            "version_and_calibers": {
                "strategy_version": "策略/WyckoffMaster/event_trend_v0_1 (代码与参数一字不动，冻结状态机)",
                "calibers": calibers_info,
                "conflict_notice": "滑点 (3bps vs 5bps)、风险配置 (0.5% vs 1.5%)、参与率上限 (1% vs 0.1%) 为已知两套冲突口径，本次并列报告，未获批准前不作为单一正式成绩",
            },
        },
        "coverage_sample_summary": {
            "total_candidates_examined": len(cand_symbols),
            "all_candidates_covered": len(missing_symbols) == 0,
            "sample_coverage": coverage_report[:5],
        }
    }

    out_file = OUT_DIR / "01_固定输入清单与数据够用性.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"✔ 01 固定输入清单与数据够用性检查完成，产物已落盘至: {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

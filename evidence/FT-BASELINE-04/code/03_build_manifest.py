# -*- coding: utf-8 -*-
"""FT-BASELINE-04 固化版本指纹清单：03_build_manifest.py"""
from __future__ import annotations

import hashlib
import json
import os

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
TARGET_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-04")

FILES_TO_HASH = [
    "evidence/FT-BASELINE-04/user_data/strategies/WyckoffEventTrendBaseline.py",
    "evidence/FT-BASELINE-04/user_data/config.json",
    "evidence/FT-BASELINE-04/user_data/config_smoke.json",
    "evidence/FT-BASELINE-04/code/01_test_p0_and_stoploss_unit.py",
    "evidence/FT-BASELINE-04/code/02_build_audit_tables.py",
    "evidence/FT-BASELINE-04/out/01_P0候选绑定与止损核对表.csv",
    "evidence/FT-BASELINE-04/out/02_资金费逐事件核对表.csv",
    "evidence/FT-BASELINE-04/out/03_资金费按笔汇总对账表.csv",
    "evidence/FT-BASELINE-04/out/04_freqtrade_trades.csv",
    "evidence/FT-BASELINE-04/logs/freqtrade_backtest_5symbols.log",
    "evidence/FT-BASELINE-04/logs/recursive_analysis.log",
    "evidence/FT-BASELINE-04/logs/lookahead_analysis.log",
    "evidence/FT-BASELINE-04/00_收尾报告.md"
]

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    manifest = {
        "work_order": "FT-BASELINE-04",
        "title": "P0与资金费最小收尾",
        "engine": "Freqtrade 2026.9",
        "python_version": "Python 3.11.16",
        "input_dataset_version": "WX-FT-IN-1.0 (evidence/FT-DATA-01)",
        "symbols": ["AR/USDT:USDT", "BANANA/USDT:USDT", "BAT/USDT:USDT", "CTSI/USDT:USDT", "PUMPBTC/USDT:USDT"],
        "timerange": "20250801-20251231",
        "review_status": "待Codex审核",
        "closure_items": {
            "p0_candidate_binding": "已彻底废除 actual_entry * 0.90 假止损；实现开仓前拒单与持仓异常报错；5 笔交易 100% 绑定真实 P0",
            "funding_fee_audit": "完成 5 笔真实持仓 168 次资金费事件逐笔对账；彻底厘清 110次 vs 168次 历史成因与 Freqtrade 现金流符号定义",
            "metrics_report_responsibility": "指标报告统一移交 Work 生成，本交付包不再维护另一套双口径指标",
            "future_data_bias_check": "recursive-analysis 完全通过；lookahead-analysis 样本不足 10 笔，明确保留为【未完成】"
        },
        "files": {}
    }

    for rel_path in FILES_TO_HASH:
        full_path = os.path.join(BASE_DIR, rel_path)
        if os.path.exists(full_path):
            manifest["files"][rel_path] = {
                "size_bytes": os.path.getsize(full_path),
                "sha256": sha256_file(full_path)
            }
        else:
            manifest["files"][rel_path] = {"status": "pending_or_generated_next"}

    out_p = os.path.join(TARGET_DIR, "out/versions.json")
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)
        
    print(f"固化清单已生成: {out_p}")

if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""FT-BASELINE-03 固化版本指纹清单：05_build_manifest.py"""
from __future__ import annotations

import hashlib
import json
import os

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
TARGET_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-03")

FILES_TO_HASH = [
    "evidence/FT-BASELINE-03/user_data/strategies/WyckoffEventTrendBaseline.py",
    "evidence/FT-BASELINE-03/user_data/config.json",
    "evidence/FT-BASELINE-03/user_data/config_smoke.json",
    "evidence/FT-BASELINE-03/code/01_prepare_data.py",
    "evidence/FT-BASELINE-03/code/02_verify_stoploss_logic.py",
    "evidence/FT-BASELINE-03/code/03_test_strategy_unit.py",
    "evidence/FT-BASELINE-03/code/04_analyze_and_compare.py",
    "evidence/FT-BASELINE-03/00_回测结果报告.md",
    "evidence/FT-BASELINE-03/out/01_freqtrade_trades.csv",
    "evidence/FT-BASELINE-03/out/02_资金费逐笔核对表.csv",
    "evidence/FT-BASELINE-03/out/03_逐笔对账详细比较表.csv",
    "evidence/FT-BASELINE-03/out/04_双口径指标汇总.json",
    "evidence/FT-BASELINE-03/out/05_权益曲线.csv",
    "evidence/FT-BASELINE-03/logs/freqtrade_backtest_5symbols.log",
    "evidence/FT-BASELINE-03/logs/lookahead_analysis.log",
    "evidence/FT-BASELINE-03/logs/recursive_analysis.log",
]

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    manifest = {
        "work_order": "FT-BASELINE-03",
        "title": "修正止损并重跑同一基线",
        "engine": "Freqtrade 2026.9",
        "python_version": "Python 3.11.16",
        "input_dataset_version": "WX-FT-IN-1.0 (evidence/FT-DATA-01)",
        "symbols": ["AR/USDT:USDT", "BANANA/USDT:USDT", "BAT/USDT:USDT", "CTSI/USDT:USDT", "PUMPBTC/USDT:USDT"],
        "timerange": "20250801-20251231",
        "review_status": "待独立指标核对",
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
            manifest["files"][rel_path] = {"error": "file_not_found"}

    out_p = os.path.join(TARGET_DIR, "out/versions.json")
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)
        
    print(f"固化清单已生成: {out_p}")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

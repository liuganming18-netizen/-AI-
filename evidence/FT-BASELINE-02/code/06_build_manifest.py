# -*- coding: utf-8 -*-
"""FT-BASELINE-02 固化版本指纹清单：06_build_manifest.py"""
from __future__ import annotations

import hashlib
import json
import os

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
TARGET_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-02")

FILES_TO_HASH = [
    "evidence/FT-BASELINE-02/user_data/strategies/WyckoffEventTrendBaseline.py",
    "evidence/FT-BASELINE-02/user_data/config.json",
    "evidence/FT-BASELINE-02/user_data/config_smoke.json",
    "evidence/FT-BASELINE-02/code/01_verify_ft_data_input.py",
    "evidence/FT-BASELINE-02/code/02_test_state_machine_on_ft_data.py",
    "evidence/FT-BASELINE-02/code/03_prepare_ft_data_utc.py",
    "evidence/FT-BASELINE-02/code/04_compare_with_old_engine.py",
    "evidence/FT-BASELINE-02/code/05_calculate_two_metrics.py",
    "evidence/FT-BASELINE-02/out/01_freqtrade_trades.csv",
    "evidence/FT-BASELINE-02/out/02_逐笔对账详细比较表.csv",
    "evidence/FT-BASELINE-02/out/03_双口径指标汇总.json",
    "evidence/FT-BASELINE-02/out/04_权益曲线.csv",
    "evidence/FT-BASELINE-02/logs/freqtrade_backtest_5symbols.log",
    "evidence/FT-BASELINE-02/logs/lookahead_analysis.log",
    "evidence/FT-BASELINE-02/logs/recursive_analysis.log",
]

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    manifest = {
        "work_order": "FT-BASELINE-02",
        "title": "恢复原策略语义并执行最小基线",
        "engine": "Freqtrade 2026.9",
        "python_version": "Python 3.11.16",
        "input_dataset_version": "WX-FT-IN-1.0 (evidence/FT-DATA-01)",
        "symbols": ["AR/USDT:USDT", "BANANA/USDT:USDT", "BAT/USDT:USDT", "CTSI/USDT:USDT", "PUMPBTC/USDT:USDT"],
        "timerange": "20250801-20251231",
        "review_status": "待Codex审核",
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

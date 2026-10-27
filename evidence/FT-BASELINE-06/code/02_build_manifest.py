# -*- coding: utf-8 -*-
"""FT-BASELINE-06 固化版本指纹清单：02_build_manifest.py"""
from __future__ import annotations

import hashlib
import json
import os

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
TARGET_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-06")

INPUT_SOURCES = {
    "evidence/FT-DATA-01/out/05_旧引擎基准成交.csv": "旧引擎基准5笔成交记录（含真实qty与funding_fee）",
    "evidence/FT-METRICS-03/out/02_逐笔核对表.csv": "FT-METRICS-03 独立核对表（含独立验证的数量与资金费）",
    "evidence/FT-BASELINE-05/out/04_freqtrade_trades.csv": "FT-BASELINE-05 冻结的官方成交记录",
    "evidence/FT-BASELINE-05/out/02_资金费逐事件核对表.csv": "FT-BASELINE-05 冻结的168次逐事件核对表"
}

DELIVERY_FILES = [
    "evidence/FT-BASELINE-06/code/01_build_funding_qty_table.py",
    "evidence/FT-BASELINE-06/out/03_资金费与持仓数量对照表.csv",
    "evidence/FT-BASELINE-06/logs/generate.log",
    "evidence/FT-BASELINE-06/00_收尾说明.md"
]

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    manifest = {
        "work_order": "FT-BASELINE-06",
        "title": "资金费数量证据表修正",
        "description": "修正 FT-BASELINE-05 中旧持仓数量填0缺陷，统一收付符号，实证数量比与资金费比在1e-15量级完全吻合",
        "timestamp_utc": "2026-10-20T08:08:00Z",
        "supersedes": "evidence/FT-BASELINE-05/out/03_资金费按笔汇总对账表.csv 中的错误数量列与填0记录已被本交付取代",
        "input_sources": {},
        "delivery_files": {}
    }

    for rel_path, desc in INPUT_SOURCES.items():
        full_p = os.path.join(BASE_DIR, rel_path)
        if os.path.exists(full_p):
            manifest["input_sources"][rel_path] = {
                "description": desc,
                "size_bytes": os.path.getsize(full_p),
                "sha256": sha256_file(full_p)
            }

    for rel_path in DELIVERY_FILES:
        full_p = os.path.join(BASE_DIR, rel_path)
        if os.path.exists(full_p):
            manifest["delivery_files"][rel_path] = {
                "size_bytes": os.path.getsize(full_p),
                "sha256": sha256_file(full_p)
            }
        else:
            manifest["delivery_files"][rel_path] = {"status": "pending"}

    out_p = os.path.join(TARGET_DIR, "out/versions.json")
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)
    print(f"版本指纹清单已生成: {out_p}")

if __name__ == "__main__":
    main()

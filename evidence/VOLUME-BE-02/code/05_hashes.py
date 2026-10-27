# -*- coding: utf-8 -*-
"""VOLUME-BE-02 · 步骤五：版本指纹与代码零改动自检。"""
from __future__ import annotations

import hashlib
import json
import os

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
BASE_DIR = os.path.join(ROOT, "evidence/VOLUME-BE-02")
OUT_DIR = os.path.join(BASE_DIR, "out")

FILES_TO_HASH = [
    "evidence/VOLUME-BE-02/code/vbe2_core.py",
    "evidence/VOLUME-BE-02/code/01_evaluate_hypotheses.py",
    "evidence/VOLUME-BE-02/code/02_run_abcd_fixed.py",
    "evidence/VOLUME-BE-02/code/03_subperiod_and_cases.py",
    "evidence/VOLUME-BE-02/code/04_verify.py",
    "evidence/VOLUME-BE-02/code/05_hashes.py",
    "evidence/VOLUME-BE-02/out/hypotheses_evaluation_detail.csv",
    "evidence/VOLUME-BE-02/out/hypotheses_summary.json",
    "evidence/VOLUME-BE-02/out/duplicate_groups.json",
    "evidence/VOLUME-BE-02/out/paired_trades_1r.csv",
    "evidence/VOLUME-BE-02/out/paired_trades_2r.csv",
    "evidence/VOLUME-BE-02/out/summary_1r.json",
    "evidence/VOLUME-BE-02/out/summary_2r.json",
    "evidence/VOLUME-BE-02/out/subperiod_analysis.json",
    "evidence/VOLUME-BE-02/out/case_studies.json",
    "evidence/VOLUME-BE-02/out/fee_reconciliation.csv",
    "evidence/VOLUME-BE-02/out/fee_reconciliation_summary.json",
    "evidence/VOLUME-BE-02/out/verify_results.json",
    "evidence/VOLUME-BE-02/报告.md",
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_hashes():
    hashes = {}
    for rel in FILES_TO_HASH:
        full = os.path.join(ROOT, rel)
        if os.path.exists(full):
            hashes[rel] = sha256(full)
        else:
            hashes[rel] = "FILE_NOT_FOUND"

    out_file = os.path.join(OUT_DIR, "versions.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(hashes, f, indent=2, ensure_ascii=False)
    print(f"Hashes written to {out_file}")


if __name__ == "__main__":
    generate_hashes()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAINLINE-01 反重力 A：反例数据提取与逐阶段比对
"""

import os
import sys
import json
import sqlite3
import pandas as pd
from datetime import datetime, timezone

REAL_ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
EVIDENCE_DIR = os.path.join(REAL_ROOT, "evidence/MAINLINE-01-ANTIGRAVITY-A")
OUT_DIR = os.path.join(EVIDENCE_DIR, "out")

def extract_counter_example_wal():
    print("=== Extracting Counter Example: WALUSDT ===")
    # Load stage divergence table from B
    b_table_path = os.path.join(REAL_ROOT, "evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-B/out/stage_divergence_table.json")
    if os.path.exists(b_table_path):
        with open(b_table_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        stages = data.get("stages", [])
        df_wal = pd.DataFrame(stages)
        out_wal_path = os.path.join(OUT_DIR, "counter_example_walusdt_stages.csv")
        df_wal.to_csv(out_wal_path, index=False)
        print(f"WALUSDT stages saved to {out_wal_path}")
        print(df_wal.to_string())

def extract_counter_example_invalidated():
    print("=== Extracting Counter Example: INVALIDATED Sample ===")
    # From A's previous failure sample
    old_a_fail_path = os.path.join(REAL_ROOT, "evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/failure_sample_key_steps.csv")
    if os.path.exists(old_a_fail_path):
        df_fail = pd.read_csv(old_a_fail_path)
        out_fail_path = os.path.join(OUT_DIR, "counter_example_invalidated_steps.csv")
        df_fail.to_csv(out_fail_path, index=False)
        print(f"Invalidated steps saved to {out_fail_path}")
        print(df_fail.to_string())

if __name__ == "__main__":
    extract_counter_example_wal()
    extract_counter_example_invalidated()

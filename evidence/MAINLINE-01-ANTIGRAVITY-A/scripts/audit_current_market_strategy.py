#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAINLINE-01 反重力 A：当前行情策略判定与逐 bar 数据还原脚本
"""

import os
import sys
import json
import hashlib
import shutil
import sqlite3
import pandas as pd
from datetime import datetime, timezone

REAL_ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
EVIDENCE_DIR = os.path.join(REAL_ROOT, "evidence/MAINLINE-01-ANTIGRAVITY-A")
SNAPSHOT_DIR = os.path.join(EVIDENCE_DIR, "snapshot")
OUT_DIR = os.path.join(EVIDENCE_DIR, "out")
SCRIPTS_DIR = os.path.join(EVIDENCE_DIR, "scripts")

os.makedirs(SNAPSHOT_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(SCRIPTS_DIR, exist_ok=True)

def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def step1_snapshot_and_manifest():
    print("=== Step 1: Snapshot and Manifest ===")
    targets = [
        "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
        "策略/WyckoffMaster/event_trend_v0_1/config.py",
        "策略/WyckoffMaster/event_trend_v0_1/contracts.py",
        "策略/WyckoffMaster/event_trend_v0_1/features.py",
        "策略/WyckoffMaster/event_trend_v0_1/runner.py",
        "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py",
        "框架/backend/unified_runtime.py",
        "框架/backend/paper_engine.py",
        "框架/backend/live_store.py",
        "框架/backend/live_api.py",
        "框架/backend/locks/paper_runtime_3a_status.json",
        "框架/backend/locks/paper_runtime_3a_observe.json",
        "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md",
    ]
    manifest = {}
    for rel_path in targets:
        full_path = os.path.join(REAL_ROOT, rel_path)
        if os.path.exists(full_path):
            basename = os.path.basename(rel_path)
            dest = os.path.join(SNAPSHOT_DIR, basename)
            shutil.copy2(full_path, dest)
            h = sha256_file(full_path)
            sz = os.path.getsize(full_path)
            manifest[rel_path] = {
                "snapshot_file": basename,
                "sha256": h,
                "size_bytes": sz,
                "mtime": datetime.fromtimestamp(os.path.getmtime(full_path), timezone.utc).isoformat()
            }
            print(f"  [OK] {rel_path} -> sha256: {h[:12]}...")
        else:
            print(f"  [MISSING] {rel_path}")

    manifest_path = os.path.join(SNAPSHOT_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"Manifest written to {manifest_path}")
    return manifest

def step2_extract_main_sample_oneusdt():
    print("\n=== Step 2: Extract Main Sample ONEUSDT ===")
    db_path = os.path.join(REAL_ROOT, "数据/市场数据/db/market_v2.sqlite3")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    # ONEUSDT 4h K lines around box formation
    # Box created at 2026-10-27 12:00:00 UTC (1793102400000)
    # Box parameters: 42 bars of 4h. 42 * 4h = 168h. 
    # Start: 1793102400000 - 42 * 4 * 3600 * 1000 = 1792497600000 (2026-10-20 12:00:00 UTC)
    q_4h = """
    SELECT open_time, close_time, open, high, low, close, volume
    FROM candles
    WHERE symbol = 'ONEUSDT' AND timeframe = '4h' AND close_time <= 1793102400000
    ORDER BY open_time DESC
    LIMIT 42
    """
    df_box_4h = pd.read_sql(q_4h, conn).sort_values("open_time").reset_index(drop=True)
    
    H = df_box_4h['high'].max()
    L = df_box_4h['low'].min()
    
    # Extract 1h K lines from box created (1793102400000) onwards
    q_1h = """
    SELECT open_time, close_time, open, high, low, close, volume, is_closed
    FROM candles
    WHERE symbol = 'ONEUSDT' AND timeframe = '1h' AND open_time >= 1793000000000
    ORDER BY open_time ASC
    """
    df_1h = pd.read_sql(q_1h, conn)
    conn.close()

    print(f"  ONEUSDT 4h box bars count: {len(df_box_4h)}, H={H}, L={L}")
    print(f"  ONEUSDT 1h bars count: {len(df_1h)}")

    # Save to CSV
    box_4h_path = os.path.join(OUT_DIR, "oneusdt_box_4h_bars.csv")
    df_box_4h.to_csv(box_4h_path, index=False)

    df_1h_path = os.path.join(OUT_DIR, "oneusdt_1h_all_bars.csv")
    df_1h.to_csv(df_1h_path, index=False)

    return df_box_4h, df_1h

if __name__ == "__main__":
    step1_snapshot_and_manifest()
    step2_extract_main_sample_oneusdt()

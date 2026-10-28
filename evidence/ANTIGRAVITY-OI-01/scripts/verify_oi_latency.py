#!/usr/bin/env python3
"""
ANTIGRAVITY-OI-01: 持仓量时效与阻断核验独立复算脚本
复算目标:
1. 验证既有 "52分钟中位延迟、15分钟要求、99.7%不满足" 的确切数据来源与算法口径;
2. 对比 period='5m' 与 period='snapshot' 的时效差异;
3. 导出复算 JSON 与样本明细 CSV。
"""

import os
import json
import sqlite3
import numpy as np
import pandas as pd

def main():
    base_dir = '/Users/boos/Desktop/威科夫因子共振趋势'
    evidence_dir = os.path.join(base_dir, 'evidence/ANTIGRAVITY-OI-01')
    out_dir = os.path.join(evidence_dir, 'out')
    os.makedirs(out_dir, exist_ok=True)

    db_path = os.path.join(base_dir, '数据/市场数据/db/market_v2.sqlite3')
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    c = conn.cursor()

    # 取最近 24 小时
    c.execute("SELECT MAX(ts) FROM oi_history WHERE source = 'binance.fapi.openInterestHist'")
    max_ts = c.fetchone()[0]
    cutoff_24h = max_ts - 86400 * 1000

    # 1. period = 5m
    c.execute("""
        SELECT symbol, ts, fetched_at, (fetched_at - ts) / 1000.0 / 60.0
        FROM oi_history
        WHERE source = 'binance.fapi.openInterestHist' AND period = '5m' AND ts >= ?
    """, (cutoff_24h,))
    rows_5m = c.fetchall()
    arr_5m = np.array([r[3] for r in rows_5m])

    # 2. period = snapshot
    c.execute("""
        SELECT symbol, ts, fetched_at, (fetched_at - ts) / 1000.0 / 60.0
        FROM oi_history
        WHERE source = 'binance.fapi.openInterestHist' AND period = 'snapshot' AND ts >= ?
    """, (cutoff_24h,))
    rows_snap = c.fetchall()
    arr_snap = np.array([r[3] for r in rows_snap])

    stats = {
        'audit_task': 'ANTIGRAVITY-OI-01',
        'sample_period_utc': {
            'from_ts': int(cutoff_24h),
            'to_ts': int(max_ts),
            'from_utc': pd.to_datetime(cutoff_24h, unit='ms', utc=True).isoformat(),
            'to_utc': pd.to_datetime(max_ts, unit='ms', utc=True).isoformat()
        },
        'oi_5m_openInterestHist': {
            'total_rows': int(len(arr_5m)),
            'unique_symbols': int(len(set(r[0] for r in rows_5m))),
            'min_min': float(np.min(arr_5m)),
            'p25_min': float(np.percentile(arr_5m, 25)),
            'p50_median_min': float(np.percentile(arr_5m, 50)),
            'p75_min': float(np.percentile(arr_5m, 75)),
            'p90_min': float(np.percentile(arr_5m, 90)),
            'p95_min': float(np.percentile(arr_5m, 95)),
            'p99_min': float(np.percentile(arr_5m, 99)),
            'max_min': float(np.max(arr_5m)),
            'within_15min_ratio': float(np.mean(arr_5m <= 15.0)),
            'exceed_15min_ratio': float(np.mean(arr_5m > 15.0)),
            'exceed_15min_percentage_str': '%.2f%%' % (np.mean(arr_5m > 15.0) * 100)
        },
        'oi_snapshot_openInterestHist': {
            'total_rows': int(len(arr_snap)),
            'unique_symbols': int(len(set(r[0] for r in rows_snap))),
            'min_min': float(np.min(arr_snap)),
            'p50_median_min': float(np.percentile(arr_snap, 50)),
            'p90_min': float(np.percentile(arr_snap, 90)),
            'max_min': float(np.max(arr_snap)),
            'within_15min_ratio': float(np.mean(arr_snap <= 15.0))
        }
    }

    out_json = os.path.join(out_dir, 'oi_latency_recalc.json')
    with open(out_json, 'w', encoding='utf-8') as fp:
        json.dump(stats, fp, indent=2, ensure_ascii=False)
    print(f"Written: {out_json}")

if __name__ == '__main__':
    main()

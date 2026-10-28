#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAINLINE-01 反重力 A：当前行情策略判定详细计算与数据导出
"""

import os
import sys
import json
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timezone

REAL_ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
EVIDENCE_DIR = os.path.join(REAL_ROOT, "evidence/MAINLINE-01-ANTIGRAVITY-A")
OUT_DIR = os.path.join(EVIDENCE_DIR, "out")

def ms_to_utc_str(ms):
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def ms_to_bj_str(ms):
    # UTC + 8
    dt = datetime.fromtimestamp(ms / 1000.0 + 8 * 3600, timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def analyze_oneusdt():
    print("=== Analyzing ONEUSDT ===")
    db_path = os.path.join(REAL_ROOT, "数据/市场数据/db/market_v2.sqlite3")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    # 1. 4h Box bars
    q_4h = """
    SELECT open_time, close_time, open, high, low, close, volume
    FROM candles
    WHERE symbol = 'ONEUSDT' AND timeframe = '4h' AND close_time <= 1793102400000
    ORDER BY open_time DESC
    LIMIT 42
    """
    df_box_4h = pd.read_sql(q_4h, conn).sort_values("open_time").reset_index(drop=True)
    
    H = float(df_box_4h['high'].max())
    L = float(df_box_4h['low'].min())
    
    # In observe.json: H=0.0015504, L=0.0012673, A=4.0969447e-05, ER=0.152414, width_atr=6.91
    A = 4.0969447e-05
    breakout_target = H + 0.1 * A # 0.0015545
    sweep_max_overshoot = L - 0.5 * A
    sweep_min_recovery = L + 0.1 * A

    # 2. 1h bars from box creation
    q_1h = """
    SELECT open_time, close_time, open, high, low, close, volume, is_closed
    FROM candles
    WHERE symbol = 'ONEUSDT' AND timeframe = '1h' AND open_time >= 1793102400000
    ORDER BY open_time ASC
    """
    df_1h = pd.read_sql(q_1h, conn)
    conn.close()

    rows = []
    current_state = "BOX"
    S = None
    Vs = None
    Lr = None
    stop_invalid_line = None

    for idx, r in df_1h.iterrows():
        o_t = int(r['open_time'])
        c_t = int(r['close_time'])
        op = float(r['open'])
        hp = float(r['high'])
        lp = float(r['low'])
        cp = float(r['close'])
        vol = float(r['volume'])

        # Evaluate rules per state
        action = "WAIT"
        rule_checked = ""
        gate_result = ""
        block_reason = ""

        if current_state == "BOX":
            # Check SWEEP:
            # low < L and low >= L - 0.5A and close >= L + 0.1A and close <= H
            rule_checked = f"SWEEP check: low < {L:.7f} and low >= {sweep_max_overshoot:.7f} and close >= {sweep_min_recovery:.7f}"
            c1 = (lp < L)
            c2 = (lp >= sweep_max_overshoot)
            c3 = (cp >= sweep_min_recovery)
            c4 = (cp <= H)
            if c1 and c2 and c3 and c4:
                current_state = "SWEEP"
                action = "TRANSITION -> SWEEP"
                S = lp
                Vs = vol
                gate_result = f"PASS: low({lp:.7f}) < L({L:.7f}), close({cp:.7f}) >= L+0.1A({sweep_min_recovery:.7f})"
            else:
                if not c1:
                    gate_result = f"NO_SWEEP: low({lp:.7f}) >= L({L:.7f}) 未破箱底下沿"
                elif not c2:
                    current_state = "INVALIDATED"
                    action = "TRANSITION -> INVALIDATED"
                    gate_result = f"OVERSHOOT: low({lp:.7f}) < L-0.5A({sweep_max_overshoot:.7f}) 跌破过深"
                elif not c3:
                    gate_result = f"RECOVERY_FAIL: close({cp:.7f}) < L+0.1A({sweep_min_recovery:.7f}) 未能有效收回"

        elif current_state == "SWEEP":
            # Check RETEST:
            # L <= low <= L + 0.5A and low > S and close >= open and volume <= 0.8 * Vs
            retest_max_zone = L + 0.5 * A
            vol_limit = 0.8 * Vs if Vs else 0
            rule_checked = f"RETEST check: L({L:.7f}) <= low <= L+0.5A({retest_max_zone:.7f}), low > S({S:.7f}), close>=open, vol<={vol_limit:.1f}"
            
            # Failure check: low < S - 0.1A
            if lp < S - 0.1 * A:
                current_state = "INVALIDATED"
                action = "TRANSITION -> INVALIDATED"
                gate_result = f"FAIL: low({lp:.7f}) < S-0.1A({S - 0.1 * A:.7f}) 破扫低低点防守位"
            else:
                c_zone = (lp >= L and lp <= retest_max_zone)
                c_higher = (lp > S)
                c_bull = (cp >= op)
                c_vol = (vol <= vol_limit)
                if c_higher and c_bull and c_vol and (lp <= retest_max_zone):
                    current_state = "RETEST"
                    action = "TRANSITION -> RETEST"
                    Lr = lp
                    stop_invalid_line = Lr - 0.1 * A
                    gate_result = f"PASS: low({lp:.7f}) > S, 收阳({cp:.7f}>={op:.7f}), 缩量 vol({vol:.1f}) <= 0.8Vs({vol_limit:.1f})"
                else:
                    reasons = []
                    if not (lp <= retest_max_zone): reasons.append("探底未进回踩区")
                    if not c_higher: reasons.append("未高于扫低S")
                    if not c_bull: reasons.append("收阴未收阳")
                    if not c_vol: reasons.append(f"未缩量(vol {vol:.1f} > {vol_limit:.1f})")
                    gate_result = "WAIT_RETEST: " + "; ".join(reasons)

        elif current_state == "RETEST":
            # Check INVALIDATED: low < Lr - 0.1A
            rule_checked = f"BREAKOUT check: close > {breakout_target:.7f}; DEFENSE check: low >= {stop_invalid_line:.7f}"
            if lp < stop_invalid_line:
                current_state = "INVALIDATED"
                action = "TRANSITION -> INVALIDATED"
                gate_result = f"FAIL: low({lp:.7f}) < Lr-0.1A({stop_invalid_line:.7f}) 跌破回踩防守线阻断"
            elif cp > breakout_target:
                action = "TRANSITION -> CANDIDATE"
                gate_result = f"BREAKOUT_DETECTED: close({cp:.7f}) > H+0.1A({breakout_target:.7f})"
            else:
                action = "WAIT_BREAKOUT"
                gap_pct = (breakout_target - cp) / cp * 100
                gate_result = f"RULE_NOT_MET: close({cp:.7f}) 距突破目标 {breakout_target:.7f} 差 {gap_pct:.2f}%，在区间内正常等待"

        rows.append({
            "bar_idx": idx + 1,
            "open_utc": ms_to_utc_str(o_t),
            "close_utc": ms_to_utc_str(c_t),
            "bj_time": ms_to_bj_str(c_t),
            "open": op,
            "high": hp,
            "low": lp,
            "close": cp,
            "volume": vol,
            "state_after": current_state,
            "action": action,
            "rule_evaluated": rule_checked,
            "gate_result": gate_result
        })

    df_res = pd.DataFrame(rows)
    out_csv = os.path.join(OUT_DIR, "oneusdt_step_by_step_audit.csv")
    df_res.to_csv(out_csv, index=False)
    print(f"ONEUSDT audit finished, {len(df_res)} bars processed, saved to {out_csv}")
    print(df_res[["bar_idx", "bj_time", "state_after", "action", "gate_result"]].to_string())
    return df_res

if __name__ == "__main__":
    analyze_oneusdt()

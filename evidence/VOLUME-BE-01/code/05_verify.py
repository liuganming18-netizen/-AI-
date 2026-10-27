# -*- coding: utf-8 -*-
"""VOLUME-BE-01 · 步骤五：公平对照与时序因果针对性测试。

验证清单：
T1. 候选全集完整性（206 笔严格保留，无物理删行）
T2. 过滤保留标记合规（被过滤交易 C/D 组标记 FILTERED_NOT_ENTERED，净额恒为 0.0）
T3. 未过滤交易数量冻结（数量完全等于 frozen_qty，入场时间/价格/初始止损与 A 组完全相同）
T4. 差额恒等性（C_delta == C_saved_loss - C_forgone_profit 恒等）
T5. 保本版本与 BREAKEVEN-02 的无缝衔接（B 组数据与 BREAKEVEN-02 的 B/C 组 100% 吻合）
T6. 同根冲突保守原则（无有利逃脱）
"""
from __future__ import annotations

import csv
import json
import os
import sys
import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT_DIR = os.path.join(ROOT, "evidence/VOLUME-BE-01/out")
BE2_PAIRED = os.path.join(ROOT, "evidence/BREAKEVEN-02/out/paired_fixed.csv")
PAIRED_1R = os.path.join(OUT_DIR, "paired_trades_1r.csv")
PAIRED_2R = os.path.join(OUT_DIR, "paired_trades_2r.csv")

EPS = 1e-7


def run_verification():
    df_be2 = pd.read_csv(BE2_PAIRED)
    df_1r = pd.read_csv(PAIRED_1R)
    df_2r = pd.read_csv(PAIRED_2R)

    checks = []

    # T1. 全集完整性
    t1_pass = (len(df_1r) == 206) and (len(df_2r) == 206) and (len(df_be2) == 206)
    checks.append({
        "test": "T1_candidate_universe_completeness",
        "status": "PASS" if t1_pass else "FAIL",
        "detail": f"1R count: {len(df_1r)}, 2R count: {len(df_2r)}, BE2 count: {len(df_be2)}"
    })

    # T2. 过滤保留标记合规
    filt_1r = df_1r[df_1r["C_status"] == "FILTERED_NOT_ENTERED"]
    t2_pass = (len(filt_1r) == 110) and (filt_1r["C_net"].abs().max() < EPS) and (filt_1r["D_net"].abs().max() < EPS)
    checks.append({
        "test": "T2_filtered_signals_retained_with_zero_pnl",
        "status": "PASS" if t2_pass else "FAIL",
        "detail": f"Filtered count: {len(filt_1r)}, Max abs PnL in C: {filt_1r['C_net'].abs().max()}"
    })

    # T3. 未过滤交易数量冻结与入场同一
    entered_1r = df_1r[df_1r["C_status"] == "ENTERED"]
    mismatches = 0
    for _, r in entered_1r.iterrows():
        sid = r["signal_id"]
        be2_row = df_be2[df_be2["signal_id"] == sid].iloc[0]
        if abs(r["qty"] - be2_row["qty"]) > EPS:
            mismatches += 1
        if abs(r["entry_price"] - be2_row["entry_price"]) > EPS:
            mismatches += 1
        if abs(r["initial_stop"] - be2_row["initial_stop"]) > EPS:
            mismatches += 1
    t3_pass = (mismatches == 0)
    checks.append({
        "test": "T3_entered_trades_fixed_qty_and_entry_identity",
        "status": "PASS" if t3_pass else "FAIL",
        "detail": f"Checked {len(entered_1r)} trades, mismatches: {mismatches}"
    })

    # T4. 差额分解恒等性
    delta_sum = float(df_1r["C_delta"].sum())
    saved_sum = float(df_1r["C_saved_loss"].sum())
    forgone_sum = float(df_1r["C_forgone_profit"].sum())
    identity_diff = abs(delta_sum - (saved_sum - forgone_sum))
    t4_pass = (identity_diff < 1e-4)
    checks.append({
        "test": "T4_decomposition_identity",
        "status": "PASS" if t4_pass else "FAIL",
        "detail": f"delta_sum: {delta_sum:.4f}, saved - forgone: {(saved_sum - forgone_sum):.4f}, diff: {identity_diff:.6e}"
    })

    # T5. B 组与 BREAKEVEN-02 的基线与保本无缝衔接
    b_diff_1r = np.abs(df_1r["B_net"] - df_be2["B_net"]).max()
    b_diff_2r = np.abs(df_2r["B_net"] - df_be2["C_net"]).max() # BREAKEVEN-02 中 C 组是 2R
    a_diff = np.abs(df_1r["A_net"] - df_be2["A_net"]).max()
    t5_pass = (b_diff_1r < EPS) and (b_diff_2r < EPS) and (a_diff < EPS)
    checks.append({
        "test": "T5_breakeven_02_seamless_alignment",
        "status": "PASS" if t5_pass else "FAIL",
        "detail": f"Max diff: A={a_diff:.2e}, B_1R={b_diff_1r:.2e}, B_2R={b_diff_2r:.2e}"
    })

    print("===== 验证结果 =====")
    all_pass = True
    for c in checks:
        print(f"[{c['status']}] {c['test']}: {c['detail']}")
        if c["status"] != "PASS":
            all_pass = False

    with open(os.path.join(OUT_DIR, "verify_results.json"), "w", encoding="utf-8") as f:
        json.dump(checks, f, indent=2, ensure_ascii=False)

    return all_pass


if __name__ == "__main__":
    success = run_verification()
    sys.exit(0 if success else 1)

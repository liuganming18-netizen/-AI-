# -*- coding: utf-8 -*-
"""VOLUME-BE-01 · 步骤二：构建 1R 与 2R 两套完整的 A/B/C/D 四组联合对照。

严格遵循公平比较要求：
1. 全集一致：完全复用 BREAKEVEN-02 的 206 笔信号，不丢失任何一行；
2. 仓位固定：所有实际成交交易，数量钉死为 frozen_qty，时间/价格/初始止损严格一致；
3. 过滤保留：被缩量规则过滤的交易，状态标记为 FILTERED_NOT_ENTERED，策略收益记为 0.0 U，
   同时完整保留原策略 A 的后续路径与真实盈亏，以便计算少亏与踏空；
4. 联合规则执行：
   - 组 A：原策略（无保本、无过滤）
   - 组 B：仅保本止损（1R 或 2R，无过滤）
   - 组 C：仅缩量规则（入场前 1h 若放量下跌则过滤记 0U，未过滤交易保持原策略退出）
   - 组 D：保本止损 ＋ 缩量规则（若过滤则记 0U，未过滤交易执行保本止损）
"""
from __future__ import annotations

import csv
import json
import os
import sys
import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
BE2_PAIRED = os.path.join(ROOT, "evidence/BREAKEVEN-02/out/paired_fixed.csv")
HYP_DETAIL = os.path.join(ROOT, "evidence/VOLUME-BE-01/out/hypotheses_evaluation_detail.csv")
OUT_DIR = os.path.join(ROOT, "evidence/VOLUME-BE-01/out")
os.makedirs(OUT_DIR, exist_ok=True)


def build_abcd_sets():
    df_be2 = pd.read_csv(BE2_PAIRED)
    df_hyp = pd.read_csv(HYP_DETAIL)

    # 信号合并
    merged = pd.merge(
        df_be2,
        df_hyp[["signal_id", "is_entry_last_1h_high_vol_down", "is_entry_last_1h_vol_down", "pos_exit_triggered", "pos_exit_net"]],
        on="signal_id",
        how="inner"
    )

    if len(merged) != 206:
        raise ValueError(f"Merged count mismatch: expected 206, got {len(merged)}")

    # 缩量过滤规则：预先确定的 VDF 规则 (入场前 1h 若放量下跌则过滤排除)
    merged["is_filtered"] = merged["is_entry_last_1h_high_vol_down"]

    # 分别构建 1R 套系与 2R 套系
    for r_label, be_col_prefix in [("1R", "B"), ("2R", "C")]:
        rows = []
        for _, row in merged.iterrows():
            sid = row["signal_id"]
            sym = row["symbol"]
            win = row["window"]
            entry_utc = row["entry_time_utc"]
            entry_px = float(row["entry_price"])
            qty = float(row["qty"])
            init_stop = float(row["initial_stop"])
            r_abs = float(row["R_abs"])

            # A 组 (原策略基准)
            a_net = float(row["A_net"])
            a_exit_px = float(row["A_exit_price"])
            a_exit_utc = row["A_exit_time_utc"]
            a_reason = row["A_reason"]
            a_hold_h = float(row["A_hold_h"])

            # B 组 (仅保本止损，1R 或 2R)
            b_net = float(row[f"{be_col_prefix}_net"])
            b_exit_px = float(row[f"{be_col_prefix}_exit_price"])
            b_exit_utc = row[f"{be_col_prefix}_exit_time_utc"]
            b_reason = row[f"{be_col_prefix}_reason"]
            b_hold_h = float(row[f"{be_col_prefix}_hold_h"])
            b_be_trig = bool(row[f"{be_col_prefix}_be_triggered"])
            b_be_px = row[f"{be_col_prefix}_be_px"]

            # C 组 (仅缩量规则)
            is_filt = bool(row["is_filtered"])
            if is_filt:
                c_status = "FILTERED_NOT_ENTERED"
                c_net = 0.0
                c_exit_px = None
                c_exit_utc = None
                c_reason = "entry_volume_filter"
                c_hold_h = 0.0
            else:
                c_status = "ENTERED"
                c_net = a_net
                c_exit_px = a_exit_px
                c_exit_utc = a_exit_utc
                c_reason = a_reason
                c_hold_h = a_hold_h

            # D 组 (保本止损 + 缩量规则)
            if is_filt:
                d_status = "FILTERED_NOT_ENTERED"
                d_net = 0.0
                d_exit_px = None
                d_exit_utc = None
                d_reason = "entry_volume_filter"
                d_hold_h = 0.0
            else:
                d_status = "ENTERED"
                d_net = b_net
                d_exit_px = b_exit_px
                d_exit_utc = b_exit_utc
                d_reason = b_reason
                d_hold_h = b_hold_h

            # 差异归因
            c_delta = c_net - a_net
            d_delta = d_net - a_net
            b_delta = b_net - a_net

            # 少亏与错失盈利分解 (相比 A 基准)
            # C 组:
            c_saved_loss = abs(a_net) if (is_filt and a_net < 0) else 0.0
            c_forgone_profit = a_net if (is_filt and a_net > 0) else 0.0

            # D 组:
            if is_filt:
                d_saved_loss = abs(a_net) if a_net < 0 else 0.0
                d_forgone_profit = a_net if a_net > 0 else 0.0
            else:
                # 未过滤，跟随保本
                d_saved_loss = abs(a_net - b_net) if (a_net < 0 and b_net > a_net) else 0.0
                d_forgone_profit = (a_net - b_net) if (a_net > 0 and b_net < a_net) else 0.0

            rows.append({
                "signal_id": sid,
                "symbol": sym,
                "window": win,
                "entry_time_utc": entry_utc,
                "entry_price": entry_px,
                "qty": qty,
                "initial_stop": init_stop,
                "R_abs": r_abs,
                # A 组
                "A_status": "ENTERED",
                "A_net": a_net,
                "A_exit_price": a_exit_px,
                "A_exit_time_utc": a_exit_utc,
                "A_reason": a_reason,
                "A_hold_h": a_hold_h,
                # B 组 (保本)
                "B_status": "ENTERED",
                "B_net": b_net,
                "B_exit_price": b_exit_px,
                "B_exit_time_utc": b_exit_utc,
                "B_reason": b_reason,
                "B_hold_h": b_hold_h,
                "B_be_triggered": b_be_trig,
                "B_be_px": b_be_px,
                "B_delta": b_delta,
                # C 组 (仅缩量规则)
                "C_status": c_status,
                "C_net": c_net,
                "C_exit_price": c_exit_px,
                "C_exit_time_utc": c_exit_utc,
                "C_reason": c_reason,
                "C_hold_h": c_hold_h,
                "C_delta": c_delta,
                "C_saved_loss": c_saved_loss,
                "C_forgone_profit": c_forgone_profit,
                # D 组 (联合规则)
                "D_status": d_status,
                "D_net": d_net,
                "D_exit_price": d_exit_px,
                "D_exit_time_utc": d_exit_utc,
                "D_reason": d_reason,
                "D_hold_h": d_hold_h,
                "D_delta": d_delta,
                "D_saved_loss": d_saved_loss,
                "D_forgone_profit": d_forgone_profit,
            })

        df_out = pd.DataFrame(rows)
        csv_path = os.path.join(OUT_DIR, f"paired_trades_{r_label.lower()}.csv")
        df_out.to_csv(csv_path, index=False)
        print(f"Saved {len(df_out)} rows to {csv_path}")

        # 生成该套系的汇总统计
        compute_summary(df_out, r_label)


def compute_summary(df: pd.DataFrame, r_label: str):
    def stats(series, holds=None):
        n = len(series)
        s_sum = float(series.sum())
        s_mean = float(series.mean())
        s_std = float(series.std()) if n > 1 else 0.0
        t_stat = float(s_mean / (s_std / np.sqrt(n))) if s_std > 0 else 0.0
        p25 = float(np.percentile(series, 25))
        p50 = float(np.percentile(series, 50))
        p75 = float(np.percentile(series, 75))
        worst = float(series.min())
        best = float(series.max())
        worst5 = float(series.nsmallest(5).sum())
        losses = series[series < -1e-6]
        wins = series[series > 1e-6]
        n_loss = len(losses)
        n_win = len(wins)
        loss_rate = n_loss / n if n > 0 else 0.0
        win_rate = n_win / n if n > 0 else 0.0
        avg_loss = float(losses.mean()) if n_loss > 0 else 0.0
        avg_win = float(wins.mean()) if n_win > 0 else 0.0
        mean_hold = float(holds.mean()) if holds is not None else 0.0

        return {
            "n": n,
            "net_sum": s_sum,
            "net_mean": s_mean,
            "net_std": s_std,
            "t_stat": t_stat,
            "win_rate": win_rate,
            "loss_rate": loss_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "p25": p25,
            "p50": p50,
            "p75": p75,
            "worst_trade": worst,
            "worst_5_sum": worst5,
            "best_trade": best,
            "mean_hold_hours": mean_hold
        }

    sum_a = stats(df["A_net"], df["A_hold_h"])
    sum_b = stats(df["B_net"], df["B_hold_h"])
    sum_c = stats(df["C_net"], df[df["C_status"] == "ENTERED"]["C_hold_h"])
    sum_d = stats(df["D_net"], df[df["D_status"] == "ENTERED"]["D_hold_h"])

    # 差额分析
    c_saved = float(df["C_saved_loss"].sum())
    c_forgone = float(df["C_forgone_profit"].sum())
    d_saved = float(df["D_saved_loss"].sum())
    d_forgone = float(df["D_forgone_profit"].sum())

    summary = {
        "r_label": r_label,
        "n_total_candidates": len(df),
        "A_baseline": sum_a,
        "B_breakeven_only": sum_b,
        "C_volume_only": sum_c,
        "D_combined": sum_d,
        "attribution_C_vs_A": {
            "saved_loss": c_saved,
            "forgone_profit": c_forgone,
            "net_delta": float(df["C_delta"].sum()),
            "n_filtered": int((df["C_status"] == "FILTERED_NOT_ENTERED").sum()),
            "n_entered": int((df["C_status"] == "ENTERED").sum())
        },
        "attribution_D_vs_A": {
            "saved_loss": d_saved,
            "forgone_profit": d_forgone,
            "net_delta": float(df["D_delta"].sum())
        },
        "combined_vs_single": {
            "D_minus_B": float(df["D_net"].sum() - df["B_net"].sum()),
            "D_minus_C": float(df["D_net"].sum() - df["C_net"].sum()),
            "overlap_analysis": "D组中，在96笔实际成交中进一步叠加保本规则；在110笔被过滤交易中保本规则不再有机会执行。"
        }
    }

    out_file = os.path.join(OUT_DIR, f"summary_{r_label.lower()}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary written to {out_file}")


if __name__ == "__main__":
    build_abcd_sets()

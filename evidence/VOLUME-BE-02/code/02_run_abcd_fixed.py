# -*- coding: utf-8 -*-
"""VOLUME-BE-02 · 步骤二：构建 1R 与 2R 两套完整的 A/B/C/D 四组联合对照及 201 事件去重。

整改修复：
1. 修正事件级去重：按 (symbol, entry_time_utc) 分组，严格按 int(group) 较小者保留（窗口先到先得原则，绝不按盈亏选择）；
2. 明确输出 duplicate_groups.json，列出 5 组重复项的完整身份、保留依据与两行对比；
3. 201 笔事件级去重结果精确输出，包含四组对照、差异分解与分期统计。
"""
from __future__ import annotations

import collections
import csv
import json
import os
import sys
import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
BE2_PAIRED = os.path.join(ROOT, "evidence/BREAKEVEN-02/out/paired_fixed.csv")
HYP_DETAIL = os.path.join(ROOT, "evidence/VOLUME-BE-02/out/hypotheses_evaluation_detail.csv")
OUT_DIR = os.path.join(ROOT, "evidence/VOLUME-BE-02/out")
os.makedirs(OUT_DIR, exist_ok=True)


def identify_duplicate_groups(df: pd.DataFrame) -> Tuple[set, list]:
    """按 (symbol, entry_time_utc) 识别重复组，保留 int(group) 最小者，禁止按盈亏挑选。"""
    groups = collections.defaultdict(list)
    for _, row in df.iterrows():
        key = (row["symbol"], row["entry_time_utc"])
        groups[key].append(row)

    dropped_ids = set()
    dup_details = []

    for key, members in groups.items():
        if len(members) > 1:
            # 严格按 int(group) 升序排序：保留最先出现的较小窗口实例
            sorted_m = sorted(members, key=lambda r: int(r["group"]))
            kept = sorted_m[0]
            dropped = sorted_m[1:]
            for d in dropped:
                dropped_ids.add(d["signal_id"])

            dup_details.append({
                "symbol": key[0],
                "entry_time_utc": key[1],
                "retention_rule": "保留 int(group) 较小的回测窗口实例（时序先到先得原则，不按盈亏选择）",
                "kept_instance": {
                    "signal_id": kept["signal_id"],
                    "group": int(kept["group"]),
                    "window": kept["window"],
                    "A_net": float(kept["A_net"]),
                    "B_net": float(kept["B_net"]),
                    "C_net": float(kept["C_net"]),
                },
                "dropped_instances": [
                    {
                        "signal_id": d["signal_id"],
                        "group": int(d["group"]),
                        "window": d["window"],
                        "A_net": float(d["A_net"]),
                        "B_net": float(d["B_net"]),
                        "C_net": float(d["C_net"]),
                    }
                    for d in dropped
                ]
            })

    return dropped_ids, dup_details


def build_abcd_sets():
    df_be2 = pd.read_csv(BE2_PAIRED)
    df_hyp = pd.read_csv(HYP_DETAIL)

    merged = pd.merge(
        df_be2,
        df_hyp[[
            "signal_id", "prev_1h_category", "prev_1h_low_vol_drop", "prev_1h_high_vol_drop",
            "m15_category", "m15_low_vol_drop", "m15_high_vol_drop",
            "pos_exit_triggered", "pos_exit_net", "pos_exit_time_ms", "pos_exit_px"
        ]],
        on="signal_id",
        how="inner"
    )

    if len(merged) != 206:
        raise ValueError(f"Merged count mismatch: expected 206, got {len(merged)}")

    # 动态识别重复事件
    dropped_ids, dup_details = identify_duplicate_groups(merged)
    print(f"Total duplicate groups identified: {len(dup_details)}, dropped signals: {len(dropped_ids)}")
    assert len(dropped_ids) == 5, f"Expected exactly 5 dropped signals, got {len(dropped_ids)}"

    # 保存重复组明细文件
    with open(os.path.join(OUT_DIR, "duplicate_groups.json"), "w", encoding="utf-8") as f:
        json.dump(dup_details, f, indent=2, ensure_ascii=False)
    print("duplicate_groups.json saved.")

    merged["is_event_dedup"] = ~merged["signal_id"].isin(dropped_ids)
    print(f"Candidate count: {len(merged)}, Event-level count: {merged['is_event_dedup'].sum()}")
    assert merged["is_event_dedup"].sum() == 201, f"Expected 201 events, got {merged['is_event_dedup'].sum()}"

    # 主假设：突破前夕 1h 缩量下跌过滤 (prev_1h_low_vol_drop)
    merged["is_filtered_main"] = merged["prev_1h_low_vol_drop"]

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

            # A 组
            a_net = float(row["A_net"])
            a_exit_px = float(row["A_exit_price"])
            a_exit_utc = row["A_exit_time_utc"]
            a_reason = row["A_reason"]
            a_hold_h = float(row["A_hold_h"])

            # B 组 (保本)
            b_net = float(row[f"{be_col_prefix}_net"])
            b_exit_px = float(row[f"{be_col_prefix}_exit_price"])
            b_exit_utc = row[f"{be_col_prefix}_exit_time_utc"]
            b_reason = row[f"{be_col_prefix}_reason"]
            b_hold_h = float(row[f"{be_col_prefix}_hold_h"])
            b_be_trig = bool(row[f"{be_col_prefix}_be_triggered"])
            b_be_px = row[f"{be_col_prefix}_be_px"]

            # C 组 (主假设：仅突破前 1h 缩量规则)
            is_filt = bool(row["is_filtered_main"])
            if is_filt:
                c_status = "FILTERED_NOT_ENTERED"
                c_net = 0.0
                c_exit_px = None
                c_exit_utc = None
                c_reason = "entry_volume_filter_low_vol_drop"
                c_hold_h = 0.0
            else:
                c_status = "ENTERED"
                c_net = a_net
                c_exit_px = a_exit_px
                c_exit_utc = a_exit_utc
                c_reason = a_reason
                c_hold_h = a_hold_h

            # D 组 (联合：保本 + 缩量规则)
            if is_filt:
                d_status = "FILTERED_NOT_ENTERED"
                d_net = 0.0
                d_exit_px = None
                d_exit_utc = None
                d_reason = "entry_volume_filter_low_vol_drop"
                d_hold_h = 0.0
            else:
                d_status = "ENTERED"
                d_net = b_net
                d_exit_px = b_exit_px
                d_exit_utc = b_exit_utc
                d_reason = b_reason
                d_hold_h = b_hold_h

            # 差异分解
            c_delta = c_net - a_net
            d_delta = d_net - a_net
            b_delta = b_net - a_net

            c_saved_loss = abs(a_net) if (is_filt and a_net < 0) else 0.0
            c_forgone_profit = a_net if (is_filt and a_net > 0) else 0.0

            if is_filt:
                d_saved_loss = abs(a_net) if a_net < 0 else 0.0
                d_forgone_profit = a_net if a_net > 0 else 0.0
            else:
                d_saved_loss = abs(a_net - b_net) if (a_net < 0 and b_net > a_net) else 0.0
                d_forgone_profit = (a_net - b_net) if (a_net > 0 and b_net < a_net) else 0.0

            rows.append({
                "signal_id": sid,
                "symbol": sym,
                "window": win,
                "group": int(row["group"]),
                "is_event_dedup": bool(row["is_event_dedup"]),
                "entry_time_utc": entry_utc,
                "entry_price": entry_px,
                "qty": qty,
                "initial_stop": init_stop,
                "R_abs": r_abs,
                "prev_1h_category": row["prev_1h_category"],
                "m15_category": row["m15_category"],
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
        mean_hold = float(holds.mean()) if holds is not None and len(holds) > 0 else 0.0

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

    # 1. 全候选级 (206 笔)
    sum_a_206 = stats(df["A_net"], df["A_hold_h"])
    sum_b_206 = stats(df["B_net"], df["B_hold_h"])
    sum_c_206 = stats(df["C_net"], df[df["C_status"] == "ENTERED"]["C_hold_h"])
    sum_d_206 = stats(df["D_net"], df[df["D_status"] == "ENTERED"]["D_hold_h"])

    # 2. 事件级去重 (201 笔)
    df_201 = df[df["is_event_dedup"] == True]
    sum_a_201 = stats(df_201["A_net"], df_201["A_hold_h"])
    sum_b_201 = stats(df_201["B_net"], df_201["B_hold_h"])
    sum_c_201 = stats(df_201["C_net"], df_201[df_201["C_status"] == "ENTERED"]["C_hold_h"])
    sum_d_201 = stats(df_201["D_net"], df_201[df_201["D_status"] == "ENTERED"]["D_hold_h"])

    summary = {
        "r_label": r_label,
        "universe_206_candidates": {
            "n_total": len(df),
            "A_baseline": sum_a_206,
            "B_breakeven_only": sum_b_206,
            "C_volume_only": sum_c_206,
            "D_combined": sum_d_206,
            "attribution_C_vs_A": {
                "saved_loss": float(df["C_saved_loss"].sum()),
                "forgone_profit": float(df["C_forgone_profit"].sum()),
                "net_delta": float(df["C_delta"].sum()),
                "n_filtered": int((df["C_status"] == "FILTERED_NOT_ENTERED").sum()),
                "n_entered": int((df["C_status"] == "ENTERED").sum())
            },
            "attribution_D_vs_A": {
                "saved_loss": float(df["D_saved_loss"].sum()),
                "forgone_profit": float(df["D_forgone_profit"].sum()),
                "net_delta": float(df["D_delta"].sum())
            },
            "combined_vs_single": {
                "D_minus_B": float(df["D_net"].sum() - df["B_net"].sum()),
                "D_minus_C": float(df["D_net"].sum() - df["C_net"].sum()),
            }
        },
        "event_level_201_dedup": {
            "n_total": len(df_201),
            "A_baseline": sum_a_201,
            "B_breakeven_only": sum_b_201,
            "C_volume_only": sum_c_201,
            "D_combined": sum_d_201,
            "attribution_C_vs_A": {
                "saved_loss": float(df_201["C_saved_loss"].sum()),
                "forgone_profit": float(df_201["C_forgone_profit"].sum()),
                "net_delta": float(df_201["C_delta"].sum()),
                "n_filtered": int((df_201["C_status"] == "FILTERED_NOT_ENTERED").sum()),
                "n_entered": int((df_201["C_status"] == "ENTERED").sum())
            },
            "attribution_D_vs_A": {
                "saved_loss": float(df_201["D_saved_loss"].sum()),
                "forgone_profit": float(df_201["D_forgone_profit"].sum()),
                "net_delta": float(df_201["D_delta"].sum())
            },
            "combined_vs_single": {
                "D_minus_B": float(df_201["D_net"].sum() - df_201["B_net"].sum()),
                "D_minus_C": float(df_201["D_net"].sum() - df_201["C_net"].sum()),
            }
        }
    }

    out_file = os.path.join(OUT_DIR, f"summary_{r_label.lower()}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary written to {out_file}")


if __name__ == "__main__":
    build_abcd_sets()

# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-02 / 04 · FACTOR-ENTRY-05 交付物内部一致性与口径核查

只读。核对「报告正文 / 交付表 / 汇总 json」三者是否互相印证，
并复现 05 分类塌缩的成因（其输入表没有 mfe_r 列）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-02"
FE05 = ROOT / "evidence" / "FACTOR-ENTRY-05"

df01 = pd.read_csv(ROOT / "evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.csv").set_index("candidate_id")
df03 = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet").set_index("candidate_id")
df04 = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-04/candidate_evaluations_audit.parquet").set_index("candidate_id")
fe5 = pd.read_parquet(FE05 / "reconstructed_candidate_features.parquet").set_index("candidate_id")
atr_audit = pd.read_csv(FE05 / "atr_source_audit.csv").set_index("candidate_id")
diff = pd.read_csv(FE05 / "discrepancy_old_vs_new.csv")
mine = pd.read_csv(OUT / "03_atr_recompute.csv").set_index("candidate_id")

c = {}

# ── 1. 「42 笔用止损距离替代 ATR」是否属实（三处独立事实） ──
fb_ids = [c for c in df01.index if pd.isna(df01.loc[c, "box_atr_4h"])]
c["fallback_ids_from_FE01"] = len(fb_ids)
c["fallback_ids_all_old_cand"] = bool(all(str(x).startswith("old_cand") for x in fb_ids))
c["FE04_box_atr_equals_initial_risk_r_max_abs_diff"] = float(
    max(abs(float(df04.loc[i, "box_atr_4h"]) - float(df01.loc[i, "initial_risk_r"])) for i in fb_ids))
c["FE03_box_atr_is_nan_for_all_42"] = bool(df03.loc[fb_ids, "box_atr_4h"].isna().all())
c["stopdist_over_true_atr_median"] = float((df01.loc[fb_ids, "initial_risk_r"] / mine.loc[fb_ids, "atr_mine"]).median())
c["stopdist_over_true_atr_min"] = float((df01.loc[fb_ids, "initial_risk_r"] / mine.loc[fb_ids, "atr_mine"]).min())
c["stopdist_over_true_atr_max"] = float((df01.loc[fb_ids, "initial_risk_r"] / mine.loc[fb_ids, "atr_mine"]).max())

# ── 2. 交付表的分类分布 vs 报告正文声称的分布 ──
dist_delivered = fe5["entry_quality_category"].value_counts().to_dict()
c["delivered_cat_dist"] = dist_delivered
c["report_claimed_new_cat"] = {"EARLY_WEAKNESS": 67, "SUSTAINED_TREND": 36, "MFE_PULLBACK": 8, "CHOPPY_OR_OTHER": 94}
c["delivered_matches_report"] = bool(
    dist_delivered.get("SUSTAINED_TREND", 0) == 36 and dist_delivered.get("MFE_PULLBACK", 0) == 8)

# ── 3. 复现塌缩成因：05 的输入表没有 mfe_r 列 ⇒ mfe_r_full 恒为 0 ──
c["FE04_has_mfe_r_column"] = "mfe_r" in df04.columns
c["FE05_input_has_mfe_r_column"] = "mfe_r" in pd.read_parquet(
    ROOT / "evidence/FACTOR-ENTRY-04/candidate_evaluations_audit.parquet").columns

sim = []
for cid in fe5.index:
    r03 = df03.loc[cid]
    mae = float(fe5.loc[cid, "mae_r_4h"])
    mfe = float(fe5.loc[cid, "mfe_r_4h"])
    fm = str(fe5.loc[cid, "first_move_4h"])
    ret4 = float(df04.loc[cid, "fwd_ret_4h"]) if pd.notna(df04.loc[cid, "fwd_ret_4h"]) else np.nan
    net = float(df03.loc[cid, "net_pnl"])
    mfe_r_full = 0.0   # ← 05 的输入表无 mfe_r，row.get 返回 None ⇒ 恒 0
    if pd.notna(ret4) and ret4 < 0 and mfe < 0.5 and (mae >= 0.5 or fm == "FIRST_ADVERSE"):
        cat = "EARLY_WEAKNESS"
    elif mfe_r_full >= 1.0 and net <= 0:
        cat = "MFE_PULLBACK"
    elif mfe_r_full >= 1.5 and net > 0:
        cat = "SUSTAINED_TREND"
    else:
        cat = "CHOPPY_OR_OTHER"
    sim.append((cid, cat))
sim = pd.Series(dict(sim))
c["simulated_bug_matches_delivered"] = int((sim == fe5["entry_quality_category"]).sum())
c["simulated_bug_dist"] = sim.value_counts().to_dict()

# ── 4. 05 的 ATR 审计表自身状态 ──
c["atr_audit_rows"] = int(len(atr_audit))
c["atr_audit_recon_status"] = atr_audit["recon_status"].value_counts().to_dict()
c["atr_audit_warmup_min"] = int(atr_audit["closed_4h_warmup_bars"].min())
_idx = mine.index.intersection(atr_audit.index)
c["atr_audit_warmup_matches_mine"] = int((atr_audit.loc[_idx, "closed_4h_warmup_bars"] == mine.loc[_idx, "warmup_bars"]).sum())
c["atr_audit_last_closed_matches_mine"] = int(
    ((pd.to_datetime(atr_audit.loc[_idx, "last_closed_4h_utc"]).values.astype("datetime64[ms]").astype(np.int64)
      - pd.to_datetime(mine.loc[_idx, "last_closed_4h_ms"], unit="ms").values.astype("datetime64[ms]").astype(np.int64)
      ).__abs__() < 1000).sum())
c["atr_audit_last_closed_note"] = "05 用 strftime 输出到秒，丢了 999ms；按 1 秒容差比对"
c["atr_audit_compared_rows"] = int(len(_idx))

# ── 5. 报告正文的数值表述核对 ──
c["report_says_amplify_range"] = "4.0 ~ 10.8 倍"
c["actual_amplify_range"] = [round(c["stopdist_over_true_atr_min"], 2), round(c["stopdist_over_true_atr_max"], 2)]
summary = json.load(open(FE05 / "discrepancy_summary.json", encoding="utf-8"))
c["summary_first_adverse_pnl"] = summary["financial_fact_vs_causation"]["first_adverse_pnl_sum"]
c["summary_stale_text_says"] = summary["financial_fact_vs_causation"]["causal_statement_retracted"]
c["summary_stale_text_uses_old_number_142_23"] = "+142.23" in c["summary_stale_text_says"]

# ── 6. 「13 笔 + 1 笔」与净增 12 的关系 ──
c["flip_in_total"] = int((diff["flipped_to_early_weakness"] == True).sum())
c["flip_out_total"] = int(((diff["old_entry_category"] == "EARLY_WEAKNESS")
                           & (diff["new_entry_category"] != "EARLY_WEAKNESS")).sum())
c["net_change"] = int((diff["new_entry_category"] == "EARLY_WEAKNESS").sum()
                      - (diff["old_entry_category"] == "EARLY_WEAKNESS").sum())

# ── 7. 两把尺子是否仍并存 ──
c["full_period_mfe_r_uses_stop_distance"] = bool(
    float((df03["mfe_r"] - df03["trade_mfe_pct"] / (df03["initial_risk_r"] / df03["p_entry"])).abs().max()) < 1e-9)
c["labels_changed_if_unified_ruler"] = int((mine["cat_unified"] != mine["cat_mine"]).sum())
c["dist_if_unified_ruler"] = mine["cat_unified"].value_counts().to_dict()

json.dump(c, open(OUT / "04_fe05_consistency.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
print(json.dumps(c, ensure_ascii=False, indent=2, default=str))

# ── 8. 逐笔分类差异表 ──
diff_tbl = mine[["symbol", "entry_time_utc", "is_old_cand", "atr_01_raw", "atr_mine",
                 "fe05_signal_atr", "mae_r_mine", "fe05_mae_r", "mfe_r_mine", "fe05_mfe_r",
                 "first_move_mine", "fe05_first_move", "cat_mine", "cat_unified", "fe05_cat", "net_pnl"]].copy()
diff_tbl["first_move_diff"] = diff_tbl["first_move_mine"] != diff_tbl["fe05_first_move"]
diff_tbl["cat_diff_vs_delivered"] = diff_tbl["cat_mine"] != diff_tbl["fe05_cat"]
diff_tbl["cat_diff_if_unified"] = diff_tbl["cat_unified"] != diff_tbl["cat_mine"]
diff_tbl.to_csv(OUT / "03_classification_diff.csv", index=False, encoding="utf-8-sig")
print("\n分类差异行数（我的复算 vs 05 交付表）:", int(diff_tbl["cat_diff_vs_delivered"].sum()))
print("首触差异行数:", int(diff_tbl["first_move_diff"].sum()))
print("若统一尺子会改变标签的行数:", int(diff_tbl["cat_diff_if_unified"].sum()))
print(diff_tbl[diff_tbl["cat_diff_vs_delivered"]][
    ["symbol", "entry_time_utc", "fe05_cat", "cat_mine", "cat_unified", "net_pnl"]].head(50).to_string())

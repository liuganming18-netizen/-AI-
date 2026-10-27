# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-02 / 03 · 第二阶段：ATR 修正独立复核

被审计对象：evidence/FACTOR-ENTRY-05/（2026-10-18 15:36–15:39 交付版本，哈希见 03_fe05_frozen.json）
本脚本不 import 被审计方的任何计算函数用于生产结果，Wilder ATR 由本脚本独立实现；
另用正式策略 features.py 的实现做一次「公式是否同源」的旁证。

输出：
  03_atr_recompute.csv      205 笔逐条：我的 ATR / 05 的 ATR / 原 box_atr / 差异
  03_classification_diff.csv 205 笔逐条：分类与首触的 05 值 vs 我的复算
  03_checks.json            逐项结论
  03_fe05_frozen.json       FACTOR-ENTRY-05 冻结版本与哈希
"""
from __future__ import annotations

import glob
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-02"
PARQUET = ROOT / "数据" / "市场数据" / "parquet"
FE05 = ROOT / "evidence" / "FACTOR-ENTRY-05"

# ── 0. 冻结被审计版本 ──
def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


frozen = {p.name: {"sha256": sha(p), "size": p.stat().st_size,
                   "mtime_utc": pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC").strftime("%Y-%m-%d %H:%M:%S")}
          for p in sorted(FE05.iterdir()) if p.is_file()}
json.dump(frozen, open(OUT / "03_fe05_frozen.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("已冻结 FACTOR-ENTRY-05：", len(frozen), "个文件")

# ── 1. 独立 Wilder ATR(14)（本脚本自写，不调用被审计方函数）──
def my_wilder_atr(high, low, close, period=14):
    n = len(high)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = np.full(n, np.nan)
    if n < period:
        return atr
    atr[period - 1] = tr[:period].mean()
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


# ── 2. 读入三份事实表 ──
df01 = pd.read_csv(ROOT / "evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.csv")
df03 = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet")
fe5 = pd.read_parquet(FE05 / "reconstructed_candidate_features.parquet")
fe5_diff = pd.read_csv(FE05 / "discrepancy_old_vs_new.csv")
fe5_atr = pd.read_csv(FE05 / "atr_source_audit.csv")

df01 = df01.set_index("candidate_id")
fe5 = fe5.set_index("candidate_id")
filled = df03[df03["exec_status"] == "FILLED"].copy()
print("205 成交：", len(filled))

# ── 3. 逐标的加载 4h/15m，独立复算 ──
rows = []
formula_cmp = []
for sym, grp in filled.groupby("symbol"):
    f4 = sorted(glob.glob(str(PARQUET / f"candles/binance/um/{sym}/4h/*.parquet")))
    f15 = sorted(glob.glob(str(PARQUET / f"candles/binance/um/{sym}/15m/*.parquet")))
    if not f4 or not f15:
        print(f"[缺数据] {sym}")
        continue
    d4 = pd.concat([pd.read_parquet(f, columns=["open_time", "close_time", "high", "low", "close"]) for f in f4],
                   ignore_index=True).sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    d15 = pd.concat([pd.read_parquet(f, columns=["open_time", "open", "high", "low", "close"]) for f in f15],
                    ignore_index=True).sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    for _c in ("high", "low", "close"):
        d4[_c] = pd.to_numeric(d4[_c], errors="coerce")
    for _c in ("open", "high", "low", "close"):
        d15[_c] = pd.to_numeric(d15[_c], errors="coerce")
    d4 = d4.dropna(subset=["high", "low", "close"]).reset_index(drop=True)
    d15 = d15.dropna(subset=["high", "low", "close"]).reset_index(drop=True)

    hi, lo, cl = d4["high"].to_numpy(np.float64), d4["low"].to_numpy(np.float64), d4["close"].to_numpy(np.float64)
    atr_mine = my_wilder_atr(hi, lo, cl, 14)

    # 公式同源性旁证：与正式策略实现比对（仅比对，不用于结果）
    sys.path.insert(0, str(ROOT / "策略"))
    from WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series
    atr_official = compute_wilder_atr_series(hi, lo, cl, 14)
    both = np.isfinite(atr_mine) & np.isfinite(atr_official)
    formula_cmp.append({"symbol": sym, "n": int(both.sum()),
                        "max_abs_diff": float(np.max(np.abs(atr_mine[both] - atr_official[both]))) if both.any() else None})

    t4_close = d4["close_time"].to_numpy(np.int64)
    t15 = d15["open_time"].to_numpy(np.int64)

    for _, r in grp.iterrows():
        cid = r["candidate_id"]
        t_in = int(r["entry_time"])
        p_entry = float(r["p_entry"])

        # 基准时刻：入场时刻之前已闭合的最后一根 4h（close_time 严格早于 t_in）
        k = int(np.searchsorted(t4_close, t_in, side="left")) - 1
        atr_sig = float(atr_mine[k]) if k >= 0 else np.nan
        last_closed_ms = int(t4_close[k]) if k >= 0 else None
        warmup = k + 1

        # 4h 观察窗（16 根 15m）
        i0 = int(np.searchsorted(t15, t_in, side="left"))
        sub = d15.iloc[i0:i0 + 16]
        h4, l4 = float(sub["high"].max()), float(sub["low"].min())
        ret4 = float((d15.iloc[i0 + 16]["close"] - p_entry) / p_entry) if i0 + 16 < len(d15) else np.nan
        mfe_r = (h4 - p_entry) / atr_sig if np.isfinite(atr_sig) and atr_sig > 0 else np.nan
        mae_r = (p_entry - l4) / atr_sig if np.isfinite(atr_sig) and atr_sig > 0 else np.nan

        # 首触 ±0.3ATR
        thf, tha = p_entry + 0.3 * atr_sig, p_entry - 0.3 * atr_sig
        fav = sub.index[sub["high"].astype(float) >= thf]
        adv = sub.index[sub["low"].astype(float) <= tha]
        tf = int(sub.loc[fav[0], "open_time"]) if len(fav) else None
        ta = int(sub.loc[adv[0], "open_time"]) if len(adv) else None
        if tf is not None and ta is not None:
            first = "FIRST_ADVERSE" if ta < tf else ("FIRST_FAVORABLE" if tf < ta else "UNKNOWN_SAME_BAR")
        elif ta is not None:
            first = "FIRST_ADVERSE"
        elif tf is not None:
            first = "FIRST_FAVORABLE"
        else:
            first = "NEITHER_REACHED_0.3ATR"

        # 分类（与 05 完全同一规则，用于隔离算术；R 尺子仍沿用 05 的做法）
        mfe_r_full = float(r["mfe_r"]) if pd.notna(r["mfe_r"]) else 0.0
        net = float(r["net_pnl"])
        if pd.notna(ret4) and ret4 < 0 and np.isfinite(mfe_r) and mfe_r < 0.5 and (mae_r >= 0.5 or first == "FIRST_ADVERSE"):
            cat = "EARLY_WEAKNESS"
        elif mfe_r_full >= 1.0 and net <= 0:
            cat = "MFE_PULLBACK"
        elif mfe_r_full >= 1.5 and net > 0:
            cat = "SUSTAINED_TREND"
        else:
            cat = "CHOPPY_OR_OTHER"

        # 统一尺子变体：把「浮盈/回吐」也改用 4h ATR 尺（mfe_r_4h / 全周期 mfe 用 ATR）
        mfe_r_4h_only = mfe_r
        if pd.notna(ret4) and ret4 < 0 and np.isfinite(mfe_r_4h_only) and mfe_r_4h_only < 0.5 and (mae_r >= 0.5 or first == "FIRST_ADVERSE"):
            cat_unified = "EARLY_WEAKNESS"
        elif np.isfinite(mfe_r_4h_only) and mfe_r_4h_only >= 1.0 and net <= 0:
            cat_unified = "MFE_PULLBACK"
        elif np.isfinite(mfe_r_4h_only) and mfe_r_4h_only >= 1.5 and net > 0:
            cat_unified = "SUSTAINED_TREND"
        else:
            cat_unified = "CHOPPY_OR_OTHER"

        rows.append({
            "candidate_id": cid,
            "symbol": sym,
            "entry_time_ms": t_in,
            "entry_time_utc": str(pd.to_datetime(t_in, unit="ms")),
            "p_entry": p_entry,
            "net_pnl": net,
            "is_old_cand": str(cid).startswith("old_cand"),
            "atr_01_raw": (float(df01.loc[cid, "box_atr_4h"])
                           if cid in df01.index and pd.notna(df01.loc[cid, "box_atr_4h"]) else np.nan),
            "initial_risk_r": float(df01.loc[cid, "initial_risk_r"]) if cid in df01.index else np.nan,
            "box_atr_03": float(r["box_atr_4h"]) if pd.notna(r["box_atr_4h"]) else np.nan,
            "fe05_signal_atr": (float(fe5.loc[cid, "signal_atr_snapshot"])
                                if cid in fe5.index and pd.notna(fe5.loc[cid, "signal_atr_snapshot"]) else np.nan),
            "atr_mine": atr_sig,
            "last_closed_4h_ms": last_closed_ms,
            "warmup_bars": warmup,
            "atr_rel_diff_vs_fe05": (abs(atr_sig - float(fe5.loc[cid, "signal_atr_snapshot"]))
                                     / float(fe5.loc[cid, "signal_atr_snapshot"])
                                     if cid in fe5.index and pd.notna(fe5.loc[cid, "signal_atr_snapshot"])
                                     and float(fe5.loc[cid, "signal_atr_snapshot"]) > 0 else np.nan),
            "mae_r_mine": mae_r,
            "mfe_r_mine": mfe_r,
            "fe05_mae_r": (float(fe5.loc[cid, "mae_r_4h"]) if cid in fe5.index and pd.notna(fe5.loc[cid, "mae_r_4h"]) else np.nan),
            "fe05_mfe_r": (float(fe5.loc[cid, "mfe_r_4h"]) if cid in fe5.index and pd.notna(fe5.loc[cid, "mfe_r_4h"]) else np.nan),
            "first_move_mine": first,
            "fe05_first_move": (str(fe5.loc[cid, "first_move_4h"]) if cid in fe5.index else ""),
            "cat_mine": cat,
            "cat_unified": cat_unified,
            "fe05_cat": (str(fe5.loc[cid, "entry_quality_category"]) if cid in fe5.index else ""),
            "ret4_mine": ret4,
        })
    del d4, d15, hi, lo, cl, atr_mine, atr_official

rec = pd.DataFrame(rows)
rec.to_csv(OUT / "03_atr_recompute.csv", index=False, encoding="utf-8-sig")

# ── 4. 逐项核查 ──
chk = {}
chk["n_filled"] = int(len(rec))
chk["n_old_cand"] = int(rec["is_old_cand"].sum())
chk["n_cand_ev"] = int((~rec["is_old_cand"]).sum())

# 4.1 42 笔回退事实
fb = rec[rec["atr_01_raw"].isna()]
chk["fallback_records"] = int(len(fb))
chk["fallback_all_old_cand"] = bool((fb["is_old_cand"]).all())
chk["fallback_box_atr_03_equals_initial_risk_r_max_diff"] = float((fb["box_atr_03"] - fb["initial_risk_r"]).abs().max())
chk["fallback_ratio_stopdist_to_true_atr_median"] = float((fb["initial_risk_r"] / fb["atr_mine"]).median())
chk["fallback_ratio_min"] = float((fb["initial_risk_r"] / fb["atr_mine"]).min())
chk["fallback_ratio_max"] = float((fb["initial_risk_r"] / fb["atr_mine"]).max())

# 4.2 我的 ATR 与 05 重建值一致性
d = rec["atr_rel_diff_vs_fe05"]
chk["atr_rel_diff_max"] = float(d.max())
chk["atr_rel_diff_median"] = float(d.median())
chk["atr_match_within_1e-9"] = int((d < 1e-9).sum())
chk["atr_mismatch_rows"] = int((d >= 1e-9).sum())

# 4.3 无前视：最后一根闭合 4h 必须严格早于入场
chk["lookahead_violations"] = int((rec["last_closed_4h_ms"] >= rec["entry_time_ms"]).sum())
chk["warmup_min"] = int(rec["warmup_bars"].min())
chk["warmup_median"] = float(rec["warmup_bars"].median())
chk["warmup_below_42"] = int((rec["warmup_bars"] < 42).sum())

# 4.4 两套 ATR 语义是否被混用
have_box = rec[rec["atr_01_raw"].notna()]
chk["box_vs_signal_atr_median_rel_diff"] = float(
    ((have_box["atr_01_raw"] - have_box["atr_mine"]).abs() / have_box["atr_mine"]).median())
chk["box_equals_signal_count"] = int(
    (((have_box["atr_01_raw"] - have_box["atr_mine"]).abs() / have_box["atr_mine"]) < 1e-12).sum())
chk["fe05_keeps_box_atr_separate"] = bool(
    (fe5.loc[have_box["candidate_id"], "box_atr_formation"].notna()).all()
    and (fe5.loc[fb["candidate_id"], "box_atr_formation"].isna()).all())

# 4.5 算术一致性：MAE/MFE/首触/分类
chk["mae_r_max_abs_diff"] = float((rec["mae_r_mine"] - rec["fe05_mae_r"]).abs().max())
chk["mfe_r_max_abs_diff"] = float((rec["mfe_r_mine"] - rec["fe05_mfe_r"]).abs().max())
chk["first_move_disagree"] = int((rec["first_move_mine"] != rec["fe05_first_move"]).sum())
chk["cat_disagree"] = int((rec["cat_mine"] != rec["fe05_cat"]).sum())
chk["cat_unified_vs_fe05_disagree"] = int((rec["cat_unified"] != rec["fe05_cat"]).sum())

# 4.6 分布
chk["dist_fe05_first_move"] = rec["fe05_first_move"].value_counts().to_dict()
chk["dist_mine_first_move"] = rec["first_move_mine"].value_counts().to_dict()
chk["dist_fe05_cat"] = rec["fe05_cat"].value_counts().to_dict()
chk["dist_mine_cat"] = rec["cat_mine"].value_counts().to_dict()
chk["dist_unified_cat"] = rec["cat_unified"].value_counts().to_dict()

# 4.7 同根未知是否被强行归类
same_bar = rec[rec["first_move_mine"] == "UNKNOWN_SAME_BAR"]
chk["same_bar_unknown"] = int(len(same_bar))
chk["fe05_unknown_same_bar"] = int((rec["fe05_first_move"] == "UNKNOWN_SAME_BAR").sum())
chk["same_bar_forced_in_fe05"] = int((same_bar["fe05_first_move"] != "UNKNOWN_SAME_BAR").sum())

# 4.8 盈亏分组（用我的首触/分类）
adv = rec[rec["first_move_mine"] == "FIRST_ADVERSE"]
non = rec[rec["first_move_mine"] != "FIRST_ADVERSE"]
chk["pnl_first_adverse"] = round(float(adv["net_pnl"].sum()), 2)
chk["pnl_non_first_adverse"] = round(float(non["net_pnl"].sum()), 2)
chk["n_first_adverse"] = int(len(adv))
weak = rec[rec["cat_mine"] == "EARLY_WEAKNESS"]
chk["n_early_weakness_mine"] = int(len(weak))
chk["pnl_early_weakness"] = round(float(weak["net_pnl"].sum()), 2)
chk["n_early_weakness_fe05"] = int((rec["fe05_cat"] == "EARLY_WEAKNESS").sum())
chk["n_early_weakness_unified"] = int((rec["cat_unified"] == "EARLY_WEAKNESS").sum())

# 4.9 05 自报的「13 笔来自回退组」核对
flip = fe5_diff[fe5_diff["flipped_to_early_weakness"] == True]
chk["fe05_flipped_to_weak_total"] = int(len(flip))
chk["fe05_flipped_from_fallback_group"] = int(flip["is_fallback_record"].sum())
chk["fe05_flipped_from_true_atr_group"] = int((~flip["is_fallback_record"]).sum())
chk["fe05_old_weak"] = int((fe5_diff["old_entry_category"] == "EARLY_WEAKNESS").sum())
chk["fe05_new_weak"] = int((fe5_diff["new_entry_category"] == "EARLY_WEAKNESS").sum())

# 4.10 全周期 mfe_r 的分母仍是止损距离（两把尺子是否仍并存）
chk["full_mfe_r_denominator_is_stop_distance"] = bool(
    (abs(filled.set_index("candidate_id")["mfe_r"]
         - filled.set_index("candidate_id")["trade_mfe_pct"]
         / (filled.set_index("candidate_id")["initial_risk_r"] / filled.set_index("candidate_id")["p_entry"])).max()) < 1e-9)
chk["formula_same_as_official_max_abs_diff"] = float(max(x["max_abs_diff"] for x in formula_cmp if x["max_abs_diff"] is not None))
chk["formula_symbols_compared"] = int(len(formula_cmp))

json.dump(chk, open(OUT / "03_checks.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
print(json.dumps(chk, ensure_ascii=False, indent=2, default=str))

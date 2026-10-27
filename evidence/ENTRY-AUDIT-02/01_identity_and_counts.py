# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-02 / 01 · 事件身份、计数闭合、重复审计

只读。输出：
  01_counts.json               全部计数与闭合等式
  01_mapping_227.csv           227 行逐条身份映射（特征行→唯一候选→成交/拒单）
  01_duplicates.csv            重复组逐条
  01_old_cand_generation.csv   42 条旧成交代际核查
  01_field_missing.csv         逐字段缺失
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-02"
PARQUET = ROOT / "数据" / "市场数据" / "parquet"

df = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet")
df["cid_ns"] = df["candidate_id"].str.extract(r"^(cand_ev|old_cand)", expand=False)
df["row_idx"] = np.arange(len(df))

# ── 旧成交原始行索引（按 symbol + t_in 精确回指 trades_rich.jsonl 行号） ──
rich = [json.loads(l) for l in open(ROOT / "evidence/FULL_SWEEP_200x100/trades_rich.jsonl", encoding="utf-8")]
rich_idx = {}
for i, t in enumerate(rich):
    rich_idx.setdefault((t["symbol"], int(t["t_in"])), []).append(i)

# 旧成交所属的 FULL_SWEEP 组（真正的 run 标识：组号 → 365 天窗口）
plan = json.load(open(ROOT / "evidence/FULL_SWEEP_200x100/plan.json", encoding="utf-8"))
plan_by_group = {int(p["group"]): (p.get("start"), p.get("end")) for p in plan}

# ── 15m / 1h K 线缓存（仅用于成交价与开盘价对账） ──
_c15, _c1h = {}, {}


def load_tf(sym: str, tf: str) -> pd.DataFrame:
    cache = _c15 if tf == "15m" else _c1h
    if sym in cache:
        return cache[sym]
    files = sorted(glob.glob(str(PARQUET / f"candles/binance/um/{sym}/{tf}/*.parquet")))
    if not files:
        cache[sym] = pd.DataFrame()
        return cache[sym]
    d = pd.concat([pd.read_parquet(f, columns=["open_time", "close_time", "open", "high", "low", "close"])
                   for f in files], ignore_index=True)
    d = d.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    cache[sym] = d
    return d


def bar_at(sym: str, tf: str, t_ms: int):
    d = load_tf(sym, tf)
    if d.empty:
        return None
    i = int(np.searchsorted(d["open_time"].to_numpy(np.int64), t_ms, side="left"))
    if i < len(d) and int(d.iloc[i]["open_time"]) == t_ms:
        return d.iloc[i]
    return None


# ── 逐条构建映射 ──
rows = []
for _, r in df.iterrows():
    sym = r["symbol"]
    ns = r["cid_ns"]
    filled = r["exec_status"] == "FILLED"

    rec = {
        "row_idx": int(r["row_idx"]),
        "candidate_id_ns": ns,
        "candidate_id": r["candidate_id"],
        "event_id": r["event_id"],
        "symbol": sym,
        "snapshot_time_ms": int(r["snapshot_time_ms"]),
        "signal_time_utc": r["datetime_utc"],
        "exec_status_raw": r["exec_status"],
        "reject_reason_raw": r["reject_reason"] if pd.notna(r["reject_reason"]) else "",
        "entry_time_ms": int(r["entry_time"]) if pd.notna(r["entry_time"]) else None,
        "exit_time_ms": int(r["exit_time"]) if pd.notna(r["exit_time"]) else None,
        "p_entry": float(r["p_entry"]) if pd.notna(r["p_entry"]) else None,
        "p_exit": float(r["p_exit"]) if pd.notna(r["p_exit"]) else None,
        "net_pnl": float(r["net_pnl"]) if pd.notna(r["net_pnl"]) else None,
        "gross_pnl": float(r["gross_pnl"]) if pd.notna(r["gross_pnl"]) else None,
        "fees": float(r["fees"]) if pd.notna(r["fees"]) else None,
        "funding": float(r["funding"]) if pd.notna(r["funding"]) else None,
        "exit_why": r["exit_why"] if pd.notna(r["exit_why"]) else "",
        "has_truncated_twin": bool(r["has_truncated_twin"]) if pd.notna(r["has_truncated_twin"]) else False,
    }
    rec["signal_id"] = r["candidate_id"]  # 引擎内 signal_id 即 candidate_id（engine_adapter 明文）
    rec["order_id"] = None               # 未从引擎原始输出取回，见 note
    rec["fill_id"] = None

    if filled:
        rec["unique_signal_key"] = f"{sym}|{int(r['snapshot_time_ms'])}"
        rec["fill_key"] = f"{sym}|{int(r['entry_time'])}"
    else:
        rec["unique_signal_key"] = f"{sym}|{int(r['snapshot_time_ms'])}"
        rec["fill_key"] = ""

    # 证据位置
    if ns == "old_cand":
        key = (sym, int(r["entry_time"]))
        idxs = rich_idx.get(key, [])
        rec["evidence_file"] = "evidence/FULL_SWEEP_200x100/trades_rich.jsonl"
        rec["evidence_line"] = ",".join(str(i + 1) for i in idxs) if idxs else ""
        rec["evidence_kind"] = "旧代 365 天窗口回测成交（原始逐笔）"
        g = rich[idxs[0]]["group"] if idxs else None
        rec["run_id"] = f"FULL_SWEEP_200x100|group_{g}" if g is not None else None
        rec["run_window"] = (f"{plan_by_group[g][0]}~{plan_by_group[g][1]}"
                             if g is not None and g in plan_by_group else None)
        rec["run_id_status"] = "原始 run 标识（组号+窗口）"
    else:
        rec["evidence_file"] = "evidence/FACTOR-ENTRY-01/candidate_features.parquet"
        rec["evidence_line"] = f"row {int(r['row_idx'])}"
        rec["evidence_kind"] = "连续回放候选（状态机 CandidateSnapshot → 引擎 ValidationResult）"
        rec["run_id"] = None
        rec["run_window"] = "2021-12-01~2026-10-18"
        rec["run_id_status"] = "未落盘：上游为进程内连续回放，未产生 run_id（本轮无法补造）"

    # 成交价对账：p_entry 是否 = 入场 15m 开盘 × (1+3bps)
    rec["entry_bar_open"] = None
    rec["entry_px_vs_open_resid"] = None
    rec["exit_in_bar_range"] = None
    if filled and rec["entry_time_ms"]:
        b = bar_at(sym, "15m", rec["entry_time_ms"])
        if b is not None and float(b["open"]) > 0:
            rec["entry_bar_open"] = float(b["open"])
            rec["entry_px_vs_open_resid"] = rec["p_entry"] / float(b["open"]) - 1.0
        b2 = bar_at(sym, "15m", rec["exit_time_ms"]) if rec["exit_time_ms"] else None
        if b2 is not None:
            rec["exit_in_bar_range"] = bool(float(b2["low"]) - 1e-12 <= rec["p_exit"] <= float(b2["high"]) + 1e-12)

    # 记账恒等式
    if filled and None not in (rec["net_pnl"], rec["gross_pnl"], rec["fees"], rec["funding"]):
        rec["pnl_identity_resid"] = rec["net_pnl"] - (rec["gross_pnl"] - rec["fees"] - rec["funding"])
    else:
        rec["pnl_identity_resid"] = None

    rows.append(rec)

mp = pd.DataFrame(rows)
mp.to_csv(OUT / "01_mapping_227.csv", index=False, encoding="utf-8-sig")

# ── 计数 ──
filled = mp[mp["exec_status_raw"] == "FILLED"]
unf = mp[mp["exec_status_raw"] != "FILLED"]
uniq_sig = mp.groupby("unique_signal_key").ngroups
uniq_fill = filled.groupby("fill_key").ngroups

# 唯一信号里，有成交的 / 从未成交的
sig_fill = set(filled["unique_signal_key"])
sig_all = set(mp["unique_signal_key"])
sig_nofill = sig_all - sig_fill

# 22 条未成交中，有几个信号在别的行上有成交
unf_sig = set(unf["unique_signal_key"])
unf_with_fill = unf_sig & sig_fill

counts = {
    "records_feature_rows": int(len(mp)),
    "unique_candidate_id": int(mp["candidate_id"].nunique()),
    "unique_event_id": int(mp["event_id"].nunique()),
    "unique_signal_symbol_snapshot": int(uniq_sig),
    "unique_fill_symbol_entry": int(uniq_fill),
    "filled_records": int(len(filled)),
    "unfilled_records": int(len(unf)),
    "records_by_namespace": {
        f"{ns}|{st}": int(n) for (ns, st), n in
        mp.groupby(["candidate_id_ns", "exec_status_raw"]).size().items()},
    "signals_with_fill": int(len(sig_fill)),
    "signals_without_fill": int(len(sig_nofill)),
    "unfilled_signals_that_have_fill_elsewhere": sorted(unf_with_fill),
    "closure_A_records": f"{len(filled)} 成交行 + {len(unf)} 未成交行 = {len(mp)} 特征行",
    "closure_B_signals": f"{len(sig_fill)} 有成交的唯一信号 + {len(sig_nofill)} 从未成交的唯一信号 = {uniq_sig} 唯一信号",
    "closure_C_rows_vs_signals": f"{len(mp)} 特征行 = {uniq_sig} 唯一信号 + {len(mp) - uniq_sig} 重复行",
    "symbols": int(mp["symbol"].nunique()),
    "date_min": mp["signal_time_utc"].min(),
    "date_max": mp["signal_time_utc"].max(),
    "entry_price_vs_open_3bps_ok": int((filled["entry_px_vs_open_resid"].sub(0.0003).abs() < 5e-4).sum()),
    "entry_price_vs_open_checked": int(filled["entry_px_vs_open_resid"].notna().sum()),
    "exit_in_bar_range_true": int((filled["exit_in_bar_range"] == True).sum()),
    "exit_in_bar_range_checked": int(filled["exit_in_bar_range"].notna().sum()),
    "pnl_identity_max_abs_resid": float(filled["pnl_identity_resid"].abs().max()),
    "pnl_identity_ok_rows": int((filled["pnl_identity_resid"].abs() < 1e-6).sum()),
    "unfilled_reject_reason_nonempty": int((unf["reject_reason_raw"].astype(str).str.len() > 0).sum()),
    "force_close_eod_rows": int((filled["exit_why"] == "force_close_eod").sum()),
}
with open(OUT / "01_counts.json", "w", encoding="utf-8") as f:
    json.dump(counts, f, ensure_ascii=False, indent=2)

# ── 重复组 ──
g = mp.groupby("unique_signal_key")
dups = []
for key, sub in g:
    if len(sub) > 1:
        dups.append({
            "unique_signal_key": key,
            "rows": len(sub),
            "detail": "; ".join(
                f"row{s.row_idx} {s.candidate_id_ns} status={s.exec_status_raw} "
                f"entry={s.entry_time_ms} net={s.net_pnl} cand={s.candidate_id}"
                for s in sub.itertuples()),
        })
pd.DataFrame(dups).to_csv(OUT / "01_duplicates.csv", index=False, encoding="utf-8-sig")

# ── 42 条旧成交代际核查 ──
oc = mp[mp["candidate_id_ns"] == "old_cand"].copy()
cand_ev = mp[mp["candidate_id_ns"] == "cand_ev"]
snap_by_sym = {s: np.sort(g2["snapshot_time_ms"].to_numpy()) for s, g2 in cand_ev.groupby("symbol")}
oc_rows = []
for _, r in oc.iterrows():
    sym, t_in = r["symbol"], int(r["entry_time_ms"])
    arr = snap_by_sym.get(sym, np.array([]))
    twin = bool(len(arr) and ((arr == t_in - 900000).any()))
    if len(arr):
        d = np.abs(arr - (t_in - 900000))
        nearest = int(d.min())
        near_cnt_4h = int(((arr >= t_in - 4 * 3600000) & (arr <= t_in)).sum())
    else:
        nearest = None
        near_cnt_4h = 0
    oc_rows.append({
        "row_idx": int(r["row_idx"]),
        "symbol": sym,
        "candidate_id": r["candidate_id"],
        "entry_time_ms": t_in,
        "entry_time_utc": pd.to_datetime(t_in, unit="ms").strftime("%Y-%m-%d %H:%M:%S"),
        "p_entry": r["p_entry"],
        "entry_bar_open": r["entry_bar_open"],
        "entry_px_vs_open_resid": r["entry_px_vs_open_resid"],
        "net_pnl": r["net_pnl"],
        "exit_why": r["exit_why"],
        "has_truncated_twin": r["has_truncated_twin"],
        "evidence_line": r["evidence_line"],
        "cand_ev_total_for_symbol": int(len(arr)),
        "has_exact_twin_snapshot_tin_minus_15m": twin,
        "nearest_cand_ev_snapshot_gap_ms": nearest,
        "cand_ev_snapshots_within_4h_before_entry": near_cnt_4h,
    })
ocdf = pd.DataFrame(oc_rows)
ocdf.to_csv(OUT / "01_old_cand_generation.csv", index=False, encoding="utf-8-sig")

counts["old_cand_records"] = int(len(ocdf))
counts["old_cand_with_exact_twin"] = int(ocdf["has_exact_twin_snapshot_tin_minus_15m"].sum())
counts["old_cand_with_any_cand_ev_within_4h"] = int((ocdf["cand_ev_snapshots_within_4h_before_entry"] > 0).sum())
counts["old_cand_entry_is_bar_open_plus_3bps"] = int(
    ((ocdf["entry_px_vs_open_resid"] - 0.0003).abs() < 5e-4).sum())
counts["old_cand_entry_px_checked"] = int(ocdf["entry_px_vs_open_resid"].notna().sum())
counts["old_cand_exit_why"] = ocdf["exit_why"].value_counts().to_dict()
counts["cand_ev_filled_exit_why"] = (
    filled[filled["candidate_id_ns"] == "cand_ev"]["exit_why"].value_counts().to_dict())

# ── 字段缺失 ──
fields = ["box_atr_4h", "box_er", "volume_ratio_1h", "oi_growth_6h", "breakout_atr_dist",
          "top_position_ls_ratio", "count_toptrader_ls_ratio", "sum_taker_ls_vol_ratio_5m",
          "retail_account_ls_ratio", "funding_rate"]
fm = []
for fld in fields:
    fm.append({
        "field": fld,
        "missing_in_227": int(df[fld].isna().sum()),
        "missing_in_205_filled": int(df.loc[df["exec_status"] == "FILLED", fld].isna().sum()),
        "missing_in_163_cand_ev_filled": int(
            df.loc[(df["exec_status"] == "FILLED") & (df["cid_ns"] == "cand_ev"), fld].isna().sum()),
        "missing_in_42_old_cand": int(df.loc[df["cid_ns"] == "old_cand", fld].isna().sum()),
    })
pd.DataFrame(fm).to_csv(OUT / "01_field_missing.csv", index=False, encoding="utf-8-sig")

with open(OUT / "01_counts.json", "w", encoding="utf-8") as f:
    json.dump(counts, f, ensure_ascii=False, indent=2)

print(json.dumps(counts, ensure_ascii=False, indent=2))
print("\n重复组：", dups)

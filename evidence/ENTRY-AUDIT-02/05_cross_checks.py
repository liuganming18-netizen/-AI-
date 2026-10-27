# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-02 / 05 · 交叉校验

1. 上游「候选→成交」是按 ±2 小时时间窗贪心匹配，不是按 candidate_id。
   检验：同一标的任意两个 cand_ev 候选快照间隔是否 ≤2h（有则存在歧义）。
2. 205 笔中有 3 笔入场价不等于「开盘×1.0003」，核对是否等于「向上取整到最小价位」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
sys.path.insert(0, str(ROOT / "策略"))
OUT = ROOT / "evidence" / "ENTRY-AUDIT-02"

df = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet")
df["ns"] = df["candidate_id"].str.extract(r"^(cand_ev|old_cand)", expand=False)
ce = df[df["ns"] == "cand_ev"].sort_values(["symbol", "snapshot_time_ms"])

amb = []
for s, g in ce.groupby("symbol"):
    t = g["snapshot_time_ms"].to_numpy(np.int64)
    d = np.diff(t)
    for i, x in enumerate(d):
        if x <= 7_200_000:
            amb.append({"symbol": s, "a": int(t[i]), "b": int(t[i + 1]), "gap_min": int(x / 60000)})

from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner

runner = EventTrendRunner()
mp = pd.read_csv(OUT / "01_mapping_227.csv")
bad = mp[(mp["exec_status_raw"] == "FILLED") & (mp["entry_px_vs_open_resid"].notna())
         & ((mp["entry_px_vs_open_resid"] - 0.0003).abs() >= 5e-4)]
ticks = {}
for sym in bad["symbol"].unique():
    df4, df1, df15, oi_t, oi_v, fu = runner.load_symbol_data(
        sym, start_date="2021-12-01", end_date="2026-10-18", allow_auto_window=False)
    spec = runner._resolve_spec(sym, df15, None)
    ticks[sym] = None if spec is None else str(spec.price_step)

tick_rows = []
for _, r in bad.iterrows():
    sym = r["symbol"]
    step = float(ticks[sym]) if ticks[sym] else None
    raw = float(r["entry_bar_open"]) * 1.0003
    ceil_px = (np.ceil(raw / step) * step) if step else None
    tick_rows.append({
        "symbol": sym,
        "entry_bar_open": float(r["entry_bar_open"]),
        "open_x_1.0003": raw,
        "actual_p_entry": float(r["p_entry"]),
        "price_step": step,
        "ceil_to_step": ceil_px,
        "matches_buy_ceil": bool(step and abs(ceil_px - float(r["p_entry"])) <= step * 1e-6),
    })

res = {
    "cand_ev_total": int(len(ce)),
    "cand_ev_symbols": int(ce["symbol"].nunique()),
    "ambiguous_pairs_within_2h": len(amb),
    "ambiguous_detail": amb,
    "entry_price_not_equal_open_plus_3bps": len(bad),
    "tick_rounding_detail": tick_rows,
    "all_explained_by_buy_ceil": bool(all(x["matches_buy_ceil"] for x in tick_rows)),
}
json.dump(res, open(OUT / "05_cross_checks.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False, indent=2))

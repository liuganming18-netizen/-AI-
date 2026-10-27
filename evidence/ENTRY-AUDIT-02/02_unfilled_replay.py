# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-02 / 02 · 22 条未成交的原始执行日志裁定

做法：对 22 条未成交涉及的 21 个标的，用与上游完全相同的入口
（EventTrendRunner.load_symbol_data → run_state_machine_simulation → run_event_trend_backtest）
重放该标的，取引擎**按候选身份**产出的拒单记录（ledger SIGNAL_REJECTED → ValidationResult），
再用 15m/1h 原始 K 线独立复算闸门不等式。

不是全池回测：只重放 21 个标的，用于取得逐笔原始拒单证据。

输出：
  02_replay_rejects.jsonl     逐标的原始拒单事件（引擎原样）
  02_unfilled_adjudication.csv 22 条逐笔裁定
  02_replay_consistency.csv   21 标的的候选/成交复现一致性
  02_summary.json
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
for p in (str(ROOT), str(ROOT / "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)

from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner

OUT = ROOT / "evidence" / "ENTRY-AUDIT-02"
PARQUET = ROOT / "数据" / "市场数据" / "parquet"
START, END = "2021-12-01", "2026-10-18"

df = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet")
df["cid_ns"] = df["candidate_id"].str.extract(r"^(cand_ev|old_cand)", expand=False)
unf = df[df["exec_status"] != "FILLED"].copy()
symbols = sorted(unf["symbol"].unique().tolist())

fe4 = json.load(open(ROOT / "evidence/FACTOR-ENTRY-04/unfilled_reasons.json", encoding="utf-8"))

_c15, _c1h = {}, {}


def load_tf(sym, tf):
    cache = _c15 if tf == "15m" else _c1h
    if sym in cache:
        return cache[sym]
    files = sorted(glob.glob(str(PARQUET / f"candles/binance/um/{sym}/{tf}/*.parquet")))
    d = pd.concat([pd.read_parquet(f, columns=["open_time", "open", "high", "low", "close"])
                   for f in files], ignore_index=True) if files else pd.DataFrame()
    if not d.empty:
        d = d.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    cache[sym] = d
    return d


def bar_at(sym, tf, t):
    d = load_tf(sym, tf)
    if d.empty:
        return None
    i = int(np.searchsorted(d["open_time"].to_numpy(np.int64), t, side="left"))
    if i < len(d) and int(d.iloc[i]["open_time"]) == t:
        return d.iloc[i]
    return None


raw_log = []
adj = []
consistency = []

PROG = OUT / "02_progress.jsonl"
done = set()
if PROG.exists():  # 断点续跑：已完成的标的直接跳过
    for line in PROG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            done.add(d["symbol"])
            raw_log.extend(d["raw_log"])
            adj.extend(d["adj"])
            consistency.extend(d["consistency"])
print(f"已完成标的 {len(done)}/{len(symbols)}", flush=True)

for sym in symbols:
    if sym in done:
        continue
    print(f"[replay] {sym} ...", flush=True)
    _r0, _a0, _c0 = len(raw_log), len(adj), len(consistency)
    runner = EventTrendRunner()
    df_4h, df_1h, df_15m, oi_ts, oi_vals, funding = runner.load_symbol_data(
        sym, start_date=START, end_date=END, allow_auto_window=False)
    sm = runner.run_state_machine_simulation(sym, df_4h, df_1h, oi_ts, oi_vals)
    cfg = EventTrendConfig(initial_equity=2000.0)
    spec = runner._resolve_spec(sym, df_15m, cfg)
    res = run_event_trend_backtest(
        symbol=sym, df_1h=df_1h, df_15m=df_15m, candidates=sm.candidates,
        funding_events=funding, df_4h=df_4h, variant="A",
        initial_equity=float(cfg.initial_equity), config=cfg, instrument_spec=spec,
        price_step=(None if spec is not None else "0.000000000001"))

    # 原始拒单事件（引擎原样落盘）
    for e in res["ledger_events"]:
        if e.get("event_type") == "SIGNAL_REJECTED":
            raw_log.append({"symbol": sym, **e})
    for r in res["engine_result"].get("rejected_orders", []):
        raw_log.append({"symbol": sym, "event_type": "rejected_orders_item", **r})

    val = {v.candidate_id: v for v in res["validation_results"]}
    cand_by_id = {c.candidate_id: c for c in sm.candidates}

    # 复现一致性：该标的在特征表里的 cand_ev 行 vs 本次重放
    tbl = df[(df["symbol"] == sym) & (df["cid_ns"] == "cand_ev")]
    for _, r in tbl.iterrows():
        cid = r["candidate_id"]
        v = val.get(cid)
        consistency.append({
            "symbol": sym,
            "candidate_id": cid,
            "in_table": True,
            "in_replay": v is not None,
            "table_status": r["exec_status"],
            "replay_executed": (None if v is None else bool(v.executed)),
            "status_match": (None if v is None else (bool(v.executed) == (r["exec_status"] == "FILLED"))),
            "table_net_pnl": (float(r["net_pnl"]) if pd.notna(r["net_pnl"]) else None),
            "replay_net_pnl": (None if v is None or not v.executed else float(v.net_pnl)),
            "net_pnl_match": (None if v is None or not v.executed or pd.isna(r["net_pnl"])
                              else bool(abs(float(v.net_pnl) - float(r["net_pnl"])) < 1e-6)),
            "table_entry_price": (float(r["p_entry"]) if pd.notna(r["p_entry"]) else None),
            "replay_entry_price": (None if v is None or not v.executed else float(v.entry_price)),
        })
    # 反向：重放有、表里没有
    table_ids = set(tbl["candidate_id"])
    for cid, v in val.items():
        if cid not in table_ids:
            consistency.append({"symbol": sym, "candidate_id": cid, "in_table": False,
                                "in_replay": True, "table_status": "", "replay_executed": bool(v.executed),
                                "status_match": None, "table_net_pnl": None,
                                "replay_net_pnl": (float(v.net_pnl) if v.executed else None),
                                "net_pnl_match": None, "table_entry_price": None,
                                "replay_entry_price": (float(v.entry_price) if v.executed else None)})

    # 逐条裁定
    for _, r in unf[unf["symbol"] == sym].iterrows():
        cid = r["candidate_id"]
        snap = int(r["snapshot_time_ms"])
        v = val.get(cid)
        c = cand_by_id.get(cid)
        eng_reason = (v.execution_canceled_reason if v is not None else None)
        executed = (bool(v.executed) if v is not None else None)

        # ── 独立复算（不依赖引擎）──
        t_exec = snap + 15 * 60 * 1000
        b15 = bar_at(sym, "15m", t_exec)
        open_px = float(b15["open"]) if b15 is not None else None
        b1h = bar_at(sym, "1h", snap)
        breakout_close = float(b1h["close"]) if b1h is not None else None
        A = float(r["box_atr_4h"]) if pd.notna(r["box_atr_4h"]) else None
        H = float(r["box_high"]) if pd.notna(r["box_high"]) else None
        max_entry = (breakout_close + 0.5 * A) if (breakout_close is not None and A is not None) else None

        # 引擎快照值（若重放拿到）
        if c is not None:
            max_entry_engine = float(c.max_entry_price)
            min_entry_engine = float(c.min_entry_price)
            A_engine = float(c.box_atr_a)
            bc_engine = float(c.breakout_close)
            H_engine = float(c.box_high_h)
        else:
            max_entry_engine = min_entry_engine = A_engine = bc_engine = H_engine = None

        if open_px is None:
            derived = "NO_BAR"
        elif max_entry_engine is not None and open_px > max_entry_engine:
            derived = "canceled_gap_above_0.5a"
        elif min_entry_engine is not None and open_px < min_entry_engine:
            derived = "canceled_fell_back_into_box"
        else:
            derived = "PASS_ENTRY_GATE"

        eng_kind = None
        if eng_reason:
            eng_kind = eng_reason.split(" ")[0].split("(")[0].strip()

        fe4_reason = (fe4.get(cid) or {}).get("reason")
        fe4_kind = fe4_reason.split(" ")[0] if fe4_reason else None

        adj.append({
            "symbol": sym,
            "candidate_id": cid,
            "event_id": r["event_id"],
            "snapshot_time_ms": snap,
            "signal_time_utc": r["datetime_utc"],
            "table_status_raw": r["exec_status"],
            "table_reject_reason_raw": r["reject_reason"] if pd.notna(r["reject_reason"]) else "",
            "replay_found_candidate": v is not None,
            "replay_executed": executed,
            "engine_reason": eng_reason,
            "engine_reason_kind": eng_kind,
            "fe4_reason": fe4_reason,
            "fe4_reason_kind": fe4_kind,
            "fe4_equals_engine": (None if (fe4_reason is None or eng_reason is None) else fe4_reason == eng_reason),
            "exec_bar_open_15m": open_px,
            "breakout_close_1h": breakout_close,
            "box_atr_4h_table": A,
            "box_high_table": H,
            "max_entry_recomputed": max_entry,
            "max_entry_engine": max_entry_engine,
            "min_entry_engine": min_entry_engine,
            "derived_kind": derived,
            "derived_equals_engine": (None if eng_kind is None else derived == eng_kind),
            "evidence": "引擎 ledger SIGNAL_REJECTED（按 signal_id=candidate_id 精确匹配）+ 15m/1h 原始 K 线复算",
        })

    # 断点续跑落盘（单标的完成即写，防中途被杀）
    with open(PROG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"symbol": sym, "raw_log": raw_log[_r0:], "adj": adj[_a0:],
                            "consistency": consistency[_c0:]}, ensure_ascii=False) + "\n")
    print(f"[replay] {sym} done: 拒单事件 {len(raw_log)-_r0}, 裁定 {len(adj)-_a0}, 一致性 {len(consistency)-_c0}", flush=True)

adjdf = pd.DataFrame(adj)
adjdf.to_csv(OUT / "02_unfilled_adjudication.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(consistency).to_csv(OUT / "02_replay_consistency.csv", index=False, encoding="utf-8-sig")
with open(OUT / "02_replay_rejects.jsonl", "w", encoding="utf-8") as f:
    for r in raw_log:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

summary = {
    "symbols_replayed": len(symbols),
    "unfilled_rows": int(len(adjdf)),
    "replay_found_candidate": int(adjdf["replay_found_candidate"].sum()),
    "engine_reason_nonnull": int(adjdf["engine_reason"].notna().sum()),
    "fe4_equals_engine_true": int((adjdf["fe4_equals_engine"] == True).sum()),
    "fe4_equals_engine_false": int((adjdf["fe4_equals_engine"] == False).sum()),
    "fe4_equals_engine_null": int(adjdf["fe4_equals_engine"].isna().sum()),
    "derived_equals_engine_true": int((adjdf["derived_equals_engine"] == True).sum()),
    "derived_equals_engine_false": int((adjdf["derived_equals_engine"] == False).sum()),
    "engine_reason_kind_counts": adjdf["engine_reason_kind"].value_counts(dropna=False).to_dict(),
    "fe4_reason_kind_counts": adjdf["fe4_reason_kind"].value_counts(dropna=False).to_dict(),
    "derived_kind_counts": adjdf["derived_kind"].value_counts(dropna=False).to_dict(),
    "table_reject_reason_nonempty": int((adjdf["table_reject_reason_raw"].astype(str).str.len() > 0).sum()),
    "raw_signal_rejected_events": int(sum(1 for r in raw_log if r.get("event_type") == "SIGNAL_REJECTED")),
    "raw_rejected_orders_items": int(sum(1 for r in raw_log if r.get("event_type") == "rejected_orders_item")),
}
cons = pd.DataFrame(consistency)
summary["consistency_rows"] = int(len(cons))
summary["consistency_status_match_true"] = int((cons["status_match"] == True).sum())
summary["consistency_status_match_false"] = int((cons["status_match"] == False).sum())
summary["consistency_net_pnl_match_true"] = int((cons["net_pnl_match"] == True).sum())
summary["consistency_net_pnl_match_false"] = int((cons["net_pnl_match"] == False).sum())
summary["consistency_in_replay_missing"] = int((cons["in_replay"] == False).sum())
summary["consistency_in_table_missing"] = int((cons["in_table"] == False).sum())
with open(OUT / "02_summary.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print(json.dumps(summary, ensure_ascii=False, indent=2))
print(adjdf[["symbol", "signal_time_utc", "engine_reason_kind", "fe4_reason_kind",
             "derived_kind", "fe4_equals_engine", "derived_equals_engine"]].to_string())

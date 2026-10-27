# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-B｜03 观察状态与候选记录（真实历史行情，**测试配置**）。

要点：
· 行情是真实历史（冷档 2026-08 / 热库 2026-10 两个窗口都跑）；
· 阈值是 `fixture/fixture_rules.json`（**未批准**，仅用于验证接线行为）；
· 每个候选都留下「条件真值表」：哪条成立、哪条不成立、哪条无法判定、缺什么数据；
· 不生成任何绩效结论。
"""
from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                    # noqa: E402

import os
os.environ["ACCUM04_CONFIG"] = str(C.FIXTURE)
os.environ["ACCUM04_FIXTURE_OK"] = "1"
os.environ["PAPER_LEDGER_DIR"] = str(C.RUNS / "03_observe" / "paper")

import accum_early_entry as AE                         # noqa: E402

RUN = C.RUNS / "03_observe"

WINDOWS = [
    {"key": "A_cold", "start": "2026-08-01T00:00:00Z", "end": "2026-09-05T00:00:00Z",
     "why": "热库 15m 从 2026-09-12 起 ⇒ 这段成交 bar 只能来自冷档",
     "max_boxes": 3,
     "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT",
                 "ADAUSDT", "AVAXUSDT", "LINKUSDT", "BNBUSDT", "LTCUSDT"]},
    {"key": "B_hot", "start": "2026-09-12T00:00:00Z", "end": "2026-10-25T00:00:00Z",
     "why": "热库覆盖段（15m 从 2026-09-12 起），用来确认热层同样能推进",
     "max_boxes": 4,
     "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT",
                 "ADAUSDT", "AVAXUSDT", "LINKUSDT", "BNBUSDT", "LTCUSDT"]},
]


def _cond_stats(cands) -> dict:
    res = Counter()
    for c in cands:
        for item in c.get("conditions") or []:
            res[item["result"]] += 1
    return dict(res)


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN)
    RUN.mkdir(parents=True, exist_ok=True)

    cfg = AE.load_config()
    store = AE.open_store(RUN / "observe.sqlite3")
    feed = AE.DataFeed()
    out = {"generated_utc": C.iso(int(__import__("time").time() * 1000)),
           "config": {k: v for k, v in cfg.items() if k != "doc"},
           "fixture_notice": ("阈值来自 fixture 测试配置，未经用户批准；"
                              "本节只证明「接线与记录能工作」，不是甲乙丙的绩效或可行性结论"),
           "windows": []}
    all_cands = []
    try:
        for w in WINDOWS:
            wr = {"key": w["key"], "why": w["why"], "start": w["start"], "end": w["end"],
                  "per_symbol": [], "events_by_state": {}, "candidates": []}
            start, end = C.ms_of(w["start"]), C.ms_of(w["end"])
            states = Counter()
            for sym in w["symbols"]:
                r = AE.scan_symbol(sym, cfg, feed, store, start_ms=start, end_ms=end,
                                   max_boxes=int(w.get("max_boxes") or 3),
                                   open_candidates=False)
                wr["per_symbol"].append({
                    "symbol": sym,
                    "bars": r["bars"]["4h"], "bars_1h": r["bars"]["1h"],
                    "boxes_formed": r["boxes_formed"], "boxes_used": r["boxes_used"],
                    "events": len(r["events"]), "candidates": len(r["candidates"]),
                    "gaps_4h": r["bars"]["4h_gaps"].get("gap_segment_count"),
                    "gaps_1h": r["bars"]["1h_gaps"].get("gap_segment_count"),
                    "available_at_4h": r["bars"]["available_at_4h"],
                    "available_at_1h": r["bars"]["available_at_1h"],
                    "cooldown_ms": r["cooldown_ms"],
                })
                for e in r["events"]:
                    states[e["state"]] += 1
                for c in r["candidates"]:
                    all_cands.append(c)
                    wr["candidates"].append({
                        "scheme": c["scheme"], "symbol": c["symbol"],
                        "candidate_id": c["candidate_id"],
                        "confirm_utc": C.iso(c["confirm_close_ms"]),
                        "earliest_entry_utc": C.iso(c["earliest_entry_ms"]),
                        "entry_bar_utc": C.iso(c["entry_bar_open_ms"]),
                        "entry_bar_source": c["entry_bar_source"],
                        "resistance": c["resistance"],
                        "entry_ref_price": c["entry_ref_price"],
                        "status": c["status"], "status_reason": c["status_reason"],
                        "conditions": c["conditions"],
                        "gap": c["gap"],
                    })
            wr["events_by_state"] = dict(states)
            wr["condition_results"] = _cond_stats(wr["candidates"])
            wr["candidates_count"] = len(wr["candidates"])
            out["windows"].append(wr)
    finally:
        feed.close()

    # 事件链：取一条完整链（同一事件的全部迁移）作样例
    con = store.con
    chain = None
    row = con.execute(
        "SELECT event_id FROM observe_event WHERE state='CANDIDATE' ORDER BY created_ms LIMIT 1"
    ).fetchone()
    if row:
        d = store.dump_event(row["event_id"])
        chain = {"event_id": row["event_id"],
                 "event": {k: d["event"][k] for k in
                           ("scheme", "symbol", "state", "box_version_id",
                            "old_low", "sweep_low", "expires_at_ms")},
                 "transitions": [{"from": t["from_state"], "to": t["to_state"],
                                  "at_utc": t["at_utc"], "reason": t["reason"],
                                  "conditions": (json.loads(t["payload_json"] or "{}")
                                                 .get("conditions") or [])}
                                 for t in d["transitions"]],
                 "candidates": [{k: c[k] for k in
                                 ("candidate_id", "state", "local_support_price",
                                  "entry_price_ref", "created_ms")}
                                for c in d["candidates"]],
                 "data_gaps": d["data_gaps"]}

    # 汇总
    ev_total = con.execute("SELECT COUNT(*) FROM observe_event").fetchone()[0]
    ev_by_state = dict(con.execute(
        "SELECT state, COUNT(*) FROM observe_event GROUP BY state").fetchall())
    cand_rows = con.execute("SELECT COUNT(*) FROM accum04_candidate").fetchone()[0]
    gap_rows = con.execute("SELECT COUNT(*) FROM observe_data_gap").fetchone()[0]
    trans_rows = con.execute("SELECT COUNT(*) FROM observe_transition").fetchone()[0]
    store.close()

    out["summary"] = {
        "events": ev_total, "events_by_state": ev_by_state,
        "transitions": trans_rows,
        "candidates_in_memory": len(all_cands),
        "candidate_rows_in_db": cand_rows,
        "data_gap_rows": gap_rows,
        "condition_results_all": _cond_stats(all_cands),
        "candidates_with_condition_table": sum(
            1 for c in all_cands if c.get("conditions")),
        "candidates_missing_condition_table": sum(
            1 for c in all_cands if not c.get("conditions")),
        "entry_bar_source": dict(Counter(c.get("entry_bar_source") for c in all_cands)),
        "schemes": dict(Counter(c["scheme"] for c in all_cands)),
        "status": dict(Counter(c["status"] for c in all_cands)),
    }
    out["sample_chain"] = chain
    out["checks"] = {
        "每个候选都有条件真值表": out["summary"]["candidates_missing_condition_table"] == 0,
        "每个候选都有策略身份": all(
            c["event_id"].startswith("ev_") for c in all_cands) if all_cands else None,
        "三组都有候选（测试配置下）": set(out["summary"]["schemes"]) == {"4a", "4b", "4c"},
        "冷窗口有成交 bar 来源": out["summary"]["entry_bar_source"].get("cold", 0) > 0,
        "热窗口有成交 bar 来源": out["summary"]["entry_bar_source"].get("hot", 0) > 0,
        "数据缺口有记录": gap_rows > 0,
    }
    C.dump("03_观察与候选.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

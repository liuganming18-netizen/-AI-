# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-B｜隔离链路工作进程（**只在独立账本里跑**）。

输入：stdin 一行 JSON（job）。输出：stdout 一行 JSON（结果）。
纪律：
  · 环境变量由父进程给全（账本目录/实时库/闸门/行情/合并开关都在隔离目录内）；
  · `origin="replay"` + `epoch_ms=0` ⇒ 允许在隔离账本里回放历史事件；
  · 只读行情，只写本进程自己的账本目录与观察库；
  · 闸门文件必须落在隔离运行目录内（父进程已断言，这里再断言一次）。

job 字段：
  phase: "open" | "resume"
  symbols / start / end / max_boxes / walk_days / legacy_4h_exit
  duplicate_probe: 是否在跑完后用同一 signal_uid 再下一次（必须被挡）
  rescan: 是否再扫一遍（必须不产生新单）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
EVID = HERE.parents[1]
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
for p in (str(PROJECT), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

M15 = 900_000
H4 = 14_400_000
DAY = 86_400_000

#: 连接探针（ACCUM05_SPY=1）：记录本进程开过哪些库、是否只读。
#: 用途：证明隔离运行**没有**以可写方式连生产库，而不是靠"我们没写"的口头声明。
_CONN_LOG: list = []


def _install_spy() -> None:
    """记录每一次 `sqlite3.connect`。

    只记「打开方式」，不猜意图：`mode=ro` / `immutable=1` 视为只读打开；
    其余一律按「可写打开」记录 —— 宁可判严，也不要漏掉一次可写连生产库。
    """
    import sqlite3 as _sq
    orig = _sq.connect

    def _spy(database, *a, **kw):
        db = str(database)
        ro = ("mode=ro" in db) or ("immutable=1" in db)
        _CONN_LOG.append({"db": db, "readonly": bool(ro),
                          "uri_kw": kw.get("uri"), "n_args": len(a)})
        return orig(database, *a, **kw)

    _sq.connect = _spy


def iso(ms):
    if ms is None:
        return None
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def ms_of(s):
    import calendar
    import time
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def main() -> int:
    job = json.loads(sys.stdin.read())
    if os.environ.get("ACCUM05_SPY") == "1":
        _install_spy()
    import live_store as L
    L.ensure_live_schema()

    # 闸门只允许写在隔离目录内
    gpath = Path(os.environ["PAPER_GATE_FILE"]).resolve()
    run_root = L.get_ledger_dir().parent.resolve()
    if run_root not in gpath.parents:
        raise SystemExit(f"拒绝：闸门文件 {gpath} 不在隔离目录 {run_root} 内")
    gpath.write_text(json.dumps({"allow_new_entries": True,
                                 "set_by": "ACCUM-EARLY-05-B_isolated",
                                 "reason": "隔离自测：只在本单独立账本内放行新开仓"},
                                ensure_ascii=False), encoding="utf-8")

    import accum_early_entry as AE
    import paper_engine as PE

    cfg = AE.load_config()
    run_dir = Path(os.environ["PAPER_LEDGER_DIR"]).parent
    store = AE.open_store(run_dir / "observe.sqlite3")
    feed = AE.DataFeed()
    start, end = ms_of(job["start"]), ms_of(job["end"])
    out = {"phase": job.get("phase"),
           "env": {"PAPER_LEDGER_DIR": os.environ.get("PAPER_LEDGER_DIR"),
                   "LIVE_DB": os.environ.get("LIVE_DB"),
                   "PAPER_GATE_FILE": os.environ.get("PAPER_GATE_FILE"),
                   "MARKET_DB": os.environ.get("MARKET_DB"),
                   "WX_MARKET_MERGE": os.environ.get("WX_MARKET_MERGE"),
                   "ACCUM05_CONFIG": os.environ.get("ACCUM05_CONFIG")},
           "market_mode": PE.market_source_mode(),
           "errors": [], "steps": []}

    try:
        run = AE.research_run(
            config=os.environ.get("ACCUM05_CONFIG"),
            symbols=job["symbols"], start_ms=start, end_ms=end,
            max_boxes=int(job.get("max_boxes") or 3),
            open_candidates=True, drive_exits=True,
            legacy_4h_exit=bool(job.get("legacy_4h_exit")),
            walk_days=int(job.get("walk_days") or 30),
            store=store, feed=feed)
        out["run"] = {
            "identity": run["identity"], "flags": run["flags"],
            "config": run["config"],
            "scan": [{"symbol": r["symbol"], "boxes_formed": r["boxes_formed"],
                      "boxes_used": r["boxes_used"], "actions": r["actions"],
                      "cooldown_ms": r["cooldown_ms"], "skipped": r["skipped"],
                      "bars": r["bars"],
                      "candidates": [{k: c.get(k) for k in
                                      ("scheme", "symbol", "candidate_id", "signal_uid",
                                       "confirm_close_ms", "earliest_entry_ms",
                                       "entry_bar_open_ms", "entry_ref_price",
                                       "entry_bar_source", "entry_bar_available_at",
                                       "resistance", "deviation", "status",
                                       "status_reason")}
                                     for c in r["candidates"]],
                      "open_results": [{"scheme": c["scheme"],
                                        "result": c.get("open_result")}
                                       for c in r["candidates"]],
                      "events": r["events"]}
                     for r in run["per_symbol"]],
            "exits": [{k: v for k, v in e.items() if k != "detail"} for e in run["exits"]],
            "exit_details": [{"position_id": e.get("position_id"),
                              "scheme": e.get("scheme"),
                              "updates": e.get("updates") or [],
                              "detail": (e.get("detail") or {})}
                             for e in run["exits"]],
            "ledgers": run["ledgers"], "errors": run["errors"],
        }

        # ── 重复下单：同一 signal_uid 再下一次（必须被挡住）────────────────
        dup = []
        if job.get("duplicate_probe"):
            for scheme in AE.SCHEMES:
                snap = AE.ledger_snapshot(scheme)
                if not snap["positions"]:
                    continue
                p0 = snap["positions"][0]
                sig = None
                import sqlite3
                lp = Path(os.environ["PAPER_LEDGER_DIR"]) / f"paper_{scheme}.sqlite3"
                con = sqlite3.connect(f"file:{lp}?mode=ro", uri=True)
                try:
                    r = con.execute("SELECT signal_uid FROM paper_position WHERE position_id=?",
                                    (int(p0["position_id"]),)).fetchone()
                    sig = r[0] if r else None
                finally:
                    con.close()
                if not sig:
                    continue
                res = PE.open_position(
                    scheme, p0["symbol"], signal_uid=sig, direction="long",
                    event_time=int(p0["entry_ts"]), available_at=int(p0["entry_ts"]),
                    requested_at=int(p0["entry_ts"]), now_ms=int(p0["entry_ts"]),
                    origin="replay", epoch_ms=0)
                dup.append({"scheme": scheme, "signal_uid": sig,
                            "ok": res.get("ok"), "reason": res.get("reason")})
        out["duplicate_attempt"] = dup

        # ── 重复扫描：再扫一遍（必须不产生新单/新候选）──────────────────────
        if job.get("rescan"):
            before = {s: AE.ledger_snapshot(s) for s in AE.SCHEMES}
            cand_before = store.con.execute(
                "SELECT COUNT(*) FROM accum05_candidate").fetchone()[0]
            tr_before = store.con.execute(
                "SELECT COUNT(*) FROM observe_transition").fetchone()[0]
            gap_before = store.con.execute(
                "SELECT COUNT(*) FROM observe_data_gap").fetchone()[0]
            rescan = AE.research_run(
                config=os.environ.get("ACCUM05_CONFIG"),
                symbols=job["symbols"], start_ms=start, end_ms=end,
                max_boxes=int(job.get("max_boxes") or 3),
                open_candidates=True, drive_exits=True,
                legacy_4h_exit=bool(job.get("legacy_4h_exit")),
                walk_days=int(job.get("walk_days") or 30),
                store=store, feed=feed)
            after = {s: AE.ledger_snapshot(s) for s in AE.SCHEMES}
            cand_after = store.con.execute(
                "SELECT COUNT(*) FROM accum05_candidate").fetchone()[0]
            tr_after = store.con.execute(
                "SELECT COUNT(*) FROM observe_transition").fetchone()[0]
            gap_after = store.con.execute(
                "SELECT COUNT(*) FROM observe_data_gap").fetchone()[0]
            out["rescan"] = {
                "orders_before": sum(len(v["orders"]) for v in before.values()),
                "orders_after": sum(len(v["orders"]) for v in after.values()),
                "fills_before": sum(len(v["fills"]) for v in before.values()),
                "fills_after": sum(len(v["fills"]) for v in after.values()),
                "candidates_before": cand_before, "candidates_after": cand_after,
                "transitions_before": tr_before, "transitions_after": tr_after,
                # 缺口表必须幂等：不加这条，「重复扫描无副作用」只对订单成立
                "gaps_before": gap_before, "gaps_after": gap_after,
                "dedup_events": sum(1 for r in rescan["per_symbol"]
                                    for e in r["events"] if e.get("dedup")),
                "errors": rescan["errors"],
            }
    finally:
        feed.close()
        store.close()
        if os.environ.get("ACCUM05_SPY") == "1":
            out["conn"] = {"total": len(_CONN_LOG), "log": _CONN_LOG}
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

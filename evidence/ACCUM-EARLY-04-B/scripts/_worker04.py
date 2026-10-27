# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-B｜隔离全链工作进程（**只在独立账本里跑**）。

输入：stdin 一行 JSON（job）。输出：stdout 一行 JSON（结果）。
纪律：
  · 环境变量由父进程给全（账本目录/实时库/闸门/行情/合并开关都在隔离目录内）；
  · `origin="replay"` + `epoch_ms=0` ⇒ 允许在隔离账本里回放历史事件；
  · 只读行情，只写本进程自己的账本目录。
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

#: 连接探针（ACCUM04_SPY=1）：记录本进程开过哪些库、是否只读。
#: 用途：证明隔离运行**没有**以可写方式连生产库，而不是靠"我们没写"的口头声明。
_CONN_LOG: list = []


def _install_spy() -> None:
    import sqlite3 as _sq
    orig = _sq.connect

    def _spy(database, *a, **kw):
        db = str(database)
        ro = ("mode=ro" in db) or ("mode=ro" in str(kw.get("uri", "")))
        _CONN_LOG.append({"db": db, "readonly_hint": bool(ro)})
        return orig(database, *a, **kw)

    _sq.connect = _spy


def iso(ms):
    if ms is None:
        return None
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def ms_of(s):
    import calendar, time
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def main() -> int:
    job = json.loads(sys.stdin.read())
    if os.environ.get("ACCUM04_SPY") == "1":
        _install_spy()
    import live_store as L
    L.ensure_live_schema()

    # 闸门只允许写在隔离目录内
    gpath = Path(os.environ["PAPER_GATE_FILE"]).resolve()
    run_root = L.get_ledger_dir().parent.resolve()
    if run_root not in gpath.parents:
        raise SystemExit(f"拒绝：闸门文件 {gpath} 不在隔离目录 {run_root} 内")
    gpath.write_text(json.dumps({"allow_new_entries": True,
                                 "set_by": "ACCUM-EARLY-04-B_isolated",
                                 "reason": "隔离自测：只在本单独立账本内放行新开仓"},
                                ensure_ascii=False), encoding="utf-8")

    import accum_early_entry as AE
    import paper_engine as PE

    cfg = AE.load_config()
    store = AE.open_store(Path(os.environ["PAPER_LEDGER_DIR"]).parent / "observe.sqlite3")
    feed = AE.DataFeed()
    out = {"phase": job.get("phase"), "env": {
        "PAPER_LEDGER_DIR": os.environ.get("PAPER_LEDGER_DIR"),
        "LIVE_DB": os.environ.get("LIVE_DB"),
        "PAPER_GATE_FILE": os.environ.get("PAPER_GATE_FILE"),
        "MARKET_DB": os.environ.get("MARKET_DB"),
        "WX_MARKET_MERGE": os.environ.get("WX_MARKET_MERGE"),
        "ACCUM04_CONFIG": os.environ.get("ACCUM04_CONFIG"),
    }, "market_mode": PE.market_source_mode(), "errors": [], "steps": []}

    start, end = ms_of(job["start"]), ms_of(job["end"])
    scan = None
    try:
        if job.get("phase") == "open":
            scan = {"per_symbol": []}
            for sym in job["symbols"]:
                r = AE.scan_symbol(sym, cfg, feed, store, start_ms=start, end_ms=end,
                                   max_boxes=int(job.get("max_boxes") or 3),
                                   open_candidates=True)
                scan["per_symbol"].append(r)
            out["scan"] = [{"symbol": r["symbol"],
                            "candidates": [{k: c.get(k) for k in
                                            ("scheme", "symbol", "candidate_id",
                                             "signal_uid", "confirm_close_ms",
                                             "earliest_entry_ms", "entry_bar_open_ms",
                                             "entry_ref_price", "entry_bar_source",
                                             "entry_bar_available_at", "resistance",
                                             "status", "status_reason")}
                                           for c in r["candidates"]],
                            "open_results": [{"scheme": c["scheme"],
                                              "result": c.get("open_result")}
                                             for c in r["candidates"]]}
                           for r in scan["per_symbol"]]
        # ── 走退出：对每个未平仓逐根 15m 推进 ──────────────────────────────
        walk_days = int(job.get("walk_days") or 40)
        for scheme in AE.SCHEMES:
            snap0 = AE.ledger_snapshot(scheme)
            for pos in snap0["positions"]:
                if pos["status"] != "open":
                    continue
                sym = pos["symbol"]
                entry_ts = int(pos["entry_ts"])
                stop_ms = min(entry_ts + walk_days * DAY, end + walk_days * DAY)
                b15 = feed.load("15m", sym, entry_ts - M15, stop_ms)
                b4 = feed.load("4h", sym, entry_ts - H4, stop_ms)
                i4, checked, exited = 0, 0, False
                for b in b15:
                    if int(b["close_time"]) < entry_ts:
                        continue
                    clk = int(b["close_time"])
                    while i4 < len(b4) and int(b4[i4]["close_time"]) <= clk:
                        adv = PE.advance_position_trailing(scheme, int(pos["position_id"]),
                                                           b4[i4], now_ms=clk, decision_at=clk)
                        if adv.get("stop_updated") or adv.get("exit_triggered"):
                            out["steps"].append({"step": "trailing", "scheme": scheme,
                                                 "position_id": pos["position_id"],
                                                 "bar4h": iso(b4[i4]["open_time"]),
                                                 "bar4h_source": b4[i4].get("source"),
                                                 "new_stop": adv.get("effective_stop"),
                                                 "available_at_proven": adv.get("available_at_proven"),
                                                 "exit_triggered": adv.get("exit_triggered")})
                        i4 += 1
                        if adv.get("exit_triggered"):
                            exited = True
                            break
                    if exited:
                        break
                    chk = PE.check_position_exit_on_bar(scheme, int(pos["position_id"]), b,
                                                        now_ms=clk, decision_at=clk)
                    checked += 1
                    if chk.get("exit_triggered"):
                        out["steps"].append({"step": "exit_15m", "scheme": scheme,
                                             "position_id": pos["position_id"],
                                             "bar": iso(b["open_time"]),
                                             "bar_source": b.get("source"),
                                             "bar_low": chk.get("bar_low"),
                                             "stop_used": chk.get("cur_stop"),
                                             "exit": chk.get("exit_result")})
                        exited = True
                        break
                    if not chk.get("ok"):
                        out["errors"].append({"step": "exit_check", "scheme": scheme,
                                              "reason": chk.get("reason"),
                                              "bar": iso(b["open_time"])})
                        break
                out["steps"].append({"step": "walk_done", "scheme": scheme,
                                     "position_id": pos["position_id"],
                                     "bars_checked": checked, "exited": exited})

        # ── 重复扫描：再扫一遍（必须无副作用）──────────────────────────────
        rescan = []
        for scheme in AE.SCHEMES:
            before = AE.ledger_snapshot(scheme)
            for pos in before["positions"]:
                if pos["status"] != "open":
                    rescan.append({"scheme": scheme, "position_id": pos["position_id"],
                                   "skipped": "position_not_open"})
                    continue
                b = feed.at_or_after("15m", pos["symbol"], int(pos["entry_ts"]))
                chk = PE.check_position_exit_on_bar(scheme, int(pos["position_id"]), b,
                                                   now_ms=int(b["close_time"]) if b else None,
                                                   decision_at=int(b["close_time"]) if b else None)
                rescan.append({"scheme": scheme, "position_id": pos["position_id"],
                               "ok": chk.get("ok"), "reason": chk.get("reason"),
                               "exit_triggered": chk.get("exit_triggered")})
            after = AE.ledger_snapshot(scheme)
            rescan.append({"scheme": scheme, "orders_before": len(before["orders"]),
                           "orders_after": len(after["orders"]),
                           "fills_before": len(before["fills"]),
                           "fills_after": len(after["fills"])})
        out["rescan"] = rescan

        # ── 重复下单：同一 signal_uid 再下一次（必须被挡住）────────────────
        dup = []
        for scheme in AE.SCHEMES:
            snap = AE.ledger_snapshot(scheme)
            if not snap["positions"]:
                continue
            p0 = snap["positions"][0]
            sig = None
            import sqlite3
            con = L.paper_conn(scheme, ro=True)
            try:
                r = con.execute("SELECT signal_uid FROM paper_position WHERE position_id=?",
                                (p0["position_id"],)).fetchone()
                sig = r["signal_uid"] if r else None
            finally:
                con.close()
            if not sig:
                dup.append({"scheme": scheme, "skipped": "no_signal_uid"})
                continue
            res = PE.open_position(scheme, p0["symbol"], signal_uid=sig, direction="long",
                                   event_time=int(p0["entry_ts"]),
                                   available_at=int(p0["entry_ts"]),
                                   requested_at=int(p0["entry_ts"]),
                                   now_ms=int(p0["entry_ts"]) + M15,
                                   origin="replay", epoch_ms=0)
            dup.append({"scheme": scheme, "signal_uid": sig,
                        "ok": res.get("ok"), "reason": res.get("reason")})
        out["duplicate_attempt"] = dup
    finally:
        feed.close()
        store.close()

    out["ledgers"] = {s: AE.ledger_snapshot(s) for s in AE.SCHEMES}
    out["connections"] = _CONN_LOG
    out["config"] = {k: v for k, v in cfg.items() if k != "doc"}
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

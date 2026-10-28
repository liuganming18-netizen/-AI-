#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""06 · 自然运行观测：重载后生产实例的扫描 / 候选 / 挂单 / 成交，逐项如实记数。

**不做任何写入**：只读状态文件、`live_signals.sqlite3`（mode=ro）、`paper_3a.sqlite3`（mode=ro）。
自然信号或成交为零就记零，不用隔离注入或既有测试探针冒充自然成交。

用法：
  python3 06_natural_observe.py --minutes 12 --interval 30
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import time
from pathlib import Path

UTC = dt.timezone.utc
PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "PARALLEL-STRATEGY-01-WORK"
OUT = EVID / "out"
BACKEND = PROJ / "框架" / "backend"
STATUS = BACKEND / "locks" / "paper_runtime_3a_status.json"
LIVE = BACKEND / "live" / "live_signals.sqlite3"
LEDGER = PROJ / "数据" / "paper" / "paper_3a.sqlite3"


def iso(ms):
    return None if ms is None else dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def now_iso():
    return dt.datetime.now(UTC).isoformat()


def ro(p: Path):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=8)
    con.row_factory = sqlite3.Row
    return con


def sample() -> dict:
    s = {"at_utc": now_iso(), "at_ms": int(time.time() * 1000)}
    # ── 运行状态 ──
    try:
        d = json.loads(STATUS.read_text(encoding="utf-8"))
        ri = d.get("runtime_identity") or {}
        se = d.get("signal_engine") or {}
        od = d.get("order_processing") or {}
        ed = d.get("execution_daemon") or {}
        s["runtime"] = {
            "pid": ri.get("pid"), "status": ri.get("status"),
            "overall_health": ri.get("overall_health"),
            "started_at_utc": iso(ri.get("started_at")),
            "cycle_count": ri.get("cycle_count"),
            "watermark_1h_utc": iso(se.get("watermark_1h")),
            "watermark_4h_utc": iso(se.get("watermark_4h")),
            "universe": se.get("universe"),
            "pending_in_db": od.get("current_pending_in_db"),
            "allow_new_entries": (od.get("new_entry_gate") or {}).get("allow_new_entries"),
            "open_positions": ed.get("open_positions"),
            "pending_matched_total": ed.get("pending_matched_total"),
            "trailing_updates_total": ed.get("trailing_updates_total"),
            "exits_triggered_total": ed.get("exits_triggered_total"),
            "last_error": ed.get("last_error"),
            "status_file_mtime_utc": iso(STATUS.stat().st_mtime * 1000),
        }
    except Exception as exc:                                   # noqa: BLE001
        s["runtime"] = {"error": f"{type(exc).__name__}: {exc}"}
    # ── 自然信号行（origin='live'）──────────────────────────────────────────
    try:
        con = ro(LIVE)
        q = {}
        for r in con.execute("SELECT origin, COUNT(*) FROM live_signal GROUP BY origin"):
            q[r[0]] = r[1]
        s["live_signal_by_origin"] = q
        s["live_signal_natural"] = q.get("live", 0)
        s["live_signal_total"] = sum(q.values())
        r = con.execute("SELECT MAX(confirmed_at) FROM live_signal WHERE origin='live'").fetchone()
        s["live_signal_last_confirmed_utc"] = iso(r[0]) if r and r[0] else None
        con.close()
    except Exception as exc:                                   # noqa: BLE001
        s["live_signal_by_origin"] = {"error": f"{type(exc).__name__}: {exc}"}
    # ── 账本 ──
    try:
        con = ro(LEDGER)
        cols = {r[1] for r in con.execute("PRAGMA table_info(paper_position)")}
        s["ledger"] = {
            "position_total": con.execute("SELECT COUNT(*) FROM paper_position").fetchone()[0],
            "position_open": con.execute(
                "SELECT COUNT(*) FROM paper_position WHERE status='open' "
                "AND COALESCE(voided,0)=0").fetchone()[0],
            "position_reality_fill_1": (con.execute(
                "SELECT COUNT(*) FROM paper_position WHERE reality_fill=1").fetchone()[0]
                if "reality_fill" in cols else None),
            "pending_pending": con.execute(
                "SELECT COUNT(*) FROM paper_pending WHERE status='pending'").fetchone()[0],
            "pending_total": con.execute("SELECT COUNT(*) FROM paper_pending").fetchone()[0],
            "order_total": con.execute("SELECT COUNT(*) FROM paper_order").fetchone()[0],
            "order_filled": con.execute(
                "SELECT COUNT(*) FROM paper_order WHERE status='filled'").fetchone()[0],
            "order_rejected": con.execute(
                "SELECT COUNT(*) FROM paper_order WHERE status='rejected'").fetchone()[0],
            "processed_total": con.execute("SELECT COUNT(*) FROM paper_processed").fetchone()[0],
            "has_reality_columns": "reality_fill" in cols,
        }
        # 最近 20 条挂单/订单的拒绝原因（看有没有新的现实撮合拒单码）
        s["recent_reject_reasons"] = [
            {"event_uid": r[0], "symbol": r[1], "reason": r[2], "ts_utc": iso(r[3])}
            for r in con.execute(
                "SELECT event_uid,symbol,reject_reason,ts FROM paper_order "
                "WHERE status='rejected' ORDER BY order_id DESC LIMIT 20")]
        con.close()
    except Exception as exc:                                   # noqa: BLE001
        s["ledger"] = {"error": f"{type(exc).__name__}: {exc}"}
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=12.0)
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else OUT / "06_natural_observe.json"

    t0 = time.time()
    series = []
    while time.time() - t0 <= args.minutes * 60:
        series.append(sample())
        last = series[-1]
        rt = last.get("runtime") or {}
        print(f"[{last['at_utc']}] pid={rt.get('pid')} cycle={rt.get('cycle_count')} "
              f"wm1h={rt.get('watermark_1h_utc')} wm4h={rt.get('watermark_4h_utc')} "
              f"natural={last.get('live_signal_natural')} "
              f"pending={(last.get('ledger') or {}).get('pending_pending')} "
              f"open={(last.get('ledger') or {}).get('position_open')} "
              f"filled={(last.get('ledger') or {}).get('order_filled')}", flush=True)
        time.sleep(args.interval)

    first, final = series[0], series[-1]
    r0, r1 = (first.get("runtime") or {}), (final.get("runtime") or {})
    l0, l1 = (first.get("ledger") or {}), (final.get("ledger") or {})
    summary = {
        "samples": len(series),
        "window_utc": [first["at_utc"], final["at_utc"]],
        "runtime_pid_stable": r0.get("pid") == r1.get("pid"),
        "pid": r1.get("pid"),
        "cycle_delta": (r1.get("cycle_count") or 0) - (r0.get("cycle_count") or 0),
        "watermark_1h_moved": r0.get("watermark_1h_utc") != r1.get("watermark_1h_utc"),
        "watermark_4h_moved": r0.get("watermark_4h_utc") != r1.get("watermark_4h_utc"),
        "watermark_1h": [r0.get("watermark_1h_utc"), r1.get("watermark_1h_utc")],
        "watermark_4h": [r0.get("watermark_4h_utc"), r1.get("watermark_4h_utc")],
        "natural_signals": {"start": first.get("live_signal_natural"),
                            "end": final.get("live_signal_natural")},
        "live_signal_by_origin_end": final.get("live_signal_by_origin"),
        "ledger": {
            "pending_pending": [l0.get("pending_pending"), l1.get("pending_pending")],
            "position_open": [l0.get("position_open"), l1.get("position_open")],
            "order_filled": [l0.get("order_filled"), l1.get("order_filled")],
            "order_rejected": [l0.get("order_rejected"), l1.get("order_rejected")],
            "position_reality_fill_1": [l0.get("position_reality_fill_1"),
                                        l1.get("position_reality_fill_1")],
            "has_reality_columns": l1.get("has_reality_columns"),
        },
        "errors": [s.get("runtime", {}).get("last_error") for s in series
                   if s.get("runtime", {}).get("last_error")],
        "honest_zero": {
            "natural_signal_zero": (final.get("live_signal_natural") or 0) == 0,
            "natural_order_zero": (l1.get("order_filled") or 0) == (l0.get("order_filled") or 0),
            "note": "自然信号/成交为零就如实记零；隔离注入与既有测试探针不算自然成交。",
        },
    }
    out.write_text(json.dumps({"summary": summary, "series": series},
                              ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"written {out}")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())

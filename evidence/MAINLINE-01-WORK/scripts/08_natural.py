#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""08 · 自然观察（只读）：重载后新实例在真实时钟下跑一段时间，如实记零。

纪律：**只读**。不造单、不注入、不写生产账本。所有数字来自 API 与账本只读查询。
输出 `out/08_natural.json`。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import subprocess
import time
from pathlib import Path

UTC = dt.timezone.utc
PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = PROJ / "evidence" / "MAINLINE-01-WORK" / "out"
LEDGER = PROJ / "数据" / "paper" / "paper_3a.sqlite3"
LIVE = PROJ / "框架" / "backend" / "live" / "live_signals.sqlite3"
STATUS = PROJ / "框架" / "backend" / "locks" / "paper_runtime_3a_status.json"
API = "http://127.0.0.1:8787"


def utc(ms=None):
    if ms is None:
        return dt.datetime.now(UTC).isoformat()
    return dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def http(path: str, timeout=8):
    cp = subprocess.run(["curl", "-s", "-m", str(timeout), f"{API}{path}"],
                        capture_output=True, text=True, timeout=timeout + 5)
    try:
        return json.loads(cp.stdout or "{}")
    except Exception:                                          # noqa: BLE001
        return {"_raw": (cp.stdout or "")[:300]}


def ro(p: Path):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=8)
    con.row_factory = sqlite3.Row
    return con


def snapshot() -> dict:
    s = {"at_utc": utc()}
    # 运行身份
    if STATUS.exists():
        d = json.loads(STATUS.read_text(encoding="utf-8"))
        ri = d.get("runtime_identity") or {}
        se = d.get("signal_engine") or {}
        od = d.get("order_processing") or {}
        ed = d.get("execution_daemon") or {}
        s["runtime"] = {
            "pid": ri.get("pid"), "status": ri.get("status"),
            "health": ri.get("overall_health"), "cycle_count": ri.get("cycle_count"),
            "started_at_utc": utc(ri["started_at"]) if ri.get("started_at") else None,
            "universe": se.get("universe"),
            "watermark_1h_utc": utc(se["watermark_1h"]) if se.get("watermark_1h") else None,
            "watermark_4h_utc": utc(se["watermark_4h"]) if se.get("watermark_4h") else None,
            "errors": (d.get("errors") or [])[:5],
            "pending_in_db": od.get("current_pending_in_db"),
            "allow_new_entries": (od.get("new_entry_gate") or {}).get("allow_new_entries"),
            "open_positions": ed.get("open_positions"),
            "pending_matched_total": ed.get("pending_matched_total"),
            "exits_triggered_total": ed.get("exits_triggered_total"),
        }
    else:
        s["runtime"] = {"status": "missing"}
    # 账本业务侧
    con = ro(LEDGER)
    cols = {r[1] for r in con.execute("PRAGMA table_info(paper_position)")}
    def c(n):
        return n if n in cols else f"NULL AS {n}"
    s["ledger"] = {
        "positions": con.execute("SELECT COUNT(*) FROM paper_position").fetchone()[0],
        "open_positions": con.execute(
            "SELECT COUNT(*) FROM paper_position WHERE status='open' AND COALESCE(voided,0)=0"
        ).fetchone()[0],
        "pending": con.execute(
            "SELECT COUNT(*) FROM paper_pending WHERE status='pending'").fetchone()[0],
        "orders_filled": con.execute(
            "SELECT COUNT(*) FROM paper_order WHERE status='filled'").fetchone()[0],
        "orders_rejected": con.execute(
            "SELECT COUNT(*) FROM paper_order WHERE status='rejected'").fetchone()[0],
        "reality_fill_1": con.execute(
            f"SELECT COUNT(*) FROM paper_position WHERE {c('reality_fill')} = 1"
            if "reality_fill" in cols else "SELECT 0").fetchone()[0],
        "with_read_matched": con.execute(
            "SELECT COUNT(*) FROM paper_position WHERE read_at_ms IS NOT NULL "
            "AND matched_at_ms IS NOT NULL" if "read_at_ms" in cols else "SELECT 0").fetchone()[0],
        "last_reject": (dict(con.execute(
            "SELECT order_id,symbol,ts,reject_reason FROM paper_order "
            "WHERE status='rejected' ORDER BY order_id DESC LIMIT 1").fetchone() or {})),
    }
    con.close()
    # 自然信号（origin='live'）
    if LIVE.exists():
        lc = ro(LIVE)
        try:
            s["signals"] = {
                "total": lc.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0],
                "natural_live": lc.execute(
                    "SELECT COUNT(*) FROM live_signal WHERE origin='live'").fetchone()[0],
                "by_origin": {r[0]: r[1] for r in lc.execute(
                    "SELECT origin,COUNT(*) FROM live_signal GROUP BY origin")},
            }
        except sqlite3.Error as exc:
            s["signals"] = {"error": str(exc)[:200]}
        lc.close()
    s["api"] = {
        "accounts_quality_status": ((http("/api/v1/paper/accounts").get("quality") or {})
                                    .get("status")),
        "fill_policy_keys_n": len(((http("/api/v1/paper/accounts").get("quality") or {})
                                   .get("fill_policy") or {})),
    }
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=12)
    ap.add_argument("--interval", type=float, default=30)
    args = ap.parse_args()
    samples = []
    t_end = time.time() + args.minutes * 60
    while time.time() < t_end:
        try:
            s = snapshot()
        except Exception as exc:                               # noqa: BLE001
            s = {"at_utc": utc(), "error": f"{type(exc).__name__}: {exc}"}
        samples.append(s)
        r = s.get("runtime") or {}
        print(f"[{s['at_utc']}] pid={r.get('pid')} cycle={r.get('cycle_count')} "
              f"uni={r.get('universe')} st={r.get('status')} h={r.get('health')} "
              f"pend={(s.get('ledger') or {}).get('pending')} "
              f"open={(s.get('ledger') or {}).get('open_positions')} "
              f"nat={(s.get('signals') or {}).get('natural_live')}", flush=True)
        time.sleep(args.interval)
    rec = {"generated_utc": utc(), "minutes": args.minutes, "interval_s": args.interval,
           "samples_n": len(samples), "samples": samples,
           "first": samples[0] if samples else None,
           "last": samples[-1] if samples else None}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "08_natural.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"written 08_natural.json samples={len(samples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

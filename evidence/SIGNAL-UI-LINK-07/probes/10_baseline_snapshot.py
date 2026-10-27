#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-UI-LINK-07 探针⓿：修复前的四环基线快照（只读）。

四环：生成 → 持久化 → API → 页面
每一环都留「可机械复算」的原始读数，供收口时逐环对撞。

只读：行情库/信号库都以 `mode=ro` 打开；接口只发 GET。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
LIVE_DB = ROOT / "框架/backend/live/live_signals.sqlite3"
STATUS = ROOT / "框架/backend/locks/paper_runtime_3a_status.json"
API = os.environ.get("API_BASE", "http://127.0.0.1:8787")


def _iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _get(path):
    try:
        out = subprocess.run(
            ["curl", "-s", "--noproxy", "*", "--max-time", "20", API + path],
            capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout)
    except Exception as exc:                                  # noqa: BLE001
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE.parents[1] / "out" / "00_baseline.json"))
    ap.add_argument("--tag", default="pre_fix")
    args = ap.parse_args()

    # ── 环②：持久化 ─────────────────────────────────────────────────────────
    con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        n_rows = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        by_origin = [dict(r) for r in con.execute(
            "SELECT origin, executable_state, COUNT(*) n FROM live_signal "
            "GROUP BY origin, executable_state")]
        runs = {r["key"]: r["value_json"] for r in con.execute(
            "SELECT key, value_json FROM live_run")}
    finally:
        con.close()

    # ── 环①：生成（生产状态文件）─────────────────────────────────────────────
    st = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}
    se = st.get("signal_engine") or {}

    # ── 环③：API ────────────────────────────────────────────────────────────
    cur = _get("/api/v1/signals?scope=current&limit=500")
    allq = _get("/api/v1/signals?scope=all&limit=500")
    pst = _get("/api/v1/paper/status?arm=3a")

    out = {
        "probe": "SIGNAL-UI-LINK-07/00_baseline",
        "tag": args.tag,
        "captured_real_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ring1_generate": {
            "signals_generated_total": se.get("signals_generated_total"),
            "funnel": se.get("funnel"),
            "state_counter": se.get("state_counter"),
            "idle_breakdown_counts": (se.get("idle_breakdown") or {}).get("counts"),
            "watermark_1h": se.get("watermark_1h"),
            "watermark_1h_utc": _iso(se.get("watermark_1h")),
            "watermark_4h": se.get("watermark_4h"),
            "watermark_4h_utc": _iso(se.get("watermark_4h")),
            "bootstrap_discarded": (se.get("bootstrap") or {}).get("history_candidates_discarded"),
            "bootstrap_recovered": (se.get("bootstrap") or {}).get("recovered_candidates"),
            "bootstrap_has_discarded_detail": "discarded_detail" in (se.get("bootstrap") or {}),
            "last_cycle_utc": _iso(se.get("last_cycle_ms")),
        },
        "ring2_persist": {
            "live_signal_rows": n_rows,
            "by_origin_exec": by_origin,
            "live_run_keys": sorted(runs.keys()),
            "live_run": runs,
        },
        "ring3_api_current": {
            "total": cur.get("total"),
            "n_items": len(cur.get("items") or []),
            "as_of": cur.get("as_of"),
            "quality": cur.get("quality"),
            "missing_reason": cur.get("missing_reason"),
        },
        "ring3_api_all": {
            "total": allq.get("total"),
            "n_items": len(allq.get("items") or []),
            "as_of": allq.get("as_of"),
            "quality": allq.get("quality"),
        },
        "ring4_ui": {
            "page_request": "/api/v1/signals?scope=current&limit=500",
            "page_source": "UI界面/web/src/api/useMarketData.ts:681",
            "empty_state_text": "暂无实时信号",
            "empty_state_source": "UI界面/web/src/components/SignalListPanel.tsx:525-531",
            "consumes_diagnostics": False,
            "note": ("空态只有一句「暂无实时信号」；/paper/status 已返回的逐币阶段/漏斗/"
                     "水位/丢弃候选数**均未在本区展示**"),
        },
        "paper_status_keys": sorted((pst.get("item") or {}).keys()),
        "paper_status_signal_engine_keys": sorted(
            ((pst.get("item") or {}).get("signal_engine") or {}).keys()),
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
    print(f"\n输出：{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

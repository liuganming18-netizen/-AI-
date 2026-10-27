# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜证据脚本公共件（只读；不写生产库、不下单、不联网）。"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[3]
BACKEND = BASE / "框架" / "backend"
OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("no_proxy", "*")
for p in (str(BACKEND), str(BASE)):
    if p not in sys.path:
        sys.path.insert(0, p)

H1 = 3_600_000
H4 = 4 * H1
DAY = 86_400_000


def utc(ms) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def now_ms() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


def market_conn():
    import live_store as L
    con = L.market_conn()
    return con


def universe():
    import live_pool as LP
    syms, meta = LP.scope_symbols()
    return list(syms), meta


def dump(name: str, obj) -> Path:
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[out] {p} ({p.stat().st_size} B)")
    return p


def api(path: str, timeout: float = 15.0):
    """读本机 API（必须绕过代理：本机有 HTTP_PROXY 而无 no_proxy）。"""
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(f"http://127.0.0.1:8787{path}", timeout=timeout) as r:
        return json.loads(r.read())


def engine_state(eng) -> dict:
    """把一台引擎的「可比较终态」压成字典（跨运行稳定身份 = (symbol, 阶段)）。"""
    sm = eng.sm
    ev = sm.active_event
    return {
        "state": ev.current_state.value if ev else "IDLE",
        "event_id": ev.event_id if ev else None,
        "box_high": (ev.box.high_h if ev else None),
        "box_low": (ev.box.low_l if ev else None),
        "box_atr": (ev.box.atr_a if ev else None),
        "box_formed_ms": (ev.box.formation_time_ms if ev else None),
        "sweep_low": (ev.sweep_low_s if ev else None),
        "retest_low": (ev.retest_low_lr if ev else None),
        "cooling_until_ms": sm.cooling_until_ms,
        "atr": eng.last_atr,
        "atr_bars_seen": eng.atr_bars_seen,
        "box_gate_bars": eng.box_gate_bars,
        "processed_1h_max": (max(eng.processed_1h_opens) if eng.processed_1h_opens else None),
        "processed_4h_max": (max(eng.processed_4h_opens) if eng.processed_4h_opens else None),
        "n_transitions": len(sm.transitions),
        "n_completed": len(sm.completed_events),
        "data_invalid": bool((eng.data_quality or {}).get("invalid")),
    }

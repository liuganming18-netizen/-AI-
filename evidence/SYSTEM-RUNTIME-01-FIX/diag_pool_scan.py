# -*- coding: utf-8 -*-
"""全池抽样诊断：用真实行情跑状态机，统计「有多少标的出过事件 / 当前处于什么状态」。

只读行情库。回答一个问题：页面空白是链路故障，还是这个策略在当前全池里确实没事件。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

MARKET = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 30


class MemWatcher:
    def __init__(self, b4, b1):
        self.b4, self.b1 = b4, b1

    def get_closed_bars(self, timeframe, start_open_ms, end_open_ms):
        src = self.b4 if timeframe == "4h" else self.b1
        return [b for b in src if int(b["open_time"]) >= int(start_open_ms)
                and int(b["close_time"]) <= int(end_open_ms)]


def load(con, sym, tf):
    return [dict(r) for r in con.execute(
        "SELECT open_time, close_time, open, high, low, close, volume, quote_volume "
        "FROM candles WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe=? "
        "AND is_closed=1 ORDER BY open_time ASC", (sym, tf))]


import unified_runtime as UR  # noqa: E402

con = sqlite3.connect(f"file:{MARKET}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
syms = [r[0] for r in con.execute(
    "SELECT symbol FROM candle_heads WHERE exchange='binance' AND market='um' "
    "AND timeframe='1h' GROUP BY symbol ORDER BY symbol")][:LIMIT]

t0 = time.time()
rows, total_cands = [], 0
for sym in syms:
    b4, b1 = load(con, sym, "4h"), load(con, sym, "1h")
    if len(b4) < 60 or len(b1) < 200:
        rows.append({"symbol": sym, "skipped": f"bars 4h={len(b4)} 1h={len(b1)}"})
        continue
    eng = UR.RealSignalEngine(symbol=sym)
    eng.history_floor_ms = int(b1[0]["open_time"])
    w = MemWatcher(b4, b1)
    n = 0
    for bar in b4:
        n += len(eng.process_market(int(bar["close_time"]), w))
    st = eng.sm.active_event.current_state.value if eng.sm.active_event else "IDLE"
    total_cands += n
    rows.append({"symbol": sym, "candidates": n, "state": st,
                 "box": eng.sm.active_event is not None})
con.close()

summary = {
    "symbols_scanned": len(syms),
    "elapsed_sec": round(time.time() - t0, 1),
    "total_candidates": total_cands,
    "with_candidates": [r for r in rows if r.get("candidates")],
    "state_counter": {},
    "sample": rows[:10],
}
for r in rows:
    k = r.get("state") or "SKIP"
    summary["state_counter"][k] = summary["state_counter"].get(k, 0) + 1
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

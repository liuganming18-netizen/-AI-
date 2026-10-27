# -*- coding: utf-8 -*-
"""离线诊断：把生产行情库里 BTCUSDT 的真实 4h/1h 序列喂给状态机，看历史上有无事件。

只读行情库、不写任何库、不启进程。目的：区分「引擎不工作」与「当前时点确实没事件」。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

MARKET = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"


class MemWatcher:
    """内存行情源：只回放已闭合 bar，绝不注入未来数据。"""

    def __init__(self, bars_4h, bars_1h):
        self.b4 = bars_4h
        self.b1 = bars_1h

    def get_closed_bars(self, timeframe, start_open_ms, end_open_ms):
        src = self.b4 if timeframe == "4h" else self.b1
        return [b for b in src
                if int(b["open_time"]) >= int(start_open_ms)
                and int(b["close_time"]) <= int(end_open_ms)]


def load(tf):
    con = sqlite3.connect(f"file:{MARKET}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT open_time, close_time, open, high, low, close, volume, quote_volume "
        "FROM candles WHERE exchange='binance' AND market='um' AND symbol='BTCUSDT' "
        "AND timeframe=? AND is_closed=1 ORDER BY open_time ASC", (tf,))]
    con.close()
    return rows


def f(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.UTC).strftime("%m-%d %H:%M")


b4, b1 = load("4h"), load("1h")
out = {
    "bars_4h": len(b4), "bars_1h": len(b1),
    "4h_range": [f(b4[0]["open_time"]), f(b4[-1]["close_time"])] if b4 else None,
    "1h_range": [f(b1[0]["open_time"]), f(b1[-1]["close_time"])] if b1 else None,
}

import unified_runtime as UR  # noqa: E402

cfg = UR.EventTrendConfig()
out["config"] = {
    "box_bars_4h": cfg.box_bars_4h,
    "atr_period_4h": cfg.atr_period_4h,
    "breakout_max_total_hours": cfg.breakout_max_total_hours,
    "cooling_hours": cfg.cooling_hours,
}

eng = UR.RealSignalEngine(symbol="BTCUSDT")
eng.history_floor_ms = int(b1[0]["open_time"]) if b1 else 0
w = MemWatcher(b4, b1)

# 逐 4h 收盘推进，模拟真实运行时（每步只看到当时已闭合的 bar）
steps, cands = 0, []
for bar in b4:
    steps += 1
    got = eng.process_market(int(bar["close_time"]), w)
    for c in got:
        cands.append({"t": f(c.snapshot_time_ms), "close": c.breakout_close,
                      "vol_ratio": round(c.volume_ratio, 3), "stop": c.initial_stop_price})

out["steps"] = steps
out["candidates"] = len(cands)
out["candidate_list"] = cands[-10:]
out["final_state"] = (eng.sm.active_event.current_state.value
                      if eng.sm.active_event else "IDLE")
out["active_box"] = eng.sm.active_event is not None

print(json.dumps(out, ensure_ascii=False, indent=2, default=str))

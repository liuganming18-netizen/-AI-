# -*- coding: utf-8 -*-
"""列出全池历史回放产出的候选明细（修复 OI 闸门后的真实信号能力证明）。

用途：运行时 bootstrap 会**主动丢弃**历史候选（不补开历史信号），
所以页面在「当前没有新突破」时必然是 0 笔。要证明引擎确实会出信号，
必须单独把历史回放产出的候选逐条列出来（标的、时点、箱体、量能倍数、OI 状态）。

只读，不写库、不落信号。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault(
    "MARKET_DB", str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"))

import unified_runtime as U  # noqa: E402

OUT = ROOT / "evidence" / "SYSTEM-RUNTIME-01-FIX" / "candidates_pool.json"


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d %H:%MZ")


def main() -> int:
    syms = U._default_universe()
    eng = U.MultiSymbolSignalEngine(syms)
    rows = []
    ready = 0
    for sym in syms:
        b4 = eng.watcher.get_closed_bars(sym, "4h", 0, 2 ** 62)
        b1 = eng.watcher.get_closed_bars(sym, "1h", 0, 2 ** 62)
        if len(b4) < eng.config.box_bars_4h + 8 or len(b1) < 200:
            continue
        e = U.RealSignalEngine(symbol=sym)
        w = U._MemWatcher(b4, b1)
        for bar in b4:
            e.process_market(int(bar["close_time"]), w)
        ready += 1
        for c in e.sm.candidates:
            rows.append({
                "symbol": sym,
                "signal_time": iso(c.snapshot_time_ms),
                "breakout_close": c.breakout_close,
                "box_high_h": c.box_high_h,
                "box_low_l": c.box_low_l,
                "box_atr_a": round(c.box_atr_a, 6),
                "box_er": round(c.box_er, 4),
                "box_width_atr": round((c.box_high_h - c.box_low_l) / c.box_atr_a, 2),
                "volume_ratio": round(c.volume_ratio, 2) if c.volume_ratio else None,
                "initial_stop": round(c.initial_stop_price, 6),
                "risk_r": round(c.initial_risk_r, 6),
                "oi_used": bool(c.pit_proven),
                "oi_note": c.pit_assumption_note,
            })
    rows.sort(key=lambda r: r["signal_time"])
    rep = {"universe": len(syms), "ready": ready,
           "candidates": len(rows), "rows": rows}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

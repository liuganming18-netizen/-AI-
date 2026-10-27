# -*- coding: utf-8 -*-
"""AUTO-LOOP-01 · 候选扫描（只读）

目的：确认「真实策略状态机」在**本轮 paper 启用下限之后**是否产出过候选。
为什么必须查这一步：`live_store.get_paper_epoch_ms()` 把下限钉死在
2026-10-14T01:00Z（fail-closed，只允许不降），早于该时刻的信号会被
`before_paper_epoch_no_backfill` 拒单。若下限之后没有任何真实候选，
隔离回放就不可能在不篡改时钟下限的前提下产生成交 —— 这一点必须如实记录，
不能靠伪造信号绕过。

只读：不写库、不落信号、不调用任何交易函数。
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
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MARKET_DB", str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"))

import unified_runtime as U  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d %H:%MZ")


def main() -> int:
    epoch = 1_791_939_600_000  # 与 live_store.PAPER_EPOCH_MS 一致（只读比对用）
    syms = U._default_universe()
    eng = U.MultiSymbolSignalEngine(syms)
    rows, ready, skipped = [], 0, 0
    for sym in syms:
        b4 = eng.watcher.get_closed_bars(sym, "4h", 0, 2 ** 62)
        b1 = eng.watcher.get_closed_bars(sym, "1h", 0, 2 ** 62)
        if len(b4) < eng.config.box_bars_4h + 8 or len(b1) < 200:
            skipped += 1
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
                "snapshot_time_ms": int(c.snapshot_time_ms),
                "after_paper_epoch": int(c.snapshot_time_ms) >= epoch,
                "breakout_close": c.breakout_close,
                "initial_stop": round(c.initial_stop_price, 8),
                "volume_ratio": round(c.volume_ratio, 2) if c.volume_ratio else None,
            })
    rows.sort(key=lambda r: r["snapshot_time_ms"])
    rep = {"universe": len(syms), "ready": ready, "skipped": skipped,
           "paper_epoch_ms": epoch,
           "paper_epoch_iso": iso(epoch),
           "candidates_total": len(rows),
           "candidates_after_epoch": sum(1 for r in rows if r["after_paper_epoch"]),
           "candidates": rows}
    (OUT / "scan_candidates.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "candidates"},
                     ensure_ascii=False, indent=2))
    for r in rows:
        print(r["signal_time"], r["symbol"], "after_epoch=", r["after_paper_epoch"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

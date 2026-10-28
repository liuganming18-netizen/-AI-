#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""03c · 跨 15m 边界的高频「实时行存在性」探测（只读）。

为什么要单独测这一条：03 在 01:45:01.502Z（执行点后 1.5 秒）读 `realtime_klines` 时，
ETHUSDT 那一行**不存在**；20 秒后再读又有了（`open_time=01:45:00Z`）。
这直接决定了「执行点当场能不能成交」，必须量清楚：

  · 边界前后各标的的行是否**瞬时消失**？
  · 消失多久？换标的换交易所是不是普遍现象？
  · 消失期间引擎会怎么做？（⇒ 挂单等待，不拒单、不回填；见 03 的重试记录）

做法：从 T0−3s 到 T0+30s，每 100ms 读一次（只读、`mode=ro`），
逐标的记录 `present / open_time / is_closed / received_at`，最后算每个标的的
「缺口时长」= 最后一次看到旧 bar 到第一次看到新 bar 之间的间隔。

输出 `out/03c_row_presence.json`。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import time
from pathlib import Path

UTC = dt.timezone.utc
PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = PROJ / "evidence" / "PARALLEL-STRATEGY-01-WORK" / "out"
MARKET_DB = PROJ / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
M15 = 900_000
SYMBOLS = ("ETHUSDT", "BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")


def iso(ms):
    return None if ms is None else dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def ro():
    con = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True, timeout=8)
    con.row_factory = sqlite3.Row
    return con


def read_all(con):
    q = ("SELECT symbol,open_time,is_closed,received_at,open FROM realtime_klines "
         "WHERE exchange='binance' AND market='um' AND timeframe='15m' AND symbol IN ("
         + ",".join("?" * len(SYMBOLS)) + ")")
    return {r["symbol"]: dict(r) for r in con.execute(q, SYMBOLS)}


def main() -> int:
    now = int(time.time() * 1000)
    t0 = ((now // M15) + 1) * M15
    rec = {"generated_at_utc": iso(now), "boundary_at": t0, "boundary_at_utc": iso(t0),
           "symbols": list(SYMBOLS), "interval_ms": 100, "samples": []}
    print(f"[*] 目标边界 {iso(t0)}，从 T0−3s 采到 T0+30s", flush=True)

    wait = t0 - 3_000 - int(time.time() * 1000)
    if wait > 0:
        time.sleep(wait / 1000.0)

    con = ro()
    try:
        end = t0 + 30_000
        while int(time.time() * 1000) <= end:
            ts = int(time.time() * 1000)
            rows = read_all(con)
            rec["samples"].append({
                "at_ms": ts, "minus_boundary_ms": ts - t0,
                "present": {s: (s in rows) for s in SYMBOLS},
                "open_time": {s: (rows[s]["open_time"] if s in rows else None) for s in SYMBOLS},
                "is_closed": {s: (rows[s]["is_closed"] if s in rows else None) for s in SYMBOLS},
                "open": {s: (rows[s]["open"] if s in rows else None) for s in SYMBOLS},
            })
            time.sleep(0.1)
    finally:
        con.close()

    # ── 逐标的算缺口 ────────────────────────────────────────────────────────
    gaps = {}
    for s in SYMBOLS:
        absent = [x for x in rec["samples"] if not x["present"][s]]
        # 缺口 = 第一次「无行」到下一次「有行且 open_time==T0」
        first_absent = next((x for x in rec["samples"] if not x["present"][s]), None)
        first_new = next((x for x in rec["samples"]
                          if x["present"][s] and x["open_time"][s] == t0), None)
        gaps[s] = {
            "absent_samples_n": len(absent),
            "first_absent_minus_boundary_ms": (first_absent["minus_boundary_ms"]
                                              if first_absent else None),
            "first_new_bar_minus_boundary_ms": (first_new["minus_boundary_ms"]
                                               if first_new else None),
            "gap_ms": ((first_new["minus_boundary_ms"]
                        - first_absent["minus_boundary_ms"])
                       if (first_absent and first_new) else 0),
            "new_bar_was_forming_on_first_sight":
                (first_new["is_closed"][s] == 0) if first_new else None,
        }
    rec["gaps"] = gaps
    rec["all_pass"] = all(v["first_new_bar_minus_boundary_ms"] is not None
                          for v in gaps.values())

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "03c_row_presence.json"
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"written {p}  样本 {len(rec['samples'])}")
    for s, v in gaps.items():
        print(f"  {s}: 首见新 bar @+{v['first_new_bar_minus_boundary_ms']} ms，"
              f"无行样本 {v['absent_samples_n']} 个，缺口 {v['gap_ms']} ms，"
              f"首见时 is_closed={v['new_bar_was_forming_on_first_sight']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

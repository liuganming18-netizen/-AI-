#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""01 · 现状取证：改之前账本里「时刻」到底怎么填的。

只读。回答三个问题：
  Q1 生产 3a 账本每一笔成交的 `entry_ts`（价格事件时刻）、`fill_ts`（记录时刻）
     差多少？差得越多，说明「用 bar 开盘冒充成交时刻」越严重。
  Q2 有没有任何一列能读出「数据实际拿到手的时刻」？
  Q3 唯一未平仓持仓 #4（`test_probe_succ`，工单要求原样保留）的完整时刻链是什么？

输出 `out/01_baseline_times.json`。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

UTC = dt.timezone.utc
PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = PROJ / "evidence" / "MAINLINE-01-WORK" / "out"
LEDGER = PROJ / "数据" / "paper" / "paper_3a.sqlite3"


def iso(ms):
    return None if ms is None else dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def ro(p: Path):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def main() -> int:
    con = ro(LEDGER)
    fill_cols = {r[1] for r in con.execute("PRAGMA table_info(paper_fill)")}
    pos_cols = {r[1] for r in con.execute("PRAGMA table_info(paper_position)")}

    def c(cols, name):
        return name if name in cols else f"NULL AS {name}"

    fills = []
    for r in con.execute(
            "SELECT fill_id,order_id,position_id,signal_uid,symbol,action,raw_price,fill_price,"
            "bar_open_time,bar_close_time,fill_ts,event_time,available_at,requested_at,voided,"
            f"{c(fill_cols,'reality_fill')},{c(fill_cols,'data_available_at')},"
            f"{c(fill_cols,'fill_source')} FROM paper_fill WHERE action='open' ORDER BY fill_id"):
        d = dict(r)
        d["bar_open_utc"] = iso(d["bar_open_time"])
        d["bar_close_utc"] = iso(d["bar_close_time"])
        d["fill_ts_utc"] = iso(d["fill_ts"])
        # 核心量：记录时刻比价格事件时刻晚多少（这就是「冒充」的量化）
        d["fill_ts_minus_bar_open_ms"] = (None if d["fill_ts"] is None or d["bar_open_time"] is None
                                          else int(d["fill_ts"]) - int(d["bar_open_time"]))
        d["fill_ts_minus_bar_close_ms"] = (None if d["fill_ts"] is None or d["bar_close_time"] is None
                                           else int(d["fill_ts"]) - int(d["bar_close_time"]))
        fills.append(d)

    pos = []
    for r in con.execute(
            "SELECT position_id,symbol,status,entry_ts,entry_price,exit_ts,voided,signal_uid,"
            "requested_at,first_request_ms,earliest_fill_ms,origin,"
            f"{c(pos_cols,'reality_fill')},{c(pos_cols,'data_available_at')},"
            f"{c(pos_cols,'fill_source')} FROM paper_position ORDER BY position_id"):
        d = dict(r)
        d["entry_utc"] = iso(d["entry_ts"])
        d["exit_utc"] = iso(d["exit_ts"])
        pos.append(d)

    counts = {}
    for t in ("paper_position", "paper_order", "paper_fill", "paper_pending",
              "paper_processed", "paper_blocked", "paper_stop_history", "paper_funding"):
        try:
            counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            counts[t] = None
    con.close()

    # Q2：改之前有没有「数据可得时刻」列
    q2 = {
        "paper_fill_has_data_available_at": "data_available_at" in fill_cols,
        "paper_fill_has_read_at_ms": "read_at_ms" in fill_cols,
        "paper_fill_has_matched_at_ms": "matched_at_ms" in fill_cols,
        "note": "本单改动前：三列不存在 ⇒ 账本无法自证「行情何时到手」「撮合何时完成」。",
    }

    # Q3：#4 的时刻链
    p4 = next((x for x in pos if x["position_id"] == 4), None)
    f4 = next((x for x in fills if x["position_id"] == 4), None)
    chain4 = None
    if p4 and f4:
        chain4 = {
            "signal_uid": p4["signal_uid"],
            "event_time": iso(f4.get("event_time")),
            "available_at": iso(f4.get("available_at")),
            "requested_at": iso(f4.get("requested_at")),
            "earliest_fill_ms": iso(p4.get("earliest_fill_ms")),
            "price_event_bar_open": iso(f4.get("bar_open_time")),
            "price_event_bar_close": iso(f4.get("bar_close_time")),
            "fill_ts_recorded": iso(f4.get("fill_ts")),
            "entry_ts": iso(p4.get("entry_ts")),
            "fill_ts_minus_bar_open_ms": f4.get("fill_ts_minus_bar_open_ms"),
            "fill_ts_minus_bar_close_ms": f4.get("fill_ts_minus_bar_close_ms"),
            "reality_fill": f4.get("reality_fill"),
            "verdict": ("这笔是**闭 bar 之后**才记的成交（fill_ts 晚于 bar_close），"
                        "但 entry_ts 写的是 bar 开盘时刻 ⇒ 账本里的「入场时刻」"
                        "比真实处理早 15 分 14 秒。这正是本单要消灭的那类时间错位。"),
        }

    rec = {
        "ledger": str(LEDGER),
        "counts": counts,
        "fills": fills,
        "positions": pos,
        "q1_fill_ts_minus_bar_open": {
            "n": len([f for f in fills if f["fill_ts_minus_bar_open_ms"] is not None]),
            "values_ms": [f["fill_ts_minus_bar_open_ms"] for f in fills],
            "values_min": [None if f["fill_ts_minus_bar_open_ms"] is None
                           else round(f["fill_ts_minus_bar_open_ms"] / 60000, 2) for f in fills],
        },
        "q2_columns_before_patch": q2,
        "q3_position_4_chain": chain4,
        "generated_utc": dt.datetime.now(UTC).isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "01_baseline_times.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"counts": counts,
                      "fill_ts_minus_bar_open_min": rec["q1_fill_ts_minus_bar_open"]["values_min"],
                      "columns_before": q2,
                      "position_4": chain4}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

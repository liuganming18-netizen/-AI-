# -*- coding: utf-8 -*-
"""AUTO-LOOP-01 · 08 根因取证：4h 止损判定用了「入场前已收盘」的 bar

背景：修复前 WALUSDT / BBUSDT / VELVETUSDT 三个样本全部在**入场后第一根 15m** 就被判
`trailing_stop_loss`，导致「止损上移」永远不可能发生。本脚本对**修复前留下的账本**
（iso/WALUSDT，未被重建）逐条取证：

  1. 退出那一刻，`advance_position_trailing` 会取 `last_closed_4h(upto=now)`；
  2. 该 4h bar 的 `close_time` 是否 **早于** 持仓 `entry_ts`；
  3. 同一时刻，15m 路径看到的 bar 的 low 是否 **根本没碰到** 止损。

三项同时成立 ⇒ 退出是「用入场前的价格事件」判出来的，不是真实价格击穿。
只读，不改任何文件。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = Path(__file__).resolve().parent.parent / "out"
ISO = Path(__file__).resolve().parent.parent / "iso"
MDB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"


def iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def main() -> int:
    rows = []
    for sym in ("WALUSDT",):
        p = ISO / sym / "paper" / "paper_3a.sqlite3"
        if not p.exists():
            print(sym, "账本不存在"); continue
        con = sqlite3.connect(p)
        con.row_factory = sqlite3.Row
        pos = dict(con.execute("SELECT * FROM paper_position ORDER BY position_id LIMIT 1").fetchone())
        con.close()
        m = sqlite3.connect(f"file:{MDB}?mode=ro", uri=True)
        m.row_factory = sqlite3.Row
        exit_now = int(pos["exit_ts"])
        entry_ts = int(pos["entry_ts"])
        stop = float(pos["stop_price"])
        b4 = dict(m.execute(
            "SELECT open_time,close_time,low FROM candle_versions WHERE symbol=? AND timeframe='4h' "
            "AND close_time<=? ORDER BY close_time DESC LIMIT 1", (sym, exit_now)).fetchone())
        b15 = dict(m.execute(
            "SELECT open_time,close_time,low FROM candle_versions WHERE symbol=? AND timeframe='15m' "
            "AND close_time<=? ORDER BY close_time DESC LIMIT 1", (sym, exit_now)).fetchone())
        m.close()
        rows.append({
            "symbol": sym,
            "entry_ts": iso(entry_ts), "entry_price": pos["entry_price"],
            "initial_stop": pos["initial_stop_price"], "exit_ts": iso(exit_now),
            "exit_price": pos["exit_price"], "exit_reason": pos["exit_reason"],
            "4h_bar_used": {"open": iso(b4["open_time"]), "close": iso(b4["close_time"]),
                            "low": b4["low"]},
            "4h_bar_closed_before_entry": int(b4["close_time"]) <= entry_ts,
            "4h_low_le_stop": float(b4["low"]) <= stop,
            "15m_bar_at_same_moment": {"open": iso(b15["open_time"]), "close": iso(b15["close_time"]),
                                       "low": b15["low"]},
            "15m_low_le_stop": float(b15["low"]) <= stop,
            "verdict": ("用入场前已收盘的 4h bar 判退出（因果倒挂）"
                        if int(b4["close_time"]) <= entry_ts and float(b4["low"]) <= stop
                        and float(b15["low"]) > stop else "需人工复核"),
        })
    rep = {"cases": rows,
           "conclusion": ("修复前：4h 路径取 last_closed_4h(upto=now)，在入场后不满一根 4h 时"
                          "拿到的是入场前就已收盘的 bar，其 low 击穿 entry×0.95 ⇒ 入场后第一根"
                          "15m 就被平仓；而同一时刻 15m 路径的 bar_low 高于止损，说明不是真实"
                          "价格击穿。修复=加 `ref_close_ms <= entry_ts` 守卫（paper_engine."
                          "advance_position_trailing）。")}
    (OUT / "rootcause_4h_causality.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    for c in rows:
        print(json.dumps(c, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

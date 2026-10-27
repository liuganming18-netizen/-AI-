# -*- coding: utf-8 -*-
"""增量推进端到端验证：新 1h bar 到达时，运行时的 `step()` 到底会不会产出候选。

为什么必须单独验：
  `bootstrap()` 走的是**历史回放**，`step()` 走的是**增量游标**
  （`start_1h = max(processed_1h_opens) + 1H`）。两者是不同代码路径。
  历史回放能出候选，不等于实盘等新 bar 时也能出。

做法（用真实数据、隔离库，绝不污染生产）：
  1. 取真实全池回放里已确认的一个候选（BBUSDT 2026-10-11 07:00Z 突破）；
  2. 把该标的的 K 线**截断到突破前一根**，导入临时库；
  3. `bootstrap()` 到突破前一刻 ⇒ 期望状态 = RETEST；
  4. 再把**真实的突破那根 1h bar** 追加进临时库；
  5. 调 `step()` ⇒ 期望产出 1 个候选。

只写临时目录，生产库只读。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
REAL_DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"

SYMBOL = "BBUSDT"
# 突破那根 1h bar 的**开盘**时刻。候选快照的 snapshot_time 是决策时刻（= 收盘后一刻），
# 所以「07:00Z 的信号」对应开盘 06:00Z 的那根 bar。这里用日期算，不手写毫秒
# （上一版手写成 1_791_010_800_000，实际差了 8 天，导致截断点错误、状态停在 BOX）。
BREAKOUT_OPEN_MS = int(dt.datetime(2026, 10, 11, 6, 0, 0, tzinfo=dt.UTC).timestamp() * 1000)
H1 = 3_600_000

OUT = ROOT / "evidence" / "SYSTEM-RUNTIME-01-FIX" / "step_advance_probe.json"


def ddl(con, kind, name):
    r = con.execute(
        "SELECT sql FROM sqlite_master WHERE type=? AND name=?", (kind, name)).fetchone()
    return r[0] if r else None


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="wx_step_probe_"))
    tdb = tmpdir / "market_probe.sqlite3"
    rep = {"symbol": SYMBOL, "tmp_db": str(tdb)}

    src = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(tdb)
    try:
        for name in ("sources", "candle_versions", "candle_heads"):
            dst.execute(ddl(src, "table", name))
        dst.execute(ddl(src, "view", "candles"))
        dst.commit()

        # 只搬该标的的行，且截断到突破前一根（close_time <= 07:00Z）
        rows = src.execute(
            "SELECT * FROM candle_versions WHERE exchange='binance' AND market='um' "
            "AND symbol=? AND close_time<=? ORDER BY timeframe, open_time",
            (SYMBOL, BREAKOUT_OPEN_MS)).fetchall()
        srcids = sorted({r["source_id"] for r in rows})
        srows = src.execute(
            f"SELECT * FROM sources WHERE id IN ({','.join('?' * len(srcids))})",
            srcids).fetchall() if srcids else []
        cols_v = [d[0] for d in src.execute(
            "SELECT * FROM candle_versions LIMIT 0").description]
        cols_s = [d[0] for d in src.execute(
            "SELECT * FROM sources LIMIT 0").description]
        dst.executemany(
            f"INSERT OR IGNORE INTO sources({','.join(cols_s)}) "
            f"VALUES({','.join('?' * len(cols_s))})",
            [tuple(r[c] for c in cols_s) for r in srows])
        dst.executemany(
            f"INSERT OR IGNORE INTO candle_versions({','.join(cols_v)}) "
            f"VALUES({','.join('?' * len(cols_v))})",
            [tuple(r[c] for c in cols_v) for r in rows])
        dst.executemany(
            "INSERT OR IGNORE INTO candle_heads(exchange,market,symbol,timeframe,"
            "open_time,source_id) VALUES(?,?,?,?,?,?)",
            [(r["exchange"], r["market"], r["symbol"], r["timeframe"],
              r["open_time"], r["source_id"]) for r in rows])
        dst.commit()
        rep["seeded_rows"] = len(rows)
        rep["per_tf"] = {tf: dst.execute(
            "SELECT COUNT(*) FROM candle_versions WHERE symbol=? AND timeframe=?",
            (SYMBOL, tf)).fetchone()[0] for tf in ("1h", "4h")}

        # 突破那根真实 bar（尚未导入）
        bo = src.execute(
            "SELECT * FROM candle_versions WHERE symbol=? AND timeframe='1h' "
            "AND open_time=?", (SYMBOL, BREAKOUT_OPEN_MS)).fetchone()
        rep["breakout_bar_found"] = bo is not None
    finally:
        src.close()
        dst.close()

    if not rep["breakout_bar_found"]:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 2

    # ── 隔离环境跑引擎 ──
    os.environ["MARKET_DB"] = str(tdb)
    for m in ("live_store", "unified_runtime"):
        sys.modules.pop(m, None)
    import unified_runtime as U  # noqa: E402

    eng = U.MultiSymbolSignalEngine([SYMBOL])
    st = eng.bootstrap(upto_ms=BREAKOUT_OPEN_MS, log=lambda *a: None)
    rep["bootstrap"] = st
    state_before = (list(eng.engines[SYMBOL].sm.active_event.current_state.value)
                    if eng.engines.get(SYMBOL) and eng.engines[SYMBOL].sm.active_event
                    else "NONE")
    rep["state_before_new_bar"] = state_before

    # 追加真实的突破 bar
    con = sqlite3.connect(tdb)
    try:
        cols = [d[0] for d in con.execute(
            "SELECT * FROM candle_versions LIMIT 0").description]
        con.execute(
            f"INSERT OR IGNORE INTO candle_versions({','.join(cols)}) "
            f"VALUES({','.join('?' * len(cols))})",
            tuple(bo[c] for c in cols))
        con.execute(
            "INSERT OR IGNORE INTO candle_heads(exchange,market,symbol,timeframe,"
            "open_time,source_id) VALUES(?,?,?,?,?,?)",
            (bo["exchange"], bo["market"], bo["symbol"], bo["timeframe"],
             bo["open_time"], bo["source_id"]))
        con.commit()
    finally:
        con.close()

    got = eng.step(upto_ms=int(bo["close_time"]), log=lambda *a: None)
    rep["step_candidates"] = len(got)
    rep["candidates"] = [
        {"symbol": c.symbol, "time_ms": c.snapshot_time_ms,
         "breakout_close": c.breakout_close,
         "volume_ratio": round(c.volume_ratio, 2) if c.volume_ratio else None,
         "oi_proven": bool(c.pit_proven), "oi_note": c.pit_assumption_note}
        for c in got]
    rep["state_after"] = (eng.engines[SYMBOL].sm.active_event.current_state.value
                          if eng.engines[SYMBOL].sm.active_event else "NONE(事件已结束)")
    rep["verdict"] = ("PASS: 新 bar 触发增量推进并产出候选"
                      if len(got) == 1 else "FAIL: 新 bar 未产出候选")

    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if len(got) == 1 else 1


if __name__ == "__main__":
    sys.exit(main())

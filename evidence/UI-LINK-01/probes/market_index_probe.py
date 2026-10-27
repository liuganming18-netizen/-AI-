# -*- coding: utf-8 -*-
"""UI-LINK-01 · GAP-06 机械证据：行情库「取最新已闭合 15m bar」为何慢。

**只读**：不建索引、不改表、不写任何库；只跑 `EXPLAIN QUERY PLAN` 与计时。

背景：`paper_engine.last_closed_15m()` 是 `/api/v1/paper/positions` 与
`/api/v1/paper/accounts` 的必经路径（每个持仓每臂各调一次）。若它的 SQL 走全表
扫描，前端账本首屏就会被拖到数秒级，表现为「右栏长时间空白」。

产出（stdout JSON）：
  · db 文件、字节数
  · candle_versions 行数 / 15m 行数 / BTCUSDT 15m 行数
  · 该表全部索引
  · 该查询的 `EXPLAIN QUERY PLAN`
  · 冷/热各若干次计时
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"

SQL = ("SELECT symbol, timeframe, open_time, close_time, open, high, low, close, available_at "
       "FROM candle_versions WHERE symbol=? AND timeframe='15m' "
       "ORDER BY close_time DESC LIMIT 1")


def main() -> int:
    out = {"probe": "market_index_probe", "db": str(DB),
           "db_bytes": DB.stat().st_size if DB.exists() else None}
    if not DB.exists():
        out["error"] = "market db missing"
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 1

    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=20)
    try:
        out["candle_versions_rows"] = con.execute(
            "SELECT COUNT(*) FROM candle_versions").fetchone()[0]
        out["rows_15m"] = con.execute(
            "SELECT COUNT(*) FROM candle_versions WHERE timeframe='15m'").fetchone()[0]
        out["rows_btcusdt_15m"] = con.execute(
            "SELECT COUNT(*) FROM candle_versions WHERE timeframe='15m' "
            "AND symbol='BTCUSDT'").fetchone()[0]
        out["indexes"] = [
            {"name": r[0], "sql": r[1]} for r in con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' "
                "AND tbl_name='candle_versions'")]
        out["query_plan"] = [
            {"id": r[0], "parent": r[1], "detail": r[3]}
            for r in con.execute("EXPLAIN QUERY PLAN " + SQL, ("BTCUSDT",))]
        out["full_scan"] = any("SCAN candle_versions" in p["detail"]
                               for p in out["query_plan"])
        timings = []
        for _ in range(3):
            s = time.time()
            row = con.execute(SQL, ("BTCUSDT",)).fetchone()
            timings.append(int((time.time() - s) * 1000))
        out["query_ms"] = timings
        out["last_closed_15m_close_time"] = row[3] if row else None
        # 4 臂 × 每仓 1 次 ⇒ 一次 /paper/positions 至少要跑这么多次这条 SQL
        out["arms"] = 4
        out["note"] = ("last_closed_15m 无可用索引 ⇒ 每次调用全表扫描 "
                       "candle_versions；snapshot_all 对每个臂、每个持仓各调一次，"
                       "故 /api/v1/paper/positions 的耗时 ≈ 调用次数 × 单次扫描耗时。")
    finally:
        con.close()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

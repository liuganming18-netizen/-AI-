#!/usr/bin/env python3
"""Q9 · 可得时间（PIT）字段填充率（全量）

结论：冷档 candles 的 available_at 0/114,466,846；冷档 oi 的 fetched_at 0/128,566,270；
      冷档 funding **根本没有 fetched_at 列**（只有 5 列）；冷档 metrics **没有任何采集/发布时刻列**；
      热库 candle_versions.available_at 50.16%、oi_history.fetched_at 44.88%（池内 42.98%）、
      funding_history.fetched_at 100%。
      另：热库 available_at - close_time 最小 12 ms、最大 75.8 天
      ⇒ available_at 是**本地入库时刻**，不是交易所发布时刻。
统计时点（本单那次运行）：2026-10-26T03:02:07Z

用法：
  /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python \
    evidence/DATA-AUDIT-01-R1/queries/Q9_pit_fill.py

只读：sqlite mode=ro + query_only；duckdb 只读 parquet；不写任何文件。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[3]
HOT_DB = ROOT / "数据/市场数据/db/market_v2.sqlite3"
ARCHIVE = ROOT / "数据/市场数据/parquet"


def hot() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True, timeout=30)
    c.execute("PRAGMA query_only=ON")
    return c


def fill_rate(con: duckdb.DuckDBPyConnection, glob: str, col: str, total_hint: str = "") -> None:
    """列存在则算填充率；列不存在则明确报「列不存在」，不报 0%。"""
    cols = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{glob}', union_by_name=true)").fetchall()]
    if col not in cols:
        print(f"  {col:<14} 列不存在（共 {len(cols)} 列：{','.join(cols)}）")
        return
    n, filled = con.execute(
        f"""SELECT COUNT(*), SUM(CASE WHEN {col} IS NOT NULL AND TRIM(CAST({col} AS VARCHAR))<>''
                                     THEN 1 ELSE 0 END)
            FROM read_parquet('{glob}', union_by_name=true)""").fetchone()
    pct = 100.0 * (filled or 0) / n if n else 0.0
    print(f"  {col:<14} {pct:6.2f}%  ({filled or 0:,}/{n:,})")


def main() -> None:
    con = duckdb.connect()
    print("冷档（parquet，全量）")
    for label, glob, col in [
        ("candles", str(ARCHIVE / "candles/*/*/*/*/*.parquet"), "available_at"),
        ("oi",      str(ARCHIVE / "oi/*/*/*/*.parquet"),      "fetched_at"),
        ("funding", str(ARCHIVE / "funding/*/*/*.parquet"),   "fetched_at"),
        ("metrics", str(ARCHIVE / "metrics/*/*/*/*.parquet"), "fetched_at"),
    ]:
        print(f" [{label}]")
        fill_rate(con, glob, col)

    print()
    print("热库（sqlite，全量）")
    c = hot()
    for table, col in [("candle_versions", "available_at"), ("oi_history", "fetched_at"),
                       ("funding_history", "fetched_at"), ("realtime_quotes", "fetched_at")]:
        try:
            n, f = c.execute(f"SELECT COUNT(*), SUM(CASE WHEN {col} IS NOT NULL THEN 1 ELSE 0 END) "
                             f"FROM {table}").fetchone()
        except sqlite3.OperationalError as e:
            print(f"  {table}.{col:<14} 查询失败：{e}")
            continue
        print(f"  {table}.{col:<14} {100.0*(f or 0)/n if n else 0:6.2f}%  ({f or 0:,}/{n:,})")

    print()
    print("热库 available_at - close_time（证明它是入库时刻，不是发布时刻）")
    # available_at / close_time 都是**整数毫秒**，直接相减；不能用 strftime
    row = c.execute("""
        SELECT MIN(available_at - close_time), MAX(available_at - close_time),
               AVG(available_at - close_time), COUNT(*)
        FROM candle_versions
        WHERE available_at IS NOT NULL AND close_time IS NOT NULL
          AND typeof(available_at) = 'integer' AND typeof(close_time) = 'integer'
        """).fetchone()
    if row and row[0] is not None:
        print(f"  样本 {row[3]:,} 行；最小 {row[0]/1000:.3f} 秒 / "
              f"最大 {row[1]/86400000:.1f} 天 / 均值 {row[2]/3600000:.1f} 小时")


if __name__ == "__main__":
    main()

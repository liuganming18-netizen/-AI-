#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库查询性能：基线 / 加索引 / 复测（全部可机械复算）。

为什么需要这个脚本而不是「我测了一下变快了」：
报告里的每个毫秒数都必须能回到「哪条 SQL、在什么 PRAGMA 下、什么时刻」。
所以本脚本把 SQL 原文、PRAGMA、耗时、索引清单、库文件大小全部落到 JSON。

用法：
  python 20_db_perf.py baseline     # 只测，不改库
  python 20_db_perf.py add-index    # 加索引（additive，可用 drop-index 回滚）
  python 20_db_perf.py drop-index   # 回滚
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "数据/市场数据/db/market_v2.sqlite3"
OUT = ROOT / "evidence/DB-PERF-01/out"
OUT.mkdir(parents=True, exist_ok=True)

# 索引定义：只加「能改变执行计划」的，不加「看起来有用」的。
INDEXES = [
    # funding_history 主键是 (exchange, market, symbol, funding_time)，
    # `GROUP BY symbol` 跨所合并时无法走主键有序前缀 ⇒ 必须建临时 B 树。
    # 单独以 symbol 打头建索引后，GROUP BY 变成索引顺序扫描，且
    # `max(funding_time) per symbol` 落在索引内，无需回表。
    ("ix_fund_hist_symbol_time",
     "CREATE INDEX IF NOT EXISTS ix_fund_hist_symbol_time "
     "ON funding_history(symbol, funding_time)"),
    # oi_history 的 `count(DISTINCT symbol)` 需要扫全表或全索引。
    # 现有 ix_oi_hist_sym_period_ts 前缀是 (exchange, symbol, period, ts)，
    # 扫它要把 period/ts 也读进来；只含 symbol 的窄索引扫起来更省。
    ("ix_oi_hist_symbol",
     "CREATE INDEX IF NOT EXISTS ix_oi_hist_symbol ON oi_history(symbol)"),
]

QUERIES = [
    ("oi_agg",
     "SELECT count(distinct symbol), count(*), max(ts) FROM oi_history", ()),
    ("funding_agg",
     "SELECT count(distinct symbol), count(*), max(funding_time) FROM funding_history", ()),
    ("funding_stale_groupby",
     "SELECT symbol FROM (SELECT symbol, max(funding_time) as max_ft "
     "FROM funding_history GROUP BY symbol) WHERE max_ft < ?",
     (1,)),
    ("coverage_by_tf",
     "SELECT count(*), sum(rows), sum(missing), min(first_open), max(last_close) "
     "FROM coverage_cache WHERE timeframe=?", ("15m",)),
]

PRAGMAS_DEFAULT: list[str] = []
PRAGMAS_TUNED = ["PRAGMA cache_size = -262144", "PRAGMA temp_store = MEMORY"]


def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def connect(pragmas: list[str]) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=120)
    con.execute("PRAGMA query_only = ON")
    for p in pragmas:
        con.execute(p)
    return con


def measure(pragmas: list[str], repeats: int = 3) -> dict:
    """同一连接内每条查询跑 repeats 次，取**中位数**。

    取中位数而不是最小值：最小值只反映「缓存最热那一次」，会系统性高估
    优化效果；中位数更接近用户实际会遇到的情况。
    """
    con = connect(pragmas)
    cur = con.cursor()
    res = {}
    for name, sql, params in QUERIES:
        times = []
        value = None
        for _ in range(repeats):
            t0 = time.perf_counter()
            value = cur.execute(sql, params).fetchone()
            times.append((time.perf_counter() - t0) * 1000.0)
        times.sort()
        res[name] = {
            "sql": " ".join(sql.split()),
            "median_ms": round(times[len(times) // 2], 2),
            "min_ms": round(times[0], 2),
            "max_ms": round(times[-1], 2),
            "result_head": list(value)[:3] if value else None,
        }
    # 执行计划：证明优化是「计划变了」而不是「碰巧快了」
    plans = {}
    for name, sql, params in QUERIES:
        try:
            plans[name] = [r[3] for r in cur.execute("EXPLAIN QUERY PLAN " + sql, params)]
        except sqlite3.Error as exc:
            plans[name] = [f"ERR {exc}"]
    con.close()
    return {"pragmas": pragmas, "queries": res, "query_plan": plans,
            "total_median_ms": round(sum(v["median_ms"] for v in res.values()), 2)}


def index_state() -> dict:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    rows = con.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master "
        "WHERE type='index' ORDER BY tbl_name, name").fetchall()
    con.close()
    return {f"{t}.{n}": (s or "(自动索引)") for n, t, s in rows}


def db_sizes() -> dict:
    out = {}
    for f in ["market_v2.sqlite3", "market_v2.sqlite3-wal", "market_v2.sqlite3-shm"]:
        p = DB.parent / f
        out[f] = round(os.path.getsize(p) / 1e6, 1) if p.exists() else None
    return out


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    snap = {
        "mode": mode,
        "captured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "db": str(DB.relative_to(ROOT)),
        "db_sizes_mb": db_sizes(),
        "indexes_before": index_state(),
    }

    if mode in ("baseline", "after", "add-index"):
        snap["measure_default_pragmas"] = measure(PRAGMAS_DEFAULT)
        snap["measure_tuned_pragmas"] = measure(PRAGMAS_TUNED)

    if mode in ("add-index", "drop-index"):
        con = sqlite3.connect(DB, timeout=300)
        con.execute("PRAGMA busy_timeout = 300000")
        done = []
        try:
            for name, ddl in INDEXES:
                t0 = time.perf_counter()
                if mode == "add-index":
                    con.execute(ddl)
                    con.commit()
                else:
                    con.execute(f"DROP INDEX IF EXISTS {name}")
                    con.commit()
                done.append({"index": name,
                             "elapsed_s": round(time.perf_counter() - t0, 2),
                             "action": mode})
                print(f"[{mode}] {name} 完成 {done[-1]['elapsed_s']}s", flush=True)
        finally:
            con.close()
        snap["index_ops"] = done
        snap["db_sizes_mb_after"] = db_sizes()
        snap["indexes_after"] = index_state()
        if mode == "add-index":
            # 加完索引再测一次，证明「计划真的变了」
            snap["measure_default_pragmas"] = measure(PRAGMAS_DEFAULT)
            snap["measure_tuned_pragmas"] = measure(PRAGMAS_TUNED)

    snap["db_sha256"] = None  # 11GB 逐字节哈希太慢，改用「大小 + 索引清单 + 时刻」定位
    name = {"baseline": "perf_baseline.json", "add-index": "perf_after_index.json",
            "after": "perf_after.json", "drop-index": "perf_after_drop.json"}[mode]
    (OUT / name).write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "mode": mode,
        "default_total_ms": snap.get("measure_default_pragmas", {}).get("total_median_ms"),
        "tuned_total_ms": snap.get("measure_tuned_pragmas", {}).get("total_median_ms"),
        "saved_to": str(OUT / name),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

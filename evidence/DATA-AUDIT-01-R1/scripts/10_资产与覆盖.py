"""10 · 资产与覆盖（全量口径）

工单整改要求 3：为「持仓量覆盖 / 新鲜度 / 大户指标停更」提供**可复算的只读查询、完整结果、统计时点**。
本脚本一次跑出：

  out/10_hot_inventory.json     热库逐表行数/标的数/时间跨度（全表 COUNT，不抽样）
  out/11_oi_5m_per_symbol.csv   持仓量 5m 逐币覆盖与新鲜度（池内 526 全量 + 池外对照）
  out/12_oi_freshness.json      新鲜度分布（1h/6h/24h 分桶，全量）
  out/13_metrics_per_symbol.csv 大户/多空比冷档逐币 max(ts)（526 全量，duckdb 读 parquet footer）
  out/14_metrics_staleness.json 停更天数分布（全量）
  out/15_cold_inventory.json    冷档逐类型文件数/字节（磁盘枚举，全量）
  out/16_funding_hot_per_symbol.csv 资金费热库逐币覆盖（全量）

运行：python evidence/DATA-AUDIT-01-R1/scripts/10_资产与覆盖.py
只读：sqlite URI mode=ro；duckdb read_only；不写任何业务目录。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (ARCHIVE, HOT_DB, OUT, dump, hot, now_ms, now_utc, pool,
                     pct, quantiles, utc, write_csv)

T0 = time.time()
NOW = now_ms()
STATS_AT = now_utc()
P = pool()
POOL_SET = set(P["symbols"])


def log(msg: str) -> None:
    print(f"[{time.time()-T0:7.1f}s] {msg}", flush=True)


# ---------------------------------------------------------------- 热库逐表
TIME_COL = {
    "candle_heads": "open_time",
    "candle_versions": "open_time",
    "funding_history": "funding_time",
    "oi_history": "ts",
    "realtime_klines": "open_time",
    "realtime_quotes": "event_time",
    "sources": "imported_at",
    "collector_rounds": "started_at",
    "coverage_cache": None,
}

con = hot()
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")]
log(f"热库对象 {len(tables)} 个")

inv = {"stats_at_utc": STATS_AT, "db": str(HOT_DB.relative_to(HOT_DB.parents[3])),
       "db_bytes": os.path.getsize(HOT_DB), "objects": len(tables), "tables": {}}

for t in tables:
    rec = {"rows": None, "symbols": None, "min_time": None, "max_time": None, "note": None}
    try:
        rec["rows"] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except Exception as exc:                                   # noqa: BLE001
        rec["note"] = f"COUNT 失败：{type(exc).__name__}"
        inv["tables"][t] = rec
        continue
    try:
        rec["symbols"] = con.execute(f"SELECT COUNT(DISTINCT symbol) FROM {t}").fetchone()[0]
    except Exception:
        rec["symbols"] = None
    tc = TIME_COL.get(t)
    if tc:
        try:
            mn, mx = con.execute(f"SELECT MIN({tc}), MAX({tc}) FROM {t}").fetchone()
            rec["min_time"], rec["max_time"] = utc(mn), utc(mx)
        except Exception:
            rec["note"] = "时间列不可读"
    inv["tables"][t] = rec
dump("10_hot_inventory.json", inv)
log("热库逐表盘点完成")

# ------------------------------------------------- 持仓量 5m 逐币（全量）
rows = con.execute("""
    SELECT symbol,
           COUNT(*)                     AS n,
           MIN(ts)                      AS min_ts,
           MAX(ts)                      AS max_ts,
           SUM(CASE WHEN fetched_at IS NOT NULL THEN 1 ELSE 0 END) AS fetched_n
    FROM oi_history
    WHERE period = '5m'
    GROUP BY symbol
""").fetchall()

oi_rows = []
fresh_sec = []
for sym, n, mn, mx, fn in rows:
    age = (NOW - mx) / 1000.0 if mx else None
    oi_rows.append([sym, n, utc(mn), utc(mx), None if age is None else round(age, 1),
                    fn, pct(fn, n), 1 if sym in POOL_SET else 0])
    if sym in POOL_SET and age is not None:
        fresh_sec.append(age)

oi_rows.sort(key=lambda r: (r[7] * -1, r[0]))
write_csv("11_oi_5m_per_symbol.csv",
          ["symbol", "rows_5m", "min_ts_utc", "max_ts_utc", "last_age_sec",
           "fetched_at_filled", "fetched_at_pct", "in_approved_pool"], oi_rows)

def bucket(sec: float) -> str:
    for lim, name in ((3600, "<=1h"), (6 * 3600, "1h~6h"), (24 * 3600, "6h~24h"),
                      (72 * 3600, "1d~3d")):
        if sec <= lim:
            return name
    return ">3d"

from collections import Counter
buckets = Counter(bucket(s) for s in fresh_sec)
oi_cov = {
    "stats_at_utc": STATS_AT,
    "population": "全量（热库 oi_history 全表 GROUP BY symbol，无抽样）",
    "query": "SELECT symbol,COUNT(*),MIN(ts),MAX(ts),SUM(fetched_at IS NOT NULL) "
             "FROM oi_history WHERE period='5m' GROUP BY symbol",
    "approved_pool": P["n"],
    "symbols_with_5m": len(rows),
    "pool_symbols_with_5m": sum(1 for r in rows if r[0] in POOL_SET),
    "pool_symbols_without_5m": sorted(POOL_SET - {r[0] for r in rows}),
    "freshness_sec_pool": quantiles(fresh_sec),
    "freshness_buckets_pool": dict(buckets),
    "fresh_le_1h": sum(1 for s in fresh_sec if s <= 3600),
    "fresh_le_1h_pct": pct(sum(1 for s in fresh_sec if s <= 3600), len(fresh_sec)),
    "total_rows_5m": sum(r[1] for r in rows),
    "min_ts_all": utc(min(r[2] for r in rows)),
    "max_ts_all": utc(max(r[3] for r in rows)),
}
dump("12_oi_freshness.json", oi_cov)
log(f"OI 5m 逐币完成：{len(rows)} 币，池内 {oi_cov['pool_symbols_with_5m']}，"
    f"1 小时内 {oi_cov['fresh_le_1h']}")

# ------------------------------------------------- 持仓量快照（池内口径）
snap = con.execute("""
    SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(ts), MAX(ts)
    FROM oi_history WHERE period <> '5m'
""").fetchone()
snap_pool = con.execute("""
    SELECT COUNT(DISTINCT symbol) FROM oi_history WHERE period <> '5m'
""").fetchone()[0]
dump("18_oi_snapshot.json", {
    "stats_at_utc": STATS_AT,
    "population": "全量（oi_history 中 period<>'5m' 的全部行）",
    "query": "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(ts), MAX(ts) "
             "FROM oi_history WHERE period <> '5m'",
    "rows": snap[0], "symbols": snap[1], "symbols_pool_only": snap_pool,
    "min_ts_utc": utc(snap[2]), "max_ts_utc": utc(snap[3]),
    "cold_archived": False,
    "note": "快照只在热层；冷档 oi 只有 5m 序列 ⇒ 快照没有长历史",
})
log(f"OI 快照 {snap[0]} 行 / {snap[1]} 币")

# ------------------------------------------------- 资金费热库逐币（全量）
fr = con.execute("""
    SELECT symbol, COUNT(*), MIN(funding_time), MAX(funding_time),
           SUM(CASE WHEN fetched_at IS NOT NULL THEN 1 ELSE 0 END)
    FROM funding_history GROUP BY symbol
""").fetchall()
fr_rows = []
fr_span = []
for sym, n, mn, mx, fn in fr:
    fr_rows.append([sym, n, utc(mn), utc(mx), fn, pct(fn, n),
                    round((mx - mn) / 86400000.0, 1), 1 if sym in POOL_SET else 0])
    if sym in POOL_SET and mx:
        fr_span.append((mx - mn) / 86400000.0)
fr_rows.sort(key=lambda r: (r[7] * -1, r[0]))
write_csv("16_funding_hot_per_symbol.csv",
          ["symbol", "rows", "min_funding_utc", "max_funding_utc",
           "fetched_at_filled", "fetched_at_pct", "span_days", "in_approved_pool"], fr_rows)
fund = {
    "stats_at_utc": STATS_AT,
    "population": "全量（funding_history 全表 GROUP BY symbol）",
    "rows_total": sum(r[1] for r in fr_rows),
    "symbols": len(fr_rows),
    "pool_symbols": sum(1 for r in fr_rows if r[7] == 1),
    "pool_missing": sorted(POOL_SET - {r[0] for r in fr_rows}),
    "span_days_pool": quantiles(fr_span),
    "min_funding_utc": utc(min(r[2] for r in fr)),
    "max_funding_utc": utc(max(r[3] for r in fr)),
    "rate_types": dict(con.execute(
        "SELECT rate_type, COUNT(*) FROM funding_history GROUP BY rate_type").fetchall()),
}
dump("17_funding_hot.json", fund)
con.close()
log("资金费热库逐币完成")

# ------------------------------------------------- 冷档磁盘枚举（全量）
types = ["candles", "candle_heads", "oi", "metrics", "funding", "sources"]
cold = {"stats_at_utc": STATS_AT, "root": str(ARCHIVE.relative_to(ARCHIVE.parents[2])),
        "types": {}}
for t in types:
    base = ARCHIVE / t
    if not base.exists():
        cold["types"][t] = {"exists": False}
        continue
    n_files = n_bytes = 0
    for dp, _dns, fns in os.walk(base):
        for fn in fns:
            if not fn.endswith(".parquet"):
                continue
            n_files += 1
            try:
                n_bytes += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    cold["types"][t] = {"exists": True, "parquet_files": n_files,
                        "bytes": n_bytes, "gb": round(n_bytes / 1024**3, 3)}
cold["total_parquet_files"] = sum(v.get("parquet_files", 0) for v in cold["types"].values())
cold["total_gb"] = round(sum(v.get("bytes", 0) for v in cold["types"].values()) / 1024**3, 3)
dump("15_cold_inventory.json", cold)
log(f"冷档枚举：{cold['total_parquet_files']} 个 parquet，{cold['total_gb']} GB")

# ------------------------------------------------- 大户/多空比冷档停更（全量）
import duckdb  # noqa: E402
dcon = duckdb.connect()
metrics_glob = str(ARCHIVE / "metrics/**/*.parquet")
log("开始 duckdb 全量读 metrics max(ts)（1,600+ 文件，只读 footer/ts 列）…")
q = f"""
    SELECT symbol, COUNT(*) AS n, MIN(ts) AS min_ts, MAX(ts) AS max_ts
    FROM read_parquet('{metrics_glob}', union_by_name = true)
    GROUP BY symbol
"""
mrows = dcon.execute(q).fetchall()
dcon.close()
log(f"metrics 逐币完成：{len(mrows)} 币")

m_rows = []
m_age = []
for sym, n, mn, mx in mrows:
    age_d = (NOW - mx) / 86400000.0 if mx else None
    m_rows.append([sym, n, utc(mn), utc(mx), None if age_d is None else round(age_d, 2),
                   1 if sym in POOL_SET else 0])
    if sym in POOL_SET and age_d is not None:
        m_age.append(age_d)
m_rows.sort(key=lambda r: (r[5] * -1, r[0]))
write_csv("13_metrics_per_symbol.csv",
          ["symbol", "rows", "min_ts_utc", "max_ts_utc", "stale_days", "in_approved_pool"],
          m_rows)

m_agg = {
    "stats_at_utc": STATS_AT,
    "population": "全量（read_parquet 全目录 GROUP BY symbol，无抽样）",
    "query": q.strip(),
    "symbols": len(mrows),
    "pool_symbols": sum(1 for r in m_rows if r[5] == 1),
    "pool_missing": sorted(POOL_SET - {r[0] for r in mrows}),
    "rows_total": sum(r[1] for r in mrows),
    "max_ts_all": utc(max(r[3] for r in mrows)),
    "min_ts_all": utc(min(r[2] for r in mrows)),
    "stale_days_pool": quantiles(m_age),
    "pool_stale_gt_1d": sum(1 for d in m_age if d > 1),
    "pool_stale_gt_7d": sum(1 for d in m_age if d > 7),
    "pool_stale_gt_30d": sum(1 for d in m_age if d > 30),
    "max_ts_by_symbol_hist": dict(
        __import__("collections").Counter(utc(r[3])[:10] for r in mrows).most_common(10)),
}
dump("14_metrics_staleness.json", m_agg)
log(f"metrics 停更：全量 max(ts)={m_agg['max_ts_all']}，"
    f"池内 >7 天 {m_agg['pool_stale_gt_7d']}/{len(m_age)}")

print(f"\n完成，用时 {time.time()-T0:.1f}s；统计时点 {STATS_AT}")
print(f"产物目录：{OUT}")

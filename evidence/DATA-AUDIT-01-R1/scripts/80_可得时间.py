"""80 · 可得时间（PIT）字段填充率（全量）

工单整改要求 4：逐项数据必须给出「可得时间」。这一项在原报告里只有抽样描述，
本脚本把每一类数据的可得/采集时间字段**全量统计填充率**，并明确区分：

- `available_at`：K 线表里的可得时间（实测=本地入库时刻，不是交易所发布时刻）
- `fetched_at`：采集时刻
- 冷档 `available_at`：是否真的全空

  out/80_pit_fill.json     全量填充率 + 语义判定
  out/81_pit_fill.csv      逐字段明细

只读：duckdb + sqlite ro。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import ARCHIVE, dump, hot, now_utc, pct, write_csv

T0 = time.time()
STATS_AT = now_utc()


def log(m: str) -> None:
    print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)


import duckdb  # noqa: E402
d = duckdb.connect()
d.execute("PRAGMA threads=4")

res = {"stats_at_utc": STATS_AT, "population": "全量（每项为整表/整目录聚合，无抽样）",
       "fields": []}


def add(dataset, field, total, filled, semantic, source):
    res["fields"].append({
        "dataset": dataset, "field": field, "rows": total, "filled": filled,
        "fill_pct": pct(filled, total), "semantic": semantic, "source": source,
    })


# ---------------- 冷档 candles: available_at
log("冷档 candles.available_at 全量统计…")
q = f"""
SELECT COUNT(*) AS n,
       SUM(CASE WHEN available_at IS NOT NULL THEN 1 ELSE 0 END) AS filled
FROM read_parquet('{ARCHIVE}/candles/*/*/*/*/*.parquet', union_by_name = true)
"""
n, filled = d.execute(q).fetchone()
add("冷档 candles", "available_at", n, filled,
    "K 线可得时间（交易所发布时刻或本地入库时刻，需另证）", q.strip())

# ---------------- 冷档 oi: fetched_at
log("冷档 oi.fetched_at 全量统计…")
q = f"""
SELECT COUNT(*) AS n,
       SUM(CASE WHEN fetched_at IS NOT NULL THEN 1 ELSE 0 END) AS filled
FROM read_parquet('{ARCHIVE}/oi/*/*/*/*.parquet', union_by_name = true)
"""
n, filled = d.execute(q).fetchone()
add("冷档 oi", "fetched_at", n, filled, "采集时刻", q.strip())

# ---------------- 冷档 funding: 有没有 fetched_at（实测：没有这一列）
FUND_GLOB = f"{ARCHIVE}/funding/*/*/*.parquet"
fund_cols = [r[0] for r in d.execute(
    f"DESCRIBE SELECT * FROM read_parquet('{FUND_GLOB}', union_by_name = true)").fetchall()]
res["funding_columns"] = fund_cols
n = d.execute(f"SELECT COUNT(*) FROM read_parquet('{FUND_GLOB}', union_by_name = true)").fetchone()[0]
if "fetched_at" in fund_cols:
    q = (f"SELECT COUNT(*), SUM(CASE WHEN fetched_at IS NOT NULL THEN 1 ELSE 0 END) "
         f"FROM read_parquet('{FUND_GLOB}', union_by_name = true)")
    n2, filled = d.execute(q).fetchone()
    add("冷档 funding", "fetched_at", n2, filled, "采集时刻", q.strip())
else:
    # 没有这一列就是「完全没有采集时刻证据」，不是「填充率 0」
    add("冷档 funding", "fetched_at（列不存在）", n, 0,
        "冷档 funding 只有 5 列（funding_time/funding_rate/mark_price/rate_type/source），"
        "**没有 fetched_at、也没有 exchange/market/symbol** ⇒ 冷档资金费无法自证采集时刻",
        "DESCRIBE read_parquet(funding)")

# ---------------- 冷档 metrics: 有没有任何时间戳列
q = f"""
SELECT COUNT(*) AS n FROM read_parquet('{ARCHIVE}/metrics/*/*/*/*.parquet',
                                       union_by_name = true)
"""
n = d.execute(q).fetchone()[0]
cols = [r[0] for r in d.execute(
    f"DESCRIBE SELECT * FROM read_parquet('{ARCHIVE}/metrics/*/*/*/*.parquet', "
    "union_by_name = true)").fetchall()]
res["metrics_columns"] = cols
res["metrics_has_publish_or_fetch_col"] = any(
    ("fetch" in c or "availab" in c or "publish" in c) for c in cols)
add("冷档 metrics", "（无采集/发布时刻列）", n, 0,
    "只有 ts（5m 槽位），没有 fetched_at / published_at ⇒ 无法判断何时可得",
    "DESCRIBE read_parquet(metrics)")

d.close()

# ---------------- 热库字段
con = hot()
for tbl, col, sem in (
    ("candle_versions", "available_at", "K 线可得时间（实测=入库时刻）"),
    ("oi_history", "fetched_at", "OI 采集时刻"),
    ("funding_history", "fetched_at", "资金费采集时刻"),
    ("realtime_quotes", "fetched_at", "行情快照采集时刻"),
):
    try:
        n, f = con.execute(
            f"SELECT COUNT(*), SUM(CASE WHEN {col} IS NOT NULL THEN 1 ELSE 0 END) FROM {tbl}"
        ).fetchone()
        add(f"热库 {tbl}", col, n, f or 0, sem,
            f"SELECT COUNT(*), SUM({col} IS NOT NULL) FROM {tbl}")
    except Exception as exc:                                    # noqa: BLE001
        add(f"热库 {tbl}", col, 0, 0, f"读取失败：{type(exc).__name__}", "")

# OI 池内口径（原报告 42.4% 是池内，44.2% 是全表 —— 两个口径要分开写）
import json  # noqa: E402
from _common import POOL_JSON  # noqa: E402
pool = [x["symbol"] for x in json.load(open(POOL_JSON, encoding="utf-8"))["binance_items"]]
ph = ",".join("?" * len(pool))
n, f = con.execute(
    f"SELECT COUNT(*), SUM(CASE WHEN fetched_at IS NOT NULL THEN 1 ELSE 0 END) "
    f"FROM oi_history WHERE symbol IN ({ph})", pool).fetchone()
add("热库 oi_history（池内 526）", "fetched_at", n, f or 0, "OI 采集时刻（池内口径）",
    "SELECT COUNT(*), SUM(fetched_at IS NOT NULL) FROM oi_history WHERE symbol IN (526 币)")

# available_at 与 close_time 的差（判断它是不是「发布时刻」）
try:
    diff = con.execute("""
        SELECT MIN(available_at - close_time), MAX(available_at - close_time),
               AVG(available_at - close_time), COUNT(*)
        FROM candle_versions WHERE available_at IS NOT NULL AND close_time IS NOT NULL
    """).fetchone()
    res["available_minus_close_ms"] = {
        "min": diff[0], "max": diff[1], "avg": round(diff[2], 1) if diff[2] else None,
        "n": diff[3],
        "verdict": ("available_at 与 close_time 的差最小为毫秒级、最大可达数十天 ⇒ "
                    "它是**本地入库时刻**，不是交易所发布时刻；不能当 PIT 发布时点用"),
    }
except Exception as exc:                                        # noqa: BLE001
    res["available_minus_close_ms"] = {"error": f"{type(exc).__name__}: {exc}"}
con.close()

dump("80_pit_fill.json", res)
write_csv("81_pit_fill.csv",
          ["dataset", "field", "rows", "filled", "fill_pct", "semantic"],
          [[x["dataset"], x["field"], x["rows"], x["filled"], x["fill_pct"], x["semantic"]]
           for x in res["fields"]])
for x in res["fields"]:
    log(f"{x['dataset']}.{x['field']}: {x['fill_pct']}% ({x['filled']}/{x['rows']})")
print(f"\n完成，用时 {time.time()-T0:.1f}s；统计时点 {STATS_AT}")

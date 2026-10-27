"""50 · 抽样发现 vs 全量结论（把原报告的抽样项升级为全量复核）

工单整改要求 3：**抽样发现与全量结论必须分开**。
原报告 `out/13_异常抽样.csv` 只抽了 10 个币，结论却按全池口吻写。本脚本把其中
4 条可全量化的重新跑成全量，并给出「抽样结论是否被全量推翻」的判定。

  out/50_alias_full.json       metrics 两组字段是否纯别名（全量 1.28 亿行比较）
  out/51_dup_15m_full.json     15m 重复 open_time（全量逐币）
  out/52_dup_oi_full.json      持仓量重复 ts（全量逐币，含 BTC 2020–2021 专项）
  out/53_zero_oi_full.json     零持仓观测（全量逐币）

只读：duckdb 读 parquet，不写数据目录。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import ARCHIVE, dump, now_utc, quantiles, write_csv

T0 = time.time()
STATS_AT = now_utc()
METRICS = f"{ARCHIVE}/metrics/*/*/*/*.parquet"


def log(m: str) -> None:
    print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)


import duckdb  # noqa: E402
d = duckdb.connect()
d.execute("PRAGMA threads=4")

# ------------------------------------------------ A6 别名（全量）
q_alias = f"""
SELECT COUNT(*) AS n,
       SUM(CASE WHEN retail_account_ls_ratio IS DISTINCT FROM count_long_short_ratio
                THEN 1 ELSE 0 END) AS diff_retail,
       SUM(CASE WHEN top_position_ls_ratio IS DISTINCT FROM sum_toptrader_long_short_ratio
                THEN 1 ELSE 0 END) AS diff_top
FROM read_parquet('{METRICS}', union_by_name = true)
"""
log("全量比较 metrics 两组字段（1.28 亿行）…")
n, d1, d2 = d.execute(q_alias).fetchone()
alias = {
    "stats_at_utc": STATS_AT,
    "population": "全量（read_parquet 全目录，逐行比较，无抽样）",
    "query": q_alias.strip(),
    "rows": n,
    "pair_retail_vs_count_ls": {"differ_rows": d1,
                                "same": d1 == 0,
                                "note": "retail_account_ls_ratio 与 count_long_short_ratio"},
    "pair_top_pos_vs_sum_toptrader": {"differ_rows": d2,
                                      "same": d2 == 0,
                                      "note": "top_position_ls_ratio 与 sum_toptrader_long_short_ratio"},
    "old_sample_claim": "原报告抽样 10/10 币两组字段全部相同 ⇒ 判为「纯别名」",
    "upgraded": ("全量成立" if (d1 == 0 and d2 == 0)
                 else f"全量不成立：分别有 {d1} / {d2} 行不同 ⇒ 只能称「高度重合」，不能称纯别名"),
}
dump("50_alias_full.json", alias)
log(f"别名全量：{n} 行，差异 {d1} / {d2}")

# ------------------------------------------------ A5 持仓量重复 ts（全量）
q_oi = f"""
SELECT symbol, COUNT(*) AS n, COUNT(DISTINCT ts) AS n_uniq
FROM read_parquet('{ARCHIVE}/oi/*/*/*/*.parquet', union_by_name = true)
GROUP BY symbol
"""
log("全量统计持仓量重复 ts（逐币）…")
oi_dup = d.execute(q_oi).fetchall()
rows = []
for sym, n_rows, n_uniq in oi_dup:
    rows.append([sym, n_rows, n_uniq, n_rows - n_uniq,
                 round((n_rows - n_uniq) * 100.0 / n_rows, 3) if n_rows else 0])
rows.sort(key=lambda r: -r[3])
write_csv("54_dup_oi_per_symbol.csv",
          ["symbol", "rows", "distinct_ts", "dup_rows", "dup_pct"], rows)
oi_sum = {
    "stats_at_utc": STATS_AT,
    "population": "全量（oi 冷档全目录 GROUP BY symbol）",
    "query": q_oi.strip(),
    "symbols": len(rows),
    "rows_total": sum(r[1] for r in rows),
    "dup_rows_total": sum(r[3] for r in rows),
    "symbols_with_dup": sum(1 for r in rows if r[3] > 0),
    "top10": rows[:10],
}
dump("52_dup_oi_full.json", oi_sum)
log(f"OI 重复：全量 {oi_sum['dup_rows_total']} 行，涉及 {oi_sum['symbols_with_dup']} 币")

# BTC 2020–2021 专项（原报告称 75,255 行重复、2020 年 100% 重复）
q_btc = f"""
SELECT CAST(ts / 31536000000 AS BIGINT) AS yr_proxy,
       COUNT(*) AS n, COUNT(DISTINCT ts) AS n_uniq
FROM read_parquet('{ARCHIVE}/oi/binance/um/BTCUSDT/*.parquet', union_by_name = true)
GROUP BY 1 ORDER BY 1
"""
btc = d.execute(q_btc).fetchall()
btc_detail = [{"year_proxy": int(y), "rows": int(n), "distinct_ts": int(u),
               "dup": int(n - u)} for y, n, u in btc]
dump("55_dup_oi_btc_by_year.json", {
    "stats_at_utc": STATS_AT,
    "population": "全量（BTCUSDT 冷档 oi 全部分片）",
    "note": "year_proxy = ts/365d 的整数部分，仅用于分桶展示，不是日历年份",
    "rows": btc_detail,
})

# ------------------------------------------------ A4 15m 重复（全量，区分 live 分片）
q_15 = f"""
SELECT symbol, COUNT(*) AS n, COUNT(DISTINCT open_time) AS n_uniq
FROM read_parquet('{ARCHIVE}/candles/*/*/*/15m/*.parquet', union_by_name = true)
GROUP BY symbol
"""
log("全量统计 15m 重复 open_time（逐币，含 live 分片）…")
c15 = d.execute(q_15).fetchall()
rows15 = [[s, n, u, n - u, round((n - u) * 100.0 / n, 4) if n else 0] for s, n, u in c15]
rows15.sort(key=lambda r: -r[3])
write_csv("56_dup_15m_per_symbol.csv",
          ["symbol", "rows", "distinct_open_time", "dup_rows", "dup_pct"], rows15)
c15_sum = {
    "stats_at_utc": STATS_AT,
    "population": "全量（candles 冷档 15m 全目录 GROUP BY symbol，含 live.parquet 滚动分片）",
    "query": q_15.strip(),
    "symbols": len(rows15),
    "rows_total": sum(r[1] for r in rows15),
    "dup_rows_total": sum(r[3] for r in rows15),
    "symbols_with_dup": sum(1 for r in rows15 if r[3] > 0),
    "dup_pct_dist": quantiles([r[4] for r in rows15]),
    "top10": rows15[:10],
}
dump("51_dup_15m_full.json", c15_sum)
log(f"15m 重复：全量 {c15_sum['dup_rows_total']} 行，涉及 {c15_sum['symbols_with_dup']} 币")

# ------------------------------------------------ A7 零持仓（全量）
q_zero = f"""
SELECT COUNT(*) AS n,
       SUM(CASE WHEN TRY_CAST(open_interest AS DOUBLE) = 0 THEN 1 ELSE 0 END) AS zero_oi,
       SUM(CASE WHEN open_interest IS NULL OR TRIM(open_interest) = '' THEN 1 ELSE 0 END) AS null_oi
FROM read_parquet('{ARCHIVE}/oi/*/*/*/*.parquet', union_by_name = true)
"""
log("全量统计零持仓观测…")
zn, z0, znull = d.execute(q_zero).fetchone()
q_zero_m = f"""
SELECT COUNT(*) AS n,
       SUM(CASE WHEN sum_open_interest = 0 THEN 1 ELSE 0 END) AS zero_oi
FROM read_parquet('{METRICS}', union_by_name = true)
"""
mn, m0 = d.execute(q_zero_m).fetchone()
dump("53_zero_oi_full.json", {
    "stats_at_utc": STATS_AT,
    "population": "全量（oi 与 metrics 两个数据集全目录聚合，无抽样）",
    "query_oi": q_zero.strip(),
    "query_metrics": q_zero_m.strip(),
    "oi": {"rows": zn, "zero": z0, "null": znull,
           "zero_pct": round(z0 * 100.0 / zn, 4) if zn else None},
    "metrics": {"rows": mn, "zero": m0,
                "zero_pct": round(m0 * 100.0 / mn, 4) if mn else None},
    "old_sample_claim": "原报告只给 BTC 473 行、CVC 709 行（抽样）",
})
log(f"零持仓：oi {z0}/{zn}，metrics {m0}/{mn}")

d.close()
print(f"\n完成，用时 {time.time()-T0:.1f}s；统计时点 {STATS_AT}")

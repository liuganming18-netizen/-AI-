# -*- coding: utf-8 -*-
"""DATA-AUDIT-01 / 02：逐币与按组合的历史可用区间（只读）。

为什么不能只给一个全局交集：
  · 价格最早 2019-12-31，但多数币上线更晚；
  · OI 冷档最早 2020-09-01，热库 5m 只有 2026-09-12 起；
  · metrics 只有 526 个币、2020-09-01 起；
  · funding 最早 2020-01-01、部分币 2022 年大段空缺。
  一个全局交集会把「本来能回测 3 年的币」压成「只能回测 40 天」。

产出：
  out/10_逐币可用区间.csv        526 币 × 各数据集区间 + 5 种组合区间
  out/11_组合区间统计.csv        每种组合的天数分布（min/p25/中位/p75/max/合计）
  out/12_区间口径说明.csv        每个区间的时间口径（事件时间/可得时间）
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HOT = ROOT / "数据/市场数据/db/market_v2.sqlite3"
PARQ = ROOT / "数据/市场数据/parquet"
OUT = ROOT / "evidence/DATA-AUDIT-01/out"
DAY = 86400000


def iso(ms):
    if not ms:
        return ""
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d")


def span(path, col):
    try:
        md = pq.ParquetFile(path).metadata
    except Exception:                                            # noqa: BLE001
        return None, None
    lo = hi = None
    for rg in range(md.num_row_groups):
        for j in range(md.num_columns):
            c = md.row_group(rg).column(j)
            if c.path_in_schema != col:
                continue
            s = c.statistics
            if s is None or s.min is None:
                continue
            lo = s.min if lo is None else min(lo, s.min)
            hi = s.max if hi is None else max(hi, s.max)
    return lo, hi


def inter(*ranges):
    lo = max(r[0] for r in ranges if r and r[0] is not None)
    hi = min(r[1] for r in ranges if r and r[1] is not None)
    return (lo, hi) if hi > lo else (None, None)


def union(*ranges):
    """冷档 ∪ 热库：同一数据集的两段物理存储，取并集才是「能查到的时间范围」。"""
    rs = [r for r in ranges if r and r[0] is not None and r[1] is not None]
    if not rs:
        return (None, None)
    return (min(r[0] for r in rs), max(r[1] for r in rs))


def main():
    scope = json.loads((ROOT / "数据/contracts/旧标的池/scope_crypto.json").read_text())
    pool = sorted(i["symbol"] for i in scope["binance_items"]
                  if i.get("symbol") and i["symbol"] not in ("BTCDOMUSDT", "ALLUSDT"))

    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True, timeout=30)

    # 价格：用 coverage_cache（全库口径：冷+热合并后的覆盖结论）
    cov = {}
    for sym, tf, fo, lc, mis, exp in con.execute(
            "select symbol,timeframe,first_open,last_close,missing,expected from coverage_cache"
            " where exchange='binance' and market='um'"):
        cov.setdefault(sym, {})[tf] = (fo, lc, mis or 0, exp or 0)

    # OI：冷档 + 热库 5m（并集），并记行数用于算内部填充率
    oi_cold, oi_rows = {}, {}
    for p in PARQ.glob("oi/binance/um/*/*.parquet"):
        lo, hi = span(p, "ts")
        a = oi_cold.setdefault(p.parent.name, [None, None])
        oi_rows[p.parent.name] = oi_rows.get(p.parent.name, 0) + pq.ParquetFile(p).metadata.num_rows
        if lo is not None:
            a[0] = lo if a[0] is None else min(a[0], lo)
            a[1] = hi if a[1] is None else max(a[1], hi)
    oi_hot = {r[0]: (r[1], r[2]) for r in con.execute(
        "select symbol,min(ts),max(ts) from oi_history where period='5m' and exchange='binance' group by 1")}

    # funding：冷档 + 热库（并集）
    fu_cold, fu_rows = {}, {}
    for p in PARQ.glob("funding/binance/um/*.parquet"):
        lo, hi = span(p, "funding_time")
        sym = p.name.replace(".parquet", "")
        fu_cold[sym] = (lo, hi)
        fu_rows[sym] = pq.ParquetFile(p).metadata.num_rows
    fu_hot = {r[0]: (r[1], r[2]) for r in con.execute(
        "select symbol,min(funding_time),max(funding_time) from funding_history"
        " where exchange='binance' group by 1")}

    # metrics（只有冷档）
    mt, mt_rows = {}, {}
    for p in PARQ.glob("metrics/binance/um/*/*.parquet"):
        lo, hi = span(p, "ts")
        a = mt.setdefault(p.parent.name, [None, None])
        mt_rows[p.parent.name] = mt_rows.get(p.parent.name, 0) + pq.ParquetFile(p).metadata.num_rows
        if lo is not None:
            a[0] = lo if a[0] is None else min(a[0], lo)
            a[1] = hi if a[1] is None else max(a[1], hi)

    rows = []
    for sym in pool:
        c = cov.get(sym, {})
        p4 = c.get("4h", (None, None))[:2]
        p1 = c.get("1h", (None, None))[:2]
        p15 = c.get("15m", (None, None))[:2]
        price = inter(p4, p1, p15)
        oi = union(oi_cold.get(sym), oi_hot.get(sym))
        fu = union(fu_cold.get(sym), fu_hot.get(sym))
        me = tuple(mt.get(sym) or (None, None))
        c_po = inter(price, oi)
        c_pf = inter(price, fu)
        c_pm = inter(price, me)
        c_pof = inter(price, oi, fu)
        c_all = inter(price, oi, fu, me)

        def days(r):
            return round((r[1] - r[0]) / DAY, 1) if r[0] and r[1] else 0.0

        def fill(r, rows_n):
            """5m 数据填充率 = 实际行数 / (区间天数 × 288)。缺桶即缺口，不插值。"""
            if not (r[0] and r[1]) or not rows_n:
                return ""
            exp = (r[1] - r[0]) / 300000
            return round(rows_n / exp * 100, 1) if exp > 0 else ""

        rows.append([
            sym,
            iso(price[0]), iso(price[1]), days(price),
            iso(oi[0]), iso(oi[1]), days(oi), fill(oi, oi_rows.get(sym, 0)),
            iso(fu[0]), iso(fu[1]), days(fu),
            iso(me[0]), iso(me[1]), days(me), fill(me, mt_rows.get(sym, 0)),
            days(c_po), days(c_pf), days(c_pm), days(c_pof), days(c_all),
            c.get("4h", (0, 0, 0, 0))[2], c.get("4h", (0, 0, 0, 0))[3],
        ])

    header = ["标的",
              "价格起", "价格止", "价格天",
              "OI起", "OI止", "OI天", "OI填充率%",
              "资金费起", "资金费止", "资金费天",
              "指标起", "指标止", "指标天", "指标填充率%",
              "价格∩OI", "价格∩资金费", "价格∩指标", "价格∩OI∩资金费", "四者齐全",
              "4h缺失根数", "4h期望根数"]
    with open(OUT / "10_逐币可用区间.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)

    # 统计
    import statistics as st
    cols = {"价格": 3, "OI": 6, "资金费": 10, "指标": 13,
            "价格∩OI": 15, "价格∩资金费": 16, "价格∩指标": 17,
            "价格∩OI∩资金费": 18, "四者齐全": 19}
    stat_rows = []
    for name, idx in cols.items():
        vals = sorted(r[idx] for r in rows)
        nz = [v for v in vals if v > 0]
        stat_rows.append([name, len(vals), len(nz),
                          round(min(vals), 1), round(st.median(vals), 1),
                          round(max(vals), 1), round(sum(vals) / 365.25, 1)])
    with open(OUT / "11_组合区间统计.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["组合", "币数", "有区间的币数", "最短天", "中位天", "最长天", "合计年"])
        w.writerows(stat_rows)

    # 口径说明
    note = [
        ["价格", "candle_heads/coverage_cache", "事件时间 open_time 起 → close_time 止",
         "热库近 30~45 天 + 冷档 2019-12-31 起；冷档归档滞后热库约 7 天"],
        ["OI 5m", "parquet/oi + oi_history(period=5m)", "观测时刻 ts",
         "冷档 2020-09-01 起；热库 5m 仅 2026-09-12 起；无发布时点证据"],
        ["资金费", "parquet/funding + funding_history", "结算时刻 funding_time",
         "冷档 2020-01-01 起；rate_type=settled；2022 年部分币大段空缺"],
        ["指标(多空比/大户)", "parquet/metrics", "观测时刻 ts",
         "2020-09-01 起；无 fetched_at；仅 526 币；生产代码无读取"],
    ]
    with open(OUT / "12_区间口径说明.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["数据集", "来源", "区间口径", "边界说明"]); w.writerows(note)

    print(f"完成：{len(rows)} 币 → out/10_逐币可用区间.csv")
    for r in stat_rows:
        print(f"  {r[0]:16s} 有区间 {r[2]:3d} 币  中位 {r[4]:>7} 天  最长 {r[5]:>7} 天")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""DATA-AUDIT-01 / 01：数据资产盘点（只读）。

产出（写入本单 out/，不改任何业务文件、不写业务库）：
  out/01_热库表与字段.csv        热库 24 张表 + 字段 + 行数
  out/02_热库数据表时间跨度.csv  逐表 min/max 事件时间、符号数、四时间戳填充率
  out/03_冷档数据类型.csv        6 类冷档：文件数/行数/字节/标的/周期/年份/列
  out/04_冷档目录一致性.csv      磁盘 vs _catalog.json 的双向差集
  out/05_逐币逐周期覆盖.csv      551×8 周期：首末时间、行数、期望、缺失
  out/06_逐币OI覆盖.csv          逐币 OI 冷+热：首末、行数、period
  out/07_逐币资金费覆盖.csv      逐币 funding：首末、行数、来源
  out/08_逐币指标覆盖.csv        逐币 metrics：首末、行数、文件
  out/09_汇总.json               供报告引用的关键数字

纪律：
  · 只用 sqlite `mode=ro` + parquet 元数据统计，不读全表、不写任何库；
  · 缺失就是缺失，不补 0、不插值；
  · 所有时间统一 UTC 毫秒，输出同时给 ISO。
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HOT = ROOT / "数据/市场数据/db/market_v2.sqlite3"
PARQ = ROOT / "数据/市场数据/parquet"
OUT = ROOT / "evidence/DATA-AUDIT-01/out"
OUT.mkdir(parents=True, exist_ok=True)

TFS = ["5m", "15m", "30m", "1h", "4h", "8h", "12h", "1d"]


def iso(ms):
    if not ms:
        return ""
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_csv(name, header, rows):
    p = OUT / name
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  → {name}  ({len(rows)} 行)")
    return p


def hot():
    return sqlite3.connect(f"file:{HOT}?mode=ro", uri=True, timeout=30)


# ══════════════════════ 1. 热库表与字段 ══════════════════════
def step_hot_tables():
    con = hot()
    rows = []
    for (name, typ) in con.execute(
            "select name,type from sqlite_master where type in ('table','view') order by type,name"):
        cols = con.execute(f'pragma table_info("{name}")').fetchall()
        try:
            cnt = con.execute(f'select count(*) from "{name}"').fetchone()[0]
        except Exception as e:                                    # noqa: BLE001
            cnt = f"ERR:{e}"
        rows.append([typ, name, cnt, len(cols),
                     " ".join(f"{c[1]}:{c[2]}" for c in cols)])
    write_csv("01_热库表与字段.csv",
              ["类型", "表名", "行数", "字段数", "字段:类型"], rows)
    return len(rows)


# ══════════════════════ 2. 热库数据表时间跨度 ══════════════════════
def step_hot_spans():
    con = hot()
    rows = []

    def add(table, tcol, note, where="", symcol="symbol"):
        sc = f"count(distinct {symcol})" if symcol else "NULL"
        q = f"select count(*),{sc},min({tcol}),max({tcol}) from {table} {where}"
        n, ns, lo, hi = con.execute(q).fetchone()
        rows.append([table, note, n, ns, iso(lo), iso(hi)])

    add("candle_heads", "open_time", "K线头版本(每 open_time 一条)", "")
    add("candle_versions", "open_time", "K线全版本(含修订)", "")
    add("funding_history", "funding_time", "资金费结算事件", "")
    add("oi_history", "ts", "OI 5m", "where period='5m'")
    add("oi_history", "ts", "OI 快照", "where period='snapshot'")
    add("realtime_klines", "event_time", "实时K线(每币每周期一行)", "")
    add("realtime_quotes", "event_time", "实时行情快照", "")
    add("sources", "imported_at", "来源登记(导入时刻)", "")
    add("collector_rounds", "started_at", "采集轮次", "", symcol=None)
    write_csv("02_热库数据表时间跨度.csv",
              ["表", "口径", "行数", "标的数", "最早事件时间", "最晚事件时间"], rows)

    # 四时间戳填充率（K线）
    extra = []
    n, na, nz, sid = con.execute(
        "select count(*),count(available_at),sum(is_closed=0),count(distinct source_id)"
        " from candle_versions").fetchone()
    extra.append(["candle_versions", "available_at 填充", f"{na}/{n}", f"{na/n*100:.1f}%",
                  "非空=采集入库时刻；不是交易所发布时刻"])
    extra.append(["candle_versions", "is_closed=0 行数", nz, "", "热库不存在未收盘行"])
    dup = con.execute("""select count(*) from (select exchange,market,symbol,timeframe,open_time,
        count(distinct source_id) n from candle_versions group by 1,2,3,4,5 having n>1)""").fetchone()[0]
    extra.append(["candle_versions", "同一K线多版本(>1 source_id)的键数", dup, "", "修订版本，不是重复采集"])
    for tbl, note in (("oi_history", "fetched_at"), ("funding_history", "fetched_at")):
        n, nf = con.execute(f"select count(*),count(fetched_at) from {tbl}").fetchone()
        extra.append([tbl, f"{note} 填充", f"{nf}/{n}", f"{nf/n*100:.1f}%", "采集时刻"])
    write_csv("02b_四时间戳填充.csv", ["表", "项", "填充", "占比", "含义"], extra)
    return len(rows)


# ══════════════════════ 3/4. 冷档盘点与目录一致性 ══════════════════════
def parquet_span(path, col):
    """用行组统计取 min/max，避免读全表。"""
    try:
        md = pq.ParquetFile(path).metadata
    except Exception:                                            # noqa: BLE001
        return None, None, 0
    lo = hi = None
    n = md.num_rows
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
    return lo, hi, n


def step_cold():
    disk = sorted(str(p.relative_to(PARQ)) for p in PARQ.rglob("*.parquet"))
    bytype = defaultdict(lambda: {"files": 0, "rows": 0, "bytes": 0, "syms": set(),
                                  "tfs": set(), "cols": set(), "lo": None, "hi": None})
    for rel in disk:
        p = PARQ / rel
        parts = rel.split("/")
        t = parts[0]
        a = bytype[t]
        a["files"] += 1
        a["bytes"] += p.stat().st_size
        if t in ("candles", "candle_heads") and len(parts) >= 5:
            a["syms"].add(parts[3]); a["tfs"].add(parts[4])
            col = "open_time"
        elif t in ("oi", "metrics") and len(parts) >= 4:
            a["syms"].add(parts[3]); col = "ts"
        elif t == "funding":
            a["syms"].add(parts[3].replace(".parquet", "")); col = "funding_time"
        elif t == "sources":
            a["syms"].add(parts[3]); col = "imported_at"
        else:
            col = "open_time"
        lo, hi, n = parquet_span(p, col)
        a["rows"] += n
        if lo is not None:
            a["lo"] = lo if a["lo"] is None else min(a["lo"], lo)
            a["hi"] = hi if a["hi"] is None else max(a["hi"], hi)
        try:
            a["cols"].update(pq.ParquetFile(p).schema_arrow.names)
        except Exception:                                        # noqa: BLE001
            pass

    rows = []
    for t, a in sorted(bytype.items()):
        rows.append([t, a["files"], a["rows"], round(a["bytes"] / 1e6, 1), len(a["syms"]),
                     "/".join(sorted(a["tfs"])) or "-", iso(a["lo"]), iso(a["hi"]),
                     " ".join(sorted(a["cols"]))])
    write_csv("03_冷档数据类型.csv",
              ["数据类型", "文件数", "行数", "MB", "标的数", "周期", "最早", "最晚", "列"], rows)

    # 目录一致性
    cat = set(json.loads((PARQ / "_catalog.json").read_text())["shards"].keys())
    disk_set = set(disk)
    unreg = sorted(disk_set - cat)
    dangling = sorted(cat - disk_set)
    rows = []
    for label, items in (("盘上有·索引无", unreg), ("索引有·盘上无", dangling)):
        c = defaultdict(int)
        for x in items:
            c[x.split("/")[0]] += 1
        for t, n in sorted(c.items()):
            rows.append([label, t, n, "; ".join(items[:0]) or ""])
    # 明细（前 200 条）
    detail = [[x, "盘上有·索引无"] for x in unreg[:200]] + [[x, "索引有·盘上无"] for x in dangling[:200]]
    write_csv("04_冷档目录一致性_汇总.csv", ["差异类型", "数据类型", "条数", "备注"], rows)
    write_csv("04b_冷档目录一致性_明细.csv", ["相对路径", "差异类型"], detail)
    return {"disk_files": len(disk), "catalog": len(cat),
            "unregistered": len(unreg), "dangling": len(dangling),
            "by_type_rows": {r[0]: r[2] for r in rows}}


# ══════════════════════ 5. 逐币逐周期覆盖 ══════════════════════
def step_coverage():
    con = hot()
    rows = []
    for ex, mk, sym, tf, n, fo, lc, exp, mis in con.execute(
            "select exchange,market,symbol,timeframe,rows,first_open,last_close,expected,missing"
            " from coverage_cache order by exchange,symbol,timeframe"):
        rows.append([ex, mk, sym, tf, n, iso(fo), iso(lc), exp, mis,
                     round((mis or 0) / exp * 100, 4) if exp else ""])
    write_csv("05_逐币逐周期覆盖.csv",
              ["交易所", "市场", "标的", "周期", "行数", "首根开盘", "末根收盘",
               "期望根数", "缺失根数", "缺失率%"], rows)
    return len(rows)


# ══════════════════════ 6/7/8. OI / funding / metrics 逐币 ══════════════════════
def step_symbol_datasets():
    # 冷档
    cold = {"oi": defaultdict(lambda: {"lo": None, "hi": None, "rows": 0, "files": 0, "periods": set()}),
            "metrics": defaultdict(lambda: {"lo": None, "hi": None, "rows": 0, "files": 0}),
            "funding": defaultdict(lambda: {"lo": None, "hi": None, "rows": 0, "files": 0})}
    for p in PARQ.glob("oi/binance/um/*/*.parquet"):
        sym = p.parent.name
        lo, hi, n = parquet_span(p, "ts")
        a = cold["oi"][sym]; a["files"] += 1; a["rows"] += n
        if lo is not None:
            a["lo"] = lo if a["lo"] is None else min(a["lo"], lo)
            a["hi"] = hi if a["hi"] is None else max(a["hi"], hi)
    for p in PARQ.glob("metrics/binance/um/*/*.parquet"):
        sym = p.parent.name
        lo, hi, n = parquet_span(p, "ts")
        a = cold["metrics"][sym]; a["files"] += 1; a["rows"] += n
        if lo is not None:
            a["lo"] = lo if a["lo"] is None else min(a["lo"], lo)
            a["hi"] = hi if a["hi"] is None else max(a["hi"], hi)
    for p in PARQ.glob("funding/binance/um/*.parquet"):
        sym = p.name.replace(".parquet", "")
        lo, hi, n = parquet_span(p, "funding_time")
        a = cold["funding"][sym]; a["files"] += 1; a["rows"] += n
        if lo is not None:
            a["lo"] = lo if a["lo"] is None else min(a["lo"], lo)
            a["hi"] = hi if a["hi"] is None else max(a["hi"], hi)

    # 热档
    con = hot()
    hot_oi = {r[0]: r[1:] for r in con.execute(
        "select symbol,count(*),min(ts),max(ts) from oi_history where period='5m' and exchange='binance' group by 1")}
    hot_oi_snap = {r[0]: r[1:] for r in con.execute(
        "select symbol,count(*),min(ts),max(ts) from oi_history where period='snapshot' and exchange='binance' group by 1")}
    hot_fu = {r[0]: r[1:] for r in con.execute(
        "select symbol,count(*),min(funding_time),max(funding_time) from funding_history"
        " where exchange='binance' group by 1")}

    pool = [r[0] for r in con.execute(
        "select symbol from symbol_pool where exchange='binance' and market='um' order by symbol")]

    # OI 表
    rows = []
    for sym in pool:
        c = cold["oi"].get(sym, {})
        h = hot_oi.get(sym)
        s = hot_oi_snap.get(sym)
        lo = min([x for x in (c.get("lo"), h[1] if h else None) if x], default=None)
        hi = max([x for x in (c.get("hi"), h[2] if h else None) if x], default=None)
        rows.append([sym, iso(lo), iso(hi), (c.get("rows") or 0), (h[0] if h else 0),
                     (s[0] if s else 0), iso(s[1]) if s else "", iso(s[2]) if s else "",
                     c.get("files") or 0])
    write_csv("06_逐币OI覆盖.csv",
              ["标的", "冷档最早", "冷档最晚", "冷档5m行数", "热库5m行数",
               "快照行数", "快照最早", "快照最晚", "冷档文件数"], rows)

    # funding 表
    rows = []
    for sym in pool:
        c = cold["funding"].get(sym, {})
        h = hot_fu.get(sym)
        lo = min([x for x in (c.get("lo"), h[1] if h else None) if x], default=None)
        hi = max([x for x in (c.get("hi"), h[2] if h else None) if x], default=None)
        rows.append([sym, iso(lo), iso(hi), c.get("rows") or 0, h[0] if h else 0,
                     c.get("files") or 0])
    write_csv("07_逐币资金费覆盖.csv",
              ["标的", "冷档最早", "冷档最晚", "冷档行数", "热库行数", "冷档文件数"], rows)

    # metrics 表
    rows = []
    for sym in pool:
        c = cold["metrics"].get(sym, {})
        rows.append([sym, iso(c.get("lo")), iso(c.get("hi")), c.get("rows") or 0,
                     c.get("files") or 0])
    write_csv("08_逐币指标覆盖.csv", ["标的", "最早", "最晚", "行数", "文件数"], rows)

    return {
        "pool": len(pool),
        "oi_cold_syms": len(cold["oi"]), "metrics_syms": len(cold["metrics"]),
        "funding_cold_syms": len(cold["funding"]),
        "oi_hot_syms": len(hot_oi), "funding_hot_syms": len(hot_fu),
        "oi_missing_cold": sorted(set(pool) - set(cold["oi"])),
        "metrics_missing": sorted(set(pool) - set(cold["metrics"])),
        "funding_missing_cold": sorted(set(pool) - set(cold["funding"])),
        "oi_missing_hot": sorted(set(pool) - set(hot_oi)),
        "funding_missing_hot": sorted(set(pool) - set(hot_fu)),
    }


# ══════════════════════ 9. 币池核对 ══════════════════════
def step_pool():
    scope = json.loads((ROOT / "数据/contracts/旧标的池/scope_crypto.json").read_text())
    items = [i["symbol"] for i in scope.get("binance_items", []) if i.get("symbol")]
    approved = [s for s in items if s not in ("BTCDOMUSDT", "ALLUSDT")]
    con = hot()
    pool = {r[0] for r in con.execute(
        "select symbol from symbol_pool where exchange='binance' and market='um'")}
    supply = {r[0] for r in con.execute("select symbol from symbol_supply")}
    heads = {r[0] for r in con.execute(
        "select distinct symbol from candle_heads where timeframe='1h'")}
    out = {
        "scope_version": scope.get("scope_version"),
        "generated_utc": scope.get("generated_utc"),
        "scope_binance_items": len(items),
        "approved_pool": len(approved),
        "symbol_pool_um": len(pool),
        "symbol_supply": len(supply),
        "candle_heads_1h": len(heads),
        "scope_minus_pool": sorted(set(approved) - pool),
        "pool_minus_scope": sorted(pool - set(approved)),
        "heads_minus_scope": sorted(heads - set(approved)),
        "excluded_indices_in_scope": [s for s in items if s in ("BTCDOMUSDT", "ALLUSDT")],
        "sha256_note": "范围文件为唯一批准币池事实源，动态读取，不硬编码历史币数",
    }
    (OUT / "09_币池核对.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  → 09_币池核对.json")
    return out


def main():
    print("[1] 热库表与字段")
    nt = step_hot_tables()
    print("[2] 热库时间跨度")
    step_hot_spans()
    print("[3] 冷档数据类型")
    cat = step_cold()
    print("[4] 逐币逐周期覆盖")
    ncov = step_coverage()
    print("[5] 逐币 OI/资金费/指标")
    ds = step_symbol_datasets()
    print("[6] 币池核对")
    pool = step_pool()

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hot_tables": nt, "hot_db_mb": round(HOT.stat().st_size / 1e6, 1),
        "cold": cat, "coverage_rows": ncov, "datasets": ds, "pool": pool,
        "archive_state": json.loads((PARQ / "_state.json").read_text()),
    }
    (OUT / "09_汇总.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                                     encoding="utf-8")
    print("\n完成。汇总 → out/09_汇总.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-02｜01 历史来源盘点 + 180 天读取路径（复用既有冷层）。

工单「三」：盘点项目现有热库、历史库、文件归档及配置入口，逐项说明实际覆盖和
策略是否能读取；**优先复用已有历史数据**接入 180 天读取目标；不下载新数据。

本脚本**不新建任何数据通道**，只用项目已有的两样东西：
  ① `框架/backend/market_repository.py` 的 `MarketDataRepository.from_archive(...)`
     —— 既有热( SQLite )+冷( Parquet ) 读路由，带 head 版本语义、去重、时间升序；
  ② `数据/市场数据/parquet/_state.json` —— 归档器写入的固定水位（冷热边界的单一事实源）。

做三件事：
  A. 盘点四类来源的实际覆盖（热库 / 冷层 parquet / 官方归档 zip / 归档状态）；
  B. 用既有路由把 180 天 1h/4h 拼进一个**隔离**库 `out/180d_market.sqlite3`
     （schema 与生产同构，`PoolMarketWatcher` 可原样读；不写生产库）；
  C. 逐币覆盖审计，把「上市不足 180 天」与「存储缺失」分开。

产出：
  out/01_历史来源盘点.json
  out/01_180天覆盖.json / out/01_180天覆盖.csv
  out/180d_market.sqlite3
"""
from __future__ import annotations

import csv
import importlib
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

ARCHIVE_ROOT = cm.BASE / "数据/市场数据/parquet"
RAW_ROOT = cm.BASE / "数据/raw"
ISO_DB = cm.OUT / "180d_market.sqlite3"
TARGET_DAYS = 180
PAGE = 2000          # 既有路由的单次上限


# ══════════════════════════════════════════════════════════════════════
# A. 来源盘点
# ══════════════════════════════════════════════════════════════════════
def inventory():
    import live_store as L
    hot_db = Path(L.MARKET_DB)
    inv = {"generated_at_utc": cm.utc_now(), "read_only": True,
           "downloaded_anything": False}

    # ① 热库
    con = cm.market_conn()
    try:
        hot = {"path": str(hot_db), "size_bytes": hot_db.stat().st_size}
        for tf in ("15m", "1h", "4h"):
            r = con.execute("SELECT MIN(open_time) a, MAX(open_time) b, COUNT(*) n, "
                            "COUNT(DISTINCT symbol) s FROM candles WHERE timeframe=?",
                            (tf,)).fetchone()
            hot[tf] = {"first_utc": cm.iso(r["a"]), "last_utc": cm.iso(r["b"]),
                       "bars": r["n"], "symbols": r["s"],
                       "span_days": round((r["b"] - r["a"]) / cm.DAY, 2) if r["a"] else None}
        hot["note"] = ("热层只保留近 retention_days 天 + 每周期 latest200 保证；"
                       "更早的数据已导出冷层后从热库裁掉。**热库跨度 ≠ 全部可得历史**。")
    finally:
        con.close()
    inv["hot_sqlite"] = hot

    # ② 冷层 parquet
    cat = json.loads((ARCHIVE_ROOT / "_catalog.json").read_text(encoding="utf-8"))
    st = json.loads((ARCHIVE_ROOT / "_state.json").read_text(encoding="utf-8"))
    cold = {"archive_root": str(ARCHIVE_ROOT),
            "watermark_utc": cm.iso(st.get("watermark_ms")),
            "retention_days": st.get("retention_days", 30),
            "catalog_shards": len(cat["shards"]),
            "state": {k: st[k] for k in ("watermark_ms", "retention_days")
                      if k in st}}
    per_tf = {}
    for tf in ("15m", "1h", "4h"):
        ks = [k for k in cat["shards"] if k.startswith("candles/") and f"/{tf}/" in k]
        years = sorted({k.rsplit("/", 1)[-1].replace(".parquet", "") for k in ks})
        per_tf[tf] = {"shards": len(ks),
                      "years": years,
                      "year_min": years[0] if years else None,
                      "year_max": years[-1] if years else None,
                      "rows": sum(cat["shards"][k]["rows"] for k in ks)}
    cold["per_timeframe"] = per_tf
    cold["note"] = ("冷层 = `candles/`（candle_versions 全版本）+ `candle_heads/`（头版本）；"
                    "读取必须 JOIN 两个分片，否则会把历史版本重复计入。")
    inv["cold_parquet"] = cold

    # ③ 官方归档 zip（原始证据，不参与读路径）
    raw = {}
    for tf in ("15m", "1h", "4h"):
        d = RAW_ROOT / tf
        syms = [p for p in d.iterdir() if p.is_dir()] if d.is_dir() else []
        months, sym_with = set(), 0
        for s in syms:
            zs = sorted(s.glob("*.zip"))
            if zs:
                sym_with += 1
            for z in zs:
                # BTCUSDT-1h-2020-01.zip / BTCUSDT-1h-2019-12-31.zip
                parts = z.name.replace(".zip", "").split("-")
                # BTCUSDT-1h-2020-01 / BTCUSDT-1h-2019-12-31 ⇒ 取日期段前 7 位为月份
                if len(parts) >= 4:
                    months.add("-".join(parts[2:])[:7])
        ms = sorted(months)
        raw[tf] = {"symbols": len(syms), "symbols_with_zip": sym_with,
                   "zip_count": sum(len(list(s.glob('*.zip'))) for s in syms),
                   "month_min": ms[0] if ms else None,
                   "month_max": ms[-1] if ms else None,
                   "checksum_sidecars": sum(len(list(s.glob('*.CHECKSUM'))) for s in syms)}
    inv["official_archive_zip"] = {
        "root": str(RAW_ROOT), "per_timeframe": raw,
        "note": "币安官方月度归档 + 逐文件 CHECKSUM；是冷层的上游原始证据，当前**不参与**任何读路径。",
    }

    # ④ 既有读路由
    sys.path.insert(0, str(cm.BACKEND))
    from market_repository import MarketDataRepository
    r = MarketDataRepository.from_archive(hot_db, ARCHIVE_ROOT)
    inv["existing_router"] = {
        "module": str(cm.BACKEND / "market_repository.py"),
        "sha256": cm.sha256_file(cm.BACKEND / "market_repository.py"),
        "class": "MarketDataRepository",
        "entry": "MarketDataRepository.from_archive(hot_db, archive_root)",
        "watermark_ms": r.watermark_ms, "watermark_utc": cm.iso(r.watermark_ms),
        "retention_days": r.retention_days, "min_bars": r.min_bars,
        "hot_floor_1h_utc": cm.iso(r.hot_floor_ms("1h")),
        "per_call_limit_max": 2000,
        "in_production_traffic": False,
        "note": ("既有热+冷读路由，带 head 版本语义 / 去重 / 升序；模块自述"
                 "「不参与生产流量（由上层决定何时切换）」⇒ 策略引擎当前**没有**走它。"),
    }
    return inv


# ══════════════════════════════════════════════════════════════════════
# B. 用既有路由拼 180 天隔离库
# ══════════════════════════════════════════════════════════════════════
def build_180d(symbols, start_ms, end_ms, log_every=40):
    """把 180 天 1h/4h 读进隔离库。去重按 open_time（同 key 保留热层值）。"""
    sys.path.insert(0, str(cm.BACKEND))
    from market_repository import MarketDataRepository
    import live_store as L2
    repo = MarketDataRepository.from_archive(Path(L2.MARKET_DB), ARCHIVE_ROOT)

    if ISO_DB.exists():
        ISO_DB.unlink()
    dst = sqlite3.connect(ISO_DB)
    src = cm.market_conn()
    try:
        for name in ("candle_versions", "candle_heads"):
            dst.execute(src.execute("SELECT sql FROM sqlite_master WHERE name=?",
                                    (name,)).fetchone()[0])
        dst.execute(src.execute("SELECT sql FROM sqlite_master WHERE name='candles'").fetchone()[0])
    finally:
        src.close()
    dst.execute("CREATE INDEX ix_cv ON candle_versions(exchange,market,symbol,timeframe,open_time)")
    dst.execute("CREATE INDEX ix_cv_ct ON candle_versions(exchange,market,symbol,timeframe,close_time)")
    dst.commit()

    cols = ["exchange", "market", "symbol", "timeframe", "open_time", "close_time",
            "open", "high", "low", "close", "volume", "quote_volume", "trade_count",
            "taker_buy_volume", "taker_buy_quote_volume", "source_id", "available_at",
            "is_closed"]
    sql_ins = (f"INSERT OR REPLACE INTO candle_versions({','.join(cols)}) "
               f"VALUES({','.join('?' * len(cols))})")
    sql_head = ("INSERT OR REPLACE INTO candle_heads(exchange,market,symbol,timeframe,"
                "open_time,source_id) VALUES(?,?,?,?,?,?)")

    per = {}
    t_start = time.time()
    for idx, sym in enumerate(symbols, 1):
        rec = {}
        for tf, interval in (("1h", cm.H1), ("4h", cm.H4)):
            rows, end, pages, truncated = [], int(end_ms), 0, False
            while True:
                got = repo.candles(cm.EXCHANGE, cm.MARKET, sym, tf,
                                   start=int(start_ms), end=int(end), limit=PAGE)
                pages += 1
                if not got:
                    break
                rows.extend(got)
                oldest = int(got[0]["open_time"])
                if oldest <= start_ms or len(got) < PAGE or pages > 6:
                    truncated = (oldest > start_ms)
                    break
                end = oldest                      # 继续往前翻
            # 去重（同 open_time 保留第一次出现的，即热层优先）+ 升序 + 只留已收盘
            seen, uniq = set(), []
            for r in rows:
                k = int(r["open_time"])
                if k in seen:
                    continue
                seen.add(k)
                if int(r.get("is_closed") or 0) != 1:
                    continue
                if int(r["close_time"]) > int(end_ms):
                    continue
                uniq.append(r)
            uniq.sort(key=lambda r: int(r["open_time"]))
            batch, heads = [], []
            for r in uniq:
                batch.append(tuple(r[c] for c in cols))
                heads.append((r["exchange"], r["market"], r["symbol"], r["timeframe"],
                              int(r["open_time"]), r["source_id"]))
            if batch:
                dst.executemany(sql_ins, batch)
                dst.executemany(sql_head, heads)
            rec[tf] = {"bars": len(uniq), "pages": pages,
                       "first_utc": cm.iso(uniq[0]["open_time"]) if uniq else None,
                       "last_utc": cm.iso(uniq[-1]["open_time"]) if uniq else None,
                       "reached_request_start": (not truncated) and bool(uniq),
                       "paged_out": truncated}
        per[sym] = rec
        dst.commit()
        if idx % log_every == 0 or idx == len(symbols):
            print(f"[180d] {idx}/{len(symbols)} {sym} "
                  f"1h={rec['1h']['bars']} 4h={rec['4h']['bars']} "
                  f"elapsed={round(time.time() - t_start, 1)}s", flush=True)

    n_head = dst.execute("SELECT COUNT(*) FROM candle_heads").fetchone()[0]
    n_view = dst.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    dst.close()
    assert n_view == n_head and n_head > 0, f"candles 视图 {n_view} != heads {n_head}"
    return per, {"heads": n_head, "visible": n_view,
                 "elapsed_sec": round(time.time() - t_start, 1)}


# ══════════════════════════════════════════════════════════════════════
# C. 逐币覆盖：上市不足 180 天  vs  存储缺失
# ══════════════════════════════════════════════════════════════════════
def earliest_anywhere(symbols, tf):
    """该标的在**冷层全历史**里的最早一根（= 真实上市起点，用于区分"上市晚"与"缺存储"）。

    结果缓存到 `out/_earliest_anywhere_<tf>.json`（首次计算约 20~40 s，之后直接复用）。
    """
    cache = cm.OUT / f"_earliest_anywhere_{tf}.json"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        return {s: d.get(s) for s in symbols}
    import duckdb
    shard_glob = str(ARCHIVE_ROOT / "candles" / cm.EXCHANGE / cm.MARKET / "*" / tf / "*.parquet")
    head_glob = str(ARCHIVE_ROOT / "candle_heads" / cm.EXCHANGE / cm.MARKET / "*" / tf / "*.parquet")
    con = duckdb.connect()
    try:
        con.execute("PRAGMA threads=2")
        q = ("SELECT v.symbol, MIN(v.open_time) FROM read_parquet(?) v "
             "JOIN read_parquet(?) h USING(exchange,market,symbol,timeframe,open_time,source_id) "
             "WHERE v.exchange=? AND v.market=? AND v.timeframe=? GROUP BY v.symbol")
        rows = con.execute(q, [shard_glob, head_glob, cm.EXCHANGE, cm.MARKET, tf]).fetchall()
    finally:
        con.close()
    d = {s: int(t) for s, t in rows if t is not None}
    cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return {s: d.get(s) for s in symbols}


def main():
    only_inv = "--inventory-only" in sys.argv
    syms, meta = cm.universe()

    inv = inventory()
    cm.write_json("01_历史来源盘点.json", inv)
    print("[inv] hot 1h span:", inv["hot_sqlite"]["1h"]["span_days"], "days")
    print("[inv] cold 1h years:", inv["cold_parquet"]["per_timeframe"]["1h"]["years"])
    print("[inv] cold 1h rows:", inv["cold_parquet"]["per_timeframe"]["1h"]["rows"])
    print("[inv] raw zip 1h months:", inv["official_archive_zip"]["per_timeframe"]["1h"]["month_min"],
          "~", inv["official_archive_zip"]["per_timeframe"]["1h"]["month_max"])
    print("[inv] router watermark:", inv["existing_router"]["watermark_utc"],
          "in_production:", inv["existing_router"]["in_production_traffic"])
    if only_inv:
        return

    con = cm.market_conn()
    try:
        ref_t = int(con.execute(
            "SELECT MAX(close_time) FROM candles WHERE exchange=? AND market=? AND timeframe='1h'",
            (cm.EXCHANGE, cm.MARKET)).fetchone()[0])
    finally:
        con.close()
    start_ms = ref_t - TARGET_DAYS * cm.DAY

    if "--skip-build" in sys.argv and ISO_DB.exists():
        print("[180d] 复用已建好的", ISO_DB.name, "（--skip-build）")
        build_stat = {"reused": True}
        per = {}
        con = sqlite3.connect(ISO_DB)
        try:
            for s in syms:
                rec = {}
                for tf in ("1h", "4h"):
                    r = con.execute(
                        "SELECT COUNT(*), MIN(open_time), MAX(open_time) FROM candle_versions "
                        "WHERE symbol=? AND timeframe=?", (s, tf)).fetchone()
                    rec[tf] = {"bars": r[0], "pages": None,
                               "first_utc": cm.iso(r[1]), "last_utc": cm.iso(r[2]),
                               "reached_request_start": None, "paged_out": None}
                per[s] = rec
        finally:
            con.close()
    else:
        per, build_stat = build_180d(syms, start_ms, ref_t)
        print("[180d] build:", build_stat)

    print("[180d] 计算冷层全历史最早一根（真实上市起点）…", flush=True)
    first_1h = earliest_anywhere(syms, "1h")
    first_4h = earliest_anywhere(syms, "4h")

    rows = []
    for s in syms:
        r = {"symbol": s,
             "requested_start_utc": cm.iso(start_ms), "requested_end_utc": cm.iso(ref_t)}
        for tf, first_any, iv in (("1h", first_1h, cm.H1), ("4h", first_4h, cm.H4)):
            d = per[s][tf]
            first_ms = (cm.dt.datetime.strptime(d["first_utc"], "%Y-%m-%dT%H:%M:%SZ")
                        .replace(tzinfo=cm.UTC).timestamp() * 1000) if d["first_utc"] else None
            fa = first_any.get(s)
            listed_late = (fa is not None and fa > start_ms)
            # 覆盖判据（不能用 `first <= start`：窗口本来就从 start 开始，第一根必然 >= start）
            #   ① 首根与请求起点的距离 <= 1 个周期（无前置缺口）
            #   ② 根数达到理论值 - 1（允许 4h 边界不对齐少一根）
            expected = int(TARGET_DAYS * cm.DAY / iv)
            lead_gap_bars = int((first_ms - start_ms) // iv) if first_ms is not None else None
            full_bars = bool(d["bars"] >= expected - 1)
            covers = bool(first_ms is not None and lead_gap_bars is not None
                          and lead_gap_bars <= 1 and full_bars)
            r[f"{tf}_bars"] = d["bars"]
            r[f"{tf}_expected_bars"] = expected
            r[f"{tf}_first_utc"] = d["first_utc"]
            r[f"{tf}_last_utc"] = d["last_utc"]
            r[f"{tf}_days"] = round(d["bars"] * iv / cm.DAY, 2)
            r[f"{tf}_lead_gap_bars"] = lead_gap_bars
            r[f"{tf}_inner_gap_bars"] = (int(expected - d["bars"] - (lead_gap_bars or 0))
                                        if d["bars"] else None)
            r[f"{tf}_covers_180d"] = covers
            r[f"{tf}_earliest_anywhere_utc"] = cm.iso(fa)
            r[f"{tf}_listed_late"] = listed_late
            # 上市不足 180 天：该标的在全历史里的最早一根就晚于请求起点 ⇒ 与存储无关
            # 存储缺失：真实起点早于请求起点，但窗口内没铺满 ⇒ 存储/导入缺失
            r[f"{tf}_storage_missing"] = bool(not listed_late and fa is not None and not covers)
            r[f"{tf}_pre_listing_shortfall_days"] = (
                round((fa - start_ms) / cm.DAY, 2) if listed_late else 0.0)
        rows.append(r)

    def agg(tf):
        return {
            "symbols": len(rows),
            "covers_180d": sum(1 for r in rows if r[f"{tf}_covers_180d"]),
            "listed_late": sum(1 for r in rows if r[f"{tf}_listed_late"]),
            "storage_missing": sum(1 for r in rows if r[f"{tf}_storage_missing"]),
            "no_data_at_all": sum(1 for r in rows if not r[f"{tf}_bars"]),
        }

    out = {
        "ticket": "SIGNAL-HISTORY-FIX-02",
        "stage": "180d_coverage",
        "generated_at_utc": cm.utc_now(),
        "read_only_on_production": True, "downloaded_anything": False,
        "isolated_db": str(ISO_DB), "build": build_stat,
        "target_days": TARGET_DAYS,
        "requested_start_utc": cm.iso(start_ms), "requested_end_utc": cm.iso(ref_t),
        "pool_n": len(syms), "pool_source": meta.get("source") if isinstance(meta, dict) else str(meta),
        "verdict": {"1h": agg("1h"), "4h": agg("4h")},
        "note": ("`listed_late` = 该标的在冷层全历史里的最早一根就晚于请求起点 ⇒ **上市不足 180 天**，"
                 "与存储无关；`storage_missing` = 真实起点早于请求起点但窗口内缺数据 ⇒ **存储缺失**。两者分开统计。"),
    }
    cm.write_json("01_180天覆盖.json", out)
    with open(cm.OUT / "01_180天覆盖.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("[180d] verdict 1h:", out["verdict"]["1h"])
    print("[180d] verdict 4h:", out["verdict"]["4h"])
    print("-> out/01_历史来源盘点.json, out/01_180天覆盖.json, out/180d_market.sqlite3")


if __name__ == "__main__":
    main()

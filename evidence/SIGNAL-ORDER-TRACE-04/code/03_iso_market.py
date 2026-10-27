# -*- coding: utf-8 -*-
"""03｜建**隔离行情库**（含 15m）：纸面成交只用「已闭合 15m bar」，180 天库里没有 15m。

复用而非重写：
  · 合并读取用既有 `框架/backend/history_loader.MergedHistory`（冷层 parquet + 热库，同 key 热层优先）；
  · 表结构从生产库 `market_v2.sqlite3` 的 DDL 原样取（`candle_versions` / `candle_heads` /
    `sources` / `candles` 视图），保证 `paper_engine` 的 SQL（走 `candles` 视图）能直接跑；
  · 只写本单证据目录内的 `out/iso_market.sqlite3`，**不写生产库**、不下载任何数据。

范围纪律（写进结果文件，不靠嘴说）：
  · 只物化**本单用到的标的**（默认 = 02 扫描出的候选标的），其余标的不复制 ⇒
    结果里明确写 `symbols` 列表，**不把「几个标的可用」外推成「全池可用」**。
"""
from __future__ import annotations

import argparse
import importlib
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")

DDL_ORDER = ("sources", "candle_versions", "candle_heads", "funding_history")
VIEWS = ("candles",)
INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_cv ON candle_versions"
    "(exchange,market,symbol,timeframe,open_time)",
    "CREATE INDEX IF NOT EXISTS ix_cv_ct ON candle_versions"
    "(exchange,market,symbol,timeframe,close_time)",
)


def _prod_ddl() -> dict:
    import live_store as L
    con = sqlite3.connect(f"file:{L.MARKET_DB}?mode=ro", uri=True)
    out = {}
    for name in DDL_ORDER + VIEWS:
        r = con.execute("SELECT type, sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not r:
            raise SystemExit(f"生产库缺少 {name} 的 DDL ⇒ 无法同构复制")
        out[name] = {"type": r[0], "sql": r[1]}
    con.close()
    return out


def build(symbols, start_ms, end_ms, out_db) -> dict:
    import live_store as L
    from history_loader import MergedHistory

    ddl = _prod_ddl()
    archive_root = cm.BASE / "数据" / "市场数据" / "parquet"
    prov = MergedHistory(L.MARKET_DB, archive_root, log=lambda *a: None)
    t0 = time.time()
    data, stats = prov.load(symbols, ("15m", "1h", "4h"), start_ms, end_ms)

    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()
    con = sqlite3.connect(str(out_db))
    for name in DDL_ORDER:
        con.execute(ddl[name]["sql"])
    for ix in INDEXES:
        con.execute(ix)
    for name in VIEWS:
        con.execute(ddl[name]["sql"])

    inserted = {}
    src_rows = []
    for i, sym in enumerate(symbols, start=1):
        src_id = 900_000 + i
        for tf in ("15m", "1h", "4h"):
            bars = data.get(sym, {}).get(tf) or []
            if not bars:
                continue
            rows = [(cm.EXCHANGE, cm.MARKET, sym, tf, int(b["open_time"]),
                     int(b["close_time"]), f"{b['open']:.10f}", f"{b['high']:.10f}",
                     f"{b['low']:.10f}", f"{b['close']:.10f}",
                     str(b.get("volume") if b.get("volume") is not None else 0),
                     str(b.get("quote_volume") if b.get("quote_volume") is not None else 0),
                     0, "0", "0", src_id, None, 1) for b in bars]
            con.executemany(
                "INSERT OR REPLACE INTO candle_versions(exchange,market,symbol,timeframe,"
                "open_time,close_time,open,high,low,close,volume,quote_volume,trade_count,"
                "taker_buy_volume,taker_buy_quote_volume,source_id,available_at,is_closed) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.executemany(
                "INSERT OR REPLACE INTO candle_heads(exchange,market,symbol,timeframe,"
                "open_time,source_id) VALUES(?,?,?,?,?,?)",
                [(cm.EXCHANGE, cm.MARKET, sym, tf, int(b["open_time"]), src_id) for b in bars])
            inserted.setdefault(sym, {})[tf] = {
                "n": len(bars),
                "first_utc": cm.iso(bars[0]["open_time"]),
                "last_utc": cm.iso(bars[-1]["open_time"]),
                "sources": sorted({b.get("source") for b in bars}),
            }
            src_rows.append((src_id, cm.EXCHANGE, cm.MARKET, sym, tf,
                             "local://merged_cold_hot", str(archive_root),
                             "n/a(evidence-only, no download)", int(time.time() * 1000),
                             None, int(bars[0]["open_time"]), int(bars[-1]["close_time"]),
                             len(bars)))
    con.executemany(
        "INSERT OR REPLACE INTO sources(id,exchange,market,symbol,timeframe,source_url,"
        "file_path,sha256,imported_at,downloaded_at,first_open,last_close,row_count) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", src_rows)

    # 资金费：真实结算事件（只读复制本单标的；没有就不插，不补 0）
    # 注意：必须「先从生产库读出来，再写进已打开的隔离库」——
    # 从另一条连接 ATTACH 同一文件会因本连接仍持锁而 `database is locked`（已踩过）。
    prod = sqlite3.connect(f"file:{L.MARKET_DB}?mode=ro", uri=True)
    fund_n = 0
    for sym in symbols:
        rows = prod.execute(
            "SELECT exchange,market,symbol,funding_time,funding_rate,mark_price,rate_type,"
            "source,fetched_at FROM funding_history WHERE symbol=? AND exchange='binance' "
            "AND market='um' AND funding_time BETWEEN ? AND ?",
            (sym, int(start_ms), int(end_ms))).fetchall()
        if rows:
            con.executemany("INSERT OR REPLACE INTO funding_history VALUES(?,?,?,?,?,?,?,?,?)",
                            rows)
            fund_n += len(rows)
    prod.close()

    # 校验：视图可用 + 15m 可查（paper_engine 的两条关键 SQL）
    chk = {}
    for sym in symbols:
        try:
            n15 = con.execute(
                "SELECT COUNT(*) FROM candles WHERE exchange='binance' AND market='um' "
                "AND symbol=? AND timeframe='15m' AND is_closed=1", (sym,)).fetchone()[0]
            first = con.execute(
                "SELECT open_time FROM candles WHERE symbol=? AND timeframe='15m' "
                "ORDER BY open_time LIMIT 1", (sym,)).fetchone()
            chk[sym] = {"candles_view_15m_rows": n15,
                        "first_utc": cm.iso(first[0]) if first else None}
        except Exception as exc:                                    # noqa: BLE001
            chk[sym] = {"error": f"{type(exc).__name__}: {exc}"}
    con.commit()
    con.close()

    return {
        "generated_at_utc": cm.utc_now(),
        "iso_market_db": str(out_db),
        "size_mb": round(out_db.stat().st_size / 1e6, 1),
        "symbols": list(symbols),
        "window": {"start_utc": cm.iso(start_ms), "end_utc": cm.iso(end_ms),
                   "days": round((int(end_ms) - int(start_ms)) / cm.DAY, 2)},
        "inserted": inserted,
        "funding_rows": fund_n,
        "merged_loader_stats": stats,
        "post_check": chk,
        "elapsed_sec": round(time.time() - t0, 1),
        "scope_statement": (
            f"本库**只物化 {len(symbols)} 个标的**（{', '.join(symbols)}）；"
            "1h/4h 来自 180 天隔离库对应的冷+热合并读取，15m 来自冷层 parquet + 热库合并。"
            "**不把本库可用外推为全池可用**；全池数据资格见 SIGNAL-HISTORY-FIX-03 的结论。"),
        "downloaded_anything": False,
        "wrote_production": False,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None, help="逗号分隔；缺省取 02 扫描的候选标的")
    ap.add_argument("--start-days", type=float, default=180.0)
    ap.add_argument("--end", default=None, help="ISO；缺省 = 180 天库 4h 末根收盘")
    ap.add_argument("--out-db", default=str(cm.ISO_MARKET_DB))
    a = ap.parse_args()

    if a.symbols:
        syms = [s.strip() for s in a.symbols.split(",") if s.strip()]
    else:
        p = cm.OUT / "02_候选扫描.json"
        if not p.exists():
            raise SystemExit("缺少 02_候选扫描.json，或显式 --symbols")
        d = cm.read_json(p)
        syms = sorted({c["symbol"] for c in d["candidates"]})
        if not syms:
            raise SystemExit("02 扫描 0 候选 ⇒ 无标的可物化（不空转）")
    con = cm.market_conn(cm.ISO_DB_180D)
    if a.end:
        end_ms = cm.parse_iso(a.end)
    else:
        end_ms = int(con.execute("SELECT MAX(close_time) FROM candle_versions "
                                 "WHERE timeframe='4h'").fetchone()[0])
    con.close()
    start_ms = end_ms - int(a.start_days * cm.DAY)
    r = build(syms, start_ms, end_ms, Path(a.out_db))
    cm.write_json("03_隔离行情库.json", r)
    print(f"隔离行情库 {r['iso_market_db']}  {r['size_mb']} MB  {r['elapsed_sec']}s")
    for s in syms:
        print(f"  {s:16s} {json.dumps(r['inserted'].get(s), ensure_ascii=False)}")
    print(f"  资金费 {r['funding_rows']} 行；downloaded={r['downloaded_anything']} "
          f"wrote_production={r['wrote_production']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

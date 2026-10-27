# -*- coding: utf-8 -*-
"""AUTO-LOOP-01 · 00 隔离环境准备（只准备数据，不碰交易函数）

职责边界（硬约束）：
  · 本脚本**只**建目录、建空库、复制行情/资金费/合约规格、写闸门控制文件；
  · **不 import** paper_engine / unified_runtime / live_store 的交易写入路径，
    不调用 open_position / advance_pending / close_at_price / 任何撮合函数；
  · 不写任何成交、持仓、权益记录 —— 这些只能由常驻进程自己产生。

隔离口径：
  · 行情库只含目标标的的 15m/1h/4h 全量历史，写入 `candle_dataset`（is_activated=0），
    由统一运行入口 `sync_dataset_bars_upto(now)` 按回放时钟逐根激活到 `candle_versions`
    ⇒ 常驻进程永远看不到「未来 bar」，因果边界由入口自己维护；
  · 价格/量全部按生产库原始 TEXT 精度复制（不经过 float 格式化，避免低价币被压成 2 位小数）；
  · 账本/live 库/锁/闸门全部落在 iso 目录，环境变量在启动器里注入。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
PROD_MARKET = Path("/Users/boos/Desktop/威科夫因子共振趋势/数据/市场数据/db/market_v2.sqlite3")

DDL = [
    """CREATE TABLE sources (
        id INTEGER PRIMARY KEY, exchange TEXT NOT NULL, market TEXT NOT NULL,
        symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
        source_url TEXT NOT NULL, file_path TEXT NOT NULL, sha256 TEXT NOT NULL,
        imported_at INTEGER NOT NULL, downloaded_at INTEGER,
        first_open INTEGER NOT NULL, last_close INTEGER NOT NULL, row_count INTEGER NOT NULL,
        UNIQUE(exchange, market, symbol, timeframe, sha256))""",
    """CREATE TABLE candle_versions (
        exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
        open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
        volume TEXT NOT NULL, quote_volume TEXT NOT NULL, trade_count INTEGER NOT NULL,
        taker_buy_volume TEXT NOT NULL, taker_buy_quote_volume TEXT NOT NULL,
        source_id INTEGER NOT NULL REFERENCES sources(id),
        available_at INTEGER, is_closed INTEGER NOT NULL CHECK(is_closed = 1),
        PRIMARY KEY(exchange, market, symbol, timeframe, open_time, source_id))""",
    """CREATE TABLE candle_heads (
        exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, source_id INTEGER NOT NULL,
        PRIMARY KEY(exchange, market, symbol, timeframe, open_time),
        FOREIGN KEY(exchange, market, symbol, timeframe, open_time, source_id)
          REFERENCES candle_versions(exchange, market, symbol, timeframe, open_time, source_id))
        WITHOUT ROWID""",
    """CREATE VIEW candles AS
       SELECT v.* FROM candle_versions v JOIN candle_heads h
       USING(exchange, market, symbol, timeframe, open_time, source_id)""",
    """CREATE TABLE candle_dataset (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
        open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
        volume TEXT NOT NULL, quote_volume TEXT NOT NULL,
        is_activated INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE funding_history (
        exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
        funding_time INTEGER NOT NULL, funding_rate TEXT, mark_price TEXT,
        rate_type TEXT, source TEXT, fetched_at INTEGER,
        PRIMARY KEY(exchange, market, symbol, funding_time))""",
]


def sha16(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--iso", default=str(OUT / "iso"))
    ap.add_argument("--gate-allow", default="1", choices=["0", "1"])
    args = ap.parse_args()

    sym = args.symbol.strip().upper()
    iso = Path(args.iso) / sym
    if iso.exists():
        shutil.rmtree(iso)
    for sub in ("paper", "live", "locks"):
        (iso / sub).mkdir(parents=True, exist_ok=True)
    mdb = iso / "market.sqlite3"

    src = sqlite3.connect(f"file:{PROD_MARKET}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(mdb)
    for stmt in DDL:
        dst.execute(stmt)
    dst.execute(
        "INSERT INTO sources(id,exchange,market,symbol,timeframe,source_url,file_path,sha256,"
        "imported_at,downloaded_at,first_open,last_close,row_count) "
        "VALUES(1,'binance','um',?,'mixed','iso://auto-loop-01','iso://auto-loop-01',"
        "'iso-auto-loop-01',0,0,0,0,0)", (sym,))

    report = {"symbol": sym, "iso_dir": str(iso), "market_db": str(mdb),
              "timeframes": {}, "funding_rows": 0, "prod_market_sha16": sha16(PROD_MARKET)}

    for tf in ("15m", "1h", "4h"):
        rows = src.execute(
            "SELECT open_time,close_time,open,high,low,close,volume,quote_volume "
            "FROM candle_versions WHERE exchange='binance' AND market='um' AND symbol=? "
            "AND timeframe=? ORDER BY open_time", (sym, tf)).fetchall()
        dst.executemany(
            "INSERT INTO candle_dataset(exchange,market,symbol,timeframe,open_time,close_time,"
            "open,high,low,close,volume,quote_volume,is_activated) "
            "VALUES('binance','um',?,?,?,?,?,?,?,?,?,?,0)",
            [(sym, tf, r["open_time"], r["close_time"], r["open"], r["high"], r["low"],
              r["close"], r["volume"], r["quote_volume"]) for r in rows])
        report["timeframes"][tf] = {
            "rows": len(rows),
            "first_open": rows[0]["open_time"] if rows else None,
            "last_close": rows[-1]["close_time"] if rows else None,
        }

    fr = src.execute("SELECT * FROM funding_history WHERE exchange='binance' AND market='um' "
                     "AND symbol=? ORDER BY funding_time", (sym,)).fetchall()
    dst.executemany(
        "INSERT INTO funding_history(exchange,market,symbol,funding_time,funding_rate,"
        "mark_price,rate_type,source,fetched_at) VALUES('binance','um',?,?,?,?,?,?,?)",
        [(sym, r["funding_time"], r["funding_rate"], r["mark_price"], r["rate_type"],
          r["source"], r["fetched_at"]) for r in fr])
    report["funding_rows"] = len(fr)
    report["funding_first"] = fr[0]["funding_time"] if fr else None
    report["funding_last"] = fr[-1]["funding_time"] if fr else None
    dst.commit()
    dst.close()
    src.close()

    gate = iso / "gate.json"
    gate.write_text(json.dumps({
        "allow_new_entries": args.gate_allow == "1",
        "reason": "auto_loop_01_isolated_replay",
        "set_by": "AUTO-LOOP-01/00_prepare_iso.py",
        "since_ms": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    report["gate_file"] = str(gate)
    report["gate_allow_new_entries"] = args.gate_allow == "1"

    p = OUT / "out" / f"prepare_{sym}.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

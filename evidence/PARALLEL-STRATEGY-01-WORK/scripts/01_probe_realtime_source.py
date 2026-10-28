#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""01 · 现实撮合可行性探测：项目现有实时行情能否支撑「执行点当场成交」。

本脚本只读（行情库 mode=ro），不写任何生产文件。

要回答四个问题：
  Q1 `realtime_klines` 的 15m 行是否覆盖全池、是否在 bar 开盘后秒级出现？
  Q2 未闭合 15m bar 的 `open` 是否等于该 bar 最终在 `candles` 里的 `open`？
     （open 若恒定 ⇒ 「执行点到达后任意时刻读到同一价格」，不需要毫秒级轮询）
  Q3 `realtime_quotes.last_price` 的新鲜度分布，作为第二价格源是否可用？
  Q4 现有 `candles`（闭 bar）路径的实际可得延迟有多大？
     （即：现在这套「等闭合再回读 open」到底晚了多久）

用法：
  python3 01_probe_realtime_source.py --out ../out/01_probe.json
  python3 01_probe_realtime_source.py --wait-forward   # 额外做前向一致性观测（约 16 分钟）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parents[3]
MARKET_DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"


def iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=8)


def q_realtime_coverage(con, now_ms):
    out = {}
    rows = list(con.execute(
        "SELECT exchange,market,COUNT(*),MIN(received_at),MAX(received_at),"
        "MIN(open_time),MAX(open_time),SUM(is_closed) "
        "FROM realtime_klines WHERE timeframe='15m' GROUP BY exchange,market"))
    out["by_venue"] = [
        {"exchange": r[0], "market": r[1], "rows": r[2],
         "received_at_min": iso(r[3]), "received_at_max": iso(r[4]),
         "open_time_min": iso(r[5]), "open_time_max": iso(r[6]),
         "is_closed_sum": r[7]} for r in rows]
    # 同一 open_time 的标的数（= 这一轮有多少标的拿到了同一根 bar）
    per_bar = list(con.execute(
        "SELECT open_time,COUNT(*) FROM realtime_klines "
        "WHERE timeframe='15m' AND exchange='binance' AND market='um' "
        "GROUP BY open_time ORDER BY open_time DESC LIMIT 6"))
    out["binance_um_symbols_per_bar"] = [
        {"open_time": iso(r[0]), "symbols": r[1]} for r in per_bar]
    # 新鲜度：received_at 距今
    ages = [now_ms - r[0] for r in con.execute(
        "SELECT received_at FROM realtime_klines "
        "WHERE timeframe='15m' AND exchange='binance' AND market='um' "
        "AND received_at IS NOT NULL")]
    if ages:
        ages.sort()
        out["binance_um_received_age_ms"] = {
            "n": len(ages), "min": ages[0], "p50": ages[len(ages)//2],
            "p95": ages[int(len(ages)*0.95)], "max": ages[-1]}
    # 池子规模
    try:
        pool = con.execute(
            "SELECT COUNT(*) FROM symbol_pool WHERE exchange='binance' AND market='um'").fetchone()[0]
        out["symbol_pool_binance_um"] = pool
    except Exception as exc:                                  # noqa: BLE001
        out["symbol_pool_binance_um"] = f"err:{type(exc).__name__}"
    return out


def q_bar_shape(con, symbol="BTCUSDT"):
    """未闭合 bar 的 open 是否落在 [low, high] 内（区间校验能否直接复用）。"""
    row = con.execute(
        "SELECT symbol,open_time,close_time,open,high,low,close,is_closed,"
        "event_time,received_at,source,transport,quote_volume "
        "FROM realtime_klines WHERE exchange='binance' AND market='um' "
        "AND symbol=? AND timeframe='15m'", (symbol,)).fetchone()
    if not row:
        return None
    d = dict(zip(["symbol", "open_time", "close_time", "open", "high", "low", "close",
                  "is_closed", "event_time", "received_at", "source", "transport",
                  "quote_volume"], row))
    o, hi, lo = float(d["open"]), float(d["high"]), float(d["low"])
    d["open_within_low_high"] = (lo <= o <= hi)
    d["open_time_iso"] = iso(d["open_time"])
    d["close_time_iso"] = iso(d["close_time"])
    d["event_time_iso"] = iso(d["event_time"])
    d["received_at_iso"] = iso(d["received_at"])
    return d


def q_quotes_freshness(con, now_ms):
    out = {}
    rows = list(con.execute(
        "SELECT exchange,market,COUNT(*),MIN(price_received_at),MAX(price_received_at) "
        "FROM realtime_quotes GROUP BY exchange,market"))
    out["by_venue"] = [{"exchange": r[0], "market": r[1], "rows": r[2],
                        "price_received_at_min": iso(r[3]),
                        "price_received_at_max": iso(r[4])} for r in rows]
    ages = [now_ms - r[0] for r in con.execute(
        "SELECT price_received_at FROM realtime_quotes "
        "WHERE exchange='binance' AND market='um' AND price_received_at IS NOT NULL")]
    if ages:
        ages.sort()
        out["binance_um_price_age_ms"] = {
            "n": len(ages), "min": ages[0], "p50": ages[len(ages)//2],
            "p90": ages[int(len(ages)*0.90)], "p95": ages[int(len(ages)*0.95)],
            "max": ages[-1],
            "stale_gt_60s": sum(1 for a in ages if a > 60_000),
            "stale_gt_300s": sum(1 for a in ages if a > 300_000)}
    return out


def q_closed_bar_latency(con, now_ms, n=40):
    """现有「等闭 bar」路径的真实延迟：闭 bar 的 available_at 距其 close_time 多久。

    注意 `candle_versions` **没有** imported_at 列；入库时刻在 `sources.imported_at`。
    """
    rows = list(con.execute(
        "SELECT v.symbol,v.open_time,v.close_time,v.available_at,v.source_id,"
        "s.imported_at,s.file_path "
        "FROM candle_versions v LEFT JOIN sources s ON s.id=v.source_id "
        "WHERE v.exchange='binance' AND v.market='um' "
        "AND v.timeframe='15m' AND v.is_closed=1 "
        "ORDER BY v.open_time DESC LIMIT ?", (n,)))
    detail = []
    for r in rows:
        d = {"symbol": r[0], "open_time": iso(r[1]), "close_time": iso(r[2]),
             "available_at": iso(r[3]), "source_id": r[4],
             "imported_at": iso(r[5]), "file_path": r[6]}
        if r[3]:
            d["available_minus_close_ms"] = int(r[3]) - int(r[2])
        if r[5]:
            d["imported_minus_close_ms"] = int(r[5]) - int(r[2])
        detail.append(d)
    deltas = [d["available_minus_close_ms"] for d in detail
              if d.get("available_minus_close_ms") is not None]
    deltas_i = [d["imported_minus_close_ms"] for d in detail
                if d.get("imported_minus_close_ms") is not None]
    def stat(xs):
        if not xs:
            return None
        xs = sorted(xs)
        return {"n": len(xs), "min": xs[0], "p50": xs[len(xs)//2],
                "p90": xs[int(len(xs) * 0.9)], "max": xs[-1]}
    # 最新一根已闭合 15m bar 的整体情况（全池同一 open_time）
    newest = list(con.execute(
        "SELECT open_time,COUNT(*) FROM candle_versions "
        "WHERE exchange='binance' AND market='um' AND timeframe='15m' AND is_closed=1 "
        "GROUP BY open_time ORDER BY open_time DESC LIMIT 3"))
    return {"detail_sample": detail,
            "available_minus_close_ms": stat(deltas),
            "imported_minus_close_ms": stat(deltas_i),
            "newest_closed_15m_bars": [
                {"open_time": iso(r[0]), "symbols": r[1]} for r in newest]}


def forward_consistency(symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")):
    """前向观测：先记下未闭合 bar 的实时 open，等它闭合并落入 candles 后比对。

    这是「实时 open 是否等于最终 open」的唯一硬证据（不能靠单点假设）。
    等一个完整 15m 周期 + 90 秒入库余量。
    """
    con = ro(MARKET_DB)
    con.row_factory = sqlite3.Row
    before = {}
    for s in symbols:
        r = con.execute(
            "SELECT symbol,open_time,open,high,low,close,is_closed,event_time,received_at "
            "FROM realtime_klines WHERE exchange='binance' AND market='um' "
            "AND symbol=? AND timeframe='15m'", (s,)).fetchone()
        if r:
            before[s] = dict(r)
    con.close()
    if not before:
        return {"error": "no realtime row at start"}
    bar_open = min(v["open_time"] for v in before.values())
    close_ms = bar_open + 15 * 60 * 1000
    wait_ms = close_ms + 90_000 - int(time.time() * 1000)
    rec = {"sampled_at": iso(int(time.time() * 1000)),
           "bar_open_time": iso(bar_open),
           "bar_close_time": iso(close_ms),
           "realtime_open_before_close": {
               s: {"open": v["open"], "event_time": iso(v["event_time"]),
                   "received_at": iso(v["received_at"]), "is_closed": v["is_closed"]}
               for s, v in before.items()},
           "waited_ms": max(0, wait_ms)}
    if wait_ms > 0:
        time.sleep(wait_ms / 1000.0)
    con = ro(MARKET_DB)
    con.row_factory = sqlite3.Row
    after = {}
    for s in before:
        r = con.execute(
            "SELECT open_time,close_time,open,high,low,close,is_closed,available_at "
            "FROM candle_versions WHERE exchange='binance' AND market='um' "
            "AND symbol=? AND timeframe='15m' AND open_time=? AND is_closed=1",
            (s, bar_open)).fetchone()
        after[s] = dict(r) if r else None
    # 同时看实时行是否已切到下一根
    rt_next = {}
    for s in before:
        r = con.execute(
            "SELECT open_time,open,is_closed FROM realtime_klines "
            "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m'",
            (s,)).fetchone()
        rt_next[s] = dict(r) if r else None
    con.close()
    rec["candles_after_close"] = {
        s: (None if v is None else {
            "open": v["open"], "high": v["high"], "low": v["low"], "close": v["close"],
            "available_at": iso(v["available_at"]),
            "available_minus_close_ms": (int(v["available_at"]) - int(v["close_time"])
                                         if v["available_at"] else None)})
        for s, v in after.items()}
    rec["realtime_row_after_close"] = {
        s: (None if v is None else {"open_time": iso(v["open_time"]), "open": v["open"],
                                    "is_closed": v["is_closed"]})
        for s, v in rt_next.items()}
    # 判定：open 是否逐位相等
    checks = {}
    for s, v in before.items():
        c = after.get(s)
        if c is None:
            checks[s] = {"match": None, "why": "candles 里还没有这根 bar"}
            continue
        eq = (str(v["open"]).strip() == str(c["open"]).strip())
        checks[s] = {"match": eq, "realtime_open": v["open"], "candles_open": c["open"],
                     "abs_diff": abs(float(v["open"]) - float(c["open"]))}
    rec["open_equality"] = checks
    rec["all_match"] = all(x.get("match") is True for x in checks.values())
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "out" / "01_probe.json"))
    ap.add_argument("--wait-forward", action="store_true")
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    con = ro(MARKET_DB)
    res = {
        "generated_at": iso(now_ms),
        "generated_at_ms": now_ms,
        "market_db": str(MARKET_DB),
        "q1_realtime_kline_coverage": q_realtime_coverage(con, now_ms),
        "q2_bar_shape_sample": q_bar_shape(con),
        "q3_quote_freshness": q_quotes_freshness(con, now_ms),
        "q4_closed_bar_latency": q_closed_bar_latency(con, now_ms),
    }
    con.close()
    if args.wait_forward:
        res["q2b_forward_open_equality"] = forward_consistency()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

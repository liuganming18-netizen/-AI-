# -*- coding: utf-8 -*-
"""补齐 BTCUSDT 的 1h / 4h 真实 K 线到最新（走币安公开 REST，不造数据）。

背景：统一运行时读 `candles` 里的已闭合 1h/4h；实测 BTCUSDT 的 1h 停在 00:59 UTC、
4h 停在 10-22 23:59 UTC，而采集器的补缺预算每轮只覆盖 10 个合约 ⇒ 单标的长期落后。
本脚本只补**该标的该周期**的缺口，写库复用采集器 `_store_closed_bars`
（先查 head 已存在即跳过，绝不覆盖既有行）。

只写 `candle_versions` / `candle_heads`（采集器本来就在写这两张表），
不改价格、不补空桶、不写未收盘 bar。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))

import os
os.environ.setdefault("MARKET_DB", str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"))

import realtime_collector as rc  # noqa: E402

SYMBOL = "BTCUSDT"
EXCH = "binance"
MKT = "um"
IV = {"1h": 3_600_000, "4h": 14_400_000}
LIMIT = {"1h": 1000, "4h": 1000}

# 无代理直连（本机 HTTP_PROXY 会劫持 127.0.0.1 与交易所域名）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(interval: str, start_ms: int, end_ms: int, limit: int):
    url = ("https://fapi.binance.com/fapi/v1/klines"
           f"?symbol={SYMBOL}&interval={interval}&startTime={start_ms}"
           f"&endTime={end_ms}&limit={limit}")
    with _OPENER.open(url, timeout=20) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return raw, url


def last_head(con, tf):
    row = con.execute(
        "SELECT MAX(open_time) FROM candle_heads WHERE exchange=? AND market=? AND symbol=? "
        "AND timeframe=?", (EXCH, MKT, SYMBOL, tf)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def main() -> int:
    now = int(time.time() * 1000)
    out = {"symbol": SYMBOL, "now_ms": now, "steps": []}
    con = rc.db()
    try:
        for tf in ("1h", "4h"):
            iv = IV[tf]
            head = last_head(con, tf)
            start = (head + iv) if head else (now - 30 * 24 * 3600 * 1000)
            if start >= now:
                out["steps"].append({"tf": tf, "action": "already_current", "head": head})
                continue
            raw, url = fetch(tf, start, now, LIMIT[tf])
            bars = []
            for k in raw:
                open_t, close_t = int(k[0]), int(k[6])
                if close_t >= now:
                    continue                      # 未收盘，绝不入库
                bars.append({
                    "open_time": open_t, "close_time": close_t,
                    "open": k[1], "high": k[2], "low": k[3], "close": k[4],
                    "volume": k[5], "quote_volume": k[7],
                    "trade_count": int(k[8]),
                    "taker_buy_volume": k[9], "taker_buy_quote_volume": k[10],
                    "is_closed": 1, "available_at": now,
                })
            written, skipped = rc._store_closed_bars(con, EXCH, MKT, SYMBOL, tf,
                                                    bars, url, "rest")
            con.commit()
            out["steps"].append({
                "tf": tf, "head_before": head, "fetched": len(raw),
                "closed_bars": len(bars), "written": written, "skipped": skipped,
                "head_after": last_head(con, tf),
            })
    finally:
        con.close()

    # 复核：candles 视图里的最新收盘
    con = sqlite3.connect(f"file:{rc.DB}?mode=ro", uri=True)
    try:
        for tf in ("1h", "4h", "15m"):
            r = con.execute(
                "SELECT MAX(close_time) FROM candles WHERE exchange=? AND market=? AND symbol=? "
                "AND timeframe=? AND is_closed=1", (EXCH, MKT, SYMBOL, tf)).fetchone()
            out.setdefault("candles_max_close", {})[tf] = r[0]
    finally:
        con.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

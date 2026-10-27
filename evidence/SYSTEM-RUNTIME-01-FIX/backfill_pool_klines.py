# -*- coding: utf-8 -*-
"""全池 K 线补洞（1h / 4h）——走币安公开 REST，写库复用采集器 `_store_closed_bars`。

背景（本轮实测）：
  · 采集器 `realtime_collector.py` 每轮补缺预算只覆盖 10 个合约 ⇒ 全池 526 个标的里，
    只有 113 个跟到最新 1h（09:00），其余 415 个的 1h 停在 00:00，**中间断 8 根**；
  · 4h 更糟：96 个跟到 04:00，432 个停在 10-22 20:00。
  · 断档不补，扫低/回踩/突破的量能与 ATR 都算在错误的 bar 上 ⇒ 信号长期为 0。

与 `backfill_btc_klines.py` 的差别：
  · 不只看「尾巴落后」，而是**逐桶对齐找洞**：以 30 天窗口按周期对齐生成期望桶序列，
    与库内已有 open_time 求差，取**最早缺失桶**为起点回补（中间断档同样能补上）；
  · `_store_closed_bars` 本身对已存在的 head 跳过 ⇒ 重复拉取的旧桶不会覆盖既有行。

纪律：只写 `candle_versions` / `candle_heads`；不造数、不补空桶、不写未收盘 bar。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault(
    "MARKET_DB", str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"))

import realtime_collector as rc  # noqa: E402

EXCH = "binance"
MKT = "um"
IV = {"1h": 3_600_000, "4h": 14_400_000}
LOOKBACK_MS = 30 * 24 * 3600 * 1000
EXCLUDE = ("ALLUSDT", "BTCDOMUSDT")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def universe(con):
    rows = con.execute(
        "SELECT DISTINCT symbol FROM candle_heads WHERE exchange=? AND market=? "
        "AND timeframe='1h' ORDER BY symbol", (EXCH, MKT)).fetchall()
    return [r[0] for r in rows if r[0] not in EXCLUDE]


def expected_buckets(now_ms, iv):
    """30 天窗口内、已收盘的桶起点序列（对齐到周期边界）。"""
    last_close = now_ms - (now_ms % iv)          # 当前未收盘桶的起点
    start = last_close - (LOOKBACK_MS // iv) * iv
    return list(range(start, last_close, iv))


def existing(con, sym, tf, since_ms):
    rows = con.execute(
        "SELECT open_time FROM candles WHERE exchange=? AND market=? AND symbol=? "
        "AND timeframe=? AND open_time>=?", (EXCH, MKT, sym, tf, since_ms)).fetchall()
    return {int(r[0]) for r in rows}


def fetch(sym, interval, start_ms, end_ms, limit):
    url = ("https://fapi.binance.com/fapi/v1/klines"
           f"?symbol={sym}&interval={interval}&startTime={start_ms}"
           f"&endTime={end_ms}&limit={limit}")
    with _OPENER.open(url, timeout=25) as r:
        return json.loads(r.read().decode("utf-8")), url


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    now = int(time.time() * 1000)
    con = rc.db()
    report = {"now_ms": now, "universe": 0, "tf": {}, "errors": []}
    try:
        syms = universe(con)
        if only:
            syms = [s for s in syms if s == only.upper()]
        report["universe"] = len(syms)
        for tf in ("1h", "4h"):
            iv = IV[tf]
            exp = expected_buckets(now, iv)
            exp_set = set(exp)
            stat = {"symbols": 0, "already_ok": 0, "repaired": 0,
                    "bars_written": 0, "bars_skipped": 0, "req": 0,
                    "missing_bars_total": 0, "still_missing": 0}
            for i, sym in enumerate(syms, 1):
                have = existing(con, sym, tf, exp[0])
                missing = sorted(exp_set - have)
                if not missing:
                    stat["already_ok"] += 1
                    continue
                stat["symbols"] += 1
                stat["missing_bars_total"] += len(missing)
                start = missing[0]
                need = (now - start) // iv + 2
                limit = int(min(1000, max(50, need)))
                try:
                    raw, url = fetch(sym, tf, start, now, limit)
                    stat["req"] += 1
                except urllib.error.HTTPError as exc:
                    report["errors"].append(
                        {"symbol": sym, "tf": tf, "http": exc.code})
                    if exc.code in (418, 429):
                        time.sleep(30)
                    continue
                except Exception as exc:                     # noqa: BLE001
                    report["errors"].append(
                        {"symbol": sym, "tf": tf, "err": f"{type(exc).__name__}: {exc}"})
                    continue
                bars = []
                for k in raw:
                    open_t, close_t = int(k[0]), int(k[6])
                    if close_t >= now:
                        continue                              # 未收盘，绝不入库
                    bars.append({
                        "open_time": open_t, "close_time": close_t,
                        "open": k[1], "high": k[2], "low": k[3], "close": k[4],
                        "volume": k[5], "quote_volume": k[7],
                        "trade_count": int(k[8]),
                        "taker_buy_volume": k[9], "taker_buy_quote_volume": k[10],
                        "is_closed": 1, "available_at": now,
                    })
                if bars:
                    w, s = rc._store_closed_bars(con, EXCH, MKT, sym, tf, bars, url, "rest")
                    con.commit()
                    stat["bars_written"] += w
                    stat["bars_skipped"] += s
                    if w:
                        stat["repaired"] += 1
                if i % 40 == 0:
                    print(f"[{tf}] {i}/{len(syms)} 已补 {stat['repaired']} "
                          f"写入 {stat['bars_written']} 根", flush=True)
                time.sleep(0.05)                              # 约 20 req/s，远低于权重上限
            # 复核残留
            for sym in syms:
                have = existing(con, sym, tf, exp[0])
                if exp_set - have:
                    stat["still_missing"] += 1
            report["tf"][tf] = stat
            print(f"[{tf}] 完成 {stat}", flush=True)
    finally:
        con.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    out = ROOT / "evidence" / "SYSTEM-RUNTIME-01-FIX" / "backfill_pool_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

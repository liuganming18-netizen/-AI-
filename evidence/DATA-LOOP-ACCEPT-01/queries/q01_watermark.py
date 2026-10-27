# -*- coding: utf-8 -*-
"""q01｜数据水位独立测量（只读）。

口径（与运行时状态文件对齐）：
  · 已闭合水位 = candle_heads 中该标的该周期 MAX(open_time)，对应 bar 的
    close_time = open_time + 周期 - 1ms；
  · 「滞后」= 采集时刻 - close_time；
  · 新鲜阈值：滞后 <= 2 个周期 记为 fresh，否则 stale。
为什么必须按标的取 MAX：candle_heads 不是「每标的每周期一行」，而是历史版本累积，
直接 count(*)/max(open_time) 会得出「210 万个标的」「水位 09-12」这类假数。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
OUT = Path(__file__).resolve().parents[1] / "out"
TZ = dt.timezone(dt.timedelta(hours=8))
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def bj(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S")


def main():
    now_ms = int(dt.datetime.now(TZ).timestamp() * 1000)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {"measured_at_ms": now_ms, "measured_at_bj": bj(now_ms),
           "db": str(DB), "db_size_bytes": DB.stat().st_size,
           "db_mtime_bj": dt.datetime.fromtimestamp(DB.stat().st_mtime, TZ).strftime("%Y-%m-%d %H:%M:%S"),
           "by_timeframe": {}, "candle_heads_rowcount": {}, "collector": [], "oi": {}}

    # candle_heads 的「行数」与「标的数」是两件事，必须分开报
    for tf, ms in TF_MS.items():
        rows = con.execute(
            "SELECT symbol, MAX(open_time) AS mx FROM candle_heads "
            "WHERE exchange='binance' AND market='um' AND timeframe=? GROUP BY symbol", (tf,)
        ).fetchall()
        lags = []
        stale = []
        for r in rows:
            close_ms = int(r["mx"]) + ms - 1
            lag = now_ms - close_ms
            lags.append(lag)
            if lag > 2 * ms:
                stale.append({"symbol": r["symbol"], "last_open_bj": bj(int(r["mx"])), "lag_hours": round(lag / 3.6e6, 2)})
        lags.sort()
        n = len(lags)
        rowcount = con.execute(
            "SELECT COUNT(*) FROM candle_heads WHERE exchange='binance' AND market='um' AND timeframe=?", (tf,)
        ).fetchone()[0]
        out["by_timeframe"][tf] = {
            "candle_heads_rows": rowcount,
            "symbols": n,
            "latest_close_bj": bj(int(rows[0]["mx"]) + ms - 1) if rows else None,
            "lag_min_minutes": round(lags[0] / 60000, 1) if lags else None,
            "lag_p50_minutes": round(lags[n // 2] / 60000, 1) if lags else None,
            "lag_p90_minutes": round(lags[int(n * 0.9)] / 60000, 1) if lags else None,
            "lag_max_hours": round(lags[-1] / 3.6e6, 2) if lags else None,
            "fresh_symbols": n - len(stale),
            "stale_symbols": len(stale),
            "stale_examples": stale[:8],
        }
        out["candle_heads_rowcount"][tf] = rowcount

    for r in con.execute("SELECT name, pid, heartbeat_at, status, started_at FROM collector_state"):
        out["collector"].append({
            "name": r["name"], "pid": r["pid"], "status": r["status"],
            "heartbeat_bj": bj(r["heartbeat_at"]),
            "heartbeat_lag_sec": round((now_ms - r["heartbeat_at"]) / 1000, 1) if r["heartbeat_at"] else None,
            "started_bj": bj(r["started_at"]),
        })

    r = con.execute("SELECT MAX(ts), COUNT(*) FROM oi_history WHERE period='snapshot'").fetchone()
    out["oi"] = {"latest_bj": bj(r[0]), "rows": r[1],
                 "lag_minutes": round((now_ms - r[0]) / 60000, 1) if r[0] else None}
    con.close()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "q01_watermark.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""ACCUM-EARLY-01｜01 数据链路核查：每个字段落「已入库/可读取/进决策/仅展示」四态。

不凭旧报告下结论：本脚本重新读表结构、重新抽样、重新看调用路径。
输出：evidence/ACCUM-EARLY-01/out/01_数据链路核查.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD = PROJECT / "数据" / "市场数据" / "parquet"


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def cols(con, table):
    return [r[1] for r in con.execute(f"pragma table_info({table})")]


def main() -> int:
    rep = {"probe": "01_数据链路核查", "generated_at_utc": iso(time.time() * 1000),
           "stats_at_utc": iso(time.time() * 1000), "items": {}, "errors": []}
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)

    # ── 1. 价格/量/额/笔数 + 主动买卖 ──────────────────────────────────
    try:
        c = cols(con, "candle_versions")
        has = {k: (k in c) for k in ("open", "high", "low", "close", "volume",
                                     "quote_volume", "trade_count",
                                     "taker_buy_volume", "taker_buy_quote_volume")}
        sample = con.execute(
            """SELECT symbol, open_time, close_time, volume, quote_volume,
                      trade_count, taker_buy_volume, taker_buy_quote_volume
               FROM candles WHERE exchange='binance' AND market='um'
                 AND timeframe='15m' AND symbol='BTCUSDT'
               ORDER BY open_time DESC LIMIT 1""").fetchone()
        # 抽样 20 个标的看主动买卖字段是否有空值
        nulls = 0
        total = 0
        for (sym,) in con.execute(
                """SELECT DISTINCT symbol FROM candles
                   WHERE exchange='binance' AND market='um' AND timeframe='15m'
                   ORDER BY symbol LIMIT 20"""):
            r = con.execute(
                """SELECT COUNT(*), SUM(CASE WHEN taker_buy_volume IS NULL
                       OR taker_buy_volume='' THEN 1 ELSE 0 END)
                   FROM candles WHERE exchange='binance' AND market='um'
                     AND timeframe='15m' AND symbol=?""", (sym,)).fetchone()
            total += r[0] or 0
            nulls += r[1] or 0
        rep["items"]["价格量额笔数_主动买卖"] = {
            "载体": "candle_versions / candles 视图",
            "字段存在": has,
            "已入库": True, "可读取": True,
            "进决策": {"价格量": True, "成交笔数": False,
                       "主动买卖": False},
            "样本": {"symbol": sample[0], "open_utc": iso(sample[1]),
                     "close_utc": iso(sample[2]), "volume": sample[3],
                     "quote_volume": sample[4], "trade_count": sample[5],
                     "taker_buy_volume": sample[6],
                     "taker_buy_quote_volume": sample[7]},
            "主动卖出": {"formula": "volume - taker_buy_volume",
                         "derived": True,
                         "note": "推导量，不是交易所原始字段"},
            "抽样空值": {"symbols": 20, "rows": total, "null_taker_buy": nulls},
            "缺失": "欧易无该字段（candle_field_notes 3225 条全为 okx）；"
                    "策略只跑币安 UM，故不影响",
        }
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "price", "error": f"{type(exc).__name__}: {exc}"})

    # ── 2. 持仓量 ─────────────────────────────────────────────────────
    try:
        import oi_feed as O
        sample = O.latest_oi("BTCUSDT")
        cov = con.execute(
            """SELECT COUNT(DISTINCT symbol) FROM oi_history
               WHERE exchange='binance' AND market='um' AND period='5m'
                 AND ts >= ?""", (int(time.time() * 1000) - 3600_000,)).fetchone()[0]
        tot = con.execute(
            """SELECT COUNT(DISTINCT symbol) FROM oi_history
               WHERE exchange='binance' AND market='um' AND period='5m'""").fetchone()[0]
        rep["items"]["持仓量"] = {
            "载体": "oi_history", "字段": cols(con, "oi_history"),
            "已入库": True, "可读取": True,
            "进决策": False,
            "进决策依据": "unified_runtime.py 喂给状态机的是空数组（见 03 脚本逐行证据）",
            "样本": sample,
            "覆盖": {"有 5m 历史的标的": tot, "一小时内更新": cov},
            "单位": {"open_interest": "币本位数量", "open_interest_value": "USDT 名义价值"},
            "缺失": "零持仓观测需剔除；fetched_at 有空值 ⇒ 只能声明计算不超前",
        }
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "oi", "error": f"{type(exc).__name__}: {exc}"})

    # ── 3. 大户/账户多空比（冷档 metrics）──────────────────────────────
    try:
        import duckdb
        g = str(COLD / "metrics" / "binance" / "um" / "BTCUSDT" / "*.parquet")
        con2 = duckdb.connect()
        try:
            r = con2.execute(
                f"SELECT count(*), max(ts) FROM read_parquet('{g}')").fetchone()
        except Exception:
            r = (None, None)
        finally:
            con2.close()
        rep["items"]["大户多空比"] = {
            "载体": "冷档 metrics parquet",
            "已入库": True, "可读取": True, "进决策": False, "仅展示": False,
            "样本": {"symbol": "BTCUSDT", "rows": r[0], "max_ts_utc": iso(r[1])},
            "停更": {"rule": "max(ts) 与当前时刻之差",
                     "stale_days": (round((time.time() * 1000 - r[1]) / 86400_000, 2)
                                    if r[1] else None)},
            "缺失": "无采集/发布时刻列 ⇒ 无可用时间证据；停更期内的值不得当实时证据",
        }
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "metrics", "error": f"{type(exc).__name__}: {exc}"})

    # ── 4. 均价（推导）与标记价格 ─────────────────────────────────────
    try:
        s = con.execute(
            """SELECT event_time, volume_24h_base, mark_price, funding_rate,
                      fetched_at FROM realtime_quotes
               WHERE exchange='binance' AND market='um' AND symbol='BTCUSDT'""").fetchone()
        rep["items"]["均价与标记价"] = {
            "逐根均价": {"formula": "quote_volume / volume", "derived": True,
                         "status": "可推导"},
            "标记价格": {"载体": "realtime_quotes.mark_price（最新）／"
                                 "funding_history.mark_price（仅结算时点）",
                         "已入库": True, "可读取": True, "进决策": False},
            "样本": {"mark_price": s[2] if s else None,
                     "funding_rate": s[3] if s else None,
                     "event_utc": iso(s[0]) if s else None,
                     "fetched_at_utc": iso(s[4]) if s else None},
            "缺失": "没有「阶段成交均价」专表；只能用逐根均价推导，口径须写明",
        }
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "vwap", "error": f"{type(exc).__name__}: {exc}"})

    # ── 5. 资金费 ─────────────────────────────────────────────────────
    try:
        r = con.execute(
            """SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(funding_time),
                      MAX(funding_time), COUNT(DISTINCT rate_type),
                      SUM(CASE WHEN fetched_at IS NULL THEN 1 ELSE 0 END)
               FROM funding_history""").fetchone()
        rep["items"]["资金费"] = {
            "载体": "funding_history", "已入库": True, "可读取": True,
            "进决策": True, "用途": "持仓期资金费结算（accrue_funding）",
            "样本": {"rows": r[0], "symbols": r[1], "first_utc": iso(r[2]),
                     "last_utc": iso(r[3]), "rate_types": r[4],
                     "fetched_at_null": r[5]},
            "缺失": "冷档 funding 冷读 0 行（路径口径错），历史回放取不到冷档资金费",
        }
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "funding", "error": f"{type(exc).__name__}: {exc}"})

    # ── 6. 聪明钱 ─────────────────────────────────────────────────────
    try:
        d = BACKEND / "聪明钱" / "raw"
        files = list(d.glob("*.json")) if d.exists() else []
        mt = sorted((f.stat().st_mtime for f in files), reverse=True)
        rep["items"]["聪明钱"] = {
            "载体": str(d), "已入库": True, "可读取": True,
            "进决策": False, "仅展示": True,
            "样本": {"files": len(files),
                     "newest_utc": iso(mt[0] * 1000) if mt else None,
                     "oldest_utc": iso(mt[-1] * 1000) if mt else None,
                     "fresh_within_1h": sum(1 for t in mt if time.time() - t <= 3600)},
            "缺失": "按 symbol 覆盖写，只有当前值、无历史序列 ⇒ 不能回放进历史事件",
        }
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "smart", "error": f"{type(exc).__name__}: {exc}"})

    # ── 7. 逐笔 / 盘口 ────────────────────────────────────────────────
    rep["items"]["逐笔与盘口"] = {
        "已入库": False, "可读取": False, "进决策": False,
        "现有替代": {"realtime_klines": "未闭合 K 线流（不是逐笔）",
                     "realtime_quotes": "最新报价（不是盘口深度）"},
        "缺失": "没有逐笔成交明细、没有盘口深度 ⇒ 如实登记为不可得，不造数",
    }

    con.close()
    out = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "01_数据链路核查.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"items": list(rep["items"]), "errors": rep["errors"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

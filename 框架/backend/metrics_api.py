#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指标字段矩阵（**只读**）：把一个标的的「已有指标」按字段逐项摊开，并给出可核验的口径。

为什么单独成文件：`adapter.py` 正被其它窗口使用，本模块自带路由处理函数，
主文件只需一行分派（与 `public_market_read.py` 同一纪律）。

口径（逐条对应工单 DATA-UI-01）：
  1. **不把「字段名存在」当「数据可用」**：K 线表里的 `taker_buy_volume` 在欧易侧是
     **空字符串**、`trade_count` 是**占位 0**（欧易原生 K 线不含这两项）。
     本模块一律按 `NULL / 空串 / 非有限数` 判缺失，绝不把占位 0 当「0 笔成交」。
  2. **不用 0 冒充缺失**：缺失一律 `value=None` 且给出 `missing_reason`。
  3. **不用当前快照补历史**：`current`（当前快照/未收盘）与 `history`（已入库归档）
     分两个独立块，字段不互相填充。
  4. **比例 / 百分比 / 买入占比 三件事分开**：
       · `*_ls_ratio`、`taker_buy_sell_ratio` 单位是**倍**（无量纲比值）；
       · `taker_buy_share` 单位是**%**（主动买入量占总成交量的百分比）；
       · `funding_rate_*` 单位是**%/期**（官方原值是小数，本模块乘 100 并标注）。
  5. **聚合比率不是「聪明钱身份/金额/均价」**：多空比是**账户数或持仓额的聚合比值**，
     无身份、无金额、无均价。字段描述里写明，不提供任何改名后的伪字段。
  6. **资金费率当前/预测 与 已结算 分开**：
       · `funding_rate_current`：`/fapi/v1/premiumIndex.lastFundingRate`
         —— 本期**正在累计、将在 `next_funding_time` 结算**的费率（官方称 lastFundingRate）；
       · `funding_rate_settled`：`/fapi/v1/fundingRate` 的**已结算**历史（`rate_type='settled'`）。
     两者语义不同，绝不用其中一个填另一个。
  7. **周期覆盖按周期分列**：1d/12h/8h/4h/1h/30m/15m/5m 各自给「历史已存」与
     「实时在采」两个状态，不用一个总开关概括。
  8. **只含加密币**：分母来自 `symbol_classification` 的**官方分类**
     （币安 `underlyingType=COIN`；欧易 `instCategory=1`），排除 INDEX/EQUITY/COMMODITY，
     **不硬编码旧分母**。

只读边界：不写库、不写快照、不联网、不下单、不碰策略与账本。
"""
from __future__ import annotations

import json
import math
import hashlib
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.environ.get("DATA_HUB_PROJECT") or os.path.dirname(os.path.dirname(HERE))
DB_PATH = os.environ.get("MARKET_DB") or os.path.join(
    PROJECT_ROOT, "数据", "市场数据", "db", "market_v2.sqlite3")
ARCHIVE_ROOT = os.environ.get("MARKET_ARCHIVE") or os.path.join(
    PROJECT_ROOT, "数据", "市场数据", "parquet")

PERIODS = ("1d", "12h", "8h", "4h", "1h", "30m", "15m", "5m")
PERIOD_LABEL = {"1d": "日线", "12h": "12小时", "8h": "8小时", "4h": "4小时",
                "1h": "1小时", "30m": "30分钟", "15m": "15分钟", "5m": "5分钟"}
PERIOD_MS = {"1d": 86_400_000, "12h": 43_200_000, "8h": 28_800_000, "4h": 14_400_000,
             "1h": 3_600_000, "30m": 1_800_000, "15m": 900_000, "5m": 300_000}

# 各交易所的「原生周期」：欧易无 8h（官方无该频道，不合成）
NATIVE_PERIODS = {"binance": set(PERIODS), "okx": set(PERIODS) - {"8h"}}

# 缓存（避免每次刷新都全量扫 parquet）；只缓存**只读统计**，不含任何实时值
_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL_S = 60.0
# 未收盘实时 K 线单独用短缓存：值随撮合推进，长缓存会让界面「刷新」看到旧值
RT_CACHE_TTL_S = 5.0


# ────────────────────────────── 基础工具 ──────────────────────────────

def iso(ms):
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def _now_ms():
    return int(time.time() * 1000)


def num(value):
    """严格数值解析：`None` / 空串 / 非有限数 ⇒ `None`（**绝不返回 0**）。

    ⚠ 这里就是欧易侧 `taker_buy_volume=''` 的拦截点：空串会被判成缺失，
    不会因为 `float('')` 抛错或 `or 0` 而变成 0。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    text = str(value).strip()
    if not text:
        return None
    try:
        f = float(text)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _cache_get(key):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if not hit:
        return None
    at, value, ttl = hit
    if time.monotonic() - at > ttl:
        return None
    return value


def _cache_put(key, value, ttl=None):
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value,
                       CACHE_TTL_S if ttl is None else ttl)
    return value


def connect_ro():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


_DUCK = None
_DUCK_ERR = None
_DUCK_LOCK = threading.Lock()


def duck():
    """惰性 duckdb 连接（只读查询 parquet）。不可用 ⇒ 返回 (None, 原因)。"""
    global _DUCK, _DUCK_ERR
    if _DUCK is not None or _DUCK_ERR is not None:
        return _DUCK, _DUCK_ERR
    with _DUCK_LOCK:
        if _DUCK is not None or _DUCK_ERR is not None:
            return _DUCK, _DUCK_ERR
        try:
            import duckdb                                        # noqa: PLC0415
            con = duckdb.connect()
            con.execute("SET threads=2")
            _DUCK = con
        except Exception as exc:                                 # noqa: BLE001
            _DUCK_ERR = f"{type(exc).__name__}: {exc}"
    return _DUCK, _DUCK_ERR


def _sql_str(text):
    return "'" + str(text).replace("'", "''") + "'"


# ────────────────────────────── 官方分类口径 ──────────────────────────────

# 官方分类字段：两所口径不同，不能混用同一个值域
CRYPTO_CATEGORY = {
    "binance": ("underlyingType", ("COIN",)),
    "okx": ("instCategory", ("1",)),          # 1=Crypto 3=Stocks 4=Commodities
}


def pool_scope():
    """**加密永续**分母（来自官方分类，不硬编码）。

    返回 `{exchange: {total, trading, source, excluded:{...}}}`。
    「分母」= 该所**加密且处于活动范围**的合约数；INDEX 单列不并入。
    """
    key = "pool_scope"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    out = {}
    try:
        con = connect_ro()
    except sqlite3.Error as exc:
        return {"error": f"库不可读：{exc}"}
    try:
        for ex, (field, values) in CRYPTO_CATEGORY.items():
            rows = con.execute(
                "SELECT instrument_category, category_field, in_active_range, COUNT(*) "
                "FROM symbol_classification WHERE exchange=? "
                "GROUP BY 1,2,3", (ex,)).fetchall()
            total = 0
            excluded = {}
            for r in rows:
                cat, field_name, active, n = r[0], r[1], r[2], r[3]
                if field_name != field:
                    continue
                if cat in values and active == 1:
                    total += n
                else:
                    label = cat if cat not in values else f"{cat}(不在活动范围)"
                    excluded[label] = excluded.get(label, 0) + n
            freshness = con.execute(
                "SELECT MIN(snapshot_utc), MAX(updated_at) FROM symbol_classification WHERE exchange=?",
                (ex,)).fetchone()
            out[ex] = {
                "snapshot_utc": freshness[0],
                "updated_at": freshness[1],
                "timestamp_basis": "oldest_classification_snapshot",
                "total": total,
                "classification_field": field,
                "classification_values": list(values),
                "excluded": excluded,
                "source": "symbol_classification（交易所官方 exchangeInfo / instruments 快照）",
                "note": ("分母 = 官方分类为加密且在活动范围的合约数；"
                         "INDEX/EQUITY/COMMODITY 等按官方分类排除，不并入分母"),
            }
        out["combined_total"] = sum(v["total"] for k, v in out.items() if isinstance(v, dict))
        snapshots = [v.get("snapshot_utc") for v in out.values() if isinstance(v, dict) and v.get("snapshot_utc")]
        out["snapshot_utc"] = min(snapshots) if snapshots else None
        out["source_file"] = DB_PATH
    except sqlite3.Error as exc:
        return {"error": f"分类表读取失败：{exc}"}
    finally:
        con.close()
    return _cache_put(key, out)


def identity(exchange, mid):
    """标的身份 + 是否加密（**官方分类**，不看名字猜）。"""
    ex = (exchange or "").strip().lower()
    symbol = (mid or "").strip()
    if symbol.startswith(ex + ":"):
        symbol = symbol[len(ex) + 1:]
    info = {"exchange": ex, "market_id": symbol, "is_crypto": None,
            "instrument_category": None, "category_field": None,
            "in_active_range": None, "excluded_reason": None, "base_asset": None,
            "contract_type": None, "status": None, "source": None}
    try:
        con = connect_ro()
    except sqlite3.Error as exc:
        info["error"] = f"库不可读：{exc}"
        return info
    try:
        row = con.execute(
            "SELECT instrument_category, category_field, in_active_range, excluded_reason "
            "FROM symbol_classification WHERE exchange=? AND symbol=?",
            (ex, symbol)).fetchone()
        if row:
            cat, field_name = row["instrument_category"], row["category_field"]
            expect = CRYPTO_CATEGORY.get(ex, (None, ()))
            info.update({
                "instrument_category": cat, "category_field": field_name,
                "in_active_range": row["in_active_range"],
                "excluded_reason": row["excluded_reason"],
                "is_crypto": bool(field_name == expect[0] and cat in expect[1]),
                "source": "symbol_classification（官方分类快照）",
            })
        else:
            info["error"] = f"官方分类表无 {ex}:{symbol}"
        pool = con.execute(
            "SELECT base_asset, contract_type, status FROM symbol_pool "
            "WHERE exchange=? AND symbol=?", (ex, symbol)).fetchone()
        if pool:
            info.update({"base_asset": pool["base_asset"],
                         "contract_type": pool["contract_type"], "status": pool["status"]})
    except sqlite3.Error as exc:
        info["error"] = f"身份读取失败：{exc}"
    finally:
        con.close()
    return info


# ────────────────────────────── K 线归档（历史）──────────────────────────────

def _candle_glob(exchange, symbol, tf):
    market_dir = "um" if exchange == "binance" else "swap"
    base = os.path.join(ARCHIVE_ROOT, "candles", exchange, market_dir, symbol, tf)
    if not os.path.isdir(base):
        return None
    files = [os.path.join(base, f) for f in sorted(os.listdir(base)) if f.endswith(".parquet")]
    return files or None


# 空串/null 一律算缺失：用 nullif(trim(x),'')
_N = lambda col: f"try_cast(nullif(trim({col}), '') AS DOUBLE)"  # noqa: E731


def candle_history(exchange, symbol, tf):
    """单标的单周期的**已入库归档**统计（行数、UTC 起止、逐字段非空行数）。"""
    files = _candle_glob(exchange, symbol, tf)
    if not files:
        return {"available": False, "reason": "该周期无归档文件"}
    con, err = duck()
    if con is None:
        return {"available": False, "reason": f"归档读取组件不可用：{err}"}
    key = f"candle_hist:{exchange}:{symbol}:{tf}:{len(files)}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    file_list = "[" + ",".join(_sql_str(f) for f in files) + "]"
    sql = f"""
        SELECT COUNT(*) AS rows,
               MIN(open_time) AS first_open, MAX(open_time) AS last_open,
               SUM(CASE WHEN is_closed = 1 THEN 1 ELSE 0 END) AS closed_rows,
               COUNT({_N('volume')}) AS n_volume,
               COUNT({_N('quote_volume')}) AS n_quote_volume,
               COUNT({_N('taker_buy_volume')}) AS n_taker_buy_volume,
               COUNT({_N('taker_buy_quote_volume')}) AS n_taker_buy_quote_volume,
               COUNT(trade_count) AS n_trade_count,
               SUM(CASE WHEN trade_count = 0 THEN 1 ELSE 0 END) AS zero_trade_count
        FROM read_parquet({file_list})
    """
    try:
        row = con.execute(sql).fetchone()
    except Exception as exc:                                     # noqa: BLE001
        return {"available": False, "reason": f"归档查询失败：{type(exc).__name__}: {exc}"}
    rows = int(row[0] or 0)
    first_open, last_open = row[1], row[2]
    duration = PERIOD_MS.get(tf)
    expected = None
    if duration and first_open is not None and last_open is not None:
        expected = (int(last_open) - int(first_open)) // duration + 1
    out = {
        "available": rows > 0,
        "rows": rows,
        "first_open": iso(first_open), "first_open_ms": first_open,
        "last_open": iso(last_open), "last_open_ms": last_open,
        "expected_bars": expected,
        "missing_bars": (max(0, expected - rows) if expected is not None else None),
        "closed_rows": int(row[3] or 0),
        "nonnull": {
            "volume": int(row[4] or 0),
            "quote_volume": int(row[5] or 0),
            "taker_buy_volume": int(row[6] or 0),
            "taker_buy_quote_volume": int(row[7] or 0),
            "trade_count": int(row[8] or 0),
        },
        "zero_trade_count_rows": int(row[9] or 0),
        "files": len(files),
        "reason": None if rows else "归档文件存在但无数据行",
    }
    return _cache_put(key, out)


def last_closed_bar(exchange, symbol, tf):
    """归档中**最后一根已收盘** K 线的原始值（历史口径的“最新已知值”）。"""
    files = _candle_glob(exchange, symbol, tf)
    if not files:
        return {"available": False, "reason": "该周期无归档文件"}
    con, err = duck()
    if con is None:
        return {"available": False, "reason": f"归档读取组件不可用：{err}"}
    file_list = "[" + ",".join(_sql_str(f) for f in files) + "]"
    sql = f"""
        SELECT open_time, close_time, volume, quote_volume, taker_buy_volume,
               taker_buy_quote_volume, trade_count, is_closed
        FROM read_parquet({file_list})
        WHERE is_closed = 1
        ORDER BY open_time DESC LIMIT 1
    """
    try:
        row = con.execute(sql).fetchone()
    except Exception as exc:                                     # noqa: BLE001
        return {"available": False, "reason": f"归档查询失败：{type(exc).__name__}: {exc}"}
    if not row:
        return {"available": False, "reason": "归档内无已收盘 K 线"}
    return {
        "available": True,
        "open_time": iso(row[0]), "open_time_ms": row[0],
        "close_time": iso(row[1]), "close_time_ms": row[1],
        "volume": num(row[2]), "quote_volume": num(row[3]),
        "taker_buy_volume": num(row[4]), "taker_buy_quote_volume": num(row[5]),
        "trade_count": num(row[6]),
        "is_closed": bool(row[7]),
        "source": f"Parquet 归档 candles/{exchange}/{'um' if exchange == 'binance' else 'swap'}/{symbol}/{tf}",
    }


# ────────────────────────────── 实时层（未收盘）──────────────────────────────

def realtime_bar(exchange, symbol, tf):
    """`realtime_klines` 里的**当前未收盘** K 线（含主动买卖量）。"""
    key = f"rtbar:{exchange}:{symbol}:{tf}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    try:
        con = connect_ro()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"库不可读：{exc}"}
    try:
        row = con.execute(
            "SELECT open_time, close_time, volume, quote_volume, taker_buy_volume, "
            "taker_buy_quote_volume, trade_count, is_closed, event_time, received_at, source "
            "FROM realtime_klines WHERE exchange=? AND symbol=? AND timeframe=?",
            (exchange, symbol, tf)).fetchone()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"实时表读取失败：{exc}"}
    finally:
        con.close()
    if not row:
        return _cache_put(key, {"available": False, "reason": "该周期无实时 K 线（采集未覆盖）"})
    out = {
        "available": True,
        "open_time": iso(row["open_time"]), "open_time_ms": row["open_time"],
        "close_time": iso(row["close_time"]), "close_time_ms": row["close_time"],
        "volume": num(row["volume"]), "quote_volume": num(row["quote_volume"]),
        "taker_buy_volume": num(row["taker_buy_volume"]),
        "taker_buy_quote_volume": num(row["taker_buy_quote_volume"]),
        "trade_count": num(row["trade_count"]),
        "is_closed": bool(row["is_closed"]),
        "event_time": iso(row["event_time"]), "event_time_ms": row["event_time"],
        "received_at": iso(row["received_at"]), "received_at_ms": row["received_at"],
        "source": f"{row['source']}（本机实时层）",
    }
    # 未收盘 K 线随撮合推进，缓存只压瞬时并发，不能让「刷新」看到 60 秒前的旧值
    return _cache_put(key, out, ttl=RT_CACHE_TTL_S)


# ────────────────────────────── 比率快照（本机 JSON）──────────────────────────────

def ratio_snapshot(exchange, symbol):
    """读**采集器写入**的公开扩展快照（只读文件，不触发任何交易所请求）。"""
    try:
        import public_snapshot                                 # noqa: PLC0415
    except Exception as exc:                                     # noqa: BLE001
        return {"available": False, "reason": f"快照模块不可用：{type(exc).__name__}"}
    snap = public_snapshot.read(exchange, symbol)
    if not snap:
        return {"available": False,
                "reason": ("本机快照未覆盖该标的（采集器只按有界集合周期刷新），"
                           "接口只读快照、不触发交易所请求")}
    fresh = public_snapshot.freshness(snap)
    return {"available": True, "payload": snap, "freshness": fresh,
            "refreshed_at": snap.get("snapshot_refreshed_at"),
            "refreshed_at_ms": snap.get("snapshot_refreshed_at_ms")}


# ────────────────────────────── 资金费率 ──────────────────────────────

def funding_current(exchange, symbol):
    """**当前/预测**资金费率（未结算）：来自 `realtime_quotes`。"""
    try:
        con = connect_ro()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"库不可读：{exc}"}
    try:
        row = con.execute(
            "SELECT funding_rate, next_funding_time, mark_price, funding_event_time, "
            "funding_source, updated_at FROM realtime_quotes WHERE exchange=? AND symbol=?",
            (exchange, symbol)).fetchone()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"实时报价读取失败：{exc}"}
    finally:
        con.close()
    if not row:
        return {"available": False, "reason": "实时报价表无该标的"}
    rate = num(row["funding_rate"])
    if rate is None:
        return {"available": False, "reason": "实时报价存在但当前费率字段为空"}
    return {
        "available": True, "rate_decimal": rate, "rate_pct": rate * 100.0,
        "next_funding_time": iso(row["next_funding_time"]),
        "next_funding_time_ms": row["next_funding_time"],
        "mark_price": num(row["mark_price"]),
        "event_time": iso(row["funding_event_time"]),
        "event_time_ms": row["funding_event_time"],
        "updated_at": iso(row["updated_at"]), "updated_at_ms": row["updated_at"],
        "source": f"{row['funding_source']} /fapi/v1/premiumIndex（lastFundingRate + nextFundingTime）",
        "semantics": "本期**正在累计、将于下次结算时生效**的费率（官方 lastFundingRate），非已结算值",
    }


def funding_settled(exchange, symbol):
    """**已结算**资金费率：来自 `funding_history`（`rate_type='settled'`）。

    新鲜度不按固定秒数判：结算费率**只在结算事件发生时**才更新，
    8 小时结算的标的天然可能 8 小时没有新行。因此用**实测结算间隔**做阈值
    （取最近几条结算的间隔中位数 × 1.5），避免把正常标的误判成过期。
    """
    try:
        con = connect_ro()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"库不可读：{exc}"}
    try:
        rows = con.execute(
            "SELECT funding_time, funding_rate, mark_price, source, fetched_at "
            "FROM funding_history WHERE exchange=? AND symbol=? AND rate_type='settled' "
            "ORDER BY funding_time DESC LIMIT 6", (exchange, symbol)).fetchall()
        agg = con.execute(
            "SELECT COUNT(*) n, MIN(funding_time) f, MAX(funding_time) l "
            "FROM funding_history WHERE exchange=? AND symbol=? AND rate_type='settled'",
            (exchange, symbol)).fetchone()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"结算历史读取失败：{exc}"}
    finally:
        con.close()
    if not rows:
        return {"available": False, "reason": "无已结算费率记录"}
    row = rows[0]
    rate = num(row["funding_rate"])
    diffs = []
    for i in range(1, len(rows)):
        d = int(rows[i - 1]["funding_time"]) - int(rows[i]["funding_time"])
        if d > 0:
            diffs.append(d)
    interval_ms = None
    if diffs:
        diffs.sort()
        interval_ms = diffs[len(diffs) // 2]
    stale_after_s = (max(900.0, (interval_ms / 1000.0) * 1.5)
                     if interval_ms else 900.0)
    return {
        "available": rate is not None, "rate_decimal": rate,
        "rate_pct": (rate * 100.0 if rate is not None else None),
        "funding_time": iso(row["funding_time"]), "funding_time_ms": row["funding_time"],
        "mark_price": num(row["mark_price"]),
        "settled_count": int(agg["n"] or 0),
        "first_settled": iso(agg["f"]), "last_settled": iso(agg["l"]),
        "settle_interval_ms": interval_ms,
        "settle_interval_label": (f"{interval_ms / 3_600_000:g} 小时"
                                  if interval_ms else None),
        "stale_after_s": stale_after_s,
        "fetched_at": iso(row["fetched_at"]),
        "source": f"{row['source']} /fapi/v1/fundingRate（rate_type=settled）",
        "semantics": "**已结算**的历史费率，与当前/预测值严格区分，不互相填充",
        "reason": None if rate is not None else "费率字段为空",
    }


def oi_snapshot(exchange, symbol):
    """OI 数量与名义额：本机快照（实时）优先，缺失时回退 `oi_history` 快照分区。"""
    snap = ratio_snapshot(exchange, symbol)
    oi = {}
    oi_reason = None
    if snap.get("available"):
        oi = (snap["payload"] or {}).get("open_interest") or {}
        qty_v = num(oi.get("amount"))
        usd_v = num(oi.get("usd"))
        # `available:true` 但两个值都空 ⇒ 空壳块（上游被推迟时会这样写），
        # 不能当成"有值但为空"，继续回退库表拿上一份好值
        if oi.get("available") and (qty_v is not None or usd_v is not None):
            return {"available": True, "qty": qty_v, "notional_usd": usd_v,
                    "qty_raw": qty_v,
                    "qty_unit": "标的币（openInterestHist.sumOpenInterest）",
                    "qty_event_time": oi.get("amount_event_time"),
                    "notional_event_time": oi.get("usd_event_time"),
                    "fetched_at": oi.get("fetched_at"),
                    "source": oi.get("source"),
                    "layer": "snapshot", "freshness": snap.get("freshness"),
                    "reason": None}
        oi_reason = oi.get("reason") or "快照内 OI 块为空（等下一轮补齐）"
    # 回退：库内快照分区（诚实标注层级，不当实时值）
    try:
        con = connect_ro()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"库不可读：{exc}"}
    try:
        row = con.execute(
            "SELECT ts, open_interest, open_interest_value, oi_ccy, oi_usd, source, fetched_at "
            "FROM oi_history WHERE exchange=? AND symbol=? AND period='snapshot' "
            "ORDER BY ts DESC LIMIT 1", (exchange, symbol)).fetchone()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"OI 读取失败：{exc}"}
    finally:
        con.close()
    if not row:
        return {"available": False,
                "reason": oi_reason or snap.get("reason") or "无 OI 记录"}
    # 数量口径：**优先 `oi_ccy`（基础币数量）**。
    #   币安 `open_interest` 本身就是基础币（BTC 张=币），两列相同；
    #   欧易 `open_interest` 是**合约张数**（如 23,113,738 张），`oi_ccy` 才是基础币
    #   （231,137.38 OKB）。若直接取 `open_interest`，欧易持仓量会放大 100 倍。
    qty_base = num(row["oi_ccy"])
    qty_raw = num(row["open_interest"])
    return {"available": True,
            "qty": qty_base if qty_base is not None else qty_raw,
            "qty_raw": qty_raw,
            "qty_unit": ("标的币（oi_ccy 基础币口径）" if qty_base is not None
                         else "合约张数（上游原始口径）"),
            "notional_usd": num(row["open_interest_value"]) or num(row["oi_usd"]),
            "qty_event_time": iso(row["ts"]), "notional_event_time": iso(row["ts"]),
            "fetched_at": iso(row["fetched_at"]), "source": f"{row['source']}（oi_history 快照分区）",
            "layer": "db_snapshot", "freshness": None,
            "reason": None}


# 各交易所**原生是否提供**这些 K 线字段（实测结论，不是按接口文档猜的）：
#   · 币安 /fapi/v1/klines 原生带 takerBuyBaseAssetVolume / takerBuyQuoteAssetVolume / count；
#   · 欧易 /market/candles 只给 [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]，
#     **没有**主动买入量与成交笔数 ⇒ 归档里前者是空串、后者是占位 0。
#     占位 0 绝不能被读成「该周期 0 笔成交」。
NATIVE_FIELD_SUPPORT = {
    "binance": {"taker_buy_volume": True, "taker_buy_quote_volume": True,
                "trade_count": True, "volume": True, "quote_volume": True},
    "okx": {"taker_buy_volume": False, "taker_buy_quote_volume": False,
            "trade_count": False, "volume": True, "quote_volume": True},
}
UNSUPPORTED_REASON = {
    "taker_buy_volume": "**上游不支持**：欧易原生 K 线不返回主动买入量（归档内为空串，非 0）",
    "taker_buy_quote_volume": "**上游不支持**：欧易原生 K 线不返回主动买入成交额（归档内为空串，非 0）",
    "trade_count": ("**上游不支持**：欧易原生 K 线不返回成交笔数"
                    "（归档内为占位 0，不能当「0 笔成交」）"),
}


def field_supported(exchange, key):
    return NATIVE_FIELD_SUPPORT.get(exchange, {}).get(key, True)


# ────────────────────────────── 字段定义 ──────────────────────────────

# 每个字段：键、中文名、分组、单位、口径说明、派生规则
FIELD_SPECS = [
    # ── 订单流（来自 K 线原生字段）──
    dict(key="taker_buy_volume", name="主动买入量", group="订单流", unit="标的币",
         basis="该周期内**主动吃单（taker）买入**的成交量", derived=False),
    dict(key="taker_buy_quote_volume", name="主动买入成交额", group="订单流", unit="USDT",
         basis="该周期内主动买入的**计价币成交额**", derived=False),
    dict(key="volume", name="总成交量", group="订单流", unit="标的币",
         basis="该周期内全部成交量（主动买 + 主动卖）", derived=False),
    dict(key="quote_volume", name="总成交额", group="订单流", unit="USDT",
         basis="该周期内全部成交的计价币成交额", derived=False),
    dict(key="trade_count", name="成交笔数", group="订单流", unit="笔",
         basis="该周期内成交笔数", derived=False),
    dict(key="taker_sell_volume", name="主动卖出量", group="订单流", unit="标的币",
         basis="**派生**：总成交量 − 主动买入量（不含未分类残差，见 evidence）", derived=True),
    dict(key="taker_buy_sell_ratio", name="主动买卖量比", group="订单流", unit="倍",
         basis="**派生比值**：主动买入量 ÷ 主动卖出量；>1 主动买占优。不是百分比", derived=True),
    dict(key="taker_buy_share", name="主动买入占比", group="订单流", unit="%",
         basis="**派生百分比**：主动买入量 ÷ 总成交量 × 100；与「主动买卖量比」不是同一口径", derived=True),
    # ── 多空比（聚合比率）──
    dict(key="top_position_ls_ratio", name="大户持仓多空比", group="多空比", unit="倍",
         basis="**聚合比率**：Top 交易员多头持仓额 ÷ 空头持仓额（官方 topLongShortPositionRatio）；无身份、无金额、无均价", derived=False),
    dict(key="top_account_ls_ratio", name="大户账户多空比", group="多空比", unit="倍",
         basis="**聚合比率**：Top 交易员多头账户数 ÷ 空头账户数（官方 topLongShortAccountRatio）；无身份、无金额", derived=False),
    dict(key="global_account_ls_ratio", name="全市场账户多空比", group="多空比", unit="倍",
         basis="**聚合比率**：全市场多头账户数 ÷ 空头账户数（官方 globalLongShortAccountRatio）；无身份、无金额", derived=False),
    # ── 持仓量 ──
    dict(key="oi_qty", name="持仓量", group="持仓量", unit="标的币",
         basis="全市场未平仓合约**数量**（无多空拆分）", derived=False),
    dict(key="oi_notional_usd", name="持仓名义额", group="持仓量", unit="USD",
         basis="全市场未平仓合约**名义金额**（USD，无多空拆分）", derived=False),
    # ── 资金费率（当前/预测 与 已结算 分开）──
    dict(key="funding_rate_current", name="资金费率（当前/预测）", group="资金费率", unit="%/期",
         basis="**未结算**：本期正在累计、下次结算时生效的费率（premiumIndex.lastFundingRate）；与已结算值不同", derived=False),
    dict(key="funding_next_time", name="下次结算时间", group="资金费率", unit="UTC 时刻",
         basis="当前费率对应的**下次结算时刻**", derived=False),
    dict(key="funding_rate_settled", name="资金费率（已结算）", group="资金费率", unit="%/期",
         basis="**已结算**：最近一次实际结算的费率（fundingRate，rate_type=settled）", derived=False),
    dict(key="funding_settled_time", name="已结算时刻", group="资金费率", unit="UTC 时刻",
         basis="最近一次已结算费率的结算时刻", derived=False),
]

# 归档（历史）里能直接查到的原始列 → 字段键
HIST_KEYS = ("volume", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume", "trade_count")
# 实时层里能直接查到的原始列 → 字段键
RT_KEYS = HIST_KEYS

# 比率字段 → 快照 long_short 内的块名
SNAPSHOT_RATIO_BLOCKS = {
    "top_position_ls_ratio": "top_position_long_short_ratio",
    "top_account_ls_ratio": "top_account_long_short_ratio",
    "global_account_ls_ratio": "global_account_long_short_ratio",
}

# ── 轮转补采块的新鲜度阈值（DATA-UI-02 订正）────────────────────────────────
# 这两个块只有轮转窗口才取：一轮铺满 = ceil(标的数 / 每轮 N) × 单轮周期。
# **单轮周期实测 456s**（取数约 156s + 间隔 300s，2026-10-18 连续 8 轮均值），
# 62 标的 / 每轮 12 ⇒ 6 轮铺满 ≈ **38 分钟**。
# 若仍按 900s 判过期，「刚铺满时最早那批已经过期」在数学上永远成立
# —— 不是数据坏了，是阈值比轮转周期还短。故这几个块的阈值跟随轮转周期。
ROTATION_RATIO_KEYS = ("top_account_ls_ratio", "global_account_ls_ratio")
ROTATION_CYCLE_S = int(os.environ.get("PUBLIC_EXTRAS_ROTATION_CYCLE_S", "2400"))
ROTATION_STALE_S = int(ROTATION_CYCLE_S * 1.2)      # 2880s = 48 分钟

# 归档里能查到「比率」的唯一来源（币安官方归档 metrics，仅 5m 粒度）
METRICS_COLUMNS = {
    "top_position_ls_ratio": "top_position_ls_ratio",
    "top_account_ls_ratio": "count_toptrader_long_short_ratio",
    "global_account_ls_ratio": "retail_account_ls_ratio",
    "taker_ratio_hist": "sum_taker_long_short_vol_ratio",
    "oi_qty": "sum_open_interest",
    "oi_notional_usd": "sum_open_interest_value",
}


def _freshness(age_s, limit_s):
    if age_s is None:
        return "unknown"
    return "ok" if age_s <= limit_s else "stale"


def _row(key, spec, *, value=None, value_text=None, event_time=None, event_ms=None,
         fetched_at=None, fetched_ms=None, source=None, layer=None, reason=None,
         extra=None, stale_after_s=900, value_kind="number"):
    """构造一行字段。

    **新鲜度口径（重要）**：`age_seconds` 一律按「本机取到/写入该值的时刻」
    （`fetched_ms`，缺则退 `event_ms`）计算，**不按业务事件时刻**。
    原因：`period=1h` 的多空比其 `event_time` 是**该 1 小时的桶起点**，
    天然最多比现在早 1 小时；8 小时结算的费率天然最多早 8 小时。
    若拿事件时刻判过期，会把「刚刷新过的正确值」误标成过期。
    业务时刻距现在多久另给 `event_age_seconds`，供核对，不参与 ok/stale 判定。
    """
    now = _now_ms()
    age = None
    if fetched_ms is not None:
        age = max(0.0, round((now - int(fetched_ms)) / 1000.0, 1))
    elif event_ms is not None:
        age = max(0.0, round((now - int(event_ms)) / 1000.0, 1))
    event_age = (max(0.0, round((now - int(event_ms)) / 1000.0, 1))
                 if event_ms is not None else None)
    status = "missing" if value is None else _freshness(age, stale_after_s)
    out = {
        "key": key, "name": spec["name"], "group": spec["group"], "unit": spec["unit"],
        "basis": spec["basis"], "derived": spec["derived"], "value_kind": value_kind,
        "value": value, "value_text": value_text,
        "event_time": event_time, "fetched_at": fetched_at,
        "age_seconds": age, "event_age_seconds": event_age, "freshness": status,
        "source": source, "layer": layer,
        "missing_reason": reason,
    }
    if extra:
        out.update(extra)
    return out


def _fmt(value, digits=6):
    if value is None:
        return None
    a = abs(value)
    if a == 0:
        return "0"
    if a >= 1000:
        return f"{value:,.2f}"
    if a >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _fmt_unit(value, unit):
    """按单位决定小数位：笔数取整、百分比 6 位、比值 4 位。"""
    if value is None:
        return None
    if unit == "笔":
        return f"{int(round(value)):,}"
    if unit == "%/期":
        return f"{value:.6f}".rstrip("0").rstrip(".")
    if unit == "%":
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if unit == "倍":
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return _fmt(value)


# ────────────────────────────── 组装 ──────────────────────────────

def build_metrics(exchange, mid, params=None):
    """完整指标矩阵（当前值 + 历史覆盖 + 字段口径 + 证据）。"""
    params = params or {}
    ex = (exchange or "").strip().lower()
    symbol = (mid or "").strip()
    if symbol.startswith(ex + ":"):
        symbol = symbol[len(ex) + 1:]
    period = (params.get("period") or ["1h"])[0] or "1h"
    if period not in PERIODS:
        period = "1h"
    ident = identity(ex, symbol)
    native = NATIVE_PERIODS.get(ex, set(PERIODS))

    snap = ratio_snapshot(ex, symbol)
    snap_fresh = (snap.get("freshness") or {}) if snap.get("available") else {}
    ls_block = ((snap.get("payload") or {}).get("long_short") or {}) if snap.get("available") else {}

    hist_bar = last_closed_bar(ex, symbol, period)
    rt = realtime_bar(ex, symbol, period)
    oi = oi_snapshot(ex, symbol)
    fcur = funding_current(ex, symbol)
    fset = funding_settled(ex, symbol)

    specs = {s["key"]: s for s in FIELD_SPECS}
    rows = []

    # ① K 线原生字段：优先**当前未收盘**（实时层），否则退回最后一根已收盘（历史层）
    use_rt = bool(rt.get("available"))
    base = rt if use_rt else hist_bar
    layer_label = "realtime_forming" if use_rt else "archive_closed"
    if not base.get("available"):
        for k in RT_KEYS:
            rows.append(_row(k, specs[k], reason=base.get("reason") or "无 K 线数据",
                             layer=None, source=None))
    else:
        ev_ms = base.get("event_time_ms") or base.get("open_time_ms")
        src = base.get("source")
        note_extra = {"closed": bool(base.get("is_closed")),
                      "bar_open_time": base.get("open_time"),
                      "bar_close_time": base.get("close_time"),
                      "period": period,
                      "period_label": PERIOD_LABEL.get(period)}
        if use_rt:
            note_extra["note"] = ("当前为**未收盘** K 线累计值（随撮合推进），"
                                  "不是已定稿的历史值；已收盘历史见 coverage.archive")
            stale_limit = max(900, PERIOD_MS.get(period, 3_600_000) // 1000 * 2)
            fetched_ms = base.get("received_at_ms")
            fetched_at = base.get("received_at")
        else:
            note_extra["note"] = ("当前为归档中**最后一根已收盘** K 线（未收盘实时层缺该周期）")
            stale_limit = 900
            fetched_ms = None
            fetched_at = None
        for k in RT_KEYS:
            value = num(base.get(k))
            reason = None
            if not field_supported(ex, k):
                # 上游不提供 ⇒ 一律判缺失，**不采用**归档里的空串/占位 0
                value = None
                reason = UNSUPPORTED_REASON.get(k, "**上游不支持**：该所原生 K 线不含该字段")
            elif value is None:
                reason = "该周期该字段为空"
            elif k == "trade_count" and value == 0 and (num(base.get("volume")) or 0) > 0:
                # 防御：成交笔数为 0 但成交量>0 ⇒ 是占位值，不是真实 0 笔
                value = None
                reason = ("成交笔数为 0 但成交量>0 ⇒ 判定为**占位值**，"
                          "该所该字段不可用，不当作 0 笔")
            rows.append(_row(k, specs[k], value=value, value_text=_fmt_unit(value, specs[k]["unit"]),
                             event_time=base.get("event_time") or base.get("open_time"),
                             event_ms=ev_ms, fetched_at=fetched_at, fetched_ms=fetched_ms,
                             source=src, layer=layer_label, reason=reason,
                             extra=dict(note_extra), stale_after_s=stale_limit))

    # ② 派生：主动卖出量 / 主动买卖量比 / 主动买入占比
    tb = num(base.get("taker_buy_volume")) if base.get("available") else None
    vol = num(base.get("volume")) if base.get("available") else None
    sell = (vol - tb) if (vol is not None and tb is not None) else None
    ratio = (tb / sell) if (tb is not None and sell not in (None, 0)) else None
    share = ((tb / vol) * 100.0) if (tb is not None and vol not in (None, 0)) else None
    ev_ms = (base.get("event_time_ms") or base.get("open_time_ms")) if base.get("available") else None
    deriv_extra = {"period": period, "period_label": PERIOD_LABEL.get(period),
                   "closed": bool(base.get("is_closed")) if base.get("available") else None}
    for k, v, why in (
            ("taker_sell_volume", sell, "缺主动买入量或总成交量，无法派生（不填 0）"),
            ("taker_buy_sell_ratio", ratio, "缺主动买入量/主动卖出量，或主动卖出量为 0（比值无定义）"),
            ("taker_buy_share", share, "缺主动买入量或总成交量为 0，占比无定义")):
        if not field_supported(ex, "taker_buy_volume"):
            why = ("**上游不支持**：欧易原生 K 线不返回主动买入量 ⇒ 依赖它的派生指标"
                   "（主动卖出量/主动买卖量比/主动买入占比）均不可计算，不用其他数据顶替")
        rows.append(_row(k, specs[k], value=v, value_text=_fmt_unit(v, specs[k]["unit"]),
                         event_time=(base.get("event_time") or base.get("open_time")) if base.get("available") else None,
                         event_ms=ev_ms, source=(base.get("source") if base.get("available") else None),
                         layer=(layer_label if base.get("available") else None),
                         reason=None if v is not None else why, extra=dict(deriv_extra)))

    # ③ 多空比：只从**本机快照**取（实时），归档口径在 coverage 里单列
    for key, block_name in SNAPSHOT_RATIO_BLOCKS.items():
        block = ls_block.get(block_name) or {}
        if block.get("available"):
            raw = num(block.get("value"))
            rows.append(_row(
                key, specs[key], value=raw, value_text=_fmt_unit(raw, specs[key]["unit"]),
                event_time=block.get("event_time"),
                event_ms=_parse_iso_ms(block.get("event_time")),
                fetched_at=block.get("fetched_at"),
                fetched_ms=_parse_iso_ms(block.get("fetched_at")),
                source=("币安 /futures/data/" + {
                    "top_position_ls_ratio": "topLongShortPositionRatio",
                    "top_account_ls_ratio": "topLongShortAccountRatio",
                    "global_account_ls_ratio": "globalLongShortAccountRatio",
                }[key] + f"（period={block.get('period') or period}）"),
                layer="snapshot", reason=None,
                extra={"period": block.get("period") or period,
                       "period_label": PERIOD_LABEL.get(block.get("period") or period),
                       "snapshot_refreshed_at": snap.get("refreshed_at"),
                       "snapshot_status": snap_fresh.get("status"),
                       "stale_after_s": (ROTATION_STALE_S
                                         if key in ROTATION_RATIO_KEYS else 900),
                       "rotation_cycle_s": (ROTATION_CYCLE_S
                                            if key in ROTATION_RATIO_KEYS else None),
                       "note": ("聚合比率，非聪明钱身份/金额/均价；"
                                + (f"该块按轮转补采，铺满一轮约 {ROTATION_CYCLE_S // 60} 分钟"
                                   if key in ROTATION_RATIO_KEYS else ""))},
                stale_after_s=(ROTATION_STALE_S if key in ROTATION_RATIO_KEYS else 900)))
        else:
            reason = block.get("reason")
            if not reason:
                reason = (snap.get("reason") if not snap.get("available")
                          else "该比率块本次未取（采集轮转未覆盖到该标的）")
            if ex == "okx":
                reason = ("**上游不支持**：欧易无 `/futures/data/*` 同类多空比公开端点；"
                          "本系统不跨所拼接、不用其他指标冒充")
            rows.append(_row(key, specs[key], reason=reason, layer=None, source=None,
                             extra={"period": period,
                                    "snapshot_refreshed_at": snap.get("refreshed_at"),
                                    "snapshot_status": snap_fresh.get("status")}))

    # ④ OI
    if oi.get("available"):
        for k, v, et in (("oi_qty", oi.get("qty"), oi.get("qty_event_time")),
                         ("oi_notional_usd", oi.get("notional_usd"), oi.get("notional_event_time"))):
            rows.append(_row(k, specs[k], value=v, value_text=_fmt_unit(v, specs[k]["unit"]), event_time=et,
                             event_ms=_parse_iso_ms(et), fetched_at=oi.get("fetched_at"),
                             fetched_ms=_parse_iso_ms(oi.get("fetched_at")),
                             source=oi.get("source"), layer=oi.get("layer"),
                             reason=None if v is not None else "该字段为空",
                             extra={"snapshot_status": (oi.get("freshness") or {}).get("status"),
                                    "oi_qty_unit": oi.get("qty_unit"),
                                    "oi_qty_raw": oi.get("qty_raw"),
                                    "note": "全市场总量，无多空拆分"}))
    else:
        for k in ("oi_qty", "oi_notional_usd"):
            rows.append(_row(k, specs[k], reason=oi.get("reason"), layer=None, source=None))

    # ⑤ 资金费率：当前/预测 与 已结算 **分开两行**
    if fcur.get("available"):
        rows.append(_row("funding_rate_current", specs["funding_rate_current"],
                         value=fcur["rate_pct"], value_text=_fmt_unit(fcur["rate_pct"], specs["funding_rate_current"]["unit"]),
                         event_time=fcur.get("event_time"), event_ms=fcur.get("event_time_ms"),
                         fetched_at=fcur.get("updated_at"), fetched_ms=fcur.get("updated_at_ms"),
                         source=fcur.get("source"), layer="realtime_quote", reason=None,
                         extra={"rate_decimal": fcur["rate_decimal"],
                                "next_funding_time": fcur.get("next_funding_time"),
                                "mark_price": fcur.get("mark_price"),
                                "semantics": fcur.get("semantics")}))
        rows.append(_row("funding_next_time", specs["funding_next_time"],
                         value_text=fcur.get("next_funding_time"),
                         value=fcur.get("next_funding_time_ms"),
                         value_kind="timestamp",
                         event_time=fcur.get("next_funding_time"),
                         event_ms=fcur.get("next_funding_time_ms"),
                         fetched_at=fcur.get("updated_at"),
                         fetched_ms=fcur.get("updated_at_ms"),
                         source=fcur.get("source"), layer="realtime_quote",
                         reason=None if fcur.get("next_funding_time") else "下次结算时间缺失",
                         extra={"semantics": "当前费率的生效结算时刻"}))
    else:
        for k in ("funding_rate_current", "funding_next_time"):
            rows.append(_row(k, specs[k], reason=fcur.get("reason"), layer=None, source=None))

    if fset.get("available"):
        _settle_stale = fset.get("stale_after_s") or 900
        rows.append(_row("funding_rate_settled", specs["funding_rate_settled"],
                         value=fset["rate_pct"], value_text=_fmt_unit(fset["rate_pct"], specs["funding_rate_settled"]["unit"]),
                         event_time=fset.get("funding_time"),
                         event_ms=fset.get("funding_time_ms"),
                         fetched_at=fset.get("fetched_at"),
                         fetched_ms=_parse_iso_ms(fset.get("fetched_at")),
                         source=fset.get("source"), layer="funding_history", reason=None,
                         stale_after_s=_settle_stale,
                         extra={"rate_decimal": fset["rate_decimal"],
                                "settled_count": fset.get("settled_count"),
                                "first_settled": fset.get("first_settled"),
                                "settle_interval": fset.get("settle_interval_label"),
                                "stale_after_s": _settle_stale,
                                "semantics": fset.get("semantics")}))
        rows.append(_row("funding_settled_time", specs["funding_settled_time"],
                         value=fset.get("funding_time_ms"),
                         value_text=fset.get("funding_time"),
                         value_kind="timestamp",
                         event_time=fset.get("funding_time"),
                         event_ms=fset.get("funding_time_ms"),
                         fetched_at=fset.get("fetched_at"),
                         fetched_ms=_parse_iso_ms(fset.get("fetched_at")),
                         source=fset.get("source"), layer="funding_history", reason=None,
                         stale_after_s=_settle_stale,
                         extra={"settled_count": fset.get("settled_count")}))
    else:
        for k in ("funding_rate_settled", "funding_settled_time"):
            rows.append(_row(k, specs[k], reason=fset.get("reason"), layer=None, source=None))

    # ⑥ 周期覆盖：历史 vs 实时 **分开**
    coverage = []
    metrics_hist = metrics_history(ex, symbol)
    for tf in PERIODS:
        hist = candle_history(ex, symbol, tf)
        rtb = realtime_bar(ex, symbol, tf)
        supported = tf in native
        entry = {
            "period": tf, "period_label": PERIOD_LABEL[tf],
            "native_supported": supported,
            "archive": {
                "status": ("stored" if hist.get("available") else
                           ("unsupported" if not supported else "empty")),
                "rows": hist.get("rows"),
                "first": hist.get("first_open"), "last": hist.get("last_open"),
                "expected_bars": hist.get("expected_bars"),
                "missing_bars": hist.get("missing_bars"),
                "nonnull": hist.get("nonnull"),
                "zero_trade_count_rows": hist.get("zero_trade_count_rows"),
                "files": hist.get("files"),
                "reason": hist.get("reason") if not hist.get("available") else None,
            },
            "realtime": {
                "status": ("collecting" if rtb.get("available") else
                           ("unsupported" if not supported else "not_collected")),
                "open_time": rtb.get("open_time"), "is_closed": rtb.get("is_closed"),
                "event_time": rtb.get("event_time"), "received_at": rtb.get("received_at"),
                "has_taker_buy": rtb.get("taker_buy_volume") is not None,
                "trade_count": rtb.get("trade_count"),
                "reason": rtb.get("reason") if not rtb.get("available") else None,
            },
        }
        # 比率/持仓类指标在该周期的历史可用性（只有币安 5m 归档）
        # 官方 `/futures/data/*` 允许的 period 是 5m/15m/30m/1h/2h/4h/6h/12h/1d —— **不含 8h**。
        ratio_periods = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
        if metrics_hist.get("available") and tf == "5m":
            m_status = "stored"
        elif tf in ratio_periods:
            m_status = "requestable_not_collected"
        else:
            m_status = "unsupported"
        entry["metrics_archive"] = {
            "status": m_status,
            "granularity": "5m" if metrics_hist.get("available") else None,
            "note": ("币安官方归档只提供 **5m** 粒度的多空比/OI 指标；"
                     "其余周期可由 REST 按 period 请求（官方仅保留近 30 天），"
                     "本系统未逐周期落库，故不声称已存"
                     if metrics_hist.get("available") else
                     ("该周期不在官方 `/futures/data/*` 允许的 period 列表内（官方不含 8h）"
                      if tf not in ratio_periods else
                      "该所无多空比/OI 指标归档")),
        }
        coverage.append(entry)

    # 字段顺序固定为 FIELD_SPECS 声明顺序（界面按此渲染，避免字典插入顺序漂移）
    order = {s["key"]: i for i, s in enumerate(FIELD_SPECS)}
    rows.sort(key=lambda r: order.get(r["key"], 999))

    hist_keys = [k for k in rows if k["key"] in HIST_KEYS]
    out = {
        "exchange": ex, "market_id": symbol, "as_of": iso(_now_ms()),
        "identity": ident,
        "period": period, "period_label": PERIOD_LABEL.get(period),
        "fields": rows,
        "coverage": coverage,
        "counts": {
            "fields_total": len(rows),
            "available": sum(1 for r in rows if r["value"] is not None),
            "missing": sum(1 for r in rows if r["value"] is None),
            "stale": sum(1 for r in rows if r["freshness"] == "stale"),
            "derived": sum(1 for r in rows if r["derived"]),
        },
        "sources": {
            "archive": ARCHIVE_ROOT,
            "db": DB_PATH,
            "snapshot": (snap.get("payload") or {}).get("snapshot_refreshed_at") if snap.get("available") else None,
            "live_klines": "realtime_klines（统一采集进程原生 WS）",
            "funding_current": "realtime_quotes ← binance.premium_index",
            "funding_settled": "funding_history(rate_type=settled)",
        },
        "snapshot_state": {
            "available": bool(snap.get("available")),
            "refreshed_at": snap.get("refreshed_at"),
            "status": snap_fresh.get("status"),
            "note": snap_fresh.get("note") or snap.get("reason"),
        },
        "quality": {
            "status": ("ok" if rows and any(r["value"] is not None for r in rows) else "missing"),
            "age_seconds": None,
            "note": ("当前值来自实时层/本机快照；历史覆盖单列在 coverage，"
                     "两者不互相填充；缺失一律 null 并给出 missing_reason"),
        },
        "missing_reason": (None if any(r["value"] is not None for r in rows)
                           else "该标的全部指标均不可用（见各字段 missing_reason）"),
        "time": {
            "event_time": hist_keys[0]["event_time"] if hist_keys else None,
            "available_at": None,
            "received_at": (rt.get("received_at") if rt.get("available") else None),
            "fetched_at": (snap.get("refreshed_at") if snap.get("available") else None),
            "note": ("event_time=该值对应的业务时刻；available_at=外部最早可获知时刻（K 线=收盘时刻，"
                     "费率=结算时刻）；received_at=本机实时层收到时刻；fetched_at=上游返回时刻"),
        },
        "evidence": {
            "history_bar": hist_bar, "realtime_bar": rt,
            "snapshot_freshness": snap_fresh,
            "metrics_history": metrics_hist,
            "pool_scope": pool_scope().get(ex),
        },
    }
    return out


def _parse_iso_ms(text):
    if not text:
        return None
    try:
        return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


# ────────────────────────────── 指标归档（币安 5m）──────────────────────────────

def metrics_history(exchange, symbol):
    """币安官方归档 `metrics`（多空比 / OI / taker 比值），**只有 5m 粒度**。"""
    if exchange != "binance":
        return {"available": False, "reason": "该所无多空比/OI 指标归档（币安专有）"}
    base = os.path.join(ARCHIVE_ROOT, "metrics", "binance", "um", symbol)
    if not os.path.isdir(base):
        return {"available": False, "reason": "该标的无 metrics 归档目录"}
    files = [os.path.join(base, f) for f in sorted(os.listdir(base)) if f.endswith(".parquet")]
    if not files:
        return {"available": False, "reason": "metrics 归档目录为空"}
    key = f"metrics_hist:{symbol}:{len(files)}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    con, err = duck()
    if con is None:
        return {"available": False, "reason": f"归档读取组件不可用：{err}"}
    file_list = "[" + ",".join(_sql_str(f) for f in files) + "]"
    sql = f"""
        SELECT COUNT(*) AS rows, MIN(ts) AS mn, MAX(ts) AS mx,
               COUNT(sum_open_interest) AS oi,
               COUNT(sum_open_interest_value) AS oiv,
               COUNT(retail_account_ls_ratio) AS retail,
               COUNT(count_long_short_ratio) AS cnt_ls,
               COUNT(top_position_ls_ratio) AS toppos,
               COUNT(sum_toptrader_long_short_ratio) AS sum_top,
               COUNT(count_toptrader_long_short_ratio) AS cnt_top,
               COUNT(sum_taker_long_short_vol_ratio) AS taker,
               SUM(CASE WHEN retail_account_ls_ratio IS NOT NULL
                         AND count_long_short_ratio IS NOT NULL
                         AND retail_account_ls_ratio <> count_long_short_ratio
                        THEN 1 ELSE 0 END) AS retail_ne,
               SUM(CASE WHEN top_position_ls_ratio IS NOT NULL
                         AND sum_toptrader_long_short_ratio IS NOT NULL
                         AND top_position_ls_ratio <> sum_toptrader_long_short_ratio
                        THEN 1 ELSE 0 END) AS toppos_ne
        FROM read_parquet({file_list})
    """
    try:
        r = con.execute(sql).fetchone()
    except Exception as exc:                                     # noqa: BLE001
        return {"available": False, "reason": f"metrics 归档查询失败：{type(exc).__name__}"}
    rows = int(r[0] or 0)
    out = {
        "available": rows > 0, "rows": rows,
        "first": iso(r[1]), "last": iso(r[2]),
        "granularity": "5m",
        "files": len(files),
        "nonnull": {"sum_open_interest": int(r[3] or 0),
                    "sum_open_interest_value": int(r[4] or 0),
                    "retail_account_ls_ratio": int(r[5] or 0),
                    "count_long_short_ratio": int(r[6] or 0),
                    "top_position_ls_ratio": int(r[7] or 0),
                    "sum_toptrader_long_short_ratio": int(r[8] or 0),
                    "count_toptrader_long_short_ratio": int(r[9] or 0),
                    "sum_taker_long_short_vol_ratio": int(r[10] or 0)},
        "duplicate_columns": {
            "retail_account_ls_ratio == count_long_short_ratio": int(r[11] or 0) == 0,
            "top_position_ls_ratio == sum_toptrader_long_short_ratio": int(r[12] or 0) == 0,
            "note": ("归档内两对列**数值完全相同**（0 行不等）⇒ 是同一指标的两个列名，"
                     "统计口径时不得重复计数；"
                     "`count_toptrader_long_short_ratio` 才是独立的大户账户多空比"),
        },
        "reason": None if rows else "归档文件存在但无数据行",
    }
    return _cache_put(key, out)


# ────────────────────────────── 路由处理 ──────────────────────────────

def handle_metrics(exchange, mid, params):
    """`GET /api/v1/markets/{ex}/{mid}/metrics` —— 只读指标矩阵。"""
    try:
        return build_metrics(exchange, mid, params)
    except Exception as exc:                                     # noqa: BLE001
        # 构建失败必须**留痕**：此前这里静默返回 200 + 空字段集，
        # 客户端只看到「所有指标都缺」，查不出真正原因（DATA-UI-02 实测撞到过一次：
        # 某个标的整块字段为空，接口仍 200，错误信息只藏在 missing_reason 里）。
        import sys as _sys
        import traceback as _tb
        print(f"[metrics_api] build_metrics 失败 {exchange}:{mid} "
              f"{type(exc).__name__}: {exc}", file=_sys.stderr, flush=True)
        _tb.print_exc(file=_sys.stderr)
        return {"exchange": exchange, "market_id": mid, "as_of": iso(_now_ms()),
                "fields": [], "coverage": [], "available": False,
                "degraded": True,
                "missing_reason": f"指标矩阵构建失败：{type(exc).__name__}: {exc}",
                "quality": {"status": "error", "age_seconds": None,
                            "note": f"{type(exc).__name__}: {exc}"}}


def handle_pool_scope(params=None):
    """`GET /api/v1/data/pool-scope` —— 加密池分母（官方分类口径，不硬编码）。"""
    return pool_scope()


def _verify_cold_shards(candle: list[tuple[str, dict]], hash_sample_ratio: float = 0.05) -> dict:
    """冷库分片三层实检：存在性（全量）→ 行数（全量）→ sha256（抽样）。

    为什么三层都要做，而不是只 stat：
    - 只 stat 只能说「文件在」，说不出「文件是那个文件」—— 截断、覆盖、
      写一半都会让文件在而内容不对。
    - Parquet 的 footer 里带 `num_rows`，读它**不用读整份数据**（每个约 0.7ms），
      所以 1.25 万个分片全量校验只要约 4 秒 —— 这个成本完全可以承受。
    - sha256 要读全文（5.4 GB），全量约 60~120 秒，放请求路径不现实；
      按固定步长均匀抽 5%（约 620 个、250 MB、约 2.5 秒）足以发现
      「整批被替换/截断」这类系统性损坏。

    边界写死在返回值里：哈希是抽样，不是全量。调用方不得把
    `content_integrity_verified=True` 读成「5.4 GB 全部逐字节校验过」。
    """
    import pyarrow.parquet as pq

    root = ARCHIVE_ROOT
    t0 = time.monotonic()

    # ① 全量 stat
    missing: list[str] = []
    present = 0
    present_bytes = 0
    for rel, _meta in candle:
        try:
            st = os.stat(os.path.join(root, rel))
            present += 1
            present_bytes += st.st_size
        except OSError:
            missing.append(rel)
    # candles/<ex>/<market>/<symbol>/<tf>/<file>.parquet
    missing_symbols = sorted({
        rel.split("/")[3] for rel in missing
        if len(rel.split("/")) >= 5 and rel.startswith("candles/")
    })
    declared_bytes = sum(int(v.get("bytes", 0) or 0) for _, v in candle)
    stat_elapsed = time.monotonic() - t0

    # ② 全量行数（读 Parquet footer，不读数据页）
    t1 = time.monotonic()
    rows_match = 0
    rows_mismatch: list[dict] = []
    read_errors: list[dict] = []
    declared_rows_sum = 0
    actual_rows_sum = 0
    for rel, meta in candle:
        declared = int(meta.get("rows", 0) or 0)
        declared_rows_sum += declared
        try:
            actual = pq.ParquetFile(os.path.join(root, rel)).metadata.num_rows
        except Exception as exc:                                # noqa: BLE001
            read_errors.append({"file": rel, "error": f"{type(exc).__name__}: {exc}"})
            continue
        actual_rows_sum += actual
        if actual == declared:
            rows_match += 1
        else:
            rows_mismatch.append({"file": rel, "actual": actual,
                                  "declared": declared, "delta": actual - declared})
    rows_elapsed = time.monotonic() - t1

    # ③ sha256 均匀抽样（固定步长，不是随机 —— 随机抽样不可复现）
    t2 = time.monotonic()
    n = len(candle)
    k = max(1, int(n * hash_sample_ratio))
    step = max(1, n // k)
    sample = candle[::step][:k]
    hash_match = 0
    hash_mismatch: list[dict] = []
    hash_bytes_read = 0
    for rel, meta in sample:
        expected = meta.get("sha256")
        try:
            digest = hashlib.sha256()
            with open(os.path.join(root, rel), "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    digest.update(chunk)
                    hash_bytes_read += len(chunk)
            actual = digest.hexdigest()
        except OSError as exc:
            hash_mismatch.append({"file": rel, "error": str(exc)})
            continue
        if expected and actual == expected:
            hash_match += 1
        else:
            hash_mismatch.append({"file": rel, "actual": actual, "declared": expected})
    hash_elapsed = time.monotonic() - t2

    all_ok = (not missing and not rows_mismatch and not read_errors and not hash_mismatch)
    return {
        "method": ("存在性全量 os.stat；行数全量读 Parquet footer（num_rows）；"
                   f"sha256 均匀抽样 {hash_sample_ratio:.0%}"),
        "checked_at_utc": iso(_now_ms()),
        "declared": n,
        "present": present,
        "missing": len(missing),
        "missing_samples": missing[:10],
        "missing_symbols": missing_symbols,
        "declared_bytes": declared_bytes,
        "present_bytes": present_bytes,
        "byte_delta": declared_bytes - present_bytes,
        "rows": {
            "checked": len(candle) - len(read_errors),
            "match": rows_match,
            "mismatch": len(rows_mismatch),
            "read_errors": len(read_errors),
            "declared_sum": declared_rows_sum,
            "actual_sum": actual_rows_sum,
            "delta_sum": actual_rows_sum - declared_rows_sum,
            "mismatch_samples": rows_mismatch[:10],
            "error_samples": read_errors[:5],
        },
        "hash_sample": {
            "ratio": hash_sample_ratio,
            "checked": len(sample),
            "match": hash_match,
            "mismatch": len(hash_mismatch),
            "bytes_read": hash_bytes_read,
            "mismatch_samples": hash_mismatch[:5],
        },
        "content_integrity_verified": all_ok,
        "verified_scope": ("存在性=全量；行数=全量；内容哈希=抽样"
                           if all_ok else "存在未全部通过，见各分项"),
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "elapsed_breakdown_ms": {
            "stat": int(stat_elapsed * 1000),
            "rows": int(rows_elapsed * 1000),
            "hash_sample": int(hash_elapsed * 1000),
        },
        "note": ("哈希只覆盖抽样部分，未对 5.4 GB 内容逐字节全量校验；"
                 "「内容完整性已验证」指的是存在性+行数全量通过、哈希抽样无异常。"),
    }


def handle_cold_archive_status(params=None):
    """只读冷层索引摘要 + 分片三层实检（存在性/行数/哈希抽样）。

    演进过程（留在这里，避免下次又退回到「只报 indexed」）：
    1. 最早这里只报 `status=indexed`（catalog 文件能读），前端只能说
       「分片存在性未验证」—— 一个查不出东西的「未验证」。
    2. 改成逐个 stat 后，立刻查出声明 12551 个里有 **53 个文件不在盘上**
       （ALLUSDT 14 个 + BTCDOMUSDT 39 个，都是指数类合约、本就被排除在
       策略池外）。修 catalog 删掉这 53 条过期声明后，缺失归零。
    3. 现在补上「全量行数 + 抽样哈希」：只 stat 证明不了「文件还是那个文件」。
       行数全量 4 秒、哈希 5% 抽样 2.5 秒，代价可接受。

    性能：整轮约 7 秒，所以 TTL 给 600 秒，并由 `adapter._prewarm` 在启动后
    后台跑一次 —— 真实请求读到的是缓存，不吃这 7 秒。
    """
    key = "cold_archive_status"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    catalog_path = os.path.join(ARCHIVE_ROOT, "_catalog.json")
    state_path = os.path.join(ARCHIVE_ROOT, "_state.json")
    out = {"status": "unverified", "source": "_catalog.json + _state.json",
           "archive_root": ARCHIVE_ROOT, "coverage_semantics":
           "catalog 声明的分片/行数，不等于逐文件存在性、内容完整性或全市场覆盖验收",
           "as_of": iso(_now_ms())}
    try:
        with open(catalog_path, "rb") as f:
            raw = f.read()
        catalog = json.loads(raw)
        shards = catalog.get("shards")
        if not isinstance(shards, dict) or not shards:
            raise ValueError("catalog shards 缺失或为空")
        state = {}
        if os.path.isfile(state_path):
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        candle = [(p, v) for p, v in shards.items() if p.startswith("candles/")]

        existence = _verify_cold_shards(candle)
        declared_bytes = existence["declared_bytes"]

        out.update({"status": "indexed" if candle else "unverified",
                    "catalog_updated_at": catalog.get("updated_at"),
                    "catalog_mtime_utc": datetime.fromtimestamp(os.path.getmtime(catalog_path), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "catalog_sha256": hashlib.sha256(raw).hexdigest(),
                    "shard_count": len(shards),
                    "candle_shard_count": len(candle),
                    "declared_candle_rows": sum(int(v.get("rows", 0) or 0) for _, v in candle),
                    "declared_candle_bytes": declared_bytes,
                    "shard_existence": existence,
                    "catalog_pruned": catalog.get("pruned"),
                    "state_watermark_ms": state.get("watermark_ms"),
                    "reconcile_watermark_iso": state.get("reconcile_watermark_iso"),
                    "archive_verified_upto_ms": state.get("archive_verified_upto_ms"),
                    "limitations": ["内容哈希为 5% 抽样，未对 5.4 GB 逐字节全量校验",
                                    "不与热库合并为覆盖率",
                                    "catalog 声明的是归档范围，不代表全市场覆盖"]})
    except Exception as exc:
        out.update({"status": "unverified", "reason": f"{type(exc).__name__}: {exc}"})
    # 整轮实检约 7 秒（stat 0.5s + 行数 4s + 哈希抽样 2.5s），
    # 分片增减是按天发生的，TTL 给 600 秒；启动时由 _prewarm 先跑一遍。
    return _cache_put(key, out, ttl=600.0)


def handle_paper_reconciliation_evidence(params=None):
    """只读展示历史机制对账证据；缺失/陈旧/结构不符均保持未验证。"""
    hit = _cache_get("paper_reconciliation_evidence")
    if hit is not None:
        return hit
    path = os.path.join(PROJECT_ROOT, "evidence", "CODEX-CLOSEOUT-01", "out", "ui_paper_reconciliation.json")
    out = {"status": "unverified", "source": path,
           "claim_boundary": "历史样本机制闭环不代表当前自然行情成交或策略有效性"}
    try:
        st = os.stat(path)
        with open(path, "rb") as f:
            raw = f.read()
        data = json.loads(raw)
        generated = data.get("generated_at_utc")
        try:
            generated_dt = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
            generated_age_s = (datetime.now(timezone.utc) - generated_dt).total_seconds()
        except (TypeError, ValueError):
            generated_age_s = None
        run_id = str(data.get("run_id") or "")
        run_id_safe = bool(run_id) and run_id == os.path.basename(run_id) and run_id not in (".", "..")
        run_dir = os.path.join(PROJECT_ROOT, "回测日志", "event_trend_v0_1", "runs", run_id) if run_id_safe else ""
        hashes = data.get("source_hashes")
        hash_results = {}
        bad_path = False
        if isinstance(hashes, dict):
            for rel, expected in hashes.items():
                normalized = os.path.normpath(str(rel))
                target = os.path.realpath(os.path.join(PROJECT_ROOT, normalized))
                if (os.path.isabs(str(rel)) or normalized == ".." or normalized.startswith(".." + os.sep) or
                        os.path.commonpath([os.path.realpath(PROJECT_ROOT), target]) != os.path.realpath(PROJECT_ROOT)):
                    bad_path = True
                    hash_results[str(rel)] = {"status": "invalid_path"}
                    continue
                try:
                    digest = hashlib.sha256()
                    with open(target, "rb") as f:
                        for chunk in iter(lambda: f.read(1024 * 1024), b""):
                            digest.update(chunk)
                    actual = digest.hexdigest()
                    hash_results[str(rel)] = {"status": "match" if actual == expected else "mismatch",
                                              "sha256": actual}
                except OSError as exc:
                    hash_results[str(rel)] = {"status": "missing", "reason": str(exc)}
        names = [os.path.basename(str(p)) for p in hashes] if isinstance(hashes, dict) else []
        required_names = {"BacktestWorkbench.tsx", "backtest_api.py", "ledger_links.py",
                          "backtest_runner.py", "trades.jsonl", "summary.json", "paper_3a.sqlite3"}
        required_present = required_names.issubset(set(names))
        hashes_ok = (isinstance(hashes, dict) and bool(hashes) and not bad_path and required_present and
                     all(r.get("status") == "match" for r in hash_results.values()))
        valid = (run_id_safe and os.path.isdir(run_dir) and
                 data.get("passed") == 17 and data.get("total") == 17 and
                 len(data.get("checks", [])) == 17 and
                 all(c.get("pass") is True for c in data.get("checks", [])) and
                 data.get("scope_kind") == "historical_sample_mechanism" and
                 generated_age_s is not None and 0 <= generated_age_s <= 48 * 3600 and hashes_ok)
        out.update({"status": "verified_historical_sample" if valid else "unverified",
                    "generated_at_utc": generated,
                    "file_mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "age_seconds": generated_age_s, "stale_after_seconds": 172800,
                    "evidence_sha256": hashlib.sha256(raw).hexdigest(),
                    "run_id": data.get("run_id"), "rule": data.get("rule"),
                    "run_directory_exists": os.path.isdir(run_dir),
                    "source_hashes": hash_results,
                    "passed": data.get("passed"), "total": data.get("total"),
                    "scope_kind": data.get("scope_kind"), "scope": data.get("scope"), "clock_note": data.get("clock_note"),
                    # 复算说明：只做披露，**不参与** valid 判定。
                    # 用途：某个源文件在 run 产出后被改过、于是重算了一次时，把「改了什么、
                    # 为什么改、为何不影响对账结论」写出来。没有它，哈希对上反而会掩盖漂移。
                    "reverify_note": data.get("reverify_note"),
                    "reason": None if valid else "需含生成时刻（非未来、48小时内）、historical_sample_mechanism范围、现存run目录、17/17断言及全部必需source_hashes一致",
                    "claim_boundary": "历史样本机制闭环不代表当前自然行情成交或策略有效性"})
    except Exception as exc:
        out["reason"] = f"{type(exc).__name__}: {exc}"
    return _cache_put("paper_reconciliation_evidence", out, ttl=15.0)


def handle_field_contract(params=None):
    """`GET /api/v1/data/field-contract` —— **机器可读字段契约**（供离线研究复用）。"""
    return field_contract()


def field_contract():
    """字段契约：键、中文名、单位、口径、来源、时间语义、是否派生。"""
    return {
        "contract_version": "DATA-UI-01/v1",
        "as_of": iso(_now_ms()),
        "periods": list(PERIODS),
        "period_labels": PERIOD_LABEL,
        "exchanges": {"binance": {"market": "um", "native_periods": sorted(NATIVE_PERIODS["binance"]),
                                  "classification_field": "underlyingType",
                                  "classification_value": "COIN"},
                      "okx": {"market": "swap", "native_periods": sorted(NATIVE_PERIODS["okx"]),
                              "classification_field": "instCategory",
                              "classification_value": "1"}},
        "fields": [
            {"key": s["key"], "name_zh": s["name"], "group": s["group"], "unit": s["unit"],
             "basis": s["basis"], "derived": s["derived"],
             "ratio_kind": ("percent" if s["unit"] == "%" else
                            "ratio" if s["unit"] == "倍" else
                            "rate_per_period" if s["unit"] == "%/期" else
                            "timestamp" if s["unit"] == "UTC 时刻" else "amount"),
             "sources": _field_sources(s["key"]),
             "time_semantics": {
                 "event_time": "该值对应的业务时刻（K 线=K 线开盘时刻；费率=结算/生效时刻）",
                 "available_at": "外部最早可获知时刻（K 线=收盘时刻；结算费率=结算时刻）",
                 "fetched_at": "本机向上游取到该值的时刻",
                 "received_at": "本机实时层收到的时刻",
             },
             "missing_policy": "缺失一律 null + missing_reason；**不得**用 0、模拟值或当前快照补历史",
            } for s in FIELD_SPECS
        ],
        "ratio_vs_percent": {
            "taker_buy_sell_ratio": "倍：主动买入量 ÷ 主动卖出量",
            "taker_buy_share": "%：主动买入量 ÷ 总成交量 × 100",
            "note": "两者不可互换；界面必须显示单位，不得只给数字",
        },
        "not_smart_money": ("top_position_ls_ratio / top_account_ls_ratio / global_account_ls_ratio "
                            "均为**聚合比率**，无身份、无金额、无均价；"
                            "不得改名为「聪明钱金额/均价/身份」"),
        "funding_split": {
            "funding_rate_current": "未结算：premiumIndex.lastFundingRate（下次结算生效）",
            "funding_rate_settled": "已结算：fundingRate（rate_type=settled）",
            "note": "两者语义不同，禁止互相填充",
        },
        "known_limits": [
            "币安多空比/OI 指标归档只有 5m 粒度；其他周期可由 REST 请求但官方仅保留近 30 天，本系统未逐周期落库",
            "欧易原生 K 线不含主动买入量与成交笔数（归档为空串/占位 0），判为上游不支持",
            "欧易无 /futures/data/* 多空比端点，判为上游不支持，不跨所拼接",
            "本机比率快照按有界标的集合轮转刷新，未覆盖的标的如实标 missing",
        ],
    }


def _field_sources(key):
    mapping = {
        "taker_buy_volume": ["binance /fapi/v1/klines.takerBuyBaseAssetVolume",
                             "okx 不支持", "归档 parquet candles/*"],
        "taker_buy_quote_volume": ["binance /fapi/v1/klines.takerBuyQuoteAssetVolume",
                                   "okx 不支持", "归档 parquet candles/*"],
        "volume": ["binance /fapi/v1/klines.volume", "okx /market/candles vol",
                   "归档 parquet candles/*"],
        "quote_volume": ["binance /fapi/v1/klines.quoteAssetVolume",
                         "okx /market/candles volCcyQuote", "归档 parquet candles/*"],
        "trade_count": ["binance /fapi/v1/klines.count", "okx 不支持",
                        "归档 parquet candles/*（欧易为占位 0）"],
        "taker_sell_volume": ["派生：volume - taker_buy_volume"],
        "taker_buy_sell_ratio": ["派生：taker_buy_volume / taker_sell_volume"],
        "taker_buy_share": ["派生：taker_buy_volume / volume × 100"],
        "top_position_ls_ratio": ["binance /futures/data/topLongShortPositionRatio",
                                  "归档 metrics.top_position_ls_ratio（仅 5m）", "okx 不支持"],
        "top_account_ls_ratio": ["binance /futures/data/topLongShortAccountRatio",
                                 "归档 metrics.count_toptrader_long_short_ratio（仅 5m）", "okx 不支持"],
        "global_account_ls_ratio": ["binance /futures/data/globalLongShortAccountRatio",
                                    "归档 metrics.retail_account_ls_ratio（仅 5m）", "okx 不支持"],
        "oi_qty": ["binance /futures/data/openInterestHist.sumOpenInterest",
                   "归档 metrics.sum_open_interest", "okx /api/v5/public/open-interest.oi"],
        "oi_notional_usd": ["binance /futures/data/openInterestHist.sumOpenInterestValue",
                            "归档 metrics.sum_open_interest_value", "okx oiUsd"],
        "funding_rate_current": ["binance /fapi/v1/premiumIndex.lastFundingRate → realtime_quotes.funding_rate"],
        "funding_next_time": ["binance /fapi/v1/premiumIndex.nextFundingTime → realtime_quotes.next_funding_time"],
        "funding_rate_settled": ["binance /fapi/v1/fundingRate → funding_history(rate_type=settled)"],
        "funding_settled_time": ["binance /fapi/v1/fundingRate → funding_history.funding_time"],
    }
    return mapping.get(key, [])


if __name__ == "__main__":
    import sys
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("用法: python metrics_api.py <exchange> <market_id> [period]")
        print("      python metrics_api.py --contract    # 输出字段契约")
        print("      python metrics_api.py --pool        # 输出加密池分母")
        raise SystemExit(0)
    if argv[0] == "--contract":
        print(json.dumps(field_contract(), ensure_ascii=False, indent=1))
        raise SystemExit(0)
    if argv[0] == "--pool":
        print(json.dumps(pool_scope(), ensure_ascii=False, indent=1))
        raise SystemExit(0)
    ex = argv[0]
    mid = argv[1] if len(argv) > 1 else "BTCUSDT"
    per = argv[2] if len(argv) > 2 else "1h"
    print(json.dumps(handle_metrics(ex, mid, {"period": [per]}), ensure_ascii=False, indent=1))

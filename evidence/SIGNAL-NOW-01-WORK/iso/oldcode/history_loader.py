# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜冷层(Parquet) + 热库(SQLite) 合并历史入口。

**只做一件事**：把项目**已有**的两层行情按**既定来源规则**合并成一份「按标的、
按周期、时间升序、无重复、已收盘」的历史序列，供生产初始化（bootstrap）使用。

为什么需要它（本单要解决的问题）：
  · 热库 `market_v2.sqlite3` 只是**滚动保留窗口**（实测 1h 仅 ~47.9 天），而生产
    `run_forever()` 声明的预热窗口是 `atr_warmup_bars*4 + breakout_max_total_hours
    + cooling_hours = 1160h = 48.3 天` ⇒ **热库单独盖不满自己声明的窗口**
    （实测短 ~11h，且有标的 `atr_bars_seen=143`）。冷层 parquet 覆盖更早，
    但生产读路径 `PoolMarketWatcher` 只查热库 ⇒ 冷层从未进入初始化。
  · 冷层与热库在时间上有重叠段，**必须按同一 key 去重**，否则同一根 K 线会进
    状态机两次（状态机虽自带去重，但 ATR 递推会被重复计数）。

来源规则（**与 `market_repository.MarketDataRepository` 完全一致，不另立一套**）：
  1. 冷热边界来自归档器写入的 `parquet/_state.json`（单一事实源），本模块只读它；
  2. 冷层 `candles/` 存 `candle_versions` 的**全版本**，必须 JOIN `candle_heads/`
     才是「头版本」语义，否则同一 `open_time` 的旧版本会被重复计入；
  3. 同一 `(symbol, timeframe, open_time)` 冷热都有时 **热层优先**（热层是生产
     实际在用的那一份，且更新更晚）；
  4. 只取 `is_closed=1` 且 `close_time <= end_ms`；
  5. **不下载、不写生产库、不补造缺口**。

与工单「不重复下载已有数据」的对应：本模块没有任何网络调用，只有本地文件读取。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
DAY_MS = 86_400_000


def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    import datetime as dt
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class MergedHistory:
    """冷+热合并读取器（只读；可重复调用，按参数缓存）。"""

    def __init__(self, hot_db, archive_root, exchange: str = "binance",
                 market: str = "um", log=print):
        self.hot_db = str(hot_db)
        self.archive_root = Path(archive_root)
        self.exchange = exchange
        self.market = market
        self.log = log
        self._cache: Dict[Tuple, Tuple[Dict[str, Dict[str, List[dict]]], dict]] = {}

    # ---------- 归档状态（与 market_repository.read_state 同一份） ----------
    def archive_state(self) -> dict:
        try:
            return json.loads((self.archive_root / "_state.json").read_text())
        except Exception:                                    # noqa: BLE001
            return {}

    # ---------- 冷层 ----------
    def _cold_iter(self, timeframe: str, start_ms: int, end_ms: int,
                   symbols: Optional[Sequence[str]] = None,
                   batch: int = 50_000) -> Iterable[Tuple]:
        """流式返回冷层头版本行（按 symbol, open_time 升序）。无分片时返回空。"""
        import duckdb
        base = self.archive_root
        shard_dir = base / "candles" / self.exchange / self.market
        glob = str(shard_dir / "*" / timeframe / "*.parquet")
        hglob = str(base / "candle_heads" / self.exchange / self.market / "*"
                    / timeframe / "*.parquet")
        if not next(shard_dir.glob(f"*/{timeframe}/*.parquet"), None):
            return
        con = duckdb.connect()
        try:
            con.execute("PRAGMA threads=4")
            sym_filter = " AND list_contains(?, v.symbol)" if symbols else ""
            q = (
                "SELECT v.symbol, v.open_time, v.close_time, v.open, v.high, v.low, v.close,"
                "       v.volume, v.quote_volume, v.source_id "
                f"FROM read_parquet('{glob}') v "
                f"JOIN read_parquet('{hglob}') h "
                "  USING(exchange,market,symbol,timeframe,open_time,source_id) "
                "WHERE v.exchange=? AND v.market=? AND v.timeframe=? "
                "  AND v.open_time>=? AND v.close_time<=? AND v.is_closed=1"
                f"{sym_filter} "
                "ORDER BY v.symbol, v.open_time")
            params = [self.exchange, self.market, timeframe, int(start_ms), int(end_ms)]
            if symbols:
                params.append(list(symbols))
            cur = con.execute(q, params)
            while True:
                rows = cur.fetchmany(batch)
                if not rows:
                    break
                for r in rows:
                    yield r
        finally:
            con.close()

    # ---------- 热层 ----------
    def _hot_iter(self, timeframe: str, start_ms: int, end_ms: int,
                  symbols: Optional[Sequence[str]] = None,
                  batch: int = 50_000) -> Iterable[Tuple]:
        con = sqlite3.connect(f"file:{self.hot_db}?mode=ro", uri=True, timeout=30)
        con.execute("PRAGMA query_only=ON")
        try:
            syms = list(symbols) if symbols else []
            sym_filter = ""
            params: List = [self.exchange, self.market, timeframe, int(start_ms), int(end_ms)]
            if syms:
                sym_filter = " AND symbol IN (" + ",".join("?" * len(syms)) + ")"
                params += syms
            cur = con.execute(
                "SELECT symbol, open_time, close_time, open, high, low, close,"
                "       volume, quote_volume, source_id "
                "FROM candles WHERE exchange=? AND market=? AND timeframe=? "
                "  AND is_closed=1 AND open_time>=? AND close_time<=?"
                f"{sym_filter} "
                "ORDER BY symbol, open_time", params)
            while True:
                rows = cur.fetchmany(batch)
                if not rows:
                    break
                for r in rows:
                    yield r
        finally:
            con.close()

    # ---------- 合并 ----------
    def load(self, symbols: Sequence[str], timeframes: Sequence[str],
             start_ms: int, end_ms: int,
             keep_from_ms: Optional[int] = None) -> Tuple[Dict[str, Dict[str, List[dict]]], dict]:
        """合并读取。

        参数
        ----
        symbols       : 需要的标的（其余标的即使库里有也丢弃，保证口径一致）
        timeframes    : 需要的周期（`1h` / `4h`）
        start_ms      : 读取下界（= 180 天前）
        end_ms        : 读取上界（= 初始化时刻；只取 `close_time <= end_ms`）
        keep_from_ms  : 内存保留下界（默认 = start_ms）。小于它的行只参与
                        「读取量 / 连续性」统计，不留在内存 —— 180 天 1h 全量
                        物化约 2.3M 行，没必要一直占着。

        返回
        ----
        (data, stats)
        data[symbol][timeframe] = [ {open_time, close_time, open, high, low, close,
                                     volume, quote_volume, source}, ... ] 升序
        """
        key = (tuple(timeframes), int(start_ms), int(end_ms),
               int(keep_from_ms if keep_from_ms is not None else start_ms), tuple(symbols))
        if key in self._cache:
            return self._cache[key]

        want = set(symbols)
        keep_from = int(keep_from_ms if keep_from_ms is not None else start_ms)
        t_all = time.time()
        data: Dict[str, Dict[str, List[dict]]] = {s: {tf: [] for tf in timeframes}
                                                  for s in symbols}
        st_all = self.archive_state()
        stats: dict = {
            "generated_at_utc": _iso(int(time.time() * 1000)),
            "read_window": {"start_ms": int(start_ms), "start_utc": _iso(start_ms),
                            "end_ms": int(end_ms), "end_utc": _iso(end_ms),
                            "days": round((int(end_ms) - int(start_ms)) / DAY_MS, 2),
                            "keep_from_utc": _iso(keep_from)},
            "archive_state": {k: st_all.get(k) for k in
                              ("watermark_ms", "reconcile_watermark_ms")},
            "hot_db": self.hot_db,
            "archive_root": str(self.archive_root),
            "downloaded_anything": False,
            "wrote_production": False,
            "per_timeframe": {},
        }

        for tf in timeframes:
            iv = INTERVAL_MS[tf]
            per: Dict[str, dict] = {}
            st: Dict[str, dict] = {}
            n_cold = n_hot = n_overlap = n_mismatch = 0
            cold_max: Optional[int] = None
            for r in self._cold_iter(tf, start_ms, end_ms, symbols):
                sym = r[0]
                if sym not in want:
                    continue
                n_cold += 1
                cold_max = r[1] if cold_max is None else max(cold_max, int(r[1]))
                _d, _m = self._merge_row(data, st, sym, tf, r, "cold", keep_from)
                n_overlap += _d
                n_mismatch += _m
            for r in self._hot_iter(tf, start_ms, end_ms, symbols):
                sym = r[0]
                if sym not in want:
                    continue
                n_hot += 1
                dup, diff = self._merge_row(data, st, sym, tf, r, "hot", keep_from)
                n_overlap += dup
                n_mismatch += diff

            # 收尾：去重 / 升序 / 缺口（保留区间）
            dup_removed = 0
            for s in symbols:
                rows = data[s][tf]
                if not rows:
                    continue
                rows.sort(key=lambda x: x["open_time"])
                dedup: List[dict] = []
                prev = None
                gaps: List[dict] = []
                for row in rows:
                    ot = row["open_time"]
                    if prev is not None and ot == prev:
                        dup_removed += 1
                        continue
                    if prev is not None and ot - prev > iv:
                        gaps.append({"after_utc": _iso(prev), "before_utc": _iso(ot),
                                     "missing": int((ot - prev) // iv - 1)})
                    prev = ot
                    dedup.append(row)
                data[s][tf] = dedup
                st.setdefault(s, {})["gaps_kept"] = gaps

            # 覆盖与连续性（基于**读取到的全区间**，不是保留区间）
            cov, late, gaps_total = [], [], 0
            for s in symbols:
                v = st.get(s) or {}
                if not v.get("count"):
                    cov.append({"symbol": s, "bars": 0, "first_utc": None, "last_utc": None,
                                "missing": None, "gaps": []})
                    continue
                expected = (v["last"] - v["first"]) // iv + 1
                missing = int(expected - v["count"])
                gaps_total += max(0, missing)
                cov.append({"symbol": s, "bars": int(v["count"]),
                            "first_utc": _iso(v["first"]), "last_utc": _iso(v["last"]),
                            "missing": missing,
                            "gaps_kept": v.get("gaps_kept", [])[:5],
                            "cold": v.get("cold", 0), "hot": v.get("hot", 0),
                            "both": v.get("both", 0)})
                if v["first"] > int(start_ms) + iv:
                    late.append({"symbol": s, "first_utc": _iso(v["first"]),
                                 "days_before_end": round((int(end_ms) - v["first"]) / DAY_MS, 2)})

            per["cold_rows_scanned"] = n_cold
            per["hot_rows_scanned"] = n_hot
            per["overlap_rows"] = n_overlap
            per["overlap_value_mismatch"] = n_mismatch
            per["rows_materialized"] = sum(len(data[s][tf]) for s in symbols)
            per["dup_removed"] = dup_removed
            per["cold_max_open_utc"] = _iso(cold_max)
            per["symbols"] = len(symbols)
            per["symbols_with_data"] = sum(1 for c in cov if c["bars"])
            per["symbols_full_window"] = sum(
                1 for c in cov if c["bars"] and c["missing"] == 0
                and c["first_utc"] is not None
                and c["first_utc"] <= _iso(int(start_ms) + iv))
            per["missing_bars_total"] = gaps_total
            per["bars_min"] = min((c["bars"] for c in cov), default=0)
            per["bars_max"] = max((c["bars"] for c in cov), default=0)
            per["listed_late_n"] = len(late)
            per["listed_late"] = late
            per["source_split"] = {
                "cold_only": sum(1 for v in st.values() if v.get("cold") and not v.get("hot")),
                "hot_only": sum(1 for v in st.values() if v.get("hot") and not v.get("cold")),
                "both": sum(1 for v in st.values() if v.get("hot") and v.get("cold")),
            }
            per["coverage"] = cov
            stats["per_timeframe"][tf] = per
            self.log(f"[history] {tf}: cold={n_cold} hot={n_hot} overlap={n_overlap} "
                     f"kept={per['rows_materialized']} missing={gaps_total} "
                     f"late={len(late)}")

        stats["elapsed_sec"] = round(time.time() - t_all, 2)
        stats["rows_materialized_total"] = sum(
            len(data[s][tf]) for s in symbols for tf in timeframes)
        self._cache[key] = (data, stats)
        return data, stats

    def release(self) -> None:
        """释放已物化的行（bootstrap 结束后调用，把峰值内存还回去）。

        `stats` 仍保留在调用方手里，`_cache` 只留键不留值。
        """
        self._cache.clear()

    @staticmethod
    def _merge_row(data, st, sym, tf, r, source, keep_from) -> Tuple[int, int]:
        """写入一行。返回 (是否「同 key 冷热重叠」, 是否「重叠但数值不同」)。"""
        (_, ot, ct, o, h, l, c, vol, qv, _sid) = r
        ot = int(ot)
        rec = st.get(sym)
        if rec is None:
            rec = st[sym] = {"count": 0, "first": ot, "last": ot, "cold": 0,
                             "hot": 0, "both": 0, "pos": {}}
        pos = rec["pos"]
        row = _row(ot, ct, o, h, l, c, vol, qv, source)
        if ot in pos:
            rec["both"] += 1
            if source == "hot":
                rec["hot"] += 1
                i = pos[ot]
                if i is not None:
                    old = data[sym][tf][i]
                    diff = 1 if (old["open"], old["high"], old["low"], old["close"],
                                 old["volume"]) != (row["open"], row["high"], row["low"],
                                                    row["close"], row["volume"]) else 0
                    data[sym][tf][i] = row          # 热层优先（覆盖冷层）
                    return 1, diff
            else:
                rec["cold"] += 1
            return 1, 0
        rec["count"] += 1
        if ot < rec["first"]:
            rec["first"] = ot
        if ot > rec["last"]:
            rec["last"] = ot
        rec[source] += 1
        if ot >= keep_from:
            rec["pos"][ot] = len(data[sym][tf])
            data[sym][tf].append(row)
        else:
            rec["pos"][ot] = None                   # 只记「见过」，不占内存
        return 0, 0


def _row(ot, ct, o, h, l, c, vol, qv, source) -> dict:
    return {"open_time": int(ot), "close_time": int(ct),
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": float(vol or 0.0), "quote_volume": float(qv or 0.0),
            "source": source}


def make_provider(hot_db, archive_root, log=print) -> MergedHistory:
    return MergedHistory(hot_db, archive_root, log=log)

# -*- coding: utf-8 -*-
"""ACCUM-EARLY-01｜十五分钟「冷档 parquet + 热库」只读合并读取器（研究用）。

本模块**只做读取**：不下载、不写库、不补造缺口、不改任何生产读路径。

为什么需要它（本单核验出来的不对称）
--------------------------------------------------
· 预热/状态重建走的是 `history_loader.MergedHistory`（冷+热合并），重放地板
  2880 小时，锚点约 2026-06-27；
· 但**成交与止损**用的 `paper_engine.closed_15m()` 只读热库，而热库 15m 只从
  2026-09-12 起 ⇒ 2026-06-27 → 2026-09-12 这段「能重建观察，重建不出成交」。
本模块补齐这条读取链，供研究回放使用；生产 `step()` 与 `closed_15m()` 不动。

四条口径（必须显式，不许隐含）
------------------------------
1. **去重**：唯一键 = `open_time`（同一标的内）。同一 `open_time` 出现多次只保留一条。
2. **冲突优先级**：冷热重叠时**热层优先**。理由与 `history_loader` 一致——热层是
   生产实际在用、且更新更晚的那一份；冷档是导出快照，可能落后（实测冷档 15m
   最新到 2026-10-18，热库到 2026-10-26）。
3. **时间连续性**：按 15 分钟步长检查缺口，缺口**逐段列出**，不插值、不补齐。
4. **可用时间**：热层 `candle_versions.available_at` 实测填充率约一半；冷档
   `available_at` 实测 **0% 填充**。为空时写 `available_at=None` 且
   `available_at_missing=True`，**不得**用 `close_time` 冒充可得时刻。

对外接口
--------
    m = Merged15m(hot_db, archive_root)
    bars, stats = m.load("BTCUSDT", start_ms, end_ms)
    stats 里给出 cold/hot 行数、去重条数、优先级命中数、缺口段、可用时间覆盖率。

    m.crosscheck_ohlcv("BTCUSDT", start_ms, end_ms) -> dict
        与 `history_loader.MergedHistory` 的合并结果逐根比对 OHLCV，
        用于证明本模块没有另立一套来源规则（两处不一致即为缺陷）。

ACCUM-EARLY-03-B 追加（**同一套合并器，不另立第二套**）
------------------------------------------------------
上面是「一次性整段取数」的研究接口。成交/退出链路需要的是「按时刻点取一根」，
因此本模块追加两个门面，**全部复用同一个 `Merged15m.load()`**：

    MergedReader  —— 单周期（默认 15m）带窗口缓存的行读取器，返回 `Bar`；
    MergedMarket  —— 15m + 4h 成对的门面（成交看 15m，跟踪止损看 4h）。

三条纪律写在代码里，不靠调用方自觉：
1. **未走合并的 SQL 会被记进 `passthrough`**（不静默回落热库）⇒ 抓漏靠这个清单；
2. `available_at` 为空时 `available_at_missing=True` 一路带到 `Bar`，**不冒充可得时刻**；
3. 只读：本模块不写任何库，缓存只在内存。
"""
from __future__ import annotations

import bisect
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

STEP_MS = 900_000
#: 各周期一根 bar 的毫秒宽度（用于「已收盘」边界与缺口判定）
TIMEFRAME_STEP_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
BAR_COLUMNS = ("open_time", "close_time", "open", "high", "low", "close",
               "volume", "quote_volume", "trade_count",
               "taker_buy_volume", "taker_buy_quote_volume",
               "available_at", "source")


def _f(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    import datetime as dt
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class Merged15m:
    """冷+热合并的只读读取器（默认 15m；`timeframe` 可切 1h/4h）。

    保留原名与默认参数，是为了让 ACCUM-EARLY-01 已交付的核验脚本**逐字复用**；
    4h 走的是**同一个类、同一段合并逻辑**，只是换了周期参数，不是另一套实现。
    """

    def __init__(self, hot_db, archive_root, exchange: str = "binance",
                 market: str = "um", timeframe: str = "15m"):
        self.hot_db = str(hot_db)
        self.archive_root = Path(archive_root)
        self.exchange = exchange
        self.market = market
        self.timeframe = timeframe
        if timeframe not in TIMEFRAME_STEP_MS:
            raise ValueError(f"unsupported_timeframe: {timeframe}")
        self.step_ms = TIMEFRAME_STEP_MS[timeframe]

    # ── 冷档边界（单一事实源：归档器写的 _state.json）─────────────────────
    def archive_state(self) -> dict:
        try:
            return json.loads((self.archive_root / "_state.json").read_text(
                encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            return {}

    def cold_upper_bound(self) -> Optional[int]:
        """冷档可信上界。取 archive_verified_upto_ms，缺失则退回 reconcile 水位。

        为什么需要上界：冷档是按天导出/对账的快照，末尾分片可能不完整；越界读取
        会把「未对账的尾巴」当成历史，与热层冲突时又会被热层覆盖，反而掩盖问题。
        """
        st = self.archive_state()
        for k in ("archive_verified_upto_ms", "reconcile_watermark_ms",
                  "watermark_ms"):
            v = st.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return int(v)
        return None

    # ── 冷层 ────────────────────────────────────────────────────────────
    def _cold_rows(self, symbol: str, start_ms: int, end_ms: int) -> List[tuple]:
        import duckdb
        pattern = str(self.archive_root / "candles" / self.exchange / self.market
                      / symbol / self.timeframe / "*.parquet")
        if not list((self.archive_root / "candles" / self.exchange / self.market
                     / symbol / self.timeframe).glob("*.parquet")):
            return []
        ub = self.cold_upper_bound()
        hi = int(end_ms) if ub is None else min(int(end_ms), int(ub))
        if hi <= int(start_ms):
            return []
        q = f"""
            SELECT open_time, close_time, open, high, low, close, volume,
                   quote_volume, trade_count, taker_buy_volume,
                   taker_buy_quote_volume, available_at, is_closed
            FROM read_parquet('{pattern}')
            WHERE open_time >= ? AND open_time < ? AND is_closed = 1
            ORDER BY open_time
        """
        con = duckdb.connect()
        try:
            return con.execute(q, [int(start_ms), int(hi)]).fetchall()
        finally:
            con.close()

    # ── 热层 ────────────────────────────────────────────────────────────
    def _hot_rows(self, symbol: str, start_ms: int, end_ms: int) -> List[tuple]:
        # 必须走 `candles` 视图（= candle_versions JOIN candle_heads），不能直查
        # `candle_versions`：后者存**全版本**，同一 open_time 可能有多条历史版本，
        # 直查会按任意顺序取到非头版本。对撞脚本正是这样抓到的
        # （2026-10-26：BTCUSDT open_time=1791273600000 收盘差 5.4）。
        con = sqlite3.connect(f"file:{self.hot_db}?mode=ro", uri=True)
        try:
            return con.execute(
                """SELECT open_time, close_time, open, high, low, close, volume,
                          quote_volume, trade_count, taker_buy_volume,
                          taker_buy_quote_volume, available_at, is_closed
                   FROM candles
                   WHERE exchange=? AND market=? AND symbol=? AND timeframe='{tf}'
                     AND open_time >= ? AND open_time < ? AND is_closed = 1
                   ORDER BY open_time""".format(tf=self.timeframe),
                (self.exchange, self.market, symbol, int(start_ms), int(end_ms))
            ).fetchall()
        finally:
            con.close()

    # ── 合并 ────────────────────────────────────────────────────────────
    def load(self, symbol: str, start_ms: int, end_ms: int) -> Tuple[List[dict], dict]:
        cold = self._cold_rows(symbol, start_ms, end_ms)
        hot = self._hot_rows(symbol, start_ms, end_ms)

        merged: Dict[int, dict] = {}
        n_cold = n_hot = n_conflict = 0
        avail_hot = avail_hot_null = 0
        avail_cold = avail_cold_null = 0

        for row in cold:
            ot = _i(row[0])
            if ot is None:
                continue
            n_cold += 1
            if row[11] is None:
                avail_cold_null += 1
            else:
                avail_cold += 1
            merged[ot] = {
                "open_time": ot, "close_time": _i(row[1]),
                "open": _f(row[2]), "high": _f(row[3]), "low": _f(row[4]),
                "close": _f(row[5]), "volume": _f(row[6]),
                "quote_volume": _f(row[7]), "trade_count": _i(row[8]),
                "taker_buy_volume": _f(row[9]),
                "taker_buy_quote_volume": _f(row[10]),
                "available_at": _i(row[11]), "source": "cold",
            }

        for row in hot:
            ot = _i(row[0])
            if ot is None:
                continue
            n_hot += 1
            if row[11] is None:
                avail_hot_null += 1
            else:
                avail_hot += 1
            if ot in merged:
                n_conflict += 1                      # 冷热重叠 ⇒ 热层覆盖
            merged[ot] = {
                "open_time": ot, "close_time": _i(row[1]),
                "open": _f(row[2]), "high": _f(row[3]), "low": _f(row[4]),
                "close": _f(row[5]), "volume": _f(row[6]),
                "quote_volume": _f(row[7]), "trade_count": _i(row[8]),
                "taker_buy_volume": _f(row[9]),
                "taker_buy_quote_volume": _f(row[10]),
                "available_at": _i(row[11]), "source": "hot",
            }

        bars = [merged[k] for k in sorted(merged)]
        for b in bars:
            b["symbol"] = symbol
            b["timeframe"] = self.timeframe
            b["available_at_missing"] = b["available_at"] is None
            # 主动卖出是**推导量**，不是交易所原始字段
            if b["volume"] is not None and b["taker_buy_volume"] is not None:
                b["taker_sell_volume"] = b["volume"] - b["taker_buy_volume"]
                b["taker_sell_derived"] = True
            else:
                b["taker_sell_volume"] = None
                b["taker_sell_derived"] = True

        gaps = self._gaps(bars)
        stats = {
            "symbol": symbol,
            "timeframe": self.timeframe,
            "window": {"start_ms": int(start_ms), "start_utc": _iso(start_ms),
                       "end_ms": int(end_ms), "end_utc": _iso(end_ms)},
            "cold_upper_bound_utc": _iso(self.cold_upper_bound()),
            "rows_cold": n_cold, "rows_hot": n_hot,
            "rows_merged": len(bars),
            "overlap_hot_wins": n_conflict,
            "dup_removed": n_cold + n_hot - len(bars),
            "available_at": {
                "hot_filled": avail_hot, "hot_missing": avail_hot_null,
                "cold_filled": avail_cold, "cold_missing": avail_cold_null,
                "rule": "为空即 available_at_missing=True，禁止用 close_time 冒充",
            },
            "continuity": {
                "expected_bars": ((int(end_ms) - int(start_ms)) // self.step_ms),
                "actual_bars": len(bars),
                "gap_segments": gaps,
                "gap_segment_count": len(gaps),
                "missing_bars_total": sum(g["missing_bars"] for g in gaps),
            },
            "rule": {
                "dedup_key": "open_time",
                "conflict_priority": "hot_over_cold",
                "only_closed": True,
                "cold_upper_bound_source": "_state.json",
                "no_interpolation": True,
                "no_download": True,
                "wrote_anything": False,
            },
        }
        return bars, stats

    def _gaps(self, bars: Sequence[dict]) -> List[dict]:
        out = []
        for prev, cur in zip(bars, bars[1:]):
            d = int(cur["open_time"]) - int(prev["open_time"])
            if d > self.step_ms:
                out.append({
                    "after_utc": _iso(prev["open_time"]),
                    "before_utc": _iso(cur["open_time"]),
                    "missing_bars": d // self.step_ms - 1,
                })
        return out

    # ── 与既有单一实现对撞 ──────────────────────────────────────────────
    def crosscheck_ohlcv(self, symbol: str, start_ms: int, end_ms: int) -> dict:
        """与 `history_loader.MergedHistory` 逐根比对 OHLCV。

        用途：证明本模块没有另立一套来源规则。只比对两边都有的 bar；
        任一侧缺失的 bar 记入 `only_in_*`，不算不一致但要如实列出。
        """
        import history_loader as HL
        bars, _ = self.load(symbol, start_ms, end_ms)
        mine = {b["open_time"]: b for b in bars}
        hist = HL.MergedHistory(self.hot_db, self.archive_root, log=lambda *a: None)
        try:
            data, _st = hist.load([symbol], [self.timeframe], start_ms, end_ms)
            theirs = {int(b["open_time"]): b for b in data[symbol][self.timeframe]}
        finally:
            try:
                hist.release()
            except Exception:                                # noqa: BLE001
                pass
        mism = []
        for ot in sorted(set(mine) & set(theirs)):
            a, b = mine[ot], theirs[ot]
            for k in ("open", "high", "low", "close", "volume", "quote_volume"):
                va, vb = _f(a.get(k)), _f(b.get(k))
                if va is None or vb is None:
                    if va is not vb:
                        mism.append({"open_time": ot, "field": k,
                                     "mine": va, "theirs": vb})
                    continue
                if abs(va - vb) > 1e-9 * max(1.0, abs(vb)):
                    mism.append({"open_time": ot, "field": k,
                                 "mine": va, "theirs": vb})
        return {
            "symbol": symbol,
            "compared_bars": len(set(mine) & set(theirs)),
            "only_in_mine": len(set(mine) - set(theirs)),
            "only_in_theirs": len(set(theirs) - set(mine)),
            "mismatch_count": len(mism),
            "mismatches": mism[:20],
            "verdict": "consistent" if not mism else "mismatch",
        }


def default_paths():
    """工程内默认路径（只读用）。"""
    root = Path(__file__).resolve().parents[2]
    hot = root / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
    archive = root / "数据" / "市场数据" / "parquet"
    return hot, archive


# ══════════════════════════════════════════════════════════════════════════
# ACCUM-EARLY-03-B｜成交链路可用的「按时刻取一根」门面
# ══════════════════════════════════════════════════════════════════════════
# 为什么不能直接用 `Merged15m.load()`：成交/退出链路是按时刻点取一根
# （`bar_at_or_after` / `bar_before` / `last_closed`），逐次整段扫 parquet 会慢到不可用。
# 所以这里只加**缓存与二分**，数据来源仍然只有一个：`Merged15m.load()`。


class Bar(dict):
    """一根行情 K 线（**dict 子类**，因此 `isinstance(x, dict)` 仍成立）。

    为什么必须是 dict 子类：既有调用方用 `validate_market_candle(candle)`（内部
    `isinstance(candle, dict)`）、`dict(row)`、`candle.get("available_at")`。
    为了不改这些调用方，行对象必须先是 dict。

    额外支持整数下标（`row[4]` = 第 5 个标准列），与 `sqlite3.Row` 的习惯对齐，
    免得「原来传 Row、现在传 Bar」时静默拿到 KeyError。
    """

    ORDER = ("open_time", "close_time", "open", "high", "low", "close",
             "volume", "quote_volume", "trade_count", "taker_buy_volume",
             "taker_buy_quote_volume", "available_at")

    def __init__(self, data: dict):
        super().__init__(data)

    def __getitem__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            return dict.__getitem__(self, self.ORDER[key])
        return dict.__getitem__(self, key)

    # ── 供证据/审计读取的派生属性 ────────────────────────────────────────
    @property
    def source_layer(self) -> Optional[str]:
        return self.get("source")

    @property
    def available_at_proven(self) -> bool:
        """可得时刻是否有证据。**False 不等于「不可用」**，只等于「没有证明」。"""
        return self.get("available_at") is not None


class MergedReader:
    """单周期（默认 15m）冷+热合并的**行读取器**（带窗口缓存 + 穿透审计）。

    三条硬约束：
      · 只读：`mode=ro` + `PRAGMA query_only=ON`，且不写任何文件；
      · 不跨缺口补造：缺口只在 `stats()` 里如实列出，取不到就是取不到；
      · 未走合并的 SQL 记入 `passthrough`（**不静默回落热库**）⇒ 抓漏看这张清单。
    """

    def __init__(self, hot_db, archive_root, timeframe: str = "15m",
                 exchange: str = "binance", market: str = "um",
                 forward_pad_ms: Optional[int] = None,
                 backward_pad_ms: Optional[int] = None):
        self.timeframe = timeframe
        self.step_ms = TIMEFRAME_STEP_MS[timeframe]
        self.hot_db = str(hot_db)
        self.archive_root = str(archive_root)
        self.exchange, self.market = exchange, market
        self._m = Merged15m(hot_db, archive_root, exchange, market, timeframe)
        self._con = sqlite3.connect(f"file:{hot_db}?mode=ro", uri=True, timeout=15)
        self._con.execute("PRAGMA query_only=ON")
        self._con.row_factory = sqlite3.Row
        #: 前向多读一段，避免「逐根推进」时反复扫 parquet（只影响缓存，不影响取数语义：
        #: 所有取值仍按时间戳边界选，多读的行不会被返回给越界的请求）
        self.forward_pad_ms = int(forward_pad_ms if forward_pad_ms is not None
                                  else 30 * 24 * 3_600_000)
        self.backward_pad_ms = int(backward_pad_ms if backward_pad_ms is not None
                                   else 7 * 24 * 3_600_000)
        self._bars: Dict[str, List[Bar]] = {}
        self._opens: Dict[str, List[int]] = {}
        self._closes: Dict[str, List[int]] = {}
        self._lo: Dict[str, int] = {}
        self._hi: Dict[str, int] = {}
        self._stats: Dict[str, dict] = {}
        self._loads: List[dict] = []
        self.passthrough: List[dict] = []
        self.reads = {"bars": 0, "at_or_after": 0, "before": 0, "last_closed": 0}
        self.unbounded_latest_calls = 0
        self.returned_by_source = {"cold": 0, "hot": 0}
        self.returned_available_at_missing = 0

    # ── 缓存加载（唯一数据来源 = Merged15m.load）────────────────────────────
    def _ensure(self, symbol: str, start_ms: int, end_ms: int) -> None:
        lo, hi = self._lo.get(symbol), self._hi.get(symbol)
        if lo is not None and lo <= start_ms and end_ms <= hi:
            return
        nlo = int(start_ms) - self.backward_pad_ms if lo is None else min(lo, int(start_ms))
        nhi = max(int(end_ms) + self.forward_pad_ms, hi if hi is not None else int(end_ms))
        raw, st = self._m.load(symbol, nlo, nhi)
        bars = [Bar(b) for b in raw]
        self._bars[symbol] = bars
        self._opens[symbol] = [int(b["open_time"]) for b in bars]
        self._closes[symbol] = [int(b["close_time"]) for b in bars]
        self._lo[symbol], self._hi[symbol] = nlo, nhi
        self._stats[symbol] = st
        self._loads.append({
            "symbol": symbol, "timeframe": self.timeframe,
            "start_utc": _iso(nlo), "end_utc": _iso(nhi),
            "rows_cold": st["rows_cold"], "rows_hot": st["rows_hot"],
            "rows_merged": st["rows_merged"],
            "overlap_hot_wins": st["overlap_hot_wins"],
            "gap_segment_count": st["continuity"]["gap_segment_count"],
        })

    def _track(self, bar: Optional[Bar]) -> Optional[Bar]:
        if bar is not None:
            self.returned_by_source[bar.get("source") or "unknown"] = (
                self.returned_by_source.get(bar.get("source") or "unknown", 0) + 1)
            if bar.get("available_at") is None:
                self.returned_available_at_missing += 1
        return bar

    # ── 取数（语义与 paper_engine 原 SQL 逐条对齐）─────────────────────────
    def bars(self, symbol: str, start_ms: int, end_ms: int) -> List[Bar]:
        """等价原 SQL：`open_time >= start AND open_time < end AND is_closed=1`。"""
        self.reads["bars"] += 1
        self._ensure(symbol, int(start_ms), int(end_ms))
        opens = self._opens[symbol]
        i = bisect.bisect_left(opens, int(start_ms))
        j = bisect.bisect_left(opens, int(end_ms))
        return [self._track(b) for b in self._bars[symbol][i:j]]

    def at_or_after(self, symbol: str, ts_ms: int) -> Optional[Bar]:
        """等价原 SQL：`open_time >= ts ORDER BY open_time LIMIT 1`。"""
        self.reads["at_or_after"] += 1
        self._ensure(symbol, int(ts_ms), int(ts_ms))
        opens = self._opens[symbol]
        i = bisect.bisect_left(opens, int(ts_ms))
        return self._track(self._bars[symbol][i]) if i < len(opens) else None

    def before(self, symbol: str, ts_ms: int) -> Optional[Bar]:
        """等价原 SQL：`open_time < ts ORDER BY open_time DESC LIMIT 1`。"""
        self.reads["before"] += 1
        self._ensure(symbol, int(ts_ms), int(ts_ms))
        opens = self._opens[symbol]
        i = bisect.bisect_left(opens, int(ts_ms)) - 1
        return self._track(self._bars[symbol][i]) if i >= 0 else None

    def last_closed(self, symbol: str, upto_ms: Optional[int] = None) -> Optional[Bar]:
        """等价原 SQL：`close_time <= upto ORDER BY open_time DESC LIMIT 1`。

        `upto_ms=None` 在**回放里是危险调用**（等于「按挂钟取最新一根」）。这里不拒绝它
        （生产估值路径就是这么调的），但做了两件事：
          · 只读**缓存窗口内**最后一根；缓存为空时最多回看 3 天，绝不整段加载；
          · 每次调用都记进 `unbounded_latest_calls` ⇒ 回放报告里能看见「有几次是按挂钟取值」。
        """
        self.reads["last_closed"] += 1
        if upto_ms is None:
            self.unbounded_latest_calls += 1
            bars = self._bars.get(symbol)
            if not bars:
                import time as _t
                now_ms = int(_t.time() * 1000)
                self._ensure(symbol, now_ms - 3 * 24 * 3_600_000, now_ms)
                bars = self._bars.get(symbol) or []
            return self._track(bars[-1]) if bars else None
        self._ensure(symbol, int(upto_ms), int(upto_ms))
        closes = self._closes[symbol]
        if closes and any(closes[k] < closes[k - 1] for k in range(1, len(closes))):
            # 数据异常（close_time 非单调）⇒ 退化为从后往前扫，绝不假设单调
            for b in reversed(self._bars[symbol]):
                if int(b["close_time"]) <= int(upto_ms):
                    return self._track(b)
            return None
        i = bisect.bisect_right(closes, int(upto_ms)) - 1
        return self._track(self._bars[symbol][i]) if i >= 0 else None

    # ── 非 K 线表（资金费等）走热库只读；**每次穿透都记账**─────────────────
    def execute(self, sql, params=()):
        self.passthrough.append({
            "sql": " ".join(str(sql).split())[:400],
            "params": [str(p)[:40] for p in (params or ())][:8],
        })
        return self._con.execute(sql, params)

    # ── 证据 ──────────────────────────────────────────────────────────────
    def stats(self, symbol: str) -> dict:
        return self._stats.get(symbol, {})

    def gaps(self, symbol: str) -> dict:
        st = self._stats.get(symbol) or {}
        return st.get("continuity", {})

    def provenance(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "hot_db": self.hot_db,
            "archive_root": self.archive_root,
            "cold_upper_bound_utc": _iso(self._m.cold_upper_bound()),
            "rule": {
                "dedup_key": "open_time",
                "conflict_priority": "hot_over_cold",
                "only_closed": True,
                "no_interpolation": True,
                "no_download": True,
                "available_at_null_kept": True,
            },
            "cache_loads": self._loads,
            "reads": dict(self.reads),
            "unbounded_latest_calls": self.unbounded_latest_calls,
            "returned_by_source": dict(self.returned_by_source),
            "returned_available_at_missing": self.returned_available_at_missing,
            "passthrough_sql_count": len(self.passthrough),
            "passthrough_sql": self.passthrough[:50],
        }

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:                                    # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class MergedMarket:
    """15m + 4h 成对门面：成交看 15m，跟踪止损看 4h（都走同一个合并器）。

    对外方法与 `paper_engine` 原来的四个取数函数一一对应，因此接线是**替换数据源**，
    不是改判定逻辑：
        bars / at_or_after / before / last_closed            → 15m
        bars_tf / last_closed_tf / at_or_after_tf            → 指定周期（4h 用）
        execute                                             → 非 K 线表走热库（穿透记账）
    """

    def __init__(self, hot_db, archive_root, exchange: str = "binance",
                 market: str = "um"):
        self.hot_db, self.archive_root = str(hot_db), str(archive_root)
        self.m15 = MergedReader(hot_db, archive_root, "15m", exchange, market)
        self.m4h = MergedReader(hot_db, archive_root, "4h", exchange, market)

    def reader(self, timeframe: str) -> MergedReader:
        if timeframe == "15m":
            return self.m15
        if timeframe == "4h":
            return self.m4h
        raise ValueError(f"unsupported_timeframe: {timeframe}")

    # 15m 快捷方式
    def bars(self, symbol, start_ms, end_ms):
        return self.m15.bars(symbol, start_ms, end_ms)

    def at_or_after(self, symbol, ts_ms):
        return self.m15.at_or_after(symbol, ts_ms)

    def before(self, symbol, ts_ms):
        return self.m15.before(symbol, ts_ms)

    def last_closed(self, symbol, upto_ms=None):
        return self.m15.last_closed(symbol, upto_ms)

    # 指定周期
    def bars_tf(self, timeframe, symbol, start_ms, end_ms):
        return self.reader(timeframe).bars(symbol, start_ms, end_ms)

    def at_or_after_tf(self, timeframe, symbol, ts_ms):
        return self.reader(timeframe).at_or_after(symbol, ts_ms)

    def before_tf(self, timeframe, symbol, ts_ms):
        return self.reader(timeframe).before(symbol, ts_ms)

    def last_closed_tf(self, timeframe, symbol, upto_ms=None):
        return self.reader(timeframe).last_closed(symbol, upto_ms)

    def execute(self, sql, params=()):
        return self.m15.execute(sql, params)

    def close(self):
        self.m15.close()
        self.m4h.close()

    def provenance(self) -> dict:
        return {"15m": self.m15.provenance(), "4h": self.m4h.provenance()}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

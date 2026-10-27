# -*- coding: utf-8 -*-
"""ACCUM-EARLY-02｜持仓量（OI）真实序列读取器（只读，研究 + 生产共用）。

只做一件事：把**已入库**的 5 分钟持仓量序列，按「决策时刻可见」的规则取出来，
连同**逐行可得时点**、单位、来源、缺失原因一起交给调用方。
不下载、不写库、不补零、不猜可得时刻。

口径（五条，必须显式）
----------------------
1. **时间戳语义**：`oi_history.ts` 是 5 分钟桶的**起点**，桶收盘 = `ts + 5min`。
   证据：全表 5m 行里 `ts % 300000 == 0` 的占绝大多数（非对齐行集中在
   2026-10-15 的 `snapshot` 类抓取，见 `03` 核验脚本实测）。
2. **可得时点**：**只有 `fetched_at`（网关实际收到响应体的时刻）能证明「当时读得到」**。
   `ts + 5min`（桶结束）**不是**可得证据 —— 交易所何时发布、我们何时抓到是两件事。
   逐行判四态（见下方 `AVAIL_*`），**未知不当作可用**。
3. **单位**：`open_interest` / `oi_ccy` 是**币本位数量**（BTC 的 93888.98 表示 93888.98 BTC）；
   `open_interest_value` / `oi_usd` 是**名义价值（USDT）**。两者不可混用。
   单位不是从列名猜的：`05` 脚本用 `open_interest_value / open_interest` 与同期
   5 分钟收盘价比值验证（比值≈1 才说明分子是币数、分母是 USDT）。
4. **缺失**：查不到就返回空数组 + `reason`，**绝不返回全零数组**冒充有数据。
5. **去重**：`period='5m'` 与 `period='snapshot'` 是**同一序列的两种落库口径**
   （实测重叠键取值逐位相同），决策只读 `5m`；同一 `ts` 出现多来源时按
   「实时源优先、其余取最早抓取时点」保留一行，重复条数如实计数。

对外接口
--------
    load_oi_pit(symbol, decision_ms, lookback_ms=6h, allow_assumed=False)
        -> (ts, vals, available_at_ms, meta)     # 逐行可得时点（未知为 NaN）
    load_oi(symbol, decision_ms, lookback_ms=6h)
        -> (ts, vals, meta)                      # 兼容旧调用；meta 内含可得性统计
    load_oi_rows(symbol, from_ms, to_ms)
        -> List[dict]                            # 原始行（带 fetched_at），供窗口预载
    slice_as_of(ts, vals, available_at, decision_ms)
        -> (ts, vals, available_at)              # 按决策时刻切片（fail-closed）
    latest_oi(symbol) -> dict                    # 最新一条真实观测（报告样本用）
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

BUCKET_MS = 300_000          # 5 分钟桶
EXCHANGE, MARKET = "binance", "um"
OI_VALUE_COLUMN = "open_interest"      # 与状态机 `oi_min_growth_pct` 同一口径：数量增长
OI_UNIT = "币本位数量（base asset）"
OI_UNIT_EVIDENCE = ("open_interest_value / open_interest ≈ 同期 5m 收盘价"
                    "（BTC/ETH/SOL/METIS 实测 1.000281/0.999705/0.999311/1.004334）"
                    "⇒ open_interest 是币数、open_interest_value 是 USDT 名义额")

#: 「抓取及时」上限：抓取时点距桶结束超过这个值 ⇒ 判为补抓/迟到（仍可用，但必须标记）
TIMELY_MAX_MS = 900_000

AVAIL_PROVEN = "proven"        # 有抓取时点、<= 决策时刻、且距桶结束 <= TIMELY_MAX_MS
AVAIL_LATE = "late"            # 有抓取时点、<= 决策时刻，但距桶结束 > TIMELY_MAX_MS
AVAIL_UNKNOWN = "unknown"      # 无抓取时点 ⇒ 没有任何可得性证据
AVAIL_FUTURE = "future"        # 抓取时点 > 决策时刻 ⇒ 决策当时还读不到

AVAIL_ALL = (AVAIL_PROVEN, AVAIL_LATE, AVAIL_UNKNOWN, AVAIL_FUTURE)


def _iso(ms) -> Optional[str]:
    if ms is None:
        return None
    import datetime as dt
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return dt.datetime.fromtimestamp(int(v) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def default_hot_db() -> Path:
    return Path(__file__).resolve().parents[2] / "数据" / "市场数据" / "db" / "market_v2.sqlite3"


def _connect(hot_db=None):
    db = str(hot_db or default_hot_db())
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


# ─────────────────────────────────────────────────────────────────────
# 1. 原始行读取（不做任何判定，只把库里的东西如实拿出来）
# ─────────────────────────────────────────────────────────────────────
def load_oi_rows(symbol: str, from_ms: int, to_ms: int,
                 period: str = "5m", hot_db=None) -> List[dict]:
    """取 `[from_ms, to_ms]` 内的 OI 原始行（含 `fetched_at`），按 `ts` 升序。

    列是 TEXT（库内实测 `open_interest` 为 TEXT 类型），这里统一 `CAST` 成 REAL，
    取不到数值的行**原样保留**并在 `parse_error` 里写明，不静默丢弃。
    """
    con = _connect(hot_db)
    try:
        rows = con.execute(
            """SELECT ts,
                      CAST(open_interest AS REAL),
                      CAST(open_interest_value AS REAL),
                      source, fetched_at
               FROM oi_history
               WHERE exchange=? AND market=? AND symbol=? AND period=?
                 AND ts >= ? AND ts <= ?
               ORDER BY ts, COALESCE(fetched_at, 9223372036854775807)""",
            (EXCHANGE, MARKET, symbol, period, int(from_ms), int(to_ms))
        ).fetchall()
    finally:
        con.close()

    out: List[dict] = []
    for r in rows:
        try:
            v = float(r[1]) if r[1] is not None else None
        except (TypeError, ValueError):
            v = None
        out.append({
            "ts": int(r[0]),
            "ts_utc": _iso(int(r[0])),
            "bucket_close_ms": int(r[0]) + BUCKET_MS,
            "bucket_close_utc": _iso(int(r[0]) + BUCKET_MS),
            "oi": v,
            "oi_value_usdt": (float(r[2]) if r[2] is not None else None),
            "source": r[3],
            "fetched_at": (int(r[4]) if r[4] is not None else None),
            "fetched_at_utc": _iso(r[4]),
            "parse_error": (None if v is not None else "open_interest 非数值"),
        })
    return out


def _dedupe_by_ts(rows: List[dict]) -> Tuple[List[dict], int, int]:
    """同一 `ts` 多行时保留一行：实时源优先 → 抓取时点最早。

    返回 `(保留行, 重复行数, 因数值非法被排除的行数)`。
    """
    kept: dict = {}
    dup = 0
    bad = 0
    for r in rows:
        if r["oi"] is None or not np.isfinite(r["oi"]) or r["oi"] <= 0:
            bad += 1
            continue
        cur = kept.get(r["ts"])
        if cur is None:
            kept[r["ts"]] = r
            continue
        dup += 1
        cur_gap = "#gapfill" in str(cur.get("source") or "")
        new_gap = "#gapfill" in str(r.get("source") or "")
        if cur_gap and not new_gap:
            kept[r["ts"]] = r
        elif cur_gap == new_gap:
            a = cur.get("fetched_at")
            b = r.get("fetched_at")
            if b is not None and (a is None or b < a):
                kept[r["ts"]] = r
    return [kept[k] for k in sorted(kept)], dup, bad


# ─────────────────────────────────────────────────────────────────────
# 2. 逐行可得性判定
# ─────────────────────────────────────────────────────────────────────
def judge_row(row: dict, decision_ms: int,
              allow_assumed: bool = False) -> Tuple[str, Optional[float]]:
    """判一行在 `decision_ms` 时是否读得到。返回 `(状态, 可得时点毫秒或 None)`。

    **不猜**：没有 `fetched_at` 就是 `unknown`，可得时点返回 `None`。
    `allow_assumed=True` 时才退化成「桶结束」，且调用方必须把 `assumed` 标出来。
    """
    f = row.get("fetched_at")
    close = int(row["ts"]) + BUCKET_MS
    if f is None:
        if allow_assumed:
            return AVAIL_UNKNOWN, float(close)
        return AVAIL_UNKNOWN, None
    f = int(f)
    if f > int(decision_ms):
        return AVAIL_FUTURE, None
    if f - close > TIMELY_MAX_MS:
        return AVAIL_LATE, float(f)
    return AVAIL_PROVEN, float(f)


def _empty_meta(symbol: str, decision_ms: int, lookback_ms: int, lo: int, hi: int,
                period: str, reason: str, allow_assumed: bool) -> dict:
    return {
        "symbol": symbol, "period": period,
        "decision_ms": int(decision_ms), "decision_utc": _iso(decision_ms),
        "lookback_ms": int(lookback_ms),
        "window_utc": {"from": _iso(lo), "to": _iso(hi)},
        "rows_returned": 0, "rows_raw": 0, "rows_duplicate_ts": 0,
        "rows_invalid_value": 0,
        "availability_counts": {k: 0 for k in AVAIL_ALL},
        "available_at_rule": ("逐行：fetched_at（网关收到响应体时刻）；"
                              "无 fetched_at = 未知，不当作可用"),
        "allow_assumed": bool(allow_assumed),
        "available_at_proven": False,
        "column": OI_VALUE_COLUMN, "unit": OI_UNIT,
        "unit_evidence": OI_UNIT_EVIDENCE,
        "source": None, "first_utc": None, "last_utc": None,
        "reason": reason,
        "note": "空结果不是零值；调用方不得用 0 填充",
    }


def load_oi_pit(symbol: str, decision_ms: int,
                lookback_ms: int = 6 * 3600 * 1000, period: str = "5m",
                hot_db=None, allow_assumed: bool = False, pad_ms: int = 0
                ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """取「决策时刻可见」的 OI 观测序列 + **逐行可得时点**。

    `pad_ms`（向前补白）
    -------------------
    「6 小时前」那一腿要的是**决策前 6 小时那一刻已可得**的观测。观测的可得时刻
    通常比桶结束晚 45~60 分钟 ⇒ 真正能用的是 `决策 - 7h` 附近的桶。窗口只取 6h
    会把这一腿切掉，读出来是 `oi_past_missing_before_start`（不报错、只是永远读不到，
    属静默失效）。需要做完整 6h 增幅时请传 `pad_ms=24*3600*1000`。
    默认 0 保持旧调用口径不变。

    返回 `(ts, vals, available_at_ms, meta)`：
      · `ts` / `vals`：桶起点与币本位持仓数量；
      · `available_at_ms`：float64，`NaN` = 该行**无法证明**当时可得（必须拒用）；
      · `meta.availability_counts`：四态计数，供报告与接口直接显示。
    """
    lo = int(decision_ms) - int(lookback_ms) - int(pad_ms) - 2 * BUCKET_MS
    hi = int(decision_ms) - BUCKET_MS          # ts + 5min <= decision
    rows = load_oi_rows(symbol, lo, hi, period=period, hot_db=hot_db)
    kept, dup, bad = _dedupe_by_ts(rows)

    ts: List[int] = []
    vals: List[float] = []
    avail: List[float] = []
    counts = {k: 0 for k in AVAIL_ALL}
    for r in kept:
        st, at = judge_row(r, decision_ms, allow_assumed=allow_assumed)
        counts[st] += 1
        if at is None:
            avail.append(float("nan"))
        else:
            avail.append(float(at))
        ts.append(int(r["ts"]))
        vals.append(float(r["oi"]))

    n = len(ts)
    usable = counts[AVAIL_PROVEN] + counts[AVAIL_LATE]
    meta = {
        "symbol": symbol, "period": period,
        "decision_ms": int(decision_ms), "decision_utc": _iso(decision_ms),
        "lookback_ms": int(lookback_ms),
        "window_utc": {"from": _iso(lo), "to": _iso(hi)},
        "rows_returned": n, "rows_raw": len(rows),
        "rows_duplicate_ts": dup, "rows_invalid_value": bad,
        "availability_counts": counts,
        "available_at_rule": ("逐行：fetched_at（网关收到响应体时刻）；"
                              "无 fetched_at = 未知，不当作可用"),
        "allow_assumed": bool(allow_assumed),
        # 「可用的那些行都有可得证据」才算 proven：
        #   · 有任何一行 `unknown` ⇒ False（没有证据就不能用）；
        #   · `future` 行是窗口里「抓取时刻晚于决策」的行，本来就会被切片排除，
        #     它们不影响「可用部分是否有证据」，但要单独计数如实暴露。
        "available_at_proven": bool(n > 0 and counts[AVAIL_UNKNOWN] == 0 and usable > 0),
        "rows_usable": int(usable),
        "rows_future_excluded": int(counts[AVAIL_FUTURE]),
        "column": OI_VALUE_COLUMN, "unit": OI_UNIT,
        "unit_evidence": OI_UNIT_EVIDENCE,
        "source": (kept[0]["source"] if kept else None),
        "first_utc": _iso(ts[0]) if ts else None,
        "last_utc": _iso(ts[-1]) if ts else None,
        "reason": (None if n else ("no_rows_in_window" if not rows else "all_rows_invalid")),
        "note": "空结果不是零值；调用方不得用 0 填充",
    }
    return (np.array(ts, dtype=np.int64),
            np.array(vals, dtype=np.float64),
            np.array(avail, dtype=np.float64),
            meta)


def load_oi(symbol: str, decision_ms: int, lookback_ms: int = 6 * 3600 * 1000,
            period: str = "5m", hot_db=None
            ) -> Tuple[np.ndarray, np.ndarray, dict]:
    """兼容旧调用：返回 `(ts, vals, meta)`，`meta` 内含逐行可得性统计。"""
    ts, vals, _avail, meta = load_oi_pit(
        symbol, decision_ms, lookback_ms=lookback_ms, period=period, hot_db=hot_db)
    return ts, vals, meta


def slice_as_of(ts: np.ndarray, vals: np.ndarray, available_at: np.ndarray,
                decision_ms: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """按决策时刻切片：只保留 `available_at <= decision_ms` 的行（`NaN` 自动排除）。"""
    ts = np.asarray(ts)
    if ts.size == 0:
        return ts, np.asarray(vals), np.asarray(available_at)
    av = np.asarray(available_at, dtype=np.float64)
    ok = np.isfinite(av) & (av <= float(decision_ms))
    return ts[ok], np.asarray(vals)[ok], av[ok]


# ─────────────────────────────────────────────────────────────────────
# 3. 报告样本
# ─────────────────────────────────────────────────────────────────────
def latest_oi(symbol: str, hot_db=None) -> dict:
    """最新一条真实观测（给报告做非空样本）。"""
    con = _connect(hot_db)
    try:
        r = con.execute(
            """SELECT ts, CAST(open_interest AS REAL), CAST(open_interest_value AS REAL),
                      CAST(oi_ccy AS REAL), CAST(oi_usd AS REAL), source, fetched_at
               FROM oi_history
               WHERE exchange=? AND market=? AND symbol=? AND period='5m'
               ORDER BY ts DESC LIMIT 1""",
            (EXCHANGE, MARKET, symbol)
        ).fetchone()
    finally:
        con.close()
    if not r:
        return {"symbol": symbol, "found": False, "reason": "no_rows"}
    ts = int(r[0])
    fetched = int(r[6]) if r[6] is not None else None
    # 「能否证明当时可得」必须和**某个决策时刻**比；单独一行只能给「距今多久」
    status, _ = judge_row({"ts": ts, "fetched_at": fetched}, ts + BUCKET_MS)
    return {
        "symbol": symbol, "found": True,
        "ts": ts, "ts_utc": _iso(ts),
        "bucket_close_utc": _iso(ts + BUCKET_MS),
        "open_interest": float(r[1]) if r[1] is not None else None,
        "open_interest_value_usdt": float(r[2]) if r[2] is not None else None,
        "oi_ccy": float(r[3]) if r[3] is not None else None,
        "oi_usd": float(r[4]) if r[4] is not None else None,
        "unit_quantity": OI_UNIT,
        "unit_notional": "USDT（名义价值）",
        "unit_evidence": OI_UNIT_EVIDENCE,
        "source": r[5],
        "fetched_at": fetched,
        "fetched_at_utc": _iso(fetched),
        "fetch_lag_sec": (round((fetched - (ts + BUCKET_MS)) / 1000.0, 3)
                          if fetched is not None else None),
        # 相对「桶收盘那一刻」的可得性判定（不是相对某个真实决策时刻）
        "availability_vs_bucket_close": status,
        "available_at_proven": status in (AVAIL_PROVEN, AVAIL_LATE),
        "available_at_rule": "fetched_at 存在且距桶结束 <= %.0f 秒" % (TIMELY_MAX_MS / 1000),
    }


def oi_growth(ts: np.ndarray, vals: np.ndarray, decision_ms: int,
              lookback_ms: int, available_at: Optional[np.ndarray] = None) -> dict:
    """算「决策时刻」相对「决策 - 回看窗」的持仓数量增幅。

    ⚠ 与状态机**必须同一口径**（ACCUM-EARLY-02 修正）
    -------------------------------------------------
    状态机取的是「**目标时刻已经可得**的最新观测」（`features._as_of_index`：
    在 `available_at <= 目标` 的行里取观测时刻最大的一行）。
    本函数原来用的是「第一个 ts >= 目标」的分桶取法，两者在**观测有缺口或滞后**时
    会差出整整一个桶 —— 旧报告里的 -0.00863777 就是这么算出来的，
    它并不等于状态机会读到的值。现在：
      · 传 `available_at` ⇒ 与状态机同一口径（**推荐，报告也用它**）；
      · 不传 ⇒ 退回旧的 ts 分桶口径，并在 `caliber` 字段里写明，避免再被当成决策值。
    """
    ts = np.asarray(ts)
    vals = np.asarray(vals, dtype=np.float64)
    if len(ts) == 0:
        return {"status": "oi_empty", "growth": None, "oi_curr": None, "oi_past": None,
                "caliber": None}

    if available_at is not None:
        av = np.asarray(available_at, dtype=np.float64)

        def _pick(target: int) -> int:
            ok = np.isfinite(av) & (av <= float(target))
            if not ok.any():
                return -1
            idx = np.flatnonzero(ok)
            return int(idx[np.argmax(ts[idx])])

        i_curr = _pick(int(decision_ms))
        if i_curr < 0:
            return {"status": "oi_no_current", "growth": None, "oi_curr": None,
                    "oi_past": None, "caliber": "available_at"}
        target_past = int(decision_ms) - int(lookback_ms)
        i_past = _pick(target_past)
        if i_past < 0 or i_past == i_curr:
            return {"status": "oi_no_past", "growth": None,
                    "oi_curr": float(vals[i_curr]), "oi_past": None,
                    "caliber": "available_at"}
        past, curr = float(vals[i_past]), float(vals[i_curr])
        return {
            "status": "ok",
            "growth": (curr - past) / past if past else None,
            "oi_curr": curr, "oi_past": past,
            "curr_utc": _iso(int(ts[i_curr])), "past_utc": _iso(int(ts[i_past])),
            "gap_sec": round((int(ts[i_curr]) - int(ts[i_past])) / 1000.0, 1),
            "caliber": "available_at（与状态机同口径）",
        }

    # ── 旧口径（仅用于复现历史报告，不要当决策值）──
    i_curr = int(np.searchsorted(ts, int(decision_ms) - BUCKET_MS, side="right")) - 1
    if i_curr < 0:
        return {"status": "oi_no_current", "growth": None, "oi_curr": None,
                "oi_past": None, "caliber": "ts_bucket_legacy"}
    target = int(ts[i_curr]) - int(lookback_ms)
    i_past = int(np.searchsorted(ts, target, side="left"))
    if i_past >= len(ts) or i_past == i_curr:
        return {"status": "oi_no_past", "growth": None,
                "oi_curr": float(vals[i_curr]), "oi_past": None,
                "caliber": "ts_bucket_legacy"}
    past = float(vals[i_past])
    curr = float(vals[i_curr])
    return {
        "status": "ok",
        "growth": (curr - past) / past if past else None,
        "oi_curr": curr, "oi_past": past,
        "curr_utc": _iso(int(ts[i_curr])), "past_utc": _iso(int(ts[i_past])),
        "gap_sec": round((int(ts[i_curr]) - int(ts[i_past])) / 1000.0, 1),
        "caliber": "ts_bucket_legacy（旧口径：第一个 ts>=目标 做分母；仅复现历史报告用）",
    }

# -*- coding: utf-8 -*-
"""ACCUM-EARLY-02｜市场结构指标读取器（只读，研究 + 观察证据）。

读什么
------
`数据/市场数据/parquet/metrics/binance/um/<SYMBOL>/<YEAR>.parquet`，列：
    symbol, ts,
    sum_open_interest, sum_open_interest_value,
    retail_account_ls_ratio, top_position_ls_ratio, count_long_short_ratio,
    sum_toptrader_long_short_ratio, count_toptrader_long_short_ratio,
    sum_taker_long_short_vol_ratio

⚠ 这一层**只进证据，不进决策**，原因必须写清楚
----------------------------------------------
1. **没有发布时点，也没有抓取时点**：parquet 只有 `ts`（5 分钟桶起点），
   既没有 `fetched_at`，也没有交易所发布时间 ⇒ 按项目 PIT 规则，
   「当时是否可得」**无法证明**，只能是 `unproven`。
2. 归档是**离线导出**的（`数据/市场数据/parquet/_export_state.json`），
   导出时刻通常远晚于桶时间 ⇒ 用它做决策等于用未来数据。
3. 因此本模块返回的每一行都带 `availability="unproven"`，
   调用方只能把它写进「证据记录」，**不得**让它参与开仓判定。
   要让它进决策，必须先拿到逐行可得时点证据（同 `oi_feed` 的做法），那是另一件事。

`ts` 语义与 `oi_history` 相同：5 分钟桶的**起点**，桶收盘 = ts + 300000ms。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

BUCKET_MS = 300_000
EXCHANGE, MARKET = "binance", "um"

#: 三类多空比（工单要求的「三类」）+ 主动买卖
RATIO_COLUMNS = {
    "retail_account_ls_ratio": "散户账户多空比（accounts）",
    "top_position_ls_ratio": "大户持仓量多空比（positions）",
    "count_long_short_ratio": "全局账户数多空比（accounts）",
    "sum_toptrader_long_short_ratio": "大户账户多空比（accounts）",
    "count_toptrader_long_short_ratio": "大户账户数多空比（count）",
}
TAKER_COLUMN = "sum_taker_long_short_vol_ratio"

AVAILABILITY = "unproven"
AVAILABILITY_NOTE = ("parquet 只有桶起点 ts，无发布时点、无抓取时点；"
                     "归档为离线导出 ⇒ 当时是否可得无法证明，只能作证据")
#: 超过这个秒数就标「陈旧」。指标是 5 分钟桶，2 小时没新数据已经不正常。
STALE_WARN_SEC = 2 * 3600

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = _PROJECT / "数据" / "市场数据" / "parquet"


def _iso(ms) -> Optional[str]:
    if ms is None:
        return None
    import datetime as dt
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def metrics_path(symbol: str, year: int, root=None) -> Path:
    return Path(root or DEFAULT_ROOT) / "metrics" / EXCHANGE / MARKET / symbol / f"{year}.parquet"


def _year_of(ms: int) -> int:
    import datetime as dt
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).year


def read_metrics_tail(symbol: str, as_of_ms: int, root=None,
                      max_row_groups: int = 3) -> List[dict]:
    """只读最后几个 row group，取 `ts <= as_of_ms` 的行（按 ts 升序）。

    为什么只读尾部：观察区每轮都要给几百个事件附证据，全量读一年的 parquet
    （实测 8 万行/文件）会把轮询拖垮。指标是 5 分钟粒度，尾部几个 row group
    足够覆盖「最近一根已处理 1h」。
    """
    try:
        import pyarrow.parquet as pq
    except Exception:                                            # noqa: BLE001
        return []
    p = metrics_path(symbol, _year_of(as_of_ms), root=root)
    if not p.exists():
        # 跨年边界：也试一下上一年
        p2 = metrics_path(symbol, _year_of(as_of_ms) - 1, root=root)
        if not p2.exists():
            return []
        p = p2
    try:
        f = pq.ParquetFile(p)
        n = f.metadata.num_row_groups
        lo = max(0, n - int(max_row_groups))
        rows: List[dict] = []
        for g in range(lo, n):
            for r in f.read_row_group(g).to_pylist():
                if int(r["ts"]) <= int(as_of_ms):
                    rows.append(r)
        rows.sort(key=lambda x: int(x["ts"]))
        return rows
    except Exception:                                            # noqa: BLE001
        return []


def _latest_nonnull(rows: List[dict], col: str):
    """返回 (row, index) —— 该列**最近一条非空**记录，没有则 (None, -1)。

    为什么不能直接用最后一行：实测尾部最新一根（如 2026-10-14T23:55Z）的
    `sum_taker_long_short_vol_ratio` 是 `None`，而 23:50 那根有值。
    若直接取最后一行，会把「本轮没采到」误报成「根本没有这个指标」；
    若把上一根的值填进本轮，又变成「用旧值冒充新值」。
    正确做法：**逐列回退到最近非空，并把该列自己的观测时刻写出来**，
    让读的人一眼看出这一项是不是本轮的。
    """
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].get(col) is not None:
            return rows[i], i
    return None, -1


def latest_metrics(symbol: str, as_of_ms: int, root=None,
                   max_row_groups: int = 3) -> dict:
    """`as_of_ms` 之前最新一条指标（**可得性 unproven**，只作证据）。

    取值口径（每条都写进返回结构，不靠调用方猜）：
      · 尾部最新一行 = `最新行`，它的时刻就是「本轮观测时刻」；
      · 每一项指标**逐列**回退到最近一条非空值，并标注 `取自最新行` 与
        该项自己的 `观测时刻_utc` / `距决策_秒`；
      · 最新行为空但历史有值的列 ⇒ 记入 `尾部空值`（本轮没采到）；
      · 整段尾部都没有非空值的列 ⇒ 记入 `缺失`，值给 `None`，**不填 0**。
    """
    cols = [TAKER_COLUMN] + list(RATIO_COLUMNS)
    rows = read_metrics_tail(symbol, as_of_ms, root=root,
                             max_row_groups=max_row_groups)
    # 尾部几个 row group 里整列都空 ⇒ 扩大读取范围再找一次（只读、无副作用）
    if rows and all(_latest_nonnull(rows, c)[0] is None for c in cols):
        rows = read_metrics_tail(symbol, as_of_ms, root=root, max_row_groups=40)
    if not rows:
        return {"found": False, "reason": "no_rows_before_as_of",
                "as_of_utc": _iso(as_of_ms), "availability": AVAILABILITY,
                "availability_note": AVAILABILITY_NOTE, "参与决策": False}

    head = rows[-1]
    ts = int(head["ts"])
    last_i = len(rows) - 1

    def _field(col: str, meaning: str) -> dict:
        row, idx = _latest_nonnull(rows, col)
        if row is None:
            return {"指标": col, "含义": meaning, "值": None,
                    "观测时刻_utc": None, "距决策_秒": None,
                    "取自最新行": False, "落后最新行_个桶": None,
                    "缺失": "尾部窗口内该列全为空"}
        t = int(row["ts"])
        return {"指标": col, "含义": meaning, "值": float(row[col]),
                "观测时刻_utc": _iso(t),
                "距决策_秒": round((int(as_of_ms) - t) / 1000.0, 1),
                "取自最新行": bool(idx == last_i),
                "落后最新行_个桶": int(last_i - idx),
                "缺失": (None if idx == last_i
                        else f"最新行({_iso(ts)})该列为空，已回退到 {_iso(t)}")}

    taker = _field(TAKER_COLUMN, "5 分钟内主动买量 / 主动卖量（>1 主动买占优）")
    ratios = {k: _field(k, v) for k, v in RATIO_COLUMNS.items()}

    used_ts = [int(rows[i]["ts"]) for i in
               {_latest_nonnull(rows, c)[1] for c in cols} if i >= 0]
    tail_null = [c for c in cols if head.get(c) is None]
    gone = [c for c in cols if _latest_nonnull(rows, c)[0] is None]

    # 陈旧度：**必须显式写出来**。实测指标 parquet 最新一根停在
    # 2026-10-14T23:55Z，而决策时刻在 2026-10-26 ⇒ 差 11.4 天。
    # 不标出来的话，「证据里有值」会被读成「当前市场的值」，那是撒谎。
    lag_s = round((int(as_of_ms) - ts) / 1000.0, 1)
    stale_h = round(lag_s / 3600.0, 2)
    stale = bool(lag_s > STALE_WARN_SEC)

    return {
        "found": True,
        # 兼容旧调用方：顶层 ts/ts_utc/距观测_秒 = 尾部最新一行的时刻
        "ts": ts, "ts_utc": _iso(ts),
        "bucket_close_utc": _iso(ts + BUCKET_MS),
        "as_of_utc": _iso(as_of_ms),
        "距观测_秒": lag_s,
        "陈旧": stale,
        "陈旧_小时": stale_h,
        "陈旧说明": (f"指标最新一根停在 {_iso(ts)}，距决策时刻 {stale_h} 小时"
                     f"（阈值 {STALE_WARN_SEC / 3600:.0f} 小时）⇒ 记录的是旧市场，"
                     "不是当前市场；采集侧需排查" if stale else None),
        "最新行": {"ts": ts, "ts_utc": _iso(ts),
                   "bucket_close_utc": _iso(ts + BUCKET_MS),
                   "距观测_秒": round((int(as_of_ms) - ts) / 1000.0, 1),
                   "该行为空的列": tail_null},
        "主动买卖": taker,
        "三类多空比": ratios,
        "持仓量_同源对照": {
            "sum_open_interest": (float(head["sum_open_interest"])
                                  if head.get("sum_open_interest") is not None else None),
            "sum_open_interest_value": (float(head["sum_open_interest_value"])
                                        if head.get("sum_open_interest_value") is not None else None),
            "取自行": "最新行",
        },
        "来源": f"parquet metrics/{EXCHANGE}/{MARKET}/{symbol}/{_year_of(ts)}.parquet",
        "时间范围": {"from_utc": _iso(min(used_ts)) if used_ts else None,
                     "to_utc": _iso(max(used_ts)) if used_ts else None,
                     "说明": "各项实际取值的时刻区间（逐列回退后的最小/最大）"},
        "availability": AVAILABILITY,
        "availability_note": AVAILABILITY_NOTE,
        "参与决策": False,
        "尾部空值": tail_null,
        "缺失": gone,
        "读法": ("`取自最新行=False` 的项是**回退值**，不是本轮观测；"
                 "`尾部空值` 是本轮没采到、但历史有值的列；"
                 "`缺失` 是整段尾部都没有值的列。"),
    }


def phase_avg_price(symbol: str, from_ms: int, to_ms: int,
                    timeframe: str = "1h", hot_db=None) -> dict:
    """阶段均价：`[from_ms, to_ms]` 内已收盘 K 线的**成交量加权均价**。

    公式：`Σ(quote_volume) / Σ(volume)`。这是「这一阶段市场平均成交在什么价位」，
    不是简单收盘价均值（后者会被极端 bar 拉偏）。
    只用 `candles` 视图（= candle_versions JOIN candle_heads，即**头版本**），
    并且只取 `is_closed=1` 且 `close_time <= to_ms` 的 bar —— 不读未收盘。
    """
    db = str(hot_db or (_PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        r = con.execute(
            """SELECT COUNT(*), SUM(CAST(quote_volume AS REAL)), SUM(CAST(volume AS REAL)),
                      MIN(open_time), MAX(close_time)
               FROM candles
               WHERE exchange=? AND market=? AND symbol=? AND timeframe=?
                 AND is_closed=1 AND open_time>=? AND close_time<=?""",
            (EXCHANGE, MARKET, symbol, timeframe, int(from_ms), int(to_ms))).fetchone()
    finally:
        con.close()
    n, qv, vol, first, last = r
    n = int(n or 0)
    vwap = (float(qv) / float(vol)) if (qv and vol) else None
    if n:
        miss = None
    elif int(from_ms) >= int(to_ms):
        miss = ("阶段窗口起点≥终点（事件刚建档、还没有已收盘 bar 落在窗口内）；"
                "这不是数据缺失，是窗口还没长出来")
    else:
        miss = "窗口内没有已收盘 bar（检查该标的该周期是否有数据）"
    return {
        "窗口_utc": {"from": _iso(from_ms), "to": _iso(to_ms)},
        "周期": timeframe,
        "bar数": n,
        "首根_utc": _iso(first) if first is not None else None,
        "末根_utc": _iso(last) if last is not None else None,
        "Σ成交额": (float(qv) if qv is not None else None),
        "Σ成交量": (float(vol) if vol is not None else None),
        "阶段均价": vwap,
        "公式": "Σquote_volume / Σvolume（成交量加权均价）",
        "来源": "market_v2.sqlite3 `candles` 视图（头版本、已收盘）",
        "缺失": miss,
        "参与决策": False,
        "note": "均价只描述阶段位置，不是入场价、不是建议",
    }


def alias_check(symbols: List[str], as_of_ms: int, root=None) -> Dict[str, object]:
    """核对「三类多空比」里有没有列其实是同一份数据（别名）。

    实测发现 `retail_account_ls_ratio` 与 `count_long_short_ratio`、
    `top_position_ls_ratio` 与 `sum_toptrader_long_short_ratio` 常常逐位相同。
    如果确实相同，就不能把它们当成「三个独立因子」来数。
    """
    pairs = [("retail_account_ls_ratio", "count_long_short_ratio"),
             ("top_position_ls_ratio", "sum_toptrader_long_short_ratio")]
    res = {f"{a}=={b}": {"same": 0, "diff": 0} for a, b in pairs}
    total = 0
    for sym in symbols:
        for r in read_metrics_tail(sym, as_of_ms, root=root, max_row_groups=2):
            total += 1
            for a, b in pairs:
                va, vb = r.get(a), r.get(b)
                key = f"{a}=={b}"
                if va is None or vb is None:
                    continue
                if abs(float(va) - float(vb)) < 1e-12:
                    res[key]["same"] += 1
                else:
                    res[key]["diff"] += 1
    return {"抽样行数": total, "pairs": res,
            "读法": ("same/diff 反映两列是否逐位相同；"
                     "若 same 远大于 diff，说明是别名，不得当作独立因子")}

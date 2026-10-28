#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[W-002 信号层] 生产信号引擎（FT-LOW4H 事件趋势）真实行情诊断。

生产信号层用的是 `event_trend_v0_1.state_machine.EventStateMachine`
（BOX→SWEEP→RETEST→BREAKOUT→候选），不是研究侧的 4a/4b/4c。
本脚本用**真实行情库**逐根推进这台状态机，回答一件事：

    RETEST 之后为什么一个候选都没有？

对每个 RETEST 事件记录：
  · 回踩确认那根的收盘 vs 突破位 H+0.1A（是否「一确认就已经在突破位上方」）
  · 之后每根 1h 的 close 与突破位的关系，以及首次穿越是否被量能/OI 挡住
  · 事件最终终结状态与原因

只读真实行情库；不写任何生产库。

用法：
    /usr/bin/python3 evidence/W-002/scripts/diag_ftlow4h.py BTCUSDT ETHUSDT ...
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
for _p in (str(BACKEND), str(PROJECT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                    # noqa: E402

import accum_early_entry as A                         # noqa: E402
import unified_runtime as UR                          # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig   # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import (           # noqa: E402
    EventStateMachine, EventState)

H = 3_600_000
DAY = 86_400_000


def ms(s: str) -> int:
    import calendar
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def iso(v) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(v) / 1000)) if v else "—"


def run_symbol(sym: str, feed, start: int, end: int, warmup_days: int = 90,
               with_oi: bool = True, oi_filter: bool = True,
               oi_max_dist_sec: int | None = None) -> dict:
    bars4 = feed.load("4h", sym, start - warmup_days * DAY, end)
    bars1 = feed.load("1h", sym, start - warmup_days * DAY, end)
    cfg = EventTrendConfig()
    if oi_max_dist_sec is not None:
        # 只用于「敏感性验证」：把可得距离放宽，看 OI 是否还挡得住。
        # 不写回任何配置，也不影响生产。
        cfg = cfg.model_copy(update={"oi_max_time_distance_sec": int(oi_max_dist_sec)}) \
            if hasattr(cfg, "model_copy") else cfg
        if not hasattr(cfg, "model_copy"):
            object.__setattr__(cfg, "oi_max_time_distance_sec", int(oi_max_dist_sec))
    sm = EventStateMachine(cfg, symbol=sym, enable_oi_filter=oi_filter)
    atr = UR._WilderATR(14)

    # ── 持仓量：完全复刻 unified_runtime._load_oi_window / _oi_slice_as_of ──
    oi_ts = np.array([], dtype=np.int64)
    oi_vals = np.array([], dtype=np.float64)
    oi_avail = np.array([], dtype=np.float64)
    oi_rows = 0
    oi_rows_no_fetch = 0
    if with_oi:
        import oi_feed as OF
        try:
            rows = OF.load_oi_rows(sym, int(start - warmup_days * DAY), int(end))
            kept, _dup, _bad = OF._dedupe_by_ts(rows)
            ts, vals, av = [], [], []
            for r in kept:
                f = r.get("fetched_at")
                if f is None:
                    oi_rows_no_fetch += 1
                av.append(float(f) if f is not None else float("nan"))
                ts.append(int(r["ts"]))
                vals.append(float(r["oi"]))
            oi_ts = np.array(ts, dtype=np.int64)
            oi_vals = np.array(vals, dtype=np.float64)
            oi_avail = np.array(av, dtype=np.float64)
            oi_rows = len(ts)
        except Exception:                                  # noqa: BLE001
            pass

    def oi_slice_as_of(decision_ms: int):
        empty = (np.array([], dtype=np.int64), np.array([], dtype=np.float64),
                 np.array([], dtype=np.float64))
        if oi_ts.size == 0:
            return empty
        d = int(decision_ms)
        lo = d - int(cfg.oi_lookback_hours) * H - DAY
        i_lo = int(np.searchsorted(oi_ts, lo, side="left"))
        i_hi = int(np.searchsorted(oi_ts, d - 300_000, side="right"))
        if i_hi <= i_lo:
            return empty
        av_w = oi_avail[i_lo:i_hi]
        ok = np.isfinite(av_w) & (av_w <= float(d))
        if not ok.any():
            return empty
        return oi_ts[i_lo:i_hi][ok], oi_vals[i_lo:i_hi][ok], av_w[ok]

    highs4, lows4, closes4 = [], [], []
    atr_seen = 0
    i4 = 0
    retest_marks: list[dict] = []
    zero_oi = np.array([], dtype=np.float64)

    for b1 in bars1:
        cl = int(b1["close_time"])
        # 先把「本根 1h 收盘时已收盘」的 4h 处理掉
        while i4 < len(bars4) and int(bars4[i4]["close_time"]) <= cl:
            b4 = bars4[i4]
            a = atr.update(float(b4["high"]), float(b4["low"]), float(b4["close"]))
            highs4.append(float(b4["high"]))
            lows4.append(float(b4["low"]))
            closes4.append(float(b4["close"]))
            if a is not None:
                atr_seen += 1
            if a is not None and atr_seen >= int(cfg.atr_warmup_bars) and len(closes4) >= cfg.box_bars_4h:
                sm.check_and_form_box(
                    int(b4["close_time"]),
                    np.array(highs4[-cfg.box_bars_4h:], dtype=np.float64),
                    np.array(lows4[-cfg.box_bars_4h:], dtype=np.float64),
                    np.array(closes4[-cfg.box_bars_4h:], dtype=np.float64),
                    float(a))
            i4 += 1

        if cl < start or cl > end:
            continue

        ev_before = sm.active_event
        decision = int(b1["open_time"]) + H
        ots, ov, oa = oi_slice_as_of(decision)
        snap = sm.process_1h_bar(
            int(b1["open_time"]), float(b1["open"]), float(b1["high"]),
            float(b1["low"]), float(b1["close"]), float(b1.get("quote_volume") or 0.0),
            ots, ov, oi_available_at=oa)

        # RETEST 期间逐根留痕：close 与突破位的关系（判断「首次穿越」会不会被吃掉）
        ev = sm.active_event or ev_before
        if ev is not None and ev.current_state == EventState.RETEST:
            Hh = float(ev.box.high_h)
            Aa = float(ev.box.atr_a)
            lvl = Hh + float(cfg.breakout_buffer_atr) * Aa
            if len(sm.h1_closes) >= 2:
                prev = float(sm.h1_closes[-2])
                cur = float(sm.h1_closes[-1])
                if cur > lvl and prev > lvl:
                    retest_marks.append({"t": iso(cl), "kind": "已在突破位上方（非首次穿越）",
                                         "cur": cur, "prev": prev, "level": lvl})

    # 统计
    tr = Counter()
    flow = Counter()
    for t in sm.transitions:
        tr[str(t.to_state)] += 1
        flow[f"{t.from_state}→{t.to_state}"] += 1
    reasons = Counter()
    for t in sm.transitions:
        if str(t.to_state) in ("EventState.EXPIRED", "EventState.INVALIDATED",
                               "EventState.VOLUME_REJECTED", "EventState.MISSED_WITHOUT_RETEST"):
            reasons[f"{t.from_state}→{t.to_state}: {t.reason}"] += 1
    end_reason = {}
    for e in sm.completed_events:
        end_reason[str(e.current_state)] = end_reason.get(str(e.current_state), 0) + 1
    return {
        "symbol": sym,
        "bars": {"4h": len(bars4), "1h": len(bars1)},
        "OI": {"行数": oi_rows, "无抓取时刻行数": oi_rows_no_fetch},
        "迁移": dict(tr),
        "状态流": dict(flow),
        "终结原因": dict(reasons),
        "终结状态": end_reason,
        "候选数": len(sm.candidates),
        "活跃事件残留": (None if sm.active_event is None
                    else {"state": str(sm.active_event.current_state),
                          "box": [sm.active_event.box.high_h, sm.active_event.box.low_l]}),
        "RETEST期间已在突破位上方根数": len(retest_marks),
        "样例": retest_marks[:5],
    }


def main() -> int:
    args = sys.argv[1:]
    start_s, end_s = "2026-06-27T08:00:00Z", "2026-10-26T09:00:00Z"
    if "--start" in args:
        start_s = args[args.index("--start") + 1]
    if "--end" in args:
        end_s = args[args.index("--end") + 1]
    skip = {start_s, end_s}
    oi_filter = "--no-oi" not in args
    with_oi = oi_filter
    oi_max_dist = None
    if "--oi-max-dist" in args:
        oi_max_dist = int(args[args.index("--oi-max-dist") + 1])
        skip.add(str(oi_max_dist))
    syms = [a for a in args if not a.startswith("-") and a not in skip]
    start, end = ms(start_s), ms(end_s)
    feed = A.DataFeed()
    if not syms:
        import sqlite3
        from pathlib import Path as _P
        mdb = _P("/Users/boos/Desktop/威科夫因子共振趋势/数据/市场数据/db/market_v2.sqlite3")
        con = sqlite3.connect(f"file:{mdb}?mode=ro", uri=True)
        try:
            syms = [r[0] for r in con.execute(
                "SELECT DISTINCT symbol FROM candles WHERE exchange='binance' AND market='um' "
                "AND timeframe='1h' ORDER BY symbol")]
        finally:
            con.close()
    print(f"标的数 {len(syms)}  窗口 {iso(start)} → {iso(end)}  "
          f"OI门槛={'开(A组)' if oi_filter else '关(B组)'}"
          f"{('  可得距离=' + str(oi_max_dist) + '秒') if oi_max_dist else ''}")
    out = []
    agg_flow: Counter = Counter()
    agg_reason: Counter = Counter()
    total_cand = 0
    retest_total = 0
    for s in syms:
        try:
            r = run_symbol(s, feed, start, end, with_oi=with_oi, oi_filter=oi_filter,
                           oi_max_dist_sec=oi_max_dist)
        except Exception as exc:                       # noqa: BLE001
            out.append({"symbol": s, "error": repr(exc)})
            continue
        out.append(r)
        for k, v in (r.get("状态流") or {}).items():
            agg_flow[k] += v
        for k, v in (r.get("终结原因") or {}).items():
            agg_reason[k] += v
        total_cand += r["候选数"]
        retest_total += (r.get("迁移") or {}).get("EventState.RETEST", 0)

    print("\n===== 全池汇总 =====")
    print(f"标的 {len(syms)}  候选合计 {total_cand}  RETEST 迁移合计 {retest_total}")
    print("状态流：")
    for k, v in agg_flow.most_common():
        print(f"  {k:<40} {v}")
    print("终结原因：")
    for k, v in agg_reason.most_common(15):
        print(f"  {k:<70} {v}")
    p = PROJECT / "evidence" / "W-002" / "out" / "ftlow4h_diag.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"窗口": [iso(start), iso(end)], "标的数": len(syms),
                             "候选合计": total_cand, "状态流汇总": dict(agg_flow),
                             "终结原因汇总": dict(agg_reason),
                             "逐标的": out}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

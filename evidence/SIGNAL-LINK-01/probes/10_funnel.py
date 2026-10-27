#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-LINK-01 探针①：状态机漏斗（只读）

回答一个问题：**526 个标的、全部可用历史，信号到底死在哪一道关口？**

做法（不改任何代码、不写任何库）：
  · 用**生产同一份**状态机类 `EventStateMachine` + 同一份 `EventTrendConfig()` 默认值；
  · 用**生产同一份** SQL 读闭合 K 线（与 `PoolMarketWatcher.get_closed_bars` 逐字一致）；
  · 按 `RealSignalEngine.process_market` 的时序规则做**一次顺序回放**
    （4h 与 1h 按收盘时刻合并，同刻先 4h 后 1h）；
  · 统计 `sm.transitions` 里每个 `to_state` 的条数 ⇒ 漏斗；
  · 统计 `sm.completed_events` 的 `termination_reason` ⇒ 淘汰原因。

纪律：
  · 行情库**只读**打开（`mode=ro`），live 库**完全不碰**；
  · 不写 `paper_*`、不写 `live_signal`、不碰闸门；
  · 所有数字可由 `out/funnel.json` 机械复算。

用法：
  python 10_funnel.py [--days 60] [--limit 0] [--out .../out/funnel.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig  # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.features import (  # noqa: E402
    compute_wilder_atr_series,
)
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import (  # noqa: E402
    EventStateMachine,
)

H1 = 3_600_000
DAY = 24 * H1
EXCHANGE = "binance"
MARKET = "um"
MARKET_DB = ROOT / "数据/市场数据/db/market_v2.sqlite3"

# 与生产一致的标的池口径（唯一事实源）
BACKEND = ROOT / "框架/backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _ro_con():
    con = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row      # 与 live_store.market_conn() 一致，dict(r) 才可用
    return con


def read_closed_bars(con, symbol: str, timeframe: str,
                     start_open_ms: int, end_open_ms: int) -> list:
    """与 `PoolMarketWatcher.get_closed_bars` **逐字相同**的读法。"""
    cur = con.execute(
        "SELECT open_time, close_time, open, high, low, close, volume, quote_volume "
        "FROM candles WHERE exchange=? AND market=? AND is_closed=1 AND symbol=? "
        "AND timeframe=? AND open_time>=? AND close_time<=? ORDER BY open_time ASC",
        (EXCHANGE, MARKET, symbol, timeframe, int(start_open_ms), int(end_open_ms)))
    return [dict(r) for r in cur.fetchall()]


def replay_symbol(sym: str, b4: list, b1: list, config) -> dict:
    """一次顺序回放 = `RealSignalEngine.process_market` 的等价单遍实现。

    为什么等价：生产里 `process_market` 被反复调用，但每次只处理
    「不在 processed_4h_closes / processed_1h_opens 里的 bar」，因此把全部调用
    合并起来，就是**按收盘时刻把 4h 与 1h 各处理一次**（同刻 4h 先于 1h）。
    """
    sm = EventStateMachine(config=config, symbol=sym, enable_oi_filter=False)
    n = config.box_bars_4h

    events = [(int(b["close_time"]), 0, i, b) for i, b in enumerate(b4)]
    events += [(int(b["close_time"]), 1, i, b) for i, b in enumerate(b1)]
    events.sort(key=lambda e: (e[0], e[1]))

    cands = []
    crossings = 0          # RETEST 状态下「首次向上穿越」次数（含量能不足）
    for cl_ms, kind, i, bar in events:
        if kind == 0:
            if i + 1 >= n:
                seg = b4[i + 1 - n:i + 1]
                h = np.array([float(x["high"]) for x in seg])
                lo = np.array([float(x["low"]) for x in seg])
                c = np.array([float(x["close"]) for x in seg])
                atr = compute_wilder_atr_series(h, lo, c, period=config.atr_period_4h)
                value = float(atr[-1]) if len(atr) else 0.0
                if np.isfinite(value) and value > 0:
                    sm.check_and_form_box(cl_ms, h, lo, c, value)
            continue
        before = len(sm.transitions)
        cand = sm.process_1h_bar(
            open_time_ms=int(bar["open_time"]), open_p=float(bar["open"]),
            high_p=float(bar["high"]), low_p=float(bar["low"]),
            close_p=float(bar["close"]), volume_v=float(bar["volume"]),
            oi_timestamps=np.array([], dtype=np.int64),
            oi_values=np.array([], dtype=np.float64))
        if cand is not None:
            cands.append(cand)
        # 本根是否发生过穿越（含被量能打回的）
        for t in sm.transitions[before:]:
            if t.to_state in ("VOLUME_REJECTED", "OI_UNAVAILABLE", "OI_REJECTED",
                              "CANDIDATE"):
                crossings += 1
                break

    return {
        "symbol": sym,
        "bars_4h": len(b4),
        "bars_1h": len(b1),
        "transitions": [{"to": t.to_state, "reason": t.reason,
                         "at": t.trigger_bar_close_ms} for t in sm.transitions],
        "terminations": [{"end": e.current_state.value,
                          "reason": e.termination_reason,
                          "at": e.ended_at_ms} for e in sm.completed_events],
        "candidates": [{"candidate_id": c.candidate_id, "symbol": c.symbol,
                        "snapshot_time_ms": c.snapshot_time_ms,
                        "available_at_ms": c.available_at_ms,
                        "breakout_close": c.breakout_close,
                        "volume_ratio": c.volume_ratio,
                        "box_high_h": c.box_high_h, "box_low_l": c.box_low_l,
                        "box_atr_a": c.box_atr_a,
                        "initial_stop_price": c.initial_stop_price}
                       for c in cands],
        "crossings": crossings,
        "final_state": (sm.active_event.current_state.value
                        if sm.active_event else "IDLE"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=0,
                    help="回放窗口天数；0=全部可用历史")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个标的（调试）")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "out" / "funnel.json"))
    args = ap.parse_args()

    import live_pool as LP
    # ── 池口径必须与**生产运行时**一致 ──────────────────────────────────────
    # 生产走的是 `unified_runtime._default_universe()`：
    #   ① 先试 `live_pool.scope_symbols()`（要求 pool_source == 'scope_crypto_v1'）；
    #   ② 不成立则退回行情库 `candle_heads` 真实覆盖的标的（排除指数）。
    # 本探针直接调用同一个函数，避免「探针跑 375、生产跑 526」这种口径分叉。
    try:
        import unified_runtime as UR
        symbols = UR._default_universe()
        universe_source = "unified_runtime._default_universe()"
        _s2, _m2 = LP.scope_symbols()
        scope_pool_source = _m2.get("pool_source")
        scope_n = len(_s2)
    except Exception as exc:                             # noqa: BLE001
        symbols, _meta = LP.scope_symbols()
        universe_source = f"live_pool.scope_symbols()（_default_universe 不可用：{exc}）"
        scope_pool_source = _meta.get("pool_source")
        scope_n = len(symbols)
    if args.limit:
        symbols = symbols[:args.limit]

    con = _ro_con()
    try:
        r = con.execute(
            "SELECT MIN(open_time), MAX(close_time) FROM candles "
            "WHERE exchange=? AND market=? AND timeframe='1h' AND is_closed=1",
            (EXCHANGE, MARKET)).fetchone()
        data_min, data_max = int(r[0]), int(r[1])
        end_ms = data_max
        if args.days and args.days > 0:
            start_ms = end_ms - int(args.days * DAY)
        else:
            start_ms = data_min
        # 4h 需要多读 42 根做箱体窗口 + ATR，故 4h 起点再往前推
        start_4h = start_ms - 60 * 4 * H1

        config = EventTrendConfig()
        t0 = time.time()
        per_sym = []
        for i, sym in enumerate(symbols):
            b4 = read_closed_bars(con, sym, "4h", start_4h, end_ms)
            b1 = read_closed_bars(con, sym, "1h", start_ms, end_ms)
            if not b4 or not b1:
                per_sym.append({"symbol": sym, "bars_4h": len(b4), "bars_1h": len(b1),
                                "transitions": [], "terminations": [],
                                "candidates": [], "crossings": 0,
                                "final_state": "IDLE"})
                continue
            per_sym.append(replay_symbol(sym, b4, b1, config))
            if (i + 1) % 50 == 0:
                print(f"  [{i+1}/{len(symbols)}] {time.time()-t0:.1f}s", flush=True)
    finally:
        con.close()

    # ── 汇总 ──────────────────────────────────────────────────────────────
    to_state = {}
    reasons = {}
    cands_all = []
    final_state = {}
    crossings = 0
    for s in per_sym:
        for t in s["transitions"]:
            to_state[t["to"]] = to_state.get(t["to"], 0) + 1
        for t in s["terminations"]:
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        cands_all.extend(s["candidates"])
        crossings += s["crossings"]
        final_state[s["final_state"]] = final_state.get(s["final_state"], 0) + 1

    out = {
        "probe": "SIGNAL-LINK-01/10_funnel",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market_db": str(MARKET_DB),
        "market_db_sha256": None,
        "window": {
            "start_utc": _iso(start_ms), "end_utc": _iso(end_ms),
            "days": round((end_ms - start_ms) / DAY, 2),
            "days_requested": args.days,
        },
        "pool": {"n": len(symbols), "universe_source": universe_source,
                 "scope_pool_source": scope_pool_source, "scope_n": scope_n},
        "config": {"class": "EventTrendConfig()",
                   "box_bars_4h": config.box_bars_4h,
                   "er_threshold": config.er_threshold,
                   "box_width_atr": [config.box_min_width_atr, config.box_max_width_atr],
                   "sweep": {"max_hours": config.sweep_max_hours,
                             "overshoot_atr": config.sweep_max_overshoot_atr,
                             "min_recovery_atr": config.sweep_min_recovery_atr},
                   "retest": {"max_hours": config.retest_max_hours,
                              "max_zone_atr": config.retest_max_zone_atr,
                              "vol_multiplier": config.retest_vol_multiplier},
                   "breakout": {"buffer_atr": config.breakout_buffer_atr,
                                "vol_multiplier": config.breakout_vol_multiplier,
                                "vol_lookback": config.breakout_vol_lookback,
                                "max_hours_after_retest": config.breakout_max_hours_after_retest},
                   "enable_oi_filter": False,
                   "cooling_hours": config.cooling_hours},
        "funnel": {"to_state_counts": dict(sorted(to_state.items(), key=lambda kv: -kv[1])),
                   "termination_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
                   "breakout_crossings": crossings,
                   "candidates_total": len(cands_all),
                   "final_state_counts": dict(sorted(final_state.items(), key=lambda kv: -kv[1]))},
        "candidates": cands_all,
        "per_symbol": per_sym,
        "elapsed_s": round(time.time() - t0, 1),
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n===== 漏斗 =====")
    for k, v in out["funnel"]["to_state_counts"].items():
        print(f"  {k:24s} {v}")
    print(f"  突破穿越(含被打回)       {crossings}")
    print(f"  候选生成                 {len(cands_all)}")
    print("\n===== 淘汰原因 =====")
    for k, v in out["funnel"]["termination_reasons"].items():
        print(f"  {v:6d}  {k}")
    print("\n===== 当前状态 =====")
    for k, v in out["funnel"]["final_state_counts"].items():
        print(f"  {k:24s} {v}")
    if cands_all:
        print("\n===== 候选明细 =====")
        for c in cands_all:
            print(f"  {c['symbol']:12s} {_iso(c['snapshot_time_ms'])} "
                  f"close={c['breakout_close']} vol_ratio={c['volume_ratio']:.2f}")
    print(f"\n窗口 {out['window']['days']} 天 / {len(symbols)} 标的 / {out['elapsed_s']}s")
    print(f"输出：{p}")
    return 0


def _iso(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    sys.exit(main())

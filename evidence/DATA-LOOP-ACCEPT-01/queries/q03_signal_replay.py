# -*- coding: utf-8 -*-
"""q03｜独立复算：用同一状态机对全池当前数据重放，判定「无信号」是自然还是下游断开（只读）。

复算与 `unified_runtime.RealSignalEngine.process_market` 完全同序：
  4h 箱体与 1h 事件按 close_time 合并推进（kind=0 先），预热窗口
  = (atr_warmup_bars*4 + breakout_max_total_hours + cooling_hours) 小时，
  与 `run_forever` 的 `history_floor_ms` 同式；`enable_oi_filter=False`
  （与运行中 runtime 的默认构造一致，main() 未传该参数）。

判据：
  · 独立复算近期窗口出现 candidate，而运行时自报 signals_generated_total=0
    ⇒ 下游断开（工程缺陷）；
  · 独立复算同样 0 candidate ⇒ 自然市场无信号（单独标注，不计工程失败）。

本脚本不写任何账本、不 import paper_engine、不触碰 live/ 目录。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
OUT = Path(__file__).resolve().parents[1] / "out"
TZ = dt.timezone(dt.timedelta(hours=8))
H1 = 3_600_000

from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig  # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series  # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine  # noqa: E402

import numpy as np  # noqa: E402


def bj(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S")


def load_bars(con, symbol, tf, floor, upto):
    cur = con.execute(
        "SELECT open_time, close_time, open, high, low, close, volume FROM candles "
        "WHERE exchange='binance' AND market='um' AND is_closed=1 AND symbol=? AND timeframe=? "
        "AND open_time>=? AND close_time<=? ORDER BY open_time ASC",
        (symbol, tf, floor, upto))
    return [dict(zip(("open_time", "close_time", "open", "high", "low", "close", "volume"), r))
            for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只复算前 N 个标的（0=全池）")
    ap.add_argument("--symbols", default="", help="逗号分隔，指定标的")
    args = ap.parse_args()

    cfg = EventTrendConfig()
    now_ms = int(dt.datetime.now(TZ).timestamp() * 1000)
    warmup_ms = (cfg.atr_warmup_bars * 4 + cfg.breakout_max_total_hours + cfg.cooling_hours) * H1
    floor = max(0, now_ms - warmup_ms)

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = [r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM candle_heads WHERE exchange='binance' AND market='um' "
            "AND timeframe='1h' ORDER BY symbol")]
        syms = [s for s in syms if s not in ("ALLUSDT", "BTCDOMUSDT")]
        if args.limit:
            syms = syms[:args.limit]

    t0 = time.time()
    out = {"measured_at_ms": now_ms, "measured_at_bj": bj(now_ms),
           "warmup_hours": round(warmup_ms / H1, 1), "floor_bj": bj(floor),
           "enable_oi_filter": False, "symbols_declared": len(syms),
           "state_counter": {}, "skipped": {}, "candidates": [], "recent_bars": {},
           "per_symbol": []}
    counters = {}
    recent_cut = now_ms - 4 * H1  # 最近 4 小时内的 1h bar（含 3 根）

    for sym in syms:
        b4 = load_bars(con, sym, "4h", floor, now_ms)
        b1 = load_bars(con, sym, "1h", floor, now_ms)
        if len(b4) < cfg.box_bars_4h + 8 or len(b1) < 200:
            out["skipped"][sym] = f"4h={len(b4)} 1h={len(b1)}"
            continue
        sm = EventStateMachine(config=cfg, symbol=sym, enable_oi_filter=False)
        p4, p1 = set(), set()
        events = [(int(b["close_time"]), 0, i, b) for i, b in enumerate(b4) if int(b["close_time"]) not in p4]
        events += [(int(b["close_time"]), 1, i, b) for i, b in enumerate(b1) if int(b["open_time"]) not in p1]
        cands = []
        for cl_ms, kind, i, bar in sorted(events, key=lambda e: (e[0], e[1])):
            if cl_ms > now_ms:
                continue
            if kind == 0:
                n = cfg.box_bars_4h
                if i + 1 >= n:
                    seg = b4[i + 1 - n:i + 1]
                    h = np.array([float(x["high"]) for x in seg])
                    l = np.array([float(x["low"]) for x in seg])
                    c = np.array([float(x["close"]) for x in seg])
                    atr = compute_wilder_atr_series(h, l, c, period=cfg.atr_period_4h)
                    v = float(atr[-1]) if len(atr) else 0.0
                    if np.isfinite(v) and v > 0:
                        sm.check_and_form_box(cl_ms, h, l, c, v)
                p4.add(cl_ms)
                continue
            cand = sm.process_1h_bar(
                open_time_ms=int(bar["open_time"]), open_p=float(bar["open"]),
                high_p=float(bar["high"]), low_p=float(bar["low"]),
                close_p=float(bar["close"]), volume_v=float(bar["volume"]),
                oi_timestamps=np.array([], dtype=np.int64), oi_values=np.array([], dtype=np.float64))
            p1.add(int(bar["open_time"]))
            if cand is not None:
                cands.append({"symbol": sym, "bar_open_bj": bj(int(bar["open_time"])),
                              "close_bj": bj(int(bar["close_time"])),
                              "close": float(bar["close"]),
                              "fields": {k: str(v)[:80] for k, v in (cand.__dict__ or {}).items()}})
        st = sm.active_event.current_state.value if sm.active_event is not None else "IDLE"
        counters[st] = counters.get(st, 0) + 1
        out["per_symbol"].append({
            "symbol": sym, "state": st,
            "event_created_bj": bj(sm.active_event.created_at_ms) if sm.active_event is not None else None,
            "completed_events": len(sm.completed_events),
            "candidates_in_window": len(cands),
            "last_1h_open_bj": bj(int(b1[-1]["open_time"])),
            "recent_1h": [{"open_bj": bj(int(b["open_time"])), "close": float(b["close"]),
                           "low": float(b["low"]), "high": float(b["high"]), "vol": float(b["volume"])}
                          for b in b1 if int(b["open_time"]) >= recent_cut],
        })
        out["candidates"].extend(cands)

    out["state_counter"] = counters
    out["elapsed_sec"] = round(time.time() - t0, 1)
    out["candidates_total"] = len(out["candidates"])
    out["candidates_in_last_4h"] = sum(
        1 for c in out["candidates"] if int(dt.datetime.strptime(c["bar_open_bj"], "%Y-%m-%d %H:%M:%S")
                                            .replace(tzinfo=TZ).timestamp() * 1000) >= recent_cut)
    con.close()

    OUT.mkdir(parents=True, exist_ok=True)
    tag = "q03_signal_replay" + (f"_{args.limit}" if args.limit else "")
    (OUT / f"{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("measured_at_bj", "warmup_hours", "symbols_declared",
                                          "state_counter", "candidates_total",
                                          "candidates_in_last_4h", "elapsed_sec")},
                     ensure_ascii=False, indent=2))
    print("skipped:", len(out["skipped"]))


if __name__ == "__main__":
    main()

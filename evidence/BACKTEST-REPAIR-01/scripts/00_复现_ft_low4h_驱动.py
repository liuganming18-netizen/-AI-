#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BACKTEST-REPAIR-01 · 任务B 复现：FT-LOW4H 回测驱动接线

目的：把 `runner.py:617-630`（现状）的 FT-LOW4H 驱动方式单独拉出来跑，与
「按 1h 步进」和「回测自有状态机 + parquet OI」两种驱动做**同数据同窗口**对照，
用状态机自己吐出的计数说话，不靠候选数猜。

三种驱动：
  A. 现状（基线）：`for bar in df_4h: engine.process_market(bar.close_time, watcher)`
  B. 逐 1h 步进：`for bar in df_1h: engine.process_market(bar.close_time, watcher)`
  C. 回测自有状态机：`runner.run_state_machine_simulation(...)`，OI 用 parquet 序列

判据（工单第四节）：
  · 必须验证状态机接收到正确的历史输入 —— 看 `processed_1h_opens` 覆盖了多少根 1h；
  · 信号时间链满足因果 —— 看每个候选的 `execution_earliest_ms >= snapshot_time_ms`；
  · **不把候选数 > 0 当唯一成功条件**。

跑法：
    export MARKET_ARCHIVE=<真实 parquet 根>
    export MARKET_DB=<真实热库>
    export CONTRACTS_DIR=<合约快照目录>
    /usr/bin/python3 evidence/BACKTEST-REPAIR-01/scripts/00_复现_ft_low4h_驱动.py [--symbol BTCUSDT] [--start 2026-06-01] [--end 2026-09-30]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent
ROOT = EVIDENCE.parents[1]
BACKEND = ROOT / "框架" / "backend"
STRATEGY = ROOT / "策略"

for p in (str(BACKEND), str(STRATEGY)):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def _iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def _funnel(sm):
    from WyckoffMaster.event_trend_v0_1.contracts import EventState

    def cnt(st):
        return sum(1 for e in sm.completed_events if e.current_state == st)

    return {
        "boxes": len(sm.completed_events) + (1 if sm.active_event else 0),
        "completed_events": len(sm.completed_events),
        "sweeps": sum(1 for e in sm.completed_events if e.sweep_time_ms is not None),
        "retests": sum(1 for e in sm.completed_events if e.retest_time_ms is not None),
        "breakouts": sum(1 for e in sm.completed_events if e.breakout_time_ms is not None),
        "volume_rejected": cnt(EventState.VOLUME_REJECTED),
        "oi_rejected": cnt(EventState.OI_REJECTED),
        "oi_unavailable": cnt(EventState.OI_UNAVAILABLE),
        "expired": cnt(EventState.EXPIRED),
        "invalidated": cnt(EventState.INVALIDATED),
        "candidates": len(sm.candidates),
    }


def _causality(cands):
    """逐候选检查时间链：执行不得早于快照，快照不得晚于可得时刻。"""
    bad = []
    for c in cands:
        snap = int(getattr(c, "snapshot_time_ms", 0) or 0)
        avail = int(getattr(c, "available_at_ms", 0) or 0)
        exe = int(getattr(c, "execution_earliest_ms", 0) or 0)
        if exe < snap:
            bad.append({"uid": getattr(c, "signal_uid", None),
                        "reason": "execution_earliest < snapshot_time",
                        "snapshot": _iso(snap), "execution_earliest": _iso(exe)})
        if avail > exe:
            bad.append({"uid": getattr(c, "signal_uid", None),
                        "reason": "available_at > execution_earliest",
                        "available_at": _iso(avail), "execution_earliest": _iso(exe)})
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--out", default=str(EVIDENCE / "out" / "00_复现_ft_low4h_驱动.json"))
    args = ap.parse_args()

    from WyckoffMaster.event_trend_v0_1 import EventTrendRunner
    from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig, make_ft_low4h_config

    env_report = {
        "MARKET_ARCHIVE": os.environ.get("MARKET_ARCHIVE"),
        "MARKET_DB": os.environ.get("MARKET_DB"),
        "CONTRACTS_DIR": os.environ.get("CONTRACTS_DIR"),
        "PYTHONPATH_HEAD": sys.path[:2],
        "cwd": os.getcwd(),
        "worktree": str(ROOT),
    }

    runner = EventTrendRunner()
    runner.parquet_base = os.environ.get(
        "MARKET_ARCHIVE", os.path.join(str(ROOT), "数据/市场数据/parquet"))

    t0 = time.time()
    df_4h, df_1h, df_15m, oi_ts, oi_vals, funding = runner.load_symbol_data(
        symbol=args.symbol, start_date=args.start, end_date=args.end,
        allow_auto_window=False)
    load_sec = round(time.time() - t0, 2)

    data = {
        "symbol": args.symbol, "window": [args.start, args.end],
        "bar_4h": len(df_4h), "bar_1h": len(df_1h), "bar_15m": len(df_15m),
        "oi_rows": int(len(oi_ts)), "funding_events": len(funding),
        "range_1h": [_iso(int(df_1h["timestamp"].iloc[0])),
                     _iso(int(df_1h["timestamp"].iloc[-1]) + 3600_000)],
        "range_4h": [_iso(int(df_4h["open_time"].iloc[0])),
                     _iso(int(df_4h["close_time"].iloc[-1]))],
        "load_sec": load_sec,
    }

    import unified_runtime as U

    results = {}

    # ── A. 现状：按 4h 收盘推进 ────────────────────────────────────────
    t0 = time.time()
    eng_a = U.RealSignalEngine(symbol=args.symbol, enable_oi_filter=True)
    eng_a.history_floor_ms = 0
    wat_a = U._MemWatcher(df_4h.to_dict("records"), df_1h.to_dict("records"))
    calls_a = 0
    for bar in df_4h.to_dict("records"):
        eng_a.process_market(int(bar["close_time"]), wat_a)
        calls_a += 1
    results["A_现状_按4h推进"] = {
        "calls": calls_a,
        "processed_1h_opens": len(eng_a.processed_1h_opens),
        "processed_4h_opens": len(eng_a.processed_4h_opens),
        "one_h_bars_available": len(df_1h),
        "one_h_coverage_pct": round(len(eng_a.processed_1h_opens) / max(1, len(df_1h)) * 100, 2),
        "candidates": len(eng_a.signals_generated),
        "funnel": _funnel(eng_a.sm),
        "oi_diag": dict(eng_a.oi_diag),
        "causality_violations": _causality(eng_a.signals_generated),
        "sec": round(time.time() - t0, 2),
    }

    # ── B. 逐 1h 步进（同一引擎，只改驱动粒度）─────────────────────────
    t0 = time.time()
    eng_b = U.RealSignalEngine(symbol=args.symbol, enable_oi_filter=True)
    eng_b.history_floor_ms = 0
    wat_b = U._MemWatcher(df_4h.to_dict("records"), df_1h.to_dict("records"))
    calls_b = 0
    for bar in df_1h.to_dict("records"):
        eng_b.process_market(int(bar["close_time"]), wat_b)
        calls_b += 1
    results["B_逐1h步进_同一引擎"] = {
        "calls": calls_b,
        "processed_1h_opens": len(eng_b.processed_1h_opens),
        "processed_4h_opens": len(eng_b.processed_4h_opens),
        "one_h_bars_available": len(df_1h),
        "one_h_coverage_pct": round(len(eng_b.processed_1h_opens) / max(1, len(df_1h)) * 100, 2),
        "candidates": len(eng_b.signals_generated),
        "funnel": _funnel(eng_b.sm),
        "oi_diag": dict(eng_b.oi_diag),
        "causality_violations": _causality(eng_b.signals_generated),
        "sec": round(time.time() - t0, 2),
    }

    # ── C. 回测自有状态机 + parquet OI（非 FT-LOW4H 分支现用路径）──────
    t0 = time.time()
    cfg = make_ft_low4h_config()
    sm_c = runner.run_state_machine_simulation(
        args.symbol, df_4h, df_1h, oi_ts, oi_vals,
        enable_oi_filter=cfg.enable_oi_filter)
    results["C_回测自有状态机_parquet_OI"] = {
        "calls": len(df_1h),
        "processed_1h_opens": len(getattr(sm_c, "processed_1h_opens", []) or []),
        "one_h_bars_available": len(df_1h),
        "candidates": len(sm_c.candidates),
        "funnel": _funnel(sm_c),
        "causality_violations": _causality(sm_c.candidates),
        "sec": round(time.time() - t0, 2),
    }

    report = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "env": env_report,
        "data": data,
        "drivers": results,
        "verdict": {
            "A_vs_B_same": (results["A_现状_按4h推进"]["candidates"]
                            == results["B_逐1h步进_同一引擎"]["candidates"]
                            and results["A_现状_按4h推进"]["processed_1h_opens"]
                            == results["B_逐1h步进_同一引擎"]["processed_1h_opens"]),
            "note": ("A_vs_B_same=True 表示「按 4h 推进」并没有丢掉 1h 步进"
                     "（引擎内部按区间批量取 1h），问题不在步进粒度上；"
                     "False 则说明 4h 驱动确实少喂了 1h。"),
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")

    print("=" * 78)
    print("数据：", json.dumps(data, ensure_ascii=False))
    for name, r in results.items():
        print("-" * 78)
        print(f"{name}")
        print(f"  调用次数={r['calls']}  已处理1h={r['processed_1h_opens']}/{r['one_h_bars_available']}"
              f"  覆盖={r.get('one_h_coverage_pct', '-')}%")
        print(f"  候选={r['candidates']}  漏斗={json.dumps(r['funnel'], ensure_ascii=False)}")
        if "oi_diag" in r:
            od = r["oi_diag"]
            print(f"  OI: 有={od.get('decisions_with_oi')} 无={od.get('decisions_without_oi')}"
                  f" 错误={len(od.get('errors') or [])} 载入行={od.get('rows_loaded')}"
                  f" 未知可得性={od.get('rows_unknown_availability')}")
        print(f"  因果违规={len(r['causality_violations'])}  耗时={r['sec']}s")
    print("=" * 78)
    print("A_vs_B_same =", report["verdict"]["A_vs_B_same"])
    print("报告：", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

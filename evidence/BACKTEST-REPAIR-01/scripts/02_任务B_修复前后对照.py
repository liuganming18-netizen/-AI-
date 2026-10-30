#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BACKTEST-REPAIR-01 · 任务 B：FT-LOW4H 回测接线「修复前 / 修复后」对照。

对照的不是「候选数变没变多」，而是**状态机是否收到正确的历史输入**：

  BEFORE  复刻 `runner.py` 原 FT-LOW4H 分支的驱动：
          `RealSignalEngine` + `_MemWatcher`，OI 由引擎自己从实时热库取。
  AFTER   修好后的 `runner.run_variant_abc`（内部改走回测自有状态机 + parquet OI）。

每一项都记录：
  · 1h 覆盖率          —— 有没有把 2904 根 1h 全喂进去（防「漏步进」）
  · 漏斗逐层计数        —— BOX / SWEEP / RETEST / BREAKOUT / OI_UNAVAILABLE …
  · OI 诊断             —— 载入行 / 有 OI 的决策 / 未知可得性
  · 候选时间链          —— snapshot <= available_at <= execution_earliest
  · PIT 审计            —— qualified / failures（门禁是否还在）

跑法：
    export MARKET_DB=<真实热库>  MARKET_ARCHIVE=<parquet 根>  CONTRACTS_DIR=<快照目录>
    /usr/bin/python3 evidence/BACKTEST-REPAIR-01/scripts/02_任务B_修复前后对照.py \
        --symbols NEARUSDT,BTCUSDT,ETHUSDT,SOLUSDT --start 2026-06-01 --end 2026-09-30
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
for p in (str(ROOT / "框架" / "backend"), str(ROOT / "策略")):
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
    """候选时间链：快照不得晚于可得，可得不得晚于最早执行。"""
    bad, rows = [], []
    for c in cands:
        snap = int(getattr(c, "snapshot_time_ms", 0) or 0)
        avail = int(getattr(c, "available_at_ms", 0) or 0)
        exe = int(getattr(c, "execution_earliest_ms", 0) or 0)
        rows.append({"uid": getattr(c, "signal_uid", None),
                     "snapshot": _iso(snap), "available_at": _iso(avail),
                     "execution_earliest": _iso(exe),
                     "exe_minus_snapshot_ms": exe - snap,
                     "avail_minus_snapshot_ms": avail - snap})
        if exe < snap:
            bad.append({"uid": getattr(c, "signal_uid", None),
                        "reason": "execution_earliest < snapshot_time"})
        if avail > exe:
            bad.append({"uid": getattr(c, "signal_uid", None),
                        "reason": "available_at > execution_earliest"})
    return bad, rows


def _pit_of(sm):
    try:
        audit = sm.pit.audit()
    except Exception as exc:                                  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    keep = ("qualified", "qualification_failures", "pit_mode",
            "candles_checked", "oi_reads", "oi_assumed_reads",
            "oi_refused_reads", "decision_first_ms", "decision_last_ms")
    out = {k: audit.get(k) for k in keep if k in audit}
    for k in ("decision_first_ms", "decision_last_ms"):
        if out.get(k) is not None:
            out[k + "_utc"] = _iso(out[k])
    return out


def arm_before(symbol, df_4h, df_1h, cfg):
    """复刻原 FT-LOW4H 分支：实时引擎 + 实时热库 OI。"""
    import unified_runtime as U
    t0 = time.time()
    eng = U.RealSignalEngine(symbol=symbol, enable_oi_filter=bool(cfg.enable_oi_filter))
    eng.history_floor_ms = 0
    wat = U._MemWatcher(df_4h.to_dict("records"), df_1h.to_dict("records"))
    calls = 0
    for bar in df_4h.to_dict("records"):
        eng.process_market(int(bar["close_time"]), wat)
        calls += 1
    od = dict(eng.oi_diag)
    od["errors"] = len(od.get("errors") or [])
    bad, rows = _causality(eng.signals_generated)
    return {
        "driver": "RealSignalEngine.process_market（按 4h 收盘推进）",
        "oi_source": "实时热库 oi_history（逐行 fetched_at）",
        "calls": calls,
        "processed_1h": len(eng.processed_1h_opens),
        "processed_4h": len(eng.processed_4h_opens),
        "bar_1h_available": len(df_1h),
        "one_h_coverage_pct": round(len(eng.processed_1h_opens) / max(1, len(df_1h)) * 100, 2),
        "candidates": len(eng.signals_generated),
        "funnel": _funnel(eng.sm),
        "oi_diag": od,
        "causality_violations": bad,
        "candidate_chain": rows,
        "sec": round(time.time() - t0, 2),
    }


def arm_after(runner, symbol, df_4h, df_1h, df_15m, oi_ts, oi_vals, funding, cfg, eq):
    """修复后的真实入口：`run_variant_abc`。"""
    from WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine

    t0 = time.time()
    abc = runner.run_variant_abc(symbol, df_4h, df_1h, df_15m, oi_ts, oi_vals,
                                 funding, cfg=cfg, initial_equity=eq)

    # 覆盖率必须**实测**：`EventStateMachine` 没有 `processed_1h_opens` 属性，
    # 直接 getattr 拿空列表会得出「覆盖 0%」的假象。这里临时计数两个真实入口的调用次数。
    counter = {"1h": 0, "box": 0}
    _orig_bar = EventStateMachine.process_1h_bar
    _orig_box = EventStateMachine.check_and_form_box

    def _bar(self, *a, **kw):
        counter["1h"] += 1
        return _orig_bar(self, *a, **kw)

    def _box(self, *a, **kw):
        counter["box"] += 1
        return _orig_box(self, *a, **kw)

    EventStateMachine.process_1h_bar = _bar
    EventStateMachine.check_and_form_box = _box
    try:
        sm_a = runner.run_state_machine_simulation(
            symbol, df_4h, df_1h, oi_ts, oi_vals,
            enable_oi_filter=bool(cfg.enable_oi_filter), cfg=cfg)
    finally:
        EventStateMachine.process_1h_bar = _orig_bar
        EventStateMachine.check_and_form_box = _orig_box

    bad, rows = _causality(sm_a.candidates)
    return {
        "driver": "run_state_machine_simulation（逐 1h 步进，4h 收盘成箱）",
        "oi_source": "parquet oi/binance/um/<symbol>/*.parquet（无发布时刻 ⇒ declared_assumption）",
        "calls": counter["1h"],
        "processed_1h": counter["1h"],
        "processed_4h": counter["box"],
        "bar_1h_available": len(df_1h),
        "one_h_coverage_pct": round(counter["1h"] / max(1, len(df_1h)) * 100, 2),
        "candidates": len(sm_a.candidates),
        "funnel": _funnel(sm_a),
        "causality_violations": bad,
        "candidate_chain": rows,
        "pit": _pit_of(sm_a),
        "abc": {
            "pit_mode": abc.get("pit_mode"),
            "pit_qualified": abc.get("pit_qualified"),
            "pit_failures": abc.get("pit_failures"),
            "funnel_a": abc.get("funnel_a"),
            "funnel_b": abc.get("funnel_b"),
            "trades_a": (abc.get("res_a") or {}).get("summary", {}).get("trade_count"),
            "trades_b": (abc.get("res_b") or {}).get("summary", {}).get("trade_count"),
            "trades_c": (abc.get("res_c") or {}).get("summary", {}).get("trade_count"),
            "formal_pass_allowed": (abc.get("res_a") or {}).get("summary", {})
                                          .get("formal_pass_allowed"),
        },
        "sec": round(time.time() - t0, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NEARUSDT")
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--equity", type=float, default=5000.0)
    ap.add_argument("--out", default=str(EVIDENCE / "out" / "02_任务B_修复前后对照.json"))
    args = ap.parse_args()

    from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
    from WyckoffMaster.event_trend_v0_1.config import make_ft_low4h_config

    runner = EventTrendRunner()
    cfg = make_ft_low4h_config()
    env = {k: os.environ.get(k) for k in
           ("MARKET_DB", "MARKET_ARCHIVE", "CONTRACTS_DIR", "WX_RUNS_BASE")}
    paths = runner.data_paths_report()

    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "env": env, "worktree": str(ROOT), "cwd": os.getcwd(),
           "data_paths": paths, "window": [args.start, args.end],
           "symbols": {}}

    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        t0 = time.time()
        try:
            d4, d1, d15, oits, oiv, fnd = runner.load_symbol_data(
                symbol=sym, start_date=args.start, end_date=args.end,
                allow_auto_window=False)
        except Exception as exc:                              # noqa: BLE001
            out["symbols"][sym] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"[{sym}] 取数失败：{exc}")
            continue
        rec = {"data": {"bar_4h": len(d4), "bar_1h": len(d1), "bar_15m": len(d15),
                        "oi_rows": int(len(oits)), "funding": len(fnd),
                        "load_sec": round(time.time() - t0, 2)}}
        rec["before"] = arm_before(sym, d4, d1, cfg)
        rec["after"] = arm_after(runner, sym, d4, d1, d15, oits, oiv, fnd, cfg, args.equity)
        rec["verdict"] = {
            "coverage_same": (rec["before"]["one_h_coverage_pct"]
                              == rec["after"]["one_h_coverage_pct"]),
            "breakouts_before": rec["before"]["funnel"]["breakouts"],
            "breakouts_after": rec["after"]["funnel"]["breakouts"],
            "candidates_before": rec["before"]["candidates"],
            "candidates_after": rec["after"]["candidates"],
            "oi_unavailable_before": rec["before"]["funnel"]["oi_unavailable"],
            "oi_unavailable_after": rec["after"]["funnel"]["oi_unavailable"],
            "causality_ok_after": not rec["after"]["causality_violations"],
        }
        out["symbols"][sym] = rec

        print("=" * 84)
        print(f"{sym}  4h={len(d4)} 1h={len(d1)} 15m={len(d15)} OI行={len(oits)}")
        for tag in ("before", "after"):
            r = rec[tag]
            print(f"  [{tag:6}] {r['driver']}")
            print(f"           OI={r['oi_source']}")
            print(f"           调用={r['calls']}  已处理1h={r['processed_1h']}/{r['bar_1h_available']}"
                  f" 覆盖={r['one_h_coverage_pct']}%  候选={r['candidates']}")
            print(f"           漏斗={json.dumps(r['funnel'], ensure_ascii=False)}")
            if tag == "before":
                od = r["oi_diag"]
                print(f"           OI诊断: 载入行={od.get('rows_loaded')}"
                      f" 未知可得性={od.get('rows_unknown_availability')}"
                      f" 有OI决策={od.get('decisions_with_oi')}"
                      f" 无OI决策={od.get('decisions_without_oi')}"
                      f" 错误={od.get('errors')}")
        print(f"           因果违规={len(rec['after']['causality_violations'])}"
              f"  PIT={json.dumps(rec['after']['pit'], ensure_ascii=False)}")
        print(f"           三变体成交数 A/B/C = {rec['after']['abc']['trades_a']}"
              f"/{rec['after']['abc']['trades_b']}/{rec['after']['abc']['trades_c']}"
              f"  formal_pass_allowed={rec['after']['abc']['formal_pass_allowed']}")

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("=" * 84)
    print("报告：", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())

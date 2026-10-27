# -*- coding: utf-8 -*-
"""BREAKEVEN-02 · 步骤二：固定仓位配对运行 A / B(1R) / C(2R) 三组。

改进点（相比 BREAKEVEN-01）
────────────────────────
1. 【固定仓位配对】：B/C 组在开仓时直接锁定基准 A 组该信号的实际成交仓位 qty，
   彻底排除串行权益变化对后续交易仓位计算的扰动。
2. 【动态仓位单列记录】：对 OPUSDT（group 86）同时记录未固定仓位（串行动态权益）
   下的 B 组实际成交记录，将其 0.112375 U 仓位差异单列。
3. 【基准 A 复现校验】：逐笔比对 FULL_SWEEP 原始记录，捕获 TRXUSDT 对账数据。
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
for _p in (ROOT, f"{ROOT}/策略", f"{ROOT}/框架/backend", f"{ROOT}/evidence/BREAKEVEN-02/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = os.path.join(ROOT, "evidence/BREAKEVEN-02/out")
PLAN = os.path.join(OUT_DIR, "sample_plan.json")
INITIAL_EQUITY = 2000.0
WORKERS = int(os.environ.get("WORKERS", "6"))
LIMIT = int(os.environ.get("LIMIT", "0"))

_G = {}


def _init_worker():
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
    from WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
    import be_core
    _G["cfg"] = EventTrendConfig(initial_equity=INITIAL_EQUITY)
    _G["runner"] = EventTrendRunner(base_dir=ROOT)
    _G["run_bt"] = run_event_trend_backtest
    _G["be_core"] = be_core


def _ordinal_to_ts(ts15, entry_time: int, ordinal: int) -> int:
    import numpy as np
    i0 = int(np.searchsorted(ts15, entry_time, side="left"))
    j = i0 + int(ordinal) - 1
    if j < 0 or j >= len(ts15):
        return -1
    return int(ts15[j])


def _trade_row(*, variant, task, tr, be_state, ts15):
    entry_time = int(tr.get("entry_time"))
    exit_time = int(tr.get("exit_time"))
    entry_price = float(tr.get("entry_price"))
    trace = tr.get("stop_trace") or []
    init_stop = tr.get("initial_stop") or tr.get("initial_stop_price")
    if init_stop is None:
        init_stop = next((t.get("stop") for t in trace if t.get("reason") == "init"), None)
    if init_stop is None:
        raise ValueError(f"initial_stop_missing:{tr.get('signal_id')}")
    init_stop = float(init_stop)
    R_abs = (entry_price - init_stop) if int(tr.get("direction", 1)) == 1 else (init_stop - entry_price)
    row = {
        "variant": variant, "group": task["group"], "symbol": task["symbol"],
        "window_start": task["start"], "window_end": task["end"],
        "signal_id": tr.get("signal_id"), "trade_id": tr.get("trade_id"),
        "direction": int(tr.get("direction", 1)),
        "entry_time": entry_time, "exit_time": exit_time,
        "entry_price": entry_price, "exit_price": float(tr.get("exit_price")),
        "qty": float(tr.get("qty")), "initial_stop": init_stop,
        "R_abs": R_abs,
        "R_pct": (R_abs / entry_price if entry_price else None),
        "entry_fee": float(tr.get("entry_fee", 0.0)),
        "exit_fee": float(tr.get("exit_fee", 0.0)),
        "fees": float(tr.get("fees", 0.0)),
        "funding_fee": float(tr.get("funding_fee", 0.0)),
        "slippage_loss": float(tr.get("slippage_loss", 0.0)),
        "gross_pnl": float(tr.get("gross_pnl", 0.0)),
        "net_pnl": float(tr.get("net_pnl", 0.0)),
        "net_R": (float(tr.get("net_pnl", 0.0)) / (R_abs * float(tr.get("qty", 0.0)))
                  if R_abs > 0 and float(tr.get("qty", 0.0)) > 0 else None),
        "exit_reason": tr.get("exit_reason"),
        "mfe": float(tr.get("mfe", 0.0)), "mae": float(tr.get("mae", 0.0)),
        "hold_hours": ((exit_time - entry_time) / 3600000.0),
        "stop_trace": tr.get("stop_trace") or [],
        "be_triggered": False, "be_trigger_time": None, "be_trigger_px": None,
        "be_price": None, "be_update_time": None, "be_applied_time": None,
        "be_net_at_trigger": None, "be_stop_before": None, "be_stop_after": None,
        "same_bar_ambiguous": False, "be_ordinal": None,
    }
    if be_state:
        st = be_state
        row["be_triggered"] = bool(st.get("triggered"))
        row["same_bar_ambiguous"] = bool(st.get("same_bar_ambiguous"))
        row["be_ordinal"] = st.get("trigger_ordinal")
        if st.get("triggered"):
            row["be_trigger_time"] = _ordinal_to_ts(ts15, entry_time, st["trigger_ordinal"])
            row["be_trigger_px"] = st.get("trigger_px")
            row["be_price"] = st.get("be_px")
            row["be_net_at_trigger"] = st.get("be_net_at_trigger")
            row["be_stop_before"] = st.get("stop_before")
            row["be_stop_after"] = st.get("stop_after")
            if st.get("update_ordinal"):
                row["be_update_time"] = _ordinal_to_ts(ts15, entry_time, st["update_ordinal"])
            if st.get("applied_ordinal"):
                t_app = _ordinal_to_ts(ts15, entry_time, st["applied_ordinal"])
                row["be_applied_time"] = (t_app if t_app > 0 else None)
    return row


def run_task(task):
    rec = {"group": task["group"], "symbol": task["symbol"],
           "start": task["start"], "end": task["end"],
           "status": "error", "err": "", "elapsed_s": 0.0,
           "trades": {"A": [], "B": [], "C": []},
           "dynamic_trades_B": [],  # 未固定仓位时的 B 组记录（供 OPUSDT 对账）
           "summary": {}, "spec_status": "", "n_candidates": 0,
           "repro": {"checked": 0, "matched": 0, "mismatch": []}}
    t0 = time.time()
    cfg = _G["cfg"]
    runner = _G["runner"]
    be_core = _G["be_core"]
    run_bt = _G["run_bt"]
    try:
        df_4h, df_1h, df_15m, oi_ts, oi_vals, fund = runner.load_symbol_data(
            task["symbol"], start_date=task["start"], end_date=task["end"],
            allow_auto_window=False)
        if len(df_15m) < 2000 or len(df_1h) < 500:
            rec["status"] = "no_data"
            rec["elapsed_s"] = round(time.time() - t0, 2)
            return rec
        spec = runner._resolve_spec(task["symbol"], df_15m, cfg)
        sm = runner.run_state_machine_simulation(
            task["symbol"], df_4h, df_1h, oi_ts, oi_vals, enable_oi_filter=True)
        rec["n_candidates"] = int(len(sm.candidates))
        ts15 = df_15m["timestamp"].to_numpy("int64")

        # ── 1. 运行基准 A ──
        be_core.install(None)
        res_A = run_bt(symbol=task["symbol"], df_1h=df_1h, df_15m=df_15m,
                       candidates=sm.candidates, funding_events=fund, df_4h=df_4h,
                       variant="A", initial_equity=INITIAL_EQUITY, config=cfg,
                       instrument_spec=spec)
        trades_A = (res_A.get("engine_result") or {}).get("trades") or []
        for tr in trades_A:
            rec["trades"]["A"].append(
                _trade_row(variant="A", task=task, tr=tr, be_state=None, ts15=ts15))
        smry = res_A.get("summary") or {}
        rec["summary"] = {k: smry.get(k) for k in
                          ("trade_count", "net_pnl", "fee_total",
                           "funding_total", "slippage_total",
                           "final_equity", "spec_status", "formal_pass_allowed")}
        rec["spec_status"] = str(smry.get("spec_status", ""))

        # 提取 A 组锁定的开仓仓位字典
        fixed_qtys = {t["signal_id"]: t["qty"] for t in trades_A if t.get("signal_id")}

        # ── 2. 运行 B 组（1R 保本，固定仓位）与 C 组（2R 保本，固定仓位）──
        for variant, r in (("B", 1.0), ("C", 2.0)):
            be_core.install(r, fixed_quantities=fixed_qtys)
            res = run_bt(symbol=task["symbol"], df_1h=df_1h, df_15m=df_15m,
                         candidates=sm.candidates, funding_events=fund, df_4h=df_4h,
                         variant="A", initial_equity=INITIAL_EQUITY, config=cfg,
                         instrument_spec=spec)
            trades = (res.get("engine_result") or {}).get("trades") or []
            states = be_core.last_policy_state()
            for tr in trades:
                st = states.get(str(tr.get("signal_id")))
                rec["trades"][variant].append(
                    _trade_row(variant=variant, task=task, tr=tr, be_state=st, ts15=ts15))

        # ── 3. 针对 OPUSDT（或多仓位标的），额外运行未固定仓位的动态 B 组作为对账证据 ──
        if task["symbol"] == "OPUSDT" and len(trades_A) > 1:
            be_core.install(1.0, fixed_quantities=None)  # 不传 fixed_quantities，复现串行动态权益
            res_dyn = run_bt(symbol=task["symbol"], df_1h=df_1h, df_15m=df_15m,
                             candidates=sm.candidates, funding_events=fund, df_4h=df_4h,
                             variant="A", initial_equity=INITIAL_EQUITY, config=cfg,
                             instrument_spec=spec)
            dyn_trades = (res_dyn.get("engine_result") or {}).get("trades") or []
            dyn_states = be_core.last_policy_state()
            for tr in dyn_trades:
                st = dyn_states.get(str(tr.get("signal_id")))
                rec["dynamic_trades_B"].append(
                    _trade_row(variant="B_dyn", task=task, tr=tr, be_state=st, ts15=ts15))

        be_core.install(None)

        # ── 4. 基准 A 复现校验：与 FULL_SWEEP 原始记录逐笔比净额 ──
        orig = {(int(t["t_in"])): t for t in task.get("orig_trade_list") or []}
        for row in rec["trades"]["A"]:
            o = orig.get(int(row["entry_time"]))
            if o is None:
                continue
            rec["repro"]["checked"] += 1
            d = float(row["net_pnl"]) - float(o["net"])
            if abs(d) <= 1e-6:
                rec["repro"]["matched"] += 1
            else:
                rec["repro"]["mismatch"].append(
                    {"entry_time": int(row["entry_time"]), "now": float(row["net_pnl"]),
                     "orig": float(o["net"]), "delta": d,
                     "now_qty": float(row["qty"]), "orig_qty": float(o.get("qty", 0.0)),
                     "now_fees": float(row["fees"]), "orig_why": o.get("why"),
                     "now_why": row.get("exit_reason")})
        rec["status"] = "ok"
    except Exception as exc:                                   # noqa: BLE001
        rec["status"] = "error"
        rec["err"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc()[-2000:]
        try:
            be_core.install(None)
        except Exception:
            pass
    rec["elapsed_s"] = round(time.time() - t0, 2)
    return rec


def main() -> int:
    plan = json.load(open(PLAN, encoding="utf-8"))["plan"]
    if LIMIT:
        plan = plan[:LIMIT]
    print(f"[plan] runs={len(plan)} workers={WORKERS}", flush=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    out = []
    with ProcessPoolExecutor(max_workers=WORKERS, initializer=_init_worker) as ex:
        for i, rec in enumerate(ex.map(run_task, plan, chunksize=1), 1):
            out.append(rec)
            if i % 10 == 0 or i == len(plan):
                done = sum(len(r["trades"]["A"]) for r in out)
                print(f"[{i}/{len(plan)}] {time.time()-t0:.0f}s tradesA={done} "
                      f"err={sum(1 for r in out if r['status']=='error')}", flush=True)
    path = os.path.join(OUT_DIR, "raw_runs.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    ok = [r for r in out if r["status"] == "ok"]
    err = [r for r in out if r["status"] == "error"]
    chk = sum(r["repro"]["checked"] for r in out)
    mat = sum(r["repro"]["matched"] for r in out)
    print(json.dumps({"runs": len(out), "ok": len(ok), "error": len(err),
                      "trades_A": sum(len(r["trades"]["A"]) for r in out),
                      "trades_B": sum(len(r["trades"]["B"]) for r in out),
                      "trades_C": sum(len(r["trades"]["C"]) for r in out),
                      "repro_checked": chk, "repro_matched": mat,
                      "wall_s": round(time.time() - t0, 1)}, ensure_ascii=False))
    for r in err[:5]:
        print("ERR", r["symbol"], r["start"], r["err"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

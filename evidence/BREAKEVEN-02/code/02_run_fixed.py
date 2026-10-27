# -*- coding: utf-8 -*-
"""BREAKEVEN-02 · 步骤二：固定仓位的逐笔配对（A / B=1R / C=2R）。

与 BREAKEVEN-01 的唯一区别（也是本单要补的缺口）
──────────────────────────────────────────────
01 的 A/B/C 是三个**各自串行**的 run：前一笔被保本提前平仓 ⇒ 账户权益变化
⇒ 下一笔仓位按 0.5% 重新计算 ⇒ 后一笔的净额被动变化。
于是「未触发保本」的交易也会出现差异（OPUSDT 那 0.112375 U 就是这么来的）。

本脚本把数量钉死：
  · 每个信号**单独隔离**成一次回放（候选列表里只放它自己）；
  · 入场数量 = 基线 A 在原始 run 里实际成交的数量（逐笔冻结）；
  · 三组共用同一个冻结数量 ⇒ 入场时间/价格/数量/初始止损完全相同。
净额只依赖数量与价格路径、不依赖账户权益 ⇒ 串行权益耦合被切断。

用法：
    python 02_run_fixed.py                # 全量 206 个信号
    LIMIT=5 python 02_run_fixed.py        # 小样
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
BE1 = os.path.join(ROOT, "evidence/BREAKEVEN-01")
BE2 = os.path.join(ROOT, "evidence/BREAKEVEN-02")
for _p in (ROOT, f"{ROOT}/策略", f"{ROOT}/框架/backend", f"{BE2}/code", f"{BE1}/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = os.path.join(BE2, "out")
PAIRED_01 = os.path.join(BE1, "out/paired_trades.csv")
PLAN_01 = os.path.join(BE1, "out/sample_plan.json")
#: 隔离回放用的权益：足够大 ⇒ 30%/90% 名义上限与现金约束都不会改动冻结数量。
#: 它只影响「风控是否放行」，不影响任何一笔的净额（数量已冻结）。
ISOLATION_EQUITY = 1_000_000.0
WORKERS = int(os.environ.get("WORKERS", "6"))
LIMIT = int(os.environ.get("LIMIT", "0"))
EPS = 1e-9

_G = {}


def _init_worker():
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
    from WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
    import be2_core
    _G["cfg"] = EventTrendConfig(initial_equity=ISOLATION_EQUITY)
    _G["runner"] = EventTrendRunner(base_dir=ROOT)
    _G["run_bt"] = run_event_trend_backtest
    _G["core"] = be2_core


def _ordinal_to_ts(ts15, entry_time: int, ordinal: int) -> int:
    import numpy as np
    i0 = int(np.searchsorted(ts15, entry_time, side="left"))
    j = i0 + int(ordinal) - 1
    if j < 0 or j >= len(ts15):
        return -1
    return int(ts15[j])


def _row(*, variant, sig, tr, st, ts15):
    entry_time = int(tr.get("entry_time"))
    exit_time = int(tr.get("exit_time"))
    entry_price = float(tr.get("entry_price"))
    trace = tr.get("stop_trace") or []
    init_stop = next((t.get("stop") for t in trace if t.get("reason") == "init"), None)
    if init_stop is None:
        raise ValueError(f"initial_stop_missing:{tr.get('signal_id')}")
    init_stop = float(init_stop)
    r_abs = entry_price - init_stop
    row = {
        "variant": variant, "signal_id": sig["signal_id"], "symbol": sig["symbol"],
        "group": sig["group"], "window": sig["window"],
        "frozen_qty": float(sig["frozen_qty"]),
        "baseline_qty": float(sig["baseline_qty"]),
        "baseline_entry_time": int(sig["baseline_entry_time"]),
        "baseline_entry_price": float(sig["baseline_entry_price"]),
        "baseline_initial_stop": float(sig["baseline_initial_stop"]),
        "baseline_net": float(sig["baseline_net"]),
        "direction": int(tr.get("direction", 1)),
        "entry_time": entry_time, "exit_time": exit_time,
        "entry_price": entry_price, "exit_price": float(tr.get("exit_price")),
        "qty": float(tr.get("qty")), "initial_stop": init_stop, "R_abs": r_abs,
        "R_pct": (r_abs / entry_price if entry_price else None),
        "entry_fee": float(tr.get("entry_fee", 0.0)),
        "exit_fee": float(tr.get("exit_fee", 0.0)),
        "fees": float(tr.get("fees", 0.0)),
        "funding_fee": float(tr.get("funding_fee", 0.0)),
        "slippage_loss": float(tr.get("slippage_loss", 0.0)),
        "slippage_loss_pure": float(tr.get("slippage_loss_pure", 0.0)),
        "rounding_loss": float(tr.get("rounding_loss", 0.0)),
        "gross_pnl": float(tr.get("gross_pnl", 0.0)),
        "net_pnl": float(tr.get("net_pnl", 0.0)),
        "exit_reason": tr.get("exit_reason"),
        "mfe": float(tr.get("mfe", 0.0)), "mae": float(tr.get("mae", 0.0)),
        "hold_hours": (exit_time - entry_time) / 3600000.0,
        "stop_trace": trace,
        "be_triggered": False, "be_trigger_time": None, "be_trigger_px": None,
        "be_price": None, "be_update_time": None, "be_applied_time": None,
        "be_net_at_trigger": None, "be_stop_before": None, "be_stop_after": None,
        "be_stop_moved": False, "same_bar_ambiguous": False,
        "entry_identical": (abs(entry_price - float(sig["baseline_entry_price"])) <= EPS
                            and entry_time == int(sig["baseline_entry_time"])),
        "qty_identical": abs(float(tr.get("qty")) - float(sig["frozen_qty"])) <= EPS,
    }
    if st:
        row["be_triggered"] = bool(st.get("triggered"))
        row["same_bar_ambiguous"] = bool(st.get("same_bar_ambiguous"))
        if st.get("triggered"):
            row["be_trigger_time"] = _ordinal_to_ts(ts15, entry_time, st["trigger_ordinal"])
            row["be_trigger_px"] = st.get("trigger_px")
            row["be_price"] = st.get("be_px")
            row["be_net_at_trigger"] = st.get("be_net_at_trigger")
            row["be_stop_before"] = st.get("stop_before")
            row["be_stop_after"] = st.get("stop_after")
            row["be_stop_moved"] = bool(
                st.get("stop_after") is not None
                and st.get("stop_before") is not None
                and abs(float(st["stop_after"]) - float(st["stop_before"])) > 0)
            if st.get("update_ordinal"):
                row["be_update_time"] = _ordinal_to_ts(ts15, entry_time, st["update_ordinal"])
            if st.get("applied_ordinal"):
                t = _ordinal_to_ts(ts15, entry_time, st["applied_ordinal"])
                row["be_applied_time"] = (t if t > 0 else None)
    return row


def run_window(task):
    """一个窗口内：加载一次数据、跑一次状态机，然后对窗口内每个信号做 3 次隔离回放。"""
    rec = {"group": task["group"], "symbol": task["symbol"], "window": task["window"],
           "status": "error", "err": "", "rows": [], "elapsed_s": 0.0,
           "n_candidates": 0, "missing_signals": []}
    t0 = time.time()
    cfg, runner, run_bt, core = _G["cfg"], _G["runner"], _G["run_bt"], _G["core"]
    try:
        start, end = task["window"].split("~")
        df_4h, df_1h, df_15m, oi_ts, oi_vals, fund = runner.load_symbol_data(
            task["symbol"], start_date=start, end_date=end, allow_auto_window=False)
        if len(df_15m) < 2000 or len(df_1h) < 500:
            rec["status"] = "no_data"
            return rec
        spec = runner._resolve_spec(task["symbol"], df_15m, cfg)
        sm = runner.run_state_machine_simulation(
            task["symbol"], df_4h, df_1h, oi_ts, oi_vals, enable_oi_filter=True)
        rec["n_candidates"] = int(len(sm.candidates))
        by_id = {c.candidate_id: c for c in sm.candidates}
        ts15 = df_15m["timestamp"].to_numpy("int64")

        for sig in task["signals"]:
            cand = by_id.get(sig["signal_id"])
            if cand is None:
                rec["missing_signals"].append(sig["signal_id"])
                continue
            for variant, r in (("A", None), ("B", 1.0), ("C", 2.0)):
                core.install(r, sig["frozen_qty"])
                res = run_bt(symbol=task["symbol"], df_1h=df_1h, df_15m=df_15m,
                             candidates=[cand], funding_events=fund, df_4h=df_4h,
                             variant="A", initial_equity=ISOLATION_EQUITY, config=cfg,
                             instrument_spec=spec)
                trades = (res.get("engine_result") or {}).get("trades") or []
                if len(trades) != 1:
                    raise RuntimeError(
                        f"isolation_violation:{sig['signal_id']}:{variant}:"
                        f"trades={len(trades)}")
                st = (core.last_state() or {}).get(str(trades[0].get("signal_id"))) \
                    if r is not None else None
                rec["rows"].append(_row(variant=variant, sig=sig, tr=trades[0],
                                        st=st, ts15=ts15))
        core.install(None, None)
        rec["status"] = "ok"
    except Exception as exc:                                     # noqa: BLE001
        rec["status"] = "error"
        rec["err"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc()[-2000:]
        try:
            core.install(None, None)
        except Exception:
            pass
    rec["elapsed_s"] = round(time.time() - t0, 2)
    return rec


def build_tasks():
    plan = {str(p["group"]) + "|" + p["symbol"]: p
            for p in json.load(open(PLAN_01, encoding="utf-8"))["plan"]}
    rows = list(csv.DictReader(open(PAIRED_01, encoding="utf-8-sig")))
    tasks = {}
    for r in rows:
        key = str(r["group"]) + "|" + r["symbol"]
        p = plan.get(key)
        if p is None:
            raise KeyError(f"plan_missing:{key}")
        t = tasks.setdefault(key, {"group": str(r["group"]), "symbol": r["symbol"],
                                   "window": f"{p['start']}~{p['end']}", "signals": []})
        t["signals"].append({
            "signal_id": r["signal_id"], "symbol": r["symbol"],
            "group": str(r["group"]), "window": t["window"],
            "frozen_qty": float(r["qty"]),
            "baseline_qty": float(r["qty"]),
            "baseline_entry_time": int(r["entry_time_ms"]),
            "baseline_entry_price": float(r["entry_price"]),
            "baseline_initial_stop": float(r["initial_stop"]),
            "baseline_net": float(r["A_net"]),
        })
    out = list(tasks.values())
    out.sort(key=lambda x: (x["symbol"], x["window"]))
    return out


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    tasks = build_tasks()
    n_sig = sum(len(t["signals"]) for t in tasks)
    if LIMIT:
        tasks = tasks[:LIMIT]
        n_sig = sum(len(t["signals"]) for t in tasks)
    print(f"[plan] windows={len(tasks)} signals={n_sig} workers={WORKERS} "
          f"isolation_equity={ISOLATION_EQUITY}", flush=True)
    t0 = time.time()
    out = []
    with ProcessPoolExecutor(max_workers=WORKERS, initializer=_init_worker) as ex:
        for i, rec in enumerate(ex.map(run_window, tasks, chunksize=1), 1):
            out.append(rec)
            if i % 20 == 0 or i == len(tasks):
                print(f"[{i}/{len(tasks)}] {time.time()-t0:.0f}s "
                      f"rows={sum(len(r['rows']) for r in out)} "
                      f"err={sum(1 for r in out if r['status']=='error')}", flush=True)
    path = os.path.join(OUT_DIR, "raw_fixed.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    err = [r for r in out if r["status"] == "error"]
    print(json.dumps({"windows": len(out), "error": len(err),
                      "rows": sum(len(r["rows"]) for r in out),
                      "missing_signals": sum(len(r["missing_signals"]) for r in out),
                      "wall_s": round(time.time() - t0, 1)}, ensure_ascii=False))
    for r in err[:5]:
        print("ERR", r["symbol"], r["window"], r["err"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

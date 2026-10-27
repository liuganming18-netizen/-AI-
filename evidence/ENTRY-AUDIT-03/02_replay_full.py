# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-03 / 02 · 全量候选的共享内核重放（身份级）

范围（= 冻结研究范围，不是抽样）
--------------------------------
A 连续回放：133 个标的 × 2021-12-01~2026-10-18  → 覆盖 185 条 cand_ev
B 窗口回放：43 个 (group, symbol) × 该组 365 天窗口 → 覆盖 42 条 old_cand

不做的事
--------
· 不跑 200 组 × 100 标的全池重叠扫描（工单明确禁止）
· 不写任何 run 目录到 回测日志/（只写本证据目录）
· 不改策略、不改撮合、不改账本；只调用现有入口

入口与上游逐字相同
------------------
runner.load_symbol_data → run_state_machine_simulation → run_event_trend_backtest
（连续回放传 price_step 兜底；窗口回放与 run_full_sweep_200x100.py 一致不传）

产出
----
02_replay_runs.csv        每个 run 一行（规格资格 / 执行有效性 / 身份链 / 成本合计）
02_replay_candidates.csv  每个候选一行（ValidationResult 全字段 + 候选快照关键值）
02_replay_trades.jsonl    每笔成交的引擎原值（含 fees / funding_fee / price_audit / 订单号）
02_replay_rejects.jsonl   每条 SIGNAL_REJECTED 原始事件
02_progress.jsonl         断点续跑
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-03"
OUT.mkdir(parents=True, exist_ok=True)
PROG = OUT / "02_progress.jsonl"

CONT_START, CONT_END = "2021-12-01", "2026-10-18"
INITIAL_EQUITY = 2000.0
WORKERS = int(os.environ.get("WORKERS", "6"))


# ══════════════════════════ worker ══════════════════════════

_W = {}


def _init():
    if _W:
        return
    for p in (str(ROOT), str(ROOT / "策略")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    from WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
    from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner

    _W["runner"] = EventTrendRunner(base_dir=str(ROOT))
    _W["cfg"] = EventTrendConfig(initial_equity=INITIAL_EQUITY)
    _W["bt"] = run_event_trend_backtest


def run_task(task: dict) -> dict:
    """一次回测 = 一个标的 × 一个窗口。返回原始结果，不做任何口径加工。"""
    _init()
    runner, cfg, bt = _W["runner"], _W["cfg"], _W["bt"]
    t0 = time.time()
    out = {"task": task, "status": "error", "error": "",
           "run": {}, "candidates": [], "trades": [], "rejects": []}
    try:
        df_4h, df_1h, df_15m, oi_ts, oi_vals, funding = runner.load_symbol_data(
            task["symbol"], start_date=task["start"], end_date=task["end"],
            allow_auto_window=False)
        out["run"] = {
            "bars_4h": int(len(df_4h)),
            "bars_1h": int(len(df_1h)),
            "bars_15m": int(len(df_15m)),
            "first_1h_ms": int(df_1h["open_time"].iloc[0]) if len(df_1h) else None,
            "last_15m_ms": int(df_15m["open_time"].iloc[-1]) if len(df_15m) else None,
            "oi_points": int(len(oi_ts)),
            "funding_events": int(len(funding)),
            "warmup_4h_bars_before_window": None,   # 由下面按需填
        }
        # 4h 预热根数 = 目标区间之前有多少根（load_symbol_data 上限 220）
        import numpy as np
        start_ms = int(__import__("pandas").to_datetime(task["start"]).timestamp() * 1000)
        n_before = int((df_4h["close_time"].to_numpy("int64") < start_ms).sum())
        out["run"]["warmup_4h_bars_before_window"] = n_before

        if len(df_15m) < 2000 or len(df_1h) < 500:
            out["status"] = "no_data"
            out["run"]["elapsed_s"] = round(time.time() - t0, 2)
            return out

        spec = runner._resolve_spec(task["symbol"], df_15m, cfg)
        sm = runner.run_state_machine_simulation(
            task["symbol"], df_4h, df_1h, oi_ts, oi_vals, enable_oi_filter=True)

        kw = dict(symbol=task["symbol"], df_1h=df_1h, df_15m=df_15m,
                  candidates=sm.candidates, funding_events=funding, df_4h=df_4h,
                  variant="A", initial_equity=INITIAL_EQUITY, config=cfg,
                  instrument_spec=spec)
        if task["generation"] == "A":
            # 与 stage2_candidate_feature_pipeline.py 逐字一致
            kw["price_step"] = (None if spec is not None else "0.000000000001")
        res = bt(**kw)

        smry = res.get("summary") or {}
        prec = res.get("precision") or {}
        ident = res.get("identity") or {}
        er = res.get("engine_result") or {}

        out["status"] = "ok"
        out["run"].update({
            "candidates": int(len(sm.candidates)),
            "trades": int(len(er.get("trades") or [])),
            "spec_status": res.get("spec_status"),
            "formal_pass_allowed": bool(smry.get("formal_pass_allowed", False)),
            "run_valid": bool(prec.get("run_valid", True)),
            "invalid_reasons": list(prec.get("invalid_reasons") or []),
            "rejects_count_precision": int(prec.get("rejects_count", 0) or 0),
            "price_steps": dict(prec.get("price_steps") or {}),
            "spec_applicability": (res.get("instrument_spec") or {}).get("applicability"),
            "spec_identity": (res.get("instrument_spec") or {}).get("identity"),
            "chain_ok": bool(ident.get("chain_ok", False)),
            "identity_breaks": ident.get("identity_breaks") or [],
            "orphan_trades": ident.get("orphan_trades") or [],
            "trades_total": ident.get("trades_total"),
            "trades_matched_by_identity": ident.get("trades_matched_by_identity"),
            "candidates_total": ident.get("candidates_total"),
            "candidates_executed": ident.get("candidates_executed"),
            "net_pnl": smry.get("net_pnl"),
            "fee_total": smry.get("fee_total"),
            "funding_total": smry.get("funding_total"),
            "slippage_total": smry.get("slippage_total"),
            "slippage_pure_total": smry.get("slippage_pure_total"),
            "rounding_total": smry.get("rounding_total"),
            "slippage_split_ok": smry.get("slippage_split_ok"),
            "reconcile": er.get("reconcile") or {},
            "first_candidate_ms": (min(int(c.snapshot_time_ms) for c in sm.candidates)
                                   if sm.candidates else None),
            "candidate_ms_list": sorted(int(c.snapshot_time_ms) for c in sm.candidates),
            "elapsed_s": round(time.time() - t0, 2),
        })

        cand_by_id = {c.candidate_id: c for c in sm.candidates}
        for v in res.get("validation_results") or []:
            c = cand_by_id.get(v.candidate_id)
            d = v.to_dict() if hasattr(v, "to_dict") else dict(v.__dict__)
            d.update({
                "generation": task["generation"],
                "task_id": task["task_id"],
                "group": task.get("group"),
                "window_start": task["start"],
                "window_end": task["end"],
                "cand_box_high": (float(c.box_high_h) if c else None),
                "cand_box_low": (float(c.box_low_l) if c else None),
                "cand_box_atr": (float(c.box_atr_a) if c else None),
                "cand_box_er": (float(c.box_er) if c else None),
                "cand_breakout_close": (float(c.breakout_close) if c else None),
                "cand_max_entry": (float(c.max_entry_price) if c else None),
                "cand_min_entry": (float(c.min_entry_price) if c else None),
                "cand_initial_stop": (float(c.initial_stop_price) if c else None),
                "cand_initial_risk_r": (float(c.initial_risk_r) if c else None),
                "cand_exec_earliest_ms": (int(c.execution_earliest_ms) if c else None),
                "cand_snapshot_hash": (c.snapshot_hash if c else None),
                "cand_pit_proven": (bool(c.pit_proven) if c else None),
                "cand_volume_ratio": (float(c.volume_ratio) if c else None),
                "cand_oi_growth_6h": (float(c.oi_growth_6h) if c else None),
                "cand_sweep_low_s": (float(c.sweep_low_s) if c else None),
                "cand_retest_low_lr": (float(c.retest_low_lr) if c else None),
            })
            out["candidates"].append(d)

        for t in er.get("trades") or []:
            tr = dict(t)
            tr["generation"] = task["generation"]
            tr["task_id"] = task["task_id"]
            tr["group"] = task.get("group")
            tr["window_start"] = task["start"]
            tr["window_end"] = task["end"]
            # stop_trace 可能很大，只留条数与首末
            st = tr.pop("stop_trace", None) or []
            tr["stop_trace_n"] = len(st)
            out["trades"].append(tr)

        for e in res.get("ledger_events") or []:
            if e.get("event_type") == "SIGNAL_REJECTED":
                out["rejects"].append({"generation": task["generation"],
                                       "task_id": task["task_id"],
                                       "group": task.get("group"),
                                       "symbol": task["symbol"], **e})
        for r in er.get("rejected_orders") or []:
            out["rejects"].append({"generation": task["generation"],
                                   "task_id": task["task_id"],
                                   "group": task.get("group"),
                                   "symbol": task["symbol"],
                                   "event_type": "rejected_orders_item", **r})
        for r in (prec.get("rejects") or []):
            out["rejects"].append({"generation": task["generation"],
                                   "task_id": task["task_id"],
                                   "group": task.get("group"),
                                   "symbol": task["symbol"],
                                   "event_type": "precision_reject", **r})
    except Exception as exc:
        out["status"] = "error"
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    out["run"]["elapsed_s"] = round(time.time() - t0, 2)
    return out


# ══════════════════════════ 任务计划 ══════════════════════════

def build_tasks() -> list[dict]:
    wp = json.load(open(OUT / "00_window_plan.json", encoding="utf-8"))
    tasks = []
    for sym in wp["generation_A_continuous"]["symbol_list"]:
        tasks.append({"task_id": f"A|{sym}", "generation": "A", "symbol": sym,
                      "start": CONT_START, "end": CONT_END, "group": None})
    seen = set()
    for rec in wp["generation_B_windows"]["per_record"]:
        for w in rec["windows"]:
            k = (w["group"], rec["symbol"])
            if k in seen:
                continue
            seen.add(k)
            tasks.append({"task_id": f"B|{w['group']}|{rec['symbol']}",
                          "generation": "B", "symbol": rec["symbol"],
                          "start": w["start"], "end": w["end"], "group": w["group"]})
    return tasks


def main() -> None:
    tasks = build_tasks()
    done = {}
    if PROG.exists():
        for line in PROG.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                done[d["task"]["task_id"]] = d
    print(f"任务总数 {len(tasks)}，已完成 {len(done)}", flush=True)

    todo = [t for t in tasks if t["task_id"] not in done]
    results = list(done.values())
    if todo:
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=WORKERS) as ex, open(PROG, "a", encoding="utf-8") as pf:
            futs = {ex.submit(run_task, t): t for t in todo}
            for i, fut in enumerate(as_completed(futs), 1):
                r = fut.result()
                results.append(r)
                pf.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
                pf.flush()
                el = time.time() - t0
                print(f"[{i}/{len(todo)}] {r['task']['task_id']} {r['status']} "
                      f"候选 {len(r['candidates'])} 成交 {len(r['trades'])} "
                      f"({el:.0f}s 累计)", flush=True)
        print(f"重放耗时 {time.time()-t0:.0f}s", flush=True)

    # ── 汇总落盘 ──
    import pandas as pd
    runs, cands, trades, rejects = [], [], [], []
    for r in results:
        t = r["task"]
        runs.append({
            "task_id": t["task_id"], "generation": t["generation"],
            "symbol": t["symbol"], "group": t.get("group"),
            "window_start": t["start"], "window_end": t["end"],
            "status": r["status"], "error": r.get("error", ""),
            **{k: v for k, v in (r.get("run") or {}).items()
               if k not in ("spec_identity", "candidate_ms_list")},
        })
        cands.extend(r.get("candidates") or [])
        trades.extend(r.get("trades") or [])
        rejects.extend(r.get("rejects") or [])

    pd.DataFrame(runs).to_csv(OUT / "02_replay_runs.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(cands).to_csv(OUT / "02_replay_candidates.csv", index=False, encoding="utf-8-sig")
    with open(OUT / "02_replay_trades.jsonl", "w", encoding="utf-8") as f:
        for t in trades:
            f.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")
    with open(OUT / "02_replay_rejects.jsonl", "w", encoding="utf-8") as f:
        for t in rejects:
            f.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")

    rdf = pd.DataFrame(runs)
    cdf = pd.DataFrame(cands)
    summary = {
        "tasks_total": len(tasks),
        "tasks_ok": int((rdf["status"] == "ok").sum()),
        "tasks_no_data": int((rdf["status"] == "no_data").sum()),
        "tasks_error": int((rdf["status"] == "error").sum()),
        "candidate_rows": int(len(cdf)),
        "candidate_rows_by_gen": cdf["generation"].value_counts().to_dict() if len(cdf) else {},
        "executed_by_gen": (cdf[cdf["executed"] == True]["generation"].value_counts().to_dict()
                            if len(cdf) else {}),
        "trades_rows": len(trades),
        "rejects_rows": len(rejects),
        "spec_status_counts": rdf["spec_status"].value_counts(dropna=False).to_dict() if len(rdf) else {},
        "formal_pass_allowed_true": int((rdf["formal_pass_allowed"] == True).sum()) if len(rdf) else 0,
        "run_valid_true": int((rdf["run_valid"] == True).sum()) if len(rdf) else 0,
        "chain_ok_true": int((rdf["chain_ok"] == True).sum()) if len(rdf) else 0,
        "identity_breaks_total": int(sum(len(x or []) for x in rdf["identity_breaks"])) if len(rdf) else 0,
        "orphan_trades_total": int(sum(len(x or []) for x in rdf["orphan_trades"])) if len(rdf) else 0,
        "slippage_split_ok_false": int((rdf["slippage_split_ok"] == False).sum()) if len(rdf) else 0,
        "fee_total_sum": float(rdf["fee_total"].fillna(0).sum()) if len(rdf) else 0.0,
        "funding_total_sum": float(rdf["funding_total"].fillna(0).sum()) if len(rdf) else 0.0,
        "net_pnl_sum": float(rdf["net_pnl"].fillna(0).sum()) if len(rdf) else 0.0,
    }
    with open(OUT / "02_replay_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

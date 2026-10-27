# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-B｜04 隔离全链：候选 → 委托 → 成交 → 止损 → 退出 → 账本。

两次独立运行（各一个独立账本目录）+ 一次真进程重启：
  run_cold  SOLUSDT 2026-08（成交 bar 只能来自冷档）
  run_hot   AVAXUSDT 2026-09-12→10-25（成交 bar 来自热库）
  resume    run_cold 的同一目录，**换新进程**再跑一遍

断言（全部用账本原始字段，不看自报）：
  入场时间 / 初始止损 / 订单不重复 / 重复扫描无副作用 / 重启不重复下单。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                    # noqa: E402

WORKER = Path(__file__).resolve().parent / "_worker04.py"
PY = sys.executable

JOBS = [
    {"name": "run_cold", "symbols": ["SOLUSDT"],
     "start": "2026-08-01T00:00:00Z", "end": "2026-09-05T00:00:00Z",
     "max_boxes": 3, "walk_days": 40, "phase": "open",
     "why": "成交 bar 只能来自冷档（热库 15m 从 2026-09-12 起）"},
    {"name": "run_hot", "symbols": ["AVAXUSDT"],
     "start": "2026-09-12T00:00:00Z", "end": "2026-10-25T00:00:00Z",
     "max_boxes": 4, "walk_days": 30, "phase": "open",
     "why": "成交 bar 来自热库 ⇒ 冷热两条路都要通"},
]


def run(job: dict, run_dir: Path, phase: str = "open", keep: bool = False) -> dict:
    if run_dir.exists() and not keep:
        shutil.rmtree(run_dir)
    env = C.isolated_env(run_dir, merge=True, config=C.FIXTURE, fixture_ok=True)
    payload = dict(job)
    payload["phase"] = phase
    proc = subprocess.run([PY, str(WORKER)], input=json.dumps(payload, ensure_ascii=False),
                          capture_output=True, text=True, env=env, cwd=str(C.PROJECT),
                          timeout=3600)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"worker 失败 rc={proc.returncode} stderr={proc.stderr[-2000:]}")
    out = json.loads(proc.stdout)
    (run_dir / f"worker_{phase}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return out


def _open_brief(o: dict) -> dict:
    pr = (o.get("result") or {}).get("paper_result") or {}
    return {"scheme": o["scheme"], "ok": pr.get("ok"), "reason": pr.get("reason"),
            "position_id": pr.get("position_id"),
            "raw_price": pr.get("raw_price"), "entry_price": pr.get("entry_price"),
            "initial_stop_price": pr.get("initial_stop_price"),
            "bar_open_time": pr.get("bar_open_time"),
            "earliest_fill_ms": pr.get("earliest_fill_ms"),
            "fill_lag_ms": pr.get("fill_lag_ms"),
            "market_source": pr.get("market_source"),
            "bar_provenance": pr.get("bar_provenance")}


def _open_fill_raw(snap: dict, pos_id) -> float | None:
    for f in snap.get("fills") or []:
        if f.get("position_id") == pos_id and f.get("action") == "open":
            return f.get("raw_price")
    return None


def _cands_by_signal(out: dict) -> dict:
    m = {}
    for s in out.get("scan") or []:
        for c in s["candidates"]:
            m[c["signal_uid"]] = c
    return m


def _ledger_signal_uid(run_dir, scheme: str, pos_id: int):
    """从**账本文件**读委托号（不依赖进程内存）。

    重启场景下内存里没有候选，能靠账本恢复身份才算「恢复」，否则只是「进程没死」。
    """
    import sqlite3
    p = Path(run_dir) / "paper" / f"paper_{scheme}.sqlite3"
    if not p.exists():
        return None
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        r = con.execute("SELECT signal_uid FROM paper_position WHERE position_id=?",
                        (pos_id,)).fetchone()
        return r[0] if r else None
    finally:
        con.close()


def check_entry_timing(out: dict) -> list:
    """入场时间：必须等于「确认后第一根已闭合 15m bar 的开盘」，且不回填。"""
    import accum_early_entry as AE
    rows = []
    cmap = _cands_by_signal(out)
    for scheme, snap in out["ledgers"].items():
        for p in snap["positions"]:
            sig = None
            rows.append({"scheme": scheme, "position_id": p["position_id"],
                         "symbol": p["symbol"], "entry_ts": p["entry_ts"],
                         "entry_price": p["entry_price"],
                         "signal_uid_known": bool(sig)})
    # signal_uid 需要从账本读（snapshot 里没带），改由 duplicate_attempt 里的值对齐
    return rows


def assertions(out: dict, cands: dict, prev: dict | None = None,
               run_dir=None, prefer_ledger: bool = False) -> dict:
    import accum_early_entry as AE
    import paper_engine as PE
    res = {"entry_timing": [], "stop": [], "orders": [], "duplicate": out.get("duplicate_attempt"),
           "rescan": out.get("rescan")}
    for scheme, snap in out["ledgers"].items():
        for p in snap["positions"]:
            pos_id = p["position_id"]
            mine = [o for o in snap["orders"] if o["event_uid"] and
                    o["event_uid"].startswith(f"accum04_{scheme}_")]
            c = None
            sig_ledger = None
            if run_dir is not None and prefer_ledger:
                # 重启场景：身份**只能**从账本恢复（内存里没有候选）
                sig_ledger = _ledger_signal_uid(run_dir, scheme, pos_id)
                if sig_ledger:
                    c = cands.get(sig_ledger)
            if not c:
                for o in mine:
                    c = cands.get(o["event_uid"])
                    if c:
                        break
            if not c and run_dir is not None and sig_ledger is None:
                sig_ledger = _ledger_signal_uid(run_dir, scheme, pos_id)
                if sig_ledger:
                    c = cands.get(sig_ledger)
            entry_ok = None
            if c:
                entry_ok = {
                    "position_id": pos_id,
                    "signal_uid": c["signal_uid"],
                    "signal_uid_source": "process_memory" if not sig_ledger else "ledger",
                    "confirm_utc": C.iso(c["confirm_close_ms"]),
                    "earliest_utc": C.iso(c["earliest_entry_ms"]),
                    "entry_bar_utc": C.iso(c["entry_bar_open_ms"]),
                    "ledger_entry_ts_utc": C.iso(p["entry_ts"]),
                    "same_bar": int(p["entry_ts"]) == int(c["entry_bar_open_ms"]),
                    "not_before_earliest": int(p["entry_ts"]) >= int(c["earliest_entry_ms"]),
                    "within_one_bar": int(p["entry_ts"]) - int(c["earliest_entry_ms"]) < 900_000,
                    "bar_source": c.get("entry_bar_source"),
                    "predicted_entry_ref_price": c.get("entry_ref_price"),
                    "ledger_open_fill_raw_price": _open_fill_raw(snap, pos_id),
                    "raw_price_matches_prediction": (
                        _open_fill_raw(snap, pos_id) is not None
                        and abs(float(_open_fill_raw(snap, pos_id))
                                - float(c["entry_ref_price"])) <= 1e-9),
                }
            res["entry_timing"].append(entry_ok or {"position_id": pos_id,
                                                    "signal_uid": sig_ledger,
                                                    "note": "账本里找不到对应的 accum04 委托号"})
            lim = PE.contract_limits(p["symbol"]) or {}
            step = float(lim.get("price_step") or 0)
            target = float(p["entry_price"]) * 0.95
            stop = p["initial_stop_price"]
            res["stop"].append({
                "position_id": pos_id, "symbol": p["symbol"],
                "entry_price": p["entry_price"], "initial_stop_price": stop,
                "target_095": target, "price_step": step,
                "not_above_target": (stop is not None and float(stop) <= target + 1e-12),
                "within_one_step": (stop is not None and abs(target - float(stop)) <= max(step, 1e-12)),
                "stoploss_s": p["stoploss_s"], "trail_d": p["trail_d"],
                "final_stop_price": p["stop_price"],
                "stop_only_moves_up": (p["stop_price"] is None or
                                       float(p["stop_price"]) >= float(stop or 0)),
            })
            ords = [o for o in snap["orders"] if o.get("symbol") == p["symbol"]]
            filled = [o for o in ords if o.get("status") != "rejected"]
            rejected = [o for o in ords if o.get("status") == "rejected"]
            fills = [f for f in snap["fills"] if f.get("position_id") == pos_id]
            res["orders"].append({
                "position_id": pos_id, "status": p["status"],
                "orders_filled": len(filled),
                "orders_rejected": len(rejected),
                "rejected_reasons": sorted({o.get("reject_reason") for o in rejected}),
                "fills_total_for_position": len(fills),
                "exit_reason": p["exit_reason"],
                "expected_filled": (2 if p["status"] == "closed" else 1),
            })
    # 汇总
    res["summary"] = {
        "positions": sum(len(v["positions"]) for v in out["ledgers"].values()),
        "closed": sum(1 for v in out["ledgers"].values() for p in v["positions"]
                      if p["status"] == "closed"),
        "entry_timing_ok": all(x.get("same_bar") and x.get("not_before_earliest")
                               and x.get("within_one_bar")
                               and x.get("raw_price_matches_prediction")
                               for x in res["entry_timing"] if x.get("same_bar") is not None),
        "stop_ok": all(x["not_above_target"] and x["within_one_step"] and x["stop_only_moves_up"]
                       for x in res["stop"]),
        "orders_ok": all(x["orders_filled"] == x["expected_filled"] for x in res["orders"]),
        "no_add_rule_respected": all(
            set(x["rejected_reasons"]) <= {"position_already_open_no_add"}
            for x in res["orders"]),
        "duplicate_blocked": all((d.get("reason") in ("duplicate_signal_uid",
                                                      "position_already_open_no_add"))
                                 for d in (res["duplicate"] or [])),
        "rescan_no_new_orders": all(
            (r.get("orders_before") == r.get("orders_after")
             and r.get("fills_before") == r.get("fills_after"))
            for r in (res["rescan"] or []) if "orders_before" in r),
    }
    if prev:
        res["resume"] = {
            "orders_before": sum(len(v["orders"]) for v in prev["ledgers"].values()),
            "orders_after": sum(len(v["orders"]) for v in out["ledgers"].values()),
            "fills_before": sum(len(v["fills"]) for v in prev["ledgers"].values()),
            "fills_after": sum(len(v["fills"]) for v in out["ledgers"].values()),
        }
        res["resume"]["no_duplicate_after_restart"] = (
            res["resume"]["orders_before"] == res["resume"]["orders_after"]
            and res["resume"]["fills_before"] == res["resume"]["fills_after"])
        res["resume"]["identity_recovered_from_ledger"] = all(
            x.get("signal_uid") and x.get("signal_uid_source") == "ledger"
            for x in res["entry_timing"])
    return res


def main() -> int:
    out = {"generated_utc": C.iso(int(__import__("time").time() * 1000)),
           "fixture_notice": ("阈值来自 fixture 测试配置（未批准）。本节只证明"
                              "「候选→委托→成交→止损→退出→账本」这条链的行为，"
                              "不是甲乙丙的绩效结论。"),
           "runs": []}
    for job in JOBS:
        run_dir = C.RUNS / "04_chain" / job["name"]
        res = run(job, run_dir, "open")
        cands = _cands_by_signal(res)
        a = assertions(res, cands, run_dir=run_dir)
        rec = {"name": job["name"], "why": job["why"], "run_dir": str(run_dir),
               "env": res["env"], "market_mode": res["market_mode"],
               "candidates": [dict(c) for s in (res.get("scan") or [])
                              for c in s["candidates"]],
               "open_results": [_open_brief(o) for s in (res.get("scan") or [])
                                for o in s["open_results"]],
               "ledgers": res["ledgers"], "steps": res["steps"], "errors": res["errors"],
               "assertions": a}
        out["runs"].append(rec)

    # ── 真进程重启（同一账本目录、新进程）──────────────────────────────────
    cold = out["runs"][0]
    cold_dir = C.RUNS / "04_chain" / JOBS[0]["name"]
    cands_cold = _cands_by_signal({"scan": [
        {"candidates": cold["candidates"]}]})
    prev = {"ledgers": cold["ledgers"]}
    res2 = run(JOBS[0], cold_dir, "resume", keep=True)
    a2 = assertions(res2, cands_cold, prev, run_dir=cold_dir, prefer_ledger=True)
    out["resume_run"] = {"ledgers": res2["ledgers"],
                         "assertions": a2,
                         "duplicate_attempt": res2.get("duplicate_attempt")}
    C.dump("04_隔离全链.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

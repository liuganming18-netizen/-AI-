# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-ANTIGRAVITY｜步骤5：重载后独立运行采样（只观察，不注入、不建单）。

与 Work 步骤8 的区别（反重力纪律）
----------------------------------
- **不复用** Work 的采样文件，本脚本自己按固定间隔抓取并保存原始样本；
- 样本里同时记录**进程身份**（PID + 创建时刻 + 命令行），用来证明采样期间没有重启；
- 只读三处：运行时状态文件、只读 SQL、只读 HTTP。**不写任何生产表、不调用下单接口**；
- 自然候选为 0 就记 0，**不承诺成交**；未跨过的边界（如 4h 收盘）标 `not_observed`，
  不得读成「通过」。

产物：out/05_运行采样.json
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-ANTIGRAVITY"
OUT = ROOT / "out"
BACKEND = PROJ / "框架/backend"
STATUS = BACKEND / "locks" / "paper_runtime_3a_status.json"
LIVE_DB = BACKEND / "live" / "live_signals.sqlite3"
PAPER_DB = PROJ / "数据/paper/paper_3a.sqlite3"
API = "http://127.0.0.1:8787"

SAMPLES = int(os.environ.get("AG_SAMPLES", "40"))
INTERVAL = int(os.environ.get("AG_INTERVAL", "30"))   # 秒
TAG = os.environ.get("AG_TAG", "A")
PROCS = {"runtime": 43827, "api": 43819, "worker": 68775, "collector": 42278}


def utc(ts=None):
    t = time.time() if ts is None else float(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def utc_ms(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(ms) / 1000)) + \
        f".{int(ms) % 1000:03d}Z"


def http_get(path, timeout=8):
    url = API + path
    cp = subprocess.run(["curl", "-s", "-m", str(timeout), "--noproxy", "*",
                         "-w", "\n__HTTP__%{http_code}", url],
                        capture_output=True, text=True, timeout=timeout + 8)
    body = cp.stdout or ""
    code = ""
    if "__HTTP__" in body:
        body, code = body.rsplit("__HTTP__", 1)
    try:
        js = json.loads(body)
    except Exception:                                        # noqa: BLE001
        js = None
    return {"path": path, "http": code.strip(), "json": js,
            "body_head": (body[:300] if js is None else None)}


def proc_identity(pid):
    """只读进程身份：PID / 创建时刻 / 命令行。用 ps，不引入额外依赖。"""
    try:
        cp = subprocess.run(["ps", "-o", "pid=,lstart=,command=", "-p", str(pid)],
                            capture_output=True, text=True, timeout=10)
        line = (cp.stdout or "").strip()
        if not line:
            return {"pid": pid, "alive": False}
        # 拆出 lstart（5 段：Dow Mon DD HH:MM:SS YYYY）
        parts = line.split(None, 6)
        return {"pid": pid, "alive": True,
                "lstart": " ".join(parts[1:6]) if len(parts) >= 6 else None,
                "command": (parts[6][:180] if len(parts) >= 7 else None)}
    except Exception as exc:                                 # noqa: BLE001
        return {"pid": pid, "error": f"{type(exc).__name__}: {exc}"}


def ro(p: Path):
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=15)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def db_snapshot():
    out = {}
    con = ro(LIVE_DB)
    try:
        out["live_signal_total"] = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        out["by_origin"] = {r[0]: r[1] for r in con.execute(
            "SELECT origin, COUNT(*) FROM live_signal GROUP BY origin")}
        out["by_exec_state"] = {r[0]: r[1] for r in con.execute(
            "SELECT executable_state, COUNT(*) FROM live_signal GROUP BY executable_state")}
        r = con.execute("SELECT MAX(COALESCE(confirmed_at,0)) FROM live_signal "
                        "WHERE origin<>'historical_research'").fetchone()
        out["latest_nonresearch_confirmed_ms"] = r[0]
        out["latest_nonresearch_confirmed_utc"] = utc_ms(r[0])
        out["nonresearch_total"] = con.execute(
            "SELECT COUNT(*) FROM live_signal WHERE origin<>'historical_research'").fetchone()[0]
        out["run"] = {r[0]: r[1] for r in con.execute("SELECT key, value_json FROM live_run")}
    finally:
        con.close()
    con = ro(PAPER_DB)
    try:
        out["pending_status"] = {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM paper_pending GROUP BY status")}
        out["position_status"] = {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM paper_position GROUP BY status")}
        out["position_max_id"] = con.execute("SELECT COALESCE(MAX(position_id),0) "
                                             "FROM paper_position").fetchone()[0]
        out["fill_count"] = con.execute("SELECT COUNT(*) FROM paper_fill").fetchone()[0]
        out["order_max_id"] = con.execute("SELECT COALESCE(MAX(order_id),0) "
                                          "FROM paper_order").fetchone()[0]
        # 既有持仓的运行管理状态：跟踪止损有没有真的在推进（4h 收盘后才可能更新）
        out["open_position_stops"] = [
            {"position_id": r[0], "symbol": r[1], "status": r[2],
             "stop_price": r[3], "initial_stop_price": r[4],
             "stop_history_n": r[5]}
            for r in con.execute(
                "SELECT p.position_id, p.symbol, p.status, p.stop_price, p.initial_stop_price,"
                " (SELECT COUNT(*) FROM paper_stop_history h WHERE h.position_id=p.position_id)"
                " FROM paper_position p WHERE p.status='open' ORDER BY p.position_id")]
        out["stop_history_total"] = con.execute(
            "SELECT COUNT(*) FROM paper_stop_history").fetchone()[0]
    finally:
        con.close()
    return out


def status_snapshot():
    try:
        d = json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception as exc:                                 # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    se = d.get("signal_engine") or {}
    wc = se.get("watermark_check") or {}
    fu = se.get("funnel") or {}
    ex = d.get("execution_daemon") or {}
    op = d.get("order_processing") or {}
    md = d.get("market_data") or {}
    ri = d.get("runtime_identity") or {}
    return {
        "runtime_identity": {"pid": ri.get("pid"), "status": ri.get("status"),
                             "started_at": ri.get("started_at"),
                             "started_utc": utc_ms(ri.get("started_at")),
                             "cycle_count": ri.get("cycle_count"),
                             "overall_health": ri.get("overall_health")},
        "last_cycle_ms": d.get("last_cycle_ms"),
        "last_cycle_utc": utc_ms(d.get("last_cycle_ms")),
        "market_data": {"status": md.get("status"),
                        "latest_15m_close_utc": utc_ms(md.get("latest_15m_close_time")),
                        "latest_1h_close_utc": utc_ms(md.get("latest_1h_close_time")),
                        "latest_4h_close_utc": utc_ms(md.get("latest_4h_close_time"))},
        "watermark_1h": se.get("watermark_1h"),
        "watermark_1h_utc": utc_ms(se.get("watermark_1h")),
        "watermark_4h": se.get("watermark_4h"),
        "watermark_4h_utc": utc_ms(se.get("watermark_4h")),
        "aligned_1h": wc.get("aligned_1h"), "lag_1h_bars": wc.get("lag_1h_bars"),
        "db_1h_max_open_utc": utc_ms(wc.get("db_1h_max_open")),
        "aligned_4h": wc.get("aligned_4h"), "lag_4h_bars": wc.get("lag_4h_bars"),
        "db_4h_max_open_utc": utc_ms(wc.get("db_4h_max_open")),
        "funnel_since_start": fu.get("since_start"),
        "funnel_history": fu.get("history"),
        "candidates_since_start": fu.get("candidates_since_start"),
        "last_candidate_utc": fu.get("last_candidate_utc"),
        "state_counter": se.get("state_counter"),
        "idle_breakdown": (se.get("idle_breakdown") or {}).get("counts"),
        "observe_counts": (se.get("observe_summary") or {}).get("counts"),
        "scan_errors_total": se.get("scan_errors_total"),
        "signals_generated_total": se.get("signals_generated_total"),
        "orders_routed_total": op.get("orders_routed_total"),
        "orders_rejected_total": op.get("orders_rejected_total"),
        "current_pending_in_db": op.get("current_pending_in_db"),
        "gate": op.get("new_entry_gate"),
        "open_positions": ex.get("open_positions"),
        "trailing_updates_total": ex.get("trailing_updates_total"),
        "pending_matched_total": ex.get("pending_matched_total"),
        "exits_triggered_total": ex.get("exits_triggered_total"),
        "execution_last_error": ex.get("last_error"),
    }


def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-ANTIGRAVITY",
         "step": "5 重载后独立运行采样（只观察）",
         "segment": TAG,
         "at_utc": utc(),
         "plan": {"samples": SAMPLES, "interval_sec": INTERVAL,
                  "expected_span_min": round(SAMPLES * INTERVAL / 60, 1),
                  "note": "分段采样；段 B 覆盖 20:00Z 的 1h 与 4h 双边界"}}
    R["procs_before"] = {k: proc_identity(v) for k, v in PROCS.items()}
    R["api_probe_before"] = {
        "health": http_get("/api/v1/health"),
        "paper_accounts": http_get("/api/v1/paper/accounts"),
    }
    # 内容级加载证据：FILL_POLICY 是否带本单改动的新键
    pa = R["api_probe_before"]["paper_accounts"]
    fp = None
    try:
        js = pa.get("json") or {}
        blk = js.get("quality") or js
        fp = blk.get("fill_policy") if isinstance(blk, dict) else None
    except Exception:                                        # noqa: BLE001
        fp = None
    R["fill_policy_from_api"] = fp
    R["load_evidence"] = {
        "fill_policy_keys": sorted(fp.keys()) if isinstance(fp, dict) else None,
        "has_on_fill_point_before_process_start":
            bool(isinstance(fp, dict) and "on_fill_point_before_process_start" in fp),
        "has_known_gap": bool(isinstance(fp, dict) and "known_gap" in fp),
        "meaning": "这两键只存在于 SIGNAL-NOW-01-WORK 改后的 paper_engine；"
                   "API 能返回它们 ⇒ 运行中的进程确实加载了新代码（不是只看文件哈希）。"}

    samples = []
    t0 = time.time()
    for i in range(SAMPLES):
        s = {"i": i, "ts": utc(), "ts_ms": int(time.time() * 1000)}
        s["status"] = status_snapshot()
        s["db"] = db_snapshot()
        if i == 0 or i == SAMPLES - 1:
            s["procs"] = {k: proc_identity(v) for k, v in PROCS.items()}
            s["api"] = {"paper_accounts": http_get("/api/v1/paper/accounts")}
        samples.append(s)
        print(f"[{i+1}/{SAMPLES}] {s['ts']} cycles={s['status'].get('runtime_identity', {}).get('cycle_count')} "
              f"wm1h={s['status'].get('watermark_1h_utc')} wm4h={s['status'].get('watermark_4h_utc')} "
              f"cand={s['status'].get('candidates_since_start')} "
              f"pend={s['db'].get('pending_status')} pos={s['db'].get('position_status')}", flush=True)
        if i < SAMPLES - 1:
            time.sleep(INTERVAL)
    R["samples"] = samples
    R["span_sec"] = round(time.time() - t0, 1)
    R["procs_after"] = {k: proc_identity(v) for k, v in PROCS.items()}
    R["api_probe_after"] = {"health": http_get("/api/v1/health"),
                            "paper_accounts": http_get("/api/v1/paper/accounts")}

    # ── 派生结论（只描述观测到的事实）──
    def first_last(key_path, samples_):
        vals = []
        for s in samples_:
            o = s
            for k in key_path:
                o = (o or {}).get(k) if isinstance(o, dict) else None
            vals.append(o)
        first, last = vals[0], vals[-1]
        seen = []
        for v in vals:
            if v not in seen:
                seen.append(v)
        return {"first": first, "last": last, "distinct": seen,
                "advanced": (first != last) if (first is not None and last is not None) else None,
                "n_distinct": len(seen),
                "note": "distinct 保留**原始**值序列（含 None），不跳过空值，"
                        "避免把「None→某值」误判成「未推进」"}

    R["derived"] = {
        "cycle_count": first_last(["status", "runtime_identity", "cycle_count"], samples),
        "last_cycle_utc": first_last(["status", "last_cycle_utc"], samples),
        "watermark_1h_utc": first_last(["status", "watermark_1h_utc"], samples),
        "watermark_4h_utc": first_last(["status", "watermark_4h_utc"], samples),
        "latest_15m_close_utc": first_last(["status", "market_data", "latest_15m_close_utc"], samples),
        "latest_4h_close_utc": first_last(["status", "market_data", "latest_4h_close_utc"], samples),
        "candidates_since_start": first_last(["status", "candidates_since_start"], samples),
        "funnel_since_start": first_last(["status", "funnel_since_start"], samples),
        "state_counter": first_last(["status", "state_counter"], samples),
        "observe_counts": first_last(["status", "observe_counts"], samples),
        "open_positions": first_last(["status", "open_positions"], samples),
        "current_pending_in_db": first_last(["status", "current_pending_in_db"], samples),
        "pending_status": first_last(["db", "pending_status"], samples),
        "position_status": first_last(["db", "position_status"], samples),
        "live_signal_total": first_last(["db", "live_signal_total"], samples),
        "by_origin": first_last(["db", "by_origin"], samples),
        "fill_count": first_last(["db", "fill_count"], samples),
        "position_max_id": first_last(["db", "position_max_id"], samples),
        "orders_routed_total": first_last(["status", "orders_routed_total"], samples),
        "trailing_updates_total": first_last(["status", "trailing_updates_total"], samples),
        "exits_triggered_total": first_last(["status", "exits_triggered_total"], samples),
        "open_position_stops": first_last(["db", "open_position_stops"], samples),
        "stop_history_total": first_last(["db", "stop_history_total"], samples),
        "scan_errors_total": first_last(["status", "scan_errors_total"], samples),
        "execution_last_error": first_last(["status", "execution_last_error"], samples),
    }
    R["process_restarted_during_sampling"] = {
        k: (R["procs_before"][k].get("lstart") != R["procs_after"][k].get("lstart"))
        for k in PROCS}
    R["verdict_facts"] = {
        "cycles_advanced": R["derived"]["cycle_count"]["advanced"],
        "watermark_1h_advanced": R["derived"]["watermark_1h_utc"]["advanced"],
        "watermark_4h_advanced": R["derived"]["watermark_4h_utc"]["advanced"],
        "natural_candidates_in_window": R["derived"]["candidates_since_start"]["last"],
        "new_positions_in_window": (
            R["derived"]["position_max_id"]["last"] != R["derived"]["position_max_id"]["first"]),
        "new_fills_in_window": (
            R["derived"]["fill_count"]["last"] != R["derived"]["fill_count"]["first"]),
        "any_process_restarted": any(R["process_restarted_during_sampling"].values()),
        "health_last": R["samples"][-1]["status"].get("runtime_identity", {}).get("overall_health"),
        "trailing_stop_updated_in_window": (
            R["derived"]["stop_history_total"]["last"] != R["derived"]["stop_history_total"]["first"]),
    }
    R["not_observed"] = [
        "自然候选→建单→成交闭环：若本段未观测到自然候选，**不得**用隔离正例替代。",
        "真实挂钟自动成交：仍只有 1 笔既有挂单成交（test_probe_succ），不是自然候选产生。",
        f"边界说明（段 {TAG}）：`watermark_1h` 只在 1h 收盘（整点后 ~1 分钟）推进；"
        "`watermark_4h` 只在 4h 收盘推进（下一根为 16:00Z 开盘、19:59:59.999Z 收盘）。"
        "窗口不跨边界时水位不动**不能**读成缺陷，跨了边界仍未动才是缺陷。",
    ]
    R["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"05_运行采样_{TAG}.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("\n=== 采样结论（只列事实）===")
    for k, v in R["verdict_facts"].items():
        print(f"  {k}: {v}")
    print("\n=== 内容级加载证据 ===")
    print(f"  fill_policy 键：{R['load_evidence']['fill_policy_keys']}")
    print(f"  含 on_fill_point_before_process_start={R['load_evidence']['has_on_fill_point_before_process_start']} "
          f"known_gap={R['load_evidence']['has_known_gap']}")
    print("\n=== 进程是否重启 ===")
    for k, v in R["process_restarted_during_sampling"].items():
        print(f"  {k}: 重启={v}  lstart={R['procs_after'][k].get('lstart')}")
    print("\n=== 未观测到（不得读成通过）===")
    for x in R["not_observed"]:
        print("  · " + x)
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

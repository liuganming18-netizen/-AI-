#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""10 · 重载后 API 原文留证（不加工、不改写，原样落盘）。

为什么单独抓：工单要求「重载前后身份/API 原文」。
`06_reload.json` 里的 `after.api` 是**解析后**的字段；这里再存一份**原始响应体**，
让复核者能直接看到运行实例吐出来的每一个键，而不是只看到我摘出来的那几个。

同时抓：
  · `/api/v1/paper/accounts`  → `quality.fill_policy`（21 键）+ `quality.note` + 账户
  · `/api/v1/paper/status`    → `runtime_identity` / `signal_engine` / `order_processing`
  · `/api/health`
  · 进程身份（pid / create_time / cmdline）与状态文件原文
输出 `out/10_api_after.json`。
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import time
from pathlib import Path

UTC = dt.timezone.utc
PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = PROJ / "evidence" / "MAINLINE-01-WORK" / "out"
BACKEND = PROJ / "框架" / "backend"
STATUS = BACKEND / "locks" / "paper_runtime_3a_status.json"
API = "http://127.0.0.1:8787"


def utc(ts=None):
    return dt.datetime.fromtimestamp(
        time.time() if ts is None else float(ts), UTC).isoformat()


def curl(path: str, timeout=12):
    cp = subprocess.run(
        ["curl", "-s", "-m", str(timeout), "-w", "\n__HTTP__%{http_code}", f"{API}{path}"],
        capture_output=True, text=True, timeout=timeout + 8)
    body = cp.stdout or ""
    code = ""
    if "__HTTP__" in body:
        body, code = body.rsplit("__HTTP__", 1)
    return {"path": path, "http": code.strip(), "raw": body,
            "json": (json.loads(body) if body.strip().startswith(("{", "[")) else None)}


def procs():
    try:
        import psutil
    except ImportError:
        return {"psutil": False}
    out = {"psutil": True, "api": None, "runtime": None, "workers": [], "collector": None}
    for p in psutil.process_iter(["pid", "cmdline", "create_time", "cwd"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
            cwd = p.info["cwd"] or ""
        except Exception:                                      # noqa: BLE001
            continue
        if str(BACKEND) not in cwd:
            continue
        rec = {"pid": p.info["pid"], "create_time_utc": utc(p.info["create_time"]),
               "cmdline": cl[:200]}
        if "adapter.py" in cl:
            out["api"] = rec
        elif "backtest_worker.py" in cl:
            out["workers"].append(rec)
        elif "unified_runtime.py" in cl:
            out["runtime"] = rec
        elif "realtime_collector.py" in cl:
            out["collector"] = rec
    out["worker_pids"] = sorted(w["pid"] for w in out["workers"])
    return out


def main() -> int:
    acc = curl("/api/v1/paper/accounts")
    st = curl("/api/v1/paper/status")
    hl = curl("/api/health")
    q = ((acc.get("json") or {}).get("quality") or {})
    fp = q.get("fill_policy") or {}
    ri = (((st.get("json") or {}).get("item") or {}).get("runtime_identity") or {})
    se = (((st.get("json") or {}).get("item") or {}).get("signal_engine") or {})
    od = (((st.get("json") or {}).get("item") or {}).get("order_processing") or {})
    rec = {
        "generated_utc": utc(),
        "processes": procs(),
        "endpoints": {"accounts": {"http": acc["http"], "raw": acc["raw"]},
                      "status": {"http": st["http"], "raw": st["raw"]},
                      "health": {"http": hl["http"], "raw": hl["raw"]}},
        "fill_policy": fp,
        "fill_policy_keys": sorted(fp.keys()),
        "fill_policy_key_count": len(fp),
        "quality_note": q.get("note"),
        "runtime_identity": ri,
        "signal_engine": {k: se.get(k) for k in
                          ("universe", "universe_declared", "watermark_1h", "watermark_4h",
                           "status")},
        "order_processing": {k: od.get(k) for k in
                             ("current_pending_in_db", "new_entry_gate")},
        "status_file": (json.loads(STATUS.read_text(encoding="utf-8"))
                        if STATUS.exists() else None),
        "checks": {
            "fill_policy_has_timestamps_split": "timestamps_split" in fp,
            "fill_policy_has_fill_ts_semantics": "fill_ts_semantics" in fp,
            "fill_policy_has_lookahead_guard": "on_lookahead_detected" in fp,
            "fill_policy_has_no_available_guard": "on_realtime_without_available_time" in fp,
            "timestamps_split_invariant_text":
                "data_available_at <= read_at <= matched_at" in
                str((fp.get("timestamps_split") or {}).get("invariant", "")),
            "fill_ts_semantics_says_matched_at":
                "撮合完成时刻" in str(fp.get("fill_ts_semantics", "")),
            "effective_mode_runtime_present": "effective_mode_runtime" in fp,
            "runtime_pid_alive": bool(ri.get("record_pid_alive")),
            "runtime_running": ri.get("status") == "running",
            "universe_526": se.get("universe") == 526,
        },
    }
    rec["all_pass"] = all(rec["checks"].values())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "10_api_after.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"all_pass": rec["all_pass"], "checks": rec["checks"],
                      "pid": ri.get("pid"), "cycle": ri.get("cycle_count"),
                      "universe": se.get("universe"),
                      "fill_policy_key_count": rec["fill_policy_key_count"]},
                     ensure_ascii=False, indent=2))
    return 0 if rec["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

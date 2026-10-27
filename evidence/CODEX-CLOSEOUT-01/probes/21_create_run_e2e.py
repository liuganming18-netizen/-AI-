#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CODEX-CLOSEOUT-01 · 收口后端到端：真实入口提交一条研究回测 → 执行 → 结果可读。

只做两件事：
  1. 记录**当前进程身份**（PID / 启动时刻）与磁盘代码 mtime，证明「跑的是新代码」；
  2. 走真实 HTTP 入口提交一条最小研究回测，轮询到终态，再取逐笔与指标。

不注入信号、不改账本、不碰生产模拟盘。
"""
from __future__ import annotations

import json
import os
import sys
import time
import datetime as dt
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
OUT = os.path.join(ROOT, "evidence", "CODEX-CLOSEOUT-01", "out")
BASE = "http://127.0.0.1:8787"

sys.path.insert(0, os.path.join(ROOT, "框架", "backend"))


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def opener():
    # 本机有 HTTP_PROXY 但没有 no_proxy ⇒ 必须显式绕过，否则 127.0.0.1 会被发给代理
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


OP = opener()


def call(method, path, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with OP.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, None


def process_binding():
    import psutil
    files = ["adapter.py", "backtest_api.py", "experiment_registry.py",
             "backtest_batches.py", "ledger_links.py", "service_manager.py"]
    mt = {}
    for f in files:
        p = os.path.join(ROOT, "框架", "backend", f)
        mt[f] = {"mtime_utc": iso(os.path.getmtime(p) * 1000),
                 "mtime_ms": int(os.path.getmtime(p) * 1000)}
    pid = None
    # 沙箱里 `psutil.net_connections()` 被拒（AccessDenied）⇒ 用 lsof 拿监听 PID
    try:
        import subprocess
        out = subprocess.check_output(["lsof", "-nP", "-iTCP:8787", "-sTCP:LISTEN", "-t"],
                                      stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        if out:
            pid = int(out.splitlines()[0])
    except Exception:
        pid = None
    out = {"api_pid": pid, "files": mt}
    if pid:
        p = psutil.Process(pid)
        out["api_started_utc"] = iso(p.create_time() * 1000)
        out["api_started_ms"] = int(p.create_time() * 1000)
        out["api_cmdline"] = p.cmdline()
        # 进程启动时刻必须晚于每个受影响文件的 mtime ⇒ 加载的就是磁盘这份
        out["loaded_newer_than_all_files"] = all(
            int(p.create_time() * 1000) > v["mtime_ms"] for v in mt.values())
        out["stale_files"] = [k for k, v in mt.items()
                              if int(p.create_time() * 1000) <= v["mtime_ms"]]
    return out


def main():
    rep = {"generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    rep["process_binding"] = process_binding()

    payload = {
        "strategy_id": "WX_EVENT_TREND_V0_1",
        "symbols": ["BTCUSDT"],
        "window_start": "2026-10-10",
        "window_end": "2026-10-12",
        "signal_timeframe": "1h",
        "exec_timeframe": "15m",
    }
    code, body = call("POST", "/api/v1/backtests", payload)
    rep["create"] = {"http": code,
                     "run_id": (body or {}).get("run_id"),
                     "status": (body or {}).get("status"),
                     "missing_reason": (body or {}).get("missing_reason"),
                     "errors": (body or {}).get("errors"),
                     "experiment": (body or {}).get("experiment")}
    rid = rep["create"]["run_id"]

    if rid:
        deadline = time.time() + 240
        seen = []
        while time.time() < deadline:
            c2, b2 = call("GET", f"/api/v1/backtests/{rid}")
            st = (b2 or {}).get("status")
            if not seen or seen[-1] != st:
                seen.append(st)
            if st in ("succeeded", "failed", "cancelled", "rejected"):
                break
            time.sleep(3)
        rep["poll_states"] = seen
        c3, b3 = call("GET", f"/api/v1/backtests/{rid}")
        rep["detail"] = {"http": c3, "status": (b3 or {}).get("status"),
                         "failure_reason": (b3 or {}).get("failure_reason"),
                         "registration": (b3 or {}).get("registration")}
        c4, b4 = call("GET", f"/api/v1/backtests/{rid}/trades")
        items = (b4 or {}).get("items") or (b4 or {}).get("trades") or []
        rep["trades"] = {"http": c4, "count": len(items),
                         "first": items[0] if items else None}
        c5, b5 = call("GET", f"/api/v1/backtests/{rid}/metrics")
        rep["metrics"] = {"http": c5,
                          "keys": sorted((b5 or {}).keys())[:20],
                          "net_return_pct": (b5 or {}).get("net_return_pct"),
                          "trades_count": (b5 or {}).get("trades_count")}

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "e2e_create_run.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:3000])


if __name__ == "__main__":
    main()

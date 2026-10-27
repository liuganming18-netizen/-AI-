# -*- coding: utf-8 -*-
"""UI-LINK-01 · 隔离环境引导：起一个**完全隔离**的后端（独立 LIVE_DIR / 账本 / 闸门 / 锁）。

为什么必须隔离：
  工单明确「日常总闸不得为测试反复切换」。生产后端（8787）的总闸与四臂账本是
  真实日常状态，测试若直接切它，会把日常运行状态改掉、并在账本里留下测试仓。
  这里用环境变量把 5 个路径全部改到 /tmp 下，起第二个 adapter（8799），
  生产进程与生产账本**零接触**。

隔离边界（逐个环境变量）：
  LIVE_DIR            → /tmp/ui-link-01/live
  PAPER_LEDGER_DIR    → /tmp/ui-link-01/paper
  PAPER_LOCK_DIR      → /tmp/ui-link-01/locks
  PAPER_GATE_FILE     → /tmp/ui-link-01/live/paper_new_entry_gate.json
  PAPER_LEDGER_ALLOW_INIT=1  （允许在空目录里新建四臂账本）
  MARKET_DB           → 生产行情库（**只读**：引擎只用来取 15m/4h 已闭合 bar 作为
                        真实成交价与止损基准；不写行情库）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
ISO = Path("/tmp/ui-link-01")
PORT = 8799
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def iso_env() -> dict:
    env = dict(os.environ)
    env.update({
        "PORT": str(PORT),
        "LIVE_DIR": str(ISO / "live"),
        "PAPER_LEDGER_DIR": str(ISO / "paper"),
        "PAPER_LOCK_DIR": str(ISO / "locks"),
        "PAPER_GATE_FILE": str(ISO / "live" / "paper_new_entry_gate.json"),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "MARKET_DB": str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"),
        "PYTHONPATH": f"{ROOT / '框架'}:{BACKEND}:{ROOT}",
        # 隔离实例不得被生产 worker/守护抢端口或复用状态
        "ADAPTER_LIGHT_CONCURRENCY": "8",
    })
    # 清掉可能继承来的代理，避免 127.0.0.1 被代理劫持
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    return env


def get(path: str, timeout=15):
    with _OPENER.open(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post(path: str, body: dict, timeout=20):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def main() -> int:
    fresh = "--fresh" in sys.argv
    if fresh and ISO.exists():
        shutil.rmtree(ISO)
    for d in ("live", "paper", "locks"):
        (ISO / d).mkdir(parents=True, exist_ok=True)

    # 已起过就不重复起
    try:
        st, _ = get("/api/health", timeout=3)
        if st == 200:
            print(json.dumps({"status": "already_running", "port": PORT}, ensure_ascii=False))
            return 0
    except Exception:
        pass

    log = ISO / "logs" / "backend-8799.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "ab")
    proc = subprocess.Popen(
        [PY, str(BACKEND / "adapter.py")],
        cwd=str(BACKEND), env=iso_env(), stdout=f, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (ISO / "backend.pid").write_text(str(proc.pid), encoding="ascii")

    for _ in range(60):
        try:
            st, body = get("/api/health", timeout=3)
            if st == 200:
                print(json.dumps({"status": "started", "port": PORT, "pid": proc.pid,
                                  "health": body}, ensure_ascii=False))
                return 0
        except Exception:
            time.sleep(0.5)
    print(json.dumps({"status": "failed_to_start", "log": str(log)}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())

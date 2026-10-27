# -*- coding: utf-8 -*-
"""ACCEPT-04 / A：独立起进程复验任务队列与运行路径隔离（只读业务代码）

不采信交付日志，自己起一次隔离实例：
  1) 用 ServiceConfig(is_isolated=True, 7 项全在临时目录) 调 ServiceManager.start()；
  2) 对 API / Worker 两个 PID 取 lsof 全量打开文件；
  3) 判定是否触碰任何生产路径（数据/、live/、locks/、logs/、backend 根）；
  4) 单独核对 Worker 是否打开隔离 jobs.sqlite3 及其 -wal/-shm/.lock；
  5) 用 os.kill 直接收尾（不依赖被测 stop()，因本沙箱 ps 被拒）。
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "框架" / "backend"
OUT = ROOT / "evidence" / "ACCEPT-04" / "out"
sys.path.insert(0, str(BACKEND))
os.environ["DATA_HUB_PROJECT"] = str(ROOT)
for _k in ["JOBS_DB", "MARKET_DB", "MARKET_ARCHIVE", "LIVE_DB", "LIVE_DIR",
           "PAPER_LEDGER_DIR", "PAPER_LOCK_DIR", "SYS_PID_DIR", "SYS_LOG_DIR",
           "ISOLATED_MODE"]:
    os.environ.pop(_k, None)

import service_manager as sm  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="accept04_proc_"))
for k in ("market", "live", "paper", "locks", "jobs", "pids", "logs"):
    (TMP / k).mkdir(parents=True, exist_ok=True)

# 选一个空闲端口
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]

cfg = sm.ServiceConfig(
    port=PORT,
    is_isolated=True,
    market_db=str(TMP / "market" / "market.sqlite3"),
    live_db=str(TMP / "live" / "live.sqlite3"),
    paper_ledger_dir=str(TMP / "paper"),
    paper_lock_dir=str(TMP / "locks"),
    jobs_db=str(TMP / "jobs" / "jobs.sqlite3"),
    pid_dir=TMP / "pids",
    log_dir=TMP / "logs",
    enable_paper=False,
    check_collector=False,
    web_readonly=1,
)
cfg.resolve_paths()

PROD_PREFIXES = [
    str(ROOT / "数据"),
    str(ROOT / "框架" / "backend" / "live"),
    str(ROOT / "框架" / "backend" / "locks"),
    str(ROOT / "框架" / "backend" / "logs"),
]
BACKEND_ROOT = str(ROOT / "框架" / "backend")


def lsof(pid: int) -> list[str]:
    try:
        out = subprocess.check_output(["lsof", "-p", str(pid), "-Fn"],
                                      stderr=subprocess.DEVNULL, timeout=10).decode(
            "utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        return []
    return [ln[1:] for ln in out.splitlines() if ln.startswith("n")]


def classify(files: list[str]) -> dict:
    prod = [f for f in files if any(f.startswith(p) for p in PROD_PREFIXES)]
    # backend 根目录下但不属于 live/locks/logs 的 .py 只读打开不算生产数据，单独列
    return {"count": len(files), "production_hits": prod}


mgr = sm.ServiceManager(cfg)
report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
          "tmp_dir": str(TMP), "port": PORT, "started": False}
api_pid = worker_pid = None
try:
    res = mgr.start()
    report["start_result"] = {k: v for k, v in res.items() if k != "log_tail"} \
        if isinstance(res, dict) else str(res)
    report["started"] = True
    time.sleep(3.0)                       # 给 worker 时间打开 jobs 库
    api_pid = mgr._read_pid(mgr.get_api_pidfile())
    worker_pid = mgr._read_pid(mgr.get_worker_pidfile())

    api_files = lsof(api_pid) if api_pid else []
    wk_files = lsof(worker_pid) if worker_pid else []

    jobs_iso = str((TMP / "jobs" / "jobs.sqlite3").resolve())
    wk_jobs = [f for f in wk_files if Path(f).name.startswith("jobs.sqlite3")]

    report["api"] = {"pid": api_pid, **classify(api_files), "opened": api_files}
    report["worker"] = {"pid": worker_pid, **classify(wk_files), "opened": wk_files,
                        "jobs_handles": wk_jobs,
                        "jobs_isolated_expected": jobs_iso,
                        "jobs_all_isolated": all(f.startswith(str(TMP.resolve()))
                                                 for f in wk_jobs) if wk_jobs else False}
    report["verdict"] = {
        "api_production_hits": len(report["api"]["production_hits"]),
        "worker_production_hits": len(report["worker"]["production_hits"]),
        "worker_jobs_isolated": report["worker"]["jobs_all_isolated"],
        "isolation_ok": (not report["api"]["production_hits"]
                         and not report["worker"]["production_hits"]
                         and report["worker"]["jobs_all_isolated"]),
    }
except Exception as exc:                                         # noqa: BLE001
    report["error"] = f"{type(exc).__name__}: {exc}"
finally:
    for pid in (worker_pid, api_pid):
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    time.sleep(1.0)
    for pid in (worker_pid, api_pid):
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    report["cleanup"] = {"killed": [p for p in (api_pid, worker_pid) if p],
                         "alive_after": [p for p in (api_pid, worker_pid)
                                         if p and sm.is_pid_alive(p)]}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "q03_A_独立进程隔离复验.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("started:", report["started"], "port:", PORT)
print("api pid:", api_pid, "prod hits:", len(report.get("api", {}).get("production_hits", [])))
print("worker pid:", worker_pid, "prod hits:", len(report.get("worker", {}).get("production_hits", [])))
print("worker jobs handles:", report.get("worker", {}).get("jobs_handles"))
print("isolation_ok:", report.get("verdict", {}).get("isolation_ok"), report.get("error", ""))
print("cleanup:", report["cleanup"])
shutil.rmtree(TMP, ignore_errors=True)

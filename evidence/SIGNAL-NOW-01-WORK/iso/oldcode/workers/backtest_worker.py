#!/usr/bin/env python3
"""回测工作进程（架构重构第 2 期）。

职责
----
只做一件事：从任务表领 `backtest` 任务，调用 `backtest_runner.execute_run()`，
把结果写回任务表。**不提供任何 HTTP 端口**，不碰网页请求。

为什么单独一个进程
------------------
`execute_run()` 是纯 CPU 计算，且内部有一把全局执行锁（`_EXEC_LOCK`）。
放在 web 进程里时，它和 HTTP 请求抢同一把 Python 全局解释器锁，
实测会让轻量接口的 p50 从毫秒级掉到秒级；一旦它崩了，整个服务跟着死。
搬到独立进程后：算力互不影响，崩溃不连坐，任务还留在表里可重试。

关键约定
--------
* **单实例**：用文件锁（`jobs.lock`）保证同机只有一个 worker 在领任务。
* **租约**：执行期间每 10 秒续租；续租失败说明任务已被判定死亡并可能被别人领走，
  此时不再写结果，只记日志。
* **确定性失败不重试**：回测自己把状态标成 `failed` 时（参数非法、闸门拒绝），
  重跑结果一样，直接落 `failed`，避免无意义地重试三次。
* **优雅退出**：收到 SIGTERM/SIGINT 后不再领新任务，把手上这个跑完再退。

用法
----
    PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
    $PY workers/backtest_worker.py --once          # 跑一个任务就退出（测试用）
    $PY workers/backtest_worker.py                 # 常驻
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sqlite3
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from infrastructure.jobs.store import (  # noqa: E402
    FAILED, RUNNING, SUCCEEDED, JobsStore, default_db_path,
)

LEASE_SECONDS = int(os.environ.get("WORKER_LEASE_SECONDS", "60"))
HEARTBEAT_SECONDS = max(2, LEASE_SECONDS // 6)
POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def log(event: str, **fields) -> None:
    """单行 JSON 日志，便于与 web 侧日志按 `job_id` / `run_id` 串联。"""
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "proc": "worker", "event": event}
    record.update(fields)
    print(json.dumps(record, ensure_ascii=False), flush=True)


class Heartbeat:
    """执行期间续租；失败即标记 `lost`。"""

    def __init__(self, store: JobsStore, job_id: str, owner: str):
        self.store = store
        self.job_id = job_id
        self.owner = owner
        self.lost = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"hb-{job_id}", daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                ok = self.store.heartbeat(self.job_id, self.owner, LEASE_SECONDS * 1000)
            except Exception as exc:                       # noqa: BLE001
                log("heartbeat_error", job_id=self.job_id, error=repr(exc))
                continue
            if not ok:
                self.lost = True
                log("lease_lost", job_id=self.job_id)
                return

    def __enter__(self) -> "Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


def _execute_tail_sync(job: dict) -> tuple[str, str | None, str | None, bool]:
    """拉取并补写行情尾段（第 3 期：整段搬出 web 请求路径）。

    为什么拉取也在这里：统一网关要写 `api_calls` / `api_leases` / `api_scope_state`
    做共享缓存与限频，web 只读之后在请求里拉取会直接失败。拉与写都归本进程，
    web 才真正只读。
    """
    payload = job.get("payload") or {}
    missing = [k for k in ("exchange", "symbol", "timeframe") if not payload.get(k)]
    if missing:
        return FAILED, None, f"任务缺少字段：{','.join(missing)}", False
    db = os.environ.get("MARKET_DB")
    if not db:
        return FAILED, None, "未设置 MARKET_DB，拒绝写库", False

    import gateway_ext
    from infrastructure.repo.tail_fetch import WriteStore, fetch_tail
    from infrastructure.repo.tail_store import store_tail

    exchange, symbol, tf = payload["exchange"], payload["symbol"], payload["timeframe"]
    gw = gateway_ext.gateway(WriteStore(db))
    bars, src = fetch_tail(gw, exchange, symbol, tf)
    closed = [b for b in bars if b.get("is_closed")]
    if not closed:
        return SUCCEEDED, "0", None, False

    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=30000")
        added = store_tail(con, exchange, symbol, tf, closed, src)
    finally:
        con.close()
    return SUCCEEDED, str(added), None, False


def _execute(job: dict) -> tuple[str, str | None, str | None, bool]:
    """执行一个任务，返回 `(终态, 结果引用, 错误, 是否值得重试)`。"""
    kind = job["kind"]
    if kind == "tail_sync":
        return _execute_tail_sync(job)
    if kind != "backtest":
        return FAILED, None, f"未知任务类型：{kind}", False

    run_id = (job.get("payload") or {}).get("run_id")
    if not run_id:
        return FAILED, None, "任务缺少 run_id", False

    import backtest_runner as RUN
    import backtest_store as S

    doc = RUN.execute_run(run_id)
    status = doc.get("status")
    if status == S.STATUS_SUCCEEDED:
        return SUCCEEDED, run_id, None, False
    # 回测已把失败原因写进 run.json；重跑不会改变结果，因此不重试。
    reason = doc.get("failure_reason") or f"回测终态={status}"
    return FAILED, run_id, reason, False


def _reconcile(store: JobsStore) -> None:
    """启动时对账：没有活跃任务的 queued/running run 标 `interrupted`。

    与旧行为（重启就把所有 queued/running 标 interrupted）的区别：
    这里**保留仍有任务在排队的 run**，避免把即将执行的回测误判为中断。
    """
    try:
        import backtest_store as S
    except Exception as exc:                               # noqa: BLE001
        log("reconcile_skip", reason=f"导入回测存储失败：{exc}")
        return

    live_runs: set[str] = set()
    try:
        rows = store._con.execute(                       # 只读查询，复用同一连接
            "SELECT DISTINCT json_extract(payload_json, '$.run_id') AS rid"
            " FROM jobs WHERE state IN ('queued','running')").fetchall()
        live_runs = {r["rid"] for r in rows if r["rid"]}
    except Exception as exc:                               # noqa: BLE001
        log("reconcile_skip", reason=f"读取任务表失败：{exc}")
        return

    try:
        touched = S.mark_orphans_interrupted(keep=live_runs)
    except TypeError:
        # 旧签名（无 keep）：只在没有活跃任务时才执行全量标记
        touched = [] if live_runs else S.mark_orphans_interrupted()
    except Exception as exc:                               # noqa: BLE001
        log("reconcile_error", error=repr(exc))
        return
    if touched:
        log("reconciled", interrupted=len(touched), kept=len(live_runs))


def main() -> int:
    ap = argparse.ArgumentParser(description="回测工作进程")
    ap.add_argument("--once", action="store_true", help="只跑一个任务就退出")
    ap.add_argument("--db", default=None, help="任务库路径（默认按 JOBS_DB/工程根推导）")
    ap.add_argument("--owner", default=None, help="租约持有者标识")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else default_db_path()
    owner = args.owner or f"worker-{os.getpid()}"

    # 单实例锁：同一任务库只允许一个 worker 领任务
    lock_path = db_path.with_suffix(db_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("worker_already_running", db=str(db_path))
        return 0

    store = JobsStore(db_path)
    log("worker_start", owner=owner, db=str(db_path), lease_s=LEASE_SECONDS)

    stop = threading.Event()

    def _on_signal(signum, _frame):
        log("signal", signum=signum)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    _reconcile(store)

    idle_logged = False
    try:
        while not stop.is_set():
            job = store.claim(owner, LEASE_SECONDS * 1000)
            if job is None:
                if not idle_logged:
                    log("idle", stats=store.stats())
                    idle_logged = True
                stop.wait(POLL_SECONDS)
                continue

            idle_logged = False
            job_id = job["job_id"]
            log("job_start", job_id=job_id, kind=job["kind"],
                attempt=job["attempts"], payload=job.get("payload"))

            with Heartbeat(store, job_id, owner) as hb:
                try:
                    state, result_ref, error, retry = _execute(job)
                except Exception as exc:                   # noqa: BLE001
                    state = FAILED
                    result_ref = None
                    error = f"{type(exc).__name__}: {exc}"
                    retry = True
                    log("job_exception", job_id=job_id,
                        traceback=traceback.format_exc()[-2000:])

            if hb.lost:
                # 租约已被收回：任务可能已分配给别的 worker，不再覆盖结果
                log("job_result_dropped", job_id=job_id, state=state)
            else:
                store.finish(job_id, owner, state, result_ref=result_ref,
                             error=error, retry=retry)
                log("job_end", job_id=job_id, state=state,
                    result=result_ref, error=(error or "")[:300])

            if args.once:
                break
    finally:
        store.close()
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except OSError:
            pass
        log("worker_stop", owner=owner)
    return 0


if __name__ == "__main__":
    sys.exit(main())

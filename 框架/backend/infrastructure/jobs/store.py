"""持久任务队列（架构重构第 2 期）。

为什么需要它
------------
改造前，回测是「HTTP 进程里的一个 daemon 线程」（`backtest_api.py:1020`）。
后果：进程重启任务消失、无法排队、无法重试、无法取消、CPU 抢占网页请求。
本模块把任务搬进一张独立的 SQLite 表，做到：

* **不丢**：任务落盘，进程重启后仍在；
* **不重**：`idempotency_key` 唯一索引，重复提交返回同一个任务；
* **有序**：按 `priority` + `created_at` 领取，而不是谁先抢到线程谁先跑；
* **可恢复**：租约（lease）到期自动回到队列，最多重试 `max_attempts` 次；
* **可观测**：任何时刻都能查「排队几件、在跑几件、失败几件」。

为什么是 SQLite 而不是消息队列
------------------------------
单机、每天几十到几百个任务。引入 Redis/RabbitMQ 会多一个常驻组件、多一份要备份
的数据、多一套要监控的东西，而任务量级差三个数量级。SQLite 足够，且可直接用任意
工具打开审计。

并发约定
--------
* 同一时刻**只有一个进程**持有写连接（web 投递、worker 领取，靠 `busy_timeout` 排队）。
* 每个任务同一时刻只被一个 worker 持有（租约 + `lease_owner` 校验）。
* 领取用 `BEGIN IMMEDIATE`，避免两个 worker 同时选中同一条。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id           TEXT PRIMARY KEY,
  kind             TEXT NOT NULL,
  idempotency_key  TEXT,
  payload_json     TEXT NOT NULL,
  state            TEXT NOT NULL,
  priority         INTEGER NOT NULL DEFAULT 0,
  attempts         INTEGER NOT NULL DEFAULT 0,
  max_attempts     INTEGER NOT NULL DEFAULT 3,
  lease_owner      TEXT,
  lease_until      INTEGER,
  heartbeat_at     INTEGER,
  next_run_at      INTEGER,
  progress_json    TEXT,
  result_ref       TEXT,
  error_json       TEXT,
  created_at       INTEGER NOT NULL,
  started_at       INTEGER,
  finished_at      INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_idem ON jobs(idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(state, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_kind  ON jobs(kind, state);
CREATE INDEX IF NOT EXISTS idx_jobs_run   ON jobs(json_extract(payload_json, '$.run_id'));
"""

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELED = "canceled"
TERMINAL = (SUCCEEDED, FAILED, CANCELED)


def now_ms() -> int:
    return int(time.time() * 1000)


def default_db_path() -> Path:
    """任务库位置：`JOBS_DB` 优先，否则 `<工程根>/数据/任务队列/jobs.sqlite3`。

    与账本同样的纪律：**路径必须显式可解释**，不从相对目录推导出一份空库冒充。
    """
    env = os.environ.get("JOBS_DB")
    if env:
        return Path(env).expanduser()
    project = os.environ.get("DATA_HUB_PROJECT")
    if project:
        root = Path(project)
    else:
        # backend/infrastructure/jobs/store.py → 上四级是工程根
        root = Path(__file__).resolve().parents[4]
    return root / "数据" / "任务队列" / "jobs.sqlite3"


class JobsStore:
    """任务表读写。线程内安全；跨进程靠 SQLite 的写锁与 `busy_timeout`。"""

    def __init__(self, path: Optional[Path | str] = None, *, timeout: float = 10.0):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(self.path), timeout=timeout,
                                    check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.execute("PRAGMA busy_timeout=10000")
        with self._lock:
            self._con.executescript(SCHEMA)

    # ── 投递 ──────────────────────────────────────────────────────────────
    def enqueue(self, kind: str, payload: dict[str, Any], *,
                idempotency_key: Optional[str] = None,
                priority: int = 0, max_attempts: int = 3) -> tuple[str, bool]:
        """投递一个任务。返回 `(job_id, 是否新建)`。

        相同 `idempotency_key` 重复投递时返回已存在的任务，**不新建、不重置状态**。
        """
        job_id = f"job_{now_ms():013d}_{uuid.uuid4().hex[:8]}"
        ts = now_ms()
        with self._lock:
            try:
                self._con.execute(
                    "INSERT INTO jobs(job_id, kind, idempotency_key, payload_json, state,"
                    " priority, max_attempts, created_at, next_run_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (job_id, kind, idempotency_key,
                     json.dumps(payload, ensure_ascii=False, sort_keys=True),
                     QUEUED, priority, max_attempts, ts, ts))
            except sqlite3.IntegrityError:
                row = self._con.execute(
                    "SELECT job_id FROM jobs WHERE idempotency_key=?",
                    (idempotency_key,)).fetchone()
                if row is None:                     # 理论上不可达：唯一冲突必能查到
                    raise
                return row["job_id"], False
        return job_id, True

    # ── 领取 ──────────────────────────────────────────────────────────────
    def claim(self, owner: str, lease_ms: int = 60_000) -> Optional[dict[str, Any]]:
        """原子领取一个可执行任务；没有则返回 None。"""
        ts = now_ms()
        with self._lock:
            try:
                self._con.execute("BEGIN IMMEDIATE")
                row = self._con.execute(
                    "SELECT * FROM jobs"
                    " WHERE ((state=? AND (next_run_at IS NULL OR next_run_at<=?))"
                    "     OR (state=? AND lease_until IS NOT NULL AND lease_until<?))"
                    "   AND attempts < max_attempts"
                    " ORDER BY priority DESC, created_at LIMIT 1",
                    (QUEUED, ts, RUNNING, ts)).fetchone()
                if row is None:
                    self._con.execute("COMMIT")
                    return None
                self._con.execute(
                    "UPDATE jobs SET state=?, lease_owner=?, lease_until=?,"
                    " heartbeat_at=?, attempts=attempts+1,"
                    " started_at=COALESCE(started_at, ?)"
                    " WHERE job_id=?",
                    (RUNNING, owner, ts + lease_ms, ts, ts, row["job_id"]))
                self._con.execute("COMMIT")
            except BaseException:
                try:
                    self._con.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            job = dict(row)
            job["state"] = RUNNING
            job["lease_owner"] = owner
            job["lease_until"] = ts + lease_ms
            job["attempts"] = int(job["attempts"]) + 1
            job["payload"] = json.loads(job.pop("payload_json") or "{}")
            job.pop("progress_json", None)
            job.pop("error_json", None)
            return job

    def heartbeat(self, job_id: str, owner: str, lease_ms: int = 60_000) -> bool:
        """续租。返回 False 表示**租约已被收回**，调用方应立即停止产生副作用。"""
        ts = now_ms()
        with self._lock:
            cur = self._con.execute(
                "UPDATE jobs SET lease_until=?, heartbeat_at=?"
                " WHERE job_id=? AND state=? AND lease_owner=?",
                (ts + lease_ms, ts, job_id, RUNNING, owner))
            return cur.rowcount > 0

    def progress(self, job_id: str, owner: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._con.execute(
                "UPDATE jobs SET progress_json=? WHERE job_id=? AND lease_owner=?",
                (json.dumps(data, ensure_ascii=False), job_id, owner))

    # ── 收尾 ──────────────────────────────────────────────────────────────
    def finish(self, job_id: str, owner: str, state: str, *,
               result_ref: Optional[str] = None,
               error: Optional[str] = None,
               retry: bool = True) -> bool:
        """结束任务。

        `retry=True` 且失败未达上限时**自动回到队列**（带指数退避）；
        `retry=False` 用于「重跑也不会变」的确定性失败（如参数非法），直接落 `failed`。
        """
        if state not in (SUCCEEDED, FAILED, CANCELED):
            raise ValueError(f"非法终态：{state}")
        ts = now_ms()
        with self._lock:
            row = self._con.execute(
                "SELECT attempts, max_attempts, state FROM jobs WHERE job_id=?",
                (job_id,)).fetchone()
            if row is None:
                return False
            if row["state"] == CANCELED:
                return False
            if state == FAILED and retry and row["attempts"] < row["max_attempts"]:
                backoff = min(60_000, 2_000 * (2 ** max(0, row["attempts"] - 1)))
                self._con.execute(
                    "UPDATE jobs SET state=?, lease_owner=NULL, lease_until=NULL,"
                    " next_run_at=?, error_json=? WHERE job_id=?",
                    (QUEUED, ts + backoff,
                     json.dumps({"message": (error or "")[:4000]}, ensure_ascii=False),
                     job_id))
                return True
            self._con.execute(
                "UPDATE jobs SET state=?, lease_owner=NULL, lease_until=NULL,"
                " finished_at=?, result_ref=?, error_json=? WHERE job_id=?",
                (state, ts, result_ref,
                 json.dumps({"message": (error or "")[:4000]}, ensure_ascii=False)
                 if error else None, job_id))
            return True

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            cur = self._con.execute(
                "UPDATE jobs SET state=?, finished_at=?, lease_owner=NULL, lease_until=NULL"
                " WHERE job_id=? AND state IN (?,?)",
                (CANCELED, now_ms(), job_id, QUEUED, RUNNING))
            return cur.rowcount > 0

    # ── 查询 ──────────────────────────────────────────────────────────────
    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["payload"] = json.loads(job.pop("payload_json") or "{}")
        for key in ("progress_json", "error_json"):
            raw = job.pop(key, None)
            job[key.replace("_json", "")] = json.loads(raw) if raw else None
        return job

    def find_by_run(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._con.execute(
                "SELECT * FROM jobs WHERE json_extract(payload_json, '$.run_id')=?"
                " ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
        return self.get(row["job_id"]) if row else None

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._con.execute(
                "SELECT state, COUNT(*) AS n FROM jobs GROUP BY state").fetchall()
        out = {QUEUED: 0, RUNNING: 0, SUCCEEDED: 0, FAILED: 0, CANCELED: 0}
        for row in rows:
            out[row["state"]] = int(row["n"])
        out["total"] = sum(out.values())
        return out

    def close(self) -> None:
        with self._lock:
            try:
                self._con.close()
            except sqlite3.Error:
                pass


__all__ = [
    "JobsStore", "default_db_path", "now_ms",
    "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "TERMINAL",
]

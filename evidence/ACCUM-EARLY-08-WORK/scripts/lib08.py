# -*- coding: utf-8 -*-
"""ACCUM-EARLY-08-WORK｜公共工具（只读 + 隔离取证）。

纪律
----
1. 生产库一律 `mode=ro` 打开；本模块不提供任何写生产的入口。
2. 所有写入只发生在 `evidence/ACCUM-EARLY-08-WORK/isolated/` 之内。
3. 零写入判据用 **SQL 级 hook**（sqlite3 `set_trace_callback`）+ 负向对照，
   **不用文件哈希单独作证**（本项目生产库正被外部进程持续写入，哈希会恒假）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "out"
ISO = BASE / "isolated"
BACKEND = PROJECT / "框架" / "backend"

MARKET_DB = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
MARKET_ARCHIVE = PROJECT / "数据" / "市场数据" / "parquet"
LIVE_DB = BACKEND / "live" / "live_signals.sqlite3"
PAPER_DIR = PROJECT / "数据" / "paper"
LOCKS = BACKEND / "locks"

PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_fingerprint(p: Path) -> dict:
    """文件身份：存在性 + 字节数 + mtime + sha256（仅对 < 64MB 的文件算哈希）。"""
    if not p.exists():
        return {"path": str(p), "exists": False}
    st = p.stat()
    rec = {"path": str(p), "exists": True, "size": st.st_size,
           "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))}
    if st.st_size < 64 * 1024 * 1024:
        rec["sha256"] = sha256_file(p)
    else:
        rec["sha256"] = None
        rec["sha256_note"] = "文件超过 64MB，本单不算全量哈希（13.9GB 行情库）；改用 size+mtime"
    return rec


def ro_conn(p: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


class SqlWriteTracer:
    """SQL 级写入探针。

    用法：
        tr = SqlWriteTracer(con)
        ... 业务读 ...
        tr.stop()
        tr.report()   # {'statements': n, 'writes': [...], 'write_count': 0}

    **负向对照**：在可写连接上执行一次 `CREATE TABLE`，探针必须记到 1 条写语句；
    否则说明探针恒真（永远报 0），本判据作废。
    """

    WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP",
                      "ALTER", "VACUUM", "REINDEX", "ATTACH", "DETACH", "PRAGMA")

    def __init__(self, con: sqlite3.Connection):
        self.statements: list[str] = []
        self.writes: list[str] = []
        self._on = True
        con.set_trace_callback(self._cb)

    def _cb(self, stmt: str) -> None:
        if not self._on:
            return
        self.statements.append(stmt)
        head = stmt.strip().split(None, 1)[0].upper() if stmt.strip() else ""
        if head in self.WRITE_PREFIXES:
            # PRAGMA 只记「会改库」的那几个；读 PRAGMA 不算写
            if head == "PRAGMA" and any(
                    k in stmt.upper() for k in ("JOURNAL_MODE=", "USER_VERSION=",
                                                "APPLICATION_ID=", "WAL_CHECKPOINT")):
                self.writes.append(stmt)
            elif head != "PRAGMA":
                self.writes.append(stmt)

    def stop(self) -> None:
        self._on = False

    def report(self) -> dict:
        return {"statements_total": len(self.statements),
                "write_count": len(self.writes),
                "writes": self.writes[:20]}


def negative_control(tmp_dir: Path) -> dict:
    """负向对照：证明 SqlWriteTracer 真的能抓到写语句。"""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    p = tmp_dir / "_negative_control.sqlite3"
    if p.exists():
        p.unlink()
    con = sqlite3.connect(str(p))
    tr = SqlWriteTracer(con)
    con.execute("CREATE TABLE probe(x INTEGER)")
    con.commit()
    tr.stop()
    rep = tr.report()
    con.close()
    p.unlink(missing_ok=True)
    return {"caught": rep["write_count"] > 0, "detail": rep,
            "note": "探针必须在可写连接上记到 CREATE TABLE；否则探针恒真、零写入结论作废"}


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None,
        timeout: int = 120) -> dict:
    t0 = time.time()
    try:
        cp = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                            capture_output=True, text=True, timeout=timeout,
                            env=env)
        return {"cmd": cmd, "returncode": cp.returncode,
                "stdout": cp.stdout, "stderr": cp.stderr[-8000:],
                "stdout_len": len(cp.stdout),
                "elapsed_s": round(time.time() - t0, 3)}
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "returncode": None, "stdout": "", "stderr": "timeout",
                "elapsed_s": round(time.time() - t0, 3)}


def proc_identity(pid: int) -> dict:
    """进程身份（**只用 lsof**）。

    为什么不用 `ps`：本机沙箱禁止 exec `ps`（实测 `PermissionError: Operation not permitted`），
    而 `lsof` 可用。可得的身份要素是：可执行文件（txt）、工作目录（cwd）、打开的库与数据库。
    命令行的逐字复原因此**不可得**，本单如实标注，不猜。
    """
    rec: dict = {"pid": pid, "alive": False}
    try:
        os.kill(pid, 0)
        rec["alive"] = True
    except OSError:
        rec["alive"] = False
    try:
        out = subprocess.run(["lsof", "-p", str(pid)], capture_output=True,
                             text=True, timeout=20).stdout
        lines = out.splitlines()
        rec["lsof_lines"] = len(lines)
        rec["cwd"] = next((l.split(None, 8)[-1] for l in lines if " cwd " in l), None)
        rec["executable"] = next((l.split(None, 8)[-1] for l in lines
                                  if " txt " in l and "Python" in l), None) or \
            next((l.split(None, 8)[-1] for l in lines if " txt " in l), None)
        rec["open_dbs"] = [l.split(None, 8)[-1] for l in lines
                           if l.rstrip().endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm"))]
        rec["open_dbs_n"] = len(rec["open_dbs"])
        rec["argv_note"] = "命令行逐字不可得（ps 被沙箱禁止）；以 pid 文件 + 运行时自报 + lsof 可执行文件/cwd 三处对撞代替"
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def dump(name: str, obj) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    return p

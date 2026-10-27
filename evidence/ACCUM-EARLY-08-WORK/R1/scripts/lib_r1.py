# -*- coding: utf-8 -*-
"""ACCUM-EARLY-08-WORK/R1｜公共工具。

沿用 08-WORK 的纪律：
  · 生产库一律 `mode=ro`；写入只发生在 `R1/isolated/` 或**明确声明**的接入动作里；
  · 零写入判据用 SQL 级写探针 + 负向对照，不单靠文件哈希；
  · 不启动第二个生产实例；HTTP 证据直接用生产 8787 的只读 GET。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[4]
BASE = Path(__file__).resolve().parents[1]          # .../R1
WORK08 = BASE.parent                                # .../ACCUM-EARLY-08-WORK
BACKEND = PROJECT / "框架" / "backend"
OUT = BASE / "out"
ISO = BASE / "isolated"

MARKET_DB = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
LIVE_DB = BACKEND / "live" / "live_signals.sqlite3"
PAPER_DIR = PROJECT / "数据" / "paper"
LOCKS = BACKEND / "locks"
ANTIGRAVITY = PROJECT / "evidence" / "ACCUM-EARLY-08-ANTIGRAVITY"
RUN_B = ANTIGRAVITY / "runs" / "antigravity_08_arm_b"
RUN_A = ANTIGRAVITY / "runs" / "antigravity_08_arm_a"

PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
API = "http://127.0.0.1:8787"
WEB = "http://127.0.0.1:3000"


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(p: Path) -> dict:
    if not p.exists():
        return {"path": str(p), "exists": False}
    st = p.stat()
    rec = {"path": str(p), "exists": True, "size": st.st_size,
           "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))}
    rec["sha256"] = sha256_file(p) if st.st_size < 64 * 1024 * 1024 else None
    if rec["sha256"] is None:
        rec["sha256_note"] = ">64MB（13.9GB 行情库），只比 size+mtime"
    return rec


def same_file(a: dict, b: dict) -> dict:
    """前后指纹是否同一份文件。

    >64MB 的文件（行情库）不算 sha256，此时**不能**拿 `None == None` 当「未变」，
    必须用 size + mtime 判定（mtime 变了就是被写过）。
    """
    if not (a.get("exists") and b.get("exists")):
        return {"same": a.get("exists") == b.get("exists"), "method": "existence",
                "sha_same": None, "size_same": None, "mtime_same": None}
    if a.get("sha256") and b.get("sha256"):
        return {"same": a["sha256"] == b["sha256"], "method": "sha256",
                "sha_same": a["sha256"] == b["sha256"],
                "size_same": a.get("size") == b.get("size"), "mtime_same": None}
    return {"same": a.get("size") == b.get("size") and a.get("mtime_utc") == b.get("mtime_utc"),
            "method": "size+mtime（>64MB 不算 sha）", "sha_same": None,
            "size_same": a.get("size") == b.get("size"),
            "mtime_same": a.get("mtime_utc") == b.get("mtime_utc")}


def ro_conn(p: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=15.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def backup_db(src: Path, dst: Path) -> dict:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=20)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    return {"src": str(src), "dst": str(dst), "bytes": dst.stat().st_size}


def reset_iso() -> Path:
    """清空隔离目录，但**保留 `pre_import/`**。

    踩过的坑：早期版本直接 `rmtree(ISO)`，把 `30_生产接入.py` 写的写前快照一起删了
    ——「回滚材料」被一个复跑动作销毁，属于自伤。写前快照只在生产库还没被本单写过时
    才能生成，一旦删掉无法原样重建。
    """
    keep = ISO / "pre_import"
    if ISO.exists():
        for sub in ISO.iterdir():
            if sub.resolve() == keep.resolve():
                continue
            if sub.is_dir():
                shutil.rmtree(sub)
            else:
                sub.unlink()
    for sub in ("live", "paper", "locks", "tmp"):
        (ISO / sub).mkdir(parents=True, exist_ok=True)
    return ISO


def run(cmd, cwd: Path | None = None, env: dict | None = None, timeout: int = 600) -> dict:
    t0 = time.time()
    try:
        cp = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                            text=True, timeout=timeout, env=env)
        return {"cmd": [str(c) for c in cmd], "returncode": cp.returncode,
                "stdout": cp.stdout, "stderr": cp.stderr[-6000:],
                "elapsed_s": round(time.time() - t0, 2)}
    except subprocess.TimeoutExpired:
        return {"cmd": [str(c) for c in cmd], "returncode": None, "stdout": "",
                "stderr": "timeout", "elapsed_s": round(time.time() - t0, 2)}


def dump(name: str, obj) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return p


def iso_env() -> dict:
    """隔离子进程环境：所有可变路径指向 R1/isolated。"""
    env = dict(os.environ)
    env.update({
        "LIVE_DB": str((ISO / "live" / "live_signals.sqlite3").resolve()),
        "LIVE_DIR": str((ISO / "live").resolve()),
        "PAPER_LEDGER_DIR": str((ISO / "paper").resolve()),
        "PAPER_LOCK_DIR": str((ISO / "locks").resolve()),
        "MARKET_DB": str(MARKET_DB),
        "PYTHONPATH": f"{PROJECT / '框架'}:{BACKEND}:{PROJECT}",
    })
    env.pop("EFFECTIVE_VIEW", None)
    return env

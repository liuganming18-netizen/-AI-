# -*- coding: utf-8 -*-
"""ACCUM-EARLY-09-WORK｜公共工具。

纪律（沿用 08-WORK/R1，本单新增两条）：
  · 生产库一律 `mode=ro`；写入只发生在 `09/isolated/` 副本上；
  · 零写入判据：账本用 sha256 逐位；行情库用 SQL 级写探针（文件指纹必变，采集进程在写）；
  · **不重启生产后端**（8787），隔离服务另起端口；
  · 复用 08-R1 的成果只读引用，不改 08 任何文件。
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

PROJECT = Path(__file__).resolve().parents[3]      # .../威科夫因子共振趋势
BASE = Path(__file__).resolve().parents[1]         # .../ACCUM-EARLY-09-WORK
BACKEND = PROJECT / "框架" / "backend"
WEB = PROJECT / "UI界面" / "web"
OUT = BASE / "out"
ISO = BASE / "isolated"
WORK08 = PROJECT / "evidence" / "ACCUM-EARLY-08-WORK" / "R1"

MARKET_DB = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
LIVE_DB = BACKEND / "live" / "live_signals.sqlite3"
PAPER_DIR = PROJECT / "数据" / "paper"
LOCKS = BACKEND / "locks"

PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
NODE = "/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node"
API = "http://127.0.0.1:8787"          # 生产（只读，不重启）
WEB_PROD = "http://127.0.0.1:3000"     # 生产前端（只读）
ISO_API_PORT = 8899                    # 本单隔离服务
# 本单隔离前端：用 3101（3100 上有来源不明的遗留 vite 进程 pid 55420，不归本单管，不动它）
ISO_WEB_PORT = 3101

# 本单允许触及的文件（开工前登记，用于变更前后对照）
TOUCH_FILES = {
    "后端_API字段映射": BACKEND / "live_api.py",
    "前端_研究区组件(新建)": WEB / "src" / "components" / "ResearchSignalSection.tsx",
    "前端_信号面板接线": WEB / "src" / "components" / "SignalListPanel.tsx",
}


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
        rec["sha256_note"] = ">64MB（行情库），只比 size+mtime"
    return rec


def same_file(a: dict, b: dict) -> dict:
    if not (a.get("exists") and b.get("exists")):
        return {"same": a.get("exists") == b.get("exists"), "method": "existence"}
    if a.get("sha256") and b.get("sha256"):
        return {"same": a["sha256"] == b["sha256"], "method": "sha256"}
    return {"same": a.get("size") == b.get("size") and a.get("mtime_utc") == b.get("mtime_utc"),
            "method": "size+mtime（>64MB 不算 sha）"}


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


def reset_dir(p: Path, keep: tuple = ()) -> Path:
    """清空目录但保留 `keep` 里的子项（避免把自己的快照/证据删掉）。"""
    keep = tuple((p / k).resolve() for k in keep)
    if p.exists():
        for sub in p.iterdir():
            if sub.resolve() in keep:
                continue
            shutil.rmtree(sub) if sub.is_dir() else sub.unlink()
    p.mkdir(parents=True, exist_ok=True)
    return p


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


def spawn(cmd, cwd: Path | None = None, env: dict | None = None) -> subprocess.Popen:
    """后台起进程（隔离服务/隔离前端用）；调用方负责终止。"""
    return subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_http(url: str, timeout_s: int = 90) -> dict:
    import urllib.error
    import urllib.request
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return {"ok": True, "status": r.status, "elapsed_s": round(time.time() - t0, 2)}
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)
    return {"ok": False, "error": last, "elapsed_s": round(time.time() - t0, 2)}


def get_json(url: str, timeout: int = 90) -> dict:
    import urllib.error
    import urllib.request
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read()
            return {"url": url, "status": r.status, "bytes": len(raw),
                    "elapsed_s": round(time.time() - t0, 3),
                    "json": json.loads(raw.decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        return {"url": url, "status": exc.code, "error": f"HTTPError {exc.code}",
                "elapsed_s": round(time.time() - t0, 3)}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "status": None, "error": f"{type(exc).__name__}: {exc}",
                "elapsed_s": round(time.time() - t0, 3)}


def dump(name: str, obj) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return p


def iso_env(extra: dict | None = None) -> dict:
    """隔离子进程环境：所有可变路径指向 09/isolated。"""
    env = dict(os.environ)
    env.update({
        "LIVE_DB": str((ISO / "live" / "live_signals.sqlite3").resolve()),
        "LIVE_DIR": str((ISO / "live").resolve()),
        "PAPER_LEDGER_DIR": str((ISO / "paper").resolve()),
        "PAPER_LOCK_DIR": str((ISO / "locks").resolve()),
        "MARKET_DB": str(MARKET_DB),
        "PYTHONPATH": f"{PROJECT / '框架'}:{BACKEND}:{PROJECT}",
    })
    if extra:
        env.update(extra)
    return env


def research_rows(db: Path) -> list:
    con = ro_conn(db)
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM live_signal WHERE origin='historical_research' "
            "ORDER BY signal_uid")]
    finally:
        con.close()


def summarise(checks: dict, concl_ok: str, concl_bad: str) -> dict:
    fail = [k for k, v in checks.items() if not v]
    return {"checks": checks, "pass_n": sum(1 for v in checks.values() if v),
            "total_n": len(checks), "fail": fail,
            "结论": concl_ok if not fail else f"{concl_bad}：{fail}"}


def print_checks(title: str, rep: dict, path: Path) -> None:
    print(f"== {title} ==")
    for k, v in rep["判定"]["checks"].items():
        print(("  [通过] " if v else "  [不通过] ") + k)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("->", path)

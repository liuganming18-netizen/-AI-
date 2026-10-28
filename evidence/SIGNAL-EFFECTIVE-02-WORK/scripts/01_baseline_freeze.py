# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-02-WORK｜步骤1：冻结基线（只读）。

纪律：
- 只读源库；快照一律走 `sqlite3.Connection.backup`（含 WAL 已提交内容）。
- **不**用主文件单独复制、**不**用文件哈希冒充一致性快照。
- 不写生产库、不动进程、不重启服务。
- 一切结论落 `baseline/BASELINE-02.json`，人读版另出 `out/01_基线摘要.md`。
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
ROOT = PROJ / "evidence" / "SIGNAL-EFFECTIVE-02-WORK"
BASE = ROOT / "baseline"
OUT = ROOT / "out"
BASE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

# ── 待快照库（含 WAL）────────────────────────────────────────────────────────
SOURCES = {
    "live_signals": PROJ / "框架/backend/live/live_signals.sqlite3",
    "paper_3a": PROJ / "数据/paper/paper_3a.sqlite3",
    "paper_3b": PROJ / "数据/paper/paper_3b.sqlite3",
    "paper_3c": PROJ / "数据/paper/paper_3c.sqlite3",
    "paper_3d": PROJ / "数据/paper/paper_3d.sqlite3",
    "jobs": PROJ / "数据/任务队列/jobs.sqlite3",
}

# ── 01 四文件六处改动 + 关键依赖，逐一哈希 ───────────────────────────────────
SRC_FILES = {
    "框架/backend/paper_engine.py": "01 改动①open_position epoch 默认 / ②position_multiplier 取值",
    "框架/backend/unified_runtime.py": "01 改动③_scan_actionable approved 判据 / ④OI 门槛默认恢复",
    "框架/backend/live_api.py": "01 改动⑤当前区 approved 判据 + excluded_breakdown",
    "框架/backend/live_worker.py": "01 改动⑥available_at 不再用 confirmed_at、epoch 传 floor",
    "框架/backend/live_store.py": "依赖：effective_floor_ms / live_run 三水位",
    "框架/backend/paper_engine_config.py": "依赖：approved_params / 冻结参数",
    "框架/backend/wyckoff_factor_resonance.py": "依赖：共振评分与倍数（工作区新文件）",
    "框架/backend/config.py": "依赖：EventTrendConfig 默认值",
    "框架/backend/service_manager.py": "依赖：服务控制",
    "框架/backend/adapter.py": "运行中：API 进程入口",
    "框架/backend/workers/backtest_worker.py": "运行中：Worker 入口",
    "框架/backend/unified_runtime.py ": "占位忽略",
}

PROC_KEYS = ("unified_runtime", "backtest_worker", "adapter.py", "realtime_collector")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def consistent_copy(src: Path, dst: Path) -> dict:
    """在线备份 API 取一致快照（源库只读打开）。"""
    if not src.exists():
        return {"source": str(src), "status": "missing"}
    con = sqlite3.connect(str(src), timeout=30.0)
    con.execute("PRAGMA query_only=1")
    try:
        bak = sqlite3.connect(str(dst))
        try:
            with bak:
                con.backup(bak)
        finally:
            bak.close()
        chk = sqlite3.connect(str(dst), timeout=30.0)
        try:
            chk.execute("PRAGMA journal_mode=DELETE")
            tables = {r[0]: chk.execute(f'SELECT COUNT(*) FROM "{r[0]}"').fetchone()[0]
                      for r in chk.execute(
                          "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
            integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            chk.close()
    finally:
        con.close()
    return {
        "source": str(src),
        "snapshot": str(dst),
        "source_size": src.stat().st_size,
        "source_mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(src.stat().st_mtime)),
        "wal_present": Path(str(src) + "-wal").exists(),
        "wal_size": Path(str(src) + "-wal").stat().st_size if Path(str(src) + "-wal").exists() else 0,
        "snapshot_size": dst.stat().st_size,
        "snapshot_sha256": sha256_file(dst),
        "integrity_check": integrity,
        "table_rows": tables,
        "status": "ok",
    }


def proc_identity() -> list:
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "ppid", "cmdline", "create_time", "cwd", "exe"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
        except Exception:
            continue
        if not any(k in cl for k in PROC_KEYS):
            continue
        if "psutil" in cl or "01_baseline_freeze" in cl:
            continue
        try:
            env = p.environ()
        except Exception:
            env = {}
        keep = ("EFFECTIVE_VIEW", "PAPER_LEDGER_DIR", "LIVE_DB", "MARKET_DB", "PAPER_LOCK_DIR",
                "PAPER_ARM", "PYTHONPATH", "PORT", "ENABLE_PAPER_RUNTIME", "WYCKOFF_RESONANCE",
                "PAPER_GATE_STRICT", "LIVE_EPOCH_MS", "PAPER_EPOCH_MS")
        try:
            openfiles = [f.path for f in p.open_files() if "sqlite" in f.path or ".db" in f.path]
        except Exception:
            openfiles = []
        out.append({
            "pid": p.info["pid"],
            "ppid": p.info["ppid"],
            "create_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime(p.info["create_time"])),
            "cwd": p.info["cwd"],
            "exe": p.info["exe"],
            "cmdline": cl,
            "env_subset": {k: env.get(k) for k in keep if env.get(k) is not None},
            "open_sqlite_files": sorted(set(openfiles)),
            "alive": p.is_running(),
        })
    out.sort(key=lambda r: r["pid"])
    return out


def live_facts() -> dict:
    """从快照副本读运行事实（读副本，不碰源库）。"""
    snap = BASE / "live_signals_snapshot.sqlite3"
    con = sqlite3.connect(str(snap))
    con.row_factory = sqlite3.Row
    facts = {}
    facts["live_run"] = [dict(r) for r in con.execute("SELECT * FROM live_run")]
    facts["live_signal_by_origin"] = [
        dict(r) for r in con.execute(
            "SELECT origin, source, executable_state, COUNT(*) n FROM live_signal "
            "GROUP BY origin, source, executable_state ORDER BY n DESC")]
    facts["live_signal_total"] = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
    facts["live_signal_by_approved"] = [
        dict(r) for r in con.execute(
            "SELECT COALESCE(json_extract(payload_json,'$.approved'),'<null>') approved, "
            "COUNT(*) n FROM live_signal GROUP BY 1 ORDER BY n DESC")]
    facts["live_signal_by_pm"] = [
        dict(r) for r in con.execute(
            "SELECT COALESCE(json_extract(payload_json,'$.position_multiplier'),'<null>') pm, "
            "COUNT(*) n FROM live_signal GROUP BY 1 ORDER BY n DESC")]
    facts["live_signal_available_at"] = [
        dict(r) for r in con.execute(
            "SELECT SUM(available_at IS NULL) null_n, COUNT(*) n FROM live_signal")]
    con.close()
    return facts


def paper_facts(name: str) -> dict:
    snap = BASE / f"{name}_snapshot.sqlite3"
    con = sqlite3.connect(str(snap))
    con.row_factory = sqlite3.Row
    f = {}
    for t in ("paper_pending", "paper_position", "paper_order", "paper_fill",
              "paper_realized", "paper_state", "paper_equity", "paper_processed"):
        try:
            rows = [dict(r) for r in con.execute(f'SELECT * FROM "{t}"')]
        except sqlite3.Error as e:
            rows = [{"error": str(e)}]
        f[t] = rows
    con.close()
    return f


def file_hashes() -> dict:
    r = {}
    for rel, why in SRC_FILES.items():
        rel = rel.strip()
        p = PROJ / rel
        if not p.exists():
            r[rel] = {"why": why, "status": "missing"}
            continue
        st = p.stat()
        r[rel] = {
            "why": why,
            "status": "ok",
            "size": st.st_size,
            "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
            "sha256": sha256_file(p),
        }
    return r


def lock_and_log_hashes() -> dict:
    r = {}
    targets = list((PROJ / "框架/backend/locks").glob("*")) + [
        PROJ / "框架/backend/service_meta_8787.json",
        PROJ / "框架/backend/runtime-3a-8787.pid",
        PROJ / "框架/backend/worker.pid",
        PROJ / "框架/backend/backend.pid",
        PROJ / "框架/backend/logs/runtime-3a-8787.pid",
        PROJ / "框架/backend/logs/runtime-3a-8787.log",
    ]
    for p in targets:
        if not p.exists():
            r[str(p.relative_to(PROJ))] = {"status": "missing"}
            continue
        st = p.stat()
        item = {
            "size": st.st_size,
            "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
            "sha256": sha256_file(p) if st.st_size < 8 << 20 else "<skip:>8MB>",
        }
        if st.st_size < 4096 and p.suffix in (".pid", ".lock"):
            item["content"] = p.read_text(errors="replace").strip()
        r[str(p.relative_to(PROJ))] = item
    return r


def http_probe() -> dict:
    out = {}
    for path in ("/api/health", "/api/live/signals?scope=current", "/api/live/run"):
        try:
            cp = subprocess.run(
                ["curl", "-s", "-m", "6", "-w", "\n__HTTP__%{http_code}",
                 f"http://127.0.0.1:8787{path}"],
                capture_output=True, text=True, timeout=12)
            body = cp.stdout
            code = ""
            if "__HTTP__" in body:
                body, code = body.rsplit("__HTTP__", 1)
            out[path] = {"http": code.strip(), "body_head": body[:1500]}
        except Exception as e:
            out[path] = {"error": repr(e)}
    return out


def git_state() -> dict:
    def run(*args):
        try:
            cp = subprocess.run(["git", *args], cwd=str(PROJ), capture_output=True,
                                text=True, timeout=120)
            return cp.stdout
        except Exception as e:
            return f"<error {e}>"
    st = run("status", "--porcelain")
    lines = [l for l in st.splitlines() if l.strip()]
    # 只看 backend 相关漂移，避免把 78G 工程全部列出
    backend = [l for l in lines if "框架/backend" in l]
    return {
        "head": run("rev-parse", "HEAD").strip(),
        "head_short": run("rev-parse", "--short", "HEAD").strip(),
        "last_commit": run("log", "-1", "--format=%H %ci %s").strip(),
        "dirty_total": len(lines),
        "dirty_backend": backend,
        "dirty_backend_n": len(backend),
    }


def main() -> int:
    now = int(time.time() * 1000)
    rep = {
        "ticket": "SIGNAL-EFFECTIVE-02-WORK",
        "step": "1 冻结基线（只读）",
        "generated_at_ms": now,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now / 1000)),
        "method": "sqlite3 Connection.backup 在线一致性快照（含 WAL 已提交内容）+ psutil 进程身份 + sha256 源码",
        "note": ("源库在运行中持续被写入；快照只代表快照时刻。"
                 "**不使用**主文件单独复制或文件哈希作为一致性证据。"
                 "本脚本对生产库零写入、对进程零操作。"),
    }
    print("[1/6] 一致性快照 …")
    rep["snapshots"] = {}
    for name, src in SOURCES.items():
        dst = BASE / f"{name}_snapshot.sqlite3"
        r = consistent_copy(src, dst)
        rep["snapshots"][name] = r
        print(f"   {name:14s} {r['status']:7s} {r.get('snapshot_size',0):>10d}B "
              f"wal={r.get('wal_size',0):>8d} {r.get('integrity_check','')}")

    print("[2/6] 进程身份 …")
    rep["processes"] = proc_identity()
    for p in rep["processes"]:
        print(f"   pid={p['pid']:<7d} {p['create_time_utc']}  {p['cmdline'][:90]}")

    print("[3/6] 源码哈希 …")
    rep["source_hashes"] = file_hashes()

    print("[4/6] locks/logs 哈希 …")
    rep["lock_log_hashes"] = lock_and_log_hashes()

    print("[5/6] 库内事实（读快照副本）…")
    rep["live_facts"] = live_facts()
    rep["paper_facts"] = {n: paper_facts(n) for n in ("paper_3a", "paper_3b", "paper_3c", "paper_3d")}

    print("[6/6] 健康/接口 + git …")
    rep["http"] = http_probe()
    rep["git"] = git_state()

    p = BASE / "BASELINE-02.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n产物：{p}  ({p.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

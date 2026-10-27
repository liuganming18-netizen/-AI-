# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-B｜共用测试台：把「独立进程 + 独立账本」跑一次事件链。

只做编排，不含任何判定：所有判定都在生产代码里（`paper_engine` / `research_15m`）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
EVID = PROJECT / "evidence" / "ACCUM-EARLY-03-B"
RUNS = EVID / "runs"
WORKER = HERE.parent / "_worker_chain.py"

PY = sys.executable
HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD = PROJECT / "数据" / "市场数据" / "parquet"
M15 = 900_000
DAY = 86_400_000

#: 真实历史事件（来自生产状态机记录，见 ACCUM-EARLY-01 的 out/07_事件回放.json）
#: 每条的 event_id 与 snapshot_utc 都是生产跑出来的，不是本单构造的。
EVENTS = [
    {
        "key": "E1_TNSR_cold",
        "symbol": "TNSRUSDT",
        "signal_uid": "ev_WX_EVENT_TREND_V0_1_binance_TNSRUSDT_1784836799999",
        "snapshot_utc": "2026-07-24T08:00:00Z",
        "expect_layer": "cold",
        "why": "快照与成交都落在热库 15m 起点（2026-09-12）之前 ⇒ 老路径根本取不到成交 bar",
    },
    {
        "key": "E2_AGT_cold",
        "symbol": "AGTUSDT",
        "signal_uid": "ev_WX_EVENT_TREND_V0_1_binance_AGTUSDT_1788177599999",
        "snapshot_utc": "2026-09-07T09:00:00Z",
        "expect_layer": "cold",
        "why": "成交 bar 在热库 15m 起点之前；且 4h 也在热库 4h 起点（2026-09-09）之前",
    },
    {
        "key": "E3_METIS_hot",
        "symbol": "METISUSDT",
        "signal_uid": "ev_WX_EVENT_TREND_V0_1_binance_METISUSDT_1791071999999",
        "snapshot_utc": "2026-10-11T20:00:00Z",
        "expect_layer": "hot",
        "why": "成交 bar 在热库里 ⇒ 对照组：接线前后结果必须逐位相同（零漂移）",
    },
]


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def ms_of(s: str) -> int:
    """UTC 字符串 → 毫秒（必须用 timegm；`mktime` 会按本地时区偏 8 小时）。"""
    import calendar
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def event_job(ev: dict, *, mode: str, end_ms: int | None = None, **extra) -> dict:
    snap = ms_of(ev["snapshot_utc"])
    job = {
        "arm": "3a",
        "event": {"symbol": ev["symbol"], "signal_uid": ev["signal_uid"],
                  "snapshot_ms": snap, "available_at_ms": snap},
        "end_ms": int(end_ms if end_ms is not None else snap + 30 * DAY),
        "mode": mode,
        "allow_new_entries": True,
    }
    job.update(extra)
    return job


def run_job(job: dict, run_name: str, *, mode: str, keep: bool = False) -> dict:
    """在独立目录 + 独立进程里跑一次；返回 worker 的 JSON。"""
    run_dir = RUNS / run_name
    if run_dir.exists() and not keep:
        shutil.rmtree(run_dir)
    (run_dir / "paper").mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update({
        "PAPER_LEDGER_DIR": str(run_dir / "paper"),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DB": str(run_dir / "live_signals.sqlite3"),
        "PAPER_GATE_FILE": str(run_dir / "gate.json"),
        "MARKET_DB": str(HOT),
        "WX_ARCHIVE_DIR": str(COLD),
    })
    if mode == "merge":
        env["WX_MARKET_MERGE"] = "1"
    else:
        env.pop("WX_MARKET_MERGE", None)

    t0 = time.time()
    proc = subprocess.run([PY, str(WORKER)], input=json.dumps(job, ensure_ascii=False),
                          capture_output=True, text=True, env=env, cwd=str(PROJECT),
                          timeout=1800)
    elapsed = round(time.time() - t0, 2)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"worker 失败 rc={proc.returncode} stderr={proc.stderr[-1500:]}")
    out = json.loads(proc.stdout)
    out["_run"] = {"name": run_name, "mode": mode, "elapsed_s": elapsed,
                   "run_dir": str(run_dir), "worker_rc": proc.returncode}
    (run_dir / "worker_out.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def ledger_identity(run_name: str) -> dict:
    """账本身份（证明本单只写了独立目录，没碰生产账本）。"""
    import hashlib
    p = RUNS / run_name / "paper" / "paper_3a.sqlite3"
    if not p.exists():
        return {"exists": False, "path": str(p)}
    return {"exists": True, "path": str(p), "bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


def production_ledger_fingerprint() -> dict:
    """生产四本账本的指纹（只读，用来证明本单没有改动它们）。"""
    import hashlib
    out = {}
    base = PROJECT / "数据" / "paper"
    for arm in ("3a", "3b", "3c", "3d"):
        p = base / f"paper_{arm}.sqlite3"
        if p.exists():
            out[arm] = {"path": str(p), "bytes": p.stat().st_size,
                        "mtime": iso(int(p.stat().st_mtime * 1000)),
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        else:
            out[arm] = {"path": str(p), "exists": False}
    return out

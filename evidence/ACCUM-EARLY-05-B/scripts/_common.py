# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-B｜共用工具：路径、时间、生产指纹、隔离环境变量。

只做编排与取证，不含任何判定；判定都在生产代码里（`accum_early_entry` /
`paper_engine` / `research_15m` / `observe_store`）。

与 04-B 的 `_common.py` 的差别（本单新增，不复制旧行为）：
  · `write_gate()` 的允许根改成本单 `runs/`（否则会写进 04-B 的目录）；
  · `isolated_env()` 的配置开关用 05 的两个环境变量（`ACCUM05_CONFIG` / `ACCUM05_FIXTURE_OK`），
    并**同时**设置 04 的旧变量为空，防止误读到 04 的 fixture；
  · 新增 `run_worker()`：所有隔离运行都走子进程，父进程不持有任何账本连接。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
EVID = PROJECT / "evidence" / "ACCUM-EARLY-05-B"
OUT = EVID / "out"
RUNS = EVID / "runs"
CONFIG_DIR = EVID / "config"
FIXTURE = CONFIG_DIR / "fixture_rules_05.json"
BLANK = CONFIG_DIR / "批准配置_空白表单_05.json"

for p in (str(PROJECT), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

H = 3_600_000
M15 = 900_000
H4 = 14_400_000
DAY = 86_400_000


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def ms_of(s: str) -> int:
    import calendar
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def sha256_file(p) -> dict:
    """分块算 sha256（`market_v2.sqlite3` 有 12 GiB，`read_bytes()` 会被系统直接杀掉）。"""
    p = Path(p)
    if not p.exists():
        return {"path": str(p), "exists": False}
    h = hashlib.sha256()
    n = 0
    with p.open("rb") as fh:
        while True:
            b = fh.read(8 * 1024 * 1024)
            if not b:
                break
            h.update(b)
            n += len(b)
    return {"path": str(p), "exists": True, "bytes": p.stat().st_size,
            "sha256": h.hexdigest(), "hashed_bytes": n}


def production_fingerprint() -> dict:
    """生产侧指纹：四本账本 + 实时信号库 + 行情库（只读，不改）。

    `market_v2.sqlite3` 的 sha **必然变化**（采集器实时写），只记录不判违规。
    """
    out = {"ledgers": {}, "live": None, "market": None}
    for arm in ("3a", "3b", "3c", "3d"):
        out["ledgers"][arm] = sha256_file(PROJECT / "数据" / "paper" / f"paper_{arm}.sqlite3")
    out["live"] = sha256_file(BACKEND / "live" / "live_signals.sqlite3")
    out["market"] = sha256_file(PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3")
    return out


def production_research_ledger_leak() -> dict:
    """生产账本目录里是否出现研究臂文件（应为 0 个）。"""
    base = PROJECT / "数据" / "paper"
    found = [str(p) for p in base.glob("paper_4*.sqlite3")] if base.exists() else []
    return {"dir": str(base), "research_ledger_files": found, "count": len(found)}


def prior_research_fingerprint() -> dict:
    """早前工单自己的研究账本指纹（本来就存在，只证明本单没碰）。"""
    out = {}
    for p in sorted((PROJECT / "数据" / "research").rglob("paper_4*.sqlite3")):
        f = sha256_file(p)
        f["mtime_utc"] = iso(p.stat().st_mtime * 1000)
        out[str(p)] = f
    return out


def isolated_env(run_dir: Path, *, merge: bool = True, config: Path | None = None,
                 fixture_ok: bool = True) -> dict:
    """构造隔离进程的环境变量。**账本/实时库/闸门都在 run_dir 内。**"""
    run_dir = Path(run_dir)
    (run_dir / "paper").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for k in ("ACCUM04_CONFIG", "ACCUM04_FIXTURE_OK"):
        env.pop(k, None)
    env.update({
        "PAPER_LEDGER_DIR": str(run_dir / "paper"),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DB": str(run_dir / "live_signals.sqlite3"),
        "PAPER_GATE_FILE": str(run_dir / "gate.json"),
        "MARKET_DB": str(PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"),
        "WX_ARCHIVE_DIR": str(PROJECT / "数据" / "市场数据" / "parquet"),
    })
    if merge:
        env["WX_MARKET_MERGE"] = "1"
    else:
        env.pop("WX_MARKET_MERGE", None)
    if config:
        env["ACCUM05_CONFIG"] = str(config)
    if fixture_ok:
        env["ACCUM05_FIXTURE_OK"] = "1"
    else:
        env.pop("ACCUM05_FIXTURE_OK", None)
    return env


def write_gate(run_dir: Path, *, allow: bool = True, set_by: str = "ACCUM-EARLY-05-B_isolated") -> dict:
    """在**隔离目录内**写放行闸门。

    两道拒绝（都必须真能拒，否则负向测试就是自比断言）：
      ① 目标目录必须在 `evidence/ACCUM-EARLY-05-B/runs/` 之下（不许写到生产目录）；
      ② 闸门文件必须落在目标目录里。
    """
    run_dir = Path(run_dir).resolve()
    runs_root = RUNS.resolve()
    if run_dir != runs_root and runs_root not in run_dir.parents:
        raise SystemExit(
            f"拒绝：闸门目录 {run_dir} 不在隔离运行根 {runs_root} 之下"
            f"（禁止把放行闸门写到生产目录）")
    g = (run_dir / "gate.json").resolve()
    if run_dir not in g.parents:
        raise SystemExit(f"拒绝：闸门文件 {g} 不在隔离目录 {run_dir} 内")
    run_dir.mkdir(parents=True, exist_ok=True)
    g.write_text(json.dumps({"allow_new_entries": bool(allow), "set_by": set_by,
                             "reason": "隔离自测：只在本单独立账本内放行/关闭新开仓"},
                            ensure_ascii=False), encoding="utf-8")
    return {"gate_file": str(g), "inside_run_dir": True, "allow": bool(allow)}


def dump(name: str, obj) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str),
                 encoding="utf-8")
    print(json.dumps({"written": str(p), "bytes": p.stat().st_size}, ensure_ascii=False))
    return p


def load_out(name: str):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def run_worker(worker: Path, job: dict, run_dir: Path, *, merge: bool = True,
               config: Path | None = None, fixture_ok: bool = True,
               timeout: int = 3600, extra_env: dict | None = None) -> dict:
    """跑一个隔离子进程（stdin 给 job、stdout 收 json）。父进程不碰账本。"""
    env = isolated_env(run_dir, merge=merge, config=config, fixture_ok=fixture_ok)
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})
    proc = subprocess.run([sys.executable, str(worker)],
                          input=json.dumps(job, ensure_ascii=False),
                          capture_output=True, text=True, env=env,
                          cwd=str(PROJECT), timeout=timeout)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"worker 失败 rc={proc.returncode} "
                           f"stdout={proc.stdout[-800:]} stderr={proc.stderr[-2000:]}")
    return json.loads(proc.stdout)

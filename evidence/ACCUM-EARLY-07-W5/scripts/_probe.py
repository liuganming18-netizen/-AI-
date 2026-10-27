# -*- coding: utf-8 -*-
"""ACCUM-EARLY-07-W5｜链路冒烟探针（只读策略代码，只写本窗 runs/）

目的：在正式跑全币池之前，用极少数标的确认
  ① 配置能过闸（ready / open_allowed）；
  ② 观察 → 候选 → 委托 → 成交 → 退出 真的走通（有账本行，不是只有事件）；
  ③ 账本表结构与我汇总脚本要读的列一致；
  ④ 单标的耗时，用来估全池时长。

不修改任何共享代码；env 全部指向本窗 runs/ 下的临时目录。
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
W5 = HERE.parents[1]
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
FIXTURE = PROJECT / "evidence" / "ACCUM-EARLY-06-W2" / "01" / "config" / "fixture_rules_06w2.json"

H = 3_600_000
DAY = 86_400_000


def ms_of(s: str) -> int:
    import calendar
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def isolated_env(run_dir: Path, config: Path) -> dict:
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
        "WX_MARKET_MERGE": "1",
        "ACCUM05_CONFIG": str(config),
        "ACCUM05_FIXTURE_OK": "1",
    })
    return env


WORKER = r'''
import json, sys, time
sys.path.insert(0, r"%s")
sys.path.insert(0, r"%s")
job = json.load(sys.stdin)
import accum_early_entry as A
cfg = A.load_config(job["config"])
brief = {k: cfg.get(k) for k in ("present","ready","open_allowed","open_scope","kind",
                                "approved","blocked_by","config_sha256","missing","unsupported")}
brief["missing_n"] = len(cfg.get("missing") or [])
brief["fixed_rules_ok"] = (cfg.get("fixed_rules") or {}).get("ok")
brief["economic"] = (cfg.get("economic_caliber") or {}).get("declared_mismatch")
if not cfg.get("ready"):
    print(json.dumps({"config": brief, "fatal": "config_not_ready"}, ensure_ascii=False))
    raise SystemExit(0)
t0 = time.time()
res = A.research_run(config=job["config"], symbols=job["symbols"],
                     start_ms=job["start_ms"], end_ms=job["end_ms"],
                     schemes=("4a","4b","4c"), open_candidates=True,
                     drive_exits=True, max_boxes=job["max_boxes"],
                     walk_days=job["walk_days"],
                     observe_db=job["observe_db"])
el = time.time() - t0
out = {"config": brief, "elapsed_s": round(el, 2),
       "errors": res["errors"][:5], "n_errors": len(res["errors"]),
       "identity_engine": res["identity"]["engine"],
       "per_symbol": [], "exits_n": len(res["exits"]),
       "exit_reasons": {},
       "ledgers": {s: {k: len(v) if isinstance(v, list) else v
                       for k, v in led.items()} for s, led in res["ledgers"].items()},
       "candidates": {s: 0 for s in ("4a","4b","4c")},
       "open_results": {s: {"opened": 0, "rejected": 0, "reasons": {}} for s in ("4a","4b","4c")},
       "symbols_zero_cand": 0}
for r in res["per_symbol"]:
    out["per_symbol"].append({"symbol": r["symbol"], "boxes": r["boxes_formed"],
                              "cands": len(r["candidates"]), "acts": r["actions"]})
    if not r["candidates"]:
        out["symbols_zero_cand"] += 1
    for c in r["candidates"]:
        out["candidates"][c["scheme"]] += 1
        orr = c.get("open_result") or {}
        if orr.get("ok"):
            out["open_results"][c["scheme"]]["opened"] += 1
        else:
            out["open_results"][c["scheme"]]["rejected"] += 1
            why = orr.get("reason") or "no_result"
            out["open_results"][c["scheme"]]["reasons"][why] = \
                out["open_results"][c["scheme"]]["reasons"].get(why, 0) + 1
for e in res["exits"]:
    k = e.get("exit_class") or e.get("reason") or "unknown"
    out["exit_reasons"][k] = out["exit_reasons"].get(k, 0) + 1
print(json.dumps(out, ensure_ascii=False, default=str))
''' % (str(PROJECT), str(BACKEND))


def main() -> int:
    run_dir = W5 / "runs" / "probe"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "gate.json").write_text(json.dumps(
        {"allow_new_entries": True, "set_by": "W5_probe"}), encoding="utf-8")
    worker = run_dir / "_worker.py"
    worker.write_text(WORKER, encoding="utf-8")

    job = {
        "config": str(FIXTURE),
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "start_ms": ms_of("2026-09-01T00:00:00Z"),
        "end_ms": ms_of("2026-10-01T00:00:00Z"),
        "max_boxes": 3, "walk_days": 30,
        "observe_db": str(run_dir / "observe.sqlite3"),
    }
    env = isolated_env(run_dir, FIXTURE)
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(worker)],
                          input=json.dumps(job), capture_output=True, text=True,
                          env=env, cwd=str(PROJECT), timeout=3600)
    wall = time.time() - t0
    print("rc", proc.returncode, "wall_s", round(wall, 2))
    if proc.returncode != 0:
        print("STDERR:", proc.stderr[-3000:])
        return 1
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    print(json.dumps(data, ensure_ascii=False, indent=1)[:6000])

    # 账本表结构
    import sqlite3
    for f in sorted((run_dir / "paper").glob("paper_*.sqlite3")):
        con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        print(f"\n== {f.name} tables={tabs}")
        for t in ("paper_account", "paper_state", "paper_realized", "paper_position",
                  "paper_funding", "paper_order", "paper_fill"):
            if t not in tabs:
                continue
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: n={n} cols={cols}")
            if n:
                row = dict(con.execute(f"SELECT * FROM {t} LIMIT 1").fetchone())
                print("    sample:", {k: row[k] for k in list(row)[:14]})
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

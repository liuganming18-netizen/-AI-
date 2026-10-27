# -*- coding: utf-8 -*-
"""ACCUM-EARLY-07-W5｜候选量探针：同一批标的，不同 max_boxes / 窗口，看候选到底出不出。

只回答一个问题：这套 fixture 配置在全池上到底能不能产出可比的成交样本。
不写策略代码，不碰生产账本。
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


def ms_of(s: str) -> int:
    import calendar
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


WORKER = r'''
import json, sys, time
sys.path.insert(0, r"%s")
sys.path.insert(0, r"%s")
job = json.load(sys.stdin)
import accum_early_entry as A
t0 = time.time()
res = A.research_run(config=job["config"], symbols=job["symbols"],
                     start_ms=job["start_ms"], end_ms=job["end_ms"],
                     schemes=("4a","4b","4c"), open_candidates=job["open"],
                     drive_exits=False, max_boxes=job["max_boxes"],
                     walk_days=30, observe_db=job["observe_db"])
out = {"elapsed_s": round(time.time()-t0, 2), "errors": res["errors"][:3],
       "per": [], "cand": {"4a":0,"4b":0,"4c":0}, "blocked": {}, "states": {}}
for r in res["per_symbol"]:
    out["per"].append({"s": r["symbol"], "boxes": r["boxes_formed"],
                       "used": r["boxes_used"], "c": len(r["candidates"]),
                       "acts": r["actions"]})
    for c in r["candidates"]:
        out["cand"][c["scheme"]] += 1
    for e in r["events"]:
        if e.get("blocked_config"):
            out["blocked"][e["reason"]] = out["blocked"].get(e["reason"], 0) + 1
        else:
            k = e.get("terminal_reason") or e.get("reason") or e.get("state")
            out["states"][str(k)[:60]] = out["states"].get(str(k)[:60], 0) + 1
print(json.dumps(out, ensure_ascii=False, default=str))
''' % (str(PROJECT), str(BACKEND))


def run(tag: str, symbols, start, end, max_boxes: int, open_=False) -> dict:
    run_dir = W5 / "runs" / f"probe_{tag}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    (run_dir / "paper").mkdir(parents=True, exist_ok=True)
    (run_dir / "gate.json").write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")
    w = run_dir / "_w.py"
    w.write_text(WORKER, encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "PAPER_LEDGER_DIR": str(run_dir / "paper"),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DB": str(run_dir / "live_signals.sqlite3"),
        "PAPER_GATE_FILE": str(run_dir / "gate.json"),
        "MARKET_DB": str(PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"),
        "WX_ARCHIVE_DIR": str(PROJECT / "数据" / "市场数据" / "parquet"),
        "WX_MARKET_MERGE": "1",
        "ACCUM05_CONFIG": str(FIXTURE),
        "ACCUM05_FIXTURE_OK": "1",
    })
    job = {"config": str(FIXTURE), "symbols": symbols, "start_ms": ms_of(start),
           "end_ms": ms_of(end), "max_boxes": max_boxes, "open": open_,
           "observe_db": str(run_dir / "observe.sqlite3")}
    t0 = time.time()
    p = subprocess.run([sys.executable, str(w)], input=json.dumps(job),
                       capture_output=True, text=True, env=env,
                       cwd=str(PROJECT), timeout=7200)
    if p.returncode != 0:
        return {"tag": tag, "rc": p.returncode, "err": p.stderr[-1500:]}
    d = json.loads(p.stdout.strip().splitlines()[-1])
    d["tag"] = tag
    d["wall_s"] = round(time.time() - t0, 2)
    d["max_boxes"] = max_boxes
    d["window"] = [start, end]
    return d


def main() -> int:
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "LINKUSDT"]
    res = []
    res.append(run("mb3_1m", syms, "2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z", 3))
    res.append(run("mb12_4m", syms, "2026-06-27T08:00:00Z", "2026-10-26T09:00:00Z", 12))
    for d in res:
        print(json.dumps(d, ensure_ascii=False, indent=1)[:2500])
    (W5 / "out" / "probe_candidates.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

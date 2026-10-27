#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A-4｜子进程环境下 live_store 路径常量解析验证（零常驻进程）

验证点：
  1. 用与 shared_env 等价的环境变量，在**独立子进程**里 import live_store，
     确认 LEDGER_DIR / LIVE_DB / MARKET_DB / LOCK_DIR 全部解析到隔离根
  2. 对照组：不注入 PAPER_LEDGER_DIR 时，LEDGER_DIR 是否回落生产 `数据/paper`
     —— 证明「隔离成立完全依赖环境变量注入，一旦漏传即回落生产」
  3. 验证 MARKET_ARCHIVE 缺失时 adapter/metrics_api 的 ARCHIVE_ROOT 回落值
"""
from __future__ import annotations

import json
import os
import sys
import subprocess
import tempfile
import datetime
from pathlib import Path

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT_ROOT / "框架" / "backend"
PY = sys.executable

PROBE = r'''
import json, os, sys
sys.path.insert(0, r"%s")
sys.path.insert(0, r"%s")
import live_store as L
out = {
    "LEDGER_DIR": str(L.LEDGER_DIR),
    "PAPER_DIR": str(L.PAPER_DIR),
    "LEDGER_MANIFEST": str(L.LEDGER_MANIFEST),
    "LIVE_DIR": str(L.LIVE_DIR),
    "LIVE_DB": str(L.LIVE_DB),
    "MARKET_DB": str(L.MARKET_DB),
    "INIT_EQUITY": L.INIT_EQUITY,
    "get_configured_initial_equity": L.get_configured_initial_equity(),
}
try:
    import unified_runtime as U
    out["unified_runtime_lock_dir"] = str(U._lock_dir())
except Exception as exc:
    out["unified_runtime_lock_dir"] = f"<{type(exc).__name__}: {exc}>"
try:
    import project_paths as PP
    out["project_paths_MARKET_ARCHIVE"] = str(PP.MARKET_ARCHIVE)
except Exception as exc:
    out["project_paths_MARKET_ARCHIVE"] = f"<{type(exc).__name__}: {exc}>"
print(json.dumps(out, ensure_ascii=False))
''' % (str(BACKEND), str(PROJECT_ROOT / "框架"))


def run_probe(env_overrides: dict) -> dict:
    env = dict(os.environ)
    for k in ("PAPER_LEDGER_DIR", "PAPER_LOCK_DIR", "LIVE_DB", "LIVE_DIR",
              "MARKET_DB", "MARKET_ARCHIVE", "JOBS_DB", "PAPER_INITIAL_EQUITY"):
        env.pop(k, None)
    env.update(env_overrides)
    try:
        r = subprocess.run([PY, "-c", PROBE], capture_output=True, text=True, timeout=60, env=env)
        if r.returncode != 0:
            return {"error": r.stderr[-800:]}
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="_audit03_env_"))
    for k in ("market", "live", "paper_ledger", "locks", "pids", "logs"):
        (tmp / k).mkdir(parents=True, exist_ok=True)

    iso_env = {
        "PAPER_LEDGER_DIR": str(tmp / "paper_ledger"),
        "PAPER_LOCK_DIR": str(tmp / "locks"),
        "LIVE_DB": str(tmp / "live" / "live_iso.sqlite3"),
        "LIVE_DIR": str(tmp / "live"),
        "MARKET_DB": str(tmp / "market" / "market_iso.sqlite3"),
        "JOBS_DB": str(tmp / "market" / "jobs_iso.sqlite3"),
    }

    rec = {
        "工单": "SYSTEM-INDEPENDENT-AUDIT-03 / A-4",
        "检查时刻": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "隔离根": str(tmp),
        "组1_全量注入": run_probe(iso_env),
        "组2_不注入PAPER_LEDGER_DIR": run_probe({k: v for k, v in iso_env.items() if k != "PAPER_LEDGER_DIR"}),
        "组3_全不注入(裸环境)": run_probe({}),
        "组4_注入PAPER_INITIAL_EQUITY=2000": run_probe({**iso_env, "PAPER_INITIAL_EQUITY": "2000"}),
    }

    prod_ledger = str((PROJECT_ROOT / "数据" / "paper").resolve())
    prod_market = str((PROJECT_ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3").resolve())
    prod_archive = str((PROJECT_ROOT / "数据" / "市场数据" / "parquet").resolve())
    iso_s = str(tmp.resolve())

    def judge(g: dict) -> dict:
        if "error" in g:
            return {"判定": "探测失败", "错误": g["error"][:300]}
        def hits(v):
            return {
                "指向生产账本": str(v).startswith(prod_ledger),
                "指向生产行情库": str(v) == prod_market,
                "指向生产归档": str(v).startswith(prod_archive),
                "在隔离根内": str(v).startswith(iso_s) or str(v).startswith("/private" + iso_s),
            }
        return {
            "LEDGER_DIR": {"value": g.get("LEDGER_DIR"), **hits(g.get("LEDGER_DIR"))},
            "LIVE_DB": {"value": g.get("LIVE_DB"), **hits(g.get("LIVE_DB"))},
            "MARKET_DB": {"value": g.get("MARKET_DB"), **hits(g.get("MARKET_DB"))},
            "unified_runtime_lock_dir": g.get("unified_runtime_lock_dir"),
            "project_paths_MARKET_ARCHIVE": g.get("project_paths_MARKET_ARCHIVE"),
            "initial_equity": g.get("get_configured_initial_equity"),
        }

    rec["判定"] = {k: judge(v) for k, v in rec.items() if k.startswith("组")}

    outp = PROJECT_ROOT / "evidence" / "SYSTEM-INDEPENDENT-AUDIT-03" / "out" / "A4_env_resolution.json"
    outp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    for k, v in rec["判定"].items():
        print(f"\n== {k} ==")
        if "错误" in v:
            print("   ", v["错误"][:200]); continue
        for f in ("LEDGER_DIR", "LIVE_DB", "MARKET_DB"):
            d = v[f]
            flags = [n for n in ("指向生产账本", "指向生产行情库", "指向生产归档", "在隔离根内") if d.get(n)]
            print(f"   {f:14s} {str(d['value'])[-58:]:60s} {flags}")
        print(f"   lock_dir      {v['unified_runtime_lock_dir']}")
        print(f"   MARKET_ARCHIVE{str(v['project_paths_MARKET_ARCHIVE'])[-58:]}")
        print(f"   初始权益      {v['initial_equity']}")
    print(f"\n落盘: {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

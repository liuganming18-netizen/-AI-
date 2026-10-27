# -*- coding: utf-8 -*-
"""q06｜版本核对：源码 SHA-256 与进程启动时刻绑定（只读）。

判据（工单第 5 项）：最后一次交付之后重新核对版本。
  · 文件身份 = SHA-256（唯一可跨时段复用的身份）；
  · 进程身份 = PID + create_time（只在当次运行有效）；
  · 若「影响运行的文件 mtime > 进程 create_time」⇒ 该进程跑的是旧代码（进程落后于代码）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
TZ = dt.timezone(dt.timedelta(hours=8))

WATCH = [
    "框架/backend/unified_runtime.py",
    "框架/backend/paper_engine.py",
    "框架/backend/live_api.py",
    "框架/backend/live_store.py",
    "框架/backend/adapter.py",
    "框架/backend/service_manager.py",
    "框架/backend/live_worker.py",
    "框架/backend/realtime_collector.py",
    "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "UI界面/web/src/components/PositionPanel.tsx",
    "UI界面/web/src/components/SignalListPanel.tsx",
    "UI界面/web/src/api/liveTrading.ts",
]


def sha(p: Path):
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bj(ts):
    return dt.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M:%S")


def main():
    now = dt.datetime.now(TZ)
    procs = []
    try:
        import psutil
        for pid in (42250, 50390, 42256, 42278, 55012):
            try:
                p = psutil.Process(pid)
                procs.append({"pid": pid, "create_time": bj(p.create_time()),
                              "create_ts": p.create_time(),
                              "cmd": " ".join(p.cmdline())[:160]})
            except Exception:
                procs.append({"pid": pid, "missing": True})
    except Exception as e:
        procs.append({"error": str(e)})

    files = {}
    for rel in WATCH:
        p = ROOT / rel
        if not p.exists():
            files[rel] = {"exists": False}
            continue
        st = p.stat()
        files[rel] = {"exists": True, "sha256": sha(p), "size": st.st_size,
                      "mtime": bj(st.st_mtime), "mtime_ts": st.st_mtime}

    # 进程落后于代码：只看「会进入该进程运行路径」的文件
    lag = {}
    pairs = {
        42250: ["框架/backend/adapter.py", "框架/backend/live_api.py", "框架/backend/paper_engine.py",
                "框架/backend/live_store.py", "框架/backend/unified_runtime.py",
                "框架/backend/service_manager.py"],
        50390: ["框架/backend/unified_runtime.py", "框架/backend/paper_engine.py",
                "框架/backend/live_store.py", "框架/backend/live_api.py",
                "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
                "策略/WyckoffMaster/event_trend_v0_1/config.py"],
        42278: ["框架/backend/realtime_collector.py"],
    }
    ct = {p["pid"]: p.get("create_ts") for p in procs if "create_ts" in p}
    for pid, rels in pairs.items():
        base = ct.get(pid)
        if not base:
            lag[str(pid)] = {"missing": True}
            continue
        rows = []
        for rel in rels:
            f = files.get(rel, {})
            if f.get("mtime_ts"):
                rows.append({"file": rel, "mtime": f["mtime"],
                             "newer_than_process": f["mtime_ts"] > base,
                             "delta_sec": round(f["mtime_ts"] - base, 1)})
        lag[str(pid)] = {"process_create": bj(base),
                         "files_newer_than_process": [r["file"] for r in rows if r["newer_than_process"]],
                         "rows": rows}

    out = {"checked_at": now.strftime("%Y-%m-%d %H:%M:%S"), "files": files,
           "processes": procs, "lag": lag}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "q06_version.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("checked_at", out["checked_at"])
    for rel, f in files.items():
        print(f"{rel:70s} {f.get('sha256','-')[:16]}  {f.get('mtime','-')}")
    print("--- processes ---")
    for p in procs:
        print(p)
    print("--- lag ---")
    for pid, v in lag.items():
        print(pid, v.get("process_create"), "newer:", v.get("files_newer_than_process"))


if __name__ == "__main__":
    main()

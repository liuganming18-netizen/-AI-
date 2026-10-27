#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""03_iso_env.py —— 隔离后端（8799）真实环境取证

不看脚本自述、不看截图：直接读进程环境变量 + 读隔离闸门文件是否存在，
再调隔离实例自己的 /paper/status 看它怎么报自己的闸门。

输出：out/05_隔离进程环境.json
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "LINK-ACCEPT-01" / "out"
ISO = Path("/tmp/ui-link-01")
PORT = 8799
KEYS = ["PORT", "LIVE_DIR", "PAPER_LEDGER_DIR", "PAPER_LOCK_DIR", "PAPER_GATE_FILE",
        "PAPER_LEDGER_ALLOW_INIT", "MARKET_DB", "EFFECTIVE_VIEW", "MARKET_ARCHIVE",
        "HTTP_PROXY", "HTTPS_PROXY"]


def listen_pids(port: int) -> list[int]:
    try:
        out = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=15).stdout
        return sorted({int(x) for x in out.split() if x.strip().isdigit()})
    except Exception:  # noqa: BLE001
        return []


def get(path: str):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(f"http://127.0.0.1:{PORT}{path}", timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"[:200]


def main() -> None:
    import psutil

    res = {"采样时刻": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "端口": PORT}
    pids = listen_pids(PORT)
    res["监听进程"] = pids
    if pids:
        p = psutil.Process(pids[0])
        env = p.environ()
        res["进程"] = {"pid": pids[0], "命令行": " ".join(p.cmdline())[:200],
                     "启动时刻": dt.datetime.fromtimestamp(p.create_time()).strftime("%Y-%m-%d %H:%M:%S"),
                     "cwd": p.cwd()}
        res["环境"] = {k: env.get(k) for k in KEYS}
        # 隔离判据：4 条写路径是否都在 ISO 下
        write_keys = ["LIVE_DIR", "PAPER_LEDGER_DIR", "PAPER_LOCK_DIR", "PAPER_GATE_FILE"]
        res["写路径全部隔离"] = all(str(env.get(k) or "").startswith(str(ISO)) for k in write_keys)
        res["写路径明细"] = {k: str(env.get(k) or "").startswith(str(ISO)) for k in write_keys}
        # 读共享：行情库 / 参数视图
        res["读共享"] = {
            "MARKET_DB_指向生产": str(env.get("MARKET_DB") or "").startswith(str(ROOT / "数据")),
            "EFFECTIVE_VIEW_已设": bool(env.get("EFFECTIVE_VIEW")),
            "说明": "EFFECTIVE_VIEW 未设 ⇒ paper_engine.py:174 回落工程内正式位置（生产参数视图，只读）",
        }

    # 隔离目录实况
    res["隔离目录"] = {
        "存在": ISO.is_dir(),
        "账本文件": sorted(p.name for p in (ISO / "paper").glob("*.sqlite3")) if (ISO / "paper").is_dir() else [],
        "闸门文件存在": (ISO / "live" / "paper_new_entry_gate.json").exists(),
    }

    # 隔离实例自己怎么报闸门
    st, body = get("/api/v1/paper/status")
    res["隔离实例状态接口"] = {"code": st}
    if st == 200:
        it = body.get("item") or {}
        g = (it.get("order_processing") or {}).get("new_entry_gate") or {}
        res["隔离实例状态接口"].update({
            "allow_new_entries": g.get("allow_new_entries"),
            "reason": g.get("reason"),
            "gate_file": g.get("gate_file"),
            "pid": (it.get("runtime_identity") or {}).get("pid"),
        })

    (OUT / "05_隔离进程环境.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"8799 监听 {pids}；写路径全部隔离={res.get('写路径全部隔离')}")
    for k, v in (res.get("写路径明细") or {}).items():
        print(f"  {k}: {v}")
    print(f"  读共享: {res.get('读共享')}")
    print(f"  隔离账本: {res.get('隔离目录', {}).get('账本文件')}")
    print(f"  隔离闸门文件存在: {res.get('隔离目录', {}).get('闸门文件存在')}")
    print(f"  隔离实例自报: {res.get('隔离实例状态接口')}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-01-WORK｜隔离复现：被改身份的历史研究行「离真下单只差一个臂号」。

做法（**生产零写入**）：
  1. 用 `baseline/` 一致性快照复制出隔离信号库与隔离账本目录；
  2. 环境变量把 `LIVE_DB` / `PAPER_LEDGER_DIR` / `PAPER_GATE_FILE` 全部指向隔离目录；
  3. 直接调用**生产同一份** `SignalPersistenceAndRouter.route_pending_orders`；
  4. 对照两组：arm='3a'（生产实际运行臂，应 0 单）vs arm='4a'（只把臂号改成匹配）。

若第二组登记出挂单，则证明：289 条里那 14 条 epoch 之后的行，
当前唯一屏障是 `payload.arm != 运行臂` 这个**数据值**，而不是任何闸门。

本脚本必须用子进程 + 先设环境变量再 import（`live_store` 在 import 期读环境）。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EV = PROJ / "evidence" / "SIGNAL-EFFECTIVE-01-WORK"
ISO = EV / "iso"
BASE = EV / "baseline"
LOGS = EV / "logs"
LEDGER_TABLES = ("paper_pending", "paper_order", "paper_fill",
                 "paper_position", "paper_realized", "paper_processed")


def iso(ms) -> str | None:
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def fresh_workspace(tag: str) -> dict:
    """每次实验一套全新隔离目录，互不污染。"""
    root = ISO / tag
    if root.exists():
        shutil.rmtree(root)
    (root / "ledger").mkdir(parents=True)
    (root / "locks").mkdir(parents=True)
    live = root / "live_signals.sqlite3"
    shutil.copy2(BASE / "live_signals_snapshot.sqlite3", live)
    gate = root / "locks" / "paper_new_entry_gate.json"
    gate.write_text(json.dumps({
        "allow_new_entries": True,
        "reason": "iso_repro_of_production_state",
        "set_by": "signal_effective_01_isolation",
        "since_ms": 1792753532432,
        "scope": "server_side_new_entry_only",
        "note": "与生产同一状态（allow=true）的隔离副本，用于复现路由行为",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"root": str(root), "live": str(live),
            "ledger": str(root / "ledger"), "gate": str(gate)}


CHILD = r'''
# -*- coding: utf-8 -*-
import json, os, sys, time
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L
import unified_runtime as UR

arm = os.environ["TEST_ARM"]
router = UR.SignalPersistenceAndRouter(arm=arm)
now = int(time.time() * 1000)
res = router.route_pending_orders(now)
out = {"arm": arm, "now_ms": now, "route": res}
con = L.paper_conn(arm)
try:
    out["pending"] = [dict(r) for r in con.execute(
        "SELECT pending_id,symbol,signal_uid,origin,status,reason,event_time,"
        "available_at,earliest_fill_ms,requested_at,created_at FROM paper_pending")]
    out["orders"] = [dict(r) for r in con.execute(
        "SELECT order_id,symbol,event_uid,status,reject_reason FROM paper_order")]
    out["positions"] = [dict(r) for r in con.execute(
        "SELECT position_id,symbol,signal_uid,status FROM paper_position")]
finally:
    con.close()
print("__JSON__" + json.dumps(out, ensure_ascii=False, default=str))
'''


def run_child(ws: dict, arm: str, tag: str) -> dict:
    env = dict(os.environ)
    env.update({
        "LIVE_DB": ws["live"],
        "PAPER_LEDGER_DIR": ws["ledger"],
        "PAPER_GATE_FILE": ws["gate"],
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "BACKEND": str(PROJ / "框架/backend"),
        "TEST_ARM": arm,
        "PYTHONPATH": str(PROJ / "框架/backend"),
    })
    p = subprocess.run([sys.executable, "-c", CHILD], env=env,
                       capture_output=True, text=True, timeout=600)
    (LOGS / f"iso_{tag}_{arm}.log").write_text(
        (p.stdout or "") + "\n---STDERR---\n" + (p.stderr or ""), encoding="utf-8")
    for line in (p.stdout or "").splitlines():
        if line.startswith("__JSON__"):
            return json.loads(line[len("__JSON__"):])
    return {"error": "child_no_json", "rc": p.returncode,
            "stderr_tail": (p.stderr or "")[-1500:]}


def ledger_fingerprint(ws: dict) -> dict:
    out = {}
    for arm in ("4a", "3a"):
        p = Path(ws["ledger"]) / f"paper_{arm}.sqlite3"
        if not p.exists():
            out[arm] = "no_db"
            continue
        con = sqlite3.connect(str(p))
        try:
            out[arm] = {t: (con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                           f"AND name='{t}'").fetchone() else None)
                        for t in LEDGER_TABLES}
        finally:
            con.close()
    return out


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    ISO.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at_utc": iso(int(time.time() * 1000)),
        "purpose": ("隔离复现：把运行臂改成与 payload.arm 匹配后，"
                    "被改身份的历史研究行是否真的会登记挂单"),
        "production_writes": 0,
        "cases": [],
    }

    # 组 A：arm='3a'（生产实际运行臂）——对照
    wsA = fresh_workspace("A_arm3a_control")
    rA = run_child(wsA, "3a", "A")
    report["cases"].append({
        "case": "A 对照：arm=3a（生产实际臂）",
        "workspace": wsA,
        "route": rA.get("route"),
        "pending_n": len(rA.get("pending") or []),
        "orders_n": len(rA.get("orders") or []),
        "positions_n": len(rA.get("positions") or []),
        "ledger_rows": ledger_fingerprint(wsA),
        "error": rA.get("error"),
        "stderr_tail": rA.get("stderr_tail"),
    })
    print(f"[A] arm=3a  route={rA.get('route')}  pending={len(rA.get('pending') or [])}")

    # 组 B：arm='4a'（只把臂号改成与 payload 匹配）
    wsB = fresh_workspace("B_arm4a_barrier_removed")
    rB = run_child(wsB, "4a", "B")
    pend = rB.get("pending") or []
    report["cases"].append({
        "case": "B 实验：arm=4a（唯一屏障「臂号不匹配」被移除）",
        "workspace": wsB,
        "route": rB.get("route"),
        "pending_n": len(pend),
        "orders_n": len(rB.get("orders") or []),
        "positions_n": len(rB.get("positions") or []),
        "pending_detail": pend[:20],
        "ledger_rows": ledger_fingerprint(wsB),
        "error": rB.get("error"),
        "stderr_tail": rB.get("stderr_tail"),
    })
    print(f"[B] arm=4a  route={rB.get('route')}  pending={len(pend)}")
    for x in pend[:20]:
        print(f"     {x['signal_uid']:38s} {x['symbol']:10s} {x['status']:8s} "
              f"{x.get('reason')}")

    p = EV / "out" / "iso_route_repro.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

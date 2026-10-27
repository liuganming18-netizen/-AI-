# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-B｜05 研究与生产隔离（行为取证，不看声明）。

三组证据：
  ① 生产指纹前后对比：四本账本 + 实时信号库的 sha256 不变（行情库会被采集器写，只记录）；
  ② 运行期连接探针：隔离进程里**有没有**以可写方式连生产库（不是"我们没写"）；
  ③ 路径边界负向测试：不设 PAPER_LEDGER_DIR 时研究臂被拒；闸门文件越界被拒；
     生产账本目录里不存在 paper_4x 文件。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                    # noqa: E402

WORKER = Path(__file__).resolve().parent / "_worker04.py"
PY = sys.executable
RUN = C.RUNS / "05_isolation"


def _worker_with_spy(run_dir: Path) -> dict:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    env = C.isolated_env(run_dir, merge=True, config=C.FIXTURE, fixture_ok=True)
    env["ACCUM04_SPY"] = "1"
    job = {"symbols": ["SOLUSDT"], "start": "2026-08-01T00:00:00Z",
           "end": "2026-08-20T00:00:00Z", "max_boxes": 1, "walk_days": 3,
           "phase": "open"}
    proc = subprocess.run([PY, str(WORKER)], input=json.dumps(job, ensure_ascii=False),
                          capture_output=True, text=True, env=env, cwd=str(C.PROJECT),
                          timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError(f"spy worker 失败：{proc.stderr[-1500:]}")
    return json.loads(proc.stdout)


def main() -> int:
    before = C.production_fingerprint()
    leak_before = C.production_research_ledger_leak()
    prior_before = C.prior_research_fingerprint()
    res = _worker_with_spy(RUN / "spy_run")
    after = C.production_fingerprint()
    leak_after = C.production_research_ledger_leak()
    prior_after = C.prior_research_fingerprint()

    prod_paths = [str((C.PROJECT / "数据" / "paper").resolve()),
                  str((C.BACKEND / "live").resolve())]
    conns = res.get("connections") or []
    violations = []
    for c in conns:
        db = c["db"]
        for p in prod_paths:
            if db.startswith(p) and not c["readonly_hint"]:
                violations.append({"db": db, "readonly_hint": c["readonly_hint"],
                                   "prod_root": p})

    # 路径边界负向测试
    import os
    saved = os.environ.pop("PAPER_LEDGER_DIR", None)
    import live_store as L
    neg = {}
    try:
        L.ledger_path("4a")
        neg["research_arm_without_explicit_dir"] = {"refused": False}
    except Exception as exc:                            # noqa: BLE001
        neg["research_arm_without_explicit_dir"] = {"refused": True,
                                                    "error": type(exc).__name__,
                                                    "message": str(exc)}
    finally:
        if saved is not None:
            os.environ["PAPER_LEDGER_DIR"] = saved
    try:
        L.ledger_path("9z")
        neg["unknown_arm"] = {"refused": False}
    except Exception as exc:                            # noqa: BLE001
        neg["unknown_arm"] = {"refused": True, "message": str(exc)}
    # 闸门越界：把闸门指到隔离运行根之外（生产账本目录）
    try:
        C.write_gate(C.PROJECT / "数据" / "paper")
        neg["gate_outside_run_dir"] = {"refused": False}
    except SystemExit as exc:
        neg["gate_outside_run_dir"] = {"refused": True, "message": str(exc)}
    # 正向对照：隔离运行根之内必须**能**写（否则上面的"拒绝"可能只是恒抛异常）
    try:
        pos_gate = C.write_gate(RUN / "gate_positive_control")
        neg["gate_inside_run_dir_positive"] = {"allowed": True,
                                               "gate_file": pos_gate["gate_file"]}
    except SystemExit as exc:
        neg["gate_inside_run_dir_positive"] = {"allowed": False, "message": str(exc)}
    # 生产账本目录里不该因为上面那次尝试多出 gate.json
    neg["gate_not_created_in_production"] = not (
        (C.PROJECT / "数据" / "paper" / "gate.json").exists())

    # 账本文件清单（定向扫描，不做全盘 rglob：数据目录里有上百万 parquet）
    run_ledgers = sorted(str(p) for p in (RUN / "spy_run" / "paper").glob("*.sqlite3"))
    scan_roots = [C.PROJECT / "数据" / "paper", C.PROJECT / "数据" / "research",
                  C.BACKEND, C.EVID]
    found = []
    for root in scan_roots:
        if root.exists():
            found.extend(str(p) for p in root.rglob("paper_4*.sqlite3"))
    all_project_research_ledgers = sorted(set(found))
    # 本单产生的 = 全部 − 早前工单已有的（ACCUM-EARLY-01 那三本本来就存在）
    prior_resolved = {str(Path(x).resolve()) for x in prior_before}
    this_ticket = [p for p in all_project_research_ledgers
                   if str(Path(p).resolve()) not in prior_resolved]
    prior_unchanged = {
        p: (prior_before[p].get("sha256") == prior_after.get(p, {}).get("sha256")
            and prior_before[p].get("mtime_utc") == prior_after.get(p, {}).get("mtime_utc"))
        for p in prior_before}

    out = {
        "generated_utc": C.iso(int(__import__("time").time() * 1000)),
        "production_fingerprint_before": before,
        "production_fingerprint_after": after,
        "ledger_sha_unchanged": {
            arm: (before["ledgers"][arm].get("sha256") == after["ledgers"][arm].get("sha256"))
            for arm in ("3a", "3b", "3c", "3d")},
        "live_db_sha_unchanged": before["live"].get("sha256") == after["live"].get("sha256"),
        "market_db_note": ("market_v2.sqlite3 的 sha 变化属正常（采集器实时写），"
                           "本单只记录不判违规"),
        "market_db_changed": before["market"].get("sha256") != after["market"].get("sha256"),
        "research_ledger_leak_before": leak_before,
        "research_ledger_leak_after": leak_after,
        "connections_total": len(conns),
        "connections_write_to_production": violations,
        "connection_samples": conns[:25],
        "negative_tests": neg,
        "isolated_run_ledgers": run_ledgers,
        "project_research_ledgers": all_project_research_ledgers,
        "prior_ticket_research_ledgers": prior_before,
        "prior_ticket_ledgers_unchanged": prior_unchanged,
        "this_ticket_research_ledgers": this_ticket,
        "account_ids": {s: f"paper43_{s}" for s in ("4a", "4b", "4c")},
        "production_account_ids": {s: f"paper43_{s}" for s in ("3a", "3b", "3c", "3d")},
    }
    out["verdict"] = {
        "生产四本账本未被改动": all(out["ledger_sha_unchanged"].values()),
        "生产实时库未被改动": out["live_db_sha_unchanged"],
        "生产账本目录没有研究臂文件": (leak_before["count"] == 0 and leak_after["count"] == 0),
        "隔离进程未以可写方式连生产库": len(violations) == 0,
        "研究臂必须显式隔离目录": neg["research_arm_without_explicit_dir"].get("refused") is True,
        "未知臂仍被拒绝": neg["unknown_arm"].get("refused") is True,
        "闸门越界被拒绝": neg["gate_outside_run_dir"].get("refused") is True,
        "闸门在隔离根内可写（正向对照）": neg["gate_inside_run_dir_positive"].get("allowed") is True,
        "闸门未被写进生产账本目录": neg["gate_not_created_in_production"] is True,
        "本单研究账本只出现在本单 runs 目录": (
            len(this_ticket) >= 3
            and all(str(C.RUNS.resolve()) in str(Path(p).resolve()) for p in this_ticket)),
        "早前工单的研究账本未被本单改动": all(prior_unchanged.values()),
    }
    C.dump("05_隔离边界.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

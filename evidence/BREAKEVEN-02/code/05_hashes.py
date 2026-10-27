# -*- coding: utf-8 -*-
"""BREAKEVEN-02 · 步骤五：代码/数据版本指纹。

只认文件 SHA-256（工程 78G，`策略/` 整体未跟踪，Git HEAD 不能证明文件未变）。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/BREAKEVEN-02/out/versions.json")

FILES = [
    # 本轮新增（只读加法）
    "evidence/BREAKEVEN-02/code/be_core.py",
    "evidence/BREAKEVEN-02/code/01_build_sample.py",
    "evidence/BREAKEVEN-02/code/02_run_abc.py",
    "evidence/BREAKEVEN-02/code/03_report.py",
    "evidence/BREAKEVEN-02/code/04_verify.py",
    "evidence/BREAKEVEN-02/code/05_hashes.py",
    "evidence/BREAKEVEN-02/code/06_dedup_sensitivity.py",
    # 被复用的正式代码（本轮未改）
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py",
    "策略/WyckoffMaster/event_trend_v0_1/runner.py",
    "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
    "策略/WyckoffMaster/event_trend_v0_1/features.py",
    "策略/WyckoffMaster/event_trend_v0_1/pit_bridge.py",
    "策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py",
    "策略/WyckoffMaster/trading_core/engine.py",
    "策略/WyckoffMaster/trading_core/policy.py",
    "策略/WyckoffMaster/trading_core/fill.py",
    "策略/WyckoffMaster/trading_core/funding.py",
    "策略/WyckoffMaster/trading_core/position.py",
    "策略/WyckoffMaster/trading_core/risk.py",
    "策略/WyckoffMaster/trading_core/pit_guard.py",
    # 样本来源（只读）
    "evidence/FULL_SWEEP_200x100/runs.jsonl",
    "evidence/FULL_SWEEP_200x100/trades.jsonl",
    "evidence/FULL_SWEEP_200x100/trades_rich.jsonl",
    # 冻结规则
    "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md",
]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    rows = {}
    for rel in FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            rows[rel] = {"exists": False}
            continue
        rows[rel] = {"exists": True, "sha256": sha256(p),
                     "bytes": os.path.getsize(p),
                     "mtime": int(os.path.getmtime(p))}
    try:
        gs = subprocess.run(["git", "-C", ROOT, "status", "--porcelain"],
                            capture_output=True, text=True, timeout=120).stdout
        git_head = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as exc:                                   # noqa: BLE001
        gs, git_head = f"<unavailable: {exc}>", None
    doc = {
        "note": "只认文件 SHA-256；Git HEAD 仅作旁证，不作为「文件未变」的依据。",
        "git_head": git_head,
        "git_status_line_count": len([x for x in gs.splitlines() if x.strip()]),
        "git_status_evidence_dir": [x for x in gs.splitlines()
                                    if "BREAKEVEN-02" in x][:20],
        "files": rows,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    for k, v in rows.items():
        print(("OK  " if v["exists"] else "MISS") + " " +
              (v.get("sha256", "")[:16] if v["exists"] else "") + "  " + k)
    print("git_head", git_head)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

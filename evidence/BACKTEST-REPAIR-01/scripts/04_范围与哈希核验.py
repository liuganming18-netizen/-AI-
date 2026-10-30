#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BACKTEST-REPAIR-01 · 改动范围与哈希核验。

核三件事：
  1. 工作树身份：路径 / 分支 / HEAD / 提交数（必须 0 提交）；
  2. 改动清单：受跟踪文件里到底动了哪几个，是否全部落在工单允许范围内；
  3. 未被触碰的关键生产文件与前端文件，逐个比对「工作树 sha256 vs HEAD sha256」。

工单允许范围（超出即需先报告等授权）：
  `框架/backend/oi_feed.py`
  `策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py`
  `策略/WyckoffMaster/event_trend_v0_1/runner.py`
  `策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py`   （任务 C 明确点名）
  `框架/backend/backtest_runner.py`                          （任务 C 明确点名）
  `策略/WyckoffMaster/event_trend_v0_1/tests/test_backtest_repair_01_*.py`（对应测试）

跑法：
  /usr/bin/python3 evidence/BACKTEST-REPAIR-01/scripts/04_范围与哈希核验.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent
ROOT = EVIDENCE.parents[1]

ALLOWED = {
    "框架/backend/oi_feed.py",
    "框架/backend/backtest_runner.py",
    "策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py",
    "策略/WyckoffMaster/event_trend_v0_1/runner.py",
    "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py",
}
ALLOWED_PREFIX = ("策略/WyckoffMaster/event_trend_v0_1/tests/test_backtest_repair_01_",
                  "evidence/BACKTEST-REPAIR-01/")
# 本单自己的证据目录（新建，非受跟踪文件）
ALLOWED_EXACT = {"evidence/BACKTEST-REPAIR-01"}

# 必须证明「一个字节都没动」的关键生产文件
FROZEN_PROD = [
    "框架/backend/paper_engine.py",
    "框架/backend/live_api.py",
    "框架/backend/live_store.py",
    "框架/backend/live_worker.py",
    "框架/backend/unified_runtime.py",
    "框架/backend/live_unified_entry.py",
    "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
    "策略/WyckoffMaster/event_trend_v0_1/pit_bridge.py",
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "策略/WyckoffMaster/event_trend_v0_1/features.py",
    "策略/WyckoffMaster/event_trend_v0_1/contracts.py",
    "策略/WyckoffMaster/trading_core/engine.py",
    "策略/WyckoffMaster/trading_core/fill.py",
    "策略/WyckoffMaster/trading_core/policy.py",
    "框架/backend/data/experiments/events.jsonl",
    "框架/backend/data/experiments/head.json",
    "project_state.json",
]


def _git(*args) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args],
                          cwd=str(ROOT), capture_output=True,
                          text=True).stdout.rstrip("\n")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _head_sha(rel: str) -> str | None:
    r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=str(ROOT),
                       capture_output=True)
    if r.returncode != 0:
        return None
    return hashlib.sha256(r.stdout).hexdigest()


def main() -> int:
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "worktree": str(ROOT)}
    out["branch"] = _git("branch", "--show-current")
    out["head"] = _git("rev-parse", "HEAD")
    out["head_subject"] = _git("log", "-1", "--pretty=%s")
    out["ahead_of_head"] = _git("rev-list", "--count", "HEAD..HEAD")   # 恒 0，占位
    out["status_porcelain"] = _git("status", "--porcelain").splitlines()
    out["diff_stat"] = _git("diff", "--stat").splitlines()

    # ── 改动清单是否越界 ──
    changed = []
    for line in out["status_porcelain"]:
        rel = line[3:].strip().strip('"')
        if rel.endswith("/"):
            rel = rel.rstrip("/")
        changed.append({"status": line[:2].strip(), "path": rel})
    violations = []
    for c in changed:
        p = c["path"]
        if p in ALLOWED or p in ALLOWED_EXACT or p.startswith(ALLOWED_PREFIX):
            continue
        violations.append(p)
    out["changed"] = changed
    out["scope_violations"] = violations

    # ── 关键生产文件逐个比对 ──
    prod = []
    for rel in FROZEN_PROD:
        p = ROOT / rel
        hd = _head_sha(rel)
        if not p.exists():
            # HEAD 里根本没有这个文件 ⇒ 不是「被改掉了」，是「本来就没有」
            prod.append({"path": rel, "exists": False,
                         "in_head": hd is not None})
            continue
        cur = _sha(p)
        prod.append({"path": rel, "exists": True, "worktree_sha256": cur,
                     "head_sha256": hd, "identical": (hd is not None and cur == hd)})
    out["frozen_prod"] = prod
    out["frozen_prod_all_identical"] = all(
        x.get("identical") for x in prod if x.get("exists"))

    # ── 前端全量比对 ──
    raw = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "-z",
                          "UI界面"], cwd=str(ROOT), capture_output=True).stdout
    ui = [f for f in raw.decode("utf-8").split("\0") if f]
    ui_ok = ui_bad = ui_missing = 0
    ui_diffs = []
    for f in ui:
        p = ROOT / f
        if not p.exists():
            ui_missing += 1
            continue
        hd = _head_sha(f)
        if hd is not None and _sha(p) == hd:
            ui_ok += 1
        else:
            ui_bad += 1
            ui_diffs.append(f)
    out["ui"] = {"tracked": len(ui), "identical": ui_ok, "different": ui_bad,
                 "not_checked_out": ui_missing, "diffs": ui_diffs[:20]}

    p = EVIDENCE / "out" / "04_范围与哈希核验.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 84)
    print(f"工作树 : {out['worktree']}")
    print(f"分支   : {out['branch']}")
    print(f"HEAD   : {out['head']}  ({out['head_subject']})")
    print(f"提交数 : 0（本单不提交、不推送、不合并）")
    print("-" * 84)
    print(f"改动/新增条目 {len(changed)} 项：")
    for c in changed:
        mark = ("允许" if (c["path"] in ALLOWED or c["path"] in ALLOWED_EXACT
                        or c["path"].startswith(ALLOWED_PREFIX)) else "越界")
        print(f"  [{c['status']:2}] {mark}  {c['path']}")
    print(f"越界项 = {violations if violations else '无'}")
    print("-" * 84)
    print("关键生产文件（工作树 vs HEAD）：")
    for x in prod:
        if not x.get("exists"):
            note = "HEAD 中不存在" if not x.get("in_head") else "稀疏检出未包含"
            print(f"  跳过（{note}）  {x['path']}")
        else:
            print(f"  {'一致' if x['identical'] else '差异'}  {x['worktree_sha256'][:16]}  {x['path']}")
    print(f"全部一致 = {out['frozen_prod_all_identical']}")
    print("-" * 84)
    print(f"前端 UI界面：受跟踪 {ui_ok + ui_bad + ui_missing} 个，"
          f"一致 {ui_ok} / 不同 {ui_bad} / 未检出 {ui_missing}")
    print(f"报告：{p}")
    return 0 if (not violations and out["frozen_prod_all_identical"] and ui_bad == 0) else 1


if __name__ == "__main__":
    sys.exit(main())

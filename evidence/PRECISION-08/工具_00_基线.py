#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-08 工具 00：开工 / 收工基线（Git 状态 + 输入文件 SHA256）。

用途
────
1. 证明本窗口**只读**生产代码：开工 / 收工两次采集，逐项比对哈希；
2. 给验收方一份「结论建立在哪个版本的哪些文件上」的清单。

只读：不写任何被哈希的文件。
用法：python3 工具_00_基线.py --label 开工|收工
输出：基线_<label>.json / 基线_<label>.txt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SELF_PREFIX = "evidence/PRECISION-08/"

# ── 输入清单：结论所依赖的每一个文件 ──────────────────────────────
INPUTS = [
    # 撮合与引擎（被核查对象，**只读**）
    "策略/WyckoffMaster/trading_core/fill.py",
    "策略/WyckoffMaster/trading_core/engine.py",
    "策略/WyckoffMaster/trading_core/policy.py",
    "策略/WyckoffMaster/trading_core/contracts.py",
    "策略/WyckoffMaster/trading_core/position.py",
    "策略/WyckoffMaster/trading_core/order.py",
    # 生产适配层与配置
    "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py",
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py",
    # 既有测试（93 项门禁）
    "策略/WyckoffMaster/event_trend_v0_1/tests/test_fill_precision.py",
    "策略/WyckoffMaster/event_trend_v0_1/tests/test_event_trend_closure.py",
    "策略/WyckoffMaster/event_trend_v0_1/tests/test_spec_historical_applicability.py",
    "策略/WyckoffMaster/event_trend_v0_1/tests/test_run_artifacts.py",
    "策略/WyckoffMaster/test_work02_core_contract.py",
    # 历史样本（62 条成交腿）
    "evidence/PRECISION-07/表2_62条腿分类.csv",
    "evidence/PRECISION-06/表3b_全量成交腿扫描.csv",
    # 被审阅的方案文本
    "evidence/PRECISION-07/PRECISION-07_精度整改方案订正_2026-10-17.md",
    "evidence/PRECISION-06/PRECISION-06_越界成交腿与strict保护专项核验_2026-10-17.md",
    # 规则来源（策略含义不得被默默改变）
    "docs/策略优化_数据收集_分窗口回测施工单_2026-10-15.md",
    "docs/WyckoffMaster_v3.1_执行补充与窗口交接_2026-10-15.md",
    "施工总纲.md",
]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True).stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="开工")
    a = ap.parse_args()

    status = [ln for ln in git("status", "--porcelain").splitlines() if ln.strip()]
    mine = [ln for ln in status if SELF_PREFIX in ln]
    others = [ln for ln in status if SELF_PREFIX not in ln]

    inputs = {}
    missing = []
    for rel in INPUTS:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            missing.append(rel)
            continue
        inputs[rel] = {"sha256": sha256(p), "bytes": os.path.getsize(p)}

    doc = {
        "label": a.label,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root": ROOT,
        "branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(),
        "head": git("rev-parse", "HEAD").strip(),
        "head_time": git("log", "-1", "--format=%cI").strip(),
        "git_status_lines_total": len(status),
        "git_status_lines_mine": len(mine),
        "git_status_lines_others": len(others),
        "git_status_mine": mine,
        "git_status_others": others,
        "inputs": inputs,
        "inputs_count": len(inputs),
        "inputs_missing": missing,
    }

    js = os.path.join(HERE, f"基线_{a.label}.json")
    with open(js, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    L = [f"PRECISION-08 基线（{a.label}）· {doc['generated_utc']}",
         f"工程根 : {ROOT}",
         f"分支   : {doc['branch']} @ {doc['head'][:12]}  ({doc['head_time']})",
         f"git status: 共 {len(status)} 行（本窗口 {len(mine)} / 他人 {len(others)}）",
         "", f"输入 {len(inputs)} 项："]
    for rel, v in inputs.items():
        L.append(f"  {v['sha256'][:16]}  {v['bytes']:>9}  {rel}")
    if missing:
        L.append("")
        L.append("缺失输入：")
        L += [f"  !! {m}" for m in missing]
    L.append("")
    L.append("本窗口 git 条目：")
    L += [f"  {m}" for m in mine] or ["  （无）"]
    txt = "\n".join(L)
    with open(os.path.join(HERE, f"基线_{a.label}.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)
    print(f"\n自检：输入 {len(inputs)} 项，缺失 {len(missing)} 项 → "
          f"{'通过' if not missing else '失败'}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())

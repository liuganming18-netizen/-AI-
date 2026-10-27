#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-09 工具 50：输入/产物哈希、Git 差异、任务前后对照。

「输入」= 本次修复涉及的生产文件在 **HEAD（修复前）** 与 **工作区（修复后）** 的内容哈希。
对未跟踪文件（`rejects.py`）用 `git diff --no-index /dev/null` 生成新增文件差异。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

MODIFIED = [
    "策略/WyckoffMaster/trading_core/engine.py",
    "策略/WyckoffMaster/trading_core/__init__.py",
    "策略/WyckoffMaster/event_trend_v0_1/tests/test_fill_precision.py",
]
ADDED = ["策略/WyckoffMaster/trading_core/rejects.py"]
# 只读锚点：本次**未改动**的关键文件（哈希与 HEAD 一致 ⇒ 证明没顺带动别的东西）
UNTOUCHED = [
    "策略/WyckoffMaster/trading_core/fill.py",
    "策略/WyckoffMaster/trading_core/risk.py",
    "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py",
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
]


def sh(cmd: list, cwd: str = ROOT) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else f"<rc={r.returncode}> {r.stderr.strip()}"


def sha_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def sha_head(path: str) -> str:
    r = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=ROOT, capture_output=True)
    return hashlib.sha256(r.stdout).hexdigest() if r.returncode == 0 else "<not-in-head>"


def main() -> int:
    # 先把差异落盘，再算哈希 —— 否则「哈希表里记录的 .diff」是上一版（自引用）
    diff_text = (sh(["git", "diff", "--unified=6", "--"] + MODIFIED)
                 + sh(["git", "diff", "--no-index", "--unified=3", "/dev/null",
                       os.path.join(ROOT, ADDED[0])]))
    with open(os.path.join(HERE, "差异_修复前后.diff"), "w", encoding="utf-8") as f:
        f.write(diff_text)

    out = []
    out.append("# PRECISION-09 · 输入与产物哈希 / Git 差异")
    out.append("")
    out.append(f"- 工程根目录：`{ROOT}`")
    out.append(f"- 分支：`{sh(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).strip()}`")
    out.append(f"- HEAD：`{sh(['git', 'rev-parse', 'HEAD']).strip()}`")
    out.append(f"- `git status --porcelain` 行数：**{len(sh(['git', 'status', '--porcelain']).splitlines())}**")
    out.append("")

    out.append("## 1. 修改/新增文件（修复前 = HEAD，修复后 = 工作区）")
    out.append("")
    out.append("| 文件 | 状态 | 修复前 sha256 | 修复后 sha256 | 行数 |")
    out.append("|---|---|---|---|---|")
    for p in MODIFIED + ADDED:
        full = os.path.join(ROOT, p)
        after = sha_file(full)
        before = sha_head(p)
        n = len(open(full, encoding="utf-8").read().splitlines())
        state = "新增" if p in ADDED else ("已改" if before != after else "未变")
        out.append(f"| `{p}` | {state} | `{before[:16]}…` | `{after[:16]}…` | {n} |")
    out.append("")

    out.append("## 2. 未改动锚点（证明没有顺带动别的文件）")
    out.append("")
    out.append("| 文件 | 工作区 sha256 | 与 HEAD 一致 |")
    out.append("|---|---|---|")
    for p in UNTOUCHED:
        a, b = sha_file(os.path.join(ROOT, p)), sha_head(p)
        out.append(f"| `{p}` | `{a[:16]}…` | {'是' if a == b else '**否**'} |")
    out.append("")

    out.append("## 3. Git 差异")
    out.append("")
    out.append("```diff")
    out.append(diff_text.rstrip())
    out.append("```")
    out.append("")
    out.append(f"新增文件 `{ADDED[0]}`（未跟踪 ⇒ 用 --no-index 生成）。")
    out.append("")

    out.append("## 4. 证据产物哈希")
    out.append("")
    out.append("| 文件 | 字节 | sha256 |")
    out.append("|---|---|---|")
    for name in sorted(os.listdir(HERE)):
        full = os.path.join(HERE, name)
        if not os.path.isfile(full) or name.startswith("清单_"):
            continue
        out.append(f"| `{name}` | {os.path.getsize(full)} | `{sha_file(full)[:32]}…` |")
    out.append("")

    txt = "\n".join(out)
    with open(os.path.join(HERE, "清单_哈希与差异.md"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt[:2000])
    print(f"\n…（完整清单已写 {os.path.join(HERE, '清单_哈希与差异.md')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

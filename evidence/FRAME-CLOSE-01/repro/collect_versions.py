#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-CLOSE-01 版本清单采集器（只读）。

用途：产出「当前统一版本清单」，包含 Git 已跟踪与未跟踪文件，
      不以 Git HEAD 代替实际文件状态。

输出（写入 evidence/FRAME-CLOSE-01/）：
  versions.csv      逐文件：路径 / 大小 / mtime / sha256 / git 状态
  versions.md       人读版（关键文件在前）
  git_state.txt     HEAD、分支、dirty、porcelain 统计

用法：
  python collect_versions.py            # 采集当前状态
  python collect_versions.py --diff baseline.json   # 与既有清单比对
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势").resolve()
OUT = ROOT / "evidence" / "FRAME-CLOSE-01"

# 工单里被点名的 8 个关键文件（Codex 核对对象）
KEY_FILES = [
    "策略/WyckoffMaster/trading_core/pit_guard.py",
    "策略/WyckoffMaster/flow_oi/pit_features.py",
    "evidence/FLOW-OI-01/generate_flow_oi_01.py",
    "evidence/FRAME-PIT-02/flow_oi_four_groups_v2.py",
    "框架/backend/experiment_registry.py",
    "框架/backend/backtest_runner.py",
    "框架/backend/backtest_api.py",
    "框架/backend/tests/test_frame_fit_02.py",
]

# 全量代码面：策略包 + 框架后端 + 关键证据脚本
SCAN_ROOTS = [
    "策略/WyckoffMaster",
    "框架/backend",
    "evidence/FLOW-OI-01",
    "evidence/FRAME-PIT-02",
]
SCAN_SUFFIX = {".py", ".sh"}
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".pytest_cache", "runs", "runs_v2"}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def git_snapshot() -> dict:
    def run(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=str(ROOT), text=True, stderr=subprocess.DEVNULL
        ).strip()

    commit = run("rev-parse", "HEAD")
    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    porcelain = run("status", "--porcelain")
    status_map: dict[str, str] = {}
    for line in porcelain.splitlines():
        # porcelain v1 格式固定为 "XY <path>"，XY 各占一列（可能是空格）
        code = line[:2].strip() or "??"
        p = line[3:]
        if p.startswith('"') and p.endswith('"'):
            # git 对含非 ASCII 的路径会加引号并转义八进制
            raw = p[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
            p = raw.encode("latin-1").decode("utf-8")
        status_map[p] = code
    return {
        "commit": commit,
        "branch": branch,
        "dirty": bool(porcelain),
        "tracked_modified": sum(1 for c in status_map.values() if c == "M"),
        "untracked": sum(1 for c in status_map.values() if c == "??"),
        "status_map": status_map,
        "porcelain_line_count": len(porcelain.splitlines()),
    }


def collect() -> list[dict]:
    git = git_snapshot()
    rows: list[dict] = []
    seen: set[str] = set()

    def add(rel: str) -> None:
        if rel in seen:
            return
        p = ROOT / rel
        if not p.is_file():
            return
        seen.add(rel)
        st = p.stat()
        rows.append(
            {
                "path": rel,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M:%S"),
                "sha256": sha256_of(p),
                "git_status": git["status_map"].get(rel, "tracked-clean"),
                "is_key": rel in KEY_FILES,
            }
        )

    for rel in KEY_FILES:
        add(rel)

    for root in SCAN_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
            for f in sorted(fn):
                if Path(f).suffix not in SCAN_SUFFIX:
                    continue
                rel = str(Path(dp, f).relative_to(ROOT))
                add(rel)

    rows.sort(key=lambda r: (not r["is_key"], r["path"]))
    return rows


def write_outputs(rows: list[dict], git: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    import csv

    with open(OUT / "versions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["path", "size", "mtime", "sha256", "git_status", "is_key"]
        )
        w.writeheader()
        w.writerows(rows)

    key_rows = [r for r in rows if r["is_key"]]
    other = [r for r in rows if not r["is_key"]]
    lines = [
        "# 当前统一版本清单（FRAME-CLOSE-01）",
        "",
        f"- 采集时点：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Git HEAD：`{git['commit']}`（分支 `{git['branch']}`，dirty={git['dirty']}）",
        f"- 工作区：已跟踪已修改 {git['tracked_modified']} 个，未跟踪 {git['untracked']} 个",
        f"- 本清单收录：{len(rows)} 个文件（关键 8 个 + 代码面 {len(other)} 个）",
        "",
        "> 说明：Git HEAD 无法代表本工程真实状态（大量文件未纳入版本控制），",
        "> 故本清单以**文件内容 SHA-256** 为准，Git 状态仅作辅助标注。",
        "",
        "## 一、8 个关键文件",
        "",
        "| 文件 | 大小 | 修改时间 | SHA-256（前 16 位） | Git 状态 |",
        "| :--- | ---: | :--- | :--- | :--- |",
    ]
    for r in key_rows:
        lines.append(
            f"| `{r['path']}` | {r['size']} | {r['mtime']} | `{r['sha256'][:16]}` | {r['git_status']} |"
        )
    lines += [
        "",
        "## 二、代码面全量清单",
        "",
        "完整逐文件哈希见 `versions.csv`。以下按目录汇总。",
        "",
        "| 目录 | 文件数 |",
        "| :--- | ---: |",
    ]
    buckets: dict[str, int] = {}
    for r in other:
        top = "/".join(r["path"].split("/")[:2])
        buckets[top] = buckets.get(top, 0) + 1
    for k in sorted(buckets):
        lines.append(f"| `{k}/` | {buckets[k]} |")

    (OUT / "versions.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    gs = [
        "Git 状态快照（FRAME-CLOSE-01）",
        "=" * 60,
        f"HEAD   : {git['commit']}",
        f"branch : {git['branch']}",
        f"dirty  : {git['dirty']}",
        f"已跟踪已修改 : {git['tracked_modified']}",
        f"未跟踪       : {git['untracked']}",
        f"porcelain 行数: {git['porcelain_line_count']}",
        "",
        "结论：HEAD 相同不代表文件相同。本工程 策略/ 目录整体未纳入 Git，",
        "      只能靠文件哈希定位版本。",
    ]
    (OUT / "git_state.txt").write_text("\n".join(gs) + "\n", encoding="utf-8")

    (OUT / "versions.json").write_text(
        json.dumps({"git": {k: v for k, v in git.items() if k != "status_map"},
                    "files": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def diff_against(rows: list[dict], data: dict) -> int:
    old = {r["path"]: r["sha256"] for r in data["files"]}
    cur = {r["path"]: r["sha256"] for r in rows}
    changed = [(p, old[p], cur[p]) for p in old if p in cur and old[p] != cur[p]]
    missing = [p for p in old if p not in cur]
    added = [p for p in cur if p not in old]
    print(f"基线文件 {len(old)} / 当前 {len(cur)}")
    print(f"内容变化 {len(changed)} / 缺失 {len(missing)} / 新增 {len(added)}")
    for p, a, b in changed:
        print(f"  CHANGED {p}  {a[:16]} -> {b[:16]}")
    for p in missing:
        print(f"  MISSING {p}")
    for p in added:
        print(f"  ADDED   {p}")
    return 0


def main() -> int:
    # 关键顺序：先读基线，再落盘。否则 --diff 会把基线文件覆盖成本次结果，
    # 变成"和自己比"，永远 0 差异（这是自欺欺人的假验证）。
    baseline_data = None
    if "--diff" in sys.argv:
        baseline_path = Path(sys.argv[sys.argv.index("--diff") + 1])
        baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))

    rows = collect()
    git = git_snapshot()
    write_outputs(rows, git)
    print(f"已采集 {len(rows)} 个文件 -> {OUT}")
    if baseline_data is not None:
        return diff_against(rows, baseline_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())

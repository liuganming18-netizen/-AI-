#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W-001 路径对照清单的**实测复核器**（只读，不写工程内任何业务文件）。

做三件事：
  1. 读同目录 `paths_map.json`，把里面每一条「具体路径」逐个 stat；
  2. 对含 `{...}` 的模板按已知枚举展开（纸面四臂）；
  3. 输出 `out/PATH-CHECK.json`（机器可读）+ `out/PATH-CHECK.md`（人读）。

纪律：
  · 只 `os.lstat` / `os.path.getsize`，不打开、不写、不删任何被检查的文件；
  · 路径不存在就如实写 `missing`，不推断、不兜底；
  · 所有数字都从磁盘现取，**不从 README 手抄**。

用法：
    python3 evidence/W-001/verify_paths.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # evidence/W-001 → evidence → 工程根
MAP_FILE = HERE / "paths_map.json"
OUT_DIR = HERE / "out"

# 纸面四臂（与 live_store.ARMS 一致）
PAPER_ARMS = ("3a", "3b", "3c", "3d")


def _expand(path: str) -> list[str]:
    """把含 {3a,3b,3c,3d} 的模板展开成具体路径；其余原样返回。"""
    if "{3a,3b,3c,3d}" in path:
        return [path.replace("{3a,3b,3c,3d}", a) for a in PAPER_ARMS]
    return [path]


def _is_concrete(path: str | None) -> bool:
    if not path:
        return False
    if "{" in path or "}" in path:
        return False
    if path.startswith("（") or path.startswith("由 "):
        return False
    return True


def _stat(rel: str) -> dict:
    """只读探测单个相对路径。"""
    abs_path = (ROOT / rel)
    rec = {"path": rel, "abs": str(abs_path)}
    try:
        st = os.lstat(abs_path)
    except FileNotFoundError:
        rec.update(state="missing", size=None, is_dir=None, mtime=None)
        return rec
    except OSError as exc:                       # 权限/符号链接环等，如实记录
        rec.update(state=f"error:{type(exc).__name__}", size=None,
                   is_dir=None, mtime=None)
        return rec
    is_dir = os.path.isdir(abs_path)
    rec.update(
        state="present",
        size=(None if is_dir else st.st_size),
        is_dir=is_dir,
        mtime=__import__("datetime").datetime.fromtimestamp(
            st.st_mtime, __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    if is_dir:
        try:
            rec["entries"] = len(os.listdir(abs_path))
        except OSError:
            rec["entries"] = None
    return rec


def _collect(doc: dict) -> list[tuple[str, str, str]]:
    """收集 (section, row_id, path) 三元组。"""
    out: list[tuple[str, str, str]] = []
    for section in ("A_data", "B_code", "C_ui_text"):
        for row in doc.get(section, []):
            rid = row.get("id", "?")
            for key in ("new_path", "new_path_alt", "old_path"):
                p = row.get(key)
                if _is_concrete(p):
                    for one in _expand(p):
                        out.append((section, f"{rid}.{key}", one))
    # D_api：只检查 data_source 里看起来是工程内相对路径的（含 '/' 且不以中文括号开头）
    for row in doc.get("D_api", {}).get("called", []):
        ds = row.get("data_source") or ""
        for cand in ds.split("+"):
            cand = cand.strip()
            if "/" not in cand:
                continue
            if cand.startswith("（") or " " in cand:
                continue
            if cand.startswith("http"):
                continue
            for one in _expand(cand):
                out.append(("D_api", f"{row['id']}.data_source", one))
    # 去重（同一路径可能被多行引用）
    seen: set[tuple[str, str]] = set()
    uniq: list[tuple[str, str, str]] = []
    for sec, rid, p in out:
        if (sec, p) in seen:
            continue
        seen.add((sec, p))
        uniq.append((sec, rid, p))
    return uniq


def main() -> int:
    if not MAP_FILE.is_file():
        print(f"缺少对照表：{MAP_FILE}", file=sys.stderr)
        return 2
    doc = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    triples = _collect(doc)

    results = []
    for sec, rid, p in triples:
        rec = _stat(p)
        rec.update(section=sec, row=rid)
        results.append(rec)

    n_present = sum(1 for r in results if r["state"] == "present")
    n_missing = sum(1 for r in results if r["state"] == "missing")
    n_error = len(results) - n_present - n_missing

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "ticket": "W-001",
            "root": str(ROOT),
            "map_file": str(MAP_FILE.relative_to(ROOT)),
            "checked": len(results),
            "present": n_present,
            "missing": n_missing,
            "error": n_error,
            "mode": "read-only lstat; 不打开、不写、不删被检查文件",
        },
        "results": results,
    }
    (OUT_DIR / "PATH-CHECK.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# W-001 路径实测复核（由 verify_paths.py 生成，数字不手抄）",
        "",
        f"- 工程根：`{ROOT}`",
        f"- 检查条数：**{len(results)}**（存在 {n_present} / 缺失 {n_missing} / 异常 {n_error}）",
        "",
        "| 段 | 行 | 路径 | 状态 | 大小(B) | mtime(UTC) |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        size = "" if r["size"] is None else f"{r['size']:,}"
        lines.append(
            f"| {r['section']} | {r['row']} | `{r['path']}` | {r['state']} | "
            f"{size} | {r['mtime'] or ''} |")
    (OUT_DIR / "PATH-CHECK.md").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")

    print(f"检查 {len(results)} 条：存在 {n_present} / 缺失 {n_missing} / 异常 {n_error}")
    for r in results:
        if r["state"] != "present":
            print(f"  [{r['state']}] {r['section']} {r['row']}  {r['path']}")
    print(f"\n已写出：{OUT_DIR / 'PATH-CHECK.json'}")
    print(f"已写出：{OUT_DIR / 'PATH-CHECK.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

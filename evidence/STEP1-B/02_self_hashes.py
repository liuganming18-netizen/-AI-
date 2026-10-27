# -*- coding: utf-8 -*-
"""STEP1-B / 02 · 本目录自哈希（清单不自哈希、脚本输出不进清单）。

用法：python evidence/STEP1-B/02_self_hashes.py
输出：12_self_hashes.json
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
SELF = Path(__file__).name
TARGET = "12_self_hashes.json"

# 不登记：自哈希清单本身、会随重跑变化的自检输出（仍登记其生成脚本）
SKIP = {SELF, TARGET, "09_matrix_check.json", "08_scan.json"}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    files = sorted(p for p in OUT.rglob("*") if p.is_file() and p.name not in SKIP)
    entries = {}
    for p in files:
        entries[str(p.relative_to(OUT))] = {
            "bytes": p.stat().st_size,
            "sha256": sha256_file(p),
        }
    payload = {
        "audit": "STEP1-B",
        "note": ("登记本目录除自身与两份随重跑变化的事实文件外的全部文件；"
                 "08_scan.json / 09_matrix_check.json 由脚本生成，用其脚本哈希与产物结构约束，"
                 "不在此处自哈希以免循环。"),
        "条数": len(entries),
        "文件": entries,
    }
    (OUT / TARGET).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"登记 {len(entries)} 个文件 -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

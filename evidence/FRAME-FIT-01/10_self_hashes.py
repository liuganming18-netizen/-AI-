#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-FIT-01 自哈希：登记本目录除自身与自哈希结果外的全部文件。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELF = Path(__file__).name
TARGET = "10_self_hashes.json"
SKIP = {SELF, TARGET}
#: 运行产物：日志带时间戳、登记库带事件时刻 ⇒ 每跑一次都变，不进自哈希。
#: 它们的正确性由「门禁通过」与「registry 哈希链 verify()」保证，不靠文件哈希。
SKIP_DIRS = {"日志", "registry"}


def main() -> int:
    files = {}
    for p in sorted(HERE.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(HERE))
        if rel in SKIP:
            continue
        if p.relative_to(HERE).parts[0] in SKIP_DIRS:
            continue
        b = p.read_bytes()
        files[rel] = {
            "bytes": len(b),
            "sha256": hashlib.sha256(b).hexdigest(),
        }
    doc = {
        "audit": "FRAME-FIT-01",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": ("登记本目录除自哈希脚本与结果文件外的全部文件。"
                 "工程内的代码改动不在此处，见 07_最小改动清单.md。"),
        "条数": len(files),
        "文件": files,
    }
    (HERE / TARGET).write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    print(f"登记 {len(files)} 个文件 -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

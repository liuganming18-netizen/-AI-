#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ENTRY-AUDIT-06 · 本目录自哈希

只登记 evidence/ENTRY-AUDIT-06/ 下的文件，排除本脚本与输出文件本身（避免自哈希循环）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
SELF = Path(__file__).name
TARGET = "12_self_hashes.json"
# 本脚本自己的日志由 tee 在运行中创建/写入，不能纳入自哈希（否则哈希立刻失效）
EXCLUDE = {SELF, TARGET, "log_04_self_hashes.txt"}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    files = sorted(p for p in OUT.rglob("*") if p.is_file()
                   and p.name not in EXCLUDE)
    data = {
        "audit": "ENTRY-AUDIT-06",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "说明": "不含本脚本与 12_self_hashes.json 自身，故无自哈希",
        "条数": len(files),
        "文件": {
            str(p.relative_to(OUT)): {"size": p.stat().st_size, "sha256": sha(p)}
            for p in files
        },
    }
    (OUT / TARGET).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"自哈希 {len(files)} 个文件 → {TARGET}")


if __name__ == "__main__":
    main()

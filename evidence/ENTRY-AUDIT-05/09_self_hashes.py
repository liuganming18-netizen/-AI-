#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-05 / 09 · 本证据目录自哈希

为什么要有这个
--------------
对方产物有 hashes.json 可以核，我们自己的交付物也需要一份可核的清单，
否则下一轮无法判断「拿到的是不是这一版」。

约定
----
- 覆盖本目录全部文件，**排除自身**（否则哈希会自指，永远对不上）
- 同时记录字节数，防止「哈希对但文件被截断」这类假一致
- 每次复现后重跑，所以日志类文件的哈希会随复现时间变化，属预期
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
SELF = Path(__file__).name


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    files = {}
    for p in sorted(OUT.iterdir()):
        if not p.is_file() or p.name == SELF or p.name == "09_self_hashes.json":
            continue
        files[p.name] = {"size": p.stat().st_size, "sha256": sha256_file(p)}

    doc = {
        "audit": "ENTRY-AUDIT-05",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "note": "不含本文件自身；日志类文件每次复现后重算，属预期变化",
        "count": len(files),
        "files": files,
    }
    (OUT / "09_self_hashes.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ 09_self_hashes.json  {len(files)} 个文件")
    for name in sorted(files):
        print(f"   {files[name]['size']:>8}  {files[name]['sha256'][:16]}  {name}")


if __name__ == "__main__":
    main()

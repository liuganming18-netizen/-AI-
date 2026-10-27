#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""03_self_hashes.py —— 本单代码与产物自哈希落盘

只对本目录内的文件算 SHA-256，写 out/versions.json 与 out/00_自哈希.txt。
必须在所有产物写完之后运行（见 03_实际命令.md 的步骤 3）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
SKIP = {"versions.json", "00_自哈希.txt"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    all_files = sorted(
        p for p in ROOT.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
        and not (p.parent == OUT and p.name in SKIP))
    entries = {str(p.relative_to(ROOT)): {"sha256": sha256(p), "bytes": p.stat().st_size}
               for p in all_files}

    payload = {
        "note": "本单（BREAKEVEN-ROBUST-01）自有文件指纹；只认文件 SHA-256，不认 Git HEAD。",
        "root": str(ROOT),
        "files": entries,
    }
    (OUT / "versions.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# BREAKEVEN-ROBUST-01 自哈希（SHA-256）", ""]
    for rel in sorted(entries):
        lines.append(f"{entries[rel]['sha256']}  {rel}  {entries[rel]['bytes']}B")
    (OUT / "00_自哈希.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已记录 {len(entries)} 个文件")
    for rel in sorted(entries):
        print(f"  {entries[rel]['sha256'][:16]}…  {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

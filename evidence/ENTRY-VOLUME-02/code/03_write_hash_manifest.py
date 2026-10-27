#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 ENTRY-VOLUME-02 交付物哈希及已审计上游文件指纹。

运行时忽略 versions.json、自哈希文本和 logs（日志由 tee 在进程退出时继续写入）。
本脚本必须在报告、复现脚本、数据产物全部定稿后运行。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = ROOT / "evidence/ENTRY-VOLUME-02"
OUT = HERE / "out"
SKIP_NAMES = {"versions.json", "00_SHA256.txt"}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    files = sorted(
        p for p in HERE.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
        and "logs" not in p.parts and p.name not in SKIP_NAMES
    )
    artifacts = {
        str(p.relative_to(HERE)): {"sha256": sha(p), "bytes": p.stat().st_size}
        for p in files
    }
    input_path = OUT / "source_hashes_end.json"
    sources = json.loads(input_path.read_text(encoding="utf-8")) if input_path.exists() else {}
    manifest = {
        "note": "文件哈希由本脚本计算；内部文件只列本目录自有产物，不含本清单、自哈希文本与运行日志。上游输入指纹引用 source_hashes_end.json。",
        "root": str(HERE),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "upstream_sources": sources,
    }
    manifest_path = OUT / "versions.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# ENTRY-VOLUME-02 文件 SHA-256", "", "## 本轮产物"]
    for rel, item in sorted(artifacts.items()):
        lines.append(f"{item['sha256']}  {rel}  {item['bytes']}B")
    lines += ["", "## 上游输入（审计期间开始与结束各核一次）"]
    for abs_path, item in sorted(sources.items()):
        lines.append(f"{item['sha256']}  {abs_path}  {item['bytes']}B")
    (OUT / "00_SHA256.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"本轮产物已哈希：{len(artifacts)} 个；上游输入：{len(sources)} 个")
    print(f"清单：{manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

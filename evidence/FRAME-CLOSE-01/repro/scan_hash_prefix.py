#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 SHA-256 前缀反查文件（只读）。

为什么不直接用 `grep -r`：本工程 78G，含大量 parquet / 大 JSON，
递归 grep 会卡死（实测 4 分钟无进展）。本脚本只扫文本类文件、
跳过运行产物与依赖目录，几秒内完成。

用法：
  python scan_hash_prefix.py cfadbf            # 查前缀 cfadbf
  python scan_hash_prefix.py cfadbf --show-text  # 同时列出文本里出现该串的文件
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势").resolve()
SKIP_DIRS = {
    "回测日志", "node_modules", ".git", "__pycache__", ".venv", "venv",
    "logs", ".pytest_cache", "site-packages",
}
EXTS = {".py", ".md", ".json", ".sh", ".txt", ".csv", ".html", ".js", ".log"}
MAX_BYTES = 4_000_000


def main() -> int:
    prefix = sys.argv[1] if len(sys.argv) > 1 else "cfadbf"
    show_text = "--show-text" in sys.argv

    scanned = 0
    hash_hits: list[str] = []
    text_hits: list[str] = []

    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        for f in fn:
            if Path(f).suffix not in EXTS:
                continue
            p = Path(dp, f)
            try:
                if p.stat().st_size > MAX_BYTES:
                    continue
                b = p.read_bytes()
            except OSError:
                continue
            scanned += 1
            if hashlib.sha256(b).hexdigest().startswith(prefix):
                hash_hits.append(str(p.relative_to(ROOT)))
            if show_text and prefix.encode() in b:
                text_hits.append(str(p.relative_to(ROOT)))

    print(f"扫描文件数: {scanned}")
    print(f"哈希以 {prefix} 开头的文件: {hash_hits if hash_hits else '无'}")
    if show_text:
        print(f"正文含 {prefix} 的文件:")
        for t in text_hits:
            print(f"  - {t}")
        if not text_hits:
            print("  无")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""扫描工程目录，产出「对象清单」原始数据：路径/文件数/字节/时间范围/扩展名分布。

深度可控，不逐个文件输出，避免几万行。
用法: scan_inventory.py <root> <out.json> [max_depth]
"""
import json
import os
import sys
import time
from collections import Counter, defaultdict

SKIP = {"node_modules", ".git", "__pycache__", ".venv", "venv"}


def scan(root, max_depth):
    """返回 {relpath: {files, bytes, mtime_min, mtime_max, ext}}，只到 max_depth。"""
    res = {}
    root = os.path.abspath(root)
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        rel = os.path.relpath(d, root)
        if rel == ".":
            rel = ""
        files = 0
        total = 0
        mn, mx = None, None
        ext = Counter()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name in SKIP:
                        continue
                    if depth < max_depth:
                        stack.append((e.path, depth + 1))
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            files += 1
            total += st.st_size
            t = int(st.st_mtime)
            mn = t if mn is None or t < mn else mn
            mx = t if mx is None or t > mx else mx
            ext[os.path.splitext(e.name)[1].lower() or "(无扩展名)"] += 1
        key = rel or "."
        res[key] = {
            "files": files,
            "bytes": total,
            "mtime_min": mn,
            "mtime_max": mx,
            "ext": dict(ext.most_common(4)),
        }
    return res


if __name__ == "__main__":
    root, out = sys.argv[1], sys.argv[2]
    depth = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    t0 = time.time()
    data = scan(root, depth)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"{root}: {len(data)} 个目录节点, 用时 {time.time()-t0:.1f}s -> {out}")

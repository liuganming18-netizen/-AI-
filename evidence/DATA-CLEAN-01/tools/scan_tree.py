#!/usr/bin/env python3
"""三方目录清单扫描：相对路径 / 大小 / 修改时间 / inode / 硬链接数。

只读。不做任何写入到被扫描目录。
用法: scan_tree.py <root> <out.json>
"""
import json
import os
import sys


def scan(root: str) -> dict:
    out = {}
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        # 不进入 _backup（正式目录扫描时排除自身）
        dirnames[:] = [d for d in dirnames if d != "_backup"]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            try:
                st = os.lstat(p)
            except OSError as e:
                out[rel] = {"err": str(e)}
                continue
            out[rel] = {
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "ino": st.st_ino,
                "nlink": st.st_nlink,
                "mode": oct(st.st_mode),
                "is_link": os.path.islink(p),
            }
    return out


if __name__ == "__main__":
    root, out = sys.argv[1], sys.argv[2]
    data = scan(root)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True)
    print(f"{root}: {len(data)} 个条目 -> {out}")

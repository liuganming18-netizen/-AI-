#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OOS-READY-01：为交付目录内全部产物生成字节数与 SHA-256 清单。

清单自身不列入（自引用会失配），已标注。
"""
import csv
import hashlib
import os

BASE = "/Users/boos/Desktop/威科夫因子共振趋势/evidence/OOS-READY-01"
SELF = "05_文件清单与哈希.csv"
# 本脚本自身的输出日志在扫描时尚未写入，属自引用，一并排除
SELF_LOG = os.path.join("logs", "02_manifest.log")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    rows = []
    for root, _, files in os.walk(BASE):
        for name in sorted(files):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, BASE)
            if name == SELF or rel == SELF_LOG or name.startswith("."):
                continue
            rows.append({"相对路径": rel, "字节数": os.path.getsize(p), "SHA256": sha256(p)})
    out = os.path.join(BASE, SELF)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["相对路径", "字节数", "SHA256"])
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["相对路径"]):
            w.writerow(r)
    print(f"已写入 {len(rows)} 条（本清单自身不列入）")
    for r in sorted(rows, key=lambda x: x["相对路径"]):
        print(f"  {r['字节数']:>9d}  {r['SHA256'][:16]}  {r['相对路径']}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-01 / 06：产物哈希清单（最后一步跑，保证日志已写完）。"""
from __future__ import annotations

import csv
import hashlib
import os

TICKET = "/Users/boos/Desktop/威科夫因子共振趋势/evidence/FT-VALIDATION-DATA-01"
MANIFEST = "05_文件清单与哈希.csv"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


rows = []
for dirpath, _, files in os.walk(TICKET):
    for fn in sorted(files):
        p = os.path.join(dirpath, fn)
        rel = os.path.relpath(p, TICKET)
        if rel == MANIFEST:
            continue
        rows.append({"path": rel, "bytes": os.path.getsize(p), "sha256": sha256(p)})
with open(os.path.join(TICKET, MANIFEST), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["path", "bytes", "sha256"])
    w.writeheader()
    w.writerows(sorted(rows, key=lambda x: x["path"]))
print("产物 %d 个" % len(rows))
for r in sorted(rows, key=lambda x: x["path"]):
    print("%-45s %9d  %s" % (r["path"], r["bytes"], r["sha256"][:16]))

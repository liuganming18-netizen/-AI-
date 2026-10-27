# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-02 / 07：产物哈希清单（最后一步跑，保证日志已写完）。

本目录内全部文件逐个算 SHA-256；目录外的三处「外部证据」另记一份，
因为它们是本单结论的落点，但不在本目录里（台账页、登记链事件表、链头）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os

TICKET = "/Users/boos/Desktop/威科夫因子共振趋势/evidence/FT-VALIDATION-DATA-02"
ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
MANIFEST = "05_文件清单与哈希.csv"
EXTERNAL_JSON = "out/07_外部证据哈希.json"
SELF_LOG = "logs/07_manifest.log"  # 本脚本自己的日志，落盘时刻与扫描时刻冲突，不入清单

EXTERNAL = [
    ("台账页", ROOT + "/evidence/_统一执行台账/06_FT-VALIDATION-DATA-02_逐条结论.md"),
    ("台账清单", ROOT + "/evidence/_统一执行台账/05_文件清单与哈希.csv"),
    ("登记链事件表", ROOT + "/框架/backend/data/experiments/events.jsonl"),
    ("登记链链头", ROOT + "/框架/backend/data/experiments/head.json"),
    ("上游资格表", ROOT + "/evidence/FT-VALIDATION-DATA-01/out/03_区间资格表.csv"),
]


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
        if rel in (MANIFEST, EXTERNAL_JSON, SELF_LOG):
            continue
        rows.append({"path": rel, "bytes": os.path.getsize(p), "sha256": sha256(p)})
with open(os.path.join(TICKET, MANIFEST), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["path", "bytes", "sha256"])
    w.writeheader()
    w.writerows(sorted(rows, key=lambda x: x["path"]))
print("本目录产物 %d 个" % len(rows))
for r in sorted(rows, key=lambda x: x["path"]):
    print("  %-42s %9d  %s" % (r["path"], r["bytes"], r["sha256"][:16]))

ext = []
for label, p in EXTERNAL:
    if not os.path.exists(p):
        ext.append({"标签": label, "路径": os.path.relpath(p, ROOT),
                    "字节": None, "sha256": None, "存在": False})
        continue
    ext.append({"标签": label, "路径": os.path.relpath(p, ROOT),
                "字节": os.path.getsize(p), "sha256": sha256(p), "存在": True})
with open(os.path.join(TICKET, EXTERNAL_JSON), "w", encoding="utf-8") as fh:
    json.dump({"说明": "本目录之外、但承载本单结论的文件指纹", "项": ext},
              fh, ensure_ascii=False, indent=2)
print("\n外部证据 %d 处" % len(ext))
for r in ext:
    print("  %-12s %-58s %s" % (r["标签"], r["路径"],
                                (r["sha256"] or "缺失")[:16]))

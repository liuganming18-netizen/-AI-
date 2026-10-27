#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一执行台账：自检 + 哈希清单。

自检把台账文档里写的数字与 CSV 实际行数逐项核对；
清单排除自身与自身日志（自引用）。
"""
import csv
import hashlib
import os

BASE = "/Users/boos/Desktop/威科夫因子共振趋势/evidence/_统一执行台账"
OUT = os.path.join(BASE, "out")
SELF = "05_文件清单与哈希.csv"
SELF_LOG = os.path.join("logs", "01_selfcheck_and_manifest.log")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def selfcheck():
    rows = list(csv.DictReader(open(os.path.join(OUT, "01_状态摘录.csv"), encoding="utf-8-sig")))
    dec = list(csv.DictReader(open(os.path.join(OUT, "02_待决策与未解决摘录.csv"), encoding="utf-8-sig")))
    from collections import Counter
    c = Counter(r["状态分类"] for r in rows)
    checks = [
        ("状态摘录 106 行", len(rows) == 106, len(rows)),
        ("可跳过复核 1 个", sum(1 for r in rows if r["是否可跳过复核"] == "是") == 1,
         sum(1 for r in rows if r["是否可跳过复核"] == "是")),
        ("待独立验收 18 个", c["待独立验收"] == 18, c["待独立验收"]),
        ("仅自检通过 1 个", c["仅自检通过"] == 1, c["仅自检通过"]),
        ("无报告文件 12 个", c["无报告文件"] == 12, c["无报告文件"]),
        ("原文写明验收通过 1 个", c["原文写明验收通过"] == 1, c["原文写明验收通过"]),
        ("待决策摘录 56 条", len(dec) == 56, len(dec)),
        ("OOS-READY-01 在摘录内", any(r["工单目录"] == "OOS-READY-01" for r in rows), "查表"),
        ("VALIDATION-CONTRACT-01 在摘录内",
         any(r["工单目录"] == "VALIDATION-CONTRACT-01" for r in rows), "查表"),
    ]
    ok = True
    for name, passed, extra in checks:
        print(("PASS " if passed else "FAIL ") + name + f"  {extra}")
        ok = ok and passed
    print(f"\n自检 {len(checks)} 项；" + ("全部通过" if ok else "存在失败项"))
    return ok


def manifest():
    rows = []
    for root, _, files in os.walk(BASE):
        for name in sorted(files):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, BASE)
            if name == SELF or rel == SELF_LOG or name.startswith("."):
                continue
            rows.append({"相对路径": rel, "字节数": os.path.getsize(p), "SHA256": sha256(p)})
    with open(os.path.join(BASE, SELF), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["相对路径", "字节数", "SHA256"])
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["相对路径"]):
            w.writerow(r)
    print(f"\n清单写入 {len(rows)} 条（本清单与自身日志不列入）")
    for r in sorted(rows, key=lambda x: x["相对路径"]):
        print(f"  {r['字节数']:>8d}  {r['SHA256'][:16]}  {r['相对路径']}")


if __name__ == "__main__":
    ok = selfcheck()
    manifest()
    if not ok:
        raise SystemExit(1)

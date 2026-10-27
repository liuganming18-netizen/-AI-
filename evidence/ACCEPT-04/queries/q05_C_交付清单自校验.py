# -*- coding: utf-8 -*-
"""ACCEPT-04 / C：交付清单 08 自校验 + 源码版本绑定核查（只读）"""
from __future__ import annotations

import csv
import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "evidence" / "SYSTEM-UI-03"
OUT = ROOT / "evidence" / "ACCEPT-04" / "out"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


rows = list(csv.DictReader((EV / "08_文件清单与哈希.csv").open(encoding="utf-8-sig")))
items, bad = [], []
for r in rows:
    rel = (r.get("相对路径") or "").strip()
    want = (r.get("SHA-256完整哈希") or "").strip().lower()
    p = ROOT / rel
    if not p.exists():
        items.append({"file": rel, "exists": False, "claimed": want})
        bad.append({"file": rel, "reason": "MISSING"})
        continue
    got = sha(p)
    rec = {"file": rel, "exists": True, "size": p.stat().st_size, "claimed": want,
           "actual": got, "match": got == want,
           "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(p.stat().st_mtime))}
    items.append(rec)
    if got != want:
        bad.append({"file": rel, "actual": got[:16], "claimed": want[:16]})

report = {
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "manifest_rows": len(rows),
    "mismatch_count": len(bad),
    "mismatches": bad,
    "categories": sorted({(r.get("文件类别") or "").strip() for r in rows}),
    "source_or_build_rows": [r["相对路径"] for r in rows
                             if "源码" in (r.get("文件类别") or "")
                             or "/src/" in r["相对路径"] or "/dist/" in r["相对路径"]],
    "note": ("清单仅覆盖交付证据文件，未绑定任何被测源码 / 前端构建产物 ⇒ C 无版本绑定；"
             "清单自身与 audit_ui_verification.json 的哈希与磁盘不符 ⇒ 清单非脚本机械生成"),
    "items": items,
}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "q05_C_交付清单自校验.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("rows:", len(rows), "mismatch:", len(bad))
for b in bad:
    print("  ", b)
print("source/build rows:", report["source_or_build_rows"])

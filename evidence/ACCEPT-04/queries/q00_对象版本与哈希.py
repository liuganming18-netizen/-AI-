# -*- coding: utf-8 -*-
"""ACCEPT-04 / 开审强制检查：对象版本与哈希漂移（只读）

对被审对象 A（SYSTEM-ISOLATION-04）与 C（SYSTEM-UI-03）逐一重算 SHA-256，
与交付声称值比对，并记录 mtime；用于判定「结论适用版本」。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evidence" / "ACCEPT-04" / "out"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def info(rel: str, claimed: str | None = None) -> dict:
    p = ROOT / rel
    if not p.exists():
        return {"file": rel, "exists": False, "claimed": claimed}
    got = sha(p)
    return {"file": rel, "exists": True, "size": p.stat().st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)),
            "claimed": claimed, "actual": got,
            "match": (got == claimed) if claimed else None}


A = ROOT / "evidence" / "SYSTEM-ISOLATION-04"
a_json = json.loads((A / "path_isolation_evidence.json").read_text(encoding="utf-8"))
a_claimed = a_json["execution_metadata"]["source_sha256"]
a_items = [info(rel, want) for rel, want in a_claimed.items()]

# C：UI-03 交付未逐文件声明源码哈希，用交付清单 08 里的声明（若存在）
C = ROOT / "evidence" / "SYSTEM-UI-03"
c_manifest = C / "08_文件清单与哈希.csv"
c_items = []
if c_manifest.exists():
    import csv
    rows = list(csv.DictReader(c_manifest.open(encoding="utf-8-sig")))
    keys = rows[0].keys() if rows else []
    fcol = next((k for k in keys if "文件" in k or "路径" in k), None)
    hcol = next((k for k in keys if "SHA" in k.upper() or "hash" in k.lower()), None)
    if fcol and hcol:
        for r in rows:
            rel = (r[fcol] or "").strip()
            want = (r[hcol] or "").strip().lower()
            if not rel or not want:
                continue
            cand = Path(rel) if Path(rel).is_absolute() else ROOT / rel
            c_items.append(info(str(cand.relative_to(ROOT)) if str(cand).startswith(str(ROOT))
                                else rel, want or None))

report = {
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "A": {
        "evidence_dir": str(A),
        "delivery_mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime((A / "README.md").stat().st_mtime)),
        "run_id": a_json.get("run_id"),
        "items": a_items,
        "drifted": [i["file"] for i in a_items if i.get("match") is False],
        "all_match": all(i.get("match") for i in a_items),
    },
    "C": {
        "evidence_dir": str(C),
        "delivery_mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime((C / "00_SYSTEM_UI_03_真实页面与数据对账交付报告.md").stat().st_mtime)),
        "manifest": str(c_manifest),
        "items": c_items,
        "drifted": [i["file"] for i in c_items if i.get("match") is False],
        "checked": len(c_items),
    },
    "B": {
        "note": "B 对象未在 evidence/ 下找到独立交付；UI-EVIDENCE-04 于 16:06 起写入，未冻结",
        "ui_evidence_04_files": sorted(
            str(p.relative_to(ROOT)) for p in (ROOT / "evidence" / "UI-EVIDENCE-04").rglob("*")
            if p.is_file()),
    },
}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "q00_对象版本与哈希.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("A delivery:", report["A"]["delivery_mtime"], "run:", report["A"]["run_id"])
for i in a_items:
    print(f"  A {i['file']:<52} match={i.get('match')} mtime={i.get('mtime')}")
print("A drifted:", report["A"]["drifted"])
print("C manifest rows checked:", report["C"]["checked"], "drifted:", report["C"]["drifted"])
for i in c_items[:20]:
    print(f"  C {i['file']:<60} match={i.get('match')} mtime={i.get('mtime')}")
print("B files:", report["B"]["ui_evidence_04_files"])

# -*- coding: utf-8 -*-
"""ACCEPT-04 / B：UI-EVIDENCE-04（疑似 B 对象）独立复算（只读）

对象：evidence/UI-EVIDENCE-04/（工单 UI-EVIDENCE-04，16:03 开工、16:11–16:19 落盘，16:20 后无写入）
检查：
  B-1 原始读数 out/*.json 与报告声称是否一致（negative_tests 9/9、patch 8/8、fixed_sample 5PASS/1FAIL）
  B-2 09 版本绑定的后端文件哈希与磁盘现状逐条比对
  B-3 自身 08 清单是否排除自身、逐行哈希是否一致
  B-4 是否声称「页面通过」（纪律：脚本改对 ≠ 端到端通过）
"""
from __future__ import annotations

import csv
import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "evidence" / "UI-EVIDENCE-04"
OUT = ROOT / "evidence" / "ACCEPT-04" / "out"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
          "evidence_dir": str(EV), "items": {}}

# B-1 原始读数
for name, f in [("negative_tests", "out/negative_tests.json"),
                ("fixed_sample_check", "out/fixed_sample_check.json"),
                ("patch_verification", "out/patch_verification.json"),
                ("recon_ui03", "out/recon_ui03.json")]:
    p = EV / f
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        report["items"][name] = {"exists": True,
                                 "keys": list(d.keys())[:14],
                                 "excerpt": json.dumps(d, ensure_ascii=False)[:600]}
    else:
        report["items"][name] = {"exists": False}

# B-2 版本绑定比对
b09 = EV / "09_源码与构建产物版本绑定.json"
bind_check = []
if b09.exists():
    d = json.loads(b09.read_text(encoding="utf-8"))
    be = d.get("绑定", {}).get("backend", {})
    for rel, meta in be.items():
        p = ROOT / "框架" / "backend" / rel
        want = (meta or {}).get("sha256")
        if not p.exists():
            bind_check.append({"file": rel, "claimed": want, "actual": None, "match": False})
            continue
        got = sha(p)
        bind_check.append({"file": rel, "claimed": want, "actual": got,
                           "match": got == want,
                           "claimed_mtime": (meta or {}).get("mtime"),
                           "actual_mtime": time.strftime(
                               "%Y-%m-%dT%H:%M:%S", time.localtime(p.stat().st_mtime))})
report["items"]["b09_backend_binding"] = bind_check
report["items"]["b09_all_match"] = all(c["match"] for c in bind_check) if bind_check else None

# B-3 自身 08 清单
c08 = EV / "08_文件清单与哈希.csv"
self_check = {"exists": c08.exists()}
if c08.exists():
    rows = list(csv.DictReader(c08.open(encoding="utf-8-sig")))
    cols = list(rows[0].keys()) if rows else []
    fcol = next((c for c in cols if "路径" in c), None) or next(
        (c for c in cols if "文件" in c), None)
    hcol = next((c for c in cols if "SHA" in c.upper() or "hash" in c.lower()), None)
    bad, selfrow = [], None
    for r in rows:
        rel = (r.get(fcol) or "").strip()
        want = (r.get(hcol) or "").strip().lower()
        if rel.endswith("08_文件清单与哈希.csv"):
            selfrow = rel
        p = ROOT / rel
        if not p.exists():
            bad.append({"file": rel, "reason": "MISSING"})
        elif sha(p) != want:
            bad.append({"file": rel, "actual": sha(p)[:16], "claimed": want[:16]})
    self_check.update({"rows": len(rows), "self_listed": selfrow, "mismatch": bad})
report["items"]["b08_self_check"] = self_check

# B-4 是否越界声称页面通过
claim_scan = []
for md in sorted(EV.glob("*.md")):
    s = md.read_text(encoding="utf-8", errors="ignore")
    claim_scan.append({"file": md.name,
                       "says_no_integration": ("未做集成采证" in s) or ("不起后端" in s),
                       "mentions_page_pass": "页面通过" in s})
report["items"]["claims"] = claim_scan

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "q06_B_UI_EVIDENCE_04复算.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("B-1 readings:", {k: v["exists"] for k, v in report["items"].items()
                        if isinstance(v, dict) and "exists" in v})
print("B-2 backend binding all match:", report["items"]["b09_all_match"])
for c in bind_check:
    print("   ", c["file"], c["match"], c.get("claimed_mtime"), c.get("actual_mtime"))
print("B-3 self check:", self_check)
print("B-4 claims:", claim_scan)

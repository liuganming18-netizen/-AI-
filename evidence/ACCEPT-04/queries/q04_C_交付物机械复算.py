# -*- coding: utf-8 -*-
"""ACCEPT-04 / C：SYSTEM-UI-03 交付物机械复算（只读）

1) 9 张状态截图 + 16 张 view 的 SHA-256：与 CSV06 声明比对、并算唯一性；
2) 三态是否真的不同（字节级 + 像素级）；
3) 生产账本 / locks 现状与交付声称的「零触碰」比对；
4) CSV03「9 列对账」逐行判定：DOM 有值而 API 为 None/暂无数据却写「一致」的行数；
5) CSV04「真实字段路径」逐行回磁盘核对。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "evidence" / "SYSTEM-UI-03"
OUT = ROOT / "evidence" / "ACCEPT-04" / "out"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


pngs = {p.name: {"sha256": sha(p), "size": p.stat().st_size,
                 "mtime": time.strftime("%H:%M:%S", time.localtime(p.stat().st_mtime))}
        for p in sorted(EV.glob("*.png"))}
uniq = {}
for n, v in pngs.items():
    uniq.setdefault(v["sha256"], []).append(n)

# CSV06 声明
csv06 = list(csv.DictReader((EV / "06_信号持仓止损三状态覆盖证据表.csv").open(encoding="utf-8-sig")))
state_rows = []
for r in csv06:
    fn = (r.get("截图文件") or "").strip()
    want = (r.get("截图SHA256(前16位)") or "").strip().lower()
    got = pngs.get(fn, {}).get("sha256", "")
    state_rows.append({"module": r.get("功能模块"), "state": r.get("覆盖状态"),
                       "file": fn, "claimed16": want, "actual16": got[:16],
                       "match": bool(want) and got.startswith(want)})

# 三态两两不同
groups = {}
for r in state_rows:
    groups.setdefault(r["module"], []).append(r)
triplet = {}
for mod, rows in groups.items():
    hashes = [pngs.get(r["file"], {}).get("sha256") for r in rows]
    triplet[mod] = {"files": [r["file"] for r in rows],
                    "unique_hashes": len(set(h for h in hashes if h)),
                    "all_distinct": len(set(h for h in hashes if h)) == len(rows)}

# 生产账本零触碰（与 README 声称的首16位比对）
prod_claims = {"paper_3a.sqlite3": "a3e18d8d9101d840",
               "paper_3b.sqlite3": "49d08d3923f7f3f7",
               "paper_3c.sqlite3": "6c9fffd77e2da5b8",
               "paper_3d.sqlite3": "b169aa30f19bfbf0"}
prod = {}
for name, want in prod_claims.items():
    p = ROOT / "数据" / "paper" / name
    if p.exists():
        prod[name] = {"claimed16": want, "actual16": sha(p)[:16],
                      "match": sha(p).startswith(want),
                      "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                             time.localtime(p.stat().st_mtime))}
    else:
        prod[name] = {"claimed16": want, "exists": False}
locks_dir = ROOT / "框架" / "backend" / "locks"
locks = sorted(p.name for p in locks_dir.glob("*")) if locks_dir.exists() else []

# CSV03 逐行：DOM 有值 vs API 空值
csv03 = list(csv.DictReader((EV / "03_顶栏与行情9列同一响应逐字段比对表.csv").open(encoding="utf-8-sig")))
empty_api_tokens = {"", "none", "null", "—", "-", "暂无数据", "n/a"}
c03 = []
for r in csv03:
    dom = (r.get("前端页面DOM渲染值") or "").strip()
    api = (r.get("后端API同一响应原始值") or "").strip()
    verdict = (r.get("匹配判定") or "").strip()
    api_empty = api.lower() in empty_api_tokens
    dom_empty = dom.lower() in empty_api_tokens
    c03.append({"field": r.get("字段名称"), "dom": dom[:40], "api": api[:40],
                "verdict": verdict,
                "api_empty_but_dom_not": api_empty and not dom_empty,
                "suspect": verdict == "一致" and api_empty and not dom_empty})
csv03_suspect = [r for r in c03 if r["suspect"]]

# CSV04 逐行回磁盘
run_dir = ROOT / "回测日志" / "event_trend_v0_1" / "runs" / "run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT"
summ = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
runj = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def dig(d, path):
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return "<MISSING>"
    return cur


csv04 = list(csv.DictReader((EV / "04_回测run详情与summary逐字段对账表.csv").open(encoding="utf-8-sig")))
c04 = []
for r in csv04:
    fp = (r.get("服务端文件真实字段路径") or "").strip()
    claimed = (r.get("服务端真实文件落盘值") or "").strip()
    file, _, fieldpath = fp.partition(" / ")
    doc = {"summary.json": summ, "run.json": runj}.get(file.strip())
    actual = dig(doc, fieldpath) if doc else "<NO_FILE>"
    c04.append({"dim": r.get("对账维度"), "claimed_path": fp, "claimed_value": claimed,
                "actual_value": actual, "path_exists": actual != "<MISSING>"})

report = {
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "screenshots": {"count": len(pngs), "unique": len(uniq),
                    "duplicates": {k: v for k, v in uniq.items() if len(v) > 1},
                    "files": pngs},
    "csv06_states": state_rows,
    "csv06_all_match": all(r["match"] for r in state_rows),
    "triplets": triplet,
    "production_paper": prod,
    "locks_files": locks,
    "csv03": {"rows": len(c03), "suspect_count": len(csv03_suspect),
              "suspects": csv03_suspect},
    "csv04": {"rows": len(c04), "path_missing": sum(1 for r in c04 if not r["path_exists"]),
              "details": c04},
    "run_dir": {"path": str(run_dir), "summary_sha256": sha(run_dir / "summary.json"),
                "manifest_exists": (run_dir / "manifest.json").exists()},
}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "q04_C_交付物机械复算.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("PNG count:", len(pngs), "unique:", len(uniq))
print("duplicates:", {k[:12]: v for k, v in uniq.items() if len(v) > 1})
print("CSV06 all match:", report["csv06_all_match"])
for mod, t in triplet.items():
    print("  triplet", mod, t["unique_hashes"], "/", len(t["files"]), "distinct:", t["all_distinct"])
print("prod paper:", {k: v.get("match") for k, v in prod.items()})
print("locks:", locks)
print("CSV03 rows:", len(c03), "suspect:", len(csv03_suspect))
for s in csv03_suspect:
    print("   SUSPECT", s["field"], "| DOM:", s["dom"][:24], "| API:", s["api"][:16])
print("CSV04 rows:", len(c04), "path missing:", report["csv04"]["path_missing"])
for d in c04:
    print(f"   {d['dim']:<12} {d['claimed_path']:<48} -> {d['actual_value']}")
print("summary sha:", report["run_dir"]["summary_sha256"][:16], "manifest:", report["run_dir"]["manifest_exists"])

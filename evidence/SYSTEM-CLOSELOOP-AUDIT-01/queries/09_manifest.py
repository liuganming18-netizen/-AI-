# -*- coding: utf-8 -*-
"""09 产物清单：本轮新增的每个文件都给 SHA-256 与字节数，便于第三方逐个复核。

只扫 evidence/SYSTEM-CLOSELOOP-AUDIT-01/ 下的文件（不改、不删任何东西）。

用法：python 09_manifest.py
产物：out/09_产物清单.csv、out/09_产物清单.json、09_产物清单.md
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-01"
OUT = HERE / "out"


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


rows = []
for p in sorted(HERE.rglob("*")):
    if not p.is_file():
        continue
    rel = p.relative_to(HERE).as_posix()
    if rel.startswith("out/09_产物清单"):
        continue                     # 自己不算自己
    rows.append({
        "相对路径": rel,
        "字节": p.stat().st_size,
        "sha256": sha(p),
        "类别": ("脚本" if rel.startswith("queries/")
                 else "截图" if rel.endswith(".png")
                 else "原始响应" if rel.startswith("out/api/")
                 else "快照" if rel.startswith("out/snapshots/")
                 else "结论数据" if rel.startswith("out/")
                 else "报告"),
    })

rows.sort(key=lambda r: (r["类别"], r["相对路径"]))

with open(OUT / "09_产物清单.csv", "w", newline="", encoding="utf-8-sig") as f:
    wri = csv.DictWriter(f, fieldnames=["类别", "相对路径", "字节", "sha256"])
    wri.writeheader()
    for r in rows:
        wri.writerow({"类别": r["类别"], "相对路径": r["相对路径"],
                      "字节": r["字节"], "sha256": r["sha256"]})

(OUT / "09_产物清单.json").write_text(
    json.dumps({"文件数": len(rows), "文件": rows}, ensure_ascii=False, indent=1),
    encoding="utf-8")

by_cat: dict[str, list] = {}
for r in rows:
    by_cat.setdefault(r["类别"], []).append(r)

L = ["# SYSTEM-CLOSELOOP-AUDIT-01 产物清单\n\n",
     f"共 **{len(rows)}** 个文件（不含本清单自身）。"
     "每个文件都有 SHA-256，第三方可逐个复核。\n\n",
     "| 类别 | 文件数 | 总字节 |\n|---|---|---|\n"]
for cat in sorted(by_cat):
    L.append(f"| {cat} | {len(by_cat[cat])} | {sum(x['字节'] for x in by_cat[cat]):,} |\n")
L.append("\n")

for cat in sorted(by_cat):
    L.append(f"## {cat}（{len(by_cat[cat])}）\n\n")
    L.append("| 相对路径 | 字节 | sha256（前 16 位） |\n|---|---|---|\n")
    for r in by_cat[cat]:
        L.append(f"| `{r['相对路径']}` | {r['字节']:,} | `{r['sha256'][:16]}` |\n")
    L.append("\n")

(HERE / "09_产物清单.md").write_text("".join(L), encoding="utf-8")

print(f"文件数 {len(rows)}")
for cat in sorted(by_cat):
    print(f"  {cat}: {len(by_cat[cat])}")

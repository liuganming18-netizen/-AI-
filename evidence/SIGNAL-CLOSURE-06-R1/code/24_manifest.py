#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""24_manifest.py —— 生成本单证据清单 `MANIFEST.json`（含每个文件的 SHA-256）

纪律：
  · 只登记本单目录 `evidence/SIGNAL-CLOSURE-06-R1/` 下的 `code/` 与 `out/`；
  · **不登记自己**（`MANIFEST.json` 无法包含自身的校验和）；
  · `out/` 下的隔离目录（`*_iso`、`*_iso_live`）只登记文件，不递归展开数据库大文件之外的东西；
  · 版本只认文件 SHA-256，进程身份只在当次运行有效。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
OUT = R1 / "out"
MANIFEST = R1 / "MANIFEST.json"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def entry(p: Path) -> dict:
    st = p.stat()
    return {
        "path": str(p.relative_to(R1)),
        "bytes": st.st_size,
        "mtime_utc": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": sha(p),
    }


files = []
for sub in ("code", "out"):
    base = R1 / sub
    if not base.exists():
        continue
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if p.name.startswith(".") or p.suffix in (".pyc",):
            continue
        if p == MANIFEST:
            continue
        files.append(entry(p))

for extra in ("SIGNAL-CLOSURE-06-R1_最终报告.md",):
    p = R1 / extra
    if p.exists():
        files.append(entry(p))

payload = {
    "work_order": "SIGNAL-CLOSURE-06-R1",
    "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "root": str(R1),
    "n_files": len(files),
    "note": "版本只认 SHA-256；本文件不登记自身。",
    "annotations": {
        "out/20c/20_运行观察_样本.jsonl":
            "20 号崩溃后重启采样留下的残留（4 行、同一根水位），进程随后被终止。"
            "保留不删（审计不删证据），但**不作任何结论依据**，报告与 22 / 27 号都不引用。",
        "out/03b/":
            "用**当前磁盘上的** 03 脚本、同一截止时刻（2026-10-25T04:59:59Z）复跑的产物；"
            "上一级 `out/03_重启一致性_D2880_anchor2880.json` 是改版前脚本产物，两者须成对引用。",
        "out/*_iso/ 与 out/*_iso_live/":
            "隔离环境（独立 LIVE_DB、独立账本目录、独立行情库），不是生产库副本。",
    },
    "files": files,
}
MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"[out] {MANIFEST}  {len(files)} 个文件")
for f in files:
    print(f"  {f['sha256'][:16]}  {f['bytes']:>10}  {f['path']}")

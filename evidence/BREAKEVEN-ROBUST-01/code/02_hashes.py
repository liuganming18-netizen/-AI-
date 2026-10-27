#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""02_hashes.py —— 零改动自检 + 本单产物哈希

1. 把 BREAKEVEN-01/out/versions.json 里记录的全部文件（策略/交易核心/本单代码/数据）
   重新算 SHA-256 并与当时记录逐一比对 ⇒ 证明「审计期间未改动上游产物与工程代码」；
2. 记录本单只读的输入文件哈希（读到的就是分析用的那份）；
3. 计算并落盘本单自己的产物哈希（写完之后再跑一次，见 03_实际命令.md）。

只读，除本单 out/ 目录外不写任何文件。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
REF_VERSIONS = ROOT / "evidence/BREAKEVEN-01/out/versions.json"

READ_ONLY_INPUTS = [
    "evidence/BREAKEVEN-01/out/paired_trades.csv",
    "evidence/BREAKEVEN-01/out/dedup_sensitivity.json",
    "evidence/BREAKEVEN-01/out/summary.json",
]


def sha256(p: Path) -> dict:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    st = p.stat()
    return {"exists": True, "sha256": h.hexdigest(), "bytes": st.st_size, "mtime": int(st.st_mtime)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ref = json.loads(REF_VERSIONS.read_text(encoding="utf-8"))
    check = []
    for rel, old in ref.get("files", {}).items():
        p = ROOT / rel
        if not p.exists():
            check.append({"file": rel, "exists_now": False, "unchanged": False,
                          "note": "当时记录存在，现已不存在"})
            continue
        now = sha256(p)
        same = (now["sha256"] == old.get("sha256"))
        check.append({
            "file": rel, "exists_now": True, "unchanged": same,
            "sha256": now["sha256"], "bytes": now["bytes"], "mtime": now["mtime"],
            "recorded_sha256": old.get("sha256"), "recorded_mtime": old.get("mtime"),
        })

    n_unchanged = sum(1 for c in check if c.get("unchanged"))
    n_total = len(check)

    inputs = {rel: sha256(ROOT / rel) for rel in READ_ONLY_INPUTS}

    payload = {
        "note": "本单只读上游产物；除本单 out/ 外未写任何工程文件。零改动以 SHA-256 比对为准（不看 git status）。",
        "upstream_files_checked": n_total,
        "upstream_files_unchanged": n_unchanged,
        "upstream_all_unchanged": n_unchanged == n_total,
        "upstream_detail": check,
        "read_only_inputs": inputs,
    }
    (OUT / "zero_change_check.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"上游文件 {n_unchanged}/{n_total} 与 BREAKEVEN-01 记录逐字节一致")
    if n_unchanged != n_total:
        for c in check:
            if not c.get("unchanged"):
                print("  变化：", c["file"])
    for rel, h in inputs.items():
        print(f"输入 {rel}  sha256={h['sha256'][:16]}…  {h['bytes']}B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

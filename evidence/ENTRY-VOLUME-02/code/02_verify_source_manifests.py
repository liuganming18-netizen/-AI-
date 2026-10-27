#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对照已有版本清单复算本次使用的两组上游产物 SHA-256。

只核验 VOLUME-BE-02 与 ENTRY-VOLUME-01 两份版本清单，不读取任何结果汇总参与算术。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence/ENTRY-VOLUME-02/out"
MANIFESTS = [
    (ROOT / "evidence/VOLUME-BE-02/out/versions.json", "root_relative"),
    (ROOT / "evidence/ENTRY-VOLUME-01/out/versions.json", "entry01_relative"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    rows = []
    for manifest_path, mode in MANIFESTS:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for rel, expected in data.items():
            path = ROOT / rel if mode == "root_relative" else ROOT / "evidence/ENTRY-VOLUME-01" / rel
            exists = path.is_file()
            actual = sha256(path) if exists else None
            rows.append({
                "manifest": str(manifest_path),
                "file": str(path),
                "exists": exists,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "matches": exists and actual == expected,
            })
    failed = [r for r in rows if not r["matches"]]
    payload = {
        "manifests_checked": [str(x[0]) for x in MANIFESTS],
        "file_count": len(rows),
        "match_count": len(rows) - len(failed),
        "all_match": not failed,
        "detail": rows,
    }
    (OUT / "upstream_manifest_check.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已有上游版本清单核验：{len(rows)-len(failed)}/{len(rows)} SHA-256 相同")
    if failed:
        for r in failed:
            print("不一致:", r["file"], "exists=", r["exists"],
                  "expected=", r["expected_sha256"], "actual=", r["actual_sha256"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

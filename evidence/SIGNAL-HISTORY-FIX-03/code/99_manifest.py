# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜99 生成 MANIFEST.json（只读；不覆盖任何旧证据）。

MANIFEST 只记「本单目录内的文件 + SHA-256 + 大小」，用于事后核对
「我读到的证据就是当时那份」。**每次核对都要重记核对时刻**（`generated_at_utc`），
因为哈希声明会随文件改动当场失效。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]          # evidence/SIGNAL-HISTORY-FIX-03
OUT = BASE / "out"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files = {}
    for p in sorted(BASE.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(BASE))
        if rel == "MANIFEST.json":
            continue
        files[rel] = {"size": p.stat().st_size, "sha256": sha(p)}
    man = {
        "work_order": "SIGNAL-HISTORY-FIX-03",
        "generated_at_utc": now,
        "root": str(BASE),
        "n_files": len(files),
        "total_bytes": sum(v["size"] for v in files.values()),
        "note": ("哈希只对**本目录内**文件负责。核对时请重新生成并比对 `generated_at_utc`；"
                 "旧时刻的哈希声明不构成当前状态的证据。"),
        "files": files,
    }
    p = BASE / "MANIFEST.json"
    p.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[out] {p} ({p.stat().st_size} B) files={len(files)} "
          f"bytes={man['total_bytes']}")
    # 关键产物的存在性自证：缺一个就报出来，别让「目录存在」冒充「证据齐」
    must = [
        "SIGNAL-HISTORY-FIX-03_主报告.md",
        "out/01_历史接入.json", "out/02_窗口归因.json", "out/03_AB对照.json",
        "out/04_隔离一致性.json", "out/04_1h节奏.csv", "out/04b_归因摘要.json",
        "out/04c_闸门锚点.json", "out/05_异常标的.json", "out/05_异常标的.csv",
        "out/06_生产核验_pre.json", "out/06_生产核验_post.json",
        "out/06_生产核验_对比.json", "out/06_重启记录.json",
        "out/07_用户入口.json", "out/07_用户入口.png",
    ]
    missing = [m for m in must if m not in files]
    print(f"[check] 必需产物 {len(must) - len(missing)}/{len(must)} 在册"
          + (f"；缺 {missing}" if missing else ""))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())

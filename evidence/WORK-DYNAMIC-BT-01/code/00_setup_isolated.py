# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01 隔离环境搭建。

只做两件事：
1) 把 FT-LOW4H-GRID-01 的基类策略**原样**复制到本单隔离目录；
2) 仅替换「写盘输出目录」三处硬编码路径（原本写进 FT-LOW4H-GRID-01/out，属于旧证据，
   本单禁止写入），并记录逐行 diff + SHA，供 Codex 核对「除输出路径外零逻辑改动」。

不做任何交易逻辑修改。
"""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
SRC = BASE / "evidence/FT-LOW4H-GRID-01/user_data/strategies/WyckoffLow4hGridBase.py"
DST = BASE / "evidence/WORK-DYNAMIC-BT-01/user_data/strategies/WyckoffLow4hGridBase.py"
EXP = BASE / "evidence/WORK-DYNAMIC-BT-01"

REPLACEMENTS = [
    (
        'os.path.join(BASE_DIR, "evidence/FT-LOW4H-GRID-01/user_data")',
        'os.path.join(BASE_DIR, "evidence/WORK-DYNAMIC-BT-01/user_data")',
    ),
    (
        'os.path.join(BASE_DIR, "evidence/FT-LOW4H-GRID-01/out")',
        'os.path.join(BASE_DIR, "evidence/WORK-DYNAMIC-BT-01/out")',
    ),
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    src_text = SRC.read_text(encoding="utf-8")
    out = src_text
    applied = {}
    for old, new in REPLACEMENTS:
        n = out.count(old)
        applied[old] = n
        out = out.replace(old, new)

    DST.write_text(out, encoding="utf-8")

    diff = list(difflib.unified_diff(
        src_text.splitlines(keepends=True),
        out.splitlines(keepends=True),
        fromfile=str(SRC), tofile=str(DST), n=1,
    ))

    rec = {
        "generated_at_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(SRC),
        "source_sha256": sha256(SRC),
        "target": str(DST),
        "target_sha256": sha256(DST),
        "replacement_counts": applied,
        "changed_lines": sum(1 for l in diff if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))),
        "note": ("仅替换写盘输出目录（旧证据目录 → 本单隔离目录）；"
                 "交易逻辑、参数默认值、状态机、止损/跟踪算法一字未改。"),
    }
    (EXP / "out").mkdir(parents=True, exist_ok=True)
    (EXP / "out/00_基类隔离改动记录.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    (EXP / "out/00_基类隔离改动记录.diff").write_text("".join(diff), encoding="utf-8")

    print(json.dumps(rec, ensure_ascii=False, indent=2))
    print("\n=== DIFF ===")
    print("".join(diff))


if __name__ == "__main__":
    main()

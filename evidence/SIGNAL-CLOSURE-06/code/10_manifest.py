# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜10 证据清单（MANIFEST.json）。

纪律：版本只认文件 SHA-256，不认「我记得改过」。清单在**产物齐了之后**跑，
缺件显式列出，不静默跳过。

用法：python code/10_manifest.py
产出：MANIFEST.json
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

EVID = Path(__file__).resolve().parents[1]

#: 本轮「必须存在」的产物（缺一个就说明有一步没跑完）
REQUIRED_OUT = [
    "01_最早分歧.json", "01_后续影响.json",
    "02_深度敏感性_effective.json",
    "03_重启一致性_D1160.json", "03_重启一致性_D2880.json",
    "03_重启一致性_D2880_anchor2880.json",
    "03_重启一致性_D2880_anchor4000.json",
    "04_生产一致性.json", "04_生产一致性_anchor2880.json",
    "05_实时与回放条件比对_anchor2880.json",
    "05b_零信号汇总_anchor2880.json",
    "06_恢复候选执行.json",
    "07_生产加载观察.json",
    "07b_水位探针.json",
    "08_迁移流水对齐诊断_BATUSDT_anchor2880.json",
    "09_报告数字.json",
]

#: 影响运行行为的代码（必须记 SHA，且必须与生产在跑的一致）
CODE = [
    "框架/backend/unified_runtime.py",
    "框架/backend/live_store.py",
    "框架/backend/paper_engine.py",
]


def one(p: Path) -> dict:
    if not p.exists():
        return {"exists": False}
    return {"exists": True, "size": p.stat().st_size,
            "sha256": cm.sha256_file(p),
            "mtime_utc": cm.utc(int(p.stat().st_mtime * 1000))}


def main() -> int:
    base = cm.BASE
    man = {
        "ticket": "SIGNAL-CLOSURE-06",
        "generated_at_utc": cm.utc(cm.now_ms()),
        "project": str(base),
        "evidence_dir": str(EVID),
        "note": ("版本只认 SHA-256。`out/unified_runtime_before.py` 是修复前原文备份，"
                 "用于复算改动规模，不参与运行。"),
        "code_in_evidence": {}, "out": {}, "runtime_code": {}, "missing": [],
    }

    for p in sorted((EVID / "code").glob("*.py")):
        man["code_in_evidence"][p.name] = one(p)

    for name in REQUIRED_OUT:
        p = EVID / "out" / name
        rec = one(p)
        man["out"][name] = rec
        if not rec["exists"]:
            man["missing"].append(f"out/{name}")

    # 其余产物（诊断、日志、CSV）一并登记，便于交叉核对
    extra = {}
    for p in sorted((EVID / "out").glob("*")):
        if p.name in REQUIRED_OUT or p.is_dir():
            continue
        extra[p.name] = one(p)
    man["out_extra"] = extra

    for rel in CODE:
        p = base / rel
        man["runtime_code"][rel] = one(p)

    # 报告本身
    man["report"] = one(EVID / "SIGNAL-CLOSURE-06_报告.md")

    (EVID / "MANIFEST.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[MANIFEST] code {len(man['code_in_evidence'])} 件 / "
          f"必需产物 {len(REQUIRED_OUT) - len(man['missing'])}/{len(REQUIRED_OUT)} / "
          f"其他产物 {len(extra)} 件")
    if man["missing"]:
        print("[缺件] " + ", ".join(man["missing"]))
    for rel, rec in man["runtime_code"].items():
        print(f"  {rel:44s} {rec.get('sha256', 'MISSING')[:16]}… {rec.get('size')}B")
    return 0 if not man["missing"] else 1


if __name__ == "__main__":
    sys.exit(main())

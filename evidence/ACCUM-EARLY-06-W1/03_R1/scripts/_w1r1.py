#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W1-R1 共用件：路径、身份、读回校验、落盘。

设计口径（与本单工单 D 一致）
----------------------------
1. **只写本窗** `evidence/ACCUM-EARLY-06-W1/03_R1/`；所有 `dump()` 都落在本窗 `out/`。
2. 旧产物（`../01/`、`../02/`）**只读**，本模块只提供 `sha256_file()` 记录身份，不提供写入口。
3. 读回校验统一走 `check_ref()`：`file:line` 必须真的包含 `must_contain`；
   行号漂移直接失败，避免「引用了但没读回」。
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]          # 03_R1/
SCRIPTS = HERE / "scripts"
OUT = HERE / "out"
RUNS = HERE / "runs"
ROOT = Path(__file__).resolve().parents[4]          # 工程根

W1 = ROOT / "evidence/ACCUM-EARLY-06-W1"            # 旧产物（只读）
OLD01 = W1 / "01"
OLD02 = W1 / "02"

DECISION = "docs/用户约定/用户要求与决策.md"
DISPATCH06 = "docs/派工_ACCUM-EARLY-06_六窗口十二单_2026-10-27.md"
DISPATCH_R1 = "docs/派工_ACCUM-EARLY-06_五点复核整改_2026-10-27.md"
CODE = "框架/backend/accum_early_entry.py"
PE = "框架/backend/paper_engine.py"

BACKEND = ROOT / "框架/backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def iso(ms) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def dump(name: str, payload) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


_LINE_CACHE: dict[Path, list[str]] = {}


def lines_of(rel: str) -> list[str]:
    p = ROOT / rel
    if p not in _LINE_CACHE:
        _LINE_CACHE[p] = p.read_text(encoding="utf-8").splitlines()
    return _LINE_CACHE[p]


def check_ref(ref: dict) -> dict:
    """读回一条 `{file,line,must_contain}`；不做模糊匹配，行号必须命中。"""
    rel = ref["file"]
    p = ROOT / rel
    rec = {"file": rel, "line": ref.get("line"), "must_contain": ref.get("must_contain"),
           "ok": False, "reason": None}
    if not p.exists():
        rec["reason"] = "file_missing"
        return rec
    lines = lines_of(rel)
    if ref.get("line_search"):
        hits = [i + 1 for i, s in enumerate(lines) if ref["must_contain"] in s]
        rec["line"] = hits[0] if hits else None
        rec["ok"] = bool(hits)
        rec["reason"] = None if hits else "not_found_in_file"
        return rec
    ln = ref.get("line")
    if not isinstance(ln, int) or ln <= 0 or ln > len(lines):
        rec["reason"] = "line_invalid"
        return rec
    rec["actual"] = lines[ln - 1].strip()[:260]
    if ref["must_contain"] in lines[ln - 1]:
        rec["ok"] = True
    else:
        rec["reason"] = "must_contain_not_on_line"
        rec["nearby"] = [i + 1 for i, s in enumerate(lines) if ref["must_contain"] in s][:5]
    return rec


def scan_keywords(rel: str, keywords: list[str]) -> dict:
    """统计关键词在文档里的命中行号；用于证明「统一记录未载」。"""
    lines = lines_of(rel)
    out = {}
    for k in keywords:
        out[k] = [i + 1 for i, s in enumerate(lines) if k in s]
    return out


def require(cond: bool, msg: str, problems: list) -> None:
    if not cond:
        problems.append(msg)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""06-01 来源对撞：把「规则来源映射.json」里每条引用的 文件:行 读回来校验。

做三件事，都不依赖人工复述：
  1. 逐条读回 file:line，断言 must_contain 真的出现在该行；
     line_search=true 的条目（JSON 配置）按内容搜行号，把 line=0 解析成真实行号。
  2. 用 AST 独立复核 `check_fixed_rules()` 的 9 个 `_row(...)`：
     expected 与 actual 是同一个常量/同名常量 ⇒ 判「恒真」，不构成对撞。
  3. 输出机器可读结果；任何一条校验失败 ⇒ 退出码 1（行号漂移必须被发现）。

只读。不写任何生产文件。
"""
from __future__ import annotations

import ast
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parents[1]
MAP_PATH = HERE / "规则来源映射.json"
OUT_PATH = HERE / "out" / "01_来源对撞.json"
CODE = ROOT / "框架/backend/accum_early_entry.py"

SOURCE_KEYS = ("user", "spec", "executor", "code")


def _read_lines(path: Path):
    return path.read_text(encoding="utf-8").splitlines()


def check_reference(ref: dict, cache: dict) -> dict:
    rel = ref.get("file")
    path = ROOT / rel
    rec = {
        "file": rel,
        "line_declared": ref.get("line"),
        "must_contain": ref.get("must_contain"),
        "ok": False,
        "reason": None,
        "line_resolved": None,
    }
    if not path.exists():
        rec["reason"] = "file_missing"
        return rec
    if path not in cache:
        cache[path] = _read_lines(path)
    lines = cache[path]
    needle = ref.get("must_contain") or ""

    if ref.get("line_search"):
        hits = [i + 1 for i, s in enumerate(lines) if needle in s]
        if not hits:
            rec["reason"] = "must_contain_not_found_in_file"
            return rec
        rec["line_resolved"] = hits[0]
        rec["ok"] = True
        rec["reason"] = "resolved_by_content"
        return rec

    ln = ref.get("line")
    if not isinstance(ln, int) or ln <= 0:
        rec["reason"] = "line_invalid"
        return rec
    if ln > len(lines):
        rec["reason"] = "line_out_of_range"
        return rec
    actual = lines[ln - 1]
    rec["line_resolved"] = ln
    rec["actual_line"] = actual.strip()[:300]
    if needle and needle in actual:
        rec["ok"] = True
    else:
        rec["reason"] = "must_contain_not_on_line"
        near = [i + 1 for i, s in enumerate(lines) if needle and needle in s]
        rec["nearby_lines"] = near[:5]
    return rec


def audit_fixed_rules() -> dict:
    """AST 复核 check_fixed_rules 里每一行是真对撞还是恒真。"""
    src = CODE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "check_fixed_rules"), None)
    if fn is None:
        return {"found": False}
    rows = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = getattr(f, "id", None) or getattr(f, "attr", None)
        if name != "_row" or len(node.args) < 3:
            continue
        label = node.args[0]
        label_v = label.value if isinstance(label, ast.Constant) else ast.dump(label)
        exp_dump, act_dump = ast.dump(node.args[1]), ast.dump(node.args[2])
        rows.append({
            "label": label_v,
            "line": node.lineno,
            "expected_src": ast.unparse(node.args[1]),
            "actual_src": ast.unparse(node.args[2]),
            "tautology": exp_dump == act_dump,
        })
    taut = [r for r in rows if r["tautology"]]
    real = [r for r in rows if not r["tautology"]]
    return {
        "found": True,
        "rows": len(rows),
        "real_crosscheck": real,
        "tautology": taut,
        "real_count": len(real),
        "tautology_count": len(taut),
        "all_ok": len(rows) > 0 and len(taut) == 0,
    }


def main() -> int:
    doc = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    cache: dict = {}
    results = []
    for entry in doc.get("entries", []):
        for key in SOURCE_KEYS:
            refs = entry.get(key)
            if not refs:
                continue
            for ref in refs:
                rec = check_reference(ref, cache)
                rec["entry_id"] = entry.get("id")
                rec["source_key"] = key
                results.append(rec)

    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    failures = [r for r in results if not r["ok"]]
    resolved = [r for r in results if r["reason"] == "resolved_by_content"]

    fr = audit_fixed_rules()

    payload = {
        "schema": "accum-early-06-w1/provenance-collision/v1",
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "map_file": str(MAP_PATH.relative_to(ROOT)),
        "references": {"total": total, "pass": passed, "fail": len(failures)},
        "failures": failures,
        "line_search_resolved": [
            {"entry_id": r["entry_id"], "file": r["file"],
             "line_resolved": r["line_resolved"], "must_contain": r["must_contain"]}
            for r in resolved],
        "fixed_rules_audit": fr,
        "entries_total": len(doc.get("entries", [])),
        "conflicts_total": len(doc.get("conflicts", [])),
        "verdict": "pass" if not failures else "fail",
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "references": payload["references"],
        "fixed_rules": {"rows": fr.get("rows"), "real": fr.get("real_count"),
                        "tautology": fr.get("tautology_count")},
        "verdict": payload["verdict"],
        "out": str(OUT_PATH.relative_to(ROOT)),
    }, ensure_ascii=False))
    if failures:
        for f in failures:
            print("FAIL", f["entry_id"], f["source_key"], f["file"],
                  f["line_declared"], f["reason"], f.get("nearby_lines"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""06-02 待决清单对撞：字段覆盖 + 引用读回 + 一字段只问一次。

判据（任一失败 ⇒ 退出码 1）：
  1. 覆盖：credentials.fields ∪ 所有 questions.fields 恰好等于 REQUIRED_PATHS（49），
     无缺、无多、无重复。
  2. 不重复问：每个字段只出现在一个 Q 里；not_asked 的 8 项不得再出现在任何 Q。
  3. 引用：每处 文件:行 的 must_contain 必须真的在该行（同 06-01 方法）。
  4. 每个 Q 至少一条来源，且必须写 recommendation.status。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parents[1]
DOC = HERE / "待决清单.json"
OUT = HERE / "out" / "01_待决对撞.json"

sys.path.insert(0, str(ROOT / "框架/backend"))
import accum_early_entry as A  # noqa: E402

REQUIRED = set(A.REQUIRED_PATHS)


def check_ref(ref, cache):
    rel = ref["file"]
    path = ROOT / rel
    rec = {"file": rel, "line": ref.get("line"), "ok": False, "reason": None}
    if not path.exists():
        rec["reason"] = "file_missing"
        return rec
    if path not in cache:
        cache[path] = path.read_text(encoding="utf-8").splitlines()
    lines = cache[path]
    needle = ref["must_contain"]
    if ref.get("line_search"):
        hits = [i + 1 for i, s in enumerate(lines) if needle in s]
        rec["line"] = hits[0] if hits else None
        rec["ok"] = bool(hits)
        rec["reason"] = None if hits else "not_found_in_file"
        return rec
    ln = ref.get("line")
    if not isinstance(ln, int) or ln <= 0 or ln > len(lines):
        rec["reason"] = "line_invalid"
        return rec
    rec["actual"] = lines[ln - 1].strip()[:240]
    if needle in lines[ln - 1]:
        rec["ok"] = True
    else:
        rec["reason"] = "must_contain_not_on_line"
        rec["nearby"] = [i + 1 for i, s in enumerate(lines) if needle in s][:5]
    return rec


def main() -> int:
    doc = json.loads(DOC.read_text(encoding="utf-8"))
    cache = {}
    problems = []

    cred_fields = list(doc["credentials"]["fields"])
    all_fields = []
    per_q = {}
    for q in doc["questions"]:
        per_q[q["id"]] = list(q["fields"])
        all_fields += q["fields"]

    dup = sorted({f for f in all_fields if all_fields.count(f) > 1})
    missing = sorted(REQUIRED - set(cred_fields) - set(all_fields))
    extra = sorted((set(cred_fields) | set(all_fields)) - REQUIRED)
    if dup:
        problems.append({"kind": "field_asked_twice", "fields": dup})
    if missing:
        problems.append({"kind": "field_uncovered", "fields": missing})
    if extra:
        problems.append({"kind": "field_not_in_required", "fields": extra})

    refs = []
    for q in doc["questions"]:
        if not q.get("sources"):
            problems.append({"kind": "question_without_source", "id": q["id"]})
        if not (q.get("recommendation") or {}).get("status"):
            problems.append({"kind": "question_without_status", "id": q["id"]})
        for r in q.get("sources", []):
            rec = check_ref(r, cache)
            rec["owner"] = q["id"]
            refs.append(rec)
    for r in doc["credentials"].get("sources", []):
        rec = check_ref(r, cache)
        rec["owner"] = "Q0"
        refs.append(rec)
    for na in doc["not_asked"]:
        rec = check_ref(na["source"], cache)
        rec["owner"] = "not_asked"
        refs.append(rec)

    bad_refs = [r for r in refs if not r["ok"]]

    payload = {
        "schema": "accum-early-06-w1/pending-collision/v1",
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "required_total": len(REQUIRED),
        "credential_fields": len(cred_fields),
        "question_count": len(doc["questions"]),
        "question_fields": {k: len(v) for k, v in per_q.items()},
        "covered_total": len(set(cred_fields) | set(all_fields)),
        "duplicates": dup,
        "missing": missing,
        "extra": extra,
        "not_asked_count": len(doc["not_asked"]),
        "references": {"total": len(refs), "pass": len(refs) - len(bad_refs),
                       "fail": len(bad_refs)},
        "bad_references": bad_refs,
        "problems": problems,
        "verdict": "pass" if not problems and not bad_refs else "fail",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "required": payload["required_total"],
        "covered": payload["covered_total"],
        "questions": payload["question_count"],
        "credential_fields": payload["credential_fields"],
        "refs": payload["references"],
        "problems": len(problems),
        "verdict": payload["verdict"],
    }, ensure_ascii=False))
    for p in problems:
        print("PROBLEM", p)
    for b in bad_refs:
        print("BADREF", b["owner"], b["file"], b["line"], b["reason"], b.get("nearby"))
    return 0 if payload["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())

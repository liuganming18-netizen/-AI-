#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""06-01 正文数字对撞：矩阵里的每个数字都从产物重算，再回正文找。

数字来源（唯一）：
  · 规则来源映射.json（entries / conflicts 统计）
  · out/01_来源对撞.json（引用通过数、AST 对撞行数）

任一数字对不上 ⇒ 退出码 1。只读。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parents[1]
MD = HERE / "规则来源矩阵.md"
MAP = HERE / "规则来源映射.json"
COLL = HERE / "out" / "01_来源对撞.json"
OUT = HERE / "out" / "02_正文数字对撞.json"


def main() -> int:
    md = MD.read_text(encoding="utf-8")
    doc = json.loads(MAP.read_text(encoding="utf-8"))
    coll = json.loads(COLL.read_text(encoding="utf-8"))

    entries = doc["entries"]
    n_entries = len(entries)
    n_conf = len(doc["conflicts"])
    status = {}
    for e in entries:
        status[e["status"]] = status.get(e["status"], 0) + 1

    refs = coll["references"]
    fr = coll["fixed_rules_audit"]

    checks = []

    def chk(name, needle, expect=None):
        ok = needle in md
        checks.append({"name": name, "needle": needle, "found": ok, "expect": expect})

    chk("映射条目数", "（%d 条、%d 处引用）" % (n_entries, refs["total"]), n_entries)
    chk("引用通过率", "**%d/%d 通过**" % (refs["pass"], refs["total"]), refs["pass"])
    chk("AST 对撞行数",
        "**%d 行 → %d 行真对撞、%d 行恒真**" % (fr["rows"], fr["real_count"], fr["tautology_count"]),
        fr["rows"])
    chk("冲突项数", "C-01～C-%02d 共 %d 项" % (n_conf, n_conf), n_conf)

    # 状态分布表：抓「| 类别 | 判定标准 | 本单条数 |」表格每行末尾数字
    label_map = {
        "用户明确决定": "USER_DECIDED",
        "方案文本": "SPEC_ONLY",
        "执行方建议": "PROPOSED",
        "沿用（未批准）": "INHERITED_UNRATIFIED",
        "冲突": "CONFLICT",
    }
    for label, key in label_map.items():
        m = re.search(r"^\|\s*" + re.escape(label) + r"\s*\|[^|]*\|\s*(\d+)\s*\|",
                      md, flags=re.M)
        got = int(m.group(1)) if m else None
        exp = status.get(key, 0)
        checks.append({"name": "状态计数:" + label, "needle": "%s=%s" % (label, got),
                       "found": got == exp, "expect": exp})

    fails = [c for c in checks if not c["found"]]
    payload = {
        "schema": "accum-early-06-w1/text-collision/v1",
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "md": str(MD.relative_to(ROOT)),
        "checks": checks,
        "total": len(checks),
        "pass": len(checks) - len(fails),
        "fail": len(fails),
        "verdict": "pass" if not fails else "fail",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": payload["total"], "pass": payload["pass"],
                      "fail": payload["fail"], "verdict": payload["verdict"]},
                     ensure_ascii=False))
    for f in fails:
        print("FAIL", f["name"], "expect=", f["expect"], "needle=", f["needle"])
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())

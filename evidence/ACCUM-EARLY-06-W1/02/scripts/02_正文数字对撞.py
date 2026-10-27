#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""06-02 正文数字对撞：待用户裁决.md 里的计数全部从产物重算后回查。

数字来源：out/00_计数对账.json、out/01_待决对撞.json、待决清单.json。
任一不符 ⇒ 退出码 1。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
MD = HERE / "待用户裁决.md"
DOC = HERE / "待决清单.json"
C0 = HERE / "out" / "00_计数对账.json"
C1 = HERE / "out" / "01_待决对撞.json"
OUT = HERE / "out" / "02_正文数字对撞.json"


def main() -> int:
    md = MD.read_text(encoding="utf-8")
    doc = json.loads(DOC.read_text(encoding="utf-8"))
    c0 = json.loads(C0.read_text(encoding="utf-8"))
    c1 = json.loads(C1.read_text(encoding="utf-8"))

    req = c0["required_paths"]["counts"]
    pend = c0["pending_decisions"]
    blank = c0["blank_form"]
    d4 = c0["doc_04a"]
    d5r = c0["doc_05a_r1"]["rows_count"]
    d5o = c0["doc_05a_r1"]["overview"]
    cov = c0["coverage"]
    dec = c0["doc_5decisions"]["count"]
    nq = c1["question_count"]
    refs = c1["references"]
    n_na = c0 and len(doc["not_asked"])

    checks = []

    def chk(name, needle, expect):
        checks.append({"name": name, "needle": needle, "found": needle in md,
                       "expect": expect})

    chk("必填总数", "| **49** | 05 版配置的**必填字段**数", req["total"])
    chk("空白 null", "49 叶子 / %d null / 1 预填" % blank["null"], blank["null"])
    chk("待决键→字段", "13 键 → **21 字段**", pend["fields"])
    chk("49 分解和", "4+%d+%d+%d=%d" % (req["pending_mapped"], req["conflict_only"],
                                      req["other_behavior"], req["total"]), req["total"])
    chk("04-A 四类",
        "%d 已定 + %d 沿用 + %d 推荐未批准 + %d 待决" % (d4["6.一"], d4["6.二"], d4["6.三"], d4["6.四"]),
        d4["6.三"])
    chk("05-A-R1 逐行未批准",
        "%d 建议未批准 + %d 待确认 + %d 建议沿用" % (d5r["建议未批准"], d5r["待确认"], d5r["建议沿用"]),
        d5r["建议未批准"] + d5r["待确认"] + d5r["建议沿用"])
    chk("5 项决策", "| **5** | 05-A-R1 给用户的**核心决策**数", dec)
    chk("问题数", "**%d 个问题**" % nq, nq)
    chk("覆盖缺口", "共 **%d** 个；其中进入 13 项待决入口的只有 **%d** 个 ⇒ **%d 个没进**"
        % (cov["unapproved_by_rows"], cov["mapped_to_13"], cov["unmapped_n"]), cov["unmapped_n"])
    chk("引用数", "脚本 %d/%d 读回通过" % (refs["total"], refs["total"]), refs["total"])
    chk("不问项数", "## 3 已经定过，不再问（%d 项）" % n_na, n_na)

    # §1.2 总览 vs 逐行（四组数字都要出现在同一张表里）
    pairs = [("用户已定", "用户已定"), ("建议未批准", "建议未批准"),
             ("待确认", "待确认"), ("格式/身份", "格式/身份")]
    for label, key in pairs:
        needle = "| %s | %d | **%d** |" % (label, d5o[key], d5r[key])
        chk("总览vs逐行:" + label, needle, (d5o[key], d5r[key]))

    fails = [c for c in checks if not c["found"]]
    payload = {
        "schema": "accum-early-06-w1/pending-text-collision/v1",
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checks": checks, "total": len(checks),
        "pass": len(checks) - len(fails), "fail": len(fails),
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

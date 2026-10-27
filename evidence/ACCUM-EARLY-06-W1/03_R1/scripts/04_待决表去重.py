#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""04 待决表去重与三类标注（工单 D 第 3/4 条）

把 06-01（31 条规则项 + 8 项冲突）与 06-02（12 问）折叠成 **21 项**，一项一决策，分三类：
  P 参数待决（12）／E 证据缺口（6）／G 明确工程问题（3）

判据（任一失败 ⇒ 退出码 1）：
  A. 21 项、P/E/G = 12/6/3，与 `counts_expected` 一致
  B. 12 个 Q 各被引用恰一次；P 项字段并集 = 49 必填 − 4 凭证 = 45，且**字段不重复**
  C. 8 个冲突编号（C-01…C-08）全部折叠进某一项（不残留独立冲突行）
  D. 06-01 里 canonical 非空的条目，其 canonical 目标必须真的存在于本表
  E. 每项都有 approval_evidence ∈ {E1,E2,E3} 与 must_not_write
  F. P-09（冷却）不得代选：不得出现 selected/value/chosen 字段，且 must_not_write 必须点名「代选/规定」
  G. G 项 isolation_testable=true；P 项不得声明可隔离推进
  H. E/G 项的 refs read-back 全部命中
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w1r1 as W  # noqa: E402

TABLE = W.HERE / "待决表_R1.json"
CORR = W.HERE / "校正表.json"
OLD_PENDING = W.OLD02 / "待决清单.json"
OLD_MAP = W.OLD01 / "规则来源映射.json"


def main() -> int:
    table = W.load_json(TABLE)
    corr = W.load_json(CORR)
    old = W.load_json(OLD_PENDING)
    old_map = W.load_json(OLD_MAP)
    problems: list = []

    items = table["items"]
    exp = table["counts_expected"]
    W.require(len(items) == exp["items_total"], f"项数 {len(items)} != {exp['items_total']}", problems)
    by_class = {}
    for it in items:
        by_class[it["class"]] = by_class.get(it["class"], 0) + 1
    for k, v in (("P", exp["P"]), ("E", exp["E"]), ("G", exp["G"])):
        W.require(by_class.get(k) == v, f"{k} 类项数 {by_class.get(k)} != {v}", problems)

    # B：Q 覆盖 + 字段不重复
    q_fields = {q["id"]: list(q["fields"]) for q in old["questions"]}
    q_used = [it["q"] for it in items if it.get("q")]
    W.require(sorted(q_used) == sorted(q_fields), f"Q 覆盖不全或重复：{sorted(q_used)}", problems)
    seen = {}
    dup = []
    for it in items:
        if it["class"] != "P":
            continue
        for f in (it.get("fields_override") if "fields_override" in it else q_fields[it["q"]]):
            if f in seen:
                dup.append((f, seen[f], it["id"]))
            seen[f] = it["id"]
    W.require(not dup, f"字段被两个参数项重复询问：{dup}", problems)

    import accum_early_entry as A
    required = set(A.REQUIRED_PATHS)
    meta = {"schema_version", "kind", "approved", "approval_ref"}
    W.require(set(seen) == required - meta,
              f"P 字段并集不等于「49 必填 − 4 凭证」：缺 {sorted((required - meta) - set(seen))} "
              f"多 {sorted(set(seen) - (required - meta))}", problems)
    W.require(len(seen) + len(meta) == exp["required_total"],
              f"字段总数 {len(seen) + len(meta)} != {exp['required_total']}", problems)

    # C：冲突全部折叠
    item_ids = {it["id"] for it in items}
    all_old_ids = {o for it in items for o in it.get("old_ids", [])}
    for c in corr["conflict_map"]:
        W.require(c["c"] in all_old_ids, f"冲突 {c['c']} 未折叠进任何项", problems)
        for target in c["canonical"].split("/"):
            W.require(target in item_ids, f"冲突 {c['c']} 指向的 {target} 不存在", problems)
    W.require(len([c for c in corr["conflict_map"]]) == 8, "conflict_map 不是 8 项", problems)

    # D：06-01 里 canonical 非空的条目必须有落点
    for row in corr["rows"]:
        if row.get("canonical"):
            W.require(row["canonical"] in item_ids,
                      f"{row['id']} 的 canonical {row['canonical']} 不在待决表里", problems)

    # E/F/G：每项自带标注
    refs = []
    for it in items:
        W.require(it.get("approval_evidence") in ("E1", "E2", "E3"),
                  f"{it['id']} approval_evidence 缺失或非法", problems)
        W.require(bool(it.get("must_not_write")), f"{it['id']} 缺 must_not_write", problems)
        if it["class"] == "G":
            W.require(it.get("isolation_testable") is True, f"{it['id']} G 项未声明可隔离验证", problems)
        if it["class"] == "P":
            W.require("isolation_testable" not in it, f"{it['id']} P 项不得声明可隔离推进", problems)
        for bad in ("selected", "chosen", "decided_value"):
            W.require(bad not in it, f"{it['id']} 出现代选字段 {bad}", problems)
        for ref in it.get("refs", []):
            rec = W.check_ref(ref)
            rec["owner"] = it["id"]
            refs.append(rec)
            W.require(rec["ok"], f"{it['id']} 引用读回失败 {ref}", problems)

    # F：冷却不得代选
    p09 = next(i for i in items if i["id"] == "P-09")
    W.require(any(w in p09["must_not_write"] for w in ("代选", "规定")),
              "P-09 的 must_not_write 未点名「代选/规定」", problems)
    W.require("24" in json.dumps(p09, ensure_ascii=False), "P-09 未提到 24 小时这个建议值", problems)
    W.require("fixture" in json.dumps(p09, ensure_ascii=False), "P-09 未记录当前 fixture 状态", problems)

    bad_refs = [r for r in refs if not r["ok"]]

    # I：正文（待决表_R1.md）里写的「字段数」必须等于 JSON 实际字段数（防止正文手抄漂移）
    md_path = W.HERE / "待决表_R1.md"
    md_check = []
    if md_path.exists():
        md = md_path.read_text(encoding="utf-8")
        for it in items:
            if it["class"] != "P":
                continue
            n = len(it.get("fields_override") if "fields_override" in it else q_fields[it["q"]])
            m = re.search(r"\|\s*%s\s*\|[^|]*\|\s*(\d+)\s*\|" % re.escape(it["id"]), md)
            got = int(m.group(1)) if m else None
            md_check.append({"id": it["id"], "md": got, "json": n, "ok": got == n})
            W.require(got == n, f"{it['id']} 正文写的字段数 {got} != JSON {n}", problems)
    else:
        problems.append("缺 待决表_R1.md，无法核对正文字段数")

    payload = {
        "schema": "accum-early-06-w1-r1/pending-dedupe/v1",
        "checked_at_utc": W.now_iso(),
        "input": {
            "pending_table_sha256": W.sha256_file(TABLE),
            "correction_table_sha256": W.sha256_file(CORR),
            "old_pending_sha256": W.sha256_file(OLD_PENDING),
            "old_rule_map_sha256": W.sha256_file(OLD_MAP),
            "required_total": len(required),
            "credential_fields": sorted(meta),
        },
        "md_field_count_check": md_check,
        "counts": {
            "items": len(items),
            "by_class": by_class,
            "p_fields": len(seen),
            "p_fields_plus_credentials": len(seen) + len(meta),
            "questions_covered": len(q_used),
            "conflicts_folded": len(corr["conflict_map"]),
        },
        "field_owner": seen,
        "questions": {it["q"]: it["id"] for it in items if it.get("q")},
        "conflicts": {c["c"]: c["canonical"] for c in corr["conflict_map"]},
        "references": {"total": len(refs), "pass": len(refs) - len(bad_refs), "fail": len(bad_refs)},
        "bad_references": bad_refs,
        "problems": problems,
        "verdict": "pass" if not problems else "fail",
        "statement": ("31 条规则项 + 8 项冲突 + 12 问 → 21 项待决（12 参数 / 6 证据 / 3 工程）；"
                      "45 个行为字段每个只归属一个参数项，加 4 个凭证字段合计 49。"),
    }
    W.dump("04_pending_dedupe.json", payload)

    print(json.dumps({
        "items": len(items), "by_class": by_class,
        "p_fields": len(seen), "questions": len(q_used), "conflicts": len(corr["conflict_map"]),
        "refs": payload["references"], "problems": len(problems), "verdict": payload["verdict"],
    }, ensure_ascii=False))
    for p in problems:
        print("PROBLEM", p)
    return 0 if payload["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

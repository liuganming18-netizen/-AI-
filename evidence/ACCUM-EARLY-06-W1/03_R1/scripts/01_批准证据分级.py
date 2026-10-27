#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""01 批准证据分级校正（工单 D 第 1 条）

把 06-01 的 31 条规则项从「一个标签」拆成「证据等级 × 行为效力」两轴，并逐项复核：

  E1 用户明确决定   ：统一记录有正面原话，且 read-back 命中 ⇒ 继续执行、不再询问
  E2 用户明确未批准 ：统一记录有否定性原话（逐项点名）      ⇒ 不得实施、不得代选
  E3 证据缺口       ：统一记录关键词 0 命中 + 本轮未取证原始消息 ⇒ 只写「未找到批准证据」

判据（任一失败 ⇒ 退出码 1）：
  A. 31 个 id 与 06-01 `规则来源映射.json` 的 entries **逐个同序**相等（不漏项、不换项）
  B. 每行 level 与 new_label 的句式一致；且不得出现该等级的禁用词
  C. E1/E2 每行的 refs 必须 read-back 成功；E2 的命中行必须含否定词
  D. E3 每行的 record_keywords 在统一记录里 **0 命中**（用「0 命中」证明「记录未载」，不靠印象）
  E. behavior_in_force=false 的行，disposition 必须写明「待工程修正」或「未生效」
     （防止把「缺证」写成「停用」——这是 06-01/06-02 被退回的同一个错）
  F. 每条 wording_fixes 的 old 文本必须真的在所指行上（防止树稻草人）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w1r1 as W  # noqa: E402

DOC = W.HERE / "校正表.json"
OLD_MAP = W.OLD01 / "规则来源映射.json"
NEG_WORDS = ("不得", "不设", "不等于", "未批准", "否决")
LABEL_PREFIX = {
    "E1": "用户已定（原话见",
    "E2": "用户明确未批准（原话：",
    "E3": "统一记录未载；本轮未找到批准证据",
}


def main() -> int:
    corr = W.load_json(DOC)
    old = W.load_json(OLD_MAP)
    problems: list = []

    rows = corr["rows"]
    old_ids = [e["id"] for e in old["entries"]]
    new_ids = [r["id"] for r in rows]
    W.require(new_ids == old_ids, f"id 序列与 06-01 不一致：{new_ids} vs {old_ids}", problems)

    scan = W.scan_keywords(W.DECISION, sorted({k for r in rows for k in r.get("record_keywords", [])}))
    # A 段：统一记录里「必须 0 命中」的词
    zero_proof = {}
    for k in corr["record_scan"]["must_be_zero"]:
        hits = W.scan_keywords(W.DECISION, [k])[k]
        zero_proof[k] = hits
        W.require(not hits, f"关键词 {k!r} 本应 0 命中，实际命中行 {hits}", problems)
    # 反向：已知有命中的词，行号必须与登记一致（防止文档被改动后结论失效）
    for k, expect in corr["record_scan"]["expected_hits"].items():
        hits = W.scan_keywords(W.DECISION, [k])[k]
        W.require(hits == expect, f"关键词 {k!r} 命中行 {hits} != 登记 {expect}", problems)

    refs_out, per_level, changed, not_in_force = [], {"E1": 0, "E2": 0, "E3": 0}, [], []
    for r in rows:
        lv = r["level"]
        W.require(lv in LABEL_PREFIX, f"{r['id']} 非法等级 {lv}", problems)
        per_level[lv] = per_level.get(lv, 0) + 1

        # B 段：句式与禁用词
        if lv in LABEL_PREFIX:
            W.require(r["new_label"].startswith(LABEL_PREFIX[lv]),
                      f"{r['id']} new_label 句式与 {lv} 不符：{r['new_label']}", problems)
            for bad in corr["evidence_levels"][lv]["forbidden_wording"]:
                W.require(bad not in r["new_label"],
                          f"{r['id']} ({lv}) new_label 含禁用词 {bad!r}", problems)

        # C 段：引用读回
        for ref in r.get("refs", []):
            rec = W.check_ref(ref)
            rec["owner"] = r["id"]
            rec["purpose"] = "evidence"
            refs_out.append(rec)
            W.require(rec["ok"], f"{r['id']} 引用读回失败 {ref}", problems)
            if lv == "E2" and rec["ok"]:
                W.require(any(w in (rec.get("actual") or "") for w in NEG_WORDS),
                          f"{r['id']} 标 E2 但命中行无否定词：{rec.get('actual')}", problems)
        if lv in ("E1", "E2"):
            W.require(bool(r.get("refs")), f"{r['id']} 标 {lv} 却没有任何引用", problems)

        # E3 的类级覆盖引用
        cc = r.get("class_covered")
        if cc:
            rec = W.check_ref(cc["ref"])
            rec["owner"] = r["id"]
            rec["purpose"] = "class_covered"
            refs_out.append(rec)
            W.require(rec["ok"], f"{r['id']} 类级覆盖引用读回失败", problems)
            W.require(cc["level"] == "E2", f"{r['id']} 类级覆盖等级应为 E2", problems)

        # D 段：E3 的「记录未载」必须用 0 命中证明
        if lv == "E3":
            for k in r.get("record_keywords", []):
                W.require(not scan.get(k), f"{r['id']} 标 E3 但 {k!r} 在统一记录命中 {scan.get(k)}", problems)

        # E 段：不得把缺证写成停用
        if r["behavior_in_force"] is False:
            d = r["disposition"]
            W.require(("待工程修正" in d) or ("未生效" in d),
                      f"{r['id']} behavior_in_force=false 但 disposition 未说明原因：{d}", problems)
            not_in_force.append({"id": r["id"], "disposition": d})

        if r["old_label"] != r["new_label"]:
            changed.append({"id": r["id"], "old_label": r["old_label"], "new_label": r["new_label"]})

    # F 段：wording_fixes 的 old 文本必须真在所指行
    fixes_out = []
    for f in corr["wording_fixes"]:
        rec = W.check_ref({"file": f["file"], "line": f["line"], "must_contain": f["old"]})
        rec["owner"] = f["file"]
        rec["purpose"] = "wording_fix"
        refs_out.append(rec)
        fixes_out.append({**f, "old_text_verified": rec["ok"]})
        W.require(rec["ok"], f"wording_fix 未能在 {f['file']}:{f['line']} 读到原文：{f['old'][:40]}", problems)

    bad_refs = [r for r in refs_out if not r["ok"]]
    payload = {
        "schema": "accum-early-06-w1-r1/approval-evidence/v1",
        "checked_at_utc": W.now_iso(),
        "input": {
            "correction_table": "校正表.json",
            "correction_table_sha256": W.sha256_file(DOC),
            "old_rule_map": "evidence/ACCUM-EARLY-06-W1/01/规则来源映射.json",
            "old_rule_map_sha256": W.sha256_file(OLD_MAP),
            "decision_doc": W.DECISION,
            "decision_doc_sha256": W.sha256_file(W.ROOT / W.DECISION),
            "raw_user_messages_retrieved": False,
            "raw_user_messages_note": "本轮未读取主聊天 01a17396-e343-7202-908d-d01292e46af7 的原始消息；"
                                      "所有「未找到」均带此限定。",
        },
        "record_scan": {
            "zero_hit_keywords": {k: v for k, v in zero_proof.items()},
            "expected_hits": {k: W.scan_keywords(W.DECISION, [k])[k]
                              for k in corr["record_scan"]["expected_hits"]},
        },
        "counts": {
            "rows": len(rows),
            "by_level": per_level,
            "changed_label_rows": len(changed),
            "behavior_not_in_force": len(not_in_force),
            "wording_fixes": len(corr["wording_fixes"]),
        },
        "changed": changed,
        "behavior_not_in_force": not_in_force,
        "wording_fixes": fixes_out,
        "references": {"total": len(refs_out), "pass": len(refs_out) - len(bad_refs), "fail": len(bad_refs)},
        "bad_references": bad_refs,
        "problems": problems,
        "verdict": "pass" if not problems else "fail",
    }
    W.dump("01_approval_evidence.json", payload)

    print(json.dumps({
        "rows": len(rows),
        "by_level": per_level,
        "changed_label_rows": len(changed),
        "not_in_force": len(not_in_force),
        "refs": payload["references"],
        "problems": len(problems),
        "verdict": payload["verdict"],
    }, ensure_ascii=False))
    for p in problems:
        print("PROBLEM", p)
    return 0 if payload["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

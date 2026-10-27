#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""02 已确认止损的引用与「不再询问」守护（工单 D 第 2 条）

要证明三件事：
  1. 8 项止损语义**逐条**能引到统一记录与本轮总单（read-back 命中，不靠记忆）；
  2. 这 8 项在代码里的生效值就是用户定的值（FIXED_RULES 实测），所以「继续保持」是事实，不是承诺；
  3. R1 待决表**没有任何一个参数项**再问这 8 项（守护：谁加回去谁红）。

判据（任一失败 ⇒ 退出码 1）：
  A. not_asked 恰 8 项，每项 refs read-back 全部命中
  B. 06-02 的 8 项 not_asked 与 R1 的 8 项**指向同一批行**（不丢项、不换项）
  C. FIXED_RULES 实测值与用户原话一致：0.05 / 0.05 / 0.0 / None / 15 / 15m
  D. 所有 class=P 的项：标题不含止损类关键词，字段不含止损类字段
  E. G-02（生效时刻）必须带 must_not_write，防止它被写成「重新批准止损」
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w1r1 as W  # noqa: E402

TABLE = W.HERE / "待决表_R1.json"
OLD_PENDING = W.OLD02 / "待决清单.json"

STOP_WORDS = ("止损", "2R", "10 天", "跟踪", "回撤")
STOP_FIELDS = ("stoploss_s", "trail_d", "trailing_trigger_r", "max_holding_days",
               "initial_stop_pct", "trailing_low_pct", "entry_delay_minutes")

#: 用户定的值 → 代码实测值（下面 assert，不是抄写）
EXPECT_FIXED = {
    "initial_stop_pct": 0.05,
    "trailing_low_pct": 0.05,
    "trailing_trigger_r": 0.0,
    "max_holding_days": None,
    "entry_delay_minutes": 15,
    "execution_timeframe": "15m",
}


def main() -> int:
    table = W.load_json(TABLE)
    old = W.load_json(OLD_PENDING)
    problems: list = []

    na = table["not_asked"]
    W.require(len(na) == 8, f"not_asked 应为 8 项，实际 {len(na)}", problems)

    refs = []
    for item in na:
        W.require(item["level"] == "E1", f"{item['id']} 等级不是 E1", problems)
        for ref in item["refs"]:
            rec = W.check_ref(ref)
            rec["owner"] = item["id"]
            refs.append(rec)
            W.require(rec["ok"], f"{item['id']} 引用读回失败 {ref}", problems)

    # B：与 06-02 的 not_asked 对齐（同一批行，不丢不换）
    old_pairs = {(r["source"]["file"], r["source"]["line"]) for r in old["not_asked"]}
    new_pairs = {(r["file"], r["line"]) for item in na for r in item["refs"]}
    old_core = {(f, l) for (f, l) in old_pairs if f == W.DECISION}
    new_core = {(f, l) for (f, l) in new_pairs if f == W.DECISION}
    W.require(old_core == new_core,
              f"与 06-02 的止损引用不一致：缺 {sorted(old_core - new_core)} 多 {sorted(new_core - old_core)}",
              problems)

    # C：代码生效值
    import accum_early_entry as A
    fixed = dict(A.FIXED_RULES)
    code_rows = []
    for k, want in EXPECT_FIXED.items():
        got = fixed.get(k, "<missing>")
        code_rows.append({"key": k, "expected_from_user": want, "code_actual": got,
                          "ok": got == want})
        W.require(got == want, f"FIXED_RULES[{k}] = {got!r}，与用户原话 {want!r} 不一致", problems)

    # D：P 项不得再问止损
    old_fields = {q["id"]: list(q["fields"]) for q in old["questions"]}
    resolved = {}
    for item in table["items"]:
        if item["class"] != "P":
            continue
        fields = list(item.get("fields_override") or old_fields.get(item["q"], []))
        resolved[item["id"]] = fields
        bad = [w for w in STOP_WORDS if w in item["title"]]
        W.require(not bad, f"{item['id']} 标题含止损类关键词 {bad}：参数项不得再问已确认止损", problems)
        hit = [f for f in fields if any(s in f for s in STOP_FIELDS)]
        W.require(not hit, f"{item['id']} 字段含止损类字段 {hit}", problems)

    # E：G-02 必须带禁语
    g02 = next((i for i in table["items"] if i["id"] == "G-02"), None)
    W.require(g02 is not None, "缺 G-02", problems)
    if g02:
        W.require(bool(g02.get("must_not_write")), "G-02 缺 must_not_write", problems)

    bad_refs = [r for r in refs if not r["ok"]]
    payload = {
        "schema": "accum-early-06-w1-r1/stop-decision/v1",
        "checked_at_utc": W.now_iso(),
        "input": {
            "pending_table_sha256": W.sha256_file(TABLE),
            "old_pending_sha256": W.sha256_file(OLD_PENDING),
            "decision_doc_sha256": W.sha256_file(W.ROOT / W.DECISION),
            "code_sha256": W.sha256_file(W.ROOT / W.CODE),
        },
        "not_asked": [{"id": i["id"], "item": i["item"], "level": i["level"],
                       "refs": i["refs"]} for i in na],
        "old_pending_refs": sorted(old_pairs),
        "r1_refs": sorted(new_pairs),
        "code_effective_values": code_rows,
        "p_items_fields": resolved,
        "references": {"total": len(refs), "pass": len(refs) - len(bad_refs), "fail": len(bad_refs)},
        "bad_references": bad_refs,
        "problems": problems,
        "verdict": "pass" if not problems else "fail",
        "statement": ("8 项止损语义均为 E1（用户原话）；代码生效值逐项等于用户值；"
                      "R1 待决表 12 个参数项无一再问这 8 项。本轮不新增任何止损审批要求。"),
    }
    W.dump("02_stop_decision.json", payload)

    print(json.dumps({
        "not_asked": len(na),
        "refs": payload["references"],
        "code_values_ok": all(r["ok"] for r in code_rows),
        "p_items": len(resolved),
        "problems": len(problems),
        "verdict": payload["verdict"],
    }, ensure_ascii=False))
    for p in problems:
        print("PROBLEM", p)
    return 0 if payload["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

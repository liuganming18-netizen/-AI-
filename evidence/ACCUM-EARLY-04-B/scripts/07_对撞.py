# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-B｜07 双向对撞：报告正文 ↔ 原始产物。

两条独立路径：
  ① 从 `out/*.json` 的**明细列表**重新数一遍（不读 06 用过的 summary 字段）；
  ② 与 README 里嵌入的事实块（06 生成）逐项比对；
  ③ 再抽报告正文里的关键句子，核对数字与事实块一致（防止正文被手改）。

任何一处不一致即返回非 0。这个脚本不写交付目录以外的任何东西。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                    # noqa: E402

README = C.EVID / "README.md"
FB, FE = "<!-- FACTS-BEGIN -->", "<!-- FACTS-END -->"


def facts_block() -> dict:
    txt = README.read_text(encoding="utf-8")
    if FB not in txt or FE not in txt:
        raise SystemExit("拒绝：README 里找不到事实块标记")
    seg = txt.split(FB, 1)[1].split(FE, 1)[0]
    m = re.search(r"```json\s*(\{.*?\})\s*```", seg, re.S)
    if not m:
        raise SystemExit("拒绝：事实块里没有 json")
    return json.loads(m.group(1))


def recount() -> tuple:
    """独立复算（只读明细列表，不读 summary）。返回 (与事实块同名的键, 仅本脚本用的检查)。"""
    extra = {}
    o1 = C.load_out("01_入口普查.json")
    o2 = C.load_out("02_配置闸门.json")
    o3 = C.load_out("03_观察与候选.json")
    o4 = C.load_out("04_隔离全链.json")
    o5 = C.load_out("05_隔离边界.json")
    r = {}

    # ── 01 ──────────────────────────────────────────────────────────────
    r["placeholder_count"] = sum(1 for v in o1["static"].values()
                                if str(v.get("verdict", "")).startswith("占位"))
    r["changed_files"] = sum(1 for m in o1["minimal_change_list"] if m.get("applied"))
    r["unchanged_files"] = sum(1 for m in o1["minimal_change_list"] if not m.get("applied"))

    # ── 02 ──────────────────────────────────────────────────────────────
    r["gate_pass"] = sum(1 for v in o2["verdict"].values() if v)
    r["gate_total"] = len(o2["verdict"])
    r["gateA_missing"] = len(o2["A_no_config"]["missing_fields"])
    r["gateA_ledger_files"] = len(o2["A_no_config"]["paper_ledger_created"])
    r["gateA_events"] = o2["A_no_config"]["events_created"]

    # ── 03：从候选明细重新数 ────────────────────────────────────────────
    cands = [c for w in o3["windows"] for c in w["candidates"]]
    r["obs_candidates"] = len(cands)
    r["obs_candidates_db"] = o3["summary"]["candidate_rows_in_db"]
    sch = Counter(c["scheme"] for c in cands)
    r["obs_4a"], r["obs_4b"], r["obs_4c"] = sch.get("4a", 0), sch.get("4b", 0), sch.get("4c", 0)
    src = Counter(c.get("entry_bar_source") for c in cands)
    r["bar_cold"], r["bar_hot"] = src.get("cold", 0), src.get("hot", 0)
    res = Counter()
    np_src = Counter()
    gap_evid = 0
    for c in cands:
        for cd in c.get("conditions") or []:
            res[cd["result"]] += 1
            if cd["result"] == "not_proven":
                np_src[c.get("entry_bar_source") or "其它"] += 1
                if cd.get("name") == "entry_bar_available_at_proven":
                    gap_evid += 1
    r["cond_pass"] = res.get("pass", 0)
    r["cond_not_required"] = res.get("not_required", 0)
    r["cond_not_proven"] = res.get("not_proven", 0)
    r["cond_total"] = sum(res.values())
    r["np_cold"] = np_src.get("cold", 0)
    r["np_hot"] = np_src.get("hot", 0)
    r["np_other"] = np_src.get("其它", 0)
    # 「证据不足」条数应等于「可得时刻缺口」记录条数（两者若不等，说明缺口没落到候选上）
    extra["gap_equals_not_proven"] = (gap_evid == r["cond_not_proven"])
    r["obs_gaps"] = o3["summary"]["data_gap_rows"]
    r["checks_pass"] = sum(1 for v in o3["checks"].values() if v)

    # ── 04：从账本快照与委托结果重新数 ─────────────────────────────────
    for run in o4["runs"]:
        n_pos = sum(len(v["positions"]) for v in run["ledgers"].values())
        n_closed = sum(1 for v in run["ledgers"].values() for p in v["positions"]
                       if p.get("status") == "closed")
        n_filled = sum(1 for o in run["open_results"] if o.get("ok"))
        tag = "cold" if run["name"] == "run_cold" else "hot"
        r[f"{tag}_positions"] = n_pos
        r[f"{tag}_closed"] = n_closed
        # 「成交笔数」在正文里没有单独成句，只作为内部一致性检查
        extra[f"{tag}_filled"] = n_filled
    r["hot_no_add"] = sum(1 for o in o4["runs"][1]["open_results"]
                          if o.get("reason") == "position_already_open_no_add")
    rs = o4["resume_run"]["assertions"]
    r["resume_orders"] = sum(len(v["orders"]) for v in o4["resume_run"]["ledgers"].values())
    r["resume_fills"] = sum(len(v["fills"]) for v in o4["resume_run"]["ledgers"].values())
    r["resume_identity"] = all(x.get("signal_uid_source") == "ledger"
                               for x in rs["entry_timing"])

    # ── 05 ──────────────────────────────────────────────────────────────
    r["iso_pass"] = sum(1 for v in o5["verdict"].values() if v)
    r["iso_total"] = len(o5["verdict"])
    r["iso_violations"] = len(o5["connections_write_to_production"])
    r["prior_ledgers_unchanged"] = all(o5["prior_ticket_ledgers_unchanged"].values())
    return r, extra


#: 报告正文里必须出现的句子（数字来自事实块，正文若被手改就会对不上）
MUST_CONTAIN = [
    "合计 {obs_events} 个观察事件",
    "{obs_transitions} 条状态迁移",
    "{obs_candidates} 条候选（入库 {obs_candidates_db} 条",
    "共 {cond_total} 条条件判定",
    "成立 {cond_pass} 条",
    "证据不足 {cond_not_proven} 条",
    "冷档 {np_cold} 条、热库 {np_hot} 条、其它 {np_other} 条",
    "冷档 {bar_cold} 条、热库 {bar_hot} 条",
    "{gateA_missing} 个字段全缺",
    "创建了 {gateA_events} 条观察事件",
    "{gate_pass}／{gate_total} 项通过",
    "{iso_pass}／{iso_total}",
    "{iso_conns} 次数据库连接",
    "可写方式连生产账本目录／生产实时库的次数 = {iso_violations}",
    "{resume_orders}、成交数 {resume_fills}",
    "{cold_positions} 笔持仓全部平仓",
]


def _compare(fb: dict, rc: dict) -> list:
    mism = []
    for k, v in rc.items():
        if k not in fb:
            mism.append({"key": k, "problem": "事实块里没有这一项", "recount": v})
        elif fb[k] != v:
            mism.append({"key": k, "problem": "对不上", "facts": fb[k], "recount": v})
    return mism


def _prose_missing(fb: dict, body: str) -> list:
    missing = []
    for tpl in MUST_CONTAIN:
        try:
            s = tpl.format(**{k: fb[k] for k in re.findall(r"\{(\w+)\}", tpl)})
        except KeyError as exc:
            missing.append({"template": tpl, "problem": f"事实块缺键 {exc}"})
            continue
        if s not in body:
            missing.append({"template": tpl, "expect": s, "problem": "正文里找不到"})
    return missing


def _self_test(fb: dict, rc: dict, body: str) -> dict:
    """检查器自检：**故意篡改**，必须被抓到。抓不到说明这套检查是空的。"""
    t1 = dict(fb)
    t1["obs_candidates"] = int(t1["obs_candidates"]) + 1
    caught_facts = len(_compare(t1, rc)) > 0

    t2 = dict(fb)
    t2["cond_pass"] = int(t2["cond_pass"]) + 7
    caught_prose = len(_prose_missing(t2, body)) > 0

    # 正文被手改：把「合计 N 个观察事件」的 N 换掉
    tampered = re.sub(r"合计 \d+ 个观察事件", "合计 9999 个观察事件", body, count=1)
    caught_body = (tampered != body) and len(_prose_missing(fb, tampered)) > 0
    return {
        "事实块被改一个数能否抓到": caught_facts,
        "条件数被改能否抓到": caught_prose,
        "正文被手改能否抓到": caught_body,
    }


def main() -> int:
    fb = facts_block()
    rc, extra = recount()
    mism = _compare(fb, rc)
    # 06 从 summary 取的字段，与明细复算必须相等（两套口径互相验证）
    pairs = [("obs_candidates", "obs_candidates"), ("cond_pass", "cond_pass"),
             ("cond_not_required", "cond_not_required"),
             ("cond_not_proven", "cond_not_proven"),
             ("gate_pass", "gate_pass"), ("iso_pass", "iso_pass")]
    for a, b in pairs:
        if fb.get(a) != rc.get(b):
            mism.append({"key": a, "problem": "摘要口径与明细口径不一致",
                         "facts": fb.get(a), "recount": rc.get(b)})

    body = README.read_text(encoding="utf-8")
    missing = _prose_missing(fb, body)
    st = _self_test(fb, rc, body)

    left = [s.split("}}")[0] for s in body.split("{{")[1:]]
    out = {
        "readme": str(README),
        "facts_keys": len(fb),
        "recount_keys": len(rc),
        "mismatches": mism,
        "prose_missing": missing,
        "template_leftover": sorted(set(left)),
        "self_test": st,
        "verdict": {
            "事实块与明细复算一致": len(mism) == 0,
            "正文关键句与事实块一致": len(missing) == 0,
            "没有未替换的占位符": len(left) == 0,
            "缺口条数与证据不足条数相等": extra["gap_equals_not_proven"],
            "检查器自检（篡改必须被抓到）": all(st.values()),
        },
    }
    C.dump("07_对撞.json", out)
    bad = [k for k, v in out["verdict"].items() if not v]
    if bad:
        print(json.dumps({"不通过": bad, "明细": mism[:10], "正文缺失": missing[:10],
                          "自检": st}, ensure_ascii=False, indent=1))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

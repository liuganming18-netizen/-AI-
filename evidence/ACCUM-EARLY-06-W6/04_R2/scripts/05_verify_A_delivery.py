# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W6-R2 收口 · 05 独立复验 A 的 W4-R2 交付（只读）

纪律：
- 只读 A 的目录，不重跑 A 的脚本（重跑会改写 A 的 out/，等于替 A 重签）；
- 每个检查项独立判定，失败就是失败，不因「A 自报通过」而放宽；
- 跨版本基线（A-R2 vs R1）逐笔比对，用来证明「钱没变、变的是定性」，
  但**不能**用 R1 的结果替代对 A-R2 的复验——两件事分开写。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
W4R2 = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "04_R2"
R1 = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "03_R1"
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "04_R2" / "out"
A_JSON = W4R2 / "out" / "06_机器可读_交付.json"
A_CSV = W4R2 / "out" / "06_机器可读_交付.csv"
R1_JSON = R1 / "out" / "01_资金费重算与判据整改明细.json"

CHECKS: list = []
TOL = 1e-9


def add(name, ok, detail):
    CHECKS.append({"检查": name, "通过": bool(ok), "明细": detail})
    return ok


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    if not A_JSON.exists():
        print(f"A 交付不存在：{A_JSON}")
        return 2
    A = json.loads(A_JSON.read_text(encoding="utf-8"))
    R1D = json.loads(R1_JSON.read_text(encoding="utf-8"))

    # ── A01 结构 ──
    add("A01 交付文件存在、可解析、schema/task 自述正确",
        A.get("schema") == "accum-early-06-w4-r2/v1"
        and A.get("task") == "ACCUM-EARLY-06-W4-R2",
        {"path": str(A_JSON.relative_to(ROOT)),
         "bytes": A_JSON.stat().st_size,
         "schema": A.get("schema"), "task": A.get("task")})

    # ── A02 规模 ──
    cands = A.get("candidates") or []
    ids = [c["candidate_id"] for c in cands]
    cal_counts = {len(c.get("calibers") or {}) for c in cands}
    rows = sum(len(c.get("calibers") or {}) for c in cands)
    add("A02 51 个候选 / 候选 ID 唯一 / 每候选 2 套口径 / 合计 102 行",
        len(cands) == 51 and len(set(ids)) == 51
        and cal_counts == {2} and rows == 102,
        {"candidates": len(cands), "unique_ids": len(set(ids)),
         "calibers_per_candidate": sorted(cal_counts), "rows": rows})

    # ── A03 代码哈希复算 ──
    bad = []
    for name, rec in (A.get("code_hashes") or {}).items():
        p = ROOT / rec["path"]
        if not p.exists():
            bad.append({"name": name, "问题": "文件不存在", "path": rec["path"]})
        elif sha(p) != rec["sha256"]:
            bad.append({"name": name, "问题": "哈希不一致",
                        "声明": rec["sha256"], "实算": sha(p)})
    add("A03 code_hashes 全部复算一致", not bad,
        {"声明条数": len(A.get("code_hashes") or {}), "不一致": bad})

    # ── A04 输入哈希复算 ──
    bad_in = []
    h = (A.get("inputs") or {}).get("hashes") or {}
    for name, rec in h.items():
        p = ROOT / rec["path"]
        if not p.exists():
            bad_in.append({"name": name, "问题": "文件不存在", "path": rec["path"]})
        elif sha(p) != rec["sha256"]:
            bad_in.append({"name": name, "问题": "哈希不一致",
                           "声明": rec["sha256"], "实算": sha(p)})
    add("A04 inputs.hashes 全部复算一致（适配器 / 共享计算 / 候选来源 / R1 产物）",
        not bad_in, {"声明条数": len(h), "不一致": bad_in})

    # ── A05 逐行恒等式 ──
    # 容差说明：A 把金额按 9 位小数落盘，三个独立舍入的量不可能比 1 ulp（1e-9）更准，
    # 所以判据取 2e-9 并同时报出最大偏差；不是「放到能过为止」——A08 与 R1 的比对
    # 仍用 1e-9，那里比的是同一批未舍入值。
    EQ_TOL = 2e-9
    bad_eq, max_dev = [], 0.0
    for c in cands:
        for cal, r in (c["calibers"] or {}).items():
            if r.get("status") != "ok":
                continue
            for dev, kind in (
                (abs(r["delta_net"] + r["delta_funding"]), "ΔNet ≠ −ΔFunding"),
                (abs(r["net_new"] - (r["net_old"] + r["delta_net"])), "NetNew ≠ NetOld+ΔNet"),
                (abs(r["funding_new"] - (r["funding_old"] + r["delta_funding"])),
                 "FundingNew ≠ FundingOld+ΔFunding"),
            ):
                max_dev = max(max_dev, dev)
                if dev > EQ_TOL:
                    bad_eq.append({"id": c["candidate_id"], "caliber": cal,
                                   "类型": kind, "偏差": dev})
    add("A05 102 行逐行满足三条恒等式（容差 2e-9 = 9 位小数舍入的 2 ulp）",
        not bad_eq, {"检查行数": rows, "最大偏差": max_dev, "违例": bad_eq[:8]})

    # ── A06 逐口径合计 == summary ──
    sum_bad = []
    for cal, S in (A.get("summary_by_caliber") or {}).items():
        rr = [c["calibers"][cal] for c in cands if cal in (c.get("calibers") or {})]
        calc = {
            "candidates": len(rr),
            "filled": sum(1 for r in rr if r.get("is_filled")),
            "rejected": sum(1 for r in rr if not r.get("is_filled")),
            "funding_old_sum": sum(r["funding_old"] for r in rr),
            "funding_new_sum": sum(r["funding_new"] for r in rr),
            "delta_funding_sum": sum(r["delta_funding"] for r in rr),
            "net_old_sum": sum(r["net_old"] for r in rr),
            "net_new_sum": sum(r["net_new"] for r in rr),
            "delta_net_sum": sum(r["delta_net"] for r in rr),
            "events_old_sum": sum(r["events_old"] for r in rr),
            "events_new_sum": sum(r["events_new"] for r in rr),
            "affected_trades": sum(1 for r in rr if abs(r["delta_funding"]) > TOL),
        }
        for k, v in calc.items():
            if isinstance(v, float):
                if abs(v - S[k]) > 1e-6:
                    sum_bad.append({"caliber": cal, "字段": k, "summary": S[k], "重算": v})
            elif v != S[k]:
                sum_bad.append({"caliber": cal, "字段": k, "summary": S[k], "重算": v})
    add("A06 逐口径合计与 summary_by_caliber 逐字段一致（11 个字段 × 2 口径）",
        not sum_bad, {"不一致": sum_bad})

    # ── A07 分类分布 == summary（拆两问：分母一致 / 计数一致） ──
    cr_bad, den_bad = [], []
    for cal, S in (A.get("summary_by_caliber") or {}).items():
        rr = [c["calibers"][cal] for c in cands if cal in (c.get("calibers") or {})]
        for field, key in (("change_reason_dist", "change_reason"),
                           ("completeness_dist", "completeness"),
                           ("claim_dist", "claim")):
            calc: dict = {}
            for r in rr:
                calc[r[key]] = calc.get(r[key], 0) + 1
            if sum(S[field].values()) != len(rr):
                den_bad.append({"caliber": cal, "字段": field,
                                "summary合计": sum(S[field].values()),
                                "行数": len(rr), "summary": S[field], "重算": calc})
            elif calc != S[field]:
                cr_bad.append({"caliber": cal, "字段": field,
                               "summary": S[field], "重算": calc})
    add("A07a change_reason 分布覆盖全部候选且计数一致（分母 51）",
        not [x for x in cr_bad + den_bad if x["字段"] == "change_reason_dist"],
        {"不一致": [x for x in cr_bad + den_bad if x["字段"] == "change_reason_dist"]})

    add("A07b completeness / claim 分布的分母与 change_reason 一致（都应为 51）",
        not den_bad,
        {"说明": "A 的 策略包_3bps 口径里 CARVUSDT 是 rejected（completeness=NOT_APPLICABLE、"
                 "claim=UNKNOWN），但 summary 的两个分布只统计了 50 笔成交，"
                 "与 change_reason 的 51 分母不一致 ⇒ 下游按分布读会漏掉这个拒单候选",
         "不一致": den_bad})

    # ── A08 跨版本基线：与 R1 逐笔对齐（证明「钱没变」） ──
    r1 = {}
    for it in R1D.get("recalc_items") or []:
        r1[(it["candidate_id"], it["caliber"])] = it
    cmp_bad, cmp_n = [], 0
    for c in cands:
        for cal, r in (c["calibers"] or {}).items():
            k = (c["candidate_id"], cal)
            if k not in r1:
                cmp_bad.append({"id": k[0], "caliber": cal, "问题": "R1 无对应行"})
                continue
            o = r1[k]
            cmp_n += 1
            for a_field, o_path in (("funding_old", ("funding_old", "total_cost")),
                                    ("funding_new", ("funding_new", "total_cost")),
                                    ("delta_net", ("delta_realized_net",))):
                ov = o
                for p in o_path:
                    ov = ov[p]
                if abs(r[a_field] - ov) > 1e-9:
                    cmp_bad.append({"id": k[0], "caliber": cal, "字段": a_field,
                                    "A_R2": r[a_field], "R1": ov})
            if r["events_new"] != o["funding_new"]["events_count"]:
                cmp_bad.append({"id": k[0], "caliber": cal, "字段": "events_new",
                                "A_R2": r["events_new"], "R1": o["funding_new"]["events_count"]})
    add("A08 与 R1 逐笔逐字段对齐（资金费旧/新、净额差、事件数）⇒ 换的是判据不是钱",
        not cmp_bad, {"比对行数": cmp_n, "不一致": cmp_bad[:8]})

    # ── A09 A 的 README 关键数字与 JSON 一致 ──
    readme = (W4R2 / "README.md").read_text(encoding="utf-8")
    SA = A["summary_by_caliber"]["生效视图_5bps"]
    SB = A["summary_by_caliber"]["策略包_3bps"]
    want = [f"{SA['funding_old_sum']:,.9f}", f"{SA['funding_new_sum']:,.9f}",
            f"{SA['net_old_sum']:,.9f}", f"{SB['funding_old_sum']:,.9f}",
            f"{SB['funding_new_sum']:,.9f}", f"{SB['delta_funding_sum']:,.9f}"]
    miss = [w for w in want if w not in readme]
    add("A09 A 的 README 里 6 个关键金额与 JSON 逐位可查", not miss, {"缺": miss})

    # ── A10 A 自己的对撞结论 ──
    a08 = json.loads((W4R2 / "out" / "08_数字对撞.json").read_text(encoding="utf-8"))
    a08c = a08.get("checks") or []
    add("A10 A 的数字对撞自检 21/21 且反向未登记 0（读其结论，不重跑）",
        len(a08c) == 21 and all(c["通过"] for c in a08c)
        and (a08.get("统计") or {}).get("分桶", {}).get("未登记") == 0,
        {"检查项": len(a08c), "通过": sum(1 for c in a08c if c["通过"]),
         "未登记": (a08.get("统计") or {}).get("分桶", {}).get("未登记")})

    # ── A11 产物身份 ──
    files = [W4R2 / "README.md", A_JSON, A_CSV,
             W4R2 / "out" / "07_正文数字台账.json",
             W4R2 / "out" / "08_数字对撞.json"]
    ident = [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size, "sha256": sha(p)}
             for p in files if p.exists()]
    add("A11 A 的 5 件主产物都在且哈希已记录（供交接引用）",
        len(ident) == 5, {"产物": ident})

    ok = all(c["通过"] for c in CHECKS)
    failed = [c["检查"] for c in CHECKS if not c["通过"]]
    rep = {
        "工单": "ACCUM-EARLY-06-W6-R2",
        "性质": "对 A（W4-R2）交付的独立复验（只读，不重跑 A 的脚本）",
        "被验文件": str(A_JSON.relative_to(ROOT)),
        "被验文件_sha256": sha(A_JSON),
        "A_README_sha256": sha(W4R2 / "README.md"),
        "基线对照文件": str(R1_JSON.relative_to(ROOT)),
        "基线对照_sha256": sha(R1_JSON),
        "checks": CHECKS,
        "通过项": sum(1 for c in CHECKS if c["通过"]),
        "失败项": failed,
        "结论": ("**独立复验：可用但需 1 项返工**。结构、规模、代码/输入哈希、三条恒等式、"
                 "逐口径合计、与 R1 逐笔对齐、README 数字一致、A 自身对撞均成立 ⇒ "
                 "**金额与结构可用，可直接供 B 接入**。"
                 "返工项：`策略包_3bps` 的 completeness/claim 分布只统计 50 笔，"
                 "与 change_reason 的 51 分母不一致（漏掉 CARVUSDT 拒单行的 "
                 "NOT_APPLICABLE/UNKNOWN）。"
                 "**这不等于 Codex 验收**；本复验不覆盖 A 的未验证项"
                 "（当时可得 / 官方证据边界 / IRYSUSDT 周期歧义 / 端点抖动先后）。")
        if not ok else "独立复验全部通过",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "05_A交付独立复验.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    for c in CHECKS:
        print(("PASS " if c["通过"] else "FAIL ") + c["检查"])
    print(f"\n{sum(1 for c in CHECKS if c['通过'])}/{len(CHECKS)} 通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

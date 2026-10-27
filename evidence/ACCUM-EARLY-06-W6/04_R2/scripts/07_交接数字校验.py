# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W6-R2 收口 · 07 交接正文数字校验

目的：交接 md 里的每个关键数字，都必须能从上游产物重新算出来。
     不允许「我手写、没人验」。

做法
----
1. 正向：从产物读值 → 格式化成 md 里应出现的文本 → 断言 md 含该文本。
2. 反向：把 md 里所有「金额型」数字（6 位以上小数）抽出来，
   逐个回查是否能在产物文本里找到；找不到的必须登记进白名单并说明来源，
   否则判为「正文有数、产物无据」。
3. 派生值（如截断差 6.60e-08）用数值比对 + 容差，不用字符串比对。

排版归一：md 用 U+2212（−）作减号，产物用 ASCII（-）。比对前统一归一化。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MD = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "04_R2" / "总交接_R2_待Codex审核.md"
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "04_R2" / "out"

A = json.loads((ROOT / "evidence/ACCUM-EARLY-06-W4/04_R2/out/06_机器可读_交付.json").read_text(encoding="utf-8"))
A02 = json.loads((ROOT / "evidence/ACCUM-EARLY-06-W4/04_R2/out/02_缺口判据与完整性.json").read_text(encoding="utf-8"))
A04 = json.loads((ROOT / "evidence/ACCUM-EARLY-06-W4/04_R2/out/04_真实记录核对.json").read_text(encoding="utf-8"))
B02 = json.loads((ROOT / "evidence/ACCUM-EARLY-06-W5/04_R2/out/02_funding_mapper_status.json").read_text(encoding="utf-8"))
BS = json.loads((ROOT / "evidence/ACCUM-EARLY-06-W5/04_R2/out/summary_verification_verdicts.json").read_text(encoding="utf-8"))
B03 = json.loads((ROOT / "evidence/ACCUM-EARLY-06-W5/04_R2/out/03_baseline_evidence_audit.json").read_text(encoding="utf-8"))
MATRIX = json.loads((OUT / "06_阶段状态矩阵.json").read_text(encoding="utf-8"))
D_PENDING = json.loads((ROOT / "evidence/ACCUM-EARLY-06-W1/03_R1/待决表_R1.json").read_text(encoding="utf-8"))
D01 = json.loads((ROOT / "evidence/ACCUM-EARLY-06-W1/03_R1/out/01_approval_evidence.json").read_text(encoding="utf-8"))
D02 = json.loads((ROOT / "evidence/ACCUM-EARLY-06-W1/03_R1/out/02_stop_decision.json").read_text(encoding="utf-8"))
D04 = json.loads((ROOT / "evidence/ACCUM-EARLY-06-W1/03_R1/out/04_pending_dedupe.json").read_text(encoding="utf-8"))

RAW = MD.read_text(encoding="utf-8")
TEXT = RAW.replace("\u2212", "-")  # 排版减号 → ASCII

CHECKS: list = []


def add(name, ok, detail):
    CHECKS.append({"检查": name, "通过": bool(ok), "明细": detail})
    return ok


def must(name, needle, src):
    """正向：md 必须含该文本。"""
    ok = needle in TEXT
    return add(name, ok, {"要求出现": needle, "来源": src, "未找到" if not ok else "已找到": True})


def must_num(name, needle_md, value, tol, src):
    """派生值：md 里的科学计数法串解析后与计算值比。"""
    ok = needle_md in TEXT
    parsed = None
    m = re.fullmatch(r"([+-]?[0-9.]+)e([+-]?[0-9]+)", needle_md)
    if m:
        parsed = float(m.group(1)) * (10 ** int(m.group(2)))
    same = parsed is not None and abs(parsed - value) <= tol
    return add(name, ok and same,
               {"md串": needle_md, "解析值": parsed, "计算值": value, "容差": tol,
                "md含该串": ok, "数值在容差内": same, "来源": src})


def main() -> int:
    sa = A["summary_by_caliber"]["生效视图_5bps"]
    sb = A["summary_by_caliber"]["策略包_3bps"]
    s = A02["汇总"]
    r1 = A04["R1_R1缺失复核"]
    r2 = A04["R2_周期切换实证"]
    r3 = A04["R3_相位实证"]
    r6 = A04["R6_补入事件是否在网格上"]
    bsum = B02["w4_04_r2_real_reconciliation"]["summary_by_caliber"]
    beff, bpkg = bsum["生效视图_5bps"], bsum["策略包_3bps"]

    # ---------- F1 A 的规模与口径 A 金额 ----------
    add("F1 A 规模 51 候选 / 102 行", len(A["candidates"]) == 51, {"candidates": len(A["candidates"])})
    must("F1a md 含「51 候选 / 102 口径行」", "51 候选 / 102 口径行", "A 交付")
    must("F1b md 含 A 口径A 资金费新值", f"{sa['funding_new_sum']}", "A summary_by_caliber")
    must("F1c md 含 A 口径A 资金费旧值", f"{sa['funding_old_sum']}", "A summary_by_caliber")
    must("F1d md 含 A 口径A Δ资金费", f"{sa['delta_funding_sum']}", "A summary_by_caliber")
    must("F1e md 含 A 口径A 净额新值", f"{sa['net_new_sum']}", "A summary_by_caliber")
    must("F1f md 含 A 口径A 净额旧值", f"{sa['net_old_sum']}", "A summary_by_caliber")
    must("F1g md 含 A 口径A 事件数旧→新", f"{sa['events_old_sum']} → {sa['events_new_sum']}", "A summary_by_caliber")

    # ---------- F2 口径 B 金额 ----------
    must("F2a md 含 A 口径B 资金费新值", f"{sb['funding_new_sum']}", "A summary_by_caliber")
    must("F2b md 含 A 口径B 资金费旧值", f"{sb['funding_old_sum']}", "A summary_by_caliber")
    must("F2c md 含 A 口径B Δ资金费", f"{sb['delta_funding_sum']}", "A summary_by_caliber")
    must("F2d md 含 A 口径B 净额新值", f"{sb['net_new_sum']}", "A summary_by_caliber")
    must("F2e md 含 A 口径B 净额旧值", f"{sb['net_old_sum']}", "A summary_by_caliber")
    must("F2f md 含 A 口径B 事件数旧→新", f"{sb['events_old_sum']} → {sb['events_new_sum']}", "A summary_by_caliber")

    # ---------- F3 claim / completeness 分布 ----------
    must("F3a md 含口径A claim 分布", "`{PROVEN 44, NOT_PROVEN 7}`", "A summary")
    must("F3b md 含口径B claim 分布", "`{PROVEN 43, NOT_PROVEN 7}`", "A summary")
    add("F3c 口径A claim 分布与 md 一致",
        sa["claim_dist"] == {"PROVEN": 44, "NOT_PROVEN": 7}, sa["claim_dist"])
    add("F3d 口径B claim 分布与 md 一致",
        sb["claim_dist"] == {"PROVEN": 43, "NOT_PROVEN": 7}, sb["claim_dist"])
    must("F3e md 含口径B completeness 分母差", "`{NO_GAP_DETECTED: 50}` = 50", "A summary")
    must("F3f md 含口径B completeness 重算", "`{NO_GAP_DETECTED: 50, NOT_APPLICABLE: 1}` = 51", "逐行重算")
    add("F3g 口径B completeness 分母 50 ≠ 51 成立",
        sum(sb["completeness_dist"].values()) == 50 and sb["candidates"] == 51,
        {"completeness合计": sum(sb["completeness_dist"].values()), "candidates": sb["candidates"]})
    add("F3h 口径B change_reason 分母 51（含 REJECTED 1）",
        sum(sb["change_reason_dist"].values()) == 51 and sb["change_reason_dist"].get("REJECTED") == 1,
        sb["change_reason_dist"])

    # ---------- F4 A 的判据缺陷实证 ----------
    must("F4a md 含 R1 缺失 188", "188 个缺失中 **188/188**", "A 04")
    add("F4b 188/188 在 ±1s 内有真实事件", r1["其中在 ±1s 内有真实事件"] == 188 and r1["r1_missing_instants"] == 188,
        {"缺失": r1["r1_missing_instants"], "±1s内": r1["其中在 ±1s 内有真实事件"]})
    must("F4c md 含最大偏移 11 ms", "最大偏移 **11 ms**", "A 04")
    add("F4d 最大偏移 = 11", r1["最大偏移ms"] == 11, r1["最大偏移ms"])
    must("F4e md 含 R1→R2 缺失归零", "188（38 候选）→ 本单 **0**（0 候选）", "A 02")
    add("F4f 缺失归零", s["r1_missing_total"] == 188 and s["r2_missing_total"] == 0
        and s["r1_would_flag_candidates"] == 38 and s["r2_flag_candidates"] == 0, s)
    must("F4g md 含 official_snapshot 46 / inferred_uniform 5",
         "`official_snapshot` 46 / `inferred_uniform` 5", "A 02")
    add("F4h 周期证据分布", s["interval_basis_dist"] == {"official_snapshot": 46, "inferred_uniform": 5},
        s["interval_basis_dist"])

    # ---------- F5 周期切换 / 相位 / 端点抖动 ----------
    must("F5a md 含 15 标的有切换点", "15 标的有切换点", "A 04")
    add("F5b 切换点标的 15 / 一致 14 / ≤48h 13",
        r2["有切换点的标的数"] == 15 and r2["官方与最后一段一致"] == 14
        and r2["updateTime 与最后切换 ≤48h 的标的数"] == 13,
        {k: r2[k] for k in ["有切换点的标的数", "官方与最后一段一致", "updateTime 与最后切换 ≤48h 的标的数"]})
    must("F5c md 含相位 51 / 0", "相位=0 的候选 51 / ≠0 的 0", "A 04")
    add("F5d 相位统计", r3["相位=0 的候选"] == 51 and r3["相位≠0 的候选"] == 0, r3)
    # R4 有 4 条 case（两口径各 2 条）。按 symbol 建字典会被后一条覆盖，
    # 必须用 (caliber, symbol) 作键，否则会把策略包的值当成生效视图的值。
    jit = {(c["caliber"], c["symbol"]): c for c in A04["R4_端点抖动实证"]["cases"]}
    add("F5e-0 端点抖动 case 数为 4（两口径各 2）", len(jit) == 4, {"键": sorted(jit)})
    for cal, tag in [("生效视图_5bps", "生效视图"), ("策略包_3bps", "策略包")]:
        for sym in ["AIGENSYNUSDT", "WALUSDT"]:
            v = jit[(cal, sym)]["cost_if_included"]
            must(f"F5e-{tag}-{sym} md 含抖动成本 {v}", f"{v}", f"A 04 R4 {cal}")
    add("F5g WALUSDT 两口径抖动成本均为负（收取）",
        all(jit[(c, "WALUSDT")]["cost_if_included"] < 0 for c in ["生效视图_5bps", "策略包_3bps"]),
        {c: jit[(c, "WALUSDT")]["cost_if_included"] for c in ["生效视图_5bps", "策略包_3bps"]})
    add("F5g2 两笔抖动的 events_old == events_new（抖动事件未进入事件计数）",
        all(jit[k]["events_old"] == jit[k]["events_new"] for k in jit),
        {f"{k[1]}/{k[0]}": [jit[k]["events_old"], jit[k]["events_new"]] for k in jit})
    add("F5g3 AIGENSYNUSDT 基数为 14、WALUSDT 基数为 0（两者不同，不可统一写成 0→0）",
        all(jit[(c, "AIGENSYNUSDT")]["events_old"] == 14 for c in ["生效视图_5bps", "策略包_3bps"])
        and all(jit[(c, "WALUSDT")]["events_old"] == 0 for c in ["生效视图_5bps", "策略包_3bps"]),
        {"AIGENSYNUSDT": jit[("生效视图_5bps", "AIGENSYNUSDT")]["events_old"],
         "WALUSDT": jit[("生效视图_5bps", "WALUSDT")]["events_old"]})
    axs = A04["R5_非零结算但仍漏"]["cases"][0]
    must("F5h md 含 AXSUSDT 30→33", f"`{axs['symbol']}` 事件 {axs['events_old']}→{axs['events_new']}", "A 04")
    add("F5i 补入 44 条网格外 0",
        r6["补入事件数（51 笔持仓内）"] == 44 and r6["网格外"] == 0, r6)

    # ---------- F6 B 的复验规模 ----------
    must("F6a md 含 B 102/102", "102/102", "B 02")
    add("F6b B 逐笔 102 全过",
        B02["w4_04_r2_real_reconciliation"]["total_trades_checked"] == 102
        and B02["w4_04_r2_real_reconciliation"]["failed_trades_count"] == 0
        and B02["w4_04_r2_real_reconciliation"]["cost_mismatches_count"] == 0
        and B02["w4_04_r2_real_reconciliation"]["equation_failures_count"] == 0,
        {k: B02["w4_04_r2_real_reconciliation"][k] for k in
         ["total_trades_checked", "failed_trades_count", "cost_mismatches_count", "equation_failures_count"]})
    must("F6c md 含 B 生效视图金额", f"{beff['w4_funding_total']}", "B 02")
    must("F6d md 含 B 策略包金额", f"{bpkg['w4_funding_total']}", "B 02")
    must("F6e md 含 B 独立 claim 生效视图", "`{44, 7, UNKNOWN 0}`", "B 02")
    must("F6f md 含 B 独立 claim 策略包", "`{43, 7, UNKNOWN 1}`", "B 02")
    add("F6g B 独立 claim 与 md 一致",
        beff["claim_distribution"] == {"PROVEN": 44, "NOT_PROVEN": 7, "UNKNOWN": 0}
        and bpkg["claim_distribution"] == {"PROVEN": 43, "NOT_PROVEN": 7, "UNKNOWN": 1},
        {"生效": beff["claim_distribution"], "策略包": bpkg["claim_distribution"]})

    # ---------- F7 截断差（派生值，数值比对） ----------
    d_eff = beff["w4_funding_total"] - sa["funding_new_sum"]
    d_pkg = bpkg["w4_funding_total"] - sb["funding_new_sum"]
    must_num("F7a md 含生效视图截断差 +6.60e-08", "+6.60e-08", d_eff, 5e-9, "B 6位 − A 9位")
    must_num("F7b md 含策略包截断差 −4.49e-07", "-4.49e-07", d_pkg, 5e-9, "B 6位 − A 9位")
    add("F7c 两口径 B 自报 difference 均为 0.0（6 位内部自洽）",
        beff["difference"] == 0.0 and bpkg["difference"] == 0.0,
        {"生效": beff["difference"], "策略包": bpkg["difference"],
         "说明": "0.0 只证明 B 的 6 位值自洽，不等于与 A 的 9 位逐位一致"})

    # ---------- F8 B 的三项缺陷在 md 中已声明 ----------
    must("F8a md 含 V05b 无 timestamp", "**无 timestamp 字段**", "B 05")
    must("F8b md 含 REFERENCED_FROM_A_EVIDENCE", "`REFERENCED_FROM_A_EVIDENCE`", "B 02")
    add("F8c B layer_2 确为引用非独立",
        B02["verification_taxonomy_and_layers"]["layer_2_official_evidence_and_gap_classification"]["status"]
        == "REFERENCED_FROM_A_EVIDENCE",
        B02["verification_taxonomy_and_layers"]["layer_2_official_evidence_and_gap_classification"])
    must("F8d md 含 B 14 项判定表", "14 项判定表", "B summary")
    add("F8e B summary 共 14 项且全 PENDING_CODEX_AUDIT",
        len(BS["items"]) == 14 and all(i["codex_audit_status"] == "PENDING_CODEX_AUDIT" for i in BS["items"]),
        {"items": len(BS["items"]),
         "codex": sorted({i["codex_audit_status"] for i in BS["items"]})})
    must("F8f md 含 6 条复用", "复用旧证据 6 条", "B 03")
    add("F8g B 复用 6 条 / 新做 5 条",
        len(B03["verification_taxonomy"]["reused_from_prior_verified_evidence"]) == 6
        and len(B03["verification_taxonomy"]["newly_performed_in_04_r2"]) == 5,
        {"复用": len(B03["verification_taxonomy"]["reused_from_prior_verified_evidence"]),
         "新做": len(B03["verification_taxonomy"]["newly_performed_in_04_r2"])})

    # ---------- F9 D 的计数 ----------
    by = D01["counts"]["by_level"]
    must("F9a md 含 31 行与 E1/E2/E3", "31（`E1` 7 / `E2` 1 / `E3` 23）", "D 01")
    add("F9b D 计数", D01["counts"]["rows"] == 31 and by == {"E1": 7, "E2": 1, "E3": 23}, D01["counts"])
    must("F9c md 含 21 项 12P/6E/3G", "21（`P` 12 / `E` 6 / `G` 3）", "D 待决表")
    cls = {c: sum(1 for i in D_PENDING["items"] if i["class"] == c) for c in ["P", "E", "G"]}
    add("F9d 待决 21 项分类", len(D_PENDING["items"]) == 21 and cls == {"P": 12, "E": 6, "G": 3},
        {"total": len(D_PENDING["items"]), "by_class": cls})
    must("F9e md 含 45+4=49", "45 个行为字段 + 4 个凭证 = **49**", "D 04")
    add("F9f 字段归属", D04["counts"]["p_fields"] == 45 and D04["counts"]["p_fields_plus_credentials"] == 49,
        D04["counts"])
    must("F9g md 含 18/18 引用", "18/18 通过", "D 01")
    add("F9h 引用校验", D01["references"]["total"] == 18 and D01["references"]["pass"] == 18
        and len(D01["bad_references"]) == 0, D01["references"])
    must("F9i md 含止损 8 项 E1", "8 项全为 `E1`", "D 02")
    add("F9j 止损 8 项不再询问", len(D02["not_asked"]) == 8, {"not_asked": len(D02["not_asked"])})
    must("F9k md 含 raw_user_messages false", "`raw_user_messages_retrieved = false`", "D 01")
    add("F9l D 未取证原始消息", D01["input"]["raw_user_messages_retrieved"] is False,
        D01["input"]["raw_user_messages_retrieved"])

    # ---------- F10 三组样本 ----------
    must("F10a md 含原 7 事件终态", "6 `INVALIDATED` + 1 `NOT_FOUND`", "B 03")
    o7 = B03["reused_evidence_details"]["original_7_events"]
    add("F10b 原 7 事件 6+1 且候选 0",
        o7["invalidated_count"] == 6 and o7["not_found_count"] == 1 and o7["candidates_produced"] == 0, o7)
    must("F10c md 含自然候选 0", "自然候选 **0**、已平仓 **0**", "矩阵 V13")
    must("F10d md 含 48 标的", "48 标的", "A 范围")

    # ---------- F11 反向：md 里的金额型数字必须能在产物里找到 ----------
    money_tokens = sorted(set(re.findall(r"-?\d+\.\d{6,9}", TEXT)))
    blob = "\n".join([
        json.dumps(A, ensure_ascii=False), json.dumps(A02, ensure_ascii=False),
        json.dumps(A04, ensure_ascii=False), json.dumps(B02, ensure_ascii=False),
        json.dumps(MATRIX, ensure_ascii=False), json.dumps(D04, ensure_ascii=False),
    ])
    WHITELIST = {
        # md 里用于说明判据/容差的常量，不是从产物读的金额
        "0.0000018", "1.0000018",  # A05 最大偏差（矩阵里有完整值）
    }
    orphan = [t for t in money_tokens if t not in blob and t not in WHITELIST]
    add("F11 md 中 6 位以上小数的数字全部能在产物中找到（或已登记白名单）",
        not orphan, {"抽出条数": len(money_tokens), "未登记": orphan,
                     "白名单": sorted(WHITELIST)})

    # ---------- 汇总 ----------
    passed = sum(1 for c in CHECKS if c["通过"])
    result = {
        "schema": "accum-early-06-w6-r2/md-number-check/v1",
        "被校验文件": "evidence/ACCUM-EARLY-06-W6/04_R2/总交接_R2_待Codex审核.md",
        "被校验文件_sha256": __import__("hashlib").sha256(MD.read_bytes()).hexdigest(),
        "检查项": CHECKS,
        "通过项": passed,
        "失败项": [c["检查"] for c in CHECKS if not c["通过"]],
    }
    dst = OUT / "07_交接数字校验.json"
    dst.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写出 {dst}  ({dst.stat().st_size} B)")
    print(f"检查项 {len(CHECKS)}：通过 {passed} / 失败 {len(result['失败项'])}")
    for c in CHECKS:
        if not c["通过"]:
            print(f"  [FAIL] {c['检查']}")
            print(f"         {json.dumps(c['明细'], ensure_ascii=False)[:300]}")
    return 0 if not result["失败项"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

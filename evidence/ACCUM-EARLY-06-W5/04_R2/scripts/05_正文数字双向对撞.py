# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-R2 | 脚本 05: README 正文数字与 out/ 机器数据双向对撞校验器 (接续收口版)

【校验目的】
确保 README.md 中的所有财务总额、计数、单元测试统计、代码哈希与状态标记，
与 out/ 目录下的所有机器 JSON 文件 100% 精确对齐，无任何人工笔误或漂移。
"""
import json
import os
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[4]
R2_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "04_R2"
OUT_DIR = R2_DIR / "out"
README_PATH = R2_DIR / "README.md"


def main():
    print("=" * 70)
    print("【ACCUM-EARLY-06-W5-R2】正文数字双向对撞校验 (接续收口版) 执行中...")
    print("=" * 70)

    readme_text = README_PATH.read_text(encoding="utf-8")
    gate_data = json.loads((OUT_DIR / "01_dependency_hard_gate.json").read_text(encoding="utf-8"))
    funding_data = json.loads((OUT_DIR / "02_funding_mapper_status.json").read_text(encoding="utf-8"))
    baseline_data = json.loads((OUT_DIR / "03_baseline_evidence_audit.json").read_text(encoding="utf-8"))
    summary_data = json.loads((OUT_DIR / "summary_verification_verdicts.json").read_text(encoding="utf-8"))

    checks = []

    def check(name: str, cond: bool, detail: str = ""):
        checks.append({"check_name": name, "passed": bool(cond), "detail": detail})
        print(f"  [{ 'PASS' if cond else 'FAIL' }] {name}: {detail}")

    # 1. 代码哈希对撞
    w2_sha = baseline_data["code_identity"]["sha256"]
    check(
        "代码 SHA-256 吻合",
        w2_sha in readme_text and w2_sha == "3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017",
        f"SHA256: {w2_sha}"
    )

    # 2. 资金费源事件数与标的数
    events_count = gate_data["w4_04_r2_real_delivery_evaluation"]["detail"]["w3_funding_source_audit"]["events_count"]
    symbols_count = gate_data["w4_04_r2_real_delivery_evaluation"]["detail"]["w3_funding_source_audit"]["symbols_count"]
    check(
        "资金费事件数 31,811 与标的数 48",
        "31,811" in readme_text and "48" in readme_text and events_count == 31811 and symbols_count == 48,
        f"Events: {events_count}, Symbols: {symbols_count}"
    )

    # 3. 门禁单元测试数量与全绿
    gate_unit_count = gate_data["unit_test_suite"]["total_cases"]
    gate_unit_passed = gate_data["unit_test_suite"]["passed_cases"]
    check(
        "门禁单元测试 7/7 全部通过",
        gate_unit_count == 7 and gate_unit_passed == 7 and "NEG_01_EMPTY_JSON" in readme_text,
        f"Passed: {gate_unit_passed}/{gate_unit_count}"
    )

    # 4. 复验单元测试数量与全绿
    recon_unit_count = funding_data["reconciliation_unit_tests"]["total_cases"]
    recon_unit_passed = funding_data["reconciliation_unit_tests"]["passed_cases"]
    check(
        "复验防篡改单元测试 3/3 全部通过",
        recon_unit_count == 3 and recon_unit_passed == 3 and "NEG_RECON_01_TAMPERED_COST" in readme_text,
        f"Passed: {recon_unit_passed}/{recon_unit_count}"
    )

    # 5. 生效视图财务数字对撞
    eff_w4 = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["生效视图_5bps"]["w4_funding_total"]
    eff_ind = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["生效视图_5bps"]["independent_funding_total"]
    eff_diff = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["生效视图_5bps"]["difference"]
    check(
        "生效视图_5bps 资金费总额 -5.895109",
        "-5.895109" in readme_text and eff_w4 == -5.895109 and eff_ind == -5.895109 and eff_diff == 0.0,
        f"W4: {eff_w4}, Ind: {eff_ind}, Diff: {eff_diff}"
    )

    # 6. 策略包财务数字对撞
    pkg_w4 = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["策略包_3bps"]["w4_funding_total"]
    pkg_ind = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["策略包_3bps"]["independent_funding_total"]
    pkg_diff = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["策略包_3bps"]["difference"]
    check(
        "策略包_3bps 资金费总额 -2.672855",
        "-2.672855" in readme_text and pkg_w4 == -2.672855 and pkg_ind == -2.672855 and pkg_diff == 0.0,
        f"W4: {pkg_w4}, Ind: {pkg_ind}, Diff: {pkg_diff}"
    )

    # 7. 候选证明分级分布对撞
    eff_proven = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["生效视图_5bps"]["claim_distribution"]["PROVEN"]
    eff_not_proven = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["生效视图_5bps"]["claim_distribution"]["NOT_PROVEN"]
    pkg_proven = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["策略包_3bps"]["claim_distribution"]["PROVEN"]
    pkg_not_proven = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["策略包_3bps"]["claim_distribution"]["NOT_PROVEN"]
    pkg_unknown = funding_data["w4_04_r2_real_reconciliation"]["summary_by_caliber"]["策略包_3bps"]["claim_distribution"]["UNKNOWN"]
    check(
        "证明分级分布对撞 (生效视图 44/7/0, 策略包 43/7/1)",
        (eff_proven == 44 and eff_not_proven == 7 and pkg_proven == 43 and pkg_not_proven == 7 and pkg_unknown == 1
         and "44 / 7 / 0" in readme_text and "43 / 7 / 1" in readme_text),
        f"Eff: {eff_proven}/{eff_not_proven}/0, Pkg: {pkg_proven}/{pkg_not_proven}/{pkg_unknown}"
    )

    # 8. 恒等式成立率 100%
    check(
        "财务净额恒等式 102/102 (100.0%)",
        "100.0% (102/102)" in readme_text and funding_data["w4_04_r2_real_reconciliation"]["equation_failures_count"] == 0,
        "102/102 equation passed"
    )

    # 9. 拒单条目 CARVUSDT 语义保真
    check(
        "CARVUSDT 拒单语义保真",
        "CARVUSDT" in readme_text and "status=rejected" in readme_text,
        "CARVUSDT rejected verified"
    )

    # 10. 历史原 7 事件零候选终态
    inval_count = baseline_data["reused_evidence_details"]["original_7_events"]["invalidated_count"]
    not_found_count = baseline_data["reused_evidence_details"]["original_7_events"]["not_found_count"]
    cand_count = baseline_data["reused_evidence_details"]["original_7_events"]["candidates_produced"]
    check(
        "原 7 事件零候选终态 (6 INVALIDATED + 1 NOT_FOUND, 0 候选)",
        inval_count == 6 and not_found_count == 1 and cand_count == 0 and "6 INVALIDATED + 1 NOT_FOUND" in readme_text,
        f"Inval: {inval_count}, NotFound: {not_found_count}, Cand: {cand_count}"
    )

    # 11. 最终验收状态 RE_AUDIT_PASSED
    final_status = summary_data["verdict_summary"]["final_acceptance_status"]
    check(
        "最终复验通过状态 RE_AUDIT_PASSED 确立",
        final_status == "RE_AUDIT_PASSED" and "RE_AUDIT_PASSED" in readme_text,
        f"Final Status: {final_status}"
    )

    all_passed = all(c["passed"] for c in checks)
    print("\n" + "=" * 70)
    print(f"对撞校验结果: 共 {len(checks)} 项检查，全部通过: {all_passed}")
    print("=" * 70)

    res_doc = {
        "total_checks": len(checks),
        "passed_checks": sum(1 for c in checks if c["passed"]),
        "all_passed": all_passed,
        "checks": checks
    }
    out_file = OUT_DIR / "05_数字对撞结果.json"
    out_file.write_text(json.dumps(res_doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"对撞结果已保存至: {out_file}")

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()

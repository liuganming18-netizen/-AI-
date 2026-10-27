# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R2 · 06 机器可读交付（给 B 直接读取）

产出两个文件：
  out/06_机器可读_交付.json   逐候选 + 逐口径的完整字段，含来源映射与哈希
  out/06_机器可读_交付.csv    一行 = (口径, 候选)，便于表格工具直接读

字段设计原则：
  · 每个结论字段都能指回一个产物文件（`source_map`）；
  · 三态判定不用布尔，用枚举字符串，避免把「未证明」读成「通过」；
  · 金额保留 9 位小数，与 replay_calc 输出一致；
  · 不含生成时刻（放在 06_运行时刻.json），保证重跑字节稳定。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r2 as L

SCHEMA = "accum-early-06-w4-r2/v1"
SCRIPTS = ["00_范围与身份锚定", "01_周期证据分级", "02_缺口判据与完整性",
           "03_隔离反例", "04_真实记录核对", "05_两口径逐笔与汇总"]


def main() -> int:
    p00 = json.loads((L.OUT / "00_范围与身份锚定.json").read_text(encoding="utf-8"))
    p02 = json.loads((L.OUT / "02_缺口判据与完整性.json").read_text(encoding="utf-8"))
    p03 = json.loads((L.OUT / "03_隔离反例.json").read_text(encoding="utf-8"))
    p04 = json.loads((L.OUT / "04_真实记录核对.json").read_text(encoding="utf-8"))
    p05 = json.loads((L.OUT / "05_两口径逐笔与汇总.json").read_text(encoding="utf-8"))

    code_hashes = {}
    for s in SCRIPTS:
        p = Path(__file__).resolve().parent / f"{s}.py"
        code_hashes[s] = {"path": f"evidence/ACCUM-EARLY-06-W4/04_R2/scripts/{s}.py",
                          "sha256": L.sha256_file(p)}
    p = Path(__file__).resolve().parent / "lib_r2.py"
    code_hashes["lib_r2"] = {"path": "evidence/ACCUM-EARLY-06-W4/04_R2/scripts/lib_r2.py",
                             "sha256": L.sha256_file(p)}

    trade_by = {}
    for r in p05["per_trade"]:
        trade_by.setdefault(r["candidate_id"], {})[r["caliber"]] = r

    cands = []
    for rec in p02["per_candidate"]:
        cid = rec["candidate_id"]
        cal = {}
        for name in L.CALIBERS:
            t = trade_by.get(cid, {}).get(name)
            if not t:
                cal[name] = {"status": "missing_in_05"}
                continue
            cal[name] = {
                "status": t["status"],
                "is_filled": t.get("is_filled"),
                "funding_old": t.get("funding_old"),
                "funding_new": t.get("funding_new"),
                "delta_funding": t.get("delta_funding"),
                "net_old": t.get("net_old"),
                "net_new": t.get("net_new"),
                "delta_net": t.get("delta_net"),
                "events_old": t.get("events_old"),
                "events_new": t.get("events_new"),
                "change_reason": t.get("change_reason"),
                "boundary_jitter_cost_if_included": t.get("boundary_jitter_cost_if_included"),
                "participation_capped": t.get("participation_capped"),
                "completeness": t.get("completeness"),
                "claim": t.get("claim"),
            }
        cands.append({
            "candidate_id": cid,
            "symbol": rec["symbol"],
            "entry_utc": rec["entry_utc"],
            "entry_ms": rec["entry_ms"],
            "exit_utc": rec["exit_utc"],
            "exit_ms": rec["exit_ms"],
            "hold_hours": rec["hold_hours"],
            # ── 周期证据 ──
            "interval_basis": rec["interval_basis"],
            "interval_step_h": rec["step_h"],
            "interval_evidence_note": rec["interval_evidence_note"],
            "official": {
                "interval_hours": (rec["official"] or {}).get("interval_hours"),
                "update_utc": (rec["official"] or {}).get("update_utc"),
                "update_ms": (rec["official"] or {}).get("update_ms"),
                "covers_hold": rec["official_covers_hold"],
            },
            # ── 相位证据 ──
            "phase_h": rec["phase_h"],
            "phase_basis": rec["phase_basis"],
            "phase_support": rec["phase_support"],
            "phase_total": rec["phase_total"],
            # ── 预期 vs 实际 ──
            "expected_count": rec["expected_count"],
            "actual_count_strict": rec["actual_count_strict"],
            "actual_count_with_boundary_tol": rec["actual_count_with_boundary_tol"],
            "matched_count": rec["matched_count"],
            "missing_count": rec["missing_count"],
            "missing_instants_utc": rec["missing_instants_utc"],
            "off_grid_count": rec["off_grid_count"],
            "off_grid_instants_utc": rec["off_grid_instants_utc"],
            "trivial": rec["trivial"],
            # ── 端点 / 字段 ──
            "boundary_jitter_count": rec["boundary_jitter_count"],
            "boundary_dependent_count": rec["boundary_dependent_count"],
            "boundary_status": rec["boundary_status"],
            "field_defect_count": rec["field_defect_count"],
            # ── 结论 ──
            "completeness": rec["completeness"],
            "claim": rec["claim"],
            "reason": rec["reason"],
            # ── 两口径 ──
            "calibers": cal,
        })

    out = {
        "schema": SCHEMA,
        "task": "ACCUM-EARLY-06-W4-R2",
        "scope": {
            "candidates": len(cands),
            "symbols": len({c["symbol"] for c in cands}),
            "calibers": list(L.CALIBERS),
            "window_lo_utc": p00["A_范围"]["window_lo_utc"],
            "window_hi_utc": p00["A_范围"]["window_hi_utc"],
        },
        "enum": {
            "interval_basis": {
                "official_snapshot": "官方快照 interval 自 updateTime 起生效，覆盖整个持仓",
                "official_partial": "官方快照存在但 updateTime 晚于入场，前段无官方证据",
                "inferred_uniform": "观测间隔恒定（仅推断）",
                "inferred_with_multiples": "观测间隔为同一基数整数倍（缺事件痕迹，仅推断）",
                "switch_detected": "观测到周期切换",
                "undetermined": "不可判定",
            },
            "completeness": {
                "NO_GAP_DETECTED": "在证据网格下未发现缺失（≠已证明完整）",
                "MISSING_DEMONSTRATED": "官方周期下确有缺失 ⇒ 完整性被否证",
                "INCONSISTENT_INFERRED": "仅推断周期下与网格不符（不能叫已证明缺失）",
                "UNDETERMINED": "周期/相位不足或持仓内切换",
                "NOT_APPLICABLE": "拒单等不适用",
            },
            "claim": {
                "PROVEN": "官方周期覆盖 + 相位观测一致 + 无缺失（仍不含「当时可得」）",
                "NOT_PROVEN": "周期或相位只有推断证据，或端点抖动有歧义",
                "REFUTED": "已演示缺失",
                "UNKNOWN": "不可判定",
            },
            "change_reason": {
                "NO_CHANGE": "热库与合并后事件相同",
                "COLD_FILL_ADDED": "合并补入冷档事件，资金费变化",
                "BOUNDARY_JITTER_ONLY": "仅端点毫秒抖动，资金费不变",
                "REJECTED": "入场即被拒，无持仓",
            },
        },
        "inputs": {
            "hashes": p00["E_代码与输入哈希"],
            "hot_db": p00["D_数据身份"]["hot_db"],
            "cold_archive": p00["D_数据身份"]["cold_archive"],
            "official_snapshot": {
                "path": "evidence/ACCUM-EARLY-06-W4/04_R2/out/00_官方快照_fundingInfo.json",
                "rows_sha256": p00["B_官方快照"]["rows_sha256"],
                "fetched_at_utc": p00["B_官方快照"]["fetched_at_utc"],
                "n_rows": p00["B_官方快照"]["n_rows"],
                "source": p00["B_官方快照"]["source"],
            },
        },
        "code_hashes": code_hashes,
        "summary_by_caliber": p05["汇总"],
        "completeness_summary": p02["汇总"],
        "real_evidence": {
            "r1_false_missing": p04["R1_R1缺失复核"],
            "cycle_switch": {"n_symbols": p04["R2_周期切换实证"]["有切换点的标的数"],
                             "official_matches_last_regime":
                                 p04["R2_周期切换实证"]["官方与最后一段一致"]},
            "phase": p04["R3_相位实证"],
            "boundary_jitter": p04["R4_端点抖动实证"],
            "nonzero_but_growing": p04["R5_非零结算但仍漏"],
            "cold_fill_on_grid": p04["R6_补入事件是否在网格上"],
        },
        "synthetic_counterexamples": [
            {"id": c["id"], "title": c["title"],
             "r1": c["r1"], "r2": c["r2"]} for c in p03["cases"]],
        "source_map": {
            "范围与身份": "out/00_范围与身份锚定.json",
            "周期证据分级": "out/01_周期证据分级.json",
            "缺口判据与完整性": "out/02_缺口判据与完整性.json",
            "隔离反例": "out/03_隔离反例.json",
            "真实记录核对": "out/04_真实记录核对.json",
            "两口径逐笔与汇总": "out/05_两口径逐笔与汇总.json",
            "官方快照原文": "out/00_官方快照_fundingInfo.json",
            "正文数字台账": "out/07_正文数字台账.json",
        },
        "candidates": cands,
    }

    (L.OUT / "06_机器可读_交付.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    cols = ["caliber", "candidate_id", "symbol", "entry_utc", "exit_utc", "hold_hours",
            "interval_basis", "interval_step_h", "phase_h", "official_interval_h",
            "official_covers_hold", "expected_count", "actual_count_strict", "missing_count",
            "off_grid_count", "boundary_jitter_count", "boundary_dependent_count",
            "field_defect_count", "completeness", "claim",
            "status", "funding_old", "funding_new", "delta_funding",
            "net_old", "net_new", "delta_net", "events_old", "events_new",
            "change_reason", "boundary_jitter_cost_if_included"]
    with open(L.OUT / "06_机器可读_交付.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in cands:
            for name in L.CALIBERS:
                t = c["calibers"][name]
                w.writerow([
                    name, c["candidate_id"], c["symbol"], c["entry_utc"], c["exit_utc"],
                    c["hold_hours"], c["interval_basis"], c["interval_step_h"], c["phase_h"],
                    c["official"]["interval_hours"], c["official"]["covers_hold"],
                    c["expected_count"], c["actual_count_strict"], c["missing_count"],
                    c["off_grid_count"], c["boundary_jitter_count"],
                    c["boundary_dependent_count"], c["field_defect_count"],
                    c["completeness"], c["claim"],
                    t.get("status"), t.get("funding_old"), t.get("funding_new"),
                    t.get("delta_funding"), t.get("net_old"), t.get("net_new"),
                    t.get("delta_net"), t.get("events_old"), t.get("events_new"),
                    t.get("change_reason"), t.get("boundary_jitter_cost_if_included"),
                ])

    # 运行时刻单独落盘，保证上面两个文件字节稳定
    import time
    (L.OUT / "06_运行时刻.json").write_text(
        json.dumps({"generated_at_utc": L.iso(int(time.time() * 1000))},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"候选 {len(cands)} | 口径行 {len(cands) * len(L.CALIBERS)}")
    print("JSON:", (L.OUT / "06_机器可读_交付.json").stat().st_size, "字节")
    print("CSV :", (L.OUT / "06_机器可读_交付.csv").stat().st_size, "字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

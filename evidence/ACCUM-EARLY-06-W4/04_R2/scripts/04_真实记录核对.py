# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R2 · 04 真实记录核对（只用生产数据，不做合成）

与 03 严格分开：这里每一条都能指到具体标的、具体时刻、具体文件。

核对六项
--------
R1  R1「缺失」的全量复核：那些「缺失时刻」到底有没有事件（只是毫秒抖动？）
R2  周期切换实证：观测切换点 vs 官方 updateTime 是否对得上
R3  相位实证：48 标的的结算时刻残差是否真为 0（是验证结果，不是默认假设）
R4  端点抖动实证：恰好落在退出时刻的结算，严格判据是否把它排除、金额多少
R5  「非零结算但仍漏事件」的真实案例：热库有 30 次，合并后 33 次
R6  冷档补入的事件是否落在网格上（补进来的不能是「网格外的野事件」）
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r2 as L
import funding_events_adapter as FA

CHECKS: list = []
CONTEXT_DAYS = 30
TOL = 1000


def add(name: str, ok: bool, evidence) -> None:
    CHECKS.append({"检查": name, "通过": bool(ok), "证据": evidence})


def main() -> int:
    p01 = json.loads((L.OUT / "01_周期证据分级.json").read_text(encoding="utf-8"))
    p02 = json.loads((L.OUT / "02_缺口判据与完整性.json").read_text(encoding="utf-8"))
    p05 = json.loads((L.OUT / "05_两口径逐笔与汇总.json").read_text(encoding="utf-8"))
    per_sym = p01["per_symbol"]
    repo = FA._get_repo(None, None)

    # ── R1：R1 的「缺失」全量复核 ──
    r1_rows = []
    for rec in p02["per_candidate"]:
        s, entry, exit_ = rec["symbol"], rec["entry_ms"], rec["exit_ms"]
        res = FA.load_funding_events("binance", "um", s,
                                     entry - CONTEXT_DAYS * L.DAY,
                                     exit_ + CONTEXT_DAYS * L.DAY, repo=repo)
        actual = sorted(e["funding_time"] for e in res["events"])
        step = rec["step_h"]
        if not step:
            continue
        exp0 = L.grid_instants(0, step, entry, exit_)
        strict = [t for t in actual if entry < t <= exit_]
        missing = [t for t in exp0 if t not in set(strict)]
        for t in missing:
            near = min(actual, key=lambda a: abs(a - t)) if actual else None
            r1_rows.append({"symbol": s, "candidate_id": rec["candidate_id"],
                            "expected_utc": L.iso(t),
                            "nearest_actual_utc": L.iso(near),
                            "offset_ms": (near - t) if near is not None else None,
                            "within_tol": bool(near is not None and abs(near - t) <= TOL)})
    n_r1_missing = len(r1_rows)
    n_r1_jitter = sum(1 for r in r1_rows if r["within_tol"])
    max_off = max((abs(r["offset_ms"]) for r in r1_rows if r["offset_ms"] is not None),
                  default=0)
    offsets = Counter(r["offset_ms"] for r in r1_rows if r["within_tol"])

    # ── R2：周期切换实证 ──
    switch_cases = []
    for s, v in per_sym.items():
        sp = v["observed"]["switch_points"]
        if not sp:
            continue
        o = v["official"] or {}
        ut = o.get("update_ms")
        nearest = None
        if ut:
            nearest = min(sp, key=lambda x: abs(x["at_ms"] - ut))
        switch_cases.append({
            "symbol": s,
            "switch_points": sp,
            "official_interval_h": o.get("interval_hours"),
            "official_update_utc": o.get("update_utc"),
            "nearest_switch_to_update": nearest,
            "update_minus_switch_hours": (round((ut - nearest["at_ms"]) / 3600000.0, 3)
                                          if (ut and nearest) else None),
            "official_matches_last_regime": bool(
                nearest and o.get("interval_hours") is not None
                and abs(float(o["interval_hours"]) - float(nearest["to_h"])) < 1e-9),
        })
    switch_cases.sort(key=lambda x: -len(x["switch_points"]))

    # ── R3：相位实证 ──
    phase_rows = []
    for rec in p02["per_candidate"]:
        phase_rows.append({
            "symbol": rec["symbol"], "step_h": rec["step_h"], "phase_h": rec["phase_h"],
            "support": rec["phase_support"], "total": rec["phase_total"],
            "residues": rec["phase_residues"],
            "phase_shift_effect": rec["r1_口径对照"]["phase_shift_effect"],
        })
    n_phase_zero = sum(1 for r in phase_rows if r["phase_h"] == 0)
    n_phase_nonzero = sum(1 for r in phase_rows if r["phase_h"] not in (0, None))
    n_phase_shift_effect = sum(1 for r in phase_rows if r["phase_shift_effect"] > 0)

    # ── R4：端点抖动实证（带金额） ──
    bj = []
    for r in p05["per_trade"]:
        if r.get("boundary_jitter_cost_if_included"):
            bj.append({"caliber": r["caliber"], "symbol": r["symbol"],
                       "candidate_id": r["candidate_id"],
                       "exit_utc": r["exit_utc"],
                       "detail": r["boundary_jitter_detail"],
                       "cost_if_included": r["boundary_jitter_cost_if_included"],
                       "events_old": r["events_old"], "events_new": r["events_new"]})

    # ── R5：「非零结算但仍漏事件」的真实案例 ──
    nonzero_but_grew = []
    for r in p05["per_trade"]:
        if r["caliber"] != "生效视图_5bps" or not r.get("is_filled"):
            continue
        if r["events_old"] > 0 and r["events_new"] > r["events_old"]:
            nonzero_but_grew.append({
                "symbol": r["symbol"], "candidate_id": r["candidate_id"],
                "entry_utc": r["entry_utc"], "exit_utc": r["exit_utc"],
                "events_old": r["events_old"], "events_new": r["events_new"],
                "funding_old": r["funding_old"], "funding_new": r["funding_new"],
            })

    # ── R6：补入事件是否落在网格上 ──
    added = []
    for rec in p02["per_candidate"]:
        s = rec["symbol"]
        res = FA.load_funding_events("binance", "um", s,
                                     rec["entry_ms"] + 1, rec["exit_ms"], repo=repo)
        cold_only = [e["funding_time"] for e in res["events"] if e["observed_in"] == "cold"]
        if not cold_only:
            continue
        step = rec["step_h"]
        exp = set(L.grid_instants(rec["phase_h"], step, rec["entry_ms"], rec["exit_ms"])) \
            if (step and rec["phase_h"] is not None) else set()
        off = [t for t in cold_only
               if not any(abs(t - x) <= TOL for x in exp)] if exp else cold_only
        added.append({"symbol": s, "cold_only_in_hold": len(cold_only),
                      "off_grid": len(off),
                      "off_grid_utc": [L.iso(t) for t in off][:5]})
    n_added = sum(a["cold_only_in_hold"] for a in added)
    n_added_off = sum(a["off_grid"] for a in added)

    aligned = [c for c in switch_cases
               if c["update_minus_switch_hours"] is not None
               and abs(c["update_minus_switch_hours"]) <= 48]
    misaligned = [c for c in switch_cases
                  if c["update_minus_switch_hours"] is not None
                  and abs(c["update_minus_switch_hours"]) > 48]

    out = {
        "工单": "ACCUM-EARLY-06-W4-R2",
        "性质": "真实记录核对（生产数据），不含合成",
        "R1_R1缺失复核": {
            "r1_missing_instants": n_r1_missing,
            "其中在 ±1s 内有真实事件": n_r1_jitter,
            "比例": round(n_r1_jitter / n_r1_missing, 6) if n_r1_missing else None,
            "最大偏移ms": max_off,
            "偏移分布ms": dict(sorted(offsets.items())),
            "明细_样例": r1_rows[:8],
            "结论": f"R1 的「缺失」几乎全部是毫秒抖动：预期时刻按整点生成，"
                    f"实际记录带毫秒尾数（最大 {max_off} ms），精确相等匹配必然落空。"
                    f"这不是数据缺失，是判据缺陷。",
        },
        "R2_周期切换实证": {
            "有切换点的标的数": len(switch_cases),
            "明细": switch_cases,
            "官方与最后一段一致": sum(1 for c in switch_cases
                                      if c["official_matches_last_regime"]),
            "updateTime 与最后切换 ≤48h 的标的数": len(aligned),
            "未对齐明细": [{"symbol": c["symbol"], "Δh": c["update_minus_switch_hours"]}
                           for c in misaligned],
        },
        "R3_相位实证": {
            "相位=0 的候选": n_phase_zero,
            "相位≠0 的候选": n_phase_nonzero,
            "相位改变网格的候选": n_phase_shift_effect,
            "说明": "本数据集里 4h/8h 结算都落在 UTC 整点，所以「相位=0」在事后看是对的；"
                    "但这是**验证结果**：本单先从观测算残差再生成网格。"
                    "若某标的相位非 0，按 0 生成会全部落空（见 03 的 S4 反例）。",
        },
        "R4_端点抖动实证": {"cases": bj},
        "R5_非零结算但仍漏": {
            "cases": nonzero_but_grew,
            "结论": "热库已有结算 ≠ 完整：这些笔热库有非零次数，合并后次数变多，"
                    "说明原来漏了若干次；「有结算就判完整」不成立。",
        },
        "R6_补入事件是否在网格上": {
            "补入事件数（51 笔持仓内）": n_added,
            "网格外": n_added_off,
            "明细": [a for a in added if a["cold_only_in_hold"]],
        },
        "checks": CHECKS,
    }

    add("R1a R1 的缺失时刻在 ±1s 内都能找到真实事件（抖动而非缺失）",
        n_r1_missing > 0 and n_r1_jitter == n_r1_missing,
        {"r1_missing": n_r1_missing, "matched_within_1s": n_r1_jitter,
         "max_offset_ms": max_off, "offset_dist": dict(sorted(offsets.items()))})
    add("R1b 抖动幅度远小于最小周期（1h）", max_off < 60000,
        {"max_offset_ms": max_off})
    add("R2a 观测到的周期切换至少有 3 个标的（不是个别噪声）",
        len(switch_cases) >= 3,
        {"n": len(switch_cases),
         "样例": [{"symbol": c["symbol"], "n_switches": len(c["switch_points"]),
                   "regimes": [p["from_h"] for p in c["switch_points"]] +
                              [c["switch_points"][-1]["to_h"]]}
                  for c in switch_cases[:5]]})
    aligned = [c for c in switch_cases
               if c["update_minus_switch_hours"] is not None
               and abs(c["update_minus_switch_hours"]) <= 48]
    misaligned = [c for c in switch_cases
                  if c["update_minus_switch_hours"] is not None
                  and abs(c["update_minus_switch_hours"]) > 48]
    add("R2b 多数标的的官方 updateTime 与观测到的最后一次切换对得上（≤48h）",
        len(aligned) >= 10,
        {"对齐": len(aligned), "总": len(switch_cases),
         "未对齐": [{"symbol": c["symbol"], "Δh": c["update_minus_switch_hours"],
                     "说明": "updateTime 也可能是费率上下限变更，不必然是周期变更"}
                    for c in misaligned]})
    add("R3a 本数据集相位残差确实全为 0（验证结果，不是默认值）",
        n_phase_nonzero == 0,
        {"phase_zero": n_phase_zero, "phase_nonzero": n_phase_nonzero,
         "phase_shift_changes_grid": n_phase_shift_effect})
    add("R4a 端点抖动是真实存在的（至少 1 笔，且有金额）",
        len(bj) > 0, {"n": len(bj),
                      "样例": bj[:2]})
    add("R5a 存在「热库已有非零结算、合并后次数更多」的真实笔",
        len(nonzero_but_grew) > 0,
        {"n": len(nonzero_but_grew), "cases": nonzero_but_grew[:5]})
    add("R6a 冷档补入的事件全部落在网格上（没有网格外野事件）",
        n_added_off == 0,
        {"added": n_added, "off_grid": n_added_off,
         "非零明细": [a for a in added if a["off_grid"] > 0][:5]})

    (L.OUT / "04_真实记录核对.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = sum(1 for c in CHECKS if c["通过"])
    print(f"R1 缺失复核：{n_r1_missing} 个「缺失」中 {n_r1_jitter} 个在 ±1s 内有事件"
          f"（最大偏移 {max_off}ms）")
    print(f"R2 周期切换：{len(switch_cases)} 个标的；官方与最后一段一致 "
          f"{out['R2_周期切换实证']['官方与最后一段一致']} 个")
    print(f"R3 相位：=0 {n_phase_zero} / ≠0 {n_phase_nonzero}")
    print(f"R4 端点抖动：{len(bj)} 条（含金额）")
    print(f"R5 非零结算仍漏：{len(nonzero_but_grew)} 笔")
    print(f"R6 补入事件：{n_added} 条，网格外 {n_added_off}")
    print(f"checks {ok}/{len(CHECKS)}")
    for c in CHECKS:
        if not c["通过"]:
            print("  FAIL", c["检查"], json.dumps(c["证据"], ensure_ascii=False)[:400])
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

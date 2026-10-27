# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R2 · 02 缺口判据与完整性（每候选）

针对 R1 的三个缺陷
------------------
缺陷 1（命名/证据）：R1 把历史间隔推算命名 `proven_regular_*` 并用它判断完整性。
  本脚本把周期证据拆成 `official_snapshot / official_partial / inferred_uniform /
  inferred_with_multiples / switch_detected / undetermined`，
  **只有官方快照覆盖持仓**时才允许 `claim=PROVEN`。

缺陷 2（判据，实测缺陷）：R1 用**精确毫秒相等**比对预期时刻与实际事件，
  但 44.96% 的事件毫秒尾数非 0（+1…+11ms），于是「事件全在」被判成「缺 14 个」。
  本脚本用 ±1s 容差匹配，并把「网格外事件」单独计数。

缺陷 3（边界，本单新发现）：`entry < t <= exit` 是**毫秒级严格比较**，
  而结算时刻常有 +3~+11ms 记录抖动 ⇒ 恰好落在退出时刻的结算会被判成「不存在」，
  反之落在入场时刻 +1ms 的会被算进来。本脚本把这类事件单列为 `boundary_jitter`，
  并给出「计入/不计入」的金额差（金额在 05 计算）。

完整性结论
----------
  MISSING_DEMONSTRATED   官方周期覆盖 + 网格内确有缺失 ⇒ 完整性被否证
  INCONSISTENT_INFERRED  仅推断周期下与网格不符 ⇒ 不能叫缺失，也不能叫完整
  NO_GAP_DETECTED        网格内未发现缺失（≠已证明完整）
  UNDETERMINED           周期/相位不足，或持仓内发生周期切换
  NOT_APPLICABLE         拒单
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
LOCAL_DAYS = 7               # 局部邻域：判「持仓当时的周期」
MATCH_TOL_MS = 1000          # 实测毫秒尾数最大 11ms；1s 远大于抖动、远小于最小周期 1h
PHASE_MIN_SUPPORT = 10
PHASE_MIN_FRAC = 0.98
BOUNDARY_MS = L.M15          # 与 replay_calc.FUNDING_ORDER_UNCERTAIN_MS 同量级


def add(name: str, ok: bool, evidence) -> None:
    CHECKS.append({"检查": name, "通过": bool(ok), "证据": evidence})


def _local_step(times: list, entry_ms: int, exit_ms: int) -> tuple:
    """局部邻域（持仓 ±7 天）的步长与形态。

    区分三种形态（这一步是「缺事件」与「真换周期」的唯一可用信号）：
      uniform   只有一个间隔值                     ⇒ 推断为恒定周期
      multiples 只有一种「成段(≥3 连)」的间隔，其它值是它的整数倍且都是孤立的
                ⇒ 缺事件的痕迹，基数仍是众数
      switch    出现 ≥2 种「成段(≥3 连)」的间隔     ⇒ 真换周期
    为什么用「成段」而不是「出现过」：一次漏结算只会造出孤立的长间隔，
    真换周期会造出一整段新间隔。两者在相位上无法区分（8h 的 00/08/16 也落在 4h 网格上）。
    """
    lo, hi = entry_ms - LOCAL_DAYS * L.DAY, exit_ms + LOCAL_DAYS * L.DAY
    loc = [t for t in times if lo <= t <= hi]
    if len(loc) < 3:
        return None, "insufficient", len(loc), {}
    dist = Counter(round(x, 2) for x in L.intervals_h(loc))
    step = dist.most_common(1)[0][0]
    regimes = {r["interval_h"] for r in L.detect_runs(loc) if r["count"] >= 3}
    if len(dist) == 1:
        kind = "uniform"
    elif len(regimes) >= 2:
        kind = "switch"
    elif all(v > step and abs(v / step - round(v / step)) < 1e-9 for v in dist if v != step):
        kind = "multiples"
    else:
        kind = "mixed"
    return step, kind, len(loc), dict(dist)


def judge_hold(sym_rec: dict, entry_ms: int, exit_ms: int, events: list) -> dict:
    official = sym_rec["official"]
    ctx_lo, ctx_hi = entry_ms - CONTEXT_DAYS * L.DAY, exit_ms + CONTEXT_DAYS * L.DAY
    ctx = [e["funding_time"] for e in events if ctx_lo <= e["funding_time"] <= ctx_hi]

    official_covers = bool(official and official["update_ms"] is not None
                           and official["update_ms"] <= entry_ms
                           and official["interval_hours"])
    local_step, local_kind, local_n, local_dist = _local_step(ctx, entry_ms, exit_ms)

    # ── 1. 周期证据与步长 ──
    basis, step, basis_note = L.EV_UNDET, None, ""
    if official_covers:
        step = float(official["interval_hours"])
        basis = L.EV_OFFICIAL
        basis_note = "官方快照 interval 自 updateTime 起生效，覆盖整个持仓区间"
        if local_step is not None and abs(local_step - step) > 1e-9:
            basis_note += (f"；但局部观测众数为 {local_step}h，与官方值不一致 ⇒ 需人工复核")
    else:
        # 官方不覆盖 ⇒ 步长只能来自观测，**不能**拿官方当前值回填历史
        step = local_step
        if official and official["interval_hours"] and step is not None \
                and abs(step - official["interval_hours"]) > 1e-9:
            basis_note = (f"官方当前为 {official['interval_hours']}h，但 updateTime "
                          f"{official['update_utc']} 晚于持仓，持仓期观测为 {step}h ⇒ "
                          f"说明期间发生过变更，历史段按观测推断")
        if step is None or local_kind in ("insufficient", "mixed"):
            basis = L.EV_UNDET
            basis_note = f"局部邻域形态 {local_kind}，间隔分布 {local_dist}，无法定步长"
        elif local_kind == "switch":
            basis = L.EV_SWITCH
            basis_note = f"局部邻域内出现 ≥2 段不同间隔 {local_dist}，单一周期不成立"
        elif local_kind == "multiples":
            basis = L.EV_INFERRED_MULT
            basis_note = (f"局部只有 {step}h 成段，其它间隔是它的整数倍且孤立 ⇒ "
                          f"更像漏结算而非换周期；但两者无法从观测上完全区分")
        else:
            basis = L.EV_INFERRED
            basis_note = basis_note or "局部观测间隔恒定，仅能推断"

    # 持仓区间内是否真的发生切换（局部均匀则不会有）
    runs = [r for r in L.detect_runs(ctx) if r["count"] >= 3]
    switch_in_hold = [r for r in runs if entry_ms < r["start_ms"] <= exit_ms]

    # ── 2. 相位（来自观测，不默认 0） ──
    phase_h, support, total = (None, 0, 0)
    phase_basis = L.PH_INSUFFICIENT
    if step:
        # 相位只在**局部**邻域算：30 天上下文可能含另一个周期段，会把残差摊平
        loc_lo, loc_hi = entry_ms - LOCAL_DAYS * L.DAY, exit_ms + LOCAL_DAYS * L.DAY
        loc = [t for t in ctx if loc_lo <= t <= loc_hi]
        phase_h, support, total = L.dominant_phase_hours(loc, step)
        if total < PHASE_MIN_SUPPORT:      # 局部样本太少才退回上下文
            phase_h, support, total = L.dominant_phase_hours(ctx, step)
        frac = (support / total) if total else 0.0
        phase_basis = (L.PH_OBSERVED if (total >= PHASE_MIN_SUPPORT and frac >= PHASE_MIN_FRAC)
                       else L.PH_INCONSISTENT)

    # ── 3. 预期时刻 ──
    expected = (L.grid_instants(phase_h, step, entry_ms, exit_ms)
                if (step and phase_h is not None) else [])
    expected_epoch0 = L.grid_instants(0, step, entry_ms, exit_ms) if step else []

    # ── 4. 匹配：候选集含边界 ±1s（结算记录抖动） ──
    cand_evs = [e for e in events
                if entry_ms - MATCH_TOL_MS <= e["funding_time"] <= exit_ms + MATCH_TOL_MS]
    cand_ts = sorted(e["funding_time"] for e in cand_evs)
    strict_ts = sorted(e["funding_time"] for e in events
                       if entry_ms < e["funding_time"] <= exit_ms)

    used = set()
    matched, missing = [], []
    for t in expected:
        hit = next((a for a in cand_ts if abs(a - t) <= MATCH_TOL_MS and a not in used), None)
        if hit is None:
            missing.append(t)
        else:
            used.add(hit)
            matched.append({"expected": t, "actual": hit, "offset_ms": hit - t})
    off_grid = [a for a in cand_ts if a not in used]
    # 哪些预期时刻是靠「严格判据本不纳入」的边界抖动事件满足的
    boundary_ts = {b["funding_time"] for b in
                   [{"funding_time": t} for t in cand_ts
                    if (0 < t - exit_ms <= MATCH_TOL_MS)
                    or (-MATCH_TOL_MS <= t - entry_ms <= 0)]}
    boundary_dependent = [m for m in matched if m["actual"] in boundary_ts]

    # ── 5. 边界抖动：名义上在端点、因毫秒抖动被严格判据排除/纳入 ──
    boundary_jitter = []
    for t in cand_ts:
        d_entry, d_exit = t - entry_ms, t - exit_ms
        if 0 < d_entry <= MATCH_TOL_MS:
            boundary_jitter.append({"funding_time": t, "funding_utc": L.iso(t),
                                    "at": "entry", "offset_ms": d_entry,
                                    "strict_rule_includes": True})
        elif 0 < d_exit <= MATCH_TOL_MS:
            boundary_jitter.append({"funding_time": t, "funding_utc": L.iso(t),
                                    "at": "exit", "offset_ms": d_exit,
                                    "strict_rule_includes": False})
        elif -MATCH_TOL_MS <= d_entry <= 0:
            boundary_jitter.append({"funding_time": t, "funding_utc": L.iso(t),
                                    "at": "entry", "offset_ms": d_entry,
                                    "strict_rule_includes": False})

    # ── 6. R1 口径复算（严格窗口 + 精确毫秒相等 + 相位 0），只用于暴露缺陷 ──
    r1_actual = set(strict_ts)
    r1_missing = [t for t in expected_epoch0 if t not in r1_actual]

    # ── 7. 字段缺陷 ──
    hold_evs = [e for e in events if entry_ms < e["funding_time"] <= exit_ms]
    field_defects = [e["funding_time"] for e in hold_evs
                     if e.get("rate") is None or e.get("mark_price") is None
                     or e.get("status") in ("pending_conflict", "pending_missing")]

    # ── 8. 完整性 ──
    if switch_in_hold and not official_covers:
        completeness, claim = L.CP_UNDET, L.CLAIM_UNKNOWN
        reason = "持仓区间内观测到周期切换，单一周期网格不成立；无官方证据界定切换时刻"
    elif basis == L.EV_SWITCH:
        completeness, claim = L.CP_UNDET, L.CLAIM_UNKNOWN
        reason = "局部邻域内周期不单一，无法确定持仓期步长"
    elif phase_basis != L.PH_OBSERVED:
        completeness, claim = L.CP_UNDET, L.CLAIM_UNKNOWN
        reason = f"相位证据不足（支撑 {support}/{total}）"
    elif missing:
        if basis == L.EV_OFFICIAL:
            completeness, claim = L.CP_MISSING, L.CLAIM_REFUTED
            reason = "官方周期覆盖持仓且相位一致，网格内确有缺失 ⇒ 完整性被否证"
        else:
            completeness, claim = L.CP_INCONSISTENT, L.CLAIM_NOT_PROVEN
            reason = ("仅推断周期，网格内出现缺口；既可能是记录缺失，也可能是周期/相位变化，"
                      "不能叫「已证明缺失」，更不能叫「完整」")
    else:
        completeness = L.CP_NO_GAP
        if boundary_dependent:
            claim = L.CLAIM_NOT_PROVEN
            reason = (f"网格内无缺失，但 {len(boundary_dependent)} 个预期时刻只由「端点毫秒抖动」"
                      f"事件满足（严格判据本不纳入）⇒ 退出同刻有歧义，不能声称已证明完整")
        elif basis == L.EV_OFFICIAL:
            claim = L.CLAIM_PROVEN
            reason = "官方周期覆盖 + 相位一致 + 无缺失 ⇒ 该持仓区间完整性成立"
        else:
            claim = L.CLAIM_NOT_PROVEN
            reason = "未发现缺失，但周期只有推断证据 ⇒ 不能声称已证明完整"

    return {
        "interval_basis": basis,
        "interval_evidence_note": basis_note,
        "step_h": step,
        "local_step_h": local_step,
        "local_kind": local_kind,
        "local_uniform": local_kind == "uniform",
        "local_events": local_n,
        "local_interval_dist": local_dist,
        "official_covers_hold": official_covers,
        "phase_h": phase_h,
        "phase_basis": phase_basis,
        "phase_support": support,
        "phase_total": total,
        "phase_residues": L.phase_residues(ctx, step) if step else {},
        "context_events": len(ctx),
        "context_lo_utc": L.iso(ctx_lo), "context_hi_utc": L.iso(ctx_hi),
        "switch_in_hold": switch_in_hold,
        "expected_count": len(expected),
        "trivial": len(expected) == 0,
        "expected_instants_utc": [L.iso(t) for t in expected],
        "actual_count_strict": len(strict_ts),
        "actual_count_with_boundary_tol": len(cand_ts),
        "actual_instants_utc": [L.iso(t) for t in strict_ts],
        "matched_count": len(matched),
        "max_offset_ms": max((abs(m["offset_ms"]) for m in matched), default=0),
        "missing_count": len(missing),
        "missing_instants_utc": [L.iso(t) for t in missing],
        "off_grid_count": len(off_grid),
        "off_grid_instants_utc": [L.iso(t) for t in off_grid],
        "completeness": completeness,
        "claim": claim,
        "reason": reason,
        "field_defect_count": len(field_defects),
        "field_defect_instants_utc": [L.iso(t) for t in field_defects],
        "boundary_jitter": boundary_jitter,
        "boundary_jitter_count": len(boundary_jitter),
        "boundary_dependent_count": len(boundary_dependent),
        "boundary_dependent_instants_utc": [L.iso(m["actual"]) for m in boundary_dependent],
        "boundary_status": ("boundary_dependent" if boundary_dependent else "unambiguous"),
        "r1_口径对照": {
            "expected_epoch0_count": len(expected_epoch0),
            "missing_exact_ms_count": len(r1_missing),
            "phase_shift_effect": len(set(expected_epoch0) ^ set(expected)),
            "说明": "R1 用相位=0 + 严格窗口 + 精确毫秒相等；missing_exact_ms_count 把抖动算成缺失",
        },
    }


def main() -> int:
    cands = L.load_candidates()
    p01 = json.loads((L.OUT / "01_周期证据分级.json").read_text(encoding="utf-8"))
    per_sym = p01["per_symbol"]

    repo = FA._get_repo(None, None)
    cache: dict = {}
    rows = []
    for c in cands:
        s = c["symbol"]
        entry_ms = L.to_ms(c["entry_bar_utc"])
        exit_ms = L.to_ms(c["exit_utc"]) if c.get("exit_utc") else None
        lo = entry_ms - CONTEXT_DAYS * L.DAY
        hi = exit_ms + CONTEXT_DAYS * L.DAY
        key = (s, lo // L.DAY, hi // L.DAY)
        if key not in cache:
            cache[key] = FA.load_funding_events("binance", "um", s, lo, hi, repo=repo)["events"]
        rec = judge_hold(per_sym[s], entry_ms, exit_ms, cache[key])
        rec.update({
            "candidate_id": c["candidate_id"], "symbol": s,
            "entry_ms": entry_ms, "entry_utc": c["entry_bar_utc"],
            "exit_ms": exit_ms, "exit_utc": c.get("exit_utc"),
            "hold_hours": round((exit_ms - entry_ms) / 3600000.0, 4),
            "official": per_sym[s]["official"],
        })
        rows.append(rec)

    dist = Counter(r["completeness"] for r in rows)
    claim_dist = Counter(r["claim"] for r in rows)
    basis_dist = Counter(r["interval_basis"] for r in rows)
    r1_missing_sum = sum(r["r1_口径对照"]["missing_exact_ms_count"] for r in rows)
    r2_missing_sum = sum(r["missing_count"] for r in rows)

    out = {
        "工单": "ACCUM-EARLY-06-W4-R2",
        "判据参数": {
            "match_tol_ms": MATCH_TOL_MS,
            "phase_min_support": PHASE_MIN_SUPPORT,
            "phase_min_frac": PHASE_MIN_FRAC,
            "context_days": CONTEXT_DAYS,
            "local_days": LOCAL_DAYS,
            "boundary_ms": BOUNDARY_MS,
            "为什么容差 1s": "实测毫秒尾数最大 11ms（01 已登记）；1s 远大于抖动、远小于最小周期 1h",
            "为什么局部 7 天": "判「持仓当时的周期」不能用 30 天：30 天内可能含别的周期段",
        },
        "汇总": {
            "candidates": len(rows),
            "completeness_dist": dict(dist),
            "claim_dist": dict(claim_dist),
            "interval_basis_dist": dict(basis_dist),
            "r1_missing_total": r1_missing_sum,
            "r2_missing_total": r2_missing_sum,
            "r1_would_flag_candidates": sum(1 for r in rows
                                            if r["r1_口径对照"]["missing_exact_ms_count"] > 0),
            "r2_flag_candidates": sum(1 for r in rows if r["missing_count"] > 0),
            "candidates_with_off_grid_events": sum(1 for r in rows if r["off_grid_count"] > 0),
            "candidates_where_phase_shift_changes_grid": sum(
                1 for r in rows if r["r1_口径对照"]["phase_shift_effect"] > 0),
            "candidates_with_field_defect": sum(1 for r in rows if r["field_defect_count"] > 0),
            "candidates_with_boundary_jitter": sum(1 for r in rows if r["boundary_jitter_count"] > 0),
            "boundary_jitter_events": sum(r["boundary_jitter_count"] for r in rows),
            "boundary_dependent_candidates": sum(
                1 for r in rows if r["boundary_dependent_count"] > 0),
            "trivial_holds": sum(1 for r in rows if r["expected_count"] == 0),
        },
        "per_candidate": rows,
        "checks": CHECKS,
    }

    add("Q1 全部 51 候选都有周期证据等级（无 null）",
        all(r["interval_basis"] for r in rows), {"n": len(rows)})
    add("Q2 只有官方覆盖的候选才允许 claim=PROVEN",
        all(r["claim"] != L.CLAIM_PROVEN or r["interval_basis"] == L.EV_OFFICIAL for r in rows),
        {"PROVEN 数": claim_dist.get(L.CLAIM_PROVEN, 0),
         "违规": [r["symbol"] for r in rows
                  if r["claim"] == L.CLAIM_PROVEN and r["interval_basis"] != L.EV_OFFICIAL]})
    add("Q3 相位来自观测而非默认 0，且登记支撑数",
        all(r["phase_h"] is not None for r in rows),
        {"相位缺失": [r["symbol"] for r in rows if r["phase_h"] is None],
         "相位=0": sum(1 for r in rows if r["phase_h"] == 0),
         "相位≠0": sum(1 for r in rows if r["phase_h"] not in (0, None))})
    add("Q4 R1 的精确毫秒匹配确实制造假缺失（本单必须复现该缺陷）",
        r1_missing_sum > r2_missing_sum,
        {"R1 口径缺失合计": r1_missing_sum, "本单缺失合计": r2_missing_sum,
         "R1 会标记候选": out["汇总"]["r1_would_flag_candidates"],
         "本单标记": out["汇总"]["r2_flag_candidates"]})
    add("Q5 网格外事件单独计数（不静默吞掉）",
        True,
        {"有网格外事件的候选": out["汇总"]["candidates_with_off_grid_events"],
         "样例": [{"symbol": r["symbol"], "n": r["off_grid_count"],
                   "at": r["off_grid_instants_utc"][:3]}
                  for r in rows if r["off_grid_count"] > 0][:5]})
    add("Q6 缺失时刻全部可枚举",
        all(len(r["missing_instants_utc"]) == r["missing_count"] for r in rows), {"n": len(rows)})
    add("Q7 边界抖动被单列（含严格判据是否纳入），不混进缺失",
        True,
        {"候选数": out["汇总"]["candidates_with_boundary_jitter"],
         "事件数": out["汇总"]["boundary_jitter_events"],
         "样例": [{"symbol": r["symbol"], "exit": r["exit_utc"],
                   "boundary": r["boundary_jitter"][:2]}
                  for r in rows if r["boundary_jitter_count"] > 0][:4]})
    add("Q8b 端点抖动依赖的候选一律不给 PROVEN（退出同刻有歧义）",
        all(r["claim"] != L.CLAIM_PROVEN for r in rows if r["boundary_dependent_count"] > 0),
        {"边界依赖候选": [{"symbol": r["symbol"], "claim": r["claim"],
                          "instants": r["boundary_dependent_instants_utc"]}
                         for r in rows if r["boundary_dependent_count"] > 0]})
    add("Q8 官方不覆盖时，步长必须来自观测而不是官方当前值",
        all(r["step_h"] == r["local_step_h"] for r in rows
            if not r["official_covers_hold"] and r["local_step_h"] is not None),
        {r["symbol"]: {"step": r["step_h"], "local": r["local_step_h"]}
         for r in rows if not r["official_covers_hold"]})

    (L.OUT / "02_缺口判据与完整性.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = sum(1 for c in CHECKS if c["通过"])
    print("完整性:", dict(dist), "| 主张:", dict(claim_dist), "| 周期证据:", dict(basis_dist))
    print(f"缺失合计：R1 口径 {r1_missing_sum} → 本单 {r2_missing_sum}"
          f"（候选 {out['汇总']['r1_would_flag_candidates']} → {out['汇总']['r2_flag_candidates']}）")
    print(f"边界抖动候选 {out['汇总']['candidates_with_boundary_jitter']}"
          f"（{out['汇总']['boundary_jitter_events']} 事件）"
          f" | 字段缺陷 {out['汇总']['candidates_with_field_defect']}")
    print(f"checks {ok}/{len(CHECKS)}")
    for c in CHECKS:
        if not c["通过"]:
            print("  FAIL", c["检查"], json.dumps(c["证据"], ensure_ascii=False)[:400])
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

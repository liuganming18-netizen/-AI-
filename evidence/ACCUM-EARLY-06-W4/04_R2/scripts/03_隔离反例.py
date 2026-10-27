# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R2 · 03 隔离反例（合成，与真实证据严格分开）

每个反例都同时跑两套判据：
  · R1 判据：直接 import `03_R1/scripts/01_适配器对账与资金费判据重构.py` 的
    `analyze_symbol_settlement_period` + `evaluate_funding_gap_and_usability`，
    只把它的数据源换成合成序列（不改它的代码）。
  · R2 判据：`02_缺口判据与完整性.judge_hold`。

反例清单
--------
S1  规律缺失导致周期误判（有官方证据）—— 真 4h，删掉一半事件后观测全是 8h
S1b 同一份数据但没有官方证据 —— 观测法**无法**区分「缺一半」与「本来就是 8h」
S2  非零结算但仍漏事件 —— 持仓 12h 应结算 3 次，只记录 1 次
S3  周期切换 —— 8h→4h 落在持仓内，单一周期网格必然错
S4  相位不一致 —— 4h 周期但结算在 01/05/09 点，按「相位=0」生成预期时刻会全部落空
S5  端点抖动 —— 结算记录比退出晚 4ms，严格 `<=` 判据把它判成不存在

本文件全部为合成数据，**不得**与 `04_真实记录核对` 的结论混用。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r2 as L

R1_SCRIPT = (L.ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "03_R1"
             / "scripts" / "01_适配器对账与资金费判据重构.py")
R2_SCRIPT = Path(__file__).resolve().parent / "02_缺口判据与完整性.py"

H = 3600000
BASE = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R1 = load_module(R1_SCRIPT, "r1_mod")
R2 = load_module(R2_SCRIPT, "r2_mod")


class _StubFA:
    """把 R1 的数据源换成本文件的合成序列（R1 代码不动）。"""

    def __init__(self, times):
        self.times = list(times)

    def load_funding_events(self, *a, **kw):
        return {"events": [{"funding_time": t, "rate": 0.0001, "mark_price": 100.0,
                            "status": "resolved"} for t in self.times]}

    def to_replay_events(self, loaded):
        return [{"funding_time": e["funding_time"], "rate": e["rate"],
                 "mark_price": e["mark_price"], "status": e["status"]}
                for e in loaded["events"]]


def synth(times, rates=None):
    rates = rates or [0.0001] * len(times)
    return [{"funding_time": int(t), "rate": rates[i], "mark_price": 100.0,
             "status": "resolved", "observed_in": "synthetic"} for i, t in enumerate(times)]


def run_r1(times, entry_ms, exit_ms, official=None):
    """用 R1 的两个函数跑合成序列。"""
    old = R1.FA
    R1.FA = _StubFA(times)
    try:
        period = R1.analyze_symbol_settlement_period("SYN", entry_ms)
        held = [{"funding_time": t, "rate": 0.0001, "mark_price": 100.0,
                 "status": "resolved"} for t in times if entry_ms < t <= exit_ms]
        ev = R1.evaluate_funding_gap_and_usability(
            is_filled=True, rejection_reason=None, exit_kind="intrabar",
            participation_capped=False, held_settled_events=held,
            entry_ms=entry_ms, exit_ms=exit_ms, symbol="SYN", period_info=period)
    finally:
        R1.FA = old
    return {"period_status": period["period_status"],
            "step_h": period["detected_period_hours"],
            "distinct_intervals": period["distinct_intervals_hours"],
            "expected": ev["expected_settlements_count"],
            "actual": ev["actual_settlements_count"],
            "missing": ev["missing_settlements_count"],
            "gap_classification": ev["gap_classification"],
            "usability": ev["usability"]}


def run_r2(times, entry_ms, exit_ms, official=None):
    rec = R2.judge_hold({"official": official}, entry_ms, exit_ms, synth(times))
    return {"interval_basis": rec["interval_basis"], "step_h": rec["step_h"],
            "phase_h": rec["phase_h"], "expected": rec["expected_count"],
            "actual": rec["actual_count_strict"], "missing": rec["missing_count"],
            "boundary_dependent": rec["boundary_dependent_count"],
            "completeness": rec["completeness"], "claim": rec["claim"],
            "reason": rec["reason"]}


def off_series(start_ms, n, step_h, phase_h=0, skip=None):
    skip = skip or set()
    return [start_ms + int(phase_h * H) + k * int(step_h * H)
            for k in range(n) if k not in skip]


def naive_old_rule(hold_hours, n_events):
    """W4-02 旧判据（R1 报告里被撤销的那条）：
    「持仓超过 8 小时且零结算才算缺口」。只用持仓时长与事件条数。"""
    return {"would_flag_gap": bool(hold_hours > 8 and n_events == 0),
            "verdict": "GAP" if (hold_hours > 8 and n_events == 0) else "NO_GAP_COMPLETE",
            "说明": "只看「持仓>8h 且 0 结算」，有任意一次结算就判完整"}


CASES = []


def case(cid, title, times, entry_ms, exit_ms, official, expect, note):
    CASES.append({"id": cid, "title": title,
                  "synthetic_series": {"n": len(times),
                                       "first_utc": L.iso(min(times)),
                                       "last_utc": L.iso(max(times))},
                  "entry_utc": L.iso(entry_ms), "exit_utc": L.iso(exit_ms),
                  "official": official,
                  "r1": run_r1(times, entry_ms, exit_ms, official),
                  "r2": run_r2(times, entry_ms, exit_ms, official),
                  "old_8h_rule": naive_old_rule((exit_ms - entry_ms) / H,
                                                sum(1 for t in times
                                                    if entry_ms < t <= exit_ms)),
                  "预期结论": expect, "说明": note})


def main() -> int:
    # ── S1 规律缺失导致周期误判（有官方证据） ──
    # 真 4h，30 天；删掉所有偶数号事件 ⇒ 观测间隔全是 8h
    full = off_series(BASE, 180, 4.0)
    half = [t for i, t in enumerate(full) if i % 2 == 0]
    entry = BASE + 60 * 4 * H + 15 * 60 * 1000      # 4h 网格后 15 分钟入场
    exit_ = entry + 30 * H
    official_4h = {"interval_hours": 4, "update_ms": BASE - 90 * 24 * H,
                   "update_utc": L.iso(BASE - 90 * 24 * H), "disclaimer": False}
    case("S1", "规律缺失导致周期误判（有官方证据）", half, entry, exit_, official_4h,
         "R1 判 proven_8h 且完整；R2 用官方 4h 判出缺一半",
         "观测间隔被「缺失」改写成 8h；只有官方证据能纠正")

    # ── S1b 同一份数据、无官方证据 ──
    case("S1b", "同一份数据但没有官方证据", half, entry, exit_, None,
         "R1 宣称 proven_8h 完整；R2 只能给 NOT_PROVEN（观测法不可区分）",
         "没有官方证据时，「缺一半」与「本来就是 8h」在观测上等价 ⇒ 只能说未证明")

    # ── S2 非零结算但仍漏事件 ──
    # 真 4h；持仓 12h 应有 3 次（k=41,42,43）；只保留 k=41，其余两次删掉。
    # 这样「有结算」成立，但缺 2 次；旧 8h 零事件判据完全看不见。
    s2_full = off_series(BASE, 180, 4.0)
    s2_entry = BASE + 40 * 4 * H + 15 * 60 * 1000
    s2_exit = s2_entry + 12 * H
    drop = {42, 43}
    s2 = [t for i, t in enumerate(s2_full) if i not in drop]
    case("S2", "非零结算但仍漏事件（12h 应结算 3 次，只记录 1 次）", s2,
         s2_entry, s2_exit, None,
         "旧 8h 零事件判据判「完整」（因为 >0 次）；R1/R2 都能判出缺 2 次",
         "「结算次数 > 0」不能推出完整；持仓 12h 只结算 1 次就是漏")

    # ── S3 周期切换 ──
    # 前 60 天 8h，后 60 天 4h，持仓跨切换点
    pre = off_series(BASE, 180, 8.0)
    switch_at = pre[-1] + 8 * H
    post = [switch_at + k * 4 * H for k in range(1, 181)]
    s3 = pre + [switch_at] + post
    s3_entry = switch_at - 10 * H
    s3_exit = switch_at + 14 * H
    case("S3", "周期切换（8h→4h）落在持仓内", s3, s3_entry, s3_exit, None,
         "R1 把混合间隔当成「4h 有缺口」，按 4h 铺满整个持仓；R2 判 UNDETERMINED",
         "切换前后步长不同，任何单一周期网格都会算错")

    # ── S4 相位不一致 ──
    # 4h 周期，但结算在 01/05/09… 点（相位 = 1h）
    s4 = off_series(BASE, 180, 4.0, phase_h=1)
    s4_entry = BASE + 40 * 4 * H + 30 * 60 * 1000
    s4_exit = s4_entry + 12 * H
    case("S4", "相位不一致（4h 周期但结算在 01/05/09 点）", s4,
         s4_entry, s4_exit, None,
         "R1 按相位 0 生成预期 ⇒ 全部落空；R2 用观测相位 1h ⇒ 全部匹配",
         "周期长度相同但相位不同，不能只凭长度默认 00/04/08")

    # ── S5 端点抖动 ──
    s5 = off_series(BASE, 180, 4.0)
    s5_entry = BASE + 40 * 4 * H + 15 * 60 * 1000
    s5_exit = BASE + 43 * 4 * H            # 恰好落在 4h 网格上
    s5 = [t + (4 if t == s5_exit else 0) for t in s5]   # 该次结算记录晚 4ms
    case("S5", "端点抖动（结算记录比退出晚 4ms）", s5, s5_entry, s5_exit, None,
         "R1 严格 `<=` 把它判成不存在；R2 容差匹配并标 boundary_dependent",
         "毫秒级记录抖动会让「退出同刻的那次结算」凭空消失或多出来")

    # ── 汇总判定 ──
    checks = []

    def add(name, ok, ev):
        checks.append({"检查": name, "通过": bool(ok), "证据": ev})

    by = {c["id"]: c for c in CASES}
    add("X1 S1：R1 把「缺一半」读成 8h 周期，R2 用官方 4h 判出缺失",
        by["S1"]["r1"]["step_h"] == 8.0
        and by["S1"]["r1"]["gap_classification"] == "NO_GAP_COMPLETE"
        and by["S1"]["r2"]["missing"] > 0
        and by["S1"]["r2"]["claim"] == L.CLAIM_REFUTED,
        {"r1": by["S1"]["r1"], "r2": by["S1"]["r2"]})
    add("X2 S1b：无官方证据时 R2 不给「已证明」，R1 给「proven + 完整」",
        by["S1b"]["r1"]["period_status"].startswith("proven_regular")
        and by["S1b"]["r1"]["gap_classification"] == "NO_GAP_COMPLETE"
        and by["S1b"]["r2"]["claim"] == L.CLAIM_NOT_PROVEN,
        {"r1": by["S1b"]["r1"], "r2": by["S1b"]["r2"]})
    add("X3 S2：有非零结算但 R2 仍判出缺失；旧 8h 零事件判据看不见",
        by["S2"]["r2"]["actual"] > 0 and by["S2"]["r2"]["missing"] > 0
        and by["S2"]["old_8h_rule"]["verdict"] == "NO_GAP_COMPLETE",
        {"old_8h_rule": by["S2"]["old_8h_rule"],
         "r1": by["S2"]["r1"], "r2": by["S2"]["r2"]})
    add("X4 S3：持仓内周期切换，R2 判 UNDETERMINED，R1 仍给确定步长",
        by["S3"]["r2"]["completeness"] == L.CP_UNDET
        and by["S3"]["r1"]["step_h"] is not None,
        {"r1": by["S3"]["r1"], "r2": by["S3"]["r2"]})
    add("X5 S4：相位不是 0 时 R1 的预期时刻全部落空，R2 全部匹配",
        by["S4"]["r1"]["missing"] > 0 and by["S4"]["r2"]["missing"] == 0,
        {"r1": by["S4"]["r1"], "r2": by["S4"]["r2"]})
    add("X6 S5：端点 4ms 抖动，R1 判缺失，R2 标 boundary_dependent 且不给 PROVEN",
        by["S5"]["r1"]["missing"] >= 1
        and by["S5"]["r2"]["missing"] == 0
        and by["S5"]["r2"]["boundary_dependent"] >= 1
        and by["S5"]["r2"]["claim"] != L.CLAIM_PROVEN,
        {"r1": by["S5"]["r1"], "r2": by["S5"]["r2"]})
    add("X7 反例全部为合成数据，未读生产库、未写生产库", True,
        {"cases": [c["id"] for c in CASES],
         "series_range": [(c["synthetic_series"]["first_utc"],
                           c["synthetic_series"]["last_utc"]) for c in CASES][:2],
         "数据来源": "本脚本内 off_series() 生成，不调用适配器读真实库"})

    out = {"工单": "ACCUM-EARLY-06-W4-R2",
           "性质": "隔离反例（合成），与 04_真实记录核对 严格分开",
           "R1 判据来源": "import 03_R1 脚本原函数，仅替换其数据源",
           "cases": CASES, "checks": checks}
    (L.OUT / "03_隔离反例.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = sum(1 for c in checks if c["通过"])
    for c in CASES:
        print(f"[{c['id']}] {c['title']}")
        print(f"   R1: {c['r1']['period_status']} step={c['r1']['step_h']} "
              f"exp={c['r1']['expected']} act={c['r1']['actual']} "
              f"miss={c['r1']['missing']} → {c['r1']['gap_classification']}")
        print(f"   R2: {c['r2']['interval_basis']} step={c['r2']['step_h']} "
              f"phase={c['r2']['phase_h']} exp={c['r2']['expected']} "
              f"act={c['r2']['actual']} miss={c['r2']['missing']} "
              f"→ {c['r2']['completeness']}/{c['r2']['claim']}")
    print(f"checks {ok}/{len(checks)}")
    for c in checks:
        if not c["通过"]:
            print("  FAIL", c["检查"], json.dumps(c["证据"], ensure_ascii=False)[:400])
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

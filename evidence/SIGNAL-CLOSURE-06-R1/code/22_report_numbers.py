#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""22_report_numbers.py —— 报告数字机械复算（只读产物，不重跑仿真）

项目纪律：报告里每个关键数字都必须能回到 `out/*.json` 的原始字段机械复算。
本脚本把最终报告引用的数字**全部**从产物里重算一遍，输出
`out/22_报告数字.json` 与 `out/22_报告数字.csv`，并打印「声明值 vs 复算值」对照。

用法：
    python code/22_report_numbers.py
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
R1 = ROOT / "evidence/SIGNAL-CLOSURE-06-R1"
O6 = ROOT / "evidence/SIGNAL-CLOSURE-06"
OUT = R1 / "out"

rows = []


def utc(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add(name, value, source, note=""):
    rows.append({"metric": name, "value": value, "source": source, "note": note})


def load(p: Path):
    if not p.exists():
        return None
    return json.load(open(p, encoding="utf-8"))


def sha(p: Path):
    if not p.exists():
        return "MISSING"
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


# ── 1. 版本绑定 ────────────────────────────────────────────────────────────
VER = [
    ("框架/backend/unified_runtime.py", "生产信号引擎（MultiSymbolSignalEngine 宿主）"),
    ("框架/backend/live_store.py", "锚点持久化 / LIVE_DB / 账本路径"),
    ("框架/backend/paper_engine.py", "下单闸门 PE.open_position"),
    ("策略/WyckoffMaster/event_trend_v0_1/state_machine.py", "状态机"),
    ("evidence/SIGNAL-CLOSURE-06/code/00_common.py", "06/R1 共用公共件"),
    ("evidence/SIGNAL-CLOSURE-06/code/03_restart_consistency.py", "06 重启一致性"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/15_dual_session_diff.py", "R1 双会话逐项对账"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/16_step_gate_diagnosis.py", "闸门诊断"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/17_batch_vs_full.py", "分批对比"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/18_restart_effect.py", "重启动作效应"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/19_export_real_candidates.py", "真实候选导出"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/13_recover_cases_r1.py", "恢复四路径"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/20_runtime_observation.py", "跨小时线运行观察"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/21_restart_count_matrix.py", "重启次数矩阵"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/25_reconstruct_event2.py", "第 2 根小时线逐币重建"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/26_observe_summary.py", "运行观察汇总重建"),
    ("evidence/SIGNAL-CLOSURE-06-R1/code/28_process_identity.py", "进程身份快照"),
]
for rel, note in VER:
    p = ROOT / rel
    add(f"sha256::{Path(rel).name}", sha(p)[:32], rel, note)
    add(f"mtime::{Path(rel).name}", utc(p.stat().st_mtime * 1000) if p.exists() else None, rel, note)

# ── 2. 15 号全池对账（当前版本，D2880 + anchor2880 + 一次重启@T1-6h）──────────
p15 = OUT / "15_双会话对账_D2880_R6h_anchor2880_fullpool.json"
d15 = load(p15)
if d15:
    r = d15["results"]["consistent"]
    n = len(r)
    add("15::n_symbols", n, p15.name)
    add("15::n_identical", sum(1 for v in r.values() if v["kind"] == "identical"), p15.name)
    add("15::n_structure_diff", sum(1 for v in r.values() if v["kind"] == "structure_diff"), p15.name)
    add("15::n_candidate_log_diff", sum(1 for v in r.values() if v["kind"] == "candidate_log_diff"), p15.name)
    add("15::n_candidate_order_only", sum(1 for v in r.values() if v["kind"] == "candidate_order_only"), p15.name)
    add("15::sum_cand_C", sum(v["n_cand_C"] for v in r.values()), p15.name)
    add("15::sum_cand_R", sum(v["n_cand_R"] for v in r.values()), p15.name)
    add("15::n_cand_list_mismatch", sum(1 for v in r.values() if v["candidates_C"] != v["candidates_R"]), p15.name)
    add("15::n_dup", sum(1 for v in r.values() if v["dup_C"] or v["dup_R"]), p15.name, "同一 candidate_id 重复登记")
    add("15::n_single_sided", sum(1 for v in r.values() if v["cand_only_C"] or v["cand_only_R"]), p15.name, "候选只出现在一边")
    add("15::sum_transitions_C", sum(v["n_transitions_C"] for v in r.values()), p15.name)
    add("15::sum_transitions_R", sum(v["n_transitions_R"] for v in r.values()), p15.name)
    add("15::n_trans_mismatch", sum(1 for v in r.values() if v["n_transitions_C"] != v["n_transitions_R"]), p15.name)
    add("15::sum_1h_C", sum(v["n_1h_window_C"] for v in r.values()), p15.name)
    add("15::sum_1h_R", sum(v["n_1h_window_R"] for v in r.values()), p15.name)
    add("15::sum_4h_C", sum(v["n_4h_window_C"] for v in r.values()), p15.name)
    add("15::sum_4h_R", sum(v["n_4h_window_R"] for v in r.values()), p15.name)
    add("15::n_4h_lost", sum(1 for v in r.values() if v["n_4h_lost_from_window"]), p15.name)
    add("15::n_symbols_with_candidates", sum(1 for v in r.values() if v["n_cand_C"]), p15.name)
    add("15::floor_C_utc", d15["floor_C_utc"], p15.name)
    add("15::floor_R_utc", d15["floor_R_utc"], p15.name)

# ── 3. 06 基线（旧版本 D1160）与 R1 的 03 产物 ────────────────────────────
p06 = O6 / "out/03_重启一致性_D1160.json"
d06 = load(p06)
if d06:
    add("06::D1160_n_diff_vs_continuous", json.dumps(d06["n_diff_vs_continuous"], ensure_ascii=False), p06.name)
    add("06::D1160_total_candidates", json.dumps(d06["total_candidates"], ensure_ascii=False), p06.name)
    add("06::D1160_generated_utc", d06["generated_at_utc"], p06.name, "旧版本产物（早于 unified_runtime.py 06:12:11Z）")

p03r1 = OUT / "03_重启一致性_D2880_anchor2880.json"
d03 = load(p03r1)
if d03:
    add("R1_03::n_diff_vs_continuous", json.dumps(d03["n_diff_vs_continuous"], ensure_ascii=False), p03r1.name)
    add("R1_03::n_candidate_log_diff", json.dumps(d03["n_candidate_log_diff"], ensure_ascii=False), p03r1.name)
    add("R1_03::total_candidates", json.dumps(d03["total_candidates"], ensure_ascii=False), p03r1.name)
    add("R1_03::generated_utc", d03["generated_at_utc"], p03r1.name, "早于 03 脚本 06:22:37Z 改版 ⇒ 已作废")

p03new = OUT / "03b" / "03_重启一致性_D2880_anchor2880.json"
d03n = load(p03new)
if d03n:
    add("R1_03b::n_diff_vs_continuous", json.dumps(d03n["n_diff_vs_continuous"], ensure_ascii=False), p03new.name)
    add("R1_03b::n_candidate_log_diff", json.dumps(d03n["n_candidate_log_diff"], ensure_ascii=False), p03new.name)
    add("R1_03b::total_candidates", json.dumps(d03n["total_candidates"], ensure_ascii=False), p03new.name)
    add("R1_03b::restart_count_irrelevant", d03n.get("restart_count_irrelevant"), p03new.name)
    add("R1_03b::phase1_vs_direct", json.dumps(d03n.get("restart_state_change_at_Tr"), ensure_ascii=False), p03new.name)
    add("R1_03b::state_distribution", json.dumps(d03n.get("state_distribution"), ensure_ascii=False), p03new.name)
    add("R1_03b::generated_utc", d03n["generated_at_utc"], p03new.name, "当前版本重跑")

# ── 4. 排除三个非重启变量 ─────────────────────────────────────────────────
p16 = OUT / "16_推进闸门诊断.json"
d16 = load(p16)
if d16:
    add("16::verdict", d16["verdict"], p16.name, "币池规模是否影响推进")
    add("16::n_common_steps", d16["n_common_steps"], p16.name)
    add("16::gate_skipped_full", d16["full526"]["n_gate_skipped"], p16.name)
    add("16::gate_skipped_subset", d16["subset6"]["n_gate_skipped"], p16.name)

p17 = OUT / "17_分批对比.json"
d17 = load(p17)
if d17:
    add("17::A_eq_B", d17["A_eq_B"], p17.name, "闸门挂钟解耦与否")
    add("17::A_eq_C", d17["A_eq_C"], p17.name, "分批 vs 全池")
    add("17::A_eq_D", d17["A_eq_D"], p17.name)
    add("17::transitions", json.dumps(d17["transitions_by_scenario"], ensure_ascii=False), p17.name)

p18 = OUT / "18_重启动作效应_D2880_R6h_anchor2880.json"
d18 = load(p18)
if d18:
    add("18::n_diff", d18["n_diff"], p18.name, "bootstrap@Tr 与连续运行到 Tr 的差异")
    add("18::n_atr_diff", d18["n_atr_diff"], p18.name)
    add("18::n_1h_count_diff", d18["n_1h_count_diff"], p18.name)
    add("18::n_4h_count_diff", d18["n_4h_count_diff"], p18.name)
    add("18::verdict", d18["verdict"], p18.name)
    add("18::catchup_sec", d18["catchup_sec"], p18.name)
    add("18::upto_utc", d18["upto_utc"], p18.name)
    add("18::T0_utc", d18["T0_utc"], p18.name)
    add("18::Tr_utc", d18["Tr_utc"], p18.name)
    add("18::read_days", d18["read_days"], p18.name)
    add("18::n_advance_steps_A", d18["n_advance_steps_A"], p18.name)
    _a = sorted({v["n_1h_A"] for v in d18["detail"].values()})
    _b = sorted({v["n_1h_B"] for v in d18["detail"].values()})
    add("18::n_1h_A_range", f"{_a[0]}~{_a[-1]}（{len(_a)} 种取值）", p18.name, "A 侧逐币 1h 根数范围")
    add("18::n_1h_B_range", f"{_b[0]}~{_b[-1]}（{len(_b)} 种取值）", p18.name, "B 侧（已补 step）")
    add("18::n_1h_sets_equal", _a == _b, p18.name, "两侧取值集合相同 ⇒ 逐币根数一一相等")
    _g = d18["detail"].get("0GUSDT") or {}
    add("18::n_1h_A_0GUSDT", _g.get("n_1h_A"), p18.name, "报告 3.4 节举例用的就是 0GUSDT")
    add("18::n_1h_B_0GUSDT", _g.get("n_1h_B"), p18.name)

p18c = OUT / "18_重启动作效应_D2880_R6h_anchor2880_对照_未补step.json"
d18c = load(p18c)
if d18c:
    add("18c::n_diff", d18c["n_diff"], p18c.name, "未补 step(upto) 的对照：假差异来源")
    add("18c::n_1h_count_diff", d18c["n_1h_count_diff"], p18c.name)
    _ca = sorted({v["n_1h_A"] for v in d18c["detail"].values()})
    _cb = sorted({v["n_1h_B"] for v in d18c["detail"].values()})
    add("18c::n_1h_A_range", f"{_ca[0]}~{_ca[-1]}", p18c.name, "未补 step 时 A 侧取值范围")
    add("18c::n_1h_B_range", f"{_cb[0]}~{_cb[-1]}", p18c.name, "未补 step 时 B 侧取值范围（每个都少 3 根）")
    _gc = d18c["detail"].get("0GUSDT") or {}
    add("18c::n_1h_A_0GUSDT", _gc.get("n_1h_A"), p18c.name)
    add("18c::n_1h_B_0GUSDT", _gc.get("n_1h_B"), p18c.name)
    add("18c::n_diff_symbols", json.dumps([k for k, v in d18c["detail"].items() if not v["same"]],
                                          ensure_ascii=False), p18c.name)

# ── 5. 真实候选与恢复四路径 ───────────────────────────────────────────────
p19 = OUT / "19_真实历史候选清单.json"
d19 = load(p19)
if d19:
    add("19::n_candidates", d19["n_candidates"], p19.name)
    add("19::n_after_paper_epoch", d19["n_after_paper_epoch"], p19.name)
    add("19::n_before_paper_epoch", d19["n_before_paper_epoch"], p19.name)
    add("19::n_recoverable_at_Tr", d19["n_recoverable_at_Tr"], p19.name)
    add("19::symbols_with_candidates", d19["symbols_with_candidates"], p19.name)
    c = d19["candidates"]
    add("19::earliest_candidate_utc", min(x["snapshot_utc"] for x in c), p19.name)
    add("19::latest_candidate_utc", max(x["snapshot_utc"] for x in c), p19.name)
    add("19::paper_epoch_utc", d19["paper_epoch_utc"], p19.name)
    add("19::max_fill_lag_ms", d19["max_fill_lag_ms"], p19.name)
    add("19::max_fill_lag_hours", round(d19["max_fill_lag_ms"] / 3_600_000, 3), p19.name)
    _ep = dt.datetime.strptime(d19["paper_epoch_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc)
    add("19::paper_epoch_ms", int(_ep.timestamp() * 1000), p19.name,
        "由 paper_epoch_utc 换算；报告 4.2 节门禁一栏引用的就是这个值")

p13 = OUT / "13_候选恢复四路径.json"
d13 = load(p13)
if d13:
    for k, v in d13["verdicts"].items():
        add(f"13::verdict::{k}", v, p13.name)
    add("13::main_case_symbol", d13["main_case"]["symbol"], p13.name)
    add("13::main_case_snapshot_utc", d13["main_case"]["snapshot_utc"], p13.name)
    add("13::main_case_exec_earliest_utc", d13["main_case"]["exec_earliest_utc"], p13.name)
    add("13::main_case_direct_reason", d13["scenarios"]["A_过期历史候选_拒绝且不下单"]["direct_open_position"]["reason"], p13.name)
    add("13::idempotency_order", f"{d13['idempotency']['order_before']}→{d13['idempotency']['order_after']}", p13.name)
    add("13::idempotency_position", f"{d13['idempotency']['pos_before']}→{d13['idempotency']['pos_after']}", p13.name)
    add("13::route_passes_epoch_ms", d13["gate_check"]["route_passes_epoch_ms"], p13.name)
    add("13::paper_epoch_unchanged", d13["gate_check"]["paper_epoch_unchanged"], p13.name)

# ── 6. 锚点语义与保留范围 ─────────────────────────────────────────────────
p10 = OUT / "10_锚点语义与覆盖.json"
d10 = load(p10)
if d10:
    add("10::verdict", d10["verdict"], p10.name)
    add("10::anchor_written_utc", d10["storage"]["written_value_utc"], p10.name,
        "10 号隔离测试的落定值（调用时刻 2026-10-25T05:59:59Z − 2880h）；"
        "与生产实际 2026-06-27T06:45:15Z 不同，因为调用时刻不同，语义相同")
    add("10::anchor_written_ms", d10["storage"]["written_value_ms"], p10.name)
    add("10::anchor_table", d10["storage"]["table"], p10.name)
    add("10::anchor_key", d10["storage"]["key"], p10.name)
    add("10::checks", json.dumps(d10["checks"], ensure_ascii=False), p10.name)
    cov = d10["coverage"]
    add("10::cold_covers_anchor_n", cov.get("cold_covers_anchor_year_n"), p10.name)
    add("10::n_uncovered", cov["n_uncovered"], p10.name)
    add("10::uncovered", json.dumps(cov["uncovered_sample"], ensure_ascii=False), p10.name)
    add("10::retention_status", d10["retention_policy"]["当前状态"], p10.name)
    add("10::cold_min_year", json.dumps(cov["cold_min_year"], ensure_ascii=False), p10.name)

# ── 7. 重启次数矩阵（21 号派生自 15 号，产物沿用 15 号命名前缀）──────────────
# 说明：21 号的产物文件名仍是 `15_双会话对账_...`（脚本由 15 号派生、dump 名未改），
# 这里显式列出，避免通配符把「6 币子集」和「全池」混在一起。
MTX = [
    "15_双会话对账_D2880_R1h_anchor2880.json",    # 1 次重启，重启点在 T1-1h
    "15_双会话对账_D2880_R6h_anchor2880_fullpool.json",  # 1 次重启，重启点在 T1-6h
    "15_双会话对账_D2880_R12h_anchor2880.json",   # 1 次重启，重启点在 T1-12h
    "15_双会话对账_D2880_R6h_x2_anchor2880.json",  # 2 次重启：T1-12h 与 T1-6h
]
for name in MTX:
    f = OUT / name
    d = load(f)
    if not d:
        continue
    r = d["results"]["consistent"]
    tag = f.stem
    add(f"21::{tag}::n_restarts", d.get("n_restarts") or 1, f.name)
    add(f"21::{tag}::restart_points_utc", json.dumps(d.get("restart_points_utc") or [], ensure_ascii=False), f.name)
    add(f"21::{tag}::n_identical", sum(1 for v in r.values() if v["kind"] == "identical"), f.name)
    add(f"21::{tag}::n_structure_diff", sum(1 for v in r.values() if v["kind"] == "structure_diff"), f.name)
    add(f"21::{tag}::n_candidate_log_diff", sum(1 for v in r.values() if v["kind"] == "candidate_log_diff"), f.name)
    add(f"21::{tag}::sum_cand_C", sum(v["n_cand_C"] for v in r.values()), f.name)
    add(f"21::{tag}::sum_cand_R", sum(v["n_cand_R"] for v in r.values()), f.name)
    add(f"21::{tag}::sum_trans_C", sum(v["n_transitions_C"] for v in r.values()), f.name)
    add(f"21::{tag}::sum_trans_R", sum(v["n_transitions_R"] for v in r.values()), f.name)

# ── 8. 运行观察（20 / 25 号，动态）───────────────────────────────────────
p20ev = OUT / "20_运行观察_逐币事件.json"
d20ev = load(p20ev)
if d20ev:
    b = d20ev["baseline"]
    add("20::baseline_watermark_utc", b["watermark_1h_utc"], p20ev.name)
    add("20::baseline_state_counter", json.dumps(b["state_counter"], ensure_ascii=False), p20ev.name)
    add("20::baseline_history_candidates_discarded", b["history_candidates_discarded"], p20ev.name)
    add("20::baseline_ledger_sha_join", b["ledger"]["sha_join"][:16], p20ev.name)
    add("20::n_events_captured", len(d20ev["events"]), p20ev.name, "脚本在第 2 根触发时崩溃，只落了第 1 根")
    for i, e in enumerate(d20ev["events"], 1):
        add(f"20::bar{i}::wm", f"{e['wm_prev_utc']} → {e['wm_new_utc']}", p20ev.name)
        add(f"20::bar{i}::n_phase_changes", e["n_phase_changes"], p20ev.name)
        add(f"20::bar{i}::state_counter_after", json.dumps(e["state_counter_now"], ensure_ascii=False), p20ev.name)
        add(f"20::bar{i}::funnel_delta", json.dumps(e["funnel_delta"], ensure_ascii=False), p20ev.name)
        add(f"20::bar{i}::candidates_delta", e["candidates_delta"], p20ev.name)
        add(f"20::bar{i}::orders_delta", e["orders_routed_delta"], p20ev.name)
        add(f"20::bar{i}::lag_hist", json.dumps(e["lag_1h_hist"], ensure_ascii=False), p20ev.name)

p20b = OUT / "20b_生产状态_08点那根之后.json"
d20b = load(p20b)
if d20b:
    se = d20b["signal_engine"]
    add("20b::watermark_utc", utc(se["watermark_1h"]), p20b.name, "第 2 根之后已收敛的生产状态")
    add("20b::state_counter", json.dumps(se["state_counter"], ensure_ascii=False), p20b.name)
    add("20b::lag_hist", json.dumps(se["watermark_check"]["per_symbol"]["lag_1h_histogram"], ensure_ascii=False), p20b.name)
    add("20b::funnel_since_start", json.dumps(se["funnel"]["since_start"], ensure_ascii=False), p20b.name)
    # ── 5.2 节「配置 / 历史源 / 恢复锚点」全部回到 bootstrap.history 的原始字段 ──
    bs = se["bootstrap"]
    hs = bs["history"]
    add("20b::replay_mode", hs["replay_mode"], p20b.name)
    add("20b::state_anchor_utc", hs["state_anchor_utc"], p20b.name)
    add("20b::replay_window_start_utc", hs["replay_window_start_utc"], p20b.name, "等于锚点才算「不随截止时刻滑动」")
    add("20b::replay_window_hours", hs["replay_window_hours"], p20b.name)
    add("20b::declared_window_hours", hs["declared_window_hours"], p20b.name, "冻结口径声明窗口")
    add("20b::declared_window_start_utc", hs["declared_window_start_utc"], p20b.name)
    add("20b::read_start_utc", hs["read_start_utc"], p20b.name)
    add("20b::read_window_days", hs["read_window"]["days"], p20b.name)
    add("20b::data_source", hs["data_source"], p20b.name)
    add("20b::watermark_clamped", bs["watermark_clamped"], p20b.name)
    add("20b::bootstrap::ready", bs["ready"], p20b.name)
    add("20b::bootstrap::skipped", bs["skipped"], p20b.name)
    add("20b::bootstrap::history_candidates_discarded", bs["history_candidates_discarded"], p20b.name)
    add("20b::bootstrap::recovered_candidates", bs["recovered_candidates"], p20b.name)
    add("20b::used::box_bars", bs["used"]["box_bars"], p20b.name)
    # 读入起点与锚点的实际差：源码是 `锚点 − (box_bars_4h − 1) × 4h`，不是 −168h
    _anchor_ms = int(dt.datetime.strptime(hs["state_anchor_utc"], "%Y-%m-%dT%H:%M:%SZ")
                     .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    _read_ms = int(dt.datetime.strptime(hs["read_start_utc"], "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    add("20b::read_start_minus_anchor_hours", round((_anchor_ms - _read_ms) / 3_600_000, 3), p20b.name,
        "= (box_bars_4h − 1) × 4h = 41 × 4h = 164h；**不是** 42×4h=168h。"
        "锚点毫秒的权威值见 anchor::state_anchor_ms（状态文件只有秒级，会截掉 .686）")
    for p in ("4h", "1h"):
        h = hs[p]
        add(f"20b::history::{p}::cold_rows_scanned", h["cold_rows_scanned"], p20b.name)
        add(f"20b::history::{p}::hot_rows_scanned", h["hot_rows_scanned"], p20b.name)
        add(f"20b::history::{p}::overlap_rows", h["overlap_rows"], p20b.name)
        add(f"20b::history::{p}::overlap_value_mismatch", h["overlap_value_mismatch"], p20b.name)
        add(f"20b::history::{p}::rows_materialized", h["rows_materialized"], p20b.name)
        add(f"20b::history::{p}::symbols_with_data", h["symbols_with_data"], p20b.name)
        add(f"20b::history::{p}::symbols_full_window", h["symbols_full_window"], p20b.name)
        add(f"20b::history::{p}::listed_late_n", h["listed_late_n"], p20b.name)

p25 = OUT / "25_第二根小时线逐币重建.json"
d25 = load(p25)
if d25:
    add("25::verdict", d25["verdict"], p25.name, "第 2 根逐币名单的离线重建（两端校验）")
    add("25::check_pre_matches_production", d25["check_pre_matches_production"], p25.name)
    add("25::check_change_list_consistent_with_counters", d25["check_change_list_consistent_with_counters"], p25.name)
    add("25::pre_counter_offline", json.dumps(d25["pre_counter_offline"], ensure_ascii=False), p25.name)
    add("25::counter_delta", json.dumps(d25["counter_delta"], ensure_ascii=False), p25.name)
    add("25::n_phase_changes", d25["n_phase_changes"], p25.name)
    add("25::phase_changes", json.dumps([[x["symbol"], x["prev"], x["now"]] for x in d25["phase_changes"]],
                                        ensure_ascii=False), p25.name)

p20s = OUT / "20_运行观察_汇总.json"
d20 = load(p20s)
if d20:
    v = d20["verdict"]
    add("20::n_advances", d20["n_advances"], p20s.name)
    add("20::got_two_new_1h_bars", v["got_two_new_1h_bars"], p20s.name)
    add("20::natural_candidates", v["natural_candidates"], p20s.name, "自然候选数；0 必须如实报告")
    add("20::natural_orders", v["natural_orders"], p20s.name)
    add("20::phase_changes_total", v["phase_changes_total"], p20s.name)
    add("20::ledger_untouched", v["ledger_untouched"], p20s.name)
    add("20::observed_watermarks_utc", json.dumps(d20["observed_watermarks_utc"], ensure_ascii=False), p20s.name)
    add("20::n_phase_changes_by_bar", json.dumps(v["n_phase_changes_by_bar"], ensure_ascii=False), p20s.name)
    add("20::ledger_sha_join_baseline", d20["ledger_check"]["baseline_sha_join"][:16], p20s.name)
    add("20::ledger_sha_join_event1", d20["ledger_check"]["event1_sha_join"][:16], p20s.name)
    add("20::ledger_sha_join_now", d20["ledger_check"]["now_sha_join"][:16], p20s.name,
        "26 号执行时刻现算；三方相同才算「未变」")
    add("20::live_signal_rows", d20["live_now"]["live_signal"], p20s.name)
    add("20::rebuilt_by", d20["rebuilt_by"], p20s.name, d20["why"])
    for e in d20["events"]:
        i = e["bar"]
        add(f"20s::bar{i}::funnel_delta", json.dumps(e["funnel_delta"], ensure_ascii=False), p20s.name)
        add(f"20s::bar{i}::state_counter_after", json.dumps(e["state_counter_after"], ensure_ascii=False), p20s.name)
        add(f"20s::bar{i}::phase_list_source", e["phase_list_source"], p20s.name)
        grp = {}
        for sym, p0, n0 in e["phase_changes"]:
            grp.setdefault(f"{p0}→{n0}", []).append(sym)
        for k in sorted(grp):
            add(f"20s::bar{i}::group::{k}", json.dumps(sorted(grp[k]), ensure_ascii=False), p20s.name,
                f"{len(grp[k])} 个币")

# ── 9. 进程身份快照（28 号；进程身份只在当次运行有效）─────────────────────
p28 = OUT / "28_进程身份快照.json"
d28 = load(p28)
if d28:
    add("28::snapshot_utc", d28["snapshot_utc"], p28.name,
        "快照晚于报告撰写，用于证明 pid 至今未变，不是报告当时那一刻的快照")
    add("28::all_alive_and_matching", d28["all_alive_and_matching"], p28.name)
    for r in d28["processes"]:
        add(f"28::proc::{r['role']}::pid", r["pid"], p28.name)
        add(f"28::proc::{r['role']}::started_utc", r.get("started_utc"), p28.name)
        add(f"28::proc::{r['role']}::script_matches", r.get("script_matches"), p28.name)

# ── 10. 生产账本基线 ───────────────────────────────────────────────────────
LED = ROOT / "数据/paper"
for name in ("paper_3a.sqlite3", "paper_3b.sqlite3", "paper_3c.sqlite3", "paper_3d.sqlite3"):
    s = sha(LED / name)
    add(f"ledger::{name}", s, "数据/paper", "账本逐字节身份")
    add(f"ledger_short::{name}", f"{s[:8]}…{s[-7:]}", "数据/paper", "报告 5.3 节引用的短形式")
_s = sha(ROOT / "框架/backend/live/live_signals.sqlite3")
add("ledger::live_signals.sqlite3", _s, "框架/backend/live", "LIVE_DB")
add("ledger_short::live_signals.sqlite3", f"{_s[:8]}…{_s[-7:]}", "框架/backend/live", "报告 5.3 节引用的短形式")

# ── 11. 锚点在 LIVE_DB 里的真实落定值（live_run 表）────────────────────────
# 生产状态文件里只有秒级 `state_anchor_utc`，会把毫秒截掉（…715000 vs …715686），
# 所以锚点的**权威值**必须回到 LIVE_DB 的 `live_run.state_anchor_ms`。
LIVE_DB = ROOT / "框架/backend/live/live_signals.sqlite3"
if LIVE_DB.exists():
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=3.0)
        try:
            row = con.execute(
                "SELECT value_json, updated_at FROM live_run WHERE key='state_anchor_ms'"
            ).fetchone()
        finally:
            con.close()
        if row:
            raw_val, upd = row
            try:
                anchor_ms = int(json.loads(raw_val))
            except Exception:
                anchor_ms = int(raw_val)
            add("anchor::state_anchor_ms", anchor_ms, "live_run.state_anchor_ms")
            add("anchor::state_anchor_utc", utc(anchor_ms), "live_run.state_anchor_ms")
            add("anchor::updated_at_ms", int(upd), "live_run.state_anchor_ms")
            add("anchor::updated_at_utc", utc(int(upd)), "live_run.state_anchor_ms",
                "重启不重算的证据：updated_at 早于运行时启动时刻 2026-10-25T07:17:27Z")
    except Exception as e:  # noqa: BLE001
        add("anchor::read_error", f"{type(e).__name__}: {e}", "live_run.state_anchor_ms")

# ── 输出 ─────────────────────────────────────────────────────────────────
payload = {
    "generated_utc": utc(dt.datetime.now(dt.timezone.utc).timestamp() * 1000),
    "note": "报告引用的全部数字都在这里机械复算；报告正文不手抄任何未出现在本文件的数字。",
    "metrics": rows,
}
json.dump(payload, open(OUT / "22_报告数字.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open(OUT / "22_报告数字.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["metric", "value", "source", "note"])
    w.writeheader()
    w.writerows(rows)

for r in rows:
    print(f"{r['metric']:52s} = {r['value']}")
print(f"\n[out] {OUT/'22_报告数字.json'}  ({len(rows)} 项)")

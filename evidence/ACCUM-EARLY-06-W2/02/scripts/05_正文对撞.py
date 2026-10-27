# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/02｜脚本 05：正文数字 ↔ 产物 双向对撞

正向：本文声明的每个关键事实，既要在 README 里出现，也要能从 `out/*.json` 的指定位点取回同值。
反向：README 里出现的每个「测量型数字」，必须能在 `out/*.json` 里找到出处，或在**带理由**的
      结构白名单里（行号 / 章节号 / 日期 / 工单号 / 哈希片段）。找不到就报出来，不许沉默。

不做的事：不把 README 的数字与 README 自己比（那是自证），也不从 JSON 汇总值反推正文。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2b as W                                    # noqa: E402

README = W.W2B / "README.md"
NF = "ev_4a_binance_AVAXUSDT_box_AVAXUSDT_1789473599999_1789473599999"

#: (说明, README 里应出现的字面量, 产物文件名, 取值路径, 期望值)
FACTS = [
    # ── 身份 ──────────────────────────────────────────────────────────────
    ("修前代码 sha256", "d96ad60eca61f7e53e273414196100ac21b33650a19480beafb0eb60c4d41a52",
     "04_handoff.json", "D1_identity.pre_fix.sha256", None),
    ("修前字节", "113875", "04_handoff.json", "D1_identity.pre_fix.bytes", 113875),
    ("修后代码 sha256", "3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017",
     "04_handoff.json", "D1_identity.post_fix.sha256", None),
    ("修后字节", "117711", "04_handoff.json", "D1_identity.post_fix.bytes", 117711),
    ("测试配置 sha256", "f322a940cea1e69d6ebe048f100b1e0b3b8234c1d42ef45a1f9b25bfd55152bd",
     "00_inputs_and_versions.json", "I3_config.my_copy.sha256", None),
    ("身份全部对上期望", "与期望一致", "04_handoff.json", "summary.identity_ok", True),
    # ── I 组：输入未换 ────────────────────────────────────────────────────
    ("原事件数", "7/7 相同", "00_inputs_and_versions.json", "I1_events.n_cases", 7),
    ("事件号同序", "逐条同序", "00_inputs_and_versions.json",
     "I1_events.ids_identical_and_ordered", True),
    ("行情范围逐字相同", "I2 范围逐字相同", "00_inputs_and_versions.json",
     "I2_ranges.identical", True),
    ("配置逐字节相同", "逐字节相同", "00_inputs_and_versions.json",
     "I3_config.byte_identical", True),
    ("05E-R1 版本与本单两版都不同", "与本单两版都不同", "00_inputs_and_versions.json",
     "I4_versions.05E_R1_differs_from_both", True),
    ("05E-R1 版本号", "f7056980e0cef0cac7be31ee76fee19357ce33b7704b37add8dc4de83816c8c6",
     "00_inputs_and_versions.json", "I4_versions.05E_R1_tested_version", None),
    ("输入核对结论", "输入未换（I1–I4）",
     "00_inputs_and_versions.json", "summary.verdict", None),
    # ── 回放汇总 ──────────────────────────────────────────────────────────
    ("修前事件数", "12 | 12", "01_replay_summary.json", "pre.n_events_total", 12),
    ("修后事件数", "12 | 12", "01_replay_summary.json", "post.n_events_total", 12),
    ("修前迁移条数", "38", "01_replay_summary.json", "pre.n_transitions_total", 38),
    ("修后迁移条数", "36", "01_replay_summary.json", "post.n_transitions_total", 36),
    ("修前候选条数", "**2**", "01_replay_summary.json", "pre.n_candidates_total", 2),
    ("修后候选条数", "**0**", "01_replay_summary.json", "post.n_candidates_total", 0),
    ("修前缺口行数", "缺口行数 | 2 | 0", "01_replay_summary.json",
     "pre.n_gap_rows_total", 2),
    ("修后缺口行数", "缺口行数 | 2 | 0", "01_replay_summary.json",
     "post.n_gap_rows_total", 0),
    ("SOL 箱体数", "146 / 2 / 14", "01_replay_summary.json",
     "pre.per_symbol.SOLUSDT.boxes_formed", 146),
    ("SOL 修前 actions", "146 / 2 / 14", "01_replay_summary.json",
     "pre.per_symbol.SOLUSDT.actions", 14),
    ("SOL 修后 actions", "146 / 2 / 12", "01_replay_summary.json",
     "post.per_symbol.SOLUSDT.actions", 12),
    ("AVAX 箱体数", "195 / 2 / 12", "01_replay_summary.json",
     "pre.per_symbol.AVAXUSDT.boxes_formed", 195),
    ("SOL 4h 根数", "4h **570** 根 / 1h **2280** 根", "01_replay_summary.json",
     "pre.per_symbol.SOLUSDT.bars_4h", 570),
    ("SOL 1h 根数", "4h **570** 根 / 1h **2280** 根", "01_replay_summary.json",
     "pre.per_symbol.SOLUSDT.bars_1h", 2280),
    ("AVAX 4h 根数", "4h **618** 根 / 1h **2472** 根", "01_replay_summary.json",
     "pre.per_symbol.AVAXUSDT.bars_4h", 618),
    ("AVAX 1h 根数", "4h **618** 根 / 1h **2472** 根", "01_replay_summary.json",
     "pre.per_symbol.AVAXUSDT.bars_1h", 2472),
    ("修前 INVALIDATED 数", "`INVALIDATED` 9", "01_replay_summary.json",
     "pre.events_by_state.INVALIDATED", 9),
    ("修后 INVALIDATED 数", "`INVALIDATED` 12", "01_replay_summary.json",
     "post.events_by_state.INVALIDATED", 12),
    ("修前 EXPIRED 数", "`EXPIRED` 1", "01_replay_summary.json",
     "pre.events_by_state.EXPIRED", 1),
    ("配置闸门状态相同", "same_config", "01_replay_summary.json", "summary.same_config", True),
    ("开仓许可档位相同", "fixture_chain_selfcheck", "01_replay_summary.json",
     "post.config.open_scope", "fixture_chain_selfcheck"),
    # ── 逐事件对比 ────────────────────────────────────────────────────────
    ("原 7 事件数", "| 7 | 4a | AVAXUSDT |", "02_event_compare.json",
     "A_original_7_events.n", 7),
    ("7 条逐字段一致", "| 一致 |", "02_event_compare.json",
     "A_original_7_events.n_identical", 7),
    ("零差异条数", "零差异是合理结果", "02_event_compare.json",
     "A_original_7_events.n_differs", 0),
    ("NOT_FOUND 条数", "6 条 `INVALIDATED` + 1 条 `NOT_FOUND`", "02_event_compare.json",
     "A_original_7_events.n_not_found", 1),
    # ── NOT_FOUND 归因 ────────────────────────────────────────────────────
    ("第 7 事件箱体达标", "formed=true", "02_event_compare.json",
     f"A2_not_found_attribution.{NF}.post.box_formed", True),
    ("第 7 事件箱体宽度", "6.465870233608587", "02_event_compare.json",
     f"A2_not_found_attribution.{NF}.post.box_row.width_atr", 6.465870233608587),
    ("第 7 事件 ER", "0.2542669584245077", "02_event_compare.json",
     f"A2_not_found_attribution.{NF}.post.box_row.er", 0.2542669584245077),
    ("第 7 事件 ATR", "0.13362470460806056", "02_event_compare.json",
     f"A2_not_found_attribution.{NF}.post.box_row.atr", 0.13362470460806056),
    ("第 7 事件占位者开始", "2026-09-12T03:59:59Z", "02_event_compare.json",
     f"A2_not_found_attribution.{NF}.post.occupied_by.0.structure_seen_utc",
     "2026-09-12T03:59:59Z"),
    ("第 7 事件占位者结束", "2026-09-16T00:59:59Z", "02_event_compare.json",
     f"A2_not_found_attribution.{NF}.post.occupied_by.0.last_transition_utc",
     "2026-09-16T00:59:59Z"),
    ("第 7 事件配额", "boxes_used=2", "02_event_compare.json",
     f"A2_not_found_attribution.{NF}.post.boxes_used", 2),
    ("第 7 事件未失效标记", "is_invalidated", "02_event_compare.json",
     f"A2_not_found_attribution.{NF}.is_invalidated", False),
    # ── 三方对照 ──────────────────────────────────────────────────────────
    ("三方终态一致数", "**7/7**", "02_event_compare.json",
     "A3_third_party_05E_R1.n_state_same", 7),
    ("三方迁移一致数", "**7/7**", "02_event_compare.json",
     "A3_third_party_05E_R1.n_transitions_same", 7),
    # ── 范围外差异 ────────────────────────────────────────────────────────
    ("范围外差异条数", "范围外差异（B 段）", "02_event_compare.json",
     "B_out_of_scope_diff.n_out_of_scope_diff", 3),
    ("4c 修前超期原因", "丙：回抽等待超期", "02_event_compare.json",
     "B_out_of_scope_diff.rows.2.pre_closed_reason", "丙：回抽等待超期"),
    ("4c 修前超期时刻", "2026-08-10T06:59:59Z", "02_event_compare.json",
     "B_out_of_scope_diff.rows.2.transition_diffs.1.pre.at", "2026-08-10T06:59:59Z"),
    # ── 机制证据 ──────────────────────────────────────────────────────────
    ("修前候选数（机制段）", "候选 2 → 0", "02_event_compare.json",
     "C_candidate_mechanism.n_pre_candidates", 2),
    ("修后候选数（机制段）", "候选 2 → 0", "02_event_compare.json",
     "C_candidate_mechanism.n_post_candidates", 0),
    ("修前买占比", "0.5053721277", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.pre_absorption_at_confirm_bar.absorption_buy_ratio.value",
     0.5053721277),
    ("修前买占比基准", "0.4954198579", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.pre_absorption_at_confirm_bar.absorption_buy_ratio.requirement",
     ">= 基准 0.4954198579 × 1.0 = 0.4954198579"),
    ("修前卖压额", "15341824.2882", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.pre_absorption_at_confirm_bar.sell_pressure_amount.value",
     15341824.2882),
    ("修前卖压上限", "15619522.64", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.pre_absorption_at_confirm_bar.sell_pressure_amount.requirement",
     "<= 基准 19524403.3 × 0.8 = 15619522.64"),
    ("修前上半区价", "74.64 ≥ 74.59", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.pre_absorption_at_confirm_bar.absorption_close_upper_half.value",
     74.64),
    ("修后扫低根买占比", "0.4202349981761731", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.post_absorption_at_sweep_bar.detail.buy_ratio",
     0.4202349981761731),
    ("修后扫低根基准", "0.5041088746024686", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.post_absorption_at_sweep_bar.detail.baseline_buy_ratio",
     0.5041088746024686),
    ("修后扫低根卖压额", "17132168.3386", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.post_absorption_at_sweep_bar.detail.sell_amount",
     17132168.3386),
    ("修后卖压上限", "10927109.81", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.post_absorption_at_sweep_bar.detail.baseline_sell_amount",
     13658887.265199998),
    ("修后扫低根收盘", "72.85 < 72.855", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.post_absorption_at_sweep_bar.detail.bar_open_ms", 1785726000000),
    ("基准窗口左端", "1785654000000", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.post_absorption_at_sweep_bar.baseline_window.from_ms",
     1785654000000),
    ("基准窗口右端", "1785725999999", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.post_absorption_at_sweep_bar.baseline_window.to_ms",
     1785725999999),
    ("基准根数", "n=20", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.post_absorption_at_sweep_bar.baseline_window.n", 20),
    ("扫低根收盘时刻", "2026-08-03T03:59:59Z", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.post_absorption_at_sweep_bar.bar_close_utc",
     "2026-08-03T03:59:59Z"),
    ("确认根时刻", "2026-08-08T05:59:59Z", "02_event_compare.json",
     "C_candidate_mechanism.rows.0.confirm_utc", "2026-08-08T05:59:59Z"),
    # ── 归因钉死（D 段）──────────────────────────────────────────────────
    ("确认根 1h 下标", "1h 下标 1613", "02_event_compare.json",
     "D_attribution_check.bar_i", 1613),
    ("确认根压制位", "74.06", "02_event_compare.json",
     "D_attribution_check.resistance", 74.06),
    ("确认根收盘", "收盘 74.64", "02_event_compare.json",
     "D_attribution_check.bar_close", 74.64),
    ("确认根开盘", "开盘 74.61", "02_event_compare.json",
     "D_attribution_check.bar_open", 74.61),
    ("确认模式", "1h_close", "02_event_compare.json",
     "D_attribution_check.confirm_mode", "1h_close"),
    ("合格区间下界", "70.97159716364895", "02_event_compare.json",
     "D_attribution_check.qualify_detail.low_bound", 70.97159716364895),
    ("合格区间上界", "77.01840283635106", "02_event_compare.json",
     "D_attribution_check.qualify_detail.high_bound", 77.01840283635106),
    ("确认根 ATR", "0.9231943272978799", "02_event_compare.json",
     "D_attribution_check.qualify_detail.atr", 0.9231943272978799),
    ("压制位合格", "why=ok", "02_event_compare.json",
     "D_attribution_check.why_qualified", "ok"),
    ("确认根承接通过", "ok=true", "02_event_compare.json",
     "D_attribution_check.absorption_at_confirm_bar_ok", True),
    ("扫低根下标", "扫低根 1491", "02_event_compare.json",
     "D_attribution_check.absorption_at_sweep_bar_i", 1491),
    ("扫低根承接不通过", "ok=false", "02_event_compare.json",
     "D_attribution_check.absorption_at_sweep_bar_ok", False),
    ("差异归因到承接", "divergence_attributable_to_absorption=true", "02_event_compare.json",
     "D_attribution_check.divergence_attributable_to_absorption", True),
    ("压制位两版逐字相同", "resistance_unchanged_between_versions=true", "02_event_compare.json",
     "D_attribution_check.resistance_unchanged_between_versions", True),
    # ── 保护回归 ──────────────────────────────────────────────────────────
    ("闸门用例数", "**11/11**", "03_protection_regression.json",
     "R1_approved_gate.summary.cases_ok", 11),
    ("闸门必填项数", "required_fields=49", "03_protection_regression.json",
     "R1_approved_gate.required_fields", 49),
    ("闸门用例总数", "**11/11**", "03_protection_regression.json",
     "R1_approved_gate.summary.cases_n", 11),
    ("闸门全通过", "**11/11** 用例通过", "03_protection_regression.json",
     "R1_approved_gate.summary.all_cases_ok", True),
    ("口径用例全过", "**5/5**", "03_protection_regression.json",
     "R2_economic_caliber.all_pass", True),
    ("口径用例数", "**5/5**", "03_protection_regression.json",
     "summary.R2_cases_n", 5),
    ("去重三次返回", "[true, false, false]", "03_protection_regression.json",
     "R3_gap_dedup.unit.returns_three_calls", [True, False, False]),
    ("去重同键行数", "**1 行**", "03_protection_regression.json",
     "R3_gap_dedup.unit.rows_after_three_same", 1),
    ("去重换键行数", "**2 行**", "03_protection_regression.json",
     "R3_gap_dedup.unit.rows_after_new_key", 2),
    ("裸写行数", "**3 行**", "03_protection_regression.json",
     "R3_gap_dedup.unit.raw_add_data_gap_three_calls_rows", 3),
    ("端到端轮数", "**1 → 1**", "03_protection_regression.json",
     "R3_gap_dedup.end_to_end.rounds.1.gap_rows_this_event", 1),
    ("端到端第 1 轮缺口", "端到端：同一事件连跑两轮，缺口行数", "03_protection_regression.json",
     "R3_gap_dedup.end_to_end.rounds.0.gap_rows_this_event", 1),
    ("端到端候选不重复", "候选 **1 → 1**", "03_protection_regression.json",
     "R3_gap_dedup.end_to_end.candidate_not_duplicated", True),
    ("共享文件数", "**10/10**", "03_protection_regression.json",
     "R4_shared_files.n", 10),
    ("共享文件全未动", "mtime 早于本日 00:00Z", "03_protection_regression.json",
     "R4_shared_files.all_untouched", True),
    ("生产目录研究臂文件数", "研究臂文件 **0** 个", "03_protection_regression.json",
     "R5_write_scope.research_ledger_files_in_production", []),
    ("保护回归全通过", "每条都给正向对照", "03_protection_regression.json", "summary.all_ok", True),
    # ── 交付冻结 ──────────────────────────────────────────────────────────
    ("diff 新增行", "+62", "04_handoff.json", "D2_diff.lines_added", 62),
    ("diff 删除行", "−6 行", "04_handoff.json", "D2_diff.lines_removed", 6),
    ("diff hunk 数", "5 个 hunk", "04_handoff.json", "D2_diff.hunks", 5),
    ("patch 字节", "6674 字节", "04_handoff.json", "D2_diff.patch_bytes", 6674),
    ("AST 新增定义数", "新增 0 个", "04_handoff.json",
     "D3_interface.ast.added", []),
    ("AST 删除定义数", "删除 0 个", "04_handoff.json",
     "D3_interface.ast.removed", []),
    ("AST 签名改动数", "签名改动 0 个", "04_handoff.json",
     "D3_interface.ast.signature_changed", []),
    ("交付接口签名未变", "签名完全未变", "04_handoff.json",
     "D3_interface.ast.iface_unchanged", True),
    ("新增 payload 键全部成立", "修前不存在、修后存在", "04_handoff.json",
     "summary.payload_keys_all_new", True),
]

#: 反向扫描白名单：结构性数字，附理由（不是「解释不了就加进来」）
WHITELIST = {
    "1": "章节号/序数", "2": "章节号/序数", "3": "章节号/序数", "4": "章节号/序数",
    "5": "章节号/序数", "6": "章节号/序数", "7": "章节号/序数", "8": "章节号/序数",
    "9": "章节号/序数", "10": "章节号/序数", "11": "章节号/序数", "12": "章节号/序数",
    "13": "章节号/序数", "14": "章节号/序数",
    "04": "工单编号 06-04 的片段", "03": "工单编号 06-03 的片段",
    "06": "工单编号 06-xx 的片段", "05": "工单编号 05-B / 05-E 的片段",
    "09": "工单编号 06-09 的片段", "07": "脚本编号 07 的片段",
    "00": "脚本编号 00 的片段", "01": "脚本编号 01 的片段",
    "02": "脚本编号 02 的片段",
    "256": "「sha256」字样里的 256，不是测量值",
    "0.5": "`0.5A` 容差 / 上半区 0.5 系数",
    "1.0": "买占比倍数 1.0（配置值）",
    "0.8": "卖压倍数 0.8（配置值）",
    "0.001": "参与率 0.1%（配置值）",
    "240": "观察期限 240 小时（冻结参数）",
    "336": "观察期限 336 小时（规则上限）",
    "200": "ATR 预热 200 根",
    "42": "箱体 42 根",
    "24": "局部支撑 24 根",
    "48": "局部支撑 48 根",
    "15": "15m 周期 / 延迟 15 分钟",
    "60": "预热 60 天",
    "103.9": "06-01 记录的「扫低根相对压制位」引用值（属 06-01 产物，不在本单 out/ 里）",
    "105.0": "06-01 记录的「确认根相对压制位」引用值（同上）",
    "8.1": "章节号 §8.1",
    "3.9": "解释器版本 3.9.6 的片段",
    "3.13": "托管解释器版本 3.13 的片段",
    "3.9.6": "解释器版本",
}


def dig(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


def norm(v):
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    return str(v)


def collect_numbers(obj, out):
    if isinstance(obj, bool):
        out.add(str(obj).lower())
    elif isinstance(obj, (int, float)):
        out.add(str(obj))
        out.add(f"{obj:.6f}".rstrip("0").rstrip("."))
    elif isinstance(obj, str):
        for m in re.findall(r"-?\d+(?:\.\d+)?", obj):
            out.add(m)
            # 日期/时刻里的 '-' 会被当成负号（"2026-08-08" → "-08"），
            # 正文里写的是 08，所以同时记一份去符号值。只放宽「符号」，不放宽数值。
            out.add(m.lstrip("-"))
    elif isinstance(obj, list):
        for x in obj:
            collect_numbers(x, out)
    elif isinstance(obj, dict):
        for x in obj.values():
            collect_numbers(x, out)


def main() -> int:
    text = README.read_text(encoding="utf-8")
    out = {"readme": str(README), "readme_bytes": README.stat().st_size,
           "facts": [], "reverse": {}, "summary": {}}

    # ── 正向 ──────────────────────────────────────────────────────────────
    cache = {}
    fwd_ok = True
    for label, literal, fname, path, expect in FACTS:
        if fname not in cache:
            cache[fname] = json.loads((W.OUT / fname).read_text(encoding="utf-8"))
        got = dig(cache[fname], path)
        in_readme = literal in text
        value_ok = True if expect is None else (norm(got) == norm(expect))
        ok = in_readme and value_ok
        fwd_ok = fwd_ok and ok
        out["facts"].append({"label": label, "literal_in_readme": in_readme,
                             "json_path": f"{fname}:{path}", "json_value": got,
                             "expected": expect, "value_ok": value_ok, "ok": ok})

    # ── 反向 ──────────────────────────────────────────────────────────────
    allowed = set()
    for p in sorted(W.OUT.glob("*.json")):
        try:
            collect_numbers(json.loads(p.read_text(encoding="utf-8")), allowed)
        except Exception:                                        # noqa: BLE001
            pass
    allowed |= set(WHITELIST)

    # 只扫正文，剔除代码块、行内代码、表格分隔线
    body = re.sub(r"```.*?```", " ", text, flags=re.S)
    body = re.sub(r"`[^`]*`", " ", body)
    body = re.sub(r"^\|[-: |]+\|$", " ", body, flags=re.M)
    tokens = re.findall(r"\d+(?:\.\d+)?", body)
    unexplained = []
    for t in tokens:
        if t in allowed:
            continue
        if re.fullmatch(r"20\d{2}", t):            # 年份
            continue
        unexplained.append(t)
    out["reverse"] = {"n_tokens": len(tokens), "n_allowed": len(allowed),
                      "unexplained": sorted(set(unexplained)),
                      "whitelist_size": len(WHITELIST)}
    rev_ok = not unexplained

    out["summary"] = {"forward_ok": fwd_ok, "n_facts": len(FACTS),
                      "forward_failed": [f["label"] for f in out["facts"] if not f["ok"]],
                      "reverse_ok": rev_ok,
                      "verdict": ("双向通过" if (fwd_ok and rev_ok) else "不通过")}
    W.dump("05_report_collision.json", out)

    print("forward:", fwd_ok, "| failed:", json.dumps(
        out["summary"]["forward_failed"], ensure_ascii=False))
    print("reverse:", rev_ok, "| unexplained:", json.dumps(
        sorted(set(unexplained))[:60], ensure_ascii=False))
    print("summary:", json.dumps(out["summary"], ensure_ascii=False))
    return 0 if (fwd_ok and rev_ok) else 2


if __name__ == "__main__":
    raise SystemExit(main())

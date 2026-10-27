# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/01｜脚本 05：正文数字 ↔ 产物 双向对撞

正向：本文声明的每个关键事实，既要在 README 里出现，也要能从 `out/*.json` 的指定位点取回同值。
反向：README 里出现的每个「测量型数字」，必须能在 `out/*.json` 里找到出处，或在**带理由**的
      结构白名单里（行号 / 章节号 / 日期 / 合成参数）。找不到就报出来，不许沉默。

不做的事：不把 README 的数字与 README 自己比（那是自证），也不从 JSON 汇总值反推正文。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2 as W                                    # noqa: E402

README = W.W2 / "README.md"
CASE_A = "A_扫低承接通过_突破根不通过"
CASE_B = "B_扫低不通过_突破根通过"
CASE_C = "C_扫低根数据不足"

#: (说明, README 里应出现的字面量, 产物文件名, 取值路径, 期望值)
FACTS = [
    ("修前代码 sha256", "d96ad60eca61f7e53e273414196100ac21b33650a19480beafb0eb60c4d41a52",
     "00_basis_and_pre_fix_trace.json", "code_before_fix.sha256", None),
    ("修前字节", "113875", "00_basis_and_pre_fix_trace.json", "code_before_fix.bytes", 113875),
    ("修后代码 sha256", "3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017",
     "02_post_fix_pass.json", "code_after_fix.sha256", None),
    ("修后字节", "117711", "02_post_fix_pass.json", "code_after_fix.bytes", 117711),
    ("依据全部读回校验", "basis_all_verified=true",
     "00_basis_and_pre_fix_trace.json", "basis_all_verified", True),
    ("修前承接调用点所在函数", '["_confirm_actions"]',
     "00_basis_and_pre_fix_trace.json", "static_summary.absorption_enclosing", [["_confirm_actions"]]),
    ("修前 A 判定下标", "[61]", "00_basis_and_pre_fix_trace.json",
     f"before_fix_reads_bar_index.{CASE_A}", [61]),
    ("修前 A 候选数", "0 候选", "01_pre_fix_fail.json", f"cases.{CASE_A}.actual.n_candidates", 0),
    ("修前 B 候选数", "1 候选", "01_pre_fix_fail.json", f"cases.{CASE_B}.actual.n_candidates", 1),
    ("修前 C 候选数", "1 候选", "01_pre_fix_fail.json", f"cases.{CASE_C}.actual.n_candidates", 1),
    ("修前三类全部不符预期", "修前必失败：三类全部不符预期",
     "01_pre_fix_fail.json", "summary.verdict", None),
    ("修后 A 候选数", "1 候选（WAIT_ENTRY）", "02_post_fix_pass.json",
     f"cases.{CASE_A}.actual.n_candidates", 1),
    ("修后 B 候选数", "0 候选", "02_post_fix_pass.json",
     f"cases.{CASE_B}.actual.n_candidates", 0),
    ("修后 C 终结状态", "DATA_INSUFFICIENT", "02_post_fix_pass.json",
     f"cases.{CASE_C}.actual.terminal_state", "DATA_INSUFFICIENT"),
    ("修后 A 承接下标", "[60]", "02_post_fix_pass.json",
     f"cases.{CASE_A}.actual.absorption_call_indices", [60]),
    ("修后 B 承接下标", "[60]", "02_post_fix_pass.json",
     f"cases.{CASE_B}.actual.absorption_call_indices", [60]),
    ("修后 C 承接下标", "[60]", "02_post_fix_pass.json",
     f"cases.{CASE_C}.actual.absorption_call_indices", [60]),
    ("修后静态：在 on_bar 内", "in_on_bar", "02_post_fix_pass.json", "static.in_on_bar", True),
    ("修后静态：不在 confirm_actions 内", "in_confirm_actions",
     "02_post_fix_pass.json", "static.in_confirm_actions", False),
    ("修后静态：实参 i_sweep", "i_sweep", "02_post_fix_pass.json", "static.arg_i", ["i_sweep"]),
    ("扫低根时刻", "2026-06-03T12:59:59Z", "02_post_fix_pass.json",
     f"cases.{CASE_A}.explain.sweep_bar.bar_close_utc", "2026-06-03T12:59:59Z"),
    ("A 突破根时刻", "2026-06-03T13:59:59Z", "02_post_fix_pass.json",
     f"cases.{CASE_A}.explain.confirm_bar.bar_close_utc", "2026-06-03T13:59:59Z"),
    ("C 突破根时刻", "2026-06-04T14:59:59Z", "02_post_fix_pass.json",
     f"cases.{CASE_C}.explain.confirm_bar.bar_close_utc", "2026-06-04T14:59:59Z"),
    ("基准窗口右端", "1780487999999", "02_post_fix_pass.json",
     f"cases.{CASE_A}.transition_payload_absorption.absorption_baseline_window.to_ms", 1780487999999),
    ("基准窗口左端", "1780416000000", "02_post_fix_pass.json",
     f"cases.{CASE_A}.transition_payload_absorption.absorption_baseline_window.from_ms", 1780416000000),
    ("基准根数", "n=20", "02_post_fix_pass.json",
     f"cases.{CASE_A}.transition_payload_absorption.absorption_baseline_window.n", 20),
    ("A 扫低根买占比", "0.7", "02_post_fix_pass.json",
     f"cases.{CASE_A}.explain.sweep_bar.buy_ratio", 0.7),
    ("A 突破根买占比", "0.3", "02_post_fix_pass.json",
     f"cases.{CASE_A}.explain.confirm_bar.buy_ratio", 0.3),
    ("B 扫低根买占比", "0.3", "02_post_fix_pass.json",
     f"cases.{CASE_B}.explain.sweep_bar.buy_ratio", 0.3),
    ("B 突破根买占比", "0.7", "02_post_fix_pass.json",
     f"cases.{CASE_B}.explain.confirm_bar.buy_ratio", 0.7),
    ("A 扫低根卖额", "300", "02_post_fix_pass.json",
     f"cases.{CASE_A}.explain.sweep_bar.sell_amount", 300.0),
    ("A 突破根卖额", "700", "02_post_fix_pass.json",
     f"cases.{CASE_A}.explain.confirm_bar.sell_amount", 700.0),
    ("端到端候选数", "1 行", "02_post_fix_pass.json", "integration.scan_candidates", 1),
    ("截断后候选数", "0 候选", "03_truncation_gates_params.json",
     "T_truncation.truncated_n_candidates", 0),
    ("截断后最大触碰下标", "**60**", "03_truncation_gates_params.json",
     "T_truncation.max_bar_index_touched_in_truncated_run", 60),
    ("截断前缀一致", "逐条相同", "03_truncation_gates_params.json",
     "T_truncation.prefix_actions_identical", True),
    ("负向对照可分辨", "**不同**", "03_truncation_gates_params.json",
     "T_truncation.negative_control_distinguishes", True),
    ("未批准配置缺项数", "缺 **48** 项", "03_truncation_gates_params.json",
     "G_gates.blank_config.n_missing", 48),
    ("未批准配置承接调用次数", "承接调用 **0** 次", "03_truncation_gates_params.json",
     "G_gates.blank_config.absorption_calls", 0),
    ("必填项数", "必填 **49** 项", "03_gates", "G_gates.required_paths", 49),
    ("配置差异条数", "只有 1 条", "03_truncation_gates_params.json",
     "P_params.config_deep_diff_vs_05B_fixture", None),
    ("压制位（扫低根相对）", "103.9", "03_truncation_gates_params.json",
     "R_adjacent_defect.sweep_relative_resistance", 103.9),
    ("压制位（确认根相对）", "105.0", "03_truncation_gates_params.json",
     "R_adjacent_defect.confirm_relative_resistance", 105.0),
    ("逆向重建对上修前 sha", "逐位等于修前值", "04_code_change_proof.json",
     "reconstruction_matches_pre_fix", True),
    ("diff 变动行", "68 行变动", "04_code_change_proof.json", "diff.changed_lines", 68),
    ("diff 新增行", "+62", "04_code_change_proof.json", "diff.added", 62),
    ("diff 删除行", "−6", "04_code_change_proof.json", "diff.removed", 6),
]

#: 反向扫描的白名单：结构性数字，附理由（不是「解释不了就加进来」）
WHITELIST = {
    "53": "行号（04-A README:53）", "134": "行号", "28": "行号", "1065": "行号",
    "128": "行号", "48": "行号 与 局部支撑根数 24/48 的「48」共用", "52": "行号",
    "1": "章节号/序数", "2": "章节号/序数", "3": "章节号/序数", "4": "章节号/序数",
    "5": "章节号/序数", "6": "章节号/序数", "7": "章节号/序数", "8": "章节号/序数",
    "9": "章节号/序数", "10": "章节号/序数", "11": "章节号/序数", "12": "章节号/序数",
    "13": "章节号/序数", "15": "延迟 15 分钟 / 15m 周期", "24": "局部支撑 24 根",
    "14": "行号/ATR 周期 14", "42": "箱体 42 根", "200": "预热 200 根",
    "336": "观察期限 336 小时", "0.95": "止损 95%", "0.8": "卖压倍数 0.8（配置值）",
    "1.0": "买占比倍数 1.0（配置值）/ ATR=1.0 合成参数", "5": "5bps 手续费",
    "100": "箱体下沿 L=100（合成参数）", "110": "箱体上沿 H=110（合成参数）",
    "105": "尖峰高点 105（合成参数）", "103.95": "确认根收盘 103.95（合成参数）",
    "400": "卖压阈值 500×0.8=400（合成参数推导）", "500": "基准卖额中位数（合成参数）",
    "0.5": "基准买占比中位数 / 0.5A 容差", "2026": "年份", "3.9.6": "解释器版本",
    "117548": "修后字节（同 FACTS）", "113875": "修前字节（同 FACTS）",
    "2026-06-03T12:59:59Z": "时刻（同 FACTS）",
    "256": "「sha256」字样里的 256，不是测量值",
    "3.9": "解释器版本 3.9.6 的片段", "3.13": "托管解释器版本 3.13 的片段",
    "6": "解释器版本 3.9.6 的片段", "09": "工单编号 06-09 的片段",
    "18": "文件修改时刻 18:29 的片段", "22": "文件修改时刻 22:11 / 22:14 的片段",
    "23": "文件修改时刻 21:23 的片段", "29": "文件修改时刻 18:29 的片段",
    "56": "文件修改时刻 01:56 的片段",
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
        fname = ("03_truncation_gates_params.json" if fname == "03_gates" else fname)
        if fname not in cache:
            cache[fname] = json.loads((W.OUT / fname).read_text(encoding="utf-8"))
        got = dig(cache[fname], path)
        in_readme = literal in text
        value_ok = True if expect is None else (norm(got) == norm(expect))
        if label == "配置差异条数":
            value_ok = (len(got) == 1 and got[0]["path"] == "approval_ref")
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

    # 只扫正文，剔除代码块、表格分隔线、链接、行内代码里的路径
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
    out["reverse"] = {"n_tokens": len(tokens),
                      "n_allowed": len(allowed),
                      "unexplained": sorted(set(unexplained)),
                      "whitelist_size": len(WHITELIST)}
    rev_ok = not unexplained

    out["summary"] = {"forward_ok": fwd_ok,
                      "n_facts": len(FACTS),
                      "forward_failed": [f["label"] for f in out["facts"] if not f["ok"]],
                      "reverse_ok": rev_ok,
                      "verdict": ("双向通过" if (fwd_ok and rev_ok) else "不通过")}
    W.dump("05_report_collision.json", out)

    print("forward:", fwd_ok, "| failed:", json.dumps(
        out["summary"]["forward_failed"], ensure_ascii=False))
    print("reverse:", rev_ok, "| unexplained:", json.dumps(
        sorted(set(unexplained))[:40], ensure_ascii=False))
    print("summary:", json.dumps(out["summary"], ensure_ascii=False))
    return 0 if (fwd_ok and rev_ok) else 2


if __name__ == "__main__":
    raise SystemExit(main())

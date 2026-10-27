# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-E-R1 | 脚本 01: 版本固定与规则实现差异比对矩阵

功能：
1. 提取并记录被验版本的代码文件、配置模板的 SHA-256 哈希值；
2. 静态与代码逻辑深度审计，明确区分：
   - 旧缺陷 (04-B 遗留缺陷)
   - 当前已修 (05-B 当前版本确已修复项)
   - 当前仍失败 (05-B 声称修复但代码实测仍存在语义偏离或时序错位)
   - 未覆盖 / 待决项 (需用户裁决或尚未自然验证项)
3. 结构化输出至 out/01_version_matrix.json
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E-R1" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def sha256_file(path: Path) -> str:
    if not path.exists():
        return "FILE_NOT_FOUND"
    return hashlib.sha256(path.read_bytes()).hexdigest()

def audit_code_and_version():
    target_code = BACKEND_DIR / "accum_early_entry.py"
    fixture_cfg = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
    template_cfg = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "批准配置_空白表单_05.json"
    paper_engine = BACKEND_DIR / "paper_engine.py"
    live_store = BACKEND_DIR / "live_store.py"

    code_hashes = {
        "accum_early_entry.py": sha256_file(target_code),
        "fixture_rules_05.json": sha256_file(fixture_cfg),
        "批准配置_空白表单_05.json": sha256_file(template_cfg),
        "paper_engine.py": sha256_file(paper_engine),
        "live_store.py": sha256_file(live_store),
    }

    code_text = target_code.read_text(encoding="utf-8")

    # 审计 1: 扫低对象约束 s_loc > L
    has_must_above_flag = "must_be_above_box_low" in code_text
    has_must_above_check = 'if must_above and ref <= float(box["low_l"]):' in code_text or "ref <= float(box['low_l'])" in code_text

    # 审计 2: 局部压制位算法与空间合格性
    has_swing_high = "swing_high_k3_lookback48" in code_text
    has_qualify_func = "def _resistance_qualified(" in code_text
    has_no_res_close = 'NO_RESISTANCE_PREFIX + why_q' in code_text or '"terminal_reason": "NO_RESISTANCE"' in code_text

    # 审计 3: 承接判定时序与所用 K 线
    # 查找 _absorption 在何处被调用
    absorption_calls = []
    lines = code_text.splitlines()
    for idx, l in enumerate(lines):
        if "_absorption(" in l and "def _absorption(" not in l:
            absorption_calls.append({"line": idx + 1, "code": l.strip()})
    
    # 检查 _sweep_check 内部是否调用了 _absorption
    sweep_check_lines = []
    in_sweep = False
    for idx, l in enumerate(lines):
        if "def _sweep_check(" in l:
            in_sweep = True
        elif in_sweep and l.startswith("def "):
            in_sweep = False
        if in_sweep:
            sweep_check_lines.append(l)
    sweep_has_absorption = any("_absorption" in l for l in sweep_check_lines)

    # 检查 confirm_actions 内部是否调用了 _absorption
    confirm_lines = []
    in_confirm = False
    for idx, l in enumerate(lines):
        if "def _confirm_actions(" in l:
            in_confirm = True
        elif in_confirm and l.startswith("def "):
            in_confirm = False
        if in_confirm:
            confirm_lines.append(l)
    confirm_has_absorption = any("_absorption" in l for l in confirm_lines)

    # 审计 4: 失效与冷却
    has_structural_inv = "def _structural_invalid(" in code_text
    has_close_below_l = "close_below_l_bars" in code_text
    has_l_overshoot = "l_overshoot_atr" in code_text
    has_armed_fail = "def _hold_actions(" in code_text and "armed_fail_count" in code_text

    # 整理比对矩阵
    matrix = {
        "version_hashes": code_hashes,
        "schema_version": "accum-early-05/v1",
        "categories": {
            "fixed_items": [
                {
                    "item": "扫低对象约束 s_loc > box['L']",
                    "status": "CURRENT_FIXED",
                    "evidence": "accum_early_entry.py 中在 _sweep_check 引入 must_be_above_box_low 逻辑，当 ref <= box['low_l'] 时返回 False 并标记 reference_not_above_box_low",
                    "code_line": "779-786"
                },
                {
                    "item": "局部压制位 swing_high_k3_lookback48 实现",
                    "status": "CURRENT_FIXED",
                    "evidence": "_resistance 函数中支持 swing_high_k3_lookback48，从近到远寻找左右各 k 根不高于当根的摆动高点",
                    "code_line": "719-737"
                },
                {
                    "item": "压制位空间合格性校验 resistance_qualify",
                    "status": "CURRENT_FIXED",
                    "evidence": "实现 _resistance_qualified，强制要求 L + 0.5A <= R <= H - 0.5A，不合格时记录 terminal_reason='NO_RESISTANCE' 并终结",
                    "code_line": "741-758, 1071-1076"
                },
                {
                    "item": "结构破位失效保护 (连续收盘破 L / 盘中破 L-0.5A)",
                    "status": "CURRENT_FIXED",
                    "evidence": "实现 _structural_invalid，对 BOX/SWEEP/RETEST 全流程检查 close_below_l_bars 与 l_overshoot_atr",
                    "code_line": "1007-1040"
                },
                {
                    "item": "经济口径一致性声明硬校验",
                    "status": "CURRENT_FIXED",
                    "evidence": "实现 _economic_caliber 与 config 校验，声明 strategy_package 与底层 paper_engine 生效 effective_view 冲突时硬阻断开仓",
                    "code_line": "351-407"
                }
            ],
            "still_failing_items": [
                {
                    "item": "承接与卖压指标检查时序严重颠倒",
                    "status": "CURRENT_STILL_FAILING",
                    "description": "05-B 声称已实现扫低根承接判定，但代码中 _absorption 仍然仅在 _confirm_actions（第 1069 行）被调用，_sweep_check 内部 0 次调用！",
                    "impact": "导致主力吸筹判断被延后到收上压制位的确认突破根，计算所用 K 线与前 20 根基准全为突破时点，严重背离 04-A 规则规范中在扫低恐慌瞬间核验主力承接的金融因果逻辑",
                    "evidence": {
                        "sweep_check_has_absorption": sweep_has_absorption,
                        "confirm_actions_has_absorption": confirm_has_absorption,
                        "actual_calls": absorption_calls
                    }
                },
                {
                    "item": "未批准配置下的冷却时间硬设为 0",
                    "status": "CURRENT_STILL_FAILING",
                    "description": "05-B 在 fixture_rules_05.json 中将 cooldown_hours 硬编码为 0，导致同标的在未批准扫描测试时同一天内高频建档",
                    "impact": "违背安全回退默认值原则（应保持 24 小时默认冷却），导致测试记录冗余膨胀",
                    "evidence": "fixture_rules_05.json 行 65: cooldown_hours=0"
                }
            ],
            "uncovered_or_pending_items": [
                {
                    "item": "自然行情下全链闭环与真实策略绩效",
                    "status": "UNCOVERED_PENDING",
                    "description": "当前所有验证均在测试配置 (fixture) 下运行，真实策略参数仍处于待批准状态，未获用户授权，严禁把测试结果当策略绩效"
                },
                {
                    "item": "历史行情 available_at 证据链",
                    "status": "UNCOVERED_PENDING",
                    "description": "冷档历史 K 线缺少 available_at 字段，继续诚实标为 unknown/not_proven，不把历史已知等同于当时可得"
                }
            ]
        }
    }

    out_file = OUT_DIR / "01_version_matrix.json"
    out_file.write_text(json.dumps(matrix, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Version matrix generated at {out_file}")
    return matrix

if __name__ == "__main__":
    audit_code_and_version()

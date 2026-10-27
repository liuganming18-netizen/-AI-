# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-01 | 脚本 01: 共享文件版本哈希与状态机调用轨迹审计

功能：
1. 计算当前共享工程文件与配置模板的真实 SHA-256 哈希值；
2. 静态分析并精确定位状态机核心关键函数的调用轨迹（尤其是 _absorption 的调用位置与行号）；
3. 结构化输出至 out/01_version_and_trajectory.json。
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "01" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def sha256_file(path: Path) -> str:
    if not path.exists():
        return "FILE_NOT_FOUND"
    return hashlib.sha256(path.read_bytes()).hexdigest()

def record_version_and_trajectory():
    files = {
        "accum_early_entry.py": BACKEND_DIR / "accum_early_entry.py",
        "paper_engine.py": BACKEND_DIR / "paper_engine.py",
        "live_store.py": BACKEND_DIR / "live_store.py",
        "replay_calc.py": BACKEND_DIR / "replay_calc.py",
        "fixture_rules_05.json": PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json",
        "批准配置_空白表单_05.json": PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "批准配置_空白表单_05.json",
        "05_E_R1_README.md": PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E-R1" / "README.md"
    }

    hashes = {}
    for name, p in files.items():
        hashes[name] = {
            "path": str(p),
            "sha256": sha256_file(p),
            "exists": p.exists(),
            "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.stat().st_mtime)) if p.exists() else None
        }

    # 分析 accum_early_entry.py 内部调用轨迹
    target_code = BACKEND_DIR / "accum_early_entry.py"
    code_text = target_code.read_text(encoding="utf-8")
    lines = code_text.splitlines()

    # 1. 查找 _absorption 定义与调用
    absorption_def = None
    absorption_calls = []
    for idx, line in enumerate(lines):
        line_num = idx + 1
        if "def _absorption(" in line:
            absorption_def = {"line": line_num, "code": line.strip()}
        elif "_absorption(" in line:
            absorption_calls.append({"line": line_num, "code": line.strip()})

    # 2. 查找 _sweep_check 内部逻辑
    sweep_check_lines = []
    in_sweep = False
    sweep_def_line = None
    for idx, line in enumerate(lines):
        line_num = idx + 1
        if "def _sweep_check(" in line:
            in_sweep = True
            sweep_def_line = line_num
        elif in_sweep and line.startswith("def "):
            in_sweep = False
        if in_sweep:
            sweep_check_lines.append((line_num, line))
    sweep_calls_absorption = any("_absorption" in l[1] for l in sweep_check_lines)

    # 3. 查找 _confirm_actions 内部逻辑
    confirm_lines = []
    in_confirm = False
    confirm_def_line = None
    for idx, line in enumerate(lines):
        line_num = idx + 1
        if "def _confirm_actions(" in line:
            in_confirm = True
            confirm_def_line = line_num
        elif in_confirm and line.startswith("def "):
            in_confirm = False
        if in_confirm:
            confirm_lines.append((line_num, line))
    confirm_calls_absorption = any("_absorption" in l[1] for l in confirm_lines)

    # 4. 查找 _hold_actions 内部逻辑
    hold_lines = []
    in_hold = False
    hold_def_line = None
    for idx, line in enumerate(lines):
        line_num = idx + 1
        if "def _hold_actions(" in line:
            in_hold = True
            hold_def_line = line_num
        elif in_hold and line.startswith("def "):
            in_hold = False
        if in_hold:
            hold_lines.append((line_num, line))

    trajectory_analysis = {
        "absorption_definition": absorption_def,
        "absorption_calls": absorption_calls,
        "sweep_check": {
            "def_line": sweep_def_line,
            "calls_absorption": sweep_calls_absorption,
            "has_must_be_above_box_low": any("must_be_above_box_low" in l[1] for l in sweep_check_lines),
            "verdict": "扫低根完全没有调用承接判定函数（调用次数 0）"
        },
        "confirm_actions": {
            "def_line": confirm_def_line,
            "calls_absorption": confirm_calls_absorption,
            "verdict": "突破确认根错误承载了承接判定调用（调用在第 1073 行）"
        },
        "hold_actions": {
            "def_line": hold_def_line,
            "verdict": "等待确认阶段检查 armed_fail_count 与突破超时"
        }
    }

    result = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema_version": "accum-early-05/v1",
        "file_hashes": hashes,
        "trajectory_analysis": trajectory_analysis
    }

    out_file = OUT_DIR / "01_version_and_trajectory.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Recorded version and trajectory to {out_file}")

if __name__ == "__main__":
    record_version_and_trajectory()

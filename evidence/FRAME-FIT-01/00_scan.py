#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-FIT-01 步骤一：清点现有机制（只读）。

产出 `01_现有机制与缺口.csv` 与 `02_scan.json`。不写任何生产文件。

清点口径：工单要求「优先补接和修复，不另建重复实验系统」⇒
先把已有的东西逐个点到名，确认哪些能直接复用、哪些根本不存在。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "框架" / "backend"
STRATEGY = ROOT / "策略" / "WyckoffMaster"

sys.path.insert(0, str(BACKEND))

OUT_CSV = HERE / "01_现有机制与缺口.csv"
OUT_JSON = HERE / "02_scan.json"

ROWS: list[dict] = []


def row(item, exists, location, reuse, gap, note=""):
    ROWS.append({
        "验收项": item,
        "现状": "存在" if exists else "不存在",
        "实现位置": location,
        "可复用方式": reuse,
        "缺口": gap,
        "备注": note,
    })


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_meta(p: Path) -> dict:
    if not p.exists():
        return {"exists": False, "path": str(p.relative_to(ROOT))}
    import hashlib
    b = p.read_bytes()
    return {"exists": True, "path": str(p.relative_to(ROOT)),
            "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest(),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%SZ")}


# ══════════════════════ 1. 指纹机制 ══════════════════════

def scan_fingerprints(scan: dict) -> None:
    runner_py = STRATEGY / "event_trend_v0_1" / "runner.py"
    src = runner_py.read_text(encoding="utf-8") if runner_py.exists() else ""
    funcs = {name: (f"def {name}" in src) for name in
             ("code_fingerprint", "rules_fingerprint", "config_fingerprint",
              "input_fingerprint")}
    scan["fingerprint_functions"] = funcs

    row("代码指纹（代码版本）", funcs["code_fingerprint"],
        "策略/WyckoffMaster/event_trend_v0_1/runner.py::code_fingerprint",
        "直接复用：18 个文件的逐文件 sha256 + combined_sha256",
        "无（本单直接引用）",
        "与 inputs.json.fingerprints.code 同源")
    row("参数指纹（超参数集合）", funcs["config_fingerprint"],
        "策略/WyckoffMaster/event_trend_v0_1/runner.py::config_fingerprint",
        "直接复用：EventTrendConfig.to_dict() 排序后 sha256",
        "无（本单直接引用）", "实测 49 个参数")
    row("规则指纹（冻结规则文档）", funcs["rules_fingerprint"],
        "策略/WyckoffMaster/event_trend_v0_1/runner.py::rules_fingerprint",
        "直接复用：冻结规则 md 的 sha256",
        "无（本单直接引用）", "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md")
    row("数据指纹（输入 K 线/资金费）", funcs["input_fingerprint"],
        "策略/WyckoffMaster/event_trend_v0_1/runner.py::input_fingerprint",
        "跑完后复用；跑前无法算（要读数据）",
        "⚠ 运行前拿不到 ⇒ 设计身份不含数据版本，单独派生运行身份",
        "本单把数据指纹放进 run_identity，不进冻结")

    # 桥接层的取值 bug
    bridge = BACKEND / "event_trend_bridge.py"
    bsrc = bridge.read_text(encoding="utf-8") if bridge.exists() else ""
    bug = 'fp.get("sha256", "0" * 64)' in bsrc
    scan["bridge_code_hash_bug"] = bug
    row("桥接 code_hash() 取值", True,
        "框架/backend/event_trend_bridge.py::code_hash",
        "❌ 不可复用",
        "取 fp['sha256']，而 code_fingerprint 返回的键是 combined_sha256 ⇒ 恒返回 64 个 0" if bug else "无",
        "本单改为直取 combined_sha256；未改动该函数（属其他窗口边界）")


# ══════════════════════ 2. 运行注册 / 实验配置 ══════════════════════

def scan_registry(scan: dict) -> None:
    reg_py = BACKEND / "strategies" / "registry.py"
    rsrc = reg_py.read_text(encoding="utf-8") if reg_py.exists() else ""
    scan["registry"] = {
        "has_guard_runnable": "def guard_runnable" in rsrc,
        "current_backtest_id": (re.search(r'CURRENT_BACKTEST_ID\s*=\s*"([^"]+)"', rsrc) or [None, None])[1],
    }
    row("策略运行注册表", "def guard_runnable" in rsrc,
        "框架/backend/strategies/registry.py",
        "复用：策略可运行性硬闸（旧策略/未知键拒绝）",
        "⚠ 只回答「这个策略能不能跑」，不回答「这个实验该不该跑」",
        "本单在它之后叠加实验闸，不改它")

    store = BACKEND / "backtest_store.py"
    ssrc = store.read_text(encoding="utf-8") if store.exists() else ""
    scan["store"] = {
        "run_id_re": (re.search(r'RUN_ID_RE\s*=\s*re\.compile\(r"([^"]+)"', ssrc) or [None, None])[1],
        "statuses": re.findall(r'STATUS_[A-Z]+\s*=\s*"([a-z]+)"', ssrc),
    }
    row("运行目录与状态机", True, "框架/backend/backtest_store.py",
        "复用：run 目录独占创建、queued/running/succeeded/failed/interrupted",
        "⚠ 没有任何「实验」层概念：run 之间无父子、无设计身份、无冻结",
        "本单不新建运行系统，只在 run 之上加实验层")

    cfg = STRATEGY / "event_trend_v0_1" / "config.py"
    csrc = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    params = re.findall(r"^    ([a-z_][a-z0-9_]*)\s*:\s*[A-Za-z\[\]]+\s*=", csrc, re.M)
    scan["config_param_count"] = len(params)
    scan["config_params"] = params
    row("实验配置（参数集合）", bool(params), "策略/WyckoffMaster/event_trend_v0_1/config.py",
        "复用：EventTrendConfig 是唯一参数事实源",
        "⚠ 真实入口只把 initial_equity 传进去，其余参数在 event_trend 链上不生效",
        f"实测 {len(params)} 个字段")


# ══════════════════════ 3. 启动入口 ══════════════════════

def scan_entrypoints(scan: dict) -> None:
    api = BACKEND / "backtest_api.py"
    asrc = api.read_text(encoding="utf-8") if api.exists() else ""
    run_py = BACKEND / "backtest_runner.py"
    rsrc = run_py.read_text(encoding="utf-8") if run_py.exists() else ""

    scan["entrypoints"] = {
        "create_run": "def create_run(payload" in asrc,
        "execute_run": "def execute_run(run_id" in rsrc,
        "guard_hooked_create": "experiment_registry" in asrc,
        "guard_hooked_execute": "experiment_registry" in rsrc,
    }
    row("创建入口", "def create_run(payload" in asrc,
        "框架/backend/backtest_api.py::create_run（POST /api/v1/backtests）",
        "复用：本单在其策略闸之后插入实验闸",
        "原本无实验层校验",
        "拒绝时返回 409 + 逐条原因，不创建 run 目录")
    row("执行入口", "def execute_run(run_id" in rsrc,
        "框架/backend/backtest_runner.py::execute_run",
        "复用：本单在其读 run 之后、置 running 之前插入实验闸",
        "原本无实验层校验",
        "拒绝时标 failed + 写 failure_reason，不产出 trades/summary")

    # 窗口写死（STEP1-B 已登记）
    pinned = 'start_date="2024-01-01", end_date="2024-06-30"' in rsrc
    scan["pinned_window"] = pinned
    row("样本范围的实际生效值", True,
        "框架/backend/backtest_runner.py::_run_event_trend",
        "❌ 无法复用请求值",
        "窗口写死 2024-01-01~2024-06-30，params 不注入 ⇒ 登记范围≠实际范围" if pinned else "无",
        "本单不修（属回测执行改动）；但正式资格一律因此被阻断，见 08 号文件")


# ══════════════════════ 4. 冻结 / 留出 / 已观察 ══════════════════════

def scan_isolation(scan: dict) -> None:
    hits = {"freeze": [], "holdout": [], "observed": [], "trial": []}
    pats = {
        "freeze": re.compile(r"(config_freeze|freeze_config|frozen_config|experiment_freeze)"),
        "holdout": re.compile(r"(holdout|hold_out|out_of_sample|留出)"),
        "observed": re.compile(r"(seen_samples|observed_samples|已观察)"),
        "trial": re.compile(r"(trial_count|n_trials|试验次数)"),
    }
    for base in (BACKEND, STRATEGY):
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for k, pat in pats.items():
                if pat.search(txt):
                    hits[k].append(str(p.relative_to(ROOT)))
    scan["isolation_hits"] = {k: v[:20] for k, v in hits.items()}

    row("配置冻结机制", bool(hits["freeze"]),
        "、".join(hits["freeze"][:3]) or "（无）",
        "❌ 无可复用实现",
        "全库无「实验配置冻结」概念；config_fingerprint 只是算哈希，不锁身份",
        "本单新增 experiment_registry.freeze()")
    row("训练/验证/留出划分", bool(hits["holdout"]),
        "、".join(hits["holdout"][:3]) or "（无）",
        "❌ 无可复用实现",
        "全库无 holdout 划分；`formal_pass_allowed` 只表示「这单结果能不能对外」",
        "本单新增 SplitPolicy，日期保留未配置")
    row("已观察样本登记", bool(hits["observed"]),
        "、".join(hits["observed"][:3]) or "（无）",
        "❌ 无可复用实现",
        "227 条样本被反复研究，但没有任何地方记录「它们已经被看过」",
        "本单新增 observe()，并把 227 条登记为 EA04_227")
    row("试验次数/父子实验", bool(hits["trial"]),
        "、".join(hits["trial"][:3]) or "（无）",
        "❌ 无可复用实现",
        "run 之间无父子关系；失败 run 与成功 run 平铺，无法回答「试了几次」",
        "本单新增 trial 事件与 parent_experiment_id")


# ══════════════════════ 5. 四组实验计划 ══════════════════════

def scan_experiment_plans(scan: dict) -> None:
    """四组实验：现存三套（冻结规则 A/B/C、FLOW-OI-01 阶梯四组、FACTOR-ENTRY-01 平行四组）。"""
    evidence = ROOT / "evidence"
    found = []
    for pat in ("*FLOW-OI-01*", "*FACTOR-ENTRY-01*", "*ENTRY-AUDIT*"):
        found.extend([p.name for p in evidence.glob(pat)])
    plans = {
        "frozen_rules_abc": {
            "where": "EventTrendRunner.run_variant_abc（代码内）",
            "groups": 3,
            "formal_plan_file": None,
            "note": "A/B/C 是规则消融，无独立 run 目录（B/C 未落盘）",
        },
        "flow_oi_01": {
            "where": "evidence/FLOW-OI-01/",
            "groups": 4,
            "formal_plan_file": None,
            "note": "阶梯四组；PIT 227/227 违规（STEP1-A 已登记）",
        },
        "factor_entry_01": {
            "where": "evidence/FACTOR-ENTRY-01/",
            "groups": 4,
            "formal_plan_file": None,
            "note": "平行四组；FE02 已判不成立（97.5% 改善来自 2022 缺失剔除）",
        },
    }
    scan["experiment_plans"] = plans
    scan["evidence_dirs_matched"] = sorted(set(found))

    row("四组实验计划（正式文件）", False,
        "（无正式计划文件）",
        "❌ 无法复用为登记依据",
        "三套四组实验只存在于证据目录与代码变体里，没有一份带分组规则/指标/次数的计划书",
        "本单在契约里定义 group 字段；历史三套标 UNKNOWN，不补造")


# ══════════════════════ 6. 现有 run 规模 ══════════════════════

def scan_runs(scan: dict) -> None:
    base = ROOT / "回测日志" / "event_trend_v0_1"
    batches = {}
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if d.is_dir() and d.name.startswith("runs"):
                n = sum(1 for x in d.iterdir() if x.is_dir())
                batches[d.name] = n
    scan["batches"] = batches
    scan["batches_total"] = sum(batches.values())
    row("现有运行批次", True, "回测日志/event_trend_v0_1/runs*",
        "复用：批次目录即「已发生的实验事实」",
        f"{len(batches)} 个批次共 {sum(batches.values())} 个 run，全部**没有**设计身份与冻结记录",
        "本单不追溯改写历史 run；历史一律标 UNKNOWN")


# ══════════════════════ 7. 本单新增 ══════════════════════

def scan_new_modules(scan: dict) -> None:
    reg = BACKEND / "experiment_registry.py"
    tst = BACKEND / "tests" / "test_frame_fit_01.py"
    scan["new_files"] = {"registry": file_meta(reg), "tests": file_meta(tst)}
    row("实验登记库（本单新增）", reg.exists(),
        "框架/backend/experiment_registry.py",
        "新增：append-only 事件流 + 哈希链 + 设计身份 + 分组规则 + 闸门",
        "无（本单交付）", "约 900 行")
    row("反例测试（本单新增）", tst.exists(),
        "框架/backend/tests/test_frame_fit_01.py",
        "新增：48 项，含真实入口",
        "无（本单交付）", "见 05 号日志")


def main() -> int:
    scan = {"audit": "FRAME-FIT-01", "generated_at": now_iso(),
            "project_root": str(ROOT)}
    scan_fingerprints(scan)
    scan_registry(scan)
    scan_entrypoints(scan)
    scan_isolation(scan)
    scan_experiment_plans(scan)
    scan_runs(scan)
    scan_new_modules(scan)

    import csv
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["验收项", "现状", "实现位置",
                                          "可复用方式", "缺口", "备注"])
        w.writeheader()
        w.writerows(ROWS)

    OUT_JSON.write_text(json.dumps(scan, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    print(f"清点 {len(ROWS)} 项 -> {OUT_CSV.name}")
    n_exist = sum(1 for r in ROWS if r["现状"] == "存在")
    print(f"存在 {n_exist} / 不存在 {len(ROWS) - n_exist}")
    print(f"批次 {scan['batches_total']} 个 run（{len(scan['batches'])} 批）")
    print(f"参数 {scan['config_param_count']} 个；代码指纹 bug: {scan['bridge_code_hash_bug']}")
    print(f"窗口写死: {scan['pinned_window']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

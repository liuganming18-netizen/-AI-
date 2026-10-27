#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-FIT-02 探针：首个正式实验「修复前 / 修复后」对照。

**这不是回测**，不跑任何策略：全程只碰闸门与登记库。
只有最后一步 `execute_run` 需要执行体，用 `_run` 打桩替换掉昂贵策略计算
（工单允许：「可替换昂贵策略计算，但不能替换被测守卫和登记逻辑」）。

场景与 FRAME-VERIFY-03 的 VERIFY-A1/A2 完全一致：
  · 窗口对齐执行链写死窗口 `2024-01-01 ~ 2024-06-30` ⇒ 无 scope_mismatch；
  · 分组完整自洽（隔离带 240h）⇒ `SplitPolicy.validate()` 为空；
  · 设计身份取自系统当前指纹 ⇒ 与登记身份逐字符一致；
  · 实验已冻结，但 **runs = []、trials = []**（这就是「首个」）。

输出：`runs/probe_launch_before_after.json`
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "框架" / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "策略"))

# 不写生产任务队列、不碰生产运行目录（下面还会用临时目录覆盖）
os.environ["INPROCESS_JOBS"] = "1"

import experiment_registry as E            # noqa: E402
import backtest_api as A                   # noqa: E402
import backtest_runner as RUN              # noqa: E402
import backtest_store as S                 # noqa: E402

WINDOW = ("2024-01-01", "2024-06-30")      # 与执行链写死窗口一致
SYMBOLS = ["BTCUSDT"]
OUT = Path(__file__).resolve().parents[1] / "runs" / "probe_launch_before_after.json"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fit02_probe_"))
    reg_root = tmp / "registry"
    runs_root = tmp / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    os.environ[E.REGISTRY_ENV] = str(reg_root)
    os.environ["EVENT_TREND_RUNS_DIR"] = str(runs_root)
    reg = E.ExperimentRegistry(reg_root)

    scope = {"symbols": SYMBOLS, "window_start": WINDOW[0],
             "window_end": WINDOW[1], "source": "request"}
    policy = E.SplitPolicy(
        train=("2024-01-01", "2024-03-31"),
        valid=("2024-04-15", "2024-06-30"),      # 15 天 = 360h ≥ 240h
        holdout=("2026-10-25", "2026-11-25"),    # 远离已观察样本
        isolation_hours=240,
        label_span_source="实测 max(exit-entry)=240h（205 笔成交）",
        note="FRAME-FIT-02 探针用划分",
    )
    assert policy.validate() == [], policy.validate()
    parts = E.current_design_parts(sample_scope=scope, split_policy=policy,
                                   metrics_spec=E.DEFAULT_METRICS_SPEC)
    reg.register(design=parts, sample_scope=scope, split_policy=policy,
                 metrics_spec=E.DEFAULT_METRICS_SPEC, experiment_id="exp_probe_first",
                 mode=E.MODE_FORMAL, by="probe:FRAME-FIT-02",
                 note="首个正式实验探针")
    reg.freeze("exp_probe_first")
    exp = reg.state()["experiments"]["exp_probe_first"]
    assert exp["runs"] == [] and exp["trials"] == [], "探针前提：零历史记录"

    payload = {"symbols": SYMBOLS, "window_start": WINDOW[0], "window_end": WINDOW[1],
               "experiment": {"mode": "formal", "experiment_id": "exp_probe_first"}}

    result: dict = {"scenario": {
        "window": list(WINDOW), "symbols": SYMBOLS,
        "split_ok": True, "frozen": bool(exp["frozen_at"]),
        "runs": len(exp["runs"]), "trials": len(exp["trials"]),
        "design_identity": exp["design_identity"],
    }}

    # ── ① 旧逻辑等价复现：旧 guard_create 的正式分支就是直接调用交付资格判定 ──
    ok_old, why_old = reg.formal_eligible("exp_probe_first")
    result["before"] = {
        "how": "旧实现 guard_create 正式分支 = reg.formal_eligible(eid)（同一句，未改状态）",
        "ok": bool(ok_old),
        "blockers": why_old,
    }

    # ── ② 新逻辑：启动许可 ──
    gate = E.guard_create(payload, registry=reg)
    result["after_guard"] = {
        "ok": bool(gate["ok"]), "stage": gate.get("stage"),
        "blockers": gate.get("blockers"),
        "delivery_eligible": gate.get("delivery_eligible"),
        "delivery_blockers": gate.get("delivery_blockers"),
    }

    # ── ③ 真实 API 控制器：受理并落 run 文档 ──
    with mock.patch.object(A, "_run_in_thread", lambda rid: None):
        resp, code = A.create_run(payload)
    result["after_api_create_run"] = {
        "http": code, "run_id": resp.get("run_id"),
        "experiment_id": (resp.get("experiment") or {}).get("experiment_id"),
        "errors": resp.get("errors") or [],
    }

    # ── ④ 真实执行入口：只替换昂贵策略计算 ──
    run_id = resp.get("run_id")
    if code == 202 and run_id:
        with mock.patch.object(RUN, "_run",
                               mock.Mock(return_value={"metrics": {"net_pnl": 12.5,
                                                                   "trades_count": 3}})):
            doc = RUN.execute_run(run_id)
        exp_after = reg.state()["experiments"]["exp_probe_first"]
        ok_deliver, why_deliver = reg.formal_eligible("exp_probe_first")
        result["after_execute"] = {
            "status": doc.get("status"),
            "failure_reason": doc.get("failure_reason"),
            "experiment_record": doc.get("experiment_record"),
            "runs": len(exp_after["runs"]),
            "trials": len(exp_after["trials"]),
            "trial_outcome": (exp_after["trials"][0]["outcome"]
                              if exp_after["trials"] else None),
            "delivery_eligible": bool(ok_deliver),
            "delivery_blockers": why_deliver,
        }
    else:
        result["after_execute"] = {"status": None, "reason": "创建未成功，未进入执行"}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # ── 判定 ──
    checks = {
        "修复前：被交付资格卡住": (result["before"]["ok"] is False
                                   and any("尚无已登记运行" in b
                                           for b in result["before"]["blockers"])),
        "修复后：启动许可通过": result["after_guard"]["ok"] is True,
        "修复后：启动时如实标注不可交付":
            result["after_guard"]["delivery_eligible"] is False,
        "修复后：真实 API 受理(202)": result["after_api_create_run"]["http"] == 202,
        "修复后：执行成功": result["after_execute"].get("status") == "succeeded",
        "修复后：run 与 trial 各一条": (result["after_execute"].get("runs") == 1
                                        and result["after_execute"].get("trials") == 1),
        "修复后：跑完才可交付": result["after_execute"].get("delivery_eligible") is True,
    }
    print("\n== 判定 ==")
    for k, v in checks.items():
        print(("PASS  " if v else "FAIL  ") + k)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

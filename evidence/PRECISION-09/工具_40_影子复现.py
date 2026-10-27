#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-09 工具 40：影子复现 —— 证明新增用例真的能抓住这个缺陷。

做法
────
不修改任何文件，只在**进程内**把 `engine.mark_if_execution_invalid` 换成空实现，
让「精度拒绝 ⇒ 写 invalid_reasons」这条链路失效，然后跑一遍新增用例：

  A 组（正常）：9 项应全绿 —— 证明修复后行为正确。
  B 组（影子）：禁用唯一写入路径 —— 依赖「判无效」的用例必须转红。

如果 B 组也全绿，说明这些用例是「假验收」（写了但抓不住缺陷）。

注意：影子比「修复前」更严格 —— 修复前出场三处是**直接** append 的，影子把出场
也一起禁掉了。所以 B 组只可能更红、不可能更绿；这里如实标注每条的实际结果。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (ROOT, os.path.join(ROOT, "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest                                                        # noqa: E402

from WyckoffMaster.trading_core import engine as engine_mod          # noqa: E402

TARGET = os.path.join(ROOT, "策略", "WyckoffMaster", "event_trend_v0_1",
                      "tests", "test_fill_precision.py")

NEW_TESTS = [
    "test_entry_notional_reject_marks_run_invalid",
    "test_entry_qty_reject_marks_run_invalid",
    "test_entry_price_reject_marks_run_invalid",
    "test_strategy_cancel_does_not_invalidate_run",
    "test_every_precision_gate_reason_is_registered",
    "test_all_fill_rejected_literals_are_registered",
    "test_strategy_reasons_are_never_classified_as_execution_errors",
    "test_mark_helper_never_writes_strategy_or_unknown_reasons",
    "test_every_precision_reject_carries_class_and_is_traceable",
    "test_stage_prefixes_are_byte_identical_to_pre_fix_literals",
]

# 期望：禁用写入路径后，哪些用例必须转红（否则就是假验收）
MUST_FAIL_UNDER_SHADOW = {
    "test_entry_notional_reject_marks_run_invalid",
    "test_entry_qty_reject_marks_run_invalid",
    "test_entry_price_reject_marks_run_invalid",
    "test_every_precision_reject_carries_class_and_is_traceable",
}


class _Collect:
    def __init__(self):
        self.outcome = {}

    def pytest_runtest_logreport(self, report):
        name = report.nodeid.split("::")[-1]
        if report.when == "call":
            self.outcome[name] = report.outcome
        elif report.when == "setup" and report.outcome in ("failed", "skipped"):
            self.outcome.setdefault(name, report.outcome)


class _DisableWritePath:
    """把唯一写入路径换成空实现（不碰磁盘、不碰测试代码）。"""

    def __init__(self):
        self._orig = None

    def pytest_configure(self, config):
        self._orig = engine_mod.mark_if_execution_invalid
        engine_mod.mark_if_execution_invalid = lambda *a, **k: False

    def pytest_unconfigure(self, config):
        if self._orig is not None:
            engine_mod.mark_if_execution_invalid = self._orig


def _run(plugins, tag: str) -> dict:
    col = _Collect()
    pytest.main([TARGET, "-q", "--no-header", "-p", "no:cacheprovider",
                 f"--basetemp=/tmp/p09_shadow_{tag}"],
                plugins=[col] + plugins)
    return col.outcome


def main() -> int:
    normal = _run([], "normal")
    shadow = _run([_DisableWritePath()], "shadow")

    lines = ["PRECISION-09 影子复现：新增用例是否真的抓得住缺陷", "=" * 78,
             f"{'用例':<62}{'正常':<8}{'影子':<8}"]
    bad = []
    for t in NEW_TESTS:
        a = normal.get(t, "missing")
        b = shadow.get(t, "missing")
        lines.append(f"{t:<62}{a:<8}{b:<8}")
        if a != "passed":
            bad.append(f"{t} 正常情况下没有通过（{a}）")
        if t in MUST_FAIL_UNDER_SHADOW and b != "failed":
            bad.append(f"{t} 影子下没有转红（{b}）⇒ 假验收")
        if t not in MUST_FAIL_UNDER_SHADOW and b == "failed" and t.startswith("test_strategy"):
            bad.append(f"{t} 影子下不该红（{b}）")

    lines.append("")
    lines.append("说明：影子只禁「写 invalid_reasons」这一条链路，不改撮合/风控/账本。")
    lines.append("判定：" + ("符合预期" if not bad else f"{len(bad)} 处异常"))
    for x in bad:
        lines.append("  ✗ " + x)
    txt = "\n".join(lines)
    with open(os.path.join(HERE, "影子复现_新增用例.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())

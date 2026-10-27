#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""研究留痕与留出段守卫的测试。

这组测试盯的是两个**已经发生过**的事故，不是假想的边界：

  A. 2,125 个回测产物目录、登记库里 0 条业务记录 —— 因为研究模式只登记不冻结，
     执行闸门据此拒绝，`record_execution` 也跟着拒绝留痕。
  B. 没有任何机制阻止「拿留出段跑回测」，也就是「AI 会偷看未来数据」。

对应断言：

  H01 未定义留出段 ⇒ 一律放行（上线本模块不得打断在跑的批量脚本）。
  H02 定义了留出段，窗口不重叠 ⇒ 放行。
  H03 窗口重叠且标的有交集 ⇒ 拒绝，并给出重叠区间。
  H04 时间重叠但标的不相交 ⇒ 放行（不冤枉）。
  H05 留出段写坏（日期解析不出 / 起止倒置）⇒ 按拒绝处理，不能当放行。
  H06 `_freeze_design` 让研究实验变成「已冻结」，重复调用安静跳过。
  H07 冻结之后 `record_execution` 真的写下 run + trial（这是 A 的正面验证）。
  H08 未冻结时 `record_execution` 仍然拒绝（不能靠改本模块绕过闸门）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FRAMEWORK = os.path.dirname(BACKEND)
for _p in (BACKEND, FRAMEWORK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import experiment_registry as E                       # noqa: E402
from infrastructure.guard import holdout as H         # noqa: E402

WINDOW = ("2024-01-01", "2024-06-30")
SYMBOLS = ["BTCUSDT"]


class HoldoutGuardTest(unittest.TestCase):
    """H01–H05：留出段守卫的四种状态。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="holdout_"))
        self.file = self.tmp / "holdout.json"
        self._env = mock.patch.dict(os.environ, {H.HOLDOUT_ENV: str(self.file)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        for p in self.tmp.rglob("*"):
            if p.is_file():
                p.unlink()
        self.tmp.rmdir()

    def _write(self, obj):
        self.file.write_text(__import__("json").dumps(obj), encoding="utf-8")

    def test_h01_no_holdout_passes_everything(self):
        self.assertFalse(self.file.exists())
        r = H.check(SYMBOLS, *WINDOW)
        self.assertEqual(r["status"], H.STATUS_NO_HOLDOUT)
        self.assertFalse(H.describe()["defined"])

    def test_h02_clear_when_window_before_holdout(self):
        self._write({"start": "2025-01-01", "end": "2026-01-01"})
        r = H.check(SYMBOLS, *WINDOW)
        self.assertEqual(r["status"], H.STATUS_CLEAR)

    def test_h03_overlap_detected_with_symbols(self):
        self._write({"start": "2024-03-01", "end": "2025-01-01", "symbols": ["*"]})
        r = H.check(SYMBOLS, *WINDOW)
        self.assertEqual(r["status"], H.STATUS_OVERLAP)
        self.assertEqual(r["symbols"], ["BTCUSDT"])
        self.assertIsNotNone(r["overlap_start"])

    def test_h04_time_overlap_but_disjoint_symbols_is_clear(self):
        self._write({"start": "2024-03-01", "end": "2025-01-01", "symbols": ["ETHUSDT"]})
        r = H.check(SYMBOLS, *WINDOW)
        self.assertEqual(r["status"], H.STATUS_CLEAR)

    def test_h05_broken_holdout_is_not_a_pass(self):
        self._write({"start": "不是日期", "end": "2025-01-01"})
        r = H.check(SYMBOLS, *WINDOW)
        self.assertEqual(r["status"], H.STATUS_INVALID)

    def test_h05b_reversed_range_is_invalid(self):
        self._write({"start": "2026-01-01", "end": "2025-01-01"})
        r = H.check(SYMBOLS, *WINDOW)
        self.assertEqual(r["status"], H.STATUS_INVALID)

    def test_h05c_window_exactly_touching_holdout_start_overlaps(self):
        # 运行 [2024-01-01, 2024-06-30) 与留出 [2024-06-30, ...] 相接
        # 半开口径下 we > hs 才重叠；这里 we == hs ⇒ 不重叠（边界清晰）
        self._write({"start": "2024-06-30", "end": "2025-01-01"})
        r = H.check(SYMBOLS, *WINDOW)
        self.assertEqual(r["status"], H.STATUS_CLEAR)


class FreezeAndRecordTest(unittest.TestCase):
    """H06–H08：冻结之后留痕才成立。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ledger_"))
        self.reg_root = self.tmp / "registry"
        self.reg = E.ExperimentRegistry(self.reg_root)
        self._env = mock.patch.dict(os.environ, {
            E.REGISTRY_ENV: str(self.reg_root),
            H.HOLDOUT_ENV: str(self.tmp / "holdout.json"),
        })
        self._env.start()

    def tearDown(self):
        self._env.stop()
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                p.rmdir()
        if self.tmp.exists():
            self.tmp.rmdir()

    def _spec(self):
        return {"symbols": list(SYMBOLS),
                "window_start": WINDOW[0], "window_end": WINDOW[1],
                "experiment": {}}

    def test_h06_freeze_makes_research_experiment_frozen(self):
        from backtest_api import _freeze_design
        spec = self._spec()
        gate = E.guard_create(spec, registry=self.reg)
        self.assertTrue(gate["ok"])
        eid = gate["experiment_id"]
        self.assertFalse(self.reg.state()["experiments"][eid]["frozen_at"])

        self.assertIsNone(_freeze_design(eid))
        self.assertTrue(self.reg.state()["experiments"][eid]["frozen_at"])

        # 重复调用必须安静跳过，不能把重复提交变成失败
        self.assertIsNone(_freeze_design(eid))

    def test_h07_frozen_experiment_actually_records_run_and_trial(self):
        from backtest_api import _freeze_design
        spec = self._spec()
        gate = E.guard_create(spec, registry=self.reg)
        eid = gate["experiment_id"]
        _freeze_design(eid)
        spec["experiment"]["experiment_id"] = eid
        doc = {"run_id": "bt_t1", "spec": spec}

        err = E.record_execution("bt_t1", doc, outcome="success",
                                 metrics=None, registry=self.reg)
        self.assertIsNone(err, f"应当写入成功，实际：{err}")

        exp = self.reg.state()["experiments"][eid]
        self.assertEqual([r["run_id"] for r in exp["runs"]], ["bt_t1"])
        self.assertEqual([t["outcome"] for t in exp["trials"]], ["success"])
        # 幂等：再记一次不应产生第二条
        self.assertIsNone(E.record_execution("bt_t1", doc, outcome="success",
                                             registry=self.reg))
        exp = self.reg.state()["experiments"][eid]
        self.assertEqual(len(exp["trials"]), 1)

    def test_h08_unfrozen_still_refused(self):
        spec = self._spec()
        gate = E.guard_create(spec, registry=self.reg)
        eid = gate["experiment_id"]
        spec["experiment"]["experiment_id"] = eid
        doc = {"run_id": "bt_t2", "spec": spec}
        err = E.record_execution("bt_t2", doc, outcome="success", registry=self.reg)
        self.assertIsNotNone(err)
        self.assertIn("未冻结", err)
        exp = self.reg.state()["experiments"][eid]
        self.assertEqual(len(exp["trials"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

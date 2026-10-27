#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BT-UI-01 回归：回测页面读取通道的**同源与不造数**纪律（只读、零网络）。

每条都对应一个页面上真实出现过的问题：

  1. **批内 run 必须能被单 run 接口读到**。
     批次目录（`回测日志/event_trend_v0_1/runs_sep_all_latest/` 等）与默认 `runs/`
     平级；`run_dir()` 只认默认根 ⇒ 页面从批次选中一个 run 后，
     `GET /backtests/{id}` 及其图表/逐笔全 404（表现为「图表整块消失」）。
     现在读路径走 `read_run_dir()`（默认根 → 同级批次目录），**写入仍只认默认根**。

  2. **`trade_count == 0` 不能变成「缺失」**。
     旧写法 `sa.get("trade_count") or sa.get("trades_count")` 把真实值 0 当假值吃掉，
     528 个 run 里 516 个「确实 0 笔」在页面上显示成「笔数 —」，看起来像字段缺失。

  3. **缺 `chart.json` 时不许用写死窗口重建图表**。
     旧实现写死 `2024-01-01 ~ 2024-06-30`，实测 4 个 run 的真实窗口
     （2024-09→2025-09 / 2021-06→2022-06 / 2022-10→2023-10 / 2023-01→2024-01）
     与该窗口**完全不相交** ⇒ 图上的 K 线不是这笔交易发生的那一段。
     现在窗口取该 run 自己的 `inputs.json(data_summary.time_range_1h)`。

  4. **读接口不得写 run 目录**。旧重建路径会 `write_chart()` 把结果写回去，
     等于用一份错窗口的图覆盖证据目录。

  5. **批次汇总与逐笔必须自洽**（`trades_total` == 逐笔行数、净盈亏两侧相等）。
"""
import json
import os
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FRAMEWORK = os.path.dirname(BACKEND)
for _p in (BACKEND, FRAMEWORK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_api as A          # noqa: E402
import backtest_batches as B      # noqa: E402
import backtest_store as S        # noqa: E402

ROOT = Path(BACKEND).parents[1] / "回测日志" / "event_trend_v0_1"
DEFAULT_ROOT = S.get_runs_root()
BATCH_ID = "runs_sep_all_latest"
BATCH_DIR = ROOT / BATCH_ID


def _first_run(d: Path):
    if not d.is_dir():
        return None
    for x in sorted(d.iterdir()):
        if x.is_dir() and x.name.startswith("run_"):
            return x
    return None


@unittest.skipUnless(DEFAULT_ROOT.is_dir(), "默认运行根不存在（离线环境）")
class TestReadPathAcrossRoots(unittest.TestCase):
    """① 批内 run 可被单 run 接口读到；写入边界不变。"""

    def test_default_root_run_resolves(self):
        d = _first_run(DEFAULT_ROOT)
        if d is None:
            self.skipTest("默认根下没有 run")
        self.assertEqual(S.read_run_dir(d.name), S.run_dir(d.name))
        self.assertEqual(S.read_run(d.name).get("run_id"), d.name)

    @unittest.skipUnless(BATCH_DIR.is_dir(), "批次目录不存在")
    def test_batch_run_resolves_for_read(self):
        d = _first_run(BATCH_DIR)
        if d is None:
            self.skipTest("批次目录下没有 run")
        # 默认根里没有它 ⇒ 旧实现会抛 RunNotFound（页面 404）
        self.assertFalse((DEFAULT_ROOT / d.name).exists())
        got = S.read_run_dir(d.name)
        self.assertEqual(got.name, d.name)
        self.assertEqual(got.parent.name, BATCH_ID)
        # 读得到 run.json / summary.json 的合并文档
        doc = S.read_run(d.name)
        self.assertEqual(doc.get("run_id"), d.name)
        self.assertIn("status", doc)

    def test_unknown_run_id_still_raises(self):
        with self.assertRaises(S.RunNotFound):
            S.read_run_dir("run_WX_EVENT_TREND_V0_1_NOT_A_REAL_RUN_1")

    def test_write_boundary_unchanged(self):
        """写入路径**不放宽**：批次目录里的 run 不能被 `write_run` 命中。"""
        d = _first_run(BATCH_DIR) if BATCH_DIR.is_dir() else None
        if d is None:
            self.skipTest("批次目录下没有 run")
        with self.assertRaises(Exception):
            S.write_run(d.name, {"run_id": d.name, "status": "succeeded"})
        # 目录里不应因此多出/改动 run.json 的写入痕迹（只读校验）
        self.assertFalse((BATCH_DIR / d.name / "run.json.tmp").exists())


@unittest.skipUnless(BATCH_DIR.is_dir(), "批次目录不存在")
class TestBatchFactsHonesty(unittest.TestCase):
    """② `trade_count == 0` 如实是 0；③ 汇总与逐笔自洽。"""

    def test_zero_trade_count_is_zero_not_none(self):
        facts = B._batch_facts(BATCH_ID, full=True)
        rows = facts.get("runs_rows") or []
        self.assertTrue(rows, "批次内应有 run")
        zeros = [r for r in rows if r["trades_count"] == 0]
        nones = [r for r in rows if r["trades_count"] is None]
        self.assertTrue(zeros, "本批次应存在「确实 0 笔」的 run")
        # 0 与「缺失」必须分开：缺失只可能来自真的读不到 summary
        self.assertEqual(
            len(zeros) + len(nones) + len([r for r in rows if (r["trades_count"] or 0) > 0]),
            len(rows),
        )

    def test_summary_matches_trades_rows(self):
        body, code = B.batch_summary(BATCH_ID)
        self.assertEqual(code, 200)
        checks = body.get("checks") or {}
        self.assertTrue(checks.get("trades_total_matches_rows"),
                        f"汇总笔数与逐笔行数不一致：{checks}")
        self.assertTrue(checks.get("net_pnl_matches_rows"),
                        f"汇总净盈亏与逐笔合计不一致：{checks}")
        # 逐笔行数 == 汇总笔数（两侧都是整数，不接受近似）
        self.assertEqual(body["trades"]["count"], checks.get("trades_rows"))

    def test_every_trade_has_trade_id_and_signal_time(self):
        body, code = B.batch_trades(BATCH_ID, limit=100000)
        self.assertEqual(code, 200)
        rows = body.get("items") or []
        self.assertTrue(rows, "本批次应有成交")
        for r in rows:
            self.assertTrue(str(r["trade_id"] or "").startswith("trd_"),
                            f"trade_id 必须是账本 trd_ 号：{r}")
            self.assertIsNotNone(r["signal_time_ms"], f"缺信号时刻：{r['run_id']}")
            self.assertIsNotNone(r["initial_stop"], f"缺初始止损：{r['run_id']}")


@unittest.skipUnless(DEFAULT_ROOT.is_dir(), "默认运行根不存在")
class TestRebuiltChartUsesOwnWindow(unittest.TestCase):
    """③④ 缺 chart.json 时按本 run 自己的窗口重建，且不写盘。"""

    def _pick_run_without_chart(self):
        for d in sorted(DEFAULT_ROOT.iterdir()):
            if not d.is_dir() or not d.name.startswith("run_"):
                continue
            if (d / "chart.json").exists():
                continue
            if not (d / "inputs.json").is_file():
                continue
            try:
                inp = json.loads((d / "inputs.json").read_text(encoding="utf-8"))
            except Exception:                              # noqa: BLE001
                continue
            tr = ((inp.get("data_summary") or {}).get("time_range_1h") or {})
            if isinstance(tr.get("start_ms"), int) and isinstance(tr.get("end_ms"), int):
                return d, tr["start_ms"], tr["end_ms"]
        return None, None, None

    def test_window_equals_run_own_inputs_and_no_write(self):
        d, ws, we = self._pick_run_without_chart()
        if d is None:
            self.skipTest("没有「缺 chart.json 但有 inputs.json 窗口」的 run")
        chart_file = d / "chart.json"
        self.assertFalse(chart_file.exists())

        body, code = A.get_chart(d.name)
        self.assertEqual(code, 200)
        chart = body.get("chart")
        self.assertIsNotNone(chart, f"应能按本 run 窗口重建：{body.get('missing_reason')}")
        self.assertEqual(int(chart["window"]["start_ms"]), int(ws))
        self.assertEqual(int(chart["window"]["end_ms"]), int(we))
        self.assertIn("inputs.json", str(chart["window"].get("source")))
        # 关键：不许把重建结果写回 run 目录
        self.assertFalse(chart_file.exists(), "读接口不得在 run 目录里生成 chart.json")
        self.assertTrue(chart.get("derived_from"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

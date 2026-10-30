# -*- coding: utf-8 -*-
"""BACKTEST-REPAIR-01 · 任务 C 回归测试：绩效报告补全。

判据（工单第四节）：
  · 从**账本与权益曲线**推导，不从信号数推成交量；
  · **不重复扣费**（滑点已含在成交价里，不得再减一次）；
  · **零分母明确处理**（返回 None，不填 0、不输出 inf）；
  · 保持资金与权益恒等式；
  · **不破坏旧版消费者**（既有 13 个 summary 字段数值一字不变）。

  M01 全盈利：PF / 平均盈亏比无定义 ⇒ None（不是 0，不是 inf）
  M02 全亏损：PF = 0.0（分子为 0 有定义）、胜率 0
  M03 零成交：全部指标 None + 说明
  M04 权益曲线不足 2 点：最大回撤 None（不是 0%）
  M05 成本恒等式：net == gross - fees - funding，且滑点不参与扣减
  M06 平盘计入分母：wins+losses+flats == trade_count，胜率分母含平盘
  M07 旧字段不被覆盖：`_merge_summary` 只加不改
  M08 `backtest_runner` 的 metrics 映射能读到新字段（含兼容旧名）
  M09 脏值（None / NaN / inf）不进合计

运行：
  /usr/bin/python3 -m unittest 策略.WyckoffMaster.event_trend_v0_1.tests.test_backtest_repair_01_metrics -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT / "策略"))
sys.path.insert(0, str(PROJECT_ROOT / "框架" / "backend"))

from WyckoffMaster.event_trend_v0_1.engine_adapter import (      # noqa: E402
    _merge_summary, compute_summary_metrics)


def _trade(net, gross=None, entry_fee=0.0, exit_fee=0.0, funding=0.0, slip=0.0):
    """造一笔成交行；不给 gross 就按恒等式反推（gross = net + fees + funding）。"""
    fees = entry_fee + exit_fee
    if gross is None:
        gross = net + fees + funding
    return {"net_pnl": net, "gross_pnl": gross, "entry_fee": entry_fee,
            "exit_fee": exit_fee, "fees": fees, "funding_fee": funding,
            "slippage_loss": slip}


def _eq(values):
    return [{"total_equity": v} for v in values]


class TestMetrics(unittest.TestCase):

    # ── M01 全盈利 ──
    def test_m01_all_wins(self):
        m = compute_summary_metrics([_trade(10.0), _trade(30.0)],
                                    _eq([1000.0, 1010.0, 1040.0]), 1000.0, 1040.0)
        self.assertEqual(m["wins"], 2)
        self.assertEqual(m["losses"], 0)
        self.assertEqual(m["flats"], 0)
        self.assertEqual(m["win_rate_pct"], 100.0)
        self.assertIsNone(m["profit_factor"], "无亏损 ⇒ PF 分母 0，必须 None")
        self.assertIsNone(m["avg_win_loss_ratio"], "无亏损 ⇒ 平均盈亏比必须 None")
        self.assertEqual(m["avg_win"], 20.0)
        self.assertIsNone(m["avg_loss"])
        self.assertTrue(any("PF" in n for n in m["metrics_notes"]))

    # ── M02 全亏损 ──
    def test_m02_all_losses(self):
        m = compute_summary_metrics([_trade(-10.0), _trade(-30.0)],
                                    _eq([1000.0, 990.0, 960.0]), 1000.0, 960.0)
        self.assertEqual(m["wins"], 0)
        self.assertEqual(m["losses"], 2)
        self.assertEqual(m["win_rate_pct"], 0.0)
        self.assertEqual(m["profit_factor"], 0.0, "有亏损、无盈利 ⇒ PF = 0（有定义）")
        self.assertIsNone(m["avg_win"])
        self.assertEqual(m["avg_loss"], -20.0)
        self.assertIsNone(m["avg_win_loss_ratio"])

    # ── M03 零成交 ──
    def test_m03_zero_trades(self):
        m = compute_summary_metrics([], _eq([1000.0, 1000.0]), 1000.0, 1000.0)
        self.assertEqual(m["trade_count"], 0)
        for k in ("win_rate_pct", "profit_factor", "avg_win_loss_ratio",
                  "avg_win", "avg_loss", "gross_pnl", "net_pnl"):
            self.assertIsNone(m[k], f"{k} 应为 None（无证据），实际 {m[k]!r}")
        self.assertTrue(any("无已平仓成交" in n for n in m["metrics_notes"]))

    # ── M04 权益曲线不足 2 点 ──
    def test_m04_mdd_none_when_curve_too_short(self):
        m = compute_summary_metrics([_trade(10.0)], _eq([1010.0]), 1000.0, 1010.0)
        self.assertIsNone(m["max_drawdown_pct"], "一个点看不出回撤，必须 None（不是 0）")
        self.assertEqual(m["equity_curve_points"], 1)
        m2 = compute_summary_metrics([_trade(10.0)], [], 1000.0, 1010.0)
        self.assertIsNone(m2["max_drawdown_pct"])
        # 空曲线时回落到引擎入参，但必须**显式标注**，不能冒充曲线实测值
        self.assertEqual(m2["period_start_equity"], 1000.0)
        self.assertEqual(m2["period_end_equity"], 1010.0)
        self.assertTrue(any("回落到引擎入参" in n for n in m2["metrics_notes"]))

    def test_m04b_mdd_peak_from_start(self):
        """回撤峰从**初始权益**起算：先跌后涨，回撤要算进去。"""
        m = compute_summary_metrics([], _eq([1000.0, 900.0, 1100.0]), 1000.0, 1100.0)
        self.assertAlmostEqual(m["max_drawdown_pct"], 10.0, places=9)

    # ── M05 成本恒等式（不重复扣费）──
    def test_m05_cost_identity_and_no_double_slippage(self):
        """滑点已含在成交价里 ⇒ `gross_pnl` 已经是滑点后口径，不得再减一次。"""
        t = _trade(net=95.0, gross=100.0, entry_fee=2.0, exit_fee=2.0,
                   funding=1.0, slip=0.4)
        m = compute_summary_metrics([t], _eq([1000.0, 1095.0]), 1000.0, 1095.0)
        self.assertTrue(m["cost_identity_ok"])
        self.assertLess(abs(m["cost_identity_delta"]), 1e-9)
        self.assertAlmostEqual(m["gross_pnl"], 100.0)
        self.assertAlmostEqual(m["fee_total"], 4.0)
        self.assertAlmostEqual(m["funding_total"], 1.0)
        self.assertAlmostEqual(m["net_pnl"], 95.0)
        # 若把滑点再减一次，net 会变成 94.6 ⇒ 恒等式立刻不成立
        self.assertNotAlmostEqual(m["net_pnl"], 100.0 - 4.0 - 1.0 - 0.4)

    def test_m05b_identity_broken_is_reported(self):
        """恒等式不成立时必须显式记下来，不能悄悄放过。"""
        bad = {"net_pnl": 90.0, "gross_pnl": 100.0, "entry_fee": 1.0,
               "exit_fee": 1.0, "fees": 2.0, "funding_fee": 0.0,
               "slippage_loss": 0.0}
        m = compute_summary_metrics([bad], _eq([1000.0, 1090.0]), 1000.0, 1090.0)
        self.assertFalse(m["cost_identity_ok"])
        self.assertTrue(any("恒等式" in n for n in m["metrics_notes"]))

    # ── M06 平盘计入分母 ──
    def test_m06_flats_in_denominator(self):
        m = compute_summary_metrics([_trade(10.0), _trade(0.0), _trade(-10.0)],
                                    _eq([1000.0, 1000.0]), 1000.0, 1000.0)
        self.assertEqual((m["wins"], m["losses"], m["flats"]), (1, 1, 1))
        self.assertEqual(m["trade_count"], 3)
        self.assertAlmostEqual(m["win_rate_pct"], 100.0 / 3, places=9)
        self.assertEqual(m["wins"] + m["losses"] + m["flats"], m["trade_count"])
        self.assertAlmostEqual(m["profit_factor"], 1.0, places=9)

    # ── M07 旧字段不被覆盖 ──
    def test_m07_legacy_fields_untouched(self):
        base = {"initial_equity": 2000.0, "final_equity": 2100.0, "net_pnl": 99.0,
                "trade_count": 7, "order_count": 9, "fee_total": 1.23,
                "slippage_total": 0.5, "slippage_pure_total": 0.3,
                "rounding_total": 0.2, "slippage_split_ok": True,
                "funding_total": 0.1, "spec_status": "spec_applied",
                "formal_pass_allowed": False}
        merged = _merge_summary(base, [_trade(10.0)], _eq([2000.0, 2010.0]),
                                2000.0, 2010.0)
        for k, v in base.items():
            self.assertEqual(merged[k], v, f"旧字段 {k} 被改写了：{merged[k]} != {v}")
        # 新字段确实加上了
        for k in ("wins", "losses", "win_rate_pct", "gross_pnl",
                  "max_drawdown_pct", "profit_factor", "avg_win_loss_ratio",
                  "period_start_equity", "period_end_equity", "cost_identity_ok"):
            self.assertIn(k, merged)
        self.assertEqual(len(merged), len(base) + len(
            [k for k in merged if k not in base]))

    # ── M08 runner 侧映射能读到新字段 ──
    def test_m08_runner_metrics_mapping(self):
        """`backtest_runner._run_event_trend` 的映射必须认新字段名，且兼容旧名。"""
        import backtest_runner as BTR
        src = Path(BTR.__file__).read_text(encoding="utf-8")
        for key in ('summary_data.get("wins")', 'summary_data.get("losses")',
                    'summary_data.get("win_rate_pct")',
                    'summary_data.get("max_drawdown_pct")',
                    'summary_data.get("profit_factor")',
                    'summary_data.get("avg_win_loss_ratio")',
                    'summary_data.get("gross_pnl")',
                    'summary_data.get("fee_total")',
                    'summary_data.get("funding_total")',
                    'summary_data.get("period_start_equity")',
                    'summary_data.get("period_end_equity")'):
            self.assertIn(key, src, f"runner 映射缺 {key}")
        # 旧名兼容必须还在（不能一刀切掉老 run 的读取）
        self.assertIn('summary_data.get("win_count")', src)
        self.assertIn('summary_data.get("loss_count")', src)
        # 严禁 `or 0` 把缺失伪装成 0
        self.assertNotIn('summary_data.get("wins") or 0', src)

    # ── M09 脏值不进合计 ──
    def test_m09_dirty_values_excluded(self):
        rows = [_trade(10.0),
                {"net_pnl": None, "gross_pnl": 1.0, "fees": 0.0, "funding_fee": 0.0},
                {"net_pnl": float("nan"), "gross_pnl": 1.0, "fees": 0.0,
                 "funding_fee": 0.0},
                {"net_pnl": float("inf"), "gross_pnl": 1.0, "fees": 0.0,
                 "funding_fee": 0.0}]
        m = compute_summary_metrics(rows, _eq([1000.0, 1010.0]), 1000.0, 1010.0)
        self.assertEqual(m["trade_count"], 1, "只有 1 笔有有效 net_pnl")
        self.assertEqual(m["wins"], 1)
        self.assertTrue(any("缺 net_pnl" in n for n in m["metrics_notes"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)

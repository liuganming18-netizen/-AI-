# -*- coding: utf-8 -*-
"""工单 GM-BACKTEST-OI-BUCKET-CAUSAL-FIX-38 隔离专项测试集

测试目标：
1. 5m OI 必须先闭合再被决策使用，要求 bucket_end <= decision_time；
2. 边界时刻验证：恰好闭合（bucket_end == decision_time）可用；刚开始（bucket_end > decision_time）不可用；
3. 真实 available_at 缺失或晚于决策时间时，必须保留 PIT_UNPROVEN，不得凭 bucket_end 升级为可信；
4. 回填数据（archive / batch / gapfill）无法证明历史物理到达，必须保留 PIT_UNPROVEN；
5. 无匹配闭合桶与空数据集场景：保持 NaN 且标记 PIT_UNPROVEN；
6. 具备真实合法 available_at 且不晚于决策的样本：允许认定为 PIT_PROVEN。
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock
import numpy as np
import pandas as pd

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import backtest_runner as RUN


class MockRepo:
    """可控的 Mock 数据仓库，注入指定的 signal, exec, oi, funding 行。"""
    def __init__(self, sig_rows=None, exec_rows=None, oi_rows=None, fu_rows=None):
        self._sig = sig_rows or []
        self._exec = exec_rows or []
        self._oi = oi_rows or []
        self._fu = fu_rows or []

    def candles(self, exchange, market, symbol, timeframe, start=None, end=None, limit=20000):
        if timeframe == "1h":
            return self._sig
        elif timeframe == "15m":
            return self._exec
        return []

    def oi(self, exchange, market, symbol, period="5m", start=None, end=None, limit=20000):
        return self._oi

    def funding(self, exchange, market, symbol, start=None, end=None, limit=2000, prefer="hot"):
        return self._fu


def make_standard_spec_and_win(win_start=1704067200000, win_days=2, warmup_hours=168):
    """构造标准回测 window 与 spec。"""
    hour_ms = 3600_000
    win_end = win_start + win_days * 86_400_000
    sig_start = win_start - warmup_hours * hour_ms
    
    spec = {
        "symbols": ["BTCUSDT"],
        "strategy": {"id": "WX_EVENT_TREND_V0_1"},
        "window": {
            "mode": "explicit",
            "start_ms": win_start,
            "end_ms": win_end,
            "warmup_bars_requested": warmup_hours,
        },
        "signal_params": {},
        "exec_params": {},
    }
    win = {
        "exchange": "binance",
        "market": "um",
        "symbol": "BTCUSDT",
        "signal_timeframe": "1h",
        "exec_timeframe": "15m",
        "signal_tf": "1h",
        "exec_tf": "15m",
        "win_start_ms": win_start,
        "win_end_ms": win_end,
        "sig_start_ms": win_start,
        "sig_end_ms": win_end,
        "signal_series_start_ms": sig_start,
        "warmup_bars_requested": warmup_hours,
    }
    return spec, win


def generate_candles(start_ms, count, iv_ms, base_price=100.0):
    rows = []
    for i in range(count):
        t = start_ms + i * iv_ms
        rows.append({
            "open_time": t, "close_time": t + iv_ms - 1,
            "open": base_price, "high": base_price + 1.0,
            "low": base_price - 1.0, "close": base_price + 0.1,
            "volume": 1000.0, "is_closed": 1
        })
    return rows


def generate_funding(start_ms, end_ms, iv_ms=8 * 3600_000):
    rows = []
    t = start_ms - 7 * 86_400_000
    t -= t % iv_ms
    while t <= end_ms + iv_ms:
        rows.append({
            "funding_time": t, "funding_rate": 0.0001,
            "mark_price": 100.0, "rate_type": "settled"
        })
        t += iv_ms
    return rows


class TestOIBucketCausalFix38(unittest.TestCase):

    def setUp(self):
        self.spec, self.win = make_standard_spec_and_win()
        self.warmup_bars = 168
        self.win_bars = 48
        self.total_sig_bars = self.warmup_bars + self.win_bars
        self.sig_rows = generate_candles(
            self.win["signal_series_start_ms"], self.total_sig_bars, 3600_000
        )
        self.exec_rows = generate_candles(
            self.win["win_start_ms"], self.win_bars * 4, 900_000
        )
        self.fu_rows = generate_funding(self.win["win_start_ms"], self.win["win_end_ms"])

    def test_01_boundary_exact_bucket_end_at_decision_time(self):
        """边界用例 1：5m 桶恰好在决策时刻收盘（bucket_end == decision_time）必须可用。"""
        # 第一根信号 bar: open=win["signal_series_start_ms"], close = open + 3600_000
        first_bar_close = self.win["signal_series_start_ms"] + 3600_000
        # 构造刚好在 first_bar_close 闭合的 5m 桶：ts = first_bar_close - 300_000
        bucket_ts = first_bar_close - 300_000
        oi_rows = [{
            "ts": bucket_ts,
            "open_interest": 5555.0,
            "available_at": first_bar_close,  # 刚好在闭合时刻到达
            "source": "rest"
        }]

        repo = MockRepo(self.sig_rows, self.exec_rows, oi_rows, self.fu_rows)
        data = RUN.load_inputs(self.spec, dict(self.win), repo)

        sig_df = data["sig_df"]
        # 第一根 bar 的 decision_time == first_bar_close，应该对齐到 5555.0
        self.assertEqual(sig_df.loc[0, "open_interest"], 5555.0)
        self.assertEqual(sig_df.loc[0, "oi_observed_at"], float(first_bar_close))
        self.assertTrue(sig_df.loc[0, "oi_pit_proven"])

    def test_02_boundary_future_bucket_not_closed_at_decision_time(self):
        """边界用例 2：5m 桶在决策时刻刚开始（ts == decision_time），尚未闭合，绝不可被使用！"""
        first_bar_close = self.win["signal_series_start_ms"] + 3600_000
        # 桶 A: 在 first_bar_close - 300_000 开始，刚好在 first_bar_close 闭合
        # 桶 B: 在 first_bar_close 开始，在 first_bar_close + 300_000 闭合（未来桶）
        oi_rows = [
            {"ts": first_bar_close - 300_000, "open_interest": 100.0, "available_at": first_bar_close},
            {"ts": first_bar_close, "open_interest": 999999.0, "available_at": first_bar_close + 300_000},
        ]
        repo = MockRepo(self.sig_rows, self.exec_rows, oi_rows, self.fu_rows)
        data = RUN.load_inputs(self.spec, dict(self.win), repo)

        sig_df = data["sig_df"]
        # 第一根 bar 必须取 100.0，绝对不能取尚未闭合的未来值 999999.0！
        self.assertEqual(sig_df.loc[0, "open_interest"], 100.0)
        self.assertNotEqual(sig_df.loc[0, "open_interest"], 999999.0)

    def test_03_missing_available_at_retains_pit_unproven(self):
        """核心契约 3：真实 available_at 缺失时，即使 bucket_end <= decision_time，也必须保留 PIT_UNPROVEN！不得凭 bucket_end 升级！"""
        first_bar_close = self.win["signal_series_start_ms"] + 3600_000
        # 5m 桶闭合，但缺失 available_at
        oi_rows = [{
            "ts": first_bar_close - 300_000,
            "open_interest": 200.0,
            "available_at": None,
            "source": "cold_parquet"
        }]
        repo = MockRepo(self.sig_rows, self.exec_rows, oi_rows, self.fu_rows)
        data = RUN.load_inputs(self.spec, dict(self.win), repo)

        sig_df = data["sig_df"]
        oi_meta = data["coverage"]["oi"]

        # 数值正常对齐（供研究使用）
        self.assertEqual(sig_df.loc[0, "open_interest"], 200.0)
        # 逐行与全局 PIT 必须保留为 UNPROVEN，绝不可凭闭合升级为 PROVEN
        self.assertFalse(sig_df.loc[0, "oi_pit_proven"])
        self.assertEqual(oi_meta["pit_status"], "PIT_UNPROVEN")
        self.assertIn("available_at_missing", oi_meta["pit_unproven_reasons"])

    def test_04_available_at_after_decision_retains_pit_unproven(self):
        """核心契约 4：真实 available_at 晚于决策时间时，必须保留 PIT_UNPROVEN！"""
        first_bar_close = self.win["signal_series_start_ms"] + 3600_000
        # 桶理论闭合在 first_bar_close，但因网络/发布延迟，在 1 分钟后才收到
        oi_rows = [{
            "ts": first_bar_close - 300_000,
            "open_interest": 300.0,
            "available_at": first_bar_close + 60_000,  # 晚于决策时刻
            "source": "rest"
        }]
        repo = MockRepo(self.sig_rows, self.exec_rows, oi_rows, self.fu_rows)
        data = RUN.load_inputs(self.spec, dict(self.win), repo)

        sig_df = data["sig_df"]
        oi_meta = data["coverage"]["oi"]

        self.assertEqual(sig_df.loc[0, "open_interest"], 300.0)
        self.assertFalse(sig_df.loc[0, "oi_pit_proven"])
        self.assertEqual(oi_meta["pit_status"], "PIT_UNPROVEN")
        self.assertIn("available_at_after_decision", oi_meta["pit_unproven_reasons"])

    def test_05_backfilled_data_retains_pit_unproven(self):
        """核心契约 5：回填数据（source 带有 archive / batch / gapfill）无法证明历史可得，必须保留 PIT_UNPROVEN！"""
        first_bar_close = self.win["signal_series_start_ms"] + 3600_000
        oi_rows = [{
            "ts": first_bar_close - 300_000,
            "open_interest": 400.0,
            "available_at": first_bar_close,
            "source": "binance_archive_batch_import"  # 标明为离线回填
        }]
        repo = MockRepo(self.sig_rows, self.exec_rows, oi_rows, self.fu_rows)
        data = RUN.load_inputs(self.spec, dict(self.win), repo)

        sig_df = data["sig_df"]
        oi_meta = data["coverage"]["oi"]

        self.assertEqual(sig_df.loc[0, "open_interest"], 400.0)
        self.assertFalse(sig_df.loc[0, "oi_pit_proven"])
        self.assertEqual(oi_meta["pit_status"], "PIT_UNPROVEN")
        self.assertIn("backfilled_data_unproven", oi_meta["pit_unproven_reasons"])

    def test_06_no_matched_bucket_returns_nan(self):
        """边界场景 6：决策时刻前没有任何 5m 桶已闭合，必须置为 NaN（绝不虚构数据）。"""
        first_bar_close = self.win["signal_series_start_ms"] + 3600_000
        # 所有 5m 桶都在 first_bar_close 之后闭合
        oi_rows = [
            {"ts": first_bar_close + 1000, "open_interest": 500.0, "available_at": first_bar_close + 301000}
        ]
        repo = MockRepo(self.sig_rows, self.exec_rows, oi_rows, self.fu_rows)
        data = RUN.load_inputs(self.spec, dict(self.win), repo)

        sig_df = data["sig_df"]
        self.assertTrue(math.isnan(sig_df.loc[0, "open_interest"]))
        self.assertFalse(sig_df.loc[0, "oi_pit_proven"])

    def test_07_empty_oi_dataset(self):
        """边界场景 7：空 OI 数据集，aligned 全部为 NaN，bars_with_oi=0，pit_status 保持 PIT_UNPROVEN。"""
        repo = MockRepo(self.sig_rows, self.exec_rows, [], self.fu_rows)
        data = RUN.load_inputs(self.spec, dict(self.win), repo)

        sig_df = data["sig_df"]
        oi_meta = data["coverage"]["oi"]

        self.assertTrue(sig_df["open_interest"].isna().all())
        self.assertEqual(oi_meta["bars_with_oi"], 0)
        self.assertEqual(oi_meta["bars_missing_oi"], len(sig_df))
        self.assertEqual(oi_meta["pit_status"], "PIT_UNPROVEN")
        self.assertIn("oi_rows_empty", oi_meta["pit_unproven_reasons"])

    def test_08_fully_proven_dataset(self):
        """标杆场景 8：所有 bar 均有已闭合并在决策前到达的真实数据，允许评定为 PIT_PROVEN。"""
        # 为每根 1h 信号 bar 在收盘前提供一条真实 5m OI：
        # 5m 桶在收盘前半小时闭合（例如 bar_close - 300_000），到达时间为 bucket_end + 2400ms，在收盘决策时刻前可得。
        oi_rows = []
        for i, row in enumerate(self.sig_rows):
            bar_close = row["open_time"] + 3600_000
            b_end = bar_close - 300_000
            oi_rows.append({
                "ts": b_end - 300_000,
                "open_interest": 1000.0 + i,
                "available_at": b_end + 2400,  # 桶闭合后 2.4s 收到，且 <= bar_close
                "source": "rest"
            })
        repo = MockRepo(self.sig_rows, self.exec_rows, oi_rows, self.fu_rows)
        data = RUN.load_inputs(self.spec, dict(self.win), repo)

        sig_df = data["sig_df"]
        oi_meta = data["coverage"]["oi"]

        self.assertEqual(oi_meta["bars_with_oi"], len(sig_df))
        self.assertEqual(oi_meta["bars_missing_oi"], 0)
        self.assertEqual(oi_meta["bars_pit_proven"], len(sig_df))
        self.assertEqual(oi_meta["bars_pit_unproven"], 0)
        self.assertEqual(oi_meta["pit_status"], "PIT_PROVEN")
        self.assertTrue(sig_df["oi_pit_proven"].all())

    def test_09_multi_bar_step_alignment(self):
        """用例 9：连续 5m OI 序列在 1h 信号 bar 上的步进因果核查。"""
        # 生成整段均匀 5m OI：从 sig_start 到 win_end
        oi_rows = []
        cur_t = self.win["signal_series_start_ms"]
        end_t = self.win["sig_end_ms"]
        idx = 0
        while cur_t < end_t:
            oi_rows.append({
                "ts": cur_t,
                "open_interest": 10000.0 + idx,
                "available_at": cur_t + 300_000,  # 闭合时刻立即可得
                "source": "rest"
            })
            cur_t += 300_000
            idx += 1

        repo = MockRepo(self.sig_rows, self.exec_rows, oi_rows, self.fu_rows)
        data = RUN.load_inputs(self.spec, dict(self.win), repo)

        sig_df = data["sig_df"]
        # 对每一根信号 bar，核对取得的 oi_observed_at 是否严格等于收盘时刻
        for i in range(len(sig_df)):
            bar_close = sig_df.loc[i, "timestamp"] + 3600_000
            matched_end = sig_df.loc[i, "oi_observed_at"]
            # 5m 桶闭合时刻必须 <= bar 收盘时刻
            self.assertLessEqual(matched_end, bar_close)
            # 且对于 1h 连续 5m 桶，最后闭合的 5m 桶恰好在 bar_close 闭合
            self.assertEqual(matched_end, bar_close)
            self.assertTrue(sig_df.loc[i, "oi_pit_proven"])

    def test_10_runner_source_code_cleanliness(self):
        """用例 10：runner 源码契约完整性守护 —— 不碰策略参数、门禁、接线模块。"""
        src = (_BACKEND_DIR / "backtest_runner.py").read_text(encoding="utf-8")
        # 守卫：不得引入门禁接线或 severity 复制
        self.assertNotIn("backtest_data_gate", src)
        self.assertNotIn("severity", src)
        # 守卫：align_rule 必须声明先闭合并保留 PIT_UNPROVEN
        self.assertIn("bucket_end <= close", src)
        self.assertIn("PIT_UNPROVEN", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

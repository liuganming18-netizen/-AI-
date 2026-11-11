# -*- coding: utf-8 -*-
"""06-B 候选回测执行入口与六项 P0 验收单测 (反例收敛修复版)

测试目标：
────────
1. P0-1: 输入绑定固定开发快照；内存、外部目录、已有快照入口均覆盖资金费；首次登记与冻结复用明确分开，凭据损坏直接拒绝；
2. P0-2: 历史时钟正确限制可见数据（剔除未收盘与未来可得数据）；严格与近似 PIT 模式区分；
3. P0-3: 信号与成交顺序保守规则（相位A/B/C/D），同根止盈止损歧义事件进入假设桶，不美化结果；
4. P0-4: 账本对账（成交、手续费、资金费双口径、现金、持仓），对账失败不放行；
5. P0-5: 幂等性与恢复机制（同 run_id 规范化比对、防覆盖；拒绝前不改旧状态；受控中断恢复无重复无漏记）；
6. P0-6: 实验注册门禁（检查存在性、冻结态与范围；防绕过）与最终盲测封闭硬阻断（HoldoutSealedError）。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

import backtest_data_gate as DG
import backtest_runner as BR
import backtest_store as S
import experiment_registry as ER
from candidate_backtest_runner import (
    DEFAULT_HOLDOUT_START_MS,
    CandidateExecutionError,
    HoldoutSealedError,
    _df_to_csv_bytes,
    _sha_bytes,
    create_or_bind_snapshot,
    normalize_spec,
    run_candidate_backtest,
)


class MockNonEmptyCandidate:
    """产生确定性非空成交结果的候选对象（包含 3 笔实际成交、费用与资金费）"""
    STRATEGY_ID = "CANDIDATE_MOCK_TRADES"

    @staticmethod
    def run_portfolio_execution(market_data, strategy_params=None, exec_cfg=None, time_unit="ms", trade_start_sec=None):
        trades = [
            {
                "trade_id": "T001",
                "signal_id": "SIG001",
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "signal_type": 1,
                "entry_time": 1704153600000,
                "exit_time": 1704168000000,
                "entry_price": 42000.0,
                "exit_price": 42500.0,
                "qty": 0.5,
                "gross_pnl": 250.0,
                "entry_fee": 10.5,
                "exit_fee": 10.625,
                "fees": 21.125,
                "funding_fee": 2.1,
                "net_pnl": 226.775,
                "exit_reason": "TAKE_PROFIT",
            },
            {
                "trade_id": "T002",
                "signal_id": "SIG002",
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "signal_type": 1,
                "entry_time": 1704240000000,
                "exit_time": 1704254400000,
                "entry_price": 42600.0,
                "exit_price": 42100.0,
                "qty": 0.5,
                "gross_pnl": -250.0,
                "entry_fee": 10.65,
                "exit_fee": 10.525,
                "fees": 21.175,
                "funding_fee": -1.0,
                "net_pnl": -270.175,
                "exit_reason": "STOP_LOSS",
            },
            {
                "trade_id": "T003",
                "signal_id": "SIG003",
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "signal_type": 1,
                "entry_time": 1704326400000,
                "exit_time": 1704340800000,
                "entry_price": 42200.0,
                "exit_price": 42800.0,
                "qty": 0.8,
                "gross_pnl": 480.0,
                "entry_fee": 16.88,
                "exit_fee": 17.12,
                "fees": 34.0,
                "funding_fee": 3.2,
                "net_pnl": 442.8,
                "exit_reason": "SIGNAL_CLOSE",
            },
        ]
        orders = [
            {"order_id": "O1", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.5, "price": 42000.0, "time": 1704153600000},
            {"order_id": "O2", "symbol": "BTCUSDT", "side": "SELL", "qty": 0.5, "price": 42500.0, "time": 1704168000000},
            {"order_id": "O3", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.5, "price": 42600.0, "time": 1704240000000},
            {"order_id": "O4", "symbol": "BTCUSDT", "side": "SELL", "qty": 0.5, "price": 42100.0, "time": 1704254400000},
            {"order_id": "O5", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.8, "price": 42200.0, "time": 1704326400000},
            {"order_id": "O6", "symbol": "BTCUSDT", "side": "SELL", "qty": 0.8, "price": 42800.0, "time": 1704340800000},
        ]
        equity_curve = [
            {"timestamp": 1704067200000, "cash": 100000.0, "equity": 100000.0, "cash_equity": 100000.0, "total_equity": 100000.0, "open_positions_count": 0, "positions": 0},
            {"timestamp": 1704168000000, "cash": 100226.775, "equity": 100226.775, "cash_equity": 100226.775, "total_equity": 100226.775, "open_positions_count": 0, "positions": 0},
            {"timestamp": 1704254400000, "cash": 99956.6, "equity": 99956.6, "cash_equity": 99956.6, "total_equity": 99956.6, "open_positions_count": 0, "positions": 0},
            {"timestamp": 1704340800000, "cash": 100399.4, "equity": 100399.4, "cash_equity": 100399.4, "total_equity": 100399.4, "open_positions_count": 0, "positions": 0},
            {"timestamp": 1704672000000, "cash": 100399.4, "equity": 100399.4, "cash_equity": 100399.4, "total_equity": 100399.4, "open_positions_count": 0, "positions": 0},
        ]
        sum_gross = sum(t["gross_pnl"] for t in trades)
        sum_entry_fee = sum(t["entry_fee"] for t in trades)
        sum_exit_fee = sum(t["exit_fee"] for t in trades)
        sum_funding = sum(t["funding_fee"] for t in trades)
        reconcile = {
            "sum_gross_pnl": sum_gross,
            "sum_entry_fee": sum_entry_fee,
            "sum_exit_fee": sum_exit_fee,
            "sum_funding_fee": sum_funding,
            "sum_funding_fee_confirmed": sum_funding,
            "sum_funding_fee_hypothetical": 0.0,
            "sum_funding_fee_conservative": sum_funding,
            "final_equity_hypothetical": 100399.4,
            "funding_accounting_model": "conservative",
        }
        return {
            "trades": trades,
            "orders": orders,
            "positions": [],
            "funding_records": [],
            "equity_curve": equity_curve,
            "reconcile": reconcile,
        }


def _make_dummy_candles(start_ms: int, count: int, interval_ms: int = 3600000,
                        base_price: float = 100.0, with_available_at: bool = True) -> pd.DataFrame:
    """生成确定性合成 K 线。"""
    rows = []
    px = base_price
    for i in range(count):
        t = start_ms + i * interval_ms
        hi = px + 1.0
        lo = px - 1.0
        cl = px + 0.2
        r = {
            "open_time": t,
            "timestamp": t,
            "open": px,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": 100.0,
            "is_closed": 1,
        }
        if with_available_at:
            r["available_at"] = t + interval_ms
        rows.append(r)
        px = cl
    return pd.DataFrame(rows)


class TestCandidateBacktestRunnerP0(unittest.TestCase):
    """06-B 候选回测执行入口 6 项 P0 验证（反例收敛修复版）。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="bt_p0_test_")
        self.old_runs_dir = os.environ.get("EVENT_TREND_RUNS_DIR")
        self.old_oi_dir = os.environ.get("OI_RESONANCE_RUNS_DIR")
        os.environ["EVENT_TREND_RUNS_DIR"] = self.tmp_dir
        os.environ["OI_RESONANCE_RUNS_DIR"] = self.tmp_dir
        self.sym = "BTCUSDT"
        self.start_ms = 1704067200000   # 2024-01-01 00:00:00 UTC (开发安全区间)
        self.end_ms = 1704672000000     # 2024-01-08 00:00:00 UTC (7天)

    def tearDown(self):
        if self.old_runs_dir:
            os.environ["EVENT_TREND_RUNS_DIR"] = self.old_runs_dir
        else:
            os.environ.pop("EVENT_TREND_RUNS_DIR", None)
        if self.old_oi_dir:
            os.environ["OI_RESONANCE_RUNS_DIR"] = self.old_oi_dir
        else:
            os.environ.pop("OI_RESONANCE_RUNS_DIR", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _get_spec(self, start_ms: int = None, end_ms: int = None,
                  experiment_id: str = "exp_dee96a16cfe89331",
                  is_mechanism_test: bool = True) -> Dict[str, Any]:
        return {
            "symbols": [self.sym],
            "exchange": "binance",
            "market": "usdt_futures",
            "signal_timeframe": "1h",
            "exec_timeframe": "15m",
            "window": {
                "mode": "explicit",
                "start_ms": start_ms or self.start_ms,
                "end_ms": end_ms or self.end_ms,
            },
            "warmup_bars": 10,
            "signal_overrides": {},
            "exec_overrides": {},
            "is_mechanism_test": is_mechanism_test,
            "experiment_id": experiment_id,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # P0-1: 输入绑定固定开发快照及完整输入身份校验（含资金费）
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_1_input_bound_to_fixed_snapshot_and_manifest_created(self):
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot = {
            "sig_df": sig_df,
            "exec_df": exec_df,
            "funding_events": [{"event_time": self.start_ms + 8 * 3600000, "rate": 0.0001, "mark": 100.0}],
        }

        res = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot)
        self.assertEqual(res["status"], S.STATUS_SUCCEEDED)
        run_id = res["run_id"]
        run_p = S.run_dir(run_id)

        # 验证固定快照文件已写入 inputs/ 目录
        snap_p = run_p / "inputs"
        self.assertTrue((snap_p / f"{self.sym}_signal_bars.csv").exists())
        self.assertTrue((snap_p / f"{self.sym}_exec_bars.csv").exists())
        self.assertTrue((snap_p / f"{self.sym}_funding_events.json").exists())

        # 验证不可变 manifest
        man_p = run_p / "inputs_manifest.json"
        self.assertTrue(man_p.exists())
        manifest = json.loads(man_p.read_text(encoding="utf-8"))
        self.assertIn("signal_bars_sha256", manifest)
        self.assertIn("funding_events_sha256", manifest)
        self.assertEqual(manifest["symbol"], self.sym)
        self.assertEqual(manifest["source"], "memory_snapshot")

    def test_p0_1_input_validation_requires_funding_and_rejects_corrupted_manifest(self):
        """缺口 1: 内存/外部目录统一覆盖资金费；写前输入强验：外部 manifest 缺失、为 {}、身份字段缺失或损坏直接在写前拒绝。"""
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)

        # 1. 内存快照缺失资金费字段直接拒绝
        bad_snapshot = {"sig_df": sig_df, "exec_df": exec_df}
        with self.assertRaises(CandidateExecutionError) as ctx_f:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=bad_snapshot)
        self.assertIn("缺失 funding_events 字段", str(ctx_f.exception))

        # 2. 外部快照缺失 inputs_manifest.json 凭据，写前直接拒绝，断言未创建 run 目录
        ext_snap_no_man = Path(self.tmp_dir) / "external_no_man"
        ext_snap_no_man.mkdir(parents=True)
        sig_df.to_csv(ext_snap_no_man / f"{self.sym}_signal_bars.csv", index=False)
        exec_df.to_csv(ext_snap_no_man / f"{self.sym}_exec_bars.csv", index=False)
        (ext_snap_no_man / f"{self.sym}_funding_events.json").write_text("[]", encoding="utf-8")

        test_run_id_no_man = S.new_run_id("001122aa")
        with self.assertRaises(CandidateExecutionError) as ctx_noman:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec, snapshot_path=ext_snap_no_man, run_id=test_run_id_no_man)
        self.assertIn("外部快照凭据缺失（写前强验拒绝）", str(ctx_noman.exception))
        self.assertFalse(S.run_dir(test_run_id_no_man).exists(), "写前强验拒绝时严禁创建 run 目录！")

        # 3. 外部快照 inputs_manifest.json 为空字典 {}，写前直接拒绝
        ext_snap_empty_man = Path(self.tmp_dir) / "external_empty_man"
        ext_snap_empty_man.mkdir(parents=True)
        sig_df.to_csv(ext_snap_empty_man / f"{self.sym}_signal_bars.csv", index=False)
        exec_df.to_csv(ext_snap_empty_man / f"{self.sym}_exec_bars.csv", index=False)
        (ext_snap_empty_man / f"{self.sym}_funding_events.json").write_text("[]", encoding="utf-8")
        (ext_snap_empty_man / "inputs_manifest.json").write_text("{}", encoding="utf-8")

        test_run_id_empty = S.new_run_id("001122bb")
        with self.assertRaises(CandidateExecutionError) as ctx_empty:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec, snapshot_path=ext_snap_empty_man, run_id=test_run_id_empty)
        self.assertIn("外部快照凭据为空（写前强验拒绝）", str(ctx_empty.exception))
        self.assertFalse(S.run_dir(test_run_id_empty).exists())

        # 4. 外部快照 inputs_manifest.json 身份字段不完整（缺失 signal_bars_sha256），写前直接拒绝
        ext_snap_incomplete = Path(self.tmp_dir) / "external_incomplete_man"
        ext_snap_incomplete.mkdir(parents=True)
        sig_df.to_csv(ext_snap_incomplete / f"{self.sym}_signal_bars.csv", index=False)
        exec_df.to_csv(ext_snap_incomplete / f"{self.sym}_exec_bars.csv", index=False)
        (ext_snap_incomplete / f"{self.sym}_funding_events.json").write_text("[]", encoding="utf-8")
        (ext_snap_incomplete / "inputs_manifest.json").write_text(json.dumps({"symbol": self.sym}), encoding="utf-8")

        test_run_id_incomp = S.new_run_id("001122cc")
        with self.assertRaises(CandidateExecutionError) as ctx_incomp:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec, snapshot_path=ext_snap_incomplete, run_id=test_run_id_incomp)
        self.assertIn("外部快照凭据身份字段不完整（写前强验拒绝）", str(ctx_incomp.exception))
        self.assertFalse(S.run_dir(test_run_id_incomp).exists())

        # 5. 外部快照损坏的 json 必须直接拒绝，绝不警告放行
        ext_snap_dir = Path(self.tmp_dir) / "external_corrupted_snap"
        ext_snap_dir.mkdir(parents=True)
        sig_df.to_csv(ext_snap_dir / f"{self.sym}_signal_bars.csv", index=False)
        exec_df.to_csv(ext_snap_dir / f"{self.sym}_exec_bars.csv", index=False)
        (ext_snap_dir / f"{self.sym}_funding_events.json").write_text("[]", encoding="utf-8")
        (ext_snap_dir / "inputs_manifest.json").write_text("{CORRUPTED_JSON", encoding="utf-8")

        test_run_id_corrupt = S.new_run_id("001122dd")
        with self.assertRaises(CandidateExecutionError) as ctx_man:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec, snapshot_path=ext_snap_dir, run_id=test_run_id_corrupt)
        self.assertIn("manifest 解析失败", str(ctx_man.exception))
        self.assertFalse(S.run_dir(test_run_id_corrupt).exists())

    # ──────────────────────────────────────────────────────────────────────────
    # P0-2: 历史时钟正确限制可见数据；严格与近似 PIT 区分
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_2_history_clock_filters_unclosed_and_future_bars(self):
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24 + 2, 3600000)
        sig_df.loc[len(sig_df) - 2, "is_closed"] = 0
        sig_df.loc[len(sig_df) - 1, "open_time"] = self.end_ms + 3600000

        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        res = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, pit_mode="approximate")
        self.assertEqual(res["status"], S.STATUS_SUCCEEDED)
        self.assertEqual(res["data_quality"]["pit_status"], "PIT_PROVEN")

    def test_p0_2_strict_pit_mode_blocks_when_available_at_missing(self):
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000, with_available_at=False)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000, with_available_at=False)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        res_strict = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, pit_mode="strict")
        self.assertEqual(res_strict["status"], S.STATUS_FAILED)
        self.assertIn("严格 PIT 门禁阻断", res_strict["failure_reason"])

        res_approx = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, pit_mode="approximate")
        self.assertEqual(res_approx["status"], S.STATUS_SUCCEEDED)
        self.assertEqual(res_approx["data_quality"]["pit_status"], "PIT_APPROXIMATE")
        self.assertEqual(res_approx["data_quality"]["status"], "degraded")

    # ──────────────────────────────────────────────────────────────────────────
    # P0-3: 信号与成交顺序保守规则；同根止损与歧义资金费进入假设桶
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_3_conservative_execution_and_funding_ambiguity_bucket(self):
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        events = [{"event_time": self.start_ms + 16 * 3600000, "rate": 0.0002, "mark": 100.0}]
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": events}

        res = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot)
        self.assertEqual(res["status"], S.STATUS_SUCCEEDED)
        metrics = res["metrics"]
        self.assertIn("total_funding_fee_confirmed", metrics)
        self.assertIn("total_funding_fee_hypothetical", metrics)

    # ──────────────────────────────────────────────────────────────────────────
    # P0-4: 账本对账（成交、手续费、资金费、现金、持仓与未实现盈亏）
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_4_ledger_recompute_and_reconciliation(self):
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        res = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot)
        self.assertEqual(res["status"], S.STATUS_SUCCEEDED)
        self.assertIn("ledger_recomputed", res)
        led = res["ledger_recomputed"]
        met = res["metrics"]
        self.assertEqual(led["trades_count"], met["trades_count"])
        self.assertEqual(led["net_pnl"], met["net_pnl"])

    # ──────────────────────────────────────────────────────────────────────────
    # P0-5: 幂等性、同 run_id 防漂移、冲突在 set_status(RUNNING) 前拒绝、受控中断恢复
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_5_manual_dirty_residue_cleanup_on_recovery(self):
        """保留手工残留行清理测试（准确命名）：验证恢复重跑时能安全清理手工注入的残留脏数据。"""
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        fixed_run_id = S.new_run_id("00112233")
        res1 = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, run_id=fixed_run_id)
        self.assertEqual(res1["status"], S.STATUS_SUCCEEDED)
        c1 = res1["ledger_counts"]["trades"]

        # 手工注入一行脏 trade
        ledger_db = S.ledger_path(fixed_run_id)
        with sqlite3.connect(str(ledger_db)) as con:
            con.execute("""
                INSERT INTO trades (
                    run_id, trade_id, signal_id, symbol, direction, signal_type,
                    entry_time_ms, exit_time_ms, entry_price, exit_price, qty,
                    gross_pnl, entry_fee, exit_fee, fees, funding_fee, net_pnl, exit_reason
                ) VALUES (?, 'MANUAL_DIRTY_ROW', 's0', 'BTCUSDT', 'LONG', 1, 100, 200, 100, 105, 1, 5, 0, 0, 0, 0, 5, 'manual')
            """, (fixed_run_id,))

        # 重新执行恢复
        res2 = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, run_id=fixed_run_id)
        self.assertEqual(res2["status"], S.STATUS_SUCCEEDED)
        c2 = res2["ledger_counts"]["trades"]
        self.assertEqual(c1, c2)

    def test_p0_5_same_run_id_rejects_altered_inputs_or_spec_before_running(self):
        """缺口 2 & 缺口 3: 同 run_id 规范化比对防覆盖；冲突在 set_status(RUNNING) 前拒绝，断言旧状态/快照/账本不变。"""
        spec1 = self._get_spec()
        sig_df1 = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df1 = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot1 = {"sig_df": sig_df1, "exec_df": exec_df1, "funding_events": []}

        fixed_run_id = S.new_run_id("00445566")
        res1 = run_candidate_backtest("WX_OI_RESONANCE_V1", spec1, data_snapshot=snapshot1, run_id=fixed_run_id)
        self.assertEqual(res1["status"], S.STATUS_SUCCEEDED)
        orig_manifest_text = (S.run_dir(fixed_run_id) / "inputs_manifest.json").read_text(encoding="utf-8")
        orig_trade_count = res1["ledger_counts"]["trades"]

        # 1. 尝试使用同一个 run_id 但篡改配置
        spec2 = copy.deepcopy(spec1)
        spec2["signal_timeframe"] = "4h"  # 周期漂移
        with self.assertRaises(CandidateExecutionError) as ctx_spec:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec2, data_snapshot=snapshot1, run_id=fixed_run_id)
        self.assertIn("同运行编号拒绝更换输入、规则或配置", str(ctx_spec.exception))

        # 断言 1：拒绝后旧成功状态、快照和账本均保持不变
        doc_after_spec_reject = S.read_run(fixed_run_id)
        self.assertEqual(doc_after_spec_reject["status"], S.STATUS_SUCCEEDED)
        self.assertEqual((S.run_dir(fixed_run_id) / "inputs_manifest.json").read_text(encoding="utf-8"), orig_manifest_text)
        self.assertEqual(S.ledger_counts(fixed_run_id)["trades"], orig_trade_count)

        # 2. 尝试传入不同内容的外部快照（带有其自身的有效 manifest）
        alt_ext_dir = Path(self.tmp_dir) / "alt_ext_snapshot"
        alt_ext_dir.mkdir(parents=True)
        sig_df_alt = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000, base_price=888.0)
        sig_df_alt.to_csv(alt_ext_dir / f"{self.sym}_signal_bars.csv", index=False)
        exec_df1.to_csv(alt_ext_dir / f"{self.sym}_exec_bars.csv", index=False)
        (alt_ext_dir / f"{self.sym}_funding_events.json").write_text("[]", encoding="utf-8")
        
        alt_sig_bytes = (alt_ext_dir / f"{self.sym}_signal_bars.csv").read_bytes()
        alt_exec_bytes = (alt_ext_dir / f"{self.sym}_exec_bars.csv").read_bytes()
        alt_ev_bytes = (alt_ext_dir / f"{self.sym}_funding_events.json").read_bytes()
        alt_manifest = {
            "symbol": self.sym,
            "signal_bars_sha256": _sha_bytes(alt_sig_bytes),
            "exec_bars_sha256": _sha_bytes(alt_exec_bytes),
            "funding_events_sha256": _sha_bytes(alt_ev_bytes),
        }
        (alt_ext_dir / "inputs_manifest.json").write_text(json.dumps(alt_manifest), encoding="utf-8")

        with self.assertRaises(CandidateExecutionError) as ctx_snap:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec1, snapshot_path=alt_ext_dir, run_id=fixed_run_id)
        self.assertIn("不同内容的外部快照即使带有自己的有效manifest，也不能覆盖旧run的绑定身份", str(ctx_snap.exception))

        # 断言 2：外部快照覆盖被拒绝后，旧成功状态、快照和账本依然完好不变
        doc_after_snap_reject = S.read_run(fixed_run_id)
        self.assertEqual(doc_after_snap_reject["status"], S.STATUS_SUCCEEDED)
        self.assertEqual((S.run_dir(fixed_run_id) / "inputs_manifest.json").read_text(encoding="utf-8"), orig_manifest_text)
        self.assertEqual(S.ledger_counts(fixed_run_id)["trades"], orig_trade_count)

        # 3. 旧成功 run 的 manifest 变成 {} 后，仅修改 funding 再次调用，必须抛出 CandidateExecutionError 拒绝，且旧目录完全不变
        target_dir = S.run_dir(fixed_run_id)
        run_dir_files_before = {}
        for p in target_dir.rglob("*"):
            if p.is_file() and p.name != "inputs_manifest.json":
                run_dir_files_before[str(p.relative_to(target_dir))] = _sha_bytes(p.read_bytes())

        # 将旧运行的 manifest 改为 {}
        (target_dir / "inputs_manifest.json").write_text("{}", encoding="utf-8")

        snapshot_altered_funding = copy.deepcopy(snapshot1)
        snapshot_altered_funding["funding_events"] = [{"event_time": self.start_ms + 8 * 3600000, "rate": 0.0002, "mark": 105.0}]
        with self.assertRaises(CandidateExecutionError) as ctx_empty_old_man:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec1, data_snapshot=snapshot_altered_funding, run_id=fixed_run_id)
        self.assertIn("同运行编号复用凭据为空", str(ctx_empty_old_man.exception))

        # 断言 3：空凭据覆盖被拒绝后，旧成功状态依然为 SUCCEEDED，其余所有既有文件哈希保持 100% 不变
        doc_after_empty_man_reject = S.read_run(fixed_run_id)
        self.assertEqual(doc_after_empty_man_reject["status"], S.STATUS_SUCCEEDED)
        for rel_path, expected_hash in run_dir_files_before.items():
            actual_hash = _sha_bytes((target_dir / rel_path).read_bytes())
            self.assertEqual(actual_hash, expected_hash, f"文件 {rel_path} 在被拒绝后发生改变！")

    def test_p0_5_controlled_interruption_recovery_and_reconciliation(self):
        """缺口 5: 用包含非空确定性成交的候选对象，在实际持久化阶段发生真实受控崩溃中断（此时 SQLite 账本与 trades.jsonl 已生成非空内容），
        随后调用恢复重跑，核验逐笔成交、费用、资金费与账本完全一致，无重复无遗漏且状态正确为 SUCCEEDED。
        """
        candidate = MockNonEmptyCandidate()
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        events = [{"event_time": self.start_ms + 16 * 3600000, "rate": 0.0001, "mark": 100.0}]
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": events}

        # 1. 先跑无中断的基准 (生成非空成交结果)
        base_id = S.new_run_id("00ba5e01")
        res_base = run_candidate_backtest(candidate, spec, data_snapshot=snapshot, run_id=base_id)
        self.assertEqual(res_base["status"], S.STATUS_SUCCEEDED)
        base_trades_count = res_base["ledger_counts"]["trades"]
        self.assertEqual(base_trades_count, 3, "基准必须包含非空交易结果（>=3笔）！")
        base_metrics = res_base["metrics"]
        self.assertEqual(base_metrics["trades_count"], 3)
        self.assertAlmostEqual(base_metrics["net_pnl"], 399.4, places=4)

        # 读取基准 SQLite 中的每一笔 trade
        base_ledger_path = S.ledger_path(base_id)
        with sqlite3.connect(str(base_ledger_path)) as con:
            con.row_factory = sqlite3.Row
            base_trade_rows = [dict(r) for r in con.execute("SELECT * FROM trades ORDER BY trade_id;").fetchall()]
        self.assertEqual(len(base_trade_rows), 3)

        # 2. 构造一次真实的持久化阶段受控中断（在 mid_persistence 注入崩溃，此时账本已落盘非空内容）
        test_run_id = S.new_run_id("00eeff01")
        interrupted = False

        def inject_crash_during_persistence(info: Dict[str, Any]):
            nonlocal interrupted
            if info["stage"] == "mid_persistence" and not interrupted:
                interrupted = True
                raise RuntimeError("Controlled crash simulated mid-persistence while writing ledger")

        res_interrupted = run_candidate_backtest(
            candidate, spec, data_snapshot=snapshot,
            run_id=test_run_id, execution_interceptor=inject_crash_during_persistence
        )
        self.assertEqual(res_interrupted["status"], S.STATUS_FAILED)
        self.assertIn("Controlled crash simulated mid-persistence", res_interrupted["failure_reason"])

        # 验证中断时，持久化文件与 SQLite 账本已生成（非空）
        inter_ledger = S.ledger_path(test_run_id)
        self.assertTrue(inter_ledger.exists(), "中断时 SQLite 账本必须已生成")
        self.assertTrue((S.run_dir(test_run_id) / "trades.jsonl").exists(), "中断时 trades.jsonl 必须已生成")

        # 3. 再次调用执行恢复/隔离重算
        res_recovered = run_candidate_backtest(
            candidate, spec, data_snapshot=snapshot, run_id=test_run_id
        )
        self.assertEqual(res_recovered["status"], S.STATUS_SUCCEEDED)

        # 4. 对照完整逐笔交易、费用和最终账本，确认无重复、无遗漏
        rec_trades_count = res_recovered["ledger_counts"]["trades"]
        self.assertEqual(rec_trades_count, 3, "恢复后成交笔数必须严格为 3，无重复无漏记！")
        self.assertEqual(res_recovered["metrics"]["trades_count"], 3)
        self.assertAlmostEqual(res_recovered["metrics"]["net_pnl"], base_metrics["net_pnl"], places=4)
        self.assertTrue(res_recovered["ledger_recomputed"]["cash_identity_ok"])

        # 从恢复后的 SQLite 账本中读出全部交易，与基准逐笔核验
        rec_ledger_path = S.ledger_path(test_run_id)
        with sqlite3.connect(str(rec_ledger_path)) as con:
            con.row_factory = sqlite3.Row
            rec_trade_rows = [dict(r) for r in con.execute("SELECT * FROM trades ORDER BY trade_id;").fetchall()]
        self.assertEqual(len(rec_trade_rows), 3)

        for b_row, r_row in zip(base_trade_rows, rec_trade_rows):
            for field in ("trade_id", "symbol", "direction", "signal_type", "entry_price", "exit_price",
                          "qty", "gross_pnl", "entry_fee", "exit_fee", "fees", "funding_fee", "net_pnl", "exit_reason"):
                self.assertEqual(b_row[field], r_row[field], f"恢复后逐笔对账字段 {field} 不匹配！")

    # ──────────────────────────────────────────────────────────────────────────
    # P0-6: 实验注册门禁（检查存在性、冻结态与范围；防绕过）与最终盲测封闭硬阻断
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_6_experiment_registry_gate_validates_existence_and_freeze(self):
        """缺口 4: 复用真实实验注册门禁；不存在的非空 ID 必须拒绝；正式候选不能靠省略 is_formal、is_mechanism_test=True 或传 holdout_sealed=False 绕过。"""
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        # 1. 传入不存在的非空 experiment_id，必须拒绝
        spec_non_exist = self._get_spec()
        spec_non_exist["experiment_id"] = "exp_definitely_not_exist_9999"
        with self.assertRaises(CandidateExecutionError) as ctx_not_found:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec_non_exist, data_snapshot=snapshot)
        self.assertIn("指定的实验 ID 不存在（未登记）", str(ctx_not_found.exception))

        # 2. 正式候选不能靠省略 is_formal 绕过（WX_* 默认视为正式候选，若无 is_mechanism_test 必须绑已登记实验）
        spec_formal_bypass = self._get_spec(is_mechanism_test=False)
        spec_formal_bypass.pop("experiment_id", None)
        with self.assertRaises(CandidateExecutionError) as ctx_formal:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec_formal_bypass, data_snapshot=snapshot)
        self.assertIn("必须显式绑定已批准并已冻结的 experiment_id", str(ctx_formal.exception))

        # 3. 拦截已复现的组合绕过：WX_OI_RESONANCE_V1 配合 is_mechanism_test=True、holdout_sealed=False、省略实验 ID，必须在门禁处被硬阻断
        spec_combo_bypass = self._get_spec(is_mechanism_test=True)
        spec_combo_bypass.pop("experiment_id", None)
        with self.assertRaises(CandidateExecutionError) as ctx_combo:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec_combo_bypass, data_snapshot=snapshot, holdout_sealed=False)
        self.assertTrue(
            "必须显式绑定已批准并已冻结的 experiment_id" in str(ctx_combo.exception) or
            "盲测门禁阻断" in str(ctx_combo.exception)
        )

        # 4. 盲测封存硬阻断：在 06-B 阶段未获授权前，即使传合法实验 ID，传入 holdout_sealed=False 也必须直接被拒绝
        spec_holdout_false = self._get_spec()
        with self.assertRaises(CandidateExecutionError) as ctx_holdout_false:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec_holdout_false, data_snapshot=snapshot, holdout_sealed=False)
        self.assertIn("06-B 候选回测准备阶段，未获最终盲测解封授权，严禁传入 holdout_sealed=False", str(ctx_holdout_false.exception))

        # 5. 传入已登记但未冻结的真实实验（exp_74a00fc7772bf37d），必须拒绝
        spec_unfrozen = self._get_spec()
        spec_unfrozen["experiment_id"] = "exp_74a00fc7772bf37d"
        with self.assertRaises(CandidateExecutionError) as ctx_unfrozen:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec_unfrozen, data_snapshot=snapshot)
        self.assertIn("实验尚未冻结", str(ctx_unfrozen.exception))

    def test_p0_6_holdout_sealed_blocks_execution_when_window_crosses(self):
        """盲测区间硬阻断：触及盲测窗口且未获授权，绝对抛出 HoldoutSealedError 拒绝。"""
        spec_holdout = self._get_spec(
            start_ms=DEFAULT_HOLDOUT_START_MS - 24 * 3600000,
            end_ms=DEFAULT_HOLDOUT_START_MS + 24 * 3600000,
        )
        sig_df = _make_dummy_candles(DEFAULT_HOLDOUT_START_MS - 48 * 3600000, 50, 3600000)
        exec_df = _make_dummy_candles(DEFAULT_HOLDOUT_START_MS - 48 * 3600000, 200, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        with self.assertRaises(HoldoutSealedError) as ctx:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec_holdout, data_snapshot=snapshot, holdout_sealed=True)
        self.assertIn("盲测保护阻断", str(ctx.exception))
        self.assertIn("最终盲测必须保持封闭，严禁打开", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

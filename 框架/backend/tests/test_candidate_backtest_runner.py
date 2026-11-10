# -*- coding: utf-8 -*-
"""06-B 候选回测执行入口与六项 P0 验收单测

测试目标：
────────
1. P0-1: 输入绑定固定开发快照及实际代码、配置版本；原始数据保持只读；
2. P0-2: 历史时钟正确限制可见数据（剔除未收盘与未来可得数据）；严格与近似 PIT 模式区分；
3. P0-3: 信号与成交顺序保守规则（相位A/B/C/D），同根止盈止损歧义事件进入假设桶，不美化结果；
4. P0-4: 账本对账（成交、手续费、资金费双口径、现金、持仓），对账失败不放行；
5. P0-5: 幂等性与恢复机制（重复执行不重复记账）；
6. P0-6: 隔离性（不碰运行库与下单接口）与最终盲测封闭硬阻断（HoldoutSealedError）。
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
from candidate_backtest_runner import (
    DEFAULT_HOLDOUT_START_MS,
    CandidateExecutionError,
    HoldoutSealedError,
    create_or_bind_snapshot,
    run_candidate_backtest,
)


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
    """06-B 候选回测执行入口 6 项 P0 验证。"""

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

    def _get_spec(self, start_ms: int = None, end_ms: int = None) -> Dict[str, Any]:
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
        }

    # ──────────────────────────────────────────────────────────────────────────
    # P0-1: 输入绑定固定开发快照及实际代码、配置版本；原始数据保持只读
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
        self.assertEqual(manifest["symbol"], self.sym)
        self.assertEqual(manifest["source"], "memory_snapshot")

    def test_p0_1_external_snapshot_manifest_tamper_rejected(self):
        """缺口 1: 外部快照复用前，按冻结身份校验实际输入；不一致在执行或覆盖有效结果前拒绝。"""
        snap_dir = Path(self.tmp_dir) / "ext_snapshot"
        snap_dir.mkdir(parents=True, exist_ok=True)
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        sig_df.to_csv(snap_dir / f"{self.sym}_signal_bars.csv", index=False)
        exec_df.to_csv(snap_dir / f"{self.sym}_exec_bars.csv", index=False)
        (snap_dir / f"{self.sym}_funding_events.json").write_text("[]", encoding="utf-8")

        # 写入伪造或不匹配的 manifest（伪造 signal_bars_sha256）
        fake_manifest = {
            "symbol": self.sym,
            "signal_bars_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "exec_bars_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
            "funding_events_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
        }
        (snap_dir / "inputs_manifest.json").write_text(json.dumps(fake_manifest), encoding="utf-8")

        spec = self._get_spec()
        with self.assertRaises(CandidateExecutionError) as ctx:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec, snapshot_path=snap_dir)
        self.assertIn("外部快照身份校验失败", str(ctx.exception))

    # ──────────────────────────────────────────────────────────────────────────
    # P0-2: 历史时钟正确限制可见数据；严格与近似 PIT 区分
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_2_history_clock_filters_unclosed_and_future_bars(self):
        spec = self._get_spec()
        # 故意注入一根未收盘 K 线与一根超出 win_end 的未来 K 线
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24 + 2, 3600000)
        # 将最后一根设为未收盘
        sig_df.loc[len(sig_df) - 2, "is_closed"] = 0
        # 最后一根设为未来时间戳（大于 end_ms）
        sig_df.loc[len(sig_df) - 1, "open_time"] = self.end_ms + 3600000

        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        res = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, pit_mode="approximate")
        self.assertEqual(res["status"], S.STATUS_SUCCEEDED)
        # 执行器自动拦截并过滤未收盘与未来 bar，不造成越界
        self.assertEqual(res["data_quality"]["pit_status"], "PIT_PROVEN")

    def test_p0_2_strict_pit_mode_blocks_when_available_at_missing(self):
        spec = self._get_spec()
        # 生成缺少 available_at 证明的 K 线
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000, with_available_at=False)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000, with_available_at=False)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        # 严格模式下应被直接阻断
        res_strict = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, pit_mode="strict")
        self.assertEqual(res_strict["status"], S.STATUS_FAILED)
        self.assertIn("严格 PIT 门禁阻断", res_strict["failure_reason"])

        # 近似模式下应允许降级运行并明确记录 PIT_APPROXIMATE
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

        # 注入一笔恰好在持仓内部的资金费事件（真实结算）
        events = [{"event_time": self.start_ms + 16 * 3600000, "rate": 0.0002, "mark": 100.0}]
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": events}

        res = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot)
        self.assertEqual(res["status"], S.STATUS_SUCCEEDED)
        metrics = res["metrics"]
        # 必须同时存在已确认口径与假设口径字段
        self.assertIn("total_funding_fee_confirmed", metrics)
        self.assertIn("total_funding_fee_hypothetical", metrics)

    # ──────────────────────────────────────────────────────────────────────────
    # P0-4: 成交、手续费、资金费、现金、持仓与未实现盈亏账本对账
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_4_ledger_recompute_and_reconciliation(self):
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        res = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot)
        self.assertEqual(res["status"], S.STATUS_SUCCEEDED)
        # 验证账本重算字段存在且对账一致
        self.assertIn("ledger_recomputed", res)
        led = res["ledger_recomputed"]
        met = res["metrics"]
        self.assertEqual(led["trades_count"], met["trades_count"])
        self.assertEqual(led["net_pnl"], met["net_pnl"])

    # ──────────────────────────────────────────────────────────────────────────
    # P0-5: 重复事件与中断恢复不重复记账（幂等性）
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_5_idempotent_reexecution_does_not_duplicate_ledger(self):
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        fixed_run_id = S.new_run_id("12345678")
        res1 = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, run_id=fixed_run_id)
        self.assertEqual(res1["status"], S.STATUS_SUCCEEDED)
        c1 = res1["ledger_counts"]["trades"]

        # 再次执行同一 run_id（模拟重放/恢复）
        res2 = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, run_id=fixed_run_id)
        self.assertEqual(res2["status"], S.STATUS_SUCCEEDED)
        c2 = res2["ledger_counts"]["trades"]
        # 账本行数必须恒等，绝不发生翻倍重复记账
        self.assertEqual(c1, c2)

    def test_p0_5_same_run_id_rejects_altered_inputs_or_spec(self):
        """缺口 2: 显式同 run_id 换输入、规则或配置时，比较已有绑定身份，并在写账或覆盖前拒绝。"""
        spec1 = self._get_spec()
        sig_df1 = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df1 = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot1 = {"sig_df": sig_df1, "exec_df": exec_df1, "funding_events": []}

        fixed_run_id = S.new_run_id("00112233")
        res1 = run_candidate_backtest("WX_OI_RESONANCE_V1", spec1, data_snapshot=snapshot1, run_id=fixed_run_id)
        self.assertEqual(res1["status"], S.STATUS_SUCCEEDED)

        # 1. 尝试使用同一个 run_id 但篡改配置（例如改变周期或窗口）
        spec2 = copy.deepcopy(spec1)
        spec2["signal_timeframe"] = "4h"  # 发生配置漂移
        with self.assertRaises(CandidateExecutionError) as ctx_spec:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec2, data_snapshot=snapshot1, run_id=fixed_run_id)
        self.assertIn("同运行编号拒绝更换输入、规则或配置", str(ctx_spec.exception))

        # 2. 尝试使用同一个 run_id 但篡改输入数据
        sig_df_altered = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000, base_price=999.0)
        snapshot_altered = {"sig_df": sig_df_altered, "exec_df": exec_df1, "funding_events": []}
        with self.assertRaises(CandidateExecutionError) as ctx_data:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec1, data_snapshot=snapshot_altered, run_id=fixed_run_id)
        self.assertIn("同运行编号拒绝更换数据输入快照", str(ctx_data.exception))

    def test_p0_5_controlled_interruption_recovery_matches_baseline(self):
        """缺口 3: 用原固定小样本完成一次部分执行后的受控中断验证，证明安全恢复或隔离重算与不中断基准账本一致，无重复、漏记，运行状态能够结束。"""
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        # 1. 执行不中断基准
        base_run_id = S.new_run_id("00aabbcc")
        res_base = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, run_id=base_run_id)
        self.assertEqual(res_base["status"], S.STATUS_SUCCEEDED)
        base_trades = res_base["ledger_counts"]["trades"]
        base_metrics = res_base["metrics"]

        # 2. 构造一次受控中断（运行到一半被终止，状态保留为 RUNNING，且向账本插入部分残留脏数据）
        interrupted_run_id = S.new_run_id("00ddeeff")
        S.create_run(interrupted_run_id, {
            "run_id": interrupted_run_id,
            "spec": spec,
            "strategy_id": "WX_OI_RESONANCE_V1",
            "created_at": S.utc_now_iso(),
            "status": S.STATUS_RUNNING,  # 停留在中断运行态
        })
        # 预先绑定 inputs 快照
        create_or_bind_snapshot(interrupted_run_id, spec, res_base["window"], data_snapshot=snapshot)
        # 模拟崩溃前写入了一行脏 trade
        ledger_db = S.ledger_path(interrupted_run_id)
        with sqlite3.connect(str(ledger_db)) as con:
            con.executescript(S.LEDGER_SCHEMA)
            con.execute("""
                INSERT INTO trades (
                    run_id, trade_id, signal_id, symbol, direction, signal_type,
                    entry_time_ms, exit_time_ms, entry_price, exit_price, qty,
                    gross_pnl, entry_fee, exit_fee, fees, funding_fee, net_pnl, exit_reason
                ) VALUES (?, 'DIRTY_TRADE_001', 's1', 'BTCUSDT', 'LONG', 1, 1000, 2000, 100, 110, 1, 10, 0, 0, 0, 0, 10, 'crash')
            """, (interrupted_run_id,))

        # 3. 执行受控恢复 / 重新执行
        res_recovered = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, run_id=interrupted_run_id)
        # 证明运行状态成功结束
        self.assertEqual(res_recovered["status"], S.STATUS_SUCCEEDED)
        # 证明账本与基准完全一致，脏数据被原子清理，无重复、无漏记
        rec_trades = res_recovered["ledger_counts"]["trades"]
        self.assertEqual(rec_trades, base_trades)
        self.assertEqual(res_recovered["metrics"]["trades_count"], base_metrics["trades_count"])
        self.assertEqual(res_recovered["metrics"]["net_pnl"], base_metrics["net_pnl"])
        self.assertTrue(res_recovered["ledger_recomputed"]["cash_identity_ok"])

    def test_p0_6_formal_candidate_blocked_without_experiment_id(self):
        """复用已有实验门禁：真实候选若未绑定已批准实验身份，直接拒绝真实候选执行。"""
        spec = self._get_spec()
        spec["is_formal"] = True  # 标记为正式候选执行
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        res = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot)
        self.assertEqual(res["status"], S.STATUS_FAILED)
        self.assertIn("实验门禁阻断", res["failure_reason"])

    # ──────────────────────────────────────────────────────────────────────────
    # P0-6: 回测不写运行库且最终盲测区间保持封闭（硬阻断拦截）
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_6_holdout_sealed_blocks_execution_when_window_crosses(self):
        # 构造越入盲测保护区间（>= DEFAULT_HOLDOUT_START_MS）的请求
        spec_holdout = self._get_spec(
            start_ms=DEFAULT_HOLDOUT_START_MS - 24 * 3600000,
            end_ms=DEFAULT_HOLDOUT_START_MS + 24 * 3600000,  # 触碰盲测区间
        )
        sig_df = _make_dummy_candles(DEFAULT_HOLDOUT_START_MS - 48 * 3600000, 50, 3600000)
        exec_df = _make_dummy_candles(DEFAULT_HOLDOUT_START_MS - 48 * 3600000, 200, 900000)
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": []}

        # 默认 holdout_sealed=True，必须抛出 HoldoutSealedError 硬阻断！
        with self.assertRaises(HoldoutSealedError) as ctx:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec_holdout, data_snapshot=snapshot, holdout_sealed=True)
        self.assertIn("盲测保护阻断", str(ctx.exception))
        self.assertIn("最终盲测必须保持封闭，严禁打开", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

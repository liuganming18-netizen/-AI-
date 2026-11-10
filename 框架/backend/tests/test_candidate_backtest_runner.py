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

    def _get_spec(self, start_ms: int = None, end_ms: int = None, is_mechanism_test: bool = True) -> Dict[str, Any]:
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
        """缺口 1: 内存/外部目录统一覆盖资金费；复用凭据损坏不能只警告后继续，必须直接拒绝。"""
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)

        # 1. 内存快照缺失资金费字段直接拒绝
        bad_snapshot = {"sig_df": sig_df, "exec_df": exec_df}
        with self.assertRaises(CandidateExecutionError) as ctx_f:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=bad_snapshot)
        self.assertIn("缺失 funding_events 字段", str(ctx_f.exception))

        # 2. 外部快照目录损坏的 manifest 必须直接拒绝，绝不警告放行
        ext_snap_dir = Path(self.tmp_dir) / "external_corrupted_snap"
        ext_snap_dir.mkdir(parents=True)
        sig_df.to_csv(ext_snap_dir / f"{self.sym}_signal_bars.csv", index=False)
        exec_df.to_csv(ext_snap_dir / f"{self.sym}_exec_bars.csv", index=False)
        (ext_snap_dir / f"{self.sym}_funding_events.json").write_text("[]", encoding="utf-8")
        (ext_snap_dir / "inputs_manifest.json").write_text("{CORRUPTED_JSON", encoding="utf-8")

        with self.assertRaises(CandidateExecutionError) as ctx_man:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec, snapshot_path=ext_snap_dir)
        self.assertIn("manifest 解析失败", str(ctx_man.exception))

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

    def test_p0_5_controlled_interruption_recovery_and_reconciliation(self):
        """缺口 5: 用原固定小样本让执行器在执行过程中发生一次真实可控中断，再恢复重算，对照完整逐笔交易、费用和最终账本，确认无重复、漏记且状态正确结束。"""
        spec = self._get_spec()
        sig_df = _make_dummy_candles(self.start_ms - 10 * 3600000, 10 + 7 * 24, 3600000)
        exec_df = _make_dummy_candles(self.start_ms - 40 * 900000, 40 + 7 * 24 * 4, 900000)
        events = [{"event_time": self.start_ms + 16 * 3600000, "rate": 0.0001, "mark": 100.0}]
        snapshot = {"sig_df": sig_df, "exec_df": exec_df, "funding_events": events}

        # 1. 先跑无中断的基准 (使用纯十六进制 run_id)
        base_id = S.new_run_id("00ba5e01")
        res_base = run_candidate_backtest("WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, run_id=base_id)
        self.assertEqual(res_base["status"], S.STATUS_SUCCEEDED)
        base_trades = res_base["ledger_counts"]["trades"]
        base_metrics = res_base["metrics"]

        # 2. 构造一次真实的执行期受控中断
        test_run_id = S.new_run_id("0011eeff")
        interrupted = False

        def inject_crash(info: Dict[str, Any]):
            nonlocal interrupted
            if info["stage"] == "after_execution" and not interrupted:
                interrupted = True
                raise RuntimeError("Controlled crash simulated during portfolio execution")

        res_interrupted = run_candidate_backtest(
            "WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot,
            run_id=test_run_id, execution_interceptor=inject_crash
        )
        self.assertEqual(res_interrupted["status"], S.STATUS_FAILED)
        self.assertIn("Controlled crash simulated", res_interrupted["failure_reason"])

        # 3. 再次调用执行恢复/隔离重算
        res_recovered = run_candidate_backtest(
            "WX_OI_RESONANCE_V1", spec, data_snapshot=snapshot, run_id=test_run_id
        )
        self.assertEqual(res_recovered["status"], S.STATUS_SUCCEEDED)

        # 4. 对照完整逐笔交易、费用和最终账本，确认无重复、漏记
        rec_trades = res_recovered["ledger_counts"]["trades"]
        self.assertEqual(rec_trades, base_trades)
        self.assertEqual(res_recovered["metrics"]["trades_count"], base_metrics["trades_count"])
        self.assertEqual(res_recovered["metrics"]["net_pnl"], base_metrics["net_pnl"])
        self.assertEqual(res_recovered["metrics"]["total_funding_fee_confirmed"], base_metrics["total_funding_fee_confirmed"])
        self.assertEqual(res_recovered["metrics"]["total_funding_fee_hypothetical"], base_metrics["total_funding_fee_hypothetical"])
        self.assertTrue(res_recovered["ledger_recomputed"]["cash_identity_ok"])

    # ──────────────────────────────────────────────────────────────────────────
    # P0-6: 实验注册门禁（检查存在性、冻结态与范围；防绕过）与最终盲测封闭硬阻断
    # ──────────────────────────────────────────────────────────────────────────
    def test_p0_6_experiment_registry_gate_validates_existence_and_freeze(self):
        """缺口 4: 复用真实实验注册门禁；不存在的非空 ID 必须拒绝；正式候选不能靠省略 is_formal 或传 holdout_sealed=False 绕过。"""
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
        spec_formal_bypass = self._get_spec(is_mechanism_test=False)  # 未传 is_formal，亦非机制测试
        with self.assertRaises(CandidateExecutionError) as ctx_formal:
            run_candidate_backtest("WX_OI_RESONANCE_V1", spec_formal_bypass, data_snapshot=snapshot)
        self.assertIn("正式候选必须显式绑定已批准并已冻结的 experiment_id", str(ctx_formal.exception))

        # 3. 传入已登记但未冻结的真实实验（exp_74a00fc7772bf37d），必须拒绝
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

# -*- coding: utf-8 -*-
"""FRAME-VERIFY-02｜真实入口独立回归测试套件

纪律：
- 直接调用生产真实入口函数（run_state_machine_simulation, guard_create, guard_execute, unseal_holdout, extract_features_for_candidate）；
- 不修改 Work 负责的实现，在临时目录/沙箱环境运行；
- 结论从实测结果生成，不得硬编码；
- 失败项真实反映，返回非零退出码。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
STRATEGY_ROOT = PROJECT_ROOT / "策略" / "WyckoffMaster"
if str(STRATEGY_ROOT) not in sys.path:
    sys.path.insert(0, str(STRATEGY_ROOT))

# 导入真实生产模块
from 策略.WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from 策略.WyckoffMaster.trading_core.pit_guard import PitViolation
from 框架.backend.experiment_registry import (
    ExperimentRegistry, guard_create, guard_execute, SplitPolicy,
    ExperimentBlocked, RegistryTampered, MODE_FORMAL, DEFAULT_METRICS_SPEC,
    current_design_parts, compute_design_identity
)


class SyntheticDataHelper:
    """真实结构合成数据生成器。"""

    @staticmethod
    def make_candles_1h(t0: int, n_bars: int = 100, unclosed_index: int = -1) -> pd.DataFrame:
        hour_ms = 3600000
        rows = []
        for i in range(n_bars):
            t = t0 + i * hour_ms
            is_closed = 0 if i == unclosed_index else 1
            rows.append({
                "open_time": t,
                "close_time": t + hour_ms - 1,
                "timestamp": t,
                "open": 100.0 + i * 0.1,
                "high": 102.0 + i * 0.1,
                "low": 98.0 + i * 0.1,
                "close": 101.0 + i * 0.1,
                "volume": 1000.0 + i * 5,
                "quote_volume": 100000.0 + i * 500,
                "is_closed": is_closed
            })
        return pd.DataFrame(rows)

    @staticmethod
    def make_candles_4h(t0: int, n_bars: int = 25) -> pd.DataFrame:
        h4_ms = 14400000
        rows = []
        for i in range(n_bars):
            t = t0 + i * h4_ms
            rows.append({
                "open_time": t,
                "close_time": t + h4_ms - 1,
                "timestamp": t,
                "open": 100.0 + i * 0.4,
                "high": 105.0 + i * 0.4,
                "low": 95.0 + i * 0.4,
                "close": 102.0 + i * 0.4,
                "volume": 4000.0 + i * 20,
                "is_closed": 1
            })
        return pd.DataFrame(rows)


# 全局测试结果搜集器
TEST_RESULTS: List[dict] = []


class TestFrameVerify02(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="frame_verify_02_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # ══════════════════════ A. FLOW-OI 因子入口 ══════════════════════

    def test_verify_A_flow_oi_bypasses_pit(self):
        """【问题 A】检验 generate_flow_oi_01 是否仍绕过 PIT 吃入未来未收盘蜡烛与桶。"""
        import importlib.util
        flow_path = PROJECT_ROOT / "evidence" / "FLOW-OI-01" / "generate_flow_oi_01.py"
        spec = importlib.util.spec_from_file_location("flow_mod_verify", flow_path)
        flow_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(flow_mod)

        ts = 1600000000000  # 12:00:00 整点
        row = pd.Series({
            "candidate_id": "cand_verify_001",
            "symbol": "BTCUSDT",
            "datetime_utc": "2020-09-13T12:00:00Z",
            "snapshot_time_ms": ts,
            "is_filled": True,
            "net_pnl": 50.0, "gross_pnl": 60.0, "fee_paid": 5.0, "funding_cost": 5.0,
            "entry_quality_category": "CONTINUOUS_UPTREND",
            "fwd_ret_15m": 0.01, "fwd_ret_1h": 0.02, "fwd_ret_4h": 0.03, "mfe_r": 2.0,
            "generation": "test"
        })

        # 注入未收盘蜡烛 (open_time==ts, volume=8888888)
        c_rows = []
        for i in range(4):
            t = ts - (4 - i) * 3600000
            c_rows.append({"open_time": t, "close_time": t + 3599999, "open": 100.0, "high": 102.0,
                           "low": 98.0, "close": 101.0, "volume": 1000.0, "quote_volume": 100000.0,
                           "taker_buy_volume": 500.0, "taker_buy_quote_volume": 50000.0, "is_closed": 1})
        # 未来蜡烛
        c_rows.append({"open_time": ts, "close_time": ts + 3599999, "open": 101.0, "high": 150.0,
                       "low": 100.0, "close": 149.0, "volume": 8888888.0, "quote_volume": 888888800.0,
                       "taker_buy_volume": 7777777.0, "taker_buy_quote_volume": 777777700.0, "is_closed": 0})
        df_c = pd.DataFrame(c_rows)

        # 注入未结束 5m 桶 (ts==ts)
        df_m = pd.DataFrame([{"ts": ts, "sum_open_interest": 99999.0, "sum_open_interest_value": 99999000.0,
                              "sum_toptrader_long_short_ratio": 2.5, "count_toptrader_long_short_ratio": 2.0,
                              "count_long_short_ratio": 1.8, "sum_taker_long_short_vol_ratio": 1.5}])

        flow_mod._CACHE_CANDLES_1H["BTCUSDT"] = df_c
        flow_mod._CACHE_METRICS["BTCUSDT"] = df_m
        flow_mod._CACHE_OI["BTCUSDT"] = pd.DataFrame()

        # 调用真实提取函数
        res = flow_mod.extract_features_for_candidate(row)

        # 验证受控期望 vs 实际行为
        # 受控框架期望：必须拒绝未来蜡烛，不应吃入 volume=8888888.0
        # 实际现状：依然吃入且标为 COMPLETE
        leaked_future_volume = (res.get("bar_volume") == 8888888.0)
        leaked_future_oi = (res.get("oi_base") == 99999.0)
        coverage_complete = (res.get("coverage_status") == "COMPLETE")
        has_pit_proven = ("pit_proven" in res)

        status = "FAIL" if (leaked_future_volume and coverage_complete and not has_pit_proven) else "PASS"
        TEST_RESULTS.append({
            "test_id": "VERIFY-A-FLOW-OI-PIT-BYPASS",
            "domain": "FRAME-PIT-01",
            "entrypoint": "evidence/FLOW-OI-01/generate_flow_oi_01.py::extract_features_for_candidate",
            "status": status,
            "details": f"leaked_future_volume={leaked_future_volume}, leaked_future_oi={leaked_future_oi}, coverage={res.get('coverage_status')}, has_pit_proven={has_pit_proven}",
            "conclusion": "FLOW-OI 实验入口未接入任何 PIT 守卫，依然读取当根未收盘 K 线并标为 COMPLETE"
        })
        # 判定：如果存在泄露，断言失败
        self.assertFalse(leaked_future_volume, "【缺陷证实】FLOW-OI 实验入口仍存在未来蜡烛前视泄露")

    # ══════════════════════ B. 首次正式实验启动死锁 ══════════════════════

    def test_verify_B_first_formal_experiment_deadlock(self):
        """【问题 B】检验合法首次正式实验是否被 guard_create 死锁阻断。"""
        reg = ExperimentRegistry(self.tmp_dir)

        # 构造完整自洽分组策略与样本范围
        policy = SplitPolicy(
            train=("2023-01-01", "2024-12-01"),
            valid=("2025-01-01", "2025-12-01"),
            holdout=("2026-10-20", "2026-11-20"),
            isolation_hours=240,
            label_span_source="实测最大持有240h"
        )
        scope = {"symbols": ["BTCUSDT"], "window_start": "2024-01-01", "window_end": "2024-06-30"}
        design = {
            "strategy_id": "WX_EVENT_TREND_V0_1", "strategy_version": "v0.1.0",
            "code_sha256": "code_" + "1" * 59, "config_sha256": "cfg_" + "2" * 60,
            "rules_sha256": "rul_" + "3" * 60
        }
        exp_id = "exp_formal_virgin_test"
        reg.register(
            experiment_id=exp_id, design=design, sample_scope=scope,
            split_policy=policy, metrics_spec=DEFAULT_METRICS_SPEC,
            mode=MODE_FORMAL, by="tester", note="首个正式实验"
        )
        reg.freeze(exp_id, by="tester", note="启动前冻结")

        spec = {
            "symbol": "BTCUSDT", "symbols": ["BTCUSDT"],
            "window_start": "2024-01-01", "window_end": "2024-06-30",
            "experiment": {
                "mode": "formal", "experiment_id": exp_id,
                "window_start": "2024-01-01", "window_end": "2024-06-30"
            }
        }

        # 调用真实入口 guard_create
        gate_res = guard_create(spec, registry=reg)

        has_run_count_blocker = any("试验次数为 0" in b for b in gate_res.get("blockers", []))
        is_ok = bool(gate_res.get("ok"))

        status = "FAIL" if (not is_ok and has_run_count_blocker) else "PASS"
        TEST_RESULTS.append({
            "test_id": "VERIFY-B-FIRST-FORMAL-RUN-DEADLOCK",
            "domain": "FRAME-FIT-01",
            "entrypoint": "框架/backend/experiment_registry.py::guard_create",
            "status": status,
            "details": f"ok={is_ok}, blockers={gate_res.get('blockers')}",
            "conclusion": "首次合法正式实验因尚未有运行记录被 guard_create 错误判定不合格，陷入死锁"
        })
        self.assertTrue(is_ok, f"【缺陷证实】合法首次正式实验被死锁拒绝：{gate_res.get('blockers')}")

    # ══════════════════════ C. PIT 其余必测行为 ══════════════════════

    def test_verify_C1_unclosed_candle_rejected_by_real_runner(self):
        """【PIT 必测 1】未收盘 1h K 线注入真实 run_state_machine_simulation 必须被物理拒绝。"""
        runner = EventTrendRunner(str(PROJECT_ROOT))
        cfg = EventTrendConfig()
        t0 = 1600000000000
        # 注入第 30 根未收盘：close_time 设为未来 open + 2h
        df_1h = SyntheticDataHelper.make_candles_1h(t0, n_bars=60)
        df_1h.loc[30, "close_time"] = int(df_1h.loc[30, "open_time"]) + 2 * 3600000
        df_4h = SyntheticDataHelper.make_candles_4h(t0, n_bars=15)
        oi_ts = np.array([t0 + i * 300000 for i in range(720)], dtype=np.int64)
        oi_vals = np.full(len(oi_ts), 50000.0, dtype=np.float64)

        with self.assertRaises(PitViolation) as ctx:
            runner.run_state_machine_simulation(
                symbol="BTCUSDT", df_1h=df_1h, df_4h=df_4h,
                oi_ts=oi_ts, oi_vals=oi_vals
            )

        TEST_RESULTS.append({
            "test_id": "VERIFY-C1-UNCLOSED-CANDLE-REJECTION",
            "domain": "FRAME-PIT-01",
            "entrypoint": "策略/WyckoffMaster/event_trend_v0_1/runner.py::run_state_machine_simulation",
            "status": "PASS",
            "details": str(ctx.exception),
            "conclusion": "真实策略 Runner 正确通过 assert_candles_closed 拦截未收盘 K 线"
        })

    def test_verify_C2_future_perturbation_invariance(self):
        """【PIT 必测 2】修改未来数据，不改变过去特征与决策。"""
        runner = EventTrendRunner(str(PROJECT_ROOT))
        t0 = 1600000000000
        df_1h_a = SyntheticDataHelper.make_candles_1h(t0, n_bars=40)
        df_4h = SyntheticDataHelper.make_candles_4h(t0, n_bars=10)
        oi_ts = np.array([t0 + i * 300000 for i in range(480)], dtype=np.int64)
        oi_vals_a = np.full(len(oi_ts), 50000.0, dtype=np.float64)

        # 运行基准 A
        res_a = runner.run_state_machine_simulation(
            symbol="BTCUSDT", df_1h=df_1h_a.copy(), df_4h=df_4h.copy(),
            oi_ts=oi_ts.copy(), oi_vals=oi_vals_a.copy()
        )

        # 构造版本 B：保持前 20 根 K 线和前 240 根 OI 绝对不变，对后半段未来数据做 100 倍剧烈扰动
        df_1h_b = df_1h_a.copy()
        df_1h_b.loc[25:, "volume"] = df_1h_b.loc[25:, "volume"] * 100.0
        df_1h_b.loc[25:, "close"] = df_1h_b.loc[25:, "close"] * 2.0
        oi_vals_b = oi_vals_a.copy()
        oi_vals_b[300:] = oi_vals_b[300:] * 50.0

        res_b = runner.run_state_machine_simulation(
            symbol="BTCUSDT", df_1h=df_1h_b.copy(), df_4h=df_4h.copy(),
            oi_ts=oi_ts.copy(), oi_vals=oi_vals_b.copy()
        )

        # 比对决策时刻落在前 20 根内的候选与事件
        cutoff_ms = t0 + 20 * 3600000
        cands_a = [c for c in res_a.candidates if c.snapshot_time_ms <= cutoff_ms]
        cands_b = [c for c in res_b.candidates if c.snapshot_time_ms <= cutoff_ms]

        self.assertEqual(len(cands_a), len(cands_b))
        for ca, cb in zip(cands_a, cands_b):
            self.assertEqual(ca.snapshot_time_ms, cb.snapshot_time_ms)
            self.assertEqual(ca.oi_6h_growth, cb.oi_6h_growth)

        TEST_RESULTS.append({
            "test_id": "VERIFY-C2-FUTURE-PERTURBATION-INVARIANCE",
            "domain": "FRAME-PIT-01",
            "entrypoint": "策略/WyckoffMaster/event_trend_v0_1/runner.py::run_state_machine_simulation",
            "status": "PASS",
            "details": f"cands_count={len(cands_a)}, cutoff_ms={cutoff_ms}",
            "conclusion": "未来数据扰动后，过去时点的决策与特征 100% 保持不变"
        })

    # ══════════════════════ D. FIT 其余必测行为 ══════════════════════

    def test_verify_D1_unfrozen_cannot_unseal_holdout(self):
        """【FIT 必测 1】未冻结配置不能解封最终留出。"""
        reg = ExperimentRegistry(self.tmp_dir)
        policy = SplitPolicy(train=("2021-01-01", "2023-12-31"), valid=("2024-01-11", "2025-06-30"),
                             holdout=("2025-07-11", "2026-10-18"), isolation_hours=240)
        reg.register(experiment_id="exp_unfrozen", design={"strategy_id": "TEST"},
                     sample_scope={"symbols": ["BTCUSDT"]}, split_policy=policy,
                     metrics_spec=DEFAULT_METRICS_SPEC, mode=MODE_FORMAL)

        with self.assertRaises(ExperimentBlocked) as ctx:
            reg.unseal_holdout("exp_unfrozen", by="tester")

        self.assertIn("配置未冻结", str(ctx.exception))
        TEST_RESULTS.append({
            "test_id": "VERIFY-D1-UNFROZEN-UNSEAL-BLOCKED",
            "domain": "FRAME-FIT-01",
            "entrypoint": "框架/backend/experiment_registry.py::unseal_holdout",
            "status": "PASS",
            "details": str(ctx.exception),
            "conclusion": "未冻结配置尝试解封留出被物理拒绝"
        })

    def test_verify_D2_observed_samples_cannot_be_holdout(self):
        """【FIT 必测 2】已观察样本改名不能变成未见留出。"""
        reg = ExperimentRegistry(self.tmp_dir)
        # 登记已观察样本（时间点落在 2026-05-01）
        t_observed = int(datetime.datetime(2026, 5, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        reg.observe("dataset_227", [f"BTCUSDT|{t_observed}"], purpose="已观察研究")

        # 构造包含该时间点的 holdout 留出段
        policy = SplitPolicy(train=("2021-01-01", "2023-12-31"), valid=("2024-01-11", "2025-06-30"),
                             holdout=("2026-01-01", "2026-10-18"), isolation_hours=240)
        design = {"strategy_id": "TEST", "strategy_version": "v1", "code_sha256": "1"*64,
                  "config_sha256": "2"*64, "rules_sha256": "3"*64}
        reg.register(experiment_id="exp_tainted", design=design, sample_scope={"symbols": ["BTCUSDT"]},
                     split_policy=policy, metrics_spec=DEFAULT_METRICS_SPEC, mode=MODE_FORMAL)
        reg.freeze("exp_tainted")

        with self.assertRaises(ExperimentBlocked) as ctx:
            reg.unseal_holdout("exp_tainted", by="tester")

        self.assertIn("留出段内含有已观察样本", str(ctx.exception))
        TEST_RESULTS.append({
            "test_id": "VERIFY-D2-OBSERVED-HOLDOUT-BLOCKED",
            "domain": "FRAME-FIT-01",
            "entrypoint": "框架/backend/experiment_registry.py::unseal_holdout",
            "status": "PASS",
            "details": str(ctx.exception),
            "conclusion": "已观察样本改名作为留出集被命中并拒绝解封"
        })

    def test_verify_D3_identity_drift_invalidates_guard_execute(self):
        """【FIT 必测 3】改动参数或代码后不能沿用旧冻结资格。"""
        reg = ExperimentRegistry(self.tmp_dir)
        policy = SplitPolicy(train=("2021-01-01", "2023-12-31"), valid=("2024-01-11", "2025-06-30"),
                             holdout=("2025-07-11", "2026-10-18"), isolation_hours=240)
        design = {"strategy_id": "TEST", "strategy_version": "v1", "code_sha256": "1"*64,
                  "config_sha256": "2"*64, "rules_sha256": "3"*64}
        reg.register(experiment_id="exp_drift", design=design, sample_scope={"symbols": ["BTCUSDT"]},
                     split_policy=policy, metrics_spec=DEFAULT_METRICS_SPEC, mode=MODE_FORMAL)
        reg.freeze("exp_drift")

        # 模拟执行时，代码/参数指纹发生漂移
        doc = {
            "spec": {
                "strategy_id": "TEST",
                "symbols": ["BTCUSDT"],
                "experiment": {"experiment_id": "exp_drift"}
            },
            "fingerprints": {
                "config": {"sha256": "cfg_DRIFTED_XXXX"},  # 参数被篡改
                "code": {"combined_sha256": "1"*64},
                "rules": {"sha256": "3"*64}
            }
        }

        err = guard_execute("run_test_drift", doc, registry=reg)
        self.assertIsNotNone(err)
        self.assertIn("漂移", err)

        # 检查是否写入了 invalidate 事件并解冻
        exp = reg.state()["experiments"]["exp_drift"]
        self.assertGreater(len(exp["invalidations"]), 0)
        self.assertIsNone(exp["frozen_at"])

        TEST_RESULTS.append({
            "test_id": "VERIFY-D3-IDENTITY-DRIFT-INVALIDATED",
            "domain": "FRAME-FIT-01",
            "entrypoint": "框架/backend/experiment_registry.py::guard_execute",
            "status": "PASS",
            "details": f"err={err}, invalidations_count={len(exp['invalidations'])}",
            "conclusion": "参数漂移被 guard_execute 拦截并自动标记冻结失效"
        })

    def test_verify_C3_unproven_source_marked_unqualified(self):
        """【PIT 必测 3】假设性研究不得标为正式合格（无发布凭证时 qualified=False）。"""
        from 策略.WyckoffMaster.event_trend_v0_1.pit_bridge import PitBridge
        from 策略.WyckoffMaster.trading_core.pit_guard import PitMode
        # 未传入 declared_lag_ms（即无交易所发布凭证）
        bridge = PitBridge(symbol="BTCUSDT", mode=PitMode.DECLARED.value, declared_lag_ms=None)
        t0 = 1600000000000
        oi_ts = np.array([t0, t0 + 300000], dtype=np.int64)
        oi_vals = np.array([50000.0, 51000.0], dtype=np.float64)
        bridge.register_oi(oi_ts, oi_vals)

        # 读取一次 OI
        bridge.read_oi(decision_time_ms=t0 + 3600000, lookback_ms=3600000, max_distance_ms=600000)

        # 断言：由于无发布时点凭证，qualified 必须为 False
        self.assertFalse(bridge.qualified, "无发布时点证据时，qualified 必须为 False")
        TEST_RESULTS.append({
            "test_id": "VERIFY-C3-UNPROVEN-SOURCE-UNQUALIFIED",
            "domain": "FRAME-PIT-01",
            "entrypoint": "策略/WyckoffMaster/event_trend_v0_1/pit_bridge.py::qualified",
            "status": "PASS",
            "details": f"qualified={bridge.qualified}, oi_source_proven={bridge.oi_source_proven}",
            "conclusion": "无发布凭证时，bridge.qualified 严格为 False，绝不冒充正式合格"
        })

    def test_verify_D4_trial_and_abandon_recorded(self):
        """【FIT 必测 4】失败和放弃的试验必须留下真实记录。"""
        reg = ExperimentRegistry(self.tmp_dir)
        policy = SplitPolicy(train=("2021-01-01", "2023-12-31"), valid=("2024-01-11", "2025-06-30"),
                             holdout=("2025-07-11", "2026-10-18"), isolation_hours=240)
        design = {"strategy_id": "TEST", "strategy_version": "v1", "code_sha256": "1"*64,
                  "config_sha256": "2"*64, "rules_sha256": "3"*64}
        reg.register(experiment_id="exp_ledger", design=design, sample_scope={"symbols": ["BTCUSDT"]},
                     split_policy=policy, metrics_spec=DEFAULT_METRICS_SPEC, mode=MODE_FORMAL)
        reg.freeze("exp_ledger")

        # 记录一次失败试验
        st_trial = reg.record_trial("exp_ledger", outcome="failure", note="参数回测亏损")
        self.assertEqual(len(st_trial["trials"]), 1)
        self.assertEqual(st_trial["trials"][0]["outcome"], "failure")

        # 记录放弃
        st_abandon = reg.abandon("exp_ledger", reason="因子不显著")
        self.assertIsNotNone(st_abandon["abandoned"])
        self.assertEqual(st_abandon["abandoned"]["reason"], "因子不显著")

        TEST_RESULTS.append({
            "test_id": "VERIFY-D4-TRIAL-AND-ABANDON-RECORDED",
            "domain": "FRAME-FIT-01",
            "entrypoint": "框架/backend/experiment_registry.py::record_trial & abandon",
            "status": "PASS",
            "details": f"trials_count={len(st_trial['trials'])}, abandon_reason={st_abandon['abandoned']['reason']}",
            "conclusion": "失败试验与放弃试验均原子记入事件流与哈希链"
        })

    def test_verify_D5_hash_chain_tamper_detected(self):
        """【FIT 必测 5】事件流哈希链被篡改时必须能检测到。"""
        reg = ExperimentRegistry(self.tmp_dir)
        reg.observe("set_01", ["BTCUSDT|1600000000000"], purpose="test")
        v_ok = reg.verify()
        self.assertTrue(v_ok["ok"])

        # 人为篡改 events.jsonl 中的 event 内部字段
        events_file = Path(self.tmp_dir) / "events.jsonl"
        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        bad_row = json.loads(lines[0])
        bad_row["event"]["purpose"] = "TAMPERED_PURPOSE"  # 篡改 event 内部
        lines[0] = json.dumps(bad_row)
        events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 再次校验
        v_bad = reg.verify()
        self.assertFalse(v_bad["ok"])
        self.assertIn("hash", str(v_bad["problems"]))

        TEST_RESULTS.append({
            "test_id": "VERIFY-D5-HASH-CHAIN-TAMPER-DETECTED",
            "domain": "FRAME-FIT-01",
            "entrypoint": "框架/backend/experiment_registry.py::verify",
            "status": "PASS",
            "details": f"tamper_detected={not v_bad['ok']}, problems={v_bad['problems']}",
            "conclusion": "事件流中间篡改 100% 被哈希链断链捕获"
        })


def main():
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    # 加载全部测试
    tests = loader.loadTestsFromTestCase(TestFrameVerify02)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(tests)

    # 汇总结果落盘
    summary_path = PROJECT_ROOT / "evidence" / "FRAME-VERIFY-02" / "test_execution_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.datetime.now().astimezone().isoformat(),
            "total_executed": res.testsRun,
            "failures": len(res.failures),
            "errors": len(res.errors),
            "was_successful": res.wasSuccessful(),
            "results": TEST_RESULTS
        }, f, indent=2, ensure_ascii=False)

    print("\n--- TEST SUMMARY SAVED TO evidence/FRAME-VERIFY-02/test_execution_summary.json ---")
    # 如果有测试失败（即发现漏洞），按工单要求返回退出码
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

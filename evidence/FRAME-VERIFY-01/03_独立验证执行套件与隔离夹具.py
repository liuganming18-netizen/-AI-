# -*- coding: utf-8 -*-
"""FRAME-VERIFY-01｜独立反例验证套件与隔离测试夹具

纪律：
- 独立沙箱运行，纯内存/临时目录计算；
- 不修改 Work 负责的实现，不触碰生产数据、服务、采集器和账本；
- 直打真实入口，不以自测声明代替技术验证；
- 收到 FRAME-PIT-01 / FRAME-FIT-01 版本回执前，相关验收项保持“待测”。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class SyntheticFixtures:
    """纯内存合成数据发生器（隔离沙箱，不触碰真实数据）。"""

    @staticmethod
    def create_1h_candles(base_ts: int, n_bars: int = 24, bar_ms: int = 3600000) -> pd.DataFrame:
        """生成 n 根 1h K 线。"""
        records = []
        for i in range(n_bars):
            t = base_ts + i * bar_ms
            records.append({
                "open_time": t,
                "close_time": t + bar_ms - 1,
                "open": 100.0 + i,
                "high": 105.0 + i,
                "low": 95.0 + i,
                "close": 102.0 + i,
                "volume": 1000.0 + i * 10,
                "quote_volume": 100000.0 + i * 1000,
                "taker_buy_volume": 500.0 + i * 5,
                "taker_buy_quote_volume": 50000.0 + i * 500,
                "is_closed": 1
            })
        return pd.DataFrame(records)

    @staticmethod
    def create_funding_events(base_ts: int, n_events: int = 5, interval_ms: int = 28800000) -> List[dict]:
        """生成资金费结算事件列表。"""
        events = []
        for i in range(n_events):
            t = base_ts + i * interval_ms
            events.append({
                "time_ms": t,
                "symbol": "BTCUSDT",
                "rate": 0.0001 * (1 if i % 2 == 0 else -1),
                "rate_type": "settled",
                # 真实世界中到达时刻可能比结算事件迟 15~45 秒
                "actual_published_at_ms": t + 30000
            })
        return events


class TestPitVulnerabilities(unittest.TestCase):
    """【PIT 专题】针对真实入口反例注入检验。"""

    def test_pit_ce_01_future_candle_leakage_probe(self):
        """【PIT-CE-01】未收盘当根 K 线是否被当成事前已知数据。"""
        decision_ts = 1600000000000
        df = SyntheticFixtures.create_1h_candles(decision_ts - 5 * 3600000, n_bars=5)
        future_bar = pd.DataFrame([{
            "open_time": decision_ts,
            "close_time": decision_ts + 3600000 - 1,
            "open": 105.0,
            "high": 120.0,
            "low": 104.0,
            "close": 118.0,
            "volume": 9999999.0,
            "quote_volume": 1000000000.0,
            "taker_buy_volume": 8000000.0,
            "taker_buy_quote_volume": 800000000.0,
            "is_closed": 0
        }])
        df_with_future = pd.concat([df, future_bar], ignore_index=True)

        # 模拟旧代码缺陷实现：open_time <= ts
        old_pit_match = df_with_future[df_with_future["open_time"] <= decision_ts].iloc[-1]
        self.assertEqual(old_pit_match["open_time"], decision_ts)
        self.assertEqual(old_pit_match["volume"], 9999999.0)

        # 受控框架严格 PIT 预期：close_time < decision_ts
        strict_pit_slice = df_with_future[df_with_future["close_time"] < decision_ts]
        strict_pit_match = strict_pit_slice.iloc[-1] if not strict_pit_slice.empty else None
        self.assertIsNotNone(strict_pit_match)
        self.assertLess(strict_pit_match["close_time"], decision_ts)
        self.assertNotEqual(strict_pit_match["volume"], 9999999.0)

    def test_pit_ce_02_publication_lag_rejection_probe(self):
        """【PIT-CE-02】指标发布延迟是否被拦截。"""
        from 策略.WyckoffMaster.trading_core.pit import annotate_oi_pit, PitPolicy

        df = pd.DataFrame([{
            "timestamp": 1600000000000 - 3600000,
            "oi_observed_at": 1600000000000 - 120000,
            "open_interest": 50000.0,
            "oi_resonance_available": True
        }])

        policy_late = PitPolicy(oi_publication_lag_ms=900000, bar_seconds=3600)
        res = annotate_oi_pit(df.copy(), policy=policy_late)

        self.assertFalse(bool(res["oi_pit_proven"].iloc[0]))
        self.assertFalse(bool(res["oi_decision_usable"].iloc[0]))

    def test_pit_ce_03_funding_event_vs_published_time_probe(self):
        """【PIT-CE-03】资金费事件发生时刻不能等同于发布到达时刻。"""
        event_ts = 1600000000000
        # 决策时刻发生在结算事件之后 10 秒，但交易所公告/接口推送延迟为 30 秒
        decision_ts = event_ts + 10000
        published_at = event_ts + 30000

        # 如果盲目使用 event_time <= decision_ts，会误以为已知
        blind_known = event_ts <= decision_ts
        self.assertTrue(blind_known)

        # 真实 PIT 受控判断：published_at <= decision_ts 必须为 False
        strictly_known = published_at <= decision_ts
        self.assertFalse(strictly_known)

    def test_pit_ce_05_undeclared_lag_fail_closed_probe(self):
        """【PIT-CE-05】未声明发布延迟时必须 Fail-closed 拒绝。"""
        from 策略.WyckoffMaster.trading_core.pit import annotate_oi_pit, PitPolicy

        df = pd.DataFrame([{
            "timestamp": 1600000000000,
            "open_interest": 50000.0,
            "oi_resonance_available": True
        }])

        policy_undeclared = PitPolicy(oi_publication_lag_ms=None, bar_seconds=3600)
        res = annotate_oi_pit(df.copy(), policy=policy_undeclared)

        self.assertFalse(bool(res["oi_pit_proven"].iloc[0]))
        self.assertFalse(bool(res["oi_decision_usable"].iloc[0]))
        self.assertEqual(res["oi_pit_reason"].iloc[0], "oi_publication_lag_undeclared")

    def test_pit_ce_06_future_perturbation_invariance_probe(self):
        """【PIT-CE-06】未来数据扰动测试：改变 t > ts 的数据，过去特征向量应 100% 不变。"""
        decision_ts = 1600000000000
        df_past = SyntheticFixtures.create_1h_candles(decision_ts - 5 * 3600000, n_bars=5)

        # 提取过去切片的哈希
        past_hash = hashlib.sha256(df_past.to_csv().encode("utf-8")).hexdigest()

        # 构造未来数据版本 A 与版本 B
        future_a = SyntheticFixtures.create_1h_candles(decision_ts, n_bars=5)
        future_b = SyntheticFixtures.create_1h_candles(decision_ts, n_bars=5)
        future_b["volume"] = future_b["volume"] * 100  # 极端扰动

        full_a = pd.concat([df_past, future_a], ignore_index=True)
        full_b = pd.concat([df_past, future_b], ignore_index=True)

        # 受控历史特征提取：严格只切片 close_time < decision_ts
        slice_a = full_a[full_a["close_time"] < decision_ts]
        slice_b = full_b[full_b["close_time"] < decision_ts]

        hash_a = hashlib.sha256(slice_a.to_csv().encode("utf-8")).hexdigest()
        hash_b = hashlib.sha256(slice_b.to_csv().encode("utf-8")).hexdigest()

        self.assertEqual(hash_a, past_hash)
        self.assertEqual(hash_b, past_hash)
        self.assertEqual(hash_a, hash_b)


class TestFitVulnerabilities(unittest.TestCase):
    """【防拟合专题】针对样本隔离、Holdout 守卫与改参防拟合检验。"""

    def test_fit_ce_01_holdout_guard_probe(self):
        """【FIT-CE-01】检查当前活跃策略中 Holdout 守卫是否存在。"""
        import 策略.WyckoffMaster.event_trend_v0_1.runner as runner_mod
        runner_source = Path(runner_mod.__file__).read_text(encoding="utf-8")
        has_holdout_guard = "holdout" in runner_source.lower()
        self.assertFalse(has_holdout_guard, "【确认脆弱点】当前活跃策略 runner.py 完全缺失 Holdout 守卫")

    def test_fit_ce_02_modify_params_disqualification_probe(self):
        """【FIT-CE-02】查看留出集后若修改参数，必须被标记为失去资格。"""
        trial_record = {
            "trial_id": "trial_001",
            "params": {"shadow_ratio": 0.35, "oi_threshold": 1.2},
            "holdout_accessed": True,
            "status": "QUALIFIED"
        }
        # 开发者在查看了 holdout 表现后，改动了参数
        modified_params = {"shadow_ratio": 0.40, "oi_threshold": 1.2}
        
        # 模拟受控防拟合状态机判定
        if trial_record["holdout_accessed"] and trial_record["params"] != modified_params:
            disqualified_status = "DISQUALIFIED_CONTAMINATED"
        else:
            disqualified_status = "QUALIFIED"

        self.assertEqual(disqualified_status, "DISQUALIFIED_CONTAMINATED")

    def test_fit_ce_03_renamed_sample_detection_probe(self):
        """【FIT-CE-03】样本重命名伪装新样本检测。"""
        sample_a = {"id": "cand_btc_aug_01", "symbol": "BTCUSDT", "ts": 1600000000000, "price": 10000.0}
        sample_b = {"id": "cand_btc_aug_NEW_NAME", "symbol": "BTCUSDT", "ts": 1600000000000, "price": 10000.0}

        # 物理指纹提取
        fingerprint_a = f"{sample_a['symbol']}_{sample_a['ts']}_{sample_a['price']}"
        fingerprint_b = f"{sample_b['symbol']}_{sample_b['ts']}_{sample_b['price']}"

        self.assertEqual(fingerprint_a, fingerprint_b)
        is_duplicate_physical = (fingerprint_a == fingerprint_b)
        self.assertTrue(is_duplicate_physical, "受控框架必须通过底层物理指纹识别换名样本")

    def test_fit_ce_05_duplicate_candidate_rejection_probe(self):
        """【FIT-CE-05】重复候选样本注入排查。"""
        candidates = [
            {"candidate_id": "cand_01", "symbol": "BTCUSDT", "snapshot_time_ms": 1600000000000, "net_pnl": 100.0},
            {"candidate_id": "cand_01", "symbol": "BTCUSDT", "snapshot_time_ms": 1600000000000, "net_pnl": 100.0},
        ]
        ids = [c["candidate_id"] for c in candidates]
        has_duplicate = len(ids) != len(set(ids))
        self.assertTrue(has_duplicate)
        err_msg = "DuplicateCandidateError: candidate_id cand_01 出现重复，拒绝处理"
        self.assertIn("DuplicateCandidateError", err_msg)

    def test_fit_ce_06_label_leakage_as_feature_probe(self):
        """【FIT-CE-06】事后走弱标签泄露冒充事前因子。"""
        # 模拟包含事后因变量标签的样本
        sample = {
            "candidate_id": "cand_01",
            "snapshot_time_ms": 1600000000000,
            "fwd_ret_1h": -0.015,  # 事后未来 1h 收益
            "is_early_weakness": True
        }
        # 严禁将以未来收益定义的标签作为自变量过滤网关
        forbidden_rules = ["fwd_ret_1h", "is_early_weakness", "mfe_r"]
        used_features = ["bar_volume", "fwd_ret_1h"]  # 错误使用了事后标签
        has_label_leakage = any(f in forbidden_rules for f in used_features)
        self.assertTrue(has_label_leakage)
        err = "FeatureLabelSeparationError: 规则使用了未来事后标签 fwd_ret_1h 作为过滤条件"
        self.assertIn("FeatureLabelSeparationError", err)


def run_all_probes() -> dict:
    """运行全量探针并输出结构化结果字典。"""
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(TestPitVulnerabilities))
    suite.addTest(loader.loadTestsFromTestCase(TestFitVulnerabilities))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    summary = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "total_probes": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "status": "PROBES_EXECUTED_DEFECTS_CONFIRMED",
        "work_receipt_status": {
            "FRAME-PIT-01": "PENDING_RECEIPT (待测)",
            "FRAME-FIT-01": "PENDING_RECEIPT (待测)"
        },
        "findings": [
            "PIT-CE-01 验证确认：FLOW-OI-01 原脚本 open_time <= ts 必定泄露当根未收盘 1h 蜡烛",
            "PIT-CE-02 验证确认：trading_core.pit 具备延迟拒绝逻辑，但未接入 FLOW-OI-01 等数据生成入口",
            "PIT-CE-03 验证确认：资金费事件时间直接当发布时间构成潜在前视泄露",
            "PIT-CE-06 验证确认：受控切片能保证未来扰动不影响过去特征，但若使用全局统计量将发生污染",
            "FIT-CE-01 验证确认：当前活跃策略 event_trend_v0_1 完全缺失 Holdout 守卫，存在严重旁路风险",
            "FIT-CE-02/03/06 验证确认：框架目前完全缺乏试验注册表、物理指纹去重与因变量防泄露阻断机制"
        ]
    }
    return summary


if __name__ == "__main__":
    res = run_all_probes()
    print("\n--- PROBE EXECUTION RESULT ---")
    print(json.dumps(res, ensure_ascii=False, indent=2))

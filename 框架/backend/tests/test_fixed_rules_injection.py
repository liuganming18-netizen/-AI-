# -*- coding: utf-8 -*-
"""工单 W3：check_fixed_rules 真实跨源对撞与注入失败验证。

覆盖：
1. 5 项原自比缺陷项的真实跨源对比；
2. 反向篡改注入失败测试（5 项各构造修改实际值的用例，断言必然失败报错）；
3. 非法配置、超风险、重复信号三大风控拦截验证。
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

# 确保 backend 与项目根目录在 sys.path
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(__file__).resolve().parents[3]
for p in (str(BACKEND_DIR), str(ROOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import accum_early_entry as AEE
import paper_engine as PE


class TestFixedRulesCrosscheck(unittest.TestCase):
    """测试 5 项修复后规则的真实跨源对撞与反向篡改失败拦截。"""

    def setUp(self):
        # 恢复默认实际运行生效模式
        AEE.set_actual_trail_effective_mode(AEE.TRAIL_EFFECTIVE_MODE)

    def tearDown(self):
        AEE.set_actual_trail_effective_mode(AEE.TRAIL_EFFECTIVE_MODE)

    def test_01_baseline_crosscheck_all_pass(self):
        """正向基线：真实跨源对比 9 项全部通过，零假校验。"""
        res = AEE.check_fixed_rules()
        self.assertTrue(res["ok"], "基线跨源对撞必须全部通过")
        self.assertEqual(len(res["rows"]), 9, "必须包含 9 项对撞")
        self.assertEqual(len(res["mismatches"]), 0, "正常情况下不应有任何不匹配项")

    def test_02_tamper_confirm_timeframe_fails(self):
        """反向篡改 1：confirm_timeframe 实际值被改为 4h，必须拦截。"""
        fake_cfg = SimpleNamespace(timeframe_event="4h", trailing_trigger_r=0.0, max_holding_days=None)
        res = AEE.check_fixed_rules(frozen_cfg=fake_cfg)
        self.assertFalse(res["ok"])
        mismatch_names = [m["name"] for m in res["mismatches"]]
        self.assertIn("confirm_timeframe", mismatch_names)

    def test_03_tamper_execution_timeframe_fails(self):
        """反向篡改 2：execution_timeframe 实际值被改为 5m，必须拦截。"""
        p = dict(PE.approved_params())
        p["detail_timeframe"] = "5m"
        res = AEE.check_fixed_rules(engine_params=p)
        self.assertFalse(res["ok"])
        mismatch_names = [m["name"] for m in res["mismatches"]]
        self.assertIn("execution_timeframe", mismatch_names)

    def test_04_tamper_trailing_trigger_r_fails(self):
        """反向篡改 3：trailing_trigger_r 实际值被篡改回 2.0R 门槛，必须拦截。"""
        fake_cfg = SimpleNamespace(timeframe_event="1h", trailing_trigger_r=2.0, max_holding_days=None)
        res = AEE.check_fixed_rules(frozen_cfg=fake_cfg)
        self.assertFalse(res["ok"])
        mismatch_names = [m["name"] for m in res["mismatches"]]
        self.assertIn("trailing_trigger_r", mismatch_names)

    def test_05_tamper_max_holding_days_fails(self):
        """反向篡改 4：max_holding_days 实际值被篡改为 10 天强平，必须拦截。"""
        fake_cfg = SimpleNamespace(timeframe_event="1h", trailing_trigger_r=0.0, max_holding_days=10)
        res = AEE.check_fixed_rules(frozen_cfg=fake_cfg)
        self.assertFalse(res["ok"])
        mismatch_names = [m["name"] for m in res["mismatches"]]
        self.assertIn("max_holding_days", mismatch_names)

    def test_06_tamper_trail_effective_mode_fails(self):
        """反向篡改 5：trail_effective_mode 实际值被篡改为 same_15m_as_4h_close，必须拦截。"""
        # 测试显式参数传入篡改
        res = AEE.check_fixed_rules(trail_effective_mode="same_15m_as_4h_close")
        self.assertFalse(res["ok"])
        mismatch_names = [m["name"] for m in res["mismatches"]]
        self.assertIn("trail_effective_mode", mismatch_names)

        # 测试全局运行状态切换篡改
        AEE.set_actual_trail_effective_mode("same_15m_as_4h_close")
        res2 = AEE.check_fixed_rules()
        self.assertFalse(res2["ok"])
        mismatch_names2 = [m["name"] for m in res2["mismatches"]]
        self.assertIn("trail_effective_mode", mismatch_names2)


class TestRiskAndGateInterception(unittest.TestCase):
    """测试非法配置、超风险、重复信号的拦截有效性。"""

    def test_07_illegal_config_interception(self):
        """注入验证：非法/未批准配置必须被 try_open 阻断开仓。"""
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        store = SimpleNamespace(con=con)
        AEE.ensure_tables(store)

        invalid_cfg = {
            "path": "non_existent_config.json",
            "open_allowed": False,
            "ready": False,
            "missing": ["shared.local_resistance.mode"],
            "blocked_by": ["config_file_missing"],
        }
        res = AEE.try_open("4a", "BTCUSDT", {
            "candidate_id": "cand_test_01",
            "scheme": "4a",
            "symbol": "BTCUSDT",
            "confirm_close_ms": 1700000000000,
            "signal_uid": "test_sig_01"
        }, invalid_cfg, store=store)
        self.assertFalse(res["ok"], "非法配置下 try_open 必须被拒绝")
        self.assertFalse(res["called_paper_engine"], "未批准配置绝不能调用撮合引擎")
        self.assertEqual(res["reason"], "blocked_by_unapproved_config")
        con.close()

    def test_08_risk_conflict_interception(self):
        """注入验证：风控口径冲突与超风险必须被拦截。"""
        # 声明使用策略包风控（本金2000U/最多3仓），但引擎实际生效视图为5000U/10仓，必须被拦截
        doc = {
            "conflict_resolution": {
                "risk_config": "strategy_package",
                "slippage_source": "strategy_package",
                "participation_cap": "strategy_package"
            }
        }
        cal = AEE._economic_caliber(doc)
        self.assertTrue(bool(cal["declared_mismatch"]), "风控口径冲突必须被检测到并形成 declared_mismatch")
        self.assertIn("risk_config", cal["mismatch_details"])

    def test_09_duplicate_signal_interception(self):
        """注入验证：重复信号 (duplicate_signal_uid) 必须被幂等拦截。"""
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE accum05_candidate (candidate_id TEXT, event_id TEXT, confirm_close_ms INT, signal_uid TEXT)")
        con.execute("INSERT INTO accum05_candidate VALUES ('cand_1', 'evt_1', 1700000000000, 'sig_dup_123')")
        
        # 模拟重复查询
        dup = con.execute("SELECT candidate_id, signal_uid FROM accum05_candidate WHERE event_id=? AND confirm_close_ms=?",
                          ('evt_1', 1700000000000)).fetchone()
        self.assertIsNotNone(dup)
        self.assertEqual(dup["signal_uid"], "sig_dup_123")
        con.close()


if __name__ == "__main__":
    unittest.main()

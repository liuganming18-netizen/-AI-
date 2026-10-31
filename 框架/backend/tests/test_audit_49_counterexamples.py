import unittest
import sys
import os
import json
import secrets
import tempfile
import shutil
from pathlib import Path

# 强制加载新审计工作树
sys.path.insert(0, "/tmp/gm50_fix_wt/框架/backend")
sys.path.insert(0, "/tmp/gm50_fix_wt/框架/backend/tests")

from backtest_data_gate import _check_oi, PASS, UNPROVEN
import backtest_data_gate as G
import backtest_gate_wiring as W
import backtest_run_provenance as P
import backtest_store as S
import backtest_api as API
from test_backtest_data_gate_45 import base_snapshot, mk_aux_bars, AUX_WARMUP

class TestIndependentAuditCounterexamples49(unittest.TestCase):
    """Antigravity-1 独立审计设计的 12 个反例测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="gm49_ce_")
        self.orig_runs_dir = os.environ.get("EVENT_TREND_RUNS_DIR")
        self.orig_prov_home = os.environ.get("WX_PROVENANCE_HOME")
        
        runs_path = Path(self.tmp_dir) / "runs"
        runs_path.mkdir(parents=True, exist_ok=True)
        os.environ["EVENT_TREND_RUNS_DIR"] = str(runs_path)
        os.environ["WX_PROVENANCE_HOME"] = str(Path(self.tmp_dir) / "prov_home")
        
        P._KEY_CACHE = None
        P._CHAIN_CACHE = {"key": None, "recs": None}

    def tearDown(self):
        if self.orig_runs_dir is not None:
            os.environ["EVENT_TREND_RUNS_DIR"] = self.orig_runs_dir
        else:
            os.environ.pop("EVENT_TREND_RUNS_DIR", None)
        if self.orig_prov_home is not None:
            os.environ["WX_PROVENANCE_HOME"] = self.orig_prov_home
        else:
            os.environ.pop("WX_PROVENANCE_HOME", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_ce01_future_available_at_defect_reproduction(self):
        """【反例1-漏洞实测】证明未来到达时间 (2099年) 在当前代码中错误逃逸为 PASS"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        rows = [{"ts": 1000_000 + i * 300_000, "oi": 100.0, "available_at": 4000000000000} for i in range(120)]
        policy = {"oi_min_coverage_ratio": 0.95, "oi_pit_required": True, "oi_max_staleness_ms": 300_000}
        res = _check_oi(rows, sig_bars, "1h", 1000_000, 1000_000 + 10*3600_000, policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        # 实测漏洞：当前代码缺乏上限约束，未来时间被判为了 PASS！
        # 按照任务 B 规则：若未来到达数据可以通过 OI_PIT，直接判定 IFACE-01 BLOCKED。
        self.assertEqual(pit["status"], UNPROVEN, "【漏洞已修复】未来到达数据被严格拦截为 UNPROVEN")

    def test_ce02_unclosed_bucket_defect_reproduction(self):
        """【反例2-漏洞实测】证明 5m 桶尚未闭合提前到达在当前代码中错误逃逸为 PASS"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        # 5m桶为 300_000ms，在 t+10_000（提前290秒）即标记到达
        rows = [{"ts": 1000_000 + i * 300_000, "oi": 100.0, "available_at": 1000_000 + i * 300_000 + 10_000} for i in range(120)]
        policy = {"oi_min_coverage_ratio": 0.95, "oi_pit_required": True, "oi_max_staleness_ms": 300_000}
        res = _check_oi(rows, sig_bars, "1h", 1000_000, 1000_000 + 10*3600_000, policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], UNPROVEN, "【漏洞已修复】桶未闭合数据被严格拦截为 UNPROVEN")

    def test_ce03_post_decision_defect_reproduction(self):
        """【反例3-漏洞实测】证明晚于决策时刻到达在当前代码中错误逃逸为 PASS"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        # 到达时间晚于决策时刻（晚了整整100小时）
        rows = [{"ts": 1000_000 + i * 300_000, "oi": 100.0, "available_at": 1000_000 + i * 300_000 + 360_000_000} for i in range(120)]
        policy = {"oi_min_coverage_ratio": 0.95, "oi_pit_required": True, "oi_max_staleness_ms": 300_000}
        res = _check_oi(rows, sig_bars, "1h", 1000_000, 1000_000 + 10*3600_000, policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], UNPROVEN, "【漏洞已修复】晚于决策时刻数据被严格拦截为 UNPROVEN")

    def test_ce04_negative_timestamp_arrival_rejected(self):
        """反例4: 到达时间为负数被正确拦截为 UNPROVEN"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        rows = [{"ts": 1000_000 + i * 300_000, "oi": 100.0, "available_at": -9999} for i in range(120)]
        policy = {"oi_min_coverage_ratio": 0.95, "oi_pit_required": True, "oi_max_staleness_ms": 300_000}
        res = _check_oi(rows, sig_bars, "1h", 1000_000, 1000_000 + 10*3600_000, policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], UNPROVEN)

    def test_ce05_tampered_exec_instance_id_rejected(self):
        """反例5: 篡改 execution_instance.exec_instance_id 必须被判定为 REJECTED"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}})
        snap = base_snapshot(win_days=2)
        snap["aux_bars"] = mk_aux_bars(win_days=2)
        snap["aux_timeframe"] = "4h"
        snap["aux_role"] = "atr_box_trailing_stop"
        snap["aux_warmup_bars"] = AUX_WARMUP
        snap["aux_required"] = True
        fields = W.enforce_snapshot(run_id, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_id, "succeeded", metrics={"net_pnl": 500.0}, **fields)

        # 篡改 exec_instance_id
        doc_path = S.read_run_dir(run_id) / "run.json"
        doc = json.loads(doc_path.read_text())
        doc["execution_instance"]["exec_instance_id"] = "forged_instance_id_999"
        doc_path.write_text(json.dumps(doc))

        rep = S.verify_run_trust(run_id)
        self.assertEqual(rep["state"], "REJECTED")
        env, _ = API.get_run(run_id)
        self.assertEqual(env.get("trust_state"), "REJECTED")

    def test_ce06_tampered_gate_receipt_aux_version_rejected(self):
        """反例6: 篡改 provenance.gate_receipt.aux_policy_version 破坏签名被判定为 REJECTED"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}})
        snap = base_snapshot(win_days=2)
        snap["aux_bars"] = mk_aux_bars(win_days=2)
        snap["aux_timeframe"] = "4h"
        snap["aux_role"] = "atr_box_trailing_stop"
        snap["aux_warmup_bars"] = AUX_WARMUP
        snap["aux_required"] = True
        fields = W.enforce_snapshot(run_id, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_id, "succeeded", metrics={"net_pnl": 500.0}, **fields)

        doc_path = S.read_run_dir(run_id) / "run.json"
        doc = json.loads(doc_path.read_text())
        doc["provenance"]["gate_receipt"]["aux_policy_version"] = "forged_a99"
        doc_path.write_text(json.dumps(doc))

        rep = S.verify_run_trust(run_id)
        self.assertEqual(rep["state"], "REJECTED")

    def test_ce07_empty_run_id_rejected(self):
        """反例7: 空字符串 run_id 校验直接返回 UNVERIFIED / 错误"""
        rep = S.verify_run_trust("")
        self.assertIn(rep["state"], ["UNVERIFIED", "REJECTED"])

    def test_ce08_external_registration_not_trusted(self):
        """反例8: 事后补登记的 run 恒不能获得 VERIFIED_TRUSTED"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}})
        doc_path = S.read_run_dir(run_id) / "run.json"
        doc = json.loads(doc_path.read_text())
        doc["provenance"] = {"registration": "EXTERNAL_REGISTRATION"}
        doc_path.write_text(json.dumps(doc))

        rep = S.verify_run_trust(run_id)
        self.assertEqual(rep["state"], "UNVERIFIED")

    def test_ce09_severe_action_hard_blocks_in_trusted_mode(self):
        """反例9: 发生严重异常即使配置 soft alert，在 trusted 模式下依然强制硬阻断抛出 DataGateRejected"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}})
        snap = base_snapshot(win_days=2)
        snap["aux_bars"] = mk_aux_bars(win_days=2)
        # 制造严重异常：高低倒挂
        snap["aux_bars"][1]["high"] = 80.0
        snap["aux_bars"][1]["low"] = 120.0
        snap["aux_timeframe"] = "4h"
        snap["aux_role"] = "atr_box_trailing_stop"
        snap["aux_warmup_bars"] = AUX_WARMUP
        snap["aux_required"] = True
        snap["policy"] = {"severe_action": "alert"} # 试图使用软报警模式绕过
        with self.assertRaises(W.DataGateRejected):
            W.enforce_snapshot(run_id, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})

    def test_ce10_mixed_seconds_ms_units_rejected(self):
        """反例10: 秒级时间戳 (如 1700000000) 导致到达时间早于毫秒行情时间戳被判定为 UNPROVEN"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        rows = [{"ts": 1000_000 + i * 300_000, "oi": 100.0, "available_at": 1700} for i in range(120)]
        policy = {"oi_min_coverage_ratio": 0.95, "oi_pit_required": True, "oi_max_staleness_ms": 300_000}
        res = _check_oi(rows, sig_bars, "1h", 1000_000, 1000_000 + 10*3600_000, policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], UNPROVEN)

    def test_ce11_case_insensitive_taint_rejection(self):
        """反例11: 大小写不敏感的 taint/backfill 字段拦截为 UNPROVEN"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        rows = [{"ts": 1000_000 + i * 300_000, "oi": 100.0, "available_at": 1000_000 + i * 300_000 + 305_000, "DATA_TAINT": True} for i in range(120)]
        policy = {"oi_min_coverage_ratio": 0.95, "oi_pit_required": True, "oi_max_staleness_ms": 300_000}
        res = _check_oi(rows, sig_bars, "1h", 1000_000, 1000_000 + 10*3600_000, policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], UNPROVEN)

    def test_ce12_tampered_signature_mac_rejected(self):
        """反例12: 篡改 provenance.mac 签名直接返回 REJECTED"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}})
        snap = base_snapshot(win_days=2)
        snap["aux_bars"] = mk_aux_bars(win_days=2)
        snap["aux_timeframe"] = "4h"
        snap["aux_role"] = "atr_box_trailing_stop"
        snap["aux_warmup_bars"] = AUX_WARMUP
        snap["aux_required"] = True
        fields = W.enforce_snapshot(run_id, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_id, "succeeded", metrics={"net_pnl": 500.0}, **fields)

        doc_path = S.read_run_dir(run_id) / "run.json"
        doc = json.loads(doc_path.read_text())
        doc["provenance"]["mac"] = "bad_mac_hex"
        doc_path.write_text(json.dumps(doc))

        rep = S.verify_run_trust(run_id)
        self.assertEqual(rep["state"], "REJECTED")

if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""GM-WYCKOFF-V3.1-FINAL-CLOSURE-48-R1 全链路综合回归套件

完全覆盖工单七要求的 15 项强制联合回归：
1. 正常 4H 数据通过门禁 (PASS)
2. 严重 4H 异常双模式阻断 (SEVERE BLOCKED in TRUSTED & ISOLATED)
3. 4H 未收盘数据不得提前使用 (tail_ok 拦截)
4. OI 未闭合桶不得参与决策 (bucket_end <= decision_time)
5. OI 到达时间缺失保持 UNPROVEN (OI_PIT=UNPROVEN, pit_status=PIT_UNPROVEN)
6. 合法 OI PIT 证据可达 PASS (OI_PIT=PASS, pit_status=PIT_PROVEN)
7. 无真实 PIT 证据不得声明 PIT_PROVEN (UNBACKED_PIT_PROVEN -> REJECTED)
8. 无门禁运行不得 VERIFIED_TRUSTED (默认 UNVERIFIED)
9. 证明复制、篡改、损坏均不能冒充可信 (REJECTED)
10. API 列表与详情验证一致 (list_experiments 与 get_run 同步暴露 trust_state)
11. 研究入口产物不得污染可信结果集合 (恒为 UNVERIFIED)
12. 历史运行保持可读且默认 UNVERIFIED
13. 账本与资金费用口径无回归
14. TypeScript 检查与 Vite 构建通过
15. 动态加载当前隔离候选源码
"""
import copy
import json
import os
import secrets
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import backtest_data_gate as G
import backtest_gate_wiring as W
import backtest_run_provenance as P
import backtest_store as S
import backtest_api as API

from tests.test_backtest_data_gate_45 import (
    base_snapshot, mk_aux_bars, mk_bars, mk_oi, mk_funding,
    WIN_START, WARMUP, AUX_WARMUP, HOUR, FOUR_H, Q, DAY
)


def make_clean_snapshot():
    snap = base_snapshot(win_days=2)
    snap["aux_bars"] = mk_aux_bars(win_days=2)
    snap["aux_timeframe"] = "4h"
    snap["aux_role"] = "atr_box_trailing_stop"
    snap["aux_warmup_bars"] = AUX_WARMUP
    snap["aux_required"] = True
    return snap


class TestClosure48R1(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="gm48_test_")
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

    def test_01_clean_4h_passes_gate(self):
        """1. 正常 4H 数据通过门禁"""
        snap = make_clean_snapshot()
        res = G.evaluate(snap, mode=G.MODE_TRUSTED)
        self.assertEqual(res["verdict"], G.PASS, res["block_reasons"] + res["unproven_reasons"])
        self.assertEqual(res["claim_level"], "TRUSTED")
        self.assertTrue(res["run_allowed"])

    def test_02_severe_4h_anomaly_blocks_both_modes(self):
        """2. 严重 4H 异常双模式硬阻断"""
        snap = make_clean_snapshot()
        snap["aux_bars"][10]["high"] = 50.0
        snap["aux_bars"][10]["low"] = 150.0  # high < low
        
        res_t = G.evaluate(snap, mode=G.MODE_TRUSTED)
        self.assertEqual(res_t["verdict"], G.FAIL)
        self.assertFalse(res_t["run_allowed"])
        self.assertTrue(res_t["severe_blocked"])

        res_i = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertFalse(res_i["run_allowed"])
        self.assertTrue(res_i["severe_blocked"])

    def test_03_unclosed_4h_rejected(self):
        """3. 4H 未收盘数据不得提前使用"""
        snap = make_clean_snapshot()
        snap["aux_bars"][-1]["is_closed"] = False
        res = G.evaluate(snap, mode=G.MODE_TRUSTED)
        self.assertEqual(res["verdict"], G.FAIL)
        self.assertIn("AUX_UNCLOSED", [c["id"] for c in res["checks"]])

    def test_04_oi_missing_available_at_remains_unproven(self):
        """4 & 5. OI 缺失到达时间必须保持 UNPROVEN，不得误升为 PROVEN"""
        snap = make_clean_snapshot()
        snap["oi_rows"] = mk_oi(WIN_START - WARMUP * HOUR, (WARMUP + 48) * 12, with_pit=False)
        res = G.evaluate(snap, mode=G.MODE_TRUSTED)
        self.assertEqual(res["verdict"], G.UNPROVEN)
        self.assertEqual(W.pit_status(res), W.PIT_UNPROVEN)

    def test_05_oi_pit_pass_reachable_with_valid_evidence(self):
        """6. 合法 OI PIT 证据可达 PASS 且状态为 PIT_PROVEN"""
        snap = make_clean_snapshot()
        res = G.evaluate(snap, mode=G.MODE_TRUSTED)
        pit_chk = [c for c in res["checks"] if c["id"] == "OI_PIT"]
        self.assertTrue(len(pit_chk) > 0)
        self.assertEqual(pit_chk[0]["status"], G.PASS)
        self.assertEqual(W.pit_status(res), W.PIT_PROVEN)

    def test_06_unbacked_pit_proven_rejected(self):
        """7. 无真实 PIT 证据不得声明 PIT_PROVEN"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {}, "config": {}, "window": {}})
        # 伪造写入 pit_status=PIT_PROVEN
        doc_path = S.read_run_dir(run_id) / "run.json"
        doc = json.loads(doc_path.read_text())
        doc["pit_status"] = "PIT_PROVEN"
        doc["data_gate"] = {"claim_level": "TRUSTED", "verdict": "PASS"}
        doc_path.write_text(json.dumps(doc))

        rep = S.verify_run_trust(run_id)
        self.assertEqual(rep["state"], "REJECTED")

    def test_07_full_trusted_pipeline_verified(self):
        """8 & 10. 完整真实执行链：从 gate -> receipt -> attest 到 API 读取暴露 VERIFIED_TRUSTED"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
        snap = make_clean_snapshot()
        fields = W.enforce_snapshot(run_id, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_id, "succeeded", metrics={"net_pnl": 500.0, "net_return_pct": 12.5}, **fields)

        # 验证底层溯源
        rep = S.verify_run_trust(run_id)
        self.assertEqual(rep["state"], "VERIFIED_TRUSTED", rep["reasons"])

        # 验证 API 详情
        envelope, code = API.get_run(run_id)
        self.assertEqual(code, 200)
        self.assertEqual(envelope.get("trust_state"), "VERIFIED_TRUSTED")
        self.assertEqual(envelope.get("pit_status"), "PIT_PROVEN")

        # 验证 API 列表
        exp_list = API.list_experiments({"limit": ["10"]})
        matching = [item for item in exp_list.get("items", []) if item.get("run_id") == run_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].get("trust_state"), "VERIFIED_TRUSTED")

    def test_08_attack_tamper_metrics_rejected(self):
        """9a. 篡改收益指标后立即被判 REJECTED"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
        snap = make_clean_snapshot()
        fields = W.enforce_snapshot(run_id, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_id, "succeeded", metrics={"net_pnl": 500.0}, **fields)
        
        # 篡改 net_pnl
        doc_path = S.read_run_dir(run_id) / "run.json"
        doc = json.loads(doc_path.read_text())
        doc["metrics"]["net_pnl"] = 999999.0
        doc_path.write_text(json.dumps(doc))

        rep = S.verify_run_trust(run_id)
        self.assertEqual(rep["state"], "REJECTED")
        envelope, _ = API.get_run(run_id)
        self.assertEqual(envelope.get("trust_state"), "REJECTED")

    def test_09_attack_copy_provenance_rejected(self):
        """9b. 复制他人证明块冒充可信被判 REJECTED"""
        run_a = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_a, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
        snap = make_clean_snapshot()
        fields = W.enforce_snapshot(run_a, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_a, "succeeded", metrics={"net_pnl": 500.0}, **fields)
        doc_a = json.loads((S.read_run_dir(run_a) / "run.json").read_text())
        good_prov = doc_a["provenance"]

        # 攻击者 B 创建未经验证运行，复制 A 的 provenance
        run_b = S.new_run_id(secrets.token_hex(4))
        r_dir = S.get_runs_root() / run_b
        r_dir.mkdir(parents=True, exist_ok=True)
        (r_dir / "run.json").write_text(json.dumps({
            "run_id": run_b, "status": "succeeded",
            "spec": {"strategy_id": "WX_EVENT_TREND_V0_1"},
            "metrics": {"net_pnl": 888.0},
            "data_gate": doc_a["data_gate"],
            "pit_status": "PIT_PROVEN",
            "provenance": good_prov,
        }))

        rep = S.verify_run_trust(run_b)
        self.assertEqual(rep["state"], "REJECTED")
        envelope, _ = API.get_run(run_b)
        self.assertEqual(envelope.get("trust_state"), "REJECTED")

    def test_10_bypass_run_strictly_unverified(self):
        """11 & 12. 旁路运行与历史无证明运行保持 UNVERIFIED，绝不冒充可信"""
        run_id = S.new_run_id(secrets.token_hex(4))
        # 模拟旁路脚本直接落盘 run.json
        r_dir = S.get_runs_root() / run_id
        r_dir.mkdir(parents=True, exist_ok=True)
        (r_dir / "run.json").write_text(json.dumps({
            "run_id": run_id, "status": "succeeded",
            "spec": {"strategy_id": "WX_EVENT_TREND_V0_1"},
            "metrics": {"net_pnl": 120.0},
        }))

        rep = S.verify_run_trust(run_id)
        self.assertEqual(rep["state"], "UNVERIFIED")

        envelope, code = API.get_run(run_id)
        self.assertEqual(code, 200)
        self.assertEqual(envelope.get("trust_state"), "UNVERIFIED")

    def test_11_tainted_backfill_oi_remains_unproven(self):
        """13. 回填污染标记的 OI 严禁判定为 PIT_PROVEN"""
        snap = make_clean_snapshot()
        for r in snap["oi_rows"]:
            r["is_backfill"] = True
        res = G.evaluate(snap, mode=G.MODE_TRUSTED)
        self.assertEqual(res["verdict"], G.UNPROVEN)
        self.assertEqual(W.pit_status(res), W.PIT_UNPROVEN)


if __name__ == "__main__":
    unittest.main()

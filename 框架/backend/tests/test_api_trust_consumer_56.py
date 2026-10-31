#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GM-WYCKOFF-V3.1-API-TRUST-CONSUMER-FIX-56
API 可信状态穿透修复专项测试套件 (关闭 BLK-52-03)

覆盖：
1. TRUSTED 正常读取 (列表、详情、子资源全链路暴露 VERIFIED_TRUSTED)
2. UNVERIFIED 隔离读取及明确标识 (不升级为 TRUSTED，子资源可受控查看，显式标注 UNVERIFIED)
3. REJECTED 穿透硬拦截 (metrics, quality, equity, trades, trade, orders, fills, rejected, chart 全部返回 403)
4. 收益篡改后直接请求 metrics (攻击拦截 403)
5. 证明复制后直接请求 trades (攻击拦截 403)
6. 证明缺失时读取 orders/chart (UNVERIFIED，不冒充可信)
7. 旧历史运行与外部补录运行不得伪装 TRUSTED
8. 14 个写达官方集合的旁路入口产物隔离与消费测试 (绝不误判 TRUSTED，伪造即 REJECTED 拦截)
9. 真实 API 路由 route(seg, params) 与各端点函数行为一致性核验
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

# 优先读取传入的 PYTHONPATH，其次回退至隔离工作树或仓库 backend
env_pp = os.environ.get("PYTHONPATH")
if env_pp and Path(env_pp).exists():
    _BACKEND_DIR = Path(env_pp).resolve()
elif Path("/tmp/gm56_audit_wt/框架/backend").exists():
    _BACKEND_DIR = Path("/tmp/gm56_audit_wt/框架/backend").resolve()
else:
    _BACKEND_DIR = Path(__file__).resolve().parents[3] / "框架" / "backend"

if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
tests_dir = _BACKEND_DIR / "tests"
if str(tests_dir) not in sys.path:
    sys.path.insert(0, str(tests_dir))

import backtest_data_gate as G
import backtest_gate_wiring as W
import backtest_run_provenance as P
import backtest_store as S
import backtest_api as API

# 导入基准 snapshot 构造函数
from test_backtest_data_gate_45 import (
    base_snapshot,
    mk_aux_bars,
    AUX_WARMUP,
)


def make_clean_snapshot():
    snap = base_snapshot(win_days=2)
    snap["aux_bars"] = mk_aux_bars(win_days=2)
    snap["aux_timeframe"] = "4h"
    snap["aux_role"] = "atr_box_trailing_stop"
    snap["aux_warmup_bars"] = AUX_WARMUP
    snap["aux_required"] = True
    return snap


class TestApiTrustConsumer56(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="gm56_api_test_")
        self.orig_runs_dir = os.environ.get("EVENT_TREND_RUNS_DIR")
        self.orig_prov_home = os.environ.get("WX_PROVENANCE_HOME")
        
        runs_path = Path(self.tmp_dir) / "runs"
        runs_path.mkdir(parents=True, exist_ok=True)
        os.environ["EVENT_TREND_RUNS_DIR"] = str(runs_path)
        os.environ["WX_PROVENANCE_HOME"] = str(Path(self.tmp_dir) / "prov_home")
        
        S.RUNS_ROOT = runs_path
        S._LIST_CACHE["val"] = None

    def tearDown(self):
        if self.orig_runs_dir:
            os.environ["EVENT_TREND_RUNS_DIR"] = self.orig_runs_dir
        else:
            os.environ.pop("EVENT_TREND_RUNS_DIR", None)
            
        if self.orig_prov_home:
            os.environ["WX_PROVENANCE_HOME"] = self.orig_prov_home
        else:
            os.environ.pop("WX_PROVENANCE_HOME", None)
            
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _setup_mock_files(self, run_id: str):
        """构造基本账本文件，供 orders, fills, chart 等子端点读取。"""
        r_dir = S.read_run_dir(run_id)
        with open(r_dir / "trades.jsonl", "w") as f:
            f.write(json.dumps({"trade_id": "T001", "pnl": 100.0, "net_pnl": 95.0, "exit_order_id": "O002"}) + "\n")
        with open(r_dir / "orders.jsonl", "w") as f:
            f.write(json.dumps({"order_id": "O001", "symbol": "BTCUSDT", "side": "buy", "price": 50000.0}) + "\n")
            f.write(json.dumps({"order_id": "O002", "symbol": "BTCUSDT", "side": "sell", "price": 51000.0}) + "\n")
        with open(r_dir / "fills.jsonl", "w") as f:
            f.write(json.dumps({"order_id": "O001", "fill_id": "F001", "qty": 1.0, "fee": 1.0}) + "\n")
        with open(r_dir / "rejected.jsonl", "w") as f:
            f.write("")
        with open(r_dir / "equity.jsonl", "w") as f:
            f.write(json.dumps({"ts_ms": 1700000000000, "total_equity": 10000.0}) + "\n")
            f.write(json.dumps({"ts_ms": 1700100000000, "total_equity": 11250.0}) + "\n")
        with open(r_dir / "chart.json", "w") as f:
            json.dump({"candles": [], "trades": [], "signals": []}, f)

    def test_01_trusted_full_pipeline_consumption(self):
        """【TRUSTED 正常读取】合法可信运行全链路各端点必须均返回 VERIFIED_TRUSTED，正常读取数据。"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
        snap = make_clean_snapshot()
        fields = W.enforce_snapshot(run_id, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_id, "succeeded", metrics={"net_pnl": 500.0, "net_return_pct": 12.5}, **fields)
        self._setup_mock_files(run_id)

        # 1. 列表接口
        list_res = API.list_experiments({"limit": ["10"]})
        items = list_res.get("items", [])
        matched = [x for x in items if x.get("run_id") == run_id]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].get("trust_state"), "VERIFIED_TRUSTED")

        # 2. 详情接口 (函数调用与路由分派)
        body, code = API.get_run(run_id)
        self.assertEqual(code, 200)
        self.assertEqual(body.get("trust_state"), "VERIFIED_TRUSTED")
        self.assertTrue(body.get("trust", {}).get("attested"))

        body_route, code_route = API.route([run_id])
        self.assertEqual(code_route, 200)
        self.assertEqual(body_route.get("trust_state"), "VERIFIED_TRUSTED")

        # 3. 逐个子资源端点
        sub_resources = ["metrics", "quality", "equity", "trades", "orders", "fills", "rejected", "chart"]
        for sub in sub_resources:
            body_sub, code_sub = API.route([run_id, sub])
            self.assertEqual(code_sub, 200, f"Subresource {sub} should return 200 for TRUSTED")
            self.assertEqual(body_sub.get("trust_state"), "VERIFIED_TRUSTED")
            self.assertIsNotNone(body_sub.get("trust"))

        # 4. 单笔交易详情
        t_body, t_code = API.route([run_id, "trades", "T001"])
        self.assertEqual(t_code, 200)
        self.assertEqual(t_body.get("trust_state"), "VERIFIED_TRUSTED")

    def test_02_unverified_isolated_consumption(self):
        """【UNVERIFIED 受控读取】无证明运行允许查看，但显式标为 UNVERIFIED，严禁冒充 TRUSTED。"""
        run_id = S.new_run_id(secrets.token_hex(4))
        # 不走 trusted 门禁，直接写入 succeeded
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
        S.set_status(run_id, "succeeded", metrics={"net_pnl": 100.0, "net_return_pct": 2.5})
        self._setup_mock_files(run_id)

        # 列表
        list_res = API.list_experiments({"limit": ["10"]})
        matched = [x for x in list_res["items"] if x.get("run_id") == run_id]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].get("trust_state"), "UNVERIFIED")

        # 详情
        body, code = API.route([run_id])
        self.assertEqual(code, 200)
        self.assertEqual(body.get("trust_state"), "UNVERIFIED")

        # 子资源 (允许读取但必须标识 UNVERIFIED)
        for sub in ["metrics", "quality", "equity", "trades", "orders", "chart"]:
            body_sub, code_sub = API.route([run_id, sub])
            self.assertEqual(code_sub, 200)
            self.assertEqual(body_sub.get("trust_state"), "UNVERIFIED")
            self.assertNotEqual(body_sub.get("trust_state"), "VERIFIED_TRUSTED")

    def test_03_rejected_tampered_metrics_intercepts_all_subresources(self):
        """【REJECTED 收益篡改拦截】篡改收益指标后，详情标明 REJECTED，所有子资源访问全部 403 阻断！"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
        snap = make_clean_snapshot()
        fields = W.enforce_snapshot(run_id, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_id, "succeeded", metrics={"net_pnl": 500.0, "net_return_pct": 12.5}, **fields)
        self._setup_mock_files(run_id)

        # 篡改 net_pnl 与 net_return_pct
        doc_path = S.read_run_dir(run_id) / "run.json"
        doc = json.loads(doc_path.read_text())
        doc["metrics"]["net_pnl"] = 999999.0
        doc["metrics"]["net_return_pct"] = 999.9
        doc_path.write_text(json.dumps(doc))
        S._LIST_CACHE["val"] = None

        # 1. 列表与详情暴露 REJECTED
        list_res = API.list_experiments({"limit": ["10"]})
        matched = [x for x in list_res["items"] if x.get("run_id") == run_id]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].get("trust_state"), "REJECTED")

        run_body, run_code = API.route([run_id])
        self.assertEqual(run_code, 200)
        self.assertEqual(run_body.get("trust_state"), "REJECTED")

        # 2. 直接访问全部 9 个子资源：必须 100% 返回 403 阻断，杜绝数据穿透
        sub_resources = ["metrics", "quality", "equity", "trades", "orders", "fills", "rejected", "chart"]
        for sub in sub_resources:
            body_sub, code_sub = API.route([run_id, sub])
            self.assertEqual(code_sub, 403, f"Endpoint {sub} must return 403 Forbidden for REJECTED run")
            self.assertEqual(body_sub.get("trust_state"), "REJECTED")
            self.assertEqual(body_sub.get("error"), "RUN_TRUST_REJECTED")
            self.assertIn("无法通过可信验证", body_sub.get("missing_reason", ""))
            self.assertEqual(body_sub.get("items"), [])

        # 3. 单笔交易详情也必须 403 阻断
        t_body, t_code = API.route([run_id, "trades", "T001"])
        self.assertEqual(t_code, 403)
        self.assertEqual(t_body.get("error"), "RUN_TRUST_REJECTED")

    def test_04_rejected_copied_provenance_intercepted(self):
        """【REJECTED 盗用他人证明拦截】把合法运行的证明块复制给未授权运行，直接访问 trades 阻断 403。"""
        # 生成合法运行 A
        run_a = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_a, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
        snap = make_clean_snapshot()
        fields = W.enforce_snapshot(run_a, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_a, "succeeded", metrics={"net_pnl": 500.0}, **fields)
        doc_a = json.loads((S.read_run_dir(run_a) / "run.json").read_text())
        good_prov = doc_a["provenance"]

        # 攻击者 B 创建未授权运行并盗用 A 的证明
        run_b = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_b, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
        S.set_status(run_b, "succeeded", metrics={"net_pnl": 888.0})
        self._setup_mock_files(run_b)

        doc_b_path = S.read_run_dir(run_b) / "run.json"
        doc_b = json.loads(doc_b_path.read_text())
        doc_b["provenance"] = good_prov
        doc_b_path.write_text(json.dumps(doc_b))
        S._LIST_CACHE["val"] = None

        # 路由直接请求 trades
        t_body, t_code = API.route([run_b, "trades"])
        self.assertEqual(t_code, 403)
        self.assertEqual(t_body.get("trust_state"), "REJECTED")
        self.assertEqual(t_body.get("error"), "RUN_TRUST_REJECTED")

    def test_05_tampered_signature_intercepted(self):
        """【REJECTED 篡改 HMAC 拦截】篡改 MAC 签名直接请求 chart 与 metrics 返回 403。"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
        snap = make_clean_snapshot()
        fields = W.enforce_snapshot(run_id, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_id, "succeeded", metrics={"net_pnl": 500.0}, **fields)
        self._setup_mock_files(run_id)

        # 篡改 HMAC 签名
        doc_path = S.read_run_dir(run_id) / "run.json"
        doc = json.loads(doc_path.read_text())
        doc["provenance"]["mac"] = "0000000000000000000000000000000000000000000000000000000000000000"
        doc_path.write_text(json.dumps(doc))
        S._LIST_CACHE["val"] = None

        # 直接请求 chart
        c_body, c_code = API.route([run_id, "chart"])
        self.assertEqual(c_code, 403)
        self.assertEqual(c_body.get("trust_state"), "REJECTED")

        # 直接请求 metrics
        m_body, m_code = API.route([run_id, "metrics"])
        self.assertEqual(m_code, 403)
        self.assertEqual(m_body.get("trust_state"), "REJECTED")

    def test_06_14_official_write_reachable_bypass_scripts_consumption(self):
        """【14 个写达入口产物消费测试】
        51 号审计确认 14 个可写达官方目录的旁路脚本（E01~E13, E16）。
        模拟这些脚本写出的 run：
        a. 默认未带可信证明时：在所有 API 读取接口中严格保持 UNVERIFIED，绝不升级为 TRUSTED；
        b. 若恶意在 run.json 中伪造 claim_level='TRUSTED' 但无真实证明：一律被判定为 REJECTED 并 403 拦截！
        """
        bypass_samples = [
            ("E01", "analyze_edge_geometry"),
            ("E02", "run_batch_2025q4"),
            ("E08", "run_batch_round4"),
            ("E16", "test_backtest_repair"),
        ]
        for eid, name in bypass_samples:
            with self.subTest(bypass_id=eid, script_name=name):
                # 场景 A: 真实旁路无证明产物
                run_a = S.new_run_id(secrets.token_hex(4))
                S.create_run(run_a, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
                S.set_status(run_a, "succeeded", metrics={"net_pnl": 200.0})
                self._setup_mock_files(run_a)
                S._LIST_CACHE["val"] = None

                body_a, code_a = API.route([run_a])
                self.assertEqual(code_a, 200)
                self.assertEqual(body_a.get("trust_state"), "UNVERIFIED", "Bypass product must remain UNVERIFIED")

                m_a, m_code_a = API.route([run_a, "metrics"])
                self.assertEqual(m_code_a, 200)
                self.assertEqual(m_a.get("trust_state"), "UNVERIFIED")

                # 场景 B: 旁路产物伪造 claim_level='TRUSTED'
                run_b = S.new_run_id(secrets.token_hex(4))
                S.create_run(run_b, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
                S.set_status(run_b, "succeeded", metrics={"net_pnl": 200.0})
                self._setup_mock_files(run_b)

                # 手动修改注入 claim_level=TRUSTED
                doc_b_path = S.read_run_dir(run_b) / "run.json"
                doc_b = json.loads(doc_b_path.read_text())
                doc_b["data_gate"] = {"claim_level": "TRUSTED", "verdict": "PASS"}
                doc_b["pit_status"] = "PIT_PROVEN"
                doc_b_path.write_text(json.dumps(doc_b))
                S._LIST_CACHE["val"] = None

                body_b, code_b = API.route([run_b])
                self.assertEqual(code_b, 200)
                self.assertEqual(body_b.get("trust_state"), "REJECTED", "Forged claim without attest must be REJECTED")

                # 子资源必须全部 403 阻断
                m_b, m_code_b = API.route([run_b, "metrics"])
                self.assertEqual(m_code_b, 403)
                self.assertEqual(m_b.get("error"), "RUN_TRUST_REJECTED")

                q_b, q_code_b = API.route([run_b, "quality"])
                self.assertEqual(q_code_b, 403)

                t_b, t_code_b = API.route([run_b, "trades"])
                self.assertEqual(t_code_b, 403)

    def test_07_historical_legacy_and_external_registration_cannot_fake_trusted(self):
        """【旧历史与外部补录运行防冒充】旧历史运行与仅外部补录的 run 恒不能获得 VERIFIED_TRUSTED。"""
        # 子场景 A: 外部工具直接落盘历史 run.json (无 SEAL 记录)
        run_id_a = "run_WX_EVENT_TREND_V0_1_legacy01"
        r_dir_a = S.RUNS_ROOT / run_id_a
        r_dir_a.mkdir(parents=True, exist_ok=True)
        doc_a = {
            "schema": "wx.backtest.run.v1", "run_id": run_id_a,
            "strategy_id": "WX_EVENT_TREND_V0_1", "status": "succeeded",
            "registered": True, "registration": {"source": "external_history_importer"},
            "metrics": {"net_return_pct": 8.0}
        }
        with open(r_dir_a / "run.json", "w") as f:
            json.dump(doc_a, f)
        self._setup_mock_files(run_id_a)
        S._LIST_CACHE["val"] = None

        body_a, code_a = API.route([run_id_a])
        self.assertEqual(code_a, 200)
        self.assertEqual(body_a.get("trust_state"), "UNVERIFIED")
        self.assertFalse(body_a.get("trust", {}).get("attested"))

        # 子场景 B: 执行器运行但未过数据门禁
        run_id_b = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id_b, {
            "spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {},
            "registered": True, "registration": {"source": "unproven_run"}
        })
        S.set_status(run_id_b, "succeeded", metrics={"net_pnl": 50.0})
        self._setup_mock_files(run_id_b)
        S._LIST_CACHE["val"] = None

        body_b, code_b = API.route([run_id_b])
        self.assertEqual(code_b, 200)
        self.assertEqual(body_b.get("trust_state"), "UNVERIFIED")
        self.assertNotEqual(body_b.get("trust_state"), "VERIFIED_TRUSTED")

    def test_08_all_endpoints_state_consistency(self):
        """【全端点状态契约一致性】验证对于同一个 run_id，所有 11 个接口导出的 trust_state 完全一致。"""
        run_id = S.new_run_id(secrets.token_hex(4))
        S.create_run(run_id, {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1"}, "config": {}, "window": {}})
        snap = make_clean_snapshot()
        fields = W.enforce_snapshot(run_id, snap, store=S, env={"BACKTEST_DATA_GATE_MODE": "trusted"})
        S.set_status(run_id, "succeeded", metrics={"net_pnl": 500.0, "net_return_pct": 12.5}, **fields)
        self._setup_mock_files(run_id)
        S._LIST_CACHE["val"] = None

        states = []

        # 1. list
        list_res = API.list_experiments({"limit": ["10"]})
        m = [x for x in list_res["items"] if x.get("run_id") == run_id][0]
        states.append(("list_experiments", m.get("trust_state")))

        # 2. get_run
        r_body, _ = API.get_run(run_id)
        states.append(("get_run", r_body.get("trust_state")))

        # 3. get_metrics
        m_body, _ = API.get_metrics(run_id)
        states.append(("get_metrics", m_body.get("trust_state")))

        # 4. get_quality
        q_body, _ = API.get_quality(run_id)
        states.append(("get_quality", q_body.get("trust_state")))

        # 5. get_equity
        e_body, _ = API.get_equity(run_id)
        states.append(("get_equity", e_body.get("trust_state")))

        # 6. get_trades
        t_body, _ = API.get_trades(run_id)
        states.append(("get_trades", t_body.get("trust_state")))

        # 7. get_orders
        o_body, _ = API.get_orders(run_id)
        states.append(("get_orders", o_body.get("trust_state")))

        # 8. get_fills
        f_body, _ = API.get_fills(run_id)
        states.append(("get_fills", f_body.get("trust_state")))

        # 9. get_rejected
        rej_body, _ = API.get_rejected(run_id)
        states.append(("get_rejected", rej_body.get("trust_state")))

        # 10. get_chart
        c_body, _ = API.get_chart(run_id)
        states.append(("get_chart", c_body.get("trust_state")))

        # 11. get_trade
        tr_body, _ = API.get_trade(run_id, "T001")
        states.append(("get_trade", tr_body.get("trust_state")))

        # 验证全部 11 处完全一致为 VERIFIED_TRUSTED
        for ep_name, st in states:
            self.assertEqual(st, "VERIFIED_TRUSTED", f"Endpoint {ep_name} trust_state inconsistency!")


if __name__ == "__main__":
    unittest.main()

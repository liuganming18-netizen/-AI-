#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-FIT-02 反例测试：首个正式实验必须能启动，但结果仍不能随便交付。

工单要求逐条落地：

  T01 **合法首次实验**：零历史记录（runs=0、trials=0）也能过真实
      `backtest_api.create_run` 并进入 `backtest_runner.execute_run`。
  T02 启动许可与结果交付资格**分开**：同一时刻「能启动」但「还不能交付」。
  T03 昂贵策略计算可以替换，**被测守卫与登记逻辑不许替换**（只 patch `_run`）。
  T04 完成 / 失败 / 放弃都留痕；没有结果时不能宣称正式交付通过。
  T05 闸门不得伪造记录（启动通过后 runs/trials 仍为 0）。
  T06 非法请求仍被拒绝：未登记、未冻结、身份漂移、窗口不符、已放弃、样本污染。
  T07 研究模式行为不变（自动登记、不具正式资格、允许运行）。

隔离长度 240h 的来源不是拍的：`evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet`
205 笔成交持有期 min=20h / 中位=240h / max=240h（100% ≤ 240h），与
`EventTrendConfig.max_holding_days=10` 一致。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FRAMEWORK = os.path.dirname(BACKEND)
for _p in (BACKEND, FRAMEWORK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import experiment_registry as E   # noqa: E402

WINDOW = ("2024-01-01", "2024-06-30")     # 与执行链写死窗口一致，避免窗口混杂
SYMBOLS = ["BTCUSDT"]


def _policy(holdout=("2026-10-25", "2026-11-25")):
    """train / valid / holdout，隔离带 240h（valid 起点比 train 终点晚 15 天 = 360h）。"""
    return E.SplitPolicy(
        train=("2024-01-01", "2024-03-31"),
        valid=("2024-04-15", "2024-06-30"),
        holdout=holdout,
        isolation_hours=240,
        label_span_source="实测 max(exit-entry)=240h（205 笔成交）",
        note="FRAME-FIT-02 测试用划分",
    )


class _Base(unittest.TestCase):
    """每个用例独立登记库 + 独立运行根；不碰生产目录。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="framefit02_"))
        self.reg_root = self.tmp / "registry"
        self.runs_root = self.tmp / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.reg = E.ExperimentRegistry(self.reg_root)
        self._env = mock.patch.dict(os.environ, {
            E.REGISTRY_ENV: str(self.reg_root),
            "EVENT_TREND_RUNS_DIR": str(self.runs_root),
            "INPROCESS_JOBS": "1",                       # 不投递任务队列
            "JOBS_DB": str(self.tmp / "jobs.sqlite3"),   # 万一走队列也只落临时库
            # 留出段守卫读的是 `data/experiments/holdout.json`。用例不该被
            # 「本机当前有没有配留出段」影响 ⇒ 指到一个不存在的临时文件。
            "FRAME_HOLDOUT_FILE": str(self.tmp / "holdout.json"),
        })
        self._env.start()
        # ⚠ `JOBS_ENABLED` 是 import 时算的模块级常量，改环境变量对它无效 ⇒
        #   必须直接把属性压成 False，否则 create_run 会往**生产任务库**投递
        #   （工单边界：不许写生产数据）。
        import backtest_api as _A
        self._jobs = mock.patch.object(_A, "JOBS_ENABLED", False)
        self._jobs.start()

    def tearDown(self):
        self._jobs.stop()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 造数据 ──────────────────────────────────────────
    def scope(self, symbols=None, ws=None, we=None):
        return {"symbols": list(symbols or SYMBOLS),
                "window_start": ws or WINDOW[0],
                "window_end": we or WINDOW[1],
                "source": "request"}

    def register_formal(self, eid="exp_first", *, policy=None, scope=None,
                        design=None):
        policy = policy or _policy()
        scope = scope or self.scope()
        parts = design or E.current_design_parts(
            sample_scope=scope, split_policy=policy,
            metrics_spec=E.DEFAULT_METRICS_SPEC)
        return self.reg.register(
            design=parts, sample_scope=scope, split_policy=policy,
            metrics_spec=E.DEFAULT_METRICS_SPEC,
            experiment_id=eid, mode=E.MODE_FORMAL,
            by="test:FRAME-FIT-02", note="首个正式实验")

    def payload(self, eid, *, mode="formal", ws=None, we=None):
        return {
            "symbols": list(SYMBOLS),
            "window_start": ws or WINDOW[0],
            "window_end": we or WINDOW[1],
            "experiment": {"mode": mode, "experiment_id": eid},
        }

    def _api(self):
        import backtest_api as A
        return A

    def _runner(self):
        import backtest_runner as RUN
        return RUN

    # ── 真实入口封装（只替换昂贵策略计算）────────────────
    def create(self, payload):
        """走真实 `create_run`，只把后台执行线程换成空函数。"""
        A = self._api()
        with mock.patch.object(A, "_run_in_thread", lambda rid: None):
            return A.create_run(payload)

    def execute(self, run_id, *, out=None, raises=None):
        """走真实 `execute_run`，只替换 `_run`（昂贵策略计算）。"""
        RUN = self._runner()
        fake = mock.Mock(side_effect=raises) if raises else mock.Mock(return_value=out)
        with mock.patch.object(RUN, "_run", fake):
            return RUN.execute_run(run_id)


# ══════════════════════ T01 合法首次实验能启动 ══════════════════════

class T01_FirstFormalLaunch(_Base):

    def test_first_formal_experiment_has_zero_history(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        exp = self.reg.state()["experiments"]["exp_first"]
        self.assertEqual(exp["runs"], [])
        self.assertEqual(exp["trials"], [])
        self.assertTrue(exp["frozen_at"])

    def test_guard_create_allows_zero_history(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        gate = E.guard_create(self.payload("exp_first"), registry=self.reg)
        self.assertTrue(gate["ok"], gate["blockers"])
        self.assertEqual(gate["stage"], E.STAGE_LAUNCH)
        self.assertEqual(gate["blockers"], [])
        # 启动通过 ≠ 可交付
        self.assertFalse(gate["delivery_eligible"])
        self.assertTrue(any("尚无已登记运行" in b for b in gate["delivery_blockers"]))

    def test_create_run_returns_202_for_first_formal(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        resp, code = self.create(self.payload("exp_first"))
        self.assertEqual(code, 202, resp)
        self.assertIsNotNone(resp.get("run_id"))
        self.assertEqual(resp["experiment"]["experiment_id"], "exp_first")
        # 落盘的 run 文档里带着实验绑定
        import backtest_store as S
        doc = S.read_run(resp["run_id"])
        self.assertEqual(doc["spec"]["experiment"]["experiment_id"], "exp_first")

    def test_execute_run_reaches_real_execution(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        resp, code = self.create(self.payload("exp_first"))
        self.assertEqual(code, 202, resp)
        doc = self.execute(resp["run_id"], out={"metrics": {"net_pnl": 12.5}})
        self.assertEqual(doc["status"], "succeeded", doc.get("failure_reason"))
        self.assertIsNone(doc.get("gate"))

    def test_full_chain_launch_then_deliver(self):
        """零历史 → 启动 → 跑完 → 才具备交付资格。"""
        self.register_formal()
        self.reg.freeze("exp_first")
        resp, code = self.create(self.payload("exp_first"))
        self.assertEqual(code, 202, resp)
        ok_before, why_before = self.reg.formal_eligible("exp_first")
        self.assertFalse(ok_before)
        self.assertTrue(any("尚无已登记运行" in w for w in why_before))

        rid = resp["run_id"]
        self.execute(rid, out={"metrics": {"net_pnl": 12.5, "trades_count": 3}})

        exp = self.reg.state()["experiments"]["exp_first"]
        self.assertEqual([r["run_id"] for r in exp["runs"]], [rid])
        self.assertEqual(len(exp["trials"]), 1)
        self.assertEqual(exp["trials"][0]["outcome"], "success")
        self.assertEqual(exp["trials"][0]["metrics"]["net_pnl"], 12.5)

        ok_after, why_after = self.reg.formal_eligible("exp_first")
        self.assertTrue(ok_after, why_after)


# ══════════════════════ T02 启动许可 vs 交付资格 ══════════════════════

class T02_StageSeparation(_Base):

    def test_launch_does_not_require_runs(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        ok, why = self.reg.formal_launchable("exp_first")
        self.assertTrue(ok, why)
        self.assertEqual(why, [])

    def test_delivery_requires_runs(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        ok, why = self.reg.formal_eligible("exp_first")
        self.assertFalse(ok)
        self.assertTrue(any("尚无已登记运行" in w for w in why), why)

    def test_runs_without_trials_blocks_delivery_only(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        self.reg.attach_run("exp_first", "bt_x", data_sha256="data-1")
        ok_launch, _ = self.reg.formal_launchable("exp_first")
        self.assertTrue(ok_launch)
        ok_deliver, why = self.reg.formal_eligible("exp_first")
        self.assertFalse(ok_deliver)
        self.assertTrue(any("只留赢家" in w for w in why), why)

    def test_trial_without_metrics_is_not_deliverable(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        self.reg.attach_run("exp_first", "bt_x", data_sha256="data-1")
        self.reg.record_trial("exp_first", outcome="failure", run_id="bt_x",
                              note="信号数为 0")
        ok, why = self.reg.formal_eligible("exp_first")
        self.assertFalse(ok)
        self.assertTrue(any("没有可交付的结果" in w for w in why), why)

    def test_run_without_its_own_trial_blocks_delivery(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        self.reg.attach_run("exp_first", "bt_a", data_sha256="d")
        self.reg.attach_run("exp_first", "bt_b", data_sha256="d")
        self.reg.record_trial("exp_first", outcome="success", run_id="bt_a",
                              metrics={"net_pnl": 1.0})
        ok, why = self.reg.formal_eligible("exp_first")
        self.assertFalse(ok)
        self.assertTrue(any("没有对应的试验结论" in w for w in why), why)


# ══════════════════════ T03 只换策略计算，不换守卫 ══════════════════════

class T03_RealGuardsNotReplaced(_Base):

    def test_guard_create_is_the_real_one(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        calls = []
        real = E.guard_create

        def spy(spec, **kw):
            calls.append(spec)
            return real(spec, **kw)

        A = self._api()
        with mock.patch.object(E, "guard_create", side_effect=spy) as m, \
                mock.patch.object(A, "_run_in_thread", lambda rid: None):
            A.create_run(self.payload("exp_first"))
        self.assertEqual(m.call_count, 1)
        self.assertEqual(len(calls), 1)

    def test_execute_uses_real_guard_and_registry(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        resp, code = self.create(self.payload("exp_first"))
        self.assertEqual(code, 202, resp)
        RUN = self._runner()
        seen = []
        real = E.guard_execute

        def spy(run_id, doc, **kw):
            seen.append(run_id)
            return real(run_id, doc, **kw)

        with mock.patch.object(E, "guard_execute", side_effect=spy), \
                mock.patch.object(RUN, "_run",
                                  mock.Mock(return_value={"metrics": {"net_pnl": 1.0}})):
            doc = RUN.execute_run(resp["run_id"])
        self.assertEqual(doc["status"], "succeeded", doc.get("failure_reason"))
        self.assertEqual(seen, [resp["run_id"]])
        # 真实登记逻辑确实跑了
        exp = self.reg.state()["experiments"]["exp_first"]
        self.assertEqual(len(exp["runs"]), 1)
        self.assertEqual(len(exp["trials"]), 1)

    def test_only_strategy_computation_is_mocked(self):
        """`_run` 被替换；`execute_run` / 闸门 / 登记库都是真身。"""
        self.register_formal()
        self.reg.freeze("exp_first")
        resp, code = self.create(self.payload("exp_first"))
        self.assertEqual(code, 202, resp)
        RUN = self._runner()
        with mock.patch.object(RUN, "_run",
                               mock.Mock(return_value={"metrics": {"net_pnl": 2.0}})) as m:
            doc = RUN.execute_run(resp["run_id"])
        self.assertEqual(m.call_count, 1)
        self.assertEqual(doc["status"], "succeeded")
        self.assertTrue(doc["experiment_record"]["recorded"])
        self.assertIsNone(doc["experiment_record"]["error"])


# ══════════════════════ T04 完成 / 失败 / 放弃都留痕 ══════════════════════

class T04_OutcomeRecorded(_Base):

    def _boot(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        resp, code = self.create(self.payload("exp_first"))
        self.assertEqual(code, 202, resp)
        return resp["run_id"]

    def test_success_recorded(self):
        rid = self._boot()
        doc = self.execute(rid, out={"metrics": {"net_pnl": 5.0}})
        self.assertEqual(doc["status"], "succeeded")
        exp = self.reg.state()["experiments"]["exp_first"]
        self.assertEqual(exp["trials"][0]["outcome"], "success")
        self.assertEqual(exp["trials"][0]["run_id"], rid)
        ok, why = self.reg.formal_eligible("exp_first")
        self.assertTrue(ok, why)

    def test_failure_recorded_and_not_deliverable(self):
        rid = self._boot()
        doc = self.execute(rid, raises=RuntimeError("模拟策略计算失败"))
        self.assertEqual(doc["status"], "failed")
        self.assertIn("模拟策略计算失败", doc.get("failure_reason") or "")
        exp = self.reg.state()["experiments"]["exp_first"]
        self.assertEqual(exp["trials"][0]["outcome"], "failure")
        self.assertIsNone(exp["trials"][0]["metrics"])
        ok, why = self.reg.formal_eligible("exp_first")
        self.assertFalse(ok)
        self.assertTrue(any("没有可交付的结果" in w for w in why), why)

    def test_abandon_recorded(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        self.reg.record_trial("exp_first", outcome="abandoned", note="样本不足")
        self.reg.abandon("exp_first", reason="方向判定错误，整组作废")
        exp = self.reg.state()["experiments"]["exp_first"]
        self.assertEqual(exp["trials"][0]["outcome"], "abandoned")
        self.assertIsNotNone(exp["abandoned"])
        ok_launch, why_launch = self.reg.formal_launchable("exp_first")
        self.assertFalse(ok_launch)
        self.assertTrue(any("已放弃" in w for w in why_launch), why_launch)
        ok_deliver, why_deliver = self.reg.formal_eligible("exp_first")
        self.assertFalse(ok_deliver)
        self.assertTrue(any("已放弃" in w for w in why_deliver), why_deliver)

    def test_gate_rejection_writes_no_record(self):
        """闸门拒绝 = 没真跑 ⇒ 不许留任何 run/trial 记录。"""
        self.register_formal()
        self.reg.freeze("exp_first")
        import backtest_store as S
        rid = "bt_20260101T000000Z_00ff02aa"
        S.create_run(rid, {"run_id": rid, "status": S.STATUS_QUEUED,
                           "strategy_id": "WX_EVENT_TREND_V0_1",
                           "spec": {"symbols": SYMBOLS,
                                    "window_start": WINDOW[0], "window_end": WINDOW[1],
                                    "experiment": {"experiment_id": "exp_ghost"}}})
        doc = self._runner().execute_run(rid)
        self.assertEqual(doc["status"], "failed")
        self.assertIn("未登记", doc.get("failure_reason") or "")
        self.assertIsNone(doc.get("experiment_record"))
        self.assertEqual(self.reg.state()["experiments"]["exp_first"]["runs"], [])


# ══════════════════════ T05 闸门不得伪造记录 ══════════════════════

class T05_NoFabrication(_Base):

    def test_launch_pass_keeps_zero_runs(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        before = len(self.reg.events())
        gate = E.guard_create(self.payload("exp_first"), registry=self.reg)
        self.assertTrue(gate["ok"], gate["blockers"])
        after = self.reg.events()
        self.assertEqual(len(after), before, "启动闸门不应写任何事件")
        exp = self.reg.state()["experiments"]["exp_first"]
        self.assertEqual(exp["runs"], [])
        self.assertEqual(exp["trials"], [])

    def test_record_execution_refuses_unfrozen(self):
        self.register_formal()
        doc = {"spec": {"experiment": {"experiment_id": "exp_first"}}}
        err = E.record_execution("bt_x", doc, outcome="success",
                                 metrics={"net_pnl": 1.0}, registry=self.reg)
        self.assertIsNotNone(err)
        self.assertIn("未冻结", err)
        self.assertEqual(self.reg.state()["experiments"]["exp_first"]["runs"], [])

    def test_record_execution_refuses_unregistered(self):
        doc = {"spec": {"experiment": {"experiment_id": "exp_ghost"}}}
        err = E.record_execution("bt_x", doc, outcome="success", registry=self.reg)
        self.assertIsNotNone(err)
        self.assertIn("未登记", err)

    def test_record_execution_is_idempotent(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        doc = {"spec": {"experiment": {"experiment_id": "exp_first"}}}
        self.assertIsNone(E.record_execution("bt_x", doc, outcome="success",
                                             metrics={"net_pnl": 1.0}, registry=self.reg))
        self.assertIsNone(E.record_execution("bt_x", doc, outcome="success",
                                             metrics={"net_pnl": 1.0}, registry=self.reg))
        exp = self.reg.state()["experiments"]["exp_first"]
        self.assertEqual(len(exp["runs"]), 1)
        self.assertEqual(len(exp["trials"]), 1)


# ══════════════════════ T06 非法请求仍被拒绝 ══════════════════════

class T06_IllegalStillRejected(_Base):

    def _expect_409(self, payload, needle):
        resp, code = self.create(payload)
        self.assertEqual(code, 409, resp)
        joined = "；".join(resp.get("errors") or [])
        self.assertIn(needle, joined)
        return resp

    def test_unregistered_rejected(self):
        self._expect_409(self.payload("exp_ghost"), "实验未登记")

    def test_unfrozen_rejected(self):
        self.register_formal()
        self._expect_409(self.payload("exp_first"), "配置未冻结")

    def test_identity_drift_rejected(self):
        policy = _policy()
        scope = self.scope()
        stale = E.current_design_parts(sample_scope=scope, split_policy=policy,
                                       metrics_spec=E.DEFAULT_METRICS_SPEC)
        stale["config_sha256"] = "0" * 64
        self.register_formal(design=stale)
        self.reg.freeze("exp_first")
        self._expect_409(self.payload("exp_first"), "与登记身份不一致")

    def test_pinned_window_mismatch_rejected(self):
        """请求窗口与登记窗口不一致 ⇒ 拒绝（由设计身份比对拦住）。

        **本用例改写过（2026-10-24）**：旧版本登记 2025 窗口后请求**同一个**
        2025 窗口，却期望 409 且理由含「写死」。那是在断言
        `backtest_runner._run_event_trend` 把窗口写死成 2024H1 这个缺陷还在。
        缺陷已删除，同窗口请求本来就该受理 —— 继续要求 409 等于要求保留缺陷。

        现在测的是真正该守的东西：**换了窗口就必须拒**，
        否则请求的样本范围与登记不符，跑出来的结果会被当成登记实验的结果。
        """
        policy = _policy()
        scope = self.scope(ws="2025-01-01", we="2025-06-30")
        parts = E.current_design_parts(sample_scope=scope, split_policy=policy,
                                       metrics_spec=E.DEFAULT_METRICS_SPEC)
        self.register_formal(design=parts, scope=scope)
        self.reg.freeze("exp_first")
        self._expect_409(self.payload("exp_first", ws="2024-01-01", we="2024-06-30"),
                         "与登记身份不一致")

    def test_abandoned_rejected(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        self.reg.abandon("exp_first", reason="方向错了")
        self._expect_409(self.payload("exp_first"), "已放弃")

    def test_unconfigured_split_rejected(self):
        scope = self.scope()
        parts = E.current_design_parts(sample_scope=scope, split_policy=E.SplitPolicy(),
                                       metrics_spec=E.DEFAULT_METRICS_SPEC)
        self.reg.register(design=parts, sample_scope=scope,
                          split_policy=E.SplitPolicy(),
                          metrics_spec=E.DEFAULT_METRICS_SPEC,
                          experiment_id="exp_nosplit", mode=E.MODE_FORMAL)
        self.reg.freeze("exp_nosplit")
        self._expect_409(self.payload("exp_nosplit"), "分组未配置")

    def test_contaminated_holdout_rejected(self):
        self.register_formal()
        self.reg.freeze("exp_first")
        ms = E.parse_date_ms("2026-11-01")
        self.reg.observe("seen_set", [f"BTCUSDT|{ms}"], rows=1,
                         by="test", purpose="已经看过")
        self._expect_409(self.payload("exp_first"), "已观察样本")

    def test_guard_execute_still_blocks_unfrozen(self):
        self.register_formal()
        doc = {"spec": {"symbols": SYMBOLS, "window_start": WINDOW[0],
                        "window_end": WINDOW[1],
                        "experiment": {"experiment_id": "exp_first"}}}
        err = E.guard_execute("bt_x", doc, registry=self.reg)
        self.assertIsNotNone(err)
        self.assertIn("未冻结", err)

    def test_guard_execute_still_blocks_identity_drift(self):
        policy = _policy()
        scope = self.scope()
        stale = E.current_design_parts(sample_scope=scope, split_policy=policy,
                                       metrics_spec=E.DEFAULT_METRICS_SPEC)
        stale["config_sha256"] = "0" * 64
        self.register_formal(design=stale)
        self.reg.freeze("exp_first")
        doc = {"spec": {"symbols": SYMBOLS, "window_start": WINDOW[0],
                        "window_end": WINDOW[1],
                        "experiment": {"experiment_id": "exp_first"}}}
        err = E.guard_execute("bt_x", doc, registry=self.reg)
        self.assertIsNotNone(err)
        self.assertIn("设计身份漂移", err)
        exp = self.reg.state()["experiments"]["exp_first"]
        self.assertIsNone(exp["frozen_at"])
        self.assertEqual(len(exp["invalidations"]), 1)


# ══════════════════════ T07 研究模式不变 ══════════════════════

class T07_ResearchUnchanged(_Base):

    def test_research_run_still_allowed_and_not_formal(self):
        A = self._api()
        payload = {"symbols": SYMBOLS,
                   "window_start": WINDOW[0], "window_end": WINDOW[1]}
        with mock.patch.object(A, "_run_in_thread", lambda rid: None):
            resp, code = A.create_run(payload)
        self.assertEqual(code, 202, resp)
        ex = resp["experiment"]
        self.assertEqual(ex["mode"], "research")
        self.assertFalse(ex["formal_eligible"])
        self.assertTrue(any("不具正式实验资格" in x for x in ex["formal_blockers"]))

    def test_research_run_is_frozen_and_writes_trial(self):
        """研究运行在创建时即冻结 ⇒ 执行闸门放行，并如实写下 run + trial。

        ⚠ **行为已变更（2026-10-19）**：原实现只登记不冻结，于是每条研究回测
        都以「实验未冻结」失败、`record_execution` 也跟着拒绝留痕。实测后果是
        **2,125 个回测产物目录对应登记库 0 条业务记录** —— 账本形同虚设。

        现在 `backtest_api.create_run` 在创建时冻结设计身份。语义是
        「跑之前先把设计定死」：设计身份由代码+参数+样本+分组+指标决定，
        任一项变了就是另一条实验，不会互相污染。

        冻结只解锁「能跑、能留痕」，**不改变研究模式不具正式资格**这一条
        （`formal_eligible` 恒 False）。
        """
        A = self._api()
        payload = {"symbols": SYMBOLS,
                   "window_start": WINDOW[0], "window_end": WINDOW[1]}
        with mock.patch.object(A, "_run_in_thread", lambda rid: None):
            resp, code = A.create_run(payload)
        self.assertEqual(code, 202, resp)
        eid = resp["experiment"]["experiment_id"]

        # 创建即冻结（这是本次修复的核心）
        self.assertTrue(self.reg.state()["experiments"][eid]["frozen_at"])

        doc = self.execute(resp["run_id"], out={"metrics": {"net_pnl": 1.0}})
        self.assertEqual(doc["status"], "succeeded")

        rec = doc.get("experiment_record") or {}
        self.assertTrue(rec.get("recorded"), f"应当写入登记，实际：{rec}")

        exp = self.reg.state()["experiments"][eid]
        self.assertEqual([r["run_id"] for r in exp["runs"]], [resp["run_id"]])
        self.assertEqual([t["outcome"] for t in exp["trials"]], ["success"])

        # 能跑能留痕 ≠ 有正式资格
        ok, _why = self.reg.formal_eligible(eid)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)

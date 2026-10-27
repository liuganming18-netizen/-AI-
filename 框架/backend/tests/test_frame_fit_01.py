#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-FIT-01 反例测试：实验隔离、参数冻结与留出集使用约束。

每条都对应工单里一句**必须实际执行**的话，不是形式检查：

  T01 事件流不可静默改写（哈希链 / 截断）
  T02 未冻结配置不能解封最终留出
  T03 已观察样本不能重新声明为未见留出
  T04 参数/数据/规则/样本变更产生新身份，不能覆盖旧结果
  T05 跨分组标签重叠、重复样本可识别并阻断
  T06 失败和被放弃的试验也进入记录（不只留赢家）
  T07 **真实启动入口**执行上述约束（`create_run` / `execute_run`）

T07 走的是生产入口函数本体：`backtest_api.create_run` 与
`backtest_runner.execute_run`。为避免真的跑策略（工单禁止跑完整策略实验），
T07 只覆盖**拒绝路径**与**受理但登记**的路径，并把后台执行线程替换掉。

隔离长度的依据来自实测标签跨度，不是拍脑袋的天数：
`evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet` 的 205 笔成交
持有期 min=20h / 中位=240h / max=240h（100% ≤ 240h），与
`EventTrendConfig.max_holding_days=10` 一致 ⇒ 最小隔离 = 240 小时。
"""
from __future__ import annotations

import json
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

# 实测标签跨度（小时）：ENTRY-AUDIT-04 交接包 205 笔成交的持有期分布
MEASURED_SPANS = [20.0, 240.0, 240.0, 193.5, 240.0, 120.0]


# ══════════════════════════ 夹具 ══════════════════════════

class _Base(unittest.TestCase):
    """每个用例独立登记库 + 独立运行根（不碰生产目录）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="framefit_"))
        self.reg_root = self.tmp / "registry"
        self.runs_root = self.tmp / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.reg = E.ExperimentRegistry(self.reg_root)
        self._env = mock.patch.dict(os.environ, {
            E.REGISTRY_ENV: str(self.reg_root),
            "EVENT_TREND_RUNS_DIR": str(self.runs_root),
        })
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 造数据 ──────────────────────────────────────────
    def design(self, *, config_sha="cfg-aaa", code_sha="code-aaa",
               rules_sha="rules-aaa", version="v0.1.0"):
        return {
            "strategy_id": "WX_EVENT_TREND_V0_1",
            "strategy_version": version,
            "code_sha256": code_sha,
            "config_sha256": config_sha,
            "rules_sha256": rules_sha,
        }

    def scope(self, *, symbols=("BTCUSDT",), ws="2021-01-01", we="2026-10-18"):
        return {"symbols": list(symbols), "window_start": ws, "window_end": we,
                "source": "test"}

    def policy(self, *, iso=240, train=("2021-01-01", "2023-12-31"),
               valid=("2024-01-11", "2025-06-30"),
               holdout=("2025-07-11", "2026-10-18")):
        return E.SplitPolicy(train=train, valid=valid, holdout=holdout,
                             isolation_hours=iso,
                             label_span_source="实测 max(exit-entry)=240h（205 笔成交）",
                             note="测试用划分；正式划分未配置")

    def register(self, eid="exp_t1", **kw):
        return self.reg.register(
            design=kw.pop("design", self.design()),
            sample_scope=kw.pop("scope", self.scope()),
            split_policy=kw.pop("policy", self.policy()),
            metrics_spec=kw.pop("metrics", E.DEFAULT_METRICS_SPEC),
            experiment_id=eid, **kw)


# ══════════════════════════ T01 不可静默改写 ══════════════════════════

class T01_AppendOnly(_Base):

    def test_chain_verifies_after_appends(self):
        self.register("exp_a")
        self.reg.freeze("exp_a")
        v = self.reg.verify()
        self.assertTrue(v["ok"], v["problems"])
        self.assertEqual(v["count"], 2)

    def test_middle_row_tamper_detected(self):
        self.register("exp_a")
        self.reg.freeze("exp_a")
        p = self.reg_root / E.EVENTS_FILENAME
        lines = p.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[0])
        row["event"]["note"] = "偷偷改掉备注"
        lines[0] = json.dumps(row, ensure_ascii=False, sort_keys=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        v = self.reg.verify()
        self.assertFalse(v["ok"])
        self.assertTrue(any("hash 不符" in x for x in v["problems"]), v["problems"])
        with self.assertRaises(E.RegistryTampered):
            self.reg.state()

    def test_tail_truncation_detected(self):
        self.register("exp_a")
        self.reg.freeze("exp_a")
        p = self.reg_root / E.EVENTS_FILENAME
        lines = p.read_text(encoding="utf-8").splitlines()
        p.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        v = self.reg.verify()
        self.assertFalse(v["ok"])
        self.assertTrue(any("head" in x for x in v["problems"]), v["problems"])


# ══════════════════════════ T02 未冻结不能解封 ══════════════════════════

class T02_FreezeBeforeUnseal(_Base):

    def test_unseal_without_freeze_blocked(self):
        self.register("exp_a")
        with self.assertRaises(E.ExperimentBlocked) as cm:
            self.reg.unseal_holdout("exp_a", by="tester")
        joined = "；".join(cm.exception.reasons)
        self.assertIn("未冻结", joined)
        self.assertIsNone(self.reg.state()["experiments"]["exp_a"]["unsealed_at"])

    def test_unseal_with_unconfigured_split_blocked_even_if_frozen(self):
        self.register("exp_a", policy=E.SplitPolicy())   # 全部 None
        self.reg.freeze("exp_a")
        with self.assertRaises(E.ExperimentBlocked) as cm:
            self.reg.unseal_holdout("exp_a", by="tester")
        joined = "；".join(cm.exception.reasons)
        self.assertIn("分组未配置", joined)

    def test_unseal_ok_when_frozen_and_split_valid(self):
        self.register("exp_a")
        self.reg.freeze("exp_a")
        st = self.reg.unseal_holdout("exp_a", by="tester", note="首次解封")
        self.assertIsNotNone(st["unsealed_at"])

    def test_second_unseal_blocked(self):
        self.register("exp_a")
        self.reg.freeze("exp_a")
        self.reg.unseal_holdout("exp_a", by="tester")
        with self.assertRaises(E.ExperimentBlocked) as cm:
            self.reg.unseal_holdout("exp_a", by="tester")
        self.assertIn("解封过", "；".join(cm.exception.reasons))

    def test_freeze_twice_blocked(self):
        self.register("exp_a")
        self.reg.freeze("exp_a")
        with self.assertRaises(E.ExperimentBlocked):
            self.reg.freeze("exp_a")


# ══════════════════════════ T03 已观察 ≠ 未见留出 ══════════════════════════

class T03_ObservedNotHoldout(_Base):

    def test_observe_requires_keys(self):
        with self.assertRaises(E.ExperimentBlocked):
            self.reg.observe("EA04_227", [])

    def test_observed_set_recorded(self):
        keys = [f"BTCUSDT|{1762023600000 + i}" for i in range(3)]
        s = self.reg.observe("EA04_227", keys, rows=227, by="tester",
                             purpose="反复查看/比较/调参")
        self.assertEqual(s["rows"], 227)
        self.assertEqual(len(s["keys"]), 3)

    def test_holdout_containing_observed_samples_blocked(self):
        # 留出段 2025-07-11 ~ 2026-10-18；把一条已观察样本的入场时刻放进该段
        hold_entry = E.parse_date_ms("2025-08-01")
        self.reg.observe("EA04_227", [f"BTCUSDT|{hold_entry}"], rows=1, by="tester")
        self.register("exp_a")
        self.reg.freeze("exp_a")
        with self.assertRaises(E.ExperimentBlocked) as cm:
            self.reg.unseal_holdout("exp_a", by="tester")
        joined = "；".join(cm.exception.reasons)
        self.assertIn("已观察样本", joined)
        self.assertIn("EA04_227", joined)

    def test_observed_outside_holdout_does_not_block(self):
        # 观察样本落在训练段 ⇒ 不影响留出
        self.reg.observe("OLD_RESEARCH", ["BTCUSDT|" + str(E.parse_date_ms("2022-03-01"))],
                         rows=1, by="tester")
        self.register("exp_a")
        self.reg.freeze("exp_a")
        st = self.reg.unseal_holdout("exp_a", by="tester")
        self.assertIsNotNone(st["unsealed_at"])


# ══════════════════════════ T04 变更产生新身份 ══════════════════════════

class T04_IdentityDerivation(_Base):

    def _ident(self, **kw):
        parts = self.design(**kw)
        parts["sample_scope"] = E.normalize_sample_scope(self.scope())
        parts["split_policy"] = self.policy().sha()
        parts["metrics_spec"] = E.DEFAULT_METRICS_SPEC
        return E.compute_design_identity(parts)

    def test_param_change_new_identity(self):
        a = self._ident(config_sha="cfg-aaa")
        b = self._ident(config_sha="cfg-bbb")
        self.assertNotEqual(a, b)

    def test_code_change_new_identity(self):
        self.assertNotEqual(self._ident(code_sha="c1"), self._ident(code_sha="c2"))

    def test_rule_change_new_identity(self):
        self.assertNotEqual(self._ident(rules_sha="r1"), self._ident(rules_sha="r2"))

    def test_sample_list_change_new_identity(self):
        parts = self.design()
        parts["split_policy"] = self.policy().sha()
        parts["metrics_spec"] = E.DEFAULT_METRICS_SPEC
        parts["sample_scope"] = E.normalize_sample_scope(self.scope(symbols=("BTCUSDT",)))
        a = E.compute_design_identity(parts)
        parts["sample_scope"] = E.normalize_sample_scope(
            self.scope(symbols=("BTCUSDT", "ETHUSDT")))
        b = E.compute_design_identity(parts)
        self.assertNotEqual(a, b)

    def test_split_change_new_identity(self):
        p1 = self.policy().sha()
        p2 = self.policy(iso=336).sha()
        self.assertNotEqual(p1, p2)

    def test_data_change_new_run_identity(self):
        d = "design-xyz"
        self.assertNotEqual(E.compute_run_identity(d, "data-1"),
                            E.compute_run_identity(d, "data-2"))

    def test_same_identity_cannot_be_reregistered(self):
        self.register("exp_a")
        with self.assertRaises(E.ExperimentBlocked) as cm:
            self.register("exp_a_copy")
        joined = "；".join(cm.exception.reasons)
        self.assertIn("同一设计身份", joined)

    def test_changed_identity_requires_parent_link(self):
        self.register("exp_v1")
        st = self.register("exp_v2", design=self.design(config_sha="cfg-bbb"),
                           parent_experiment_id="exp_v1")
        self.assertEqual(st["parent_experiment_id"], "exp_v1")
        self.assertNotEqual(
            self.reg.state()["experiments"]["exp_v1"]["design_identity"],
            st["design_identity"])

    def test_parent_must_exist(self):
        with self.assertRaises(E.ExperimentBlocked):
            self.register("exp_v2", design=self.design(config_sha="cfg-bbb"),
                          parent_experiment_id="exp_nope")

    def test_frozen_identity_drift_invalidates(self):
        self.register("exp_a")
        self.reg.freeze("exp_a")
        self.reg.invalidate("exp_a", actual_identity="deadbeef", reason="参数被改")
        exp = self.reg.state()["experiments"]["exp_a"]
        self.assertIsNone(exp["frozen_at"])
        self.assertEqual(len(exp["invalidations"]), 1)


# ══════════════════════════ T05 标签跨界 / 重复样本 ══════════════════════════

class T05_Leakage(_Base):

    def test_isolation_derived_from_measured_spans(self):
        self.assertEqual(E.required_isolation_hours(MEASURED_SPANS), 240)
        self.assertEqual(E.required_isolation_hours([20.0, 240.4]), 241)  # 向上取整
        self.assertIsNone(E.required_isolation_hours([]))                # UNKNOWN 不猜
        self.assertIsNone(E.required_isolation_hours([None, None]))

    def test_insufficient_isolation_rejected(self):
        p = self.policy(train=("2021-01-01", "2023-12-31"),
                        valid=("2024-01-05", "2025-06-30"),   # 间隔 5 天 < 240h
                        holdout=("2025-07-11", "2026-10-18"))
        errs = p.validate()
        self.assertTrue(any("小于标签跨度" in e for e in errs), errs)

    def test_overlapping_segments_rejected(self):
        p = self.policy(train=("2021-01-01", "2024-06-30"),
                        valid=("2024-01-11", "2025-06-30"))
        errs = p.validate()
        self.assertTrue(any("重叠" in e for e in errs), errs)

    def test_label_crossing_detected(self):
        """出场越过**下一段起点** ⇒ 标签跨界（落进隔离带内不算）。"""
        p = self.policy()
        train_end = E.parse_date_ms("2023-12-31")
        rows = [{
            "symbol": "BTCUSDT",
            "entry_ms": train_end - 3600_000 * 5,          # 训练段末尾前 5h 入场
            "exit_ms": train_end + 3600_000 * 500,         # 持有 500h ⇒ 越过验证段起点
        }]
        errs = E.check_leakage(rows, p)
        self.assertTrue(any("标签跨界" in e for e in errs), errs)

    def test_exit_inside_isolation_band_is_not_crossing(self):
        """出场落在隔离带内 ⇒ 不算跨界（隔离带就是用来吸收这段尾巴的）。"""
        p = self.policy()                                  # 训练止 2023-12-31，验证起 2024-01-11
        train_end = E.parse_date_ms("2023-12-31")
        rows = [{
            "symbol": "BTCUSDT",
            "entry_ms": train_end - 3600_000 * 5,
            "exit_ms": train_end + 3600_000 * 100,         # 落在 10 天隔离带内
        }]
        self.assertEqual(E.check_leakage(rows, p), [])

    def test_duplicate_sample_detected(self):
        p = self.policy()
        t = E.parse_date_ms("2022-05-01")
        rows = [{"symbol": "BTCUSDT", "entry_ms": t, "exit_ms": t + 3600_000 * 24},
                {"symbol": "BTCUSDT", "entry_ms": t, "exit_ms": t + 3600_000 * 24}]
        errs = E.check_leakage(rows, p)
        self.assertTrue(any("重复样本" in e for e in errs), errs)

    def test_unconfigured_split_cannot_pass_leakage_check(self):
        errs = E.check_leakage([], E.SplitPolicy())
        self.assertTrue(any("无法进行泄漏检查" in e for e in errs), errs)

    def test_clean_sample_passes(self):
        p = self.policy()
        t = E.parse_date_ms("2022-05-01")
        rows = [{"symbol": "BTCUSDT", "entry_ms": t, "exit_ms": t + 3600_000 * 240}]
        self.assertEqual(E.check_leakage(rows, p), [])


# ══════════════════════════ T06 失败/放弃也入册 ══════════════════════════

class T06_TrialLedger(_Base):

    def test_failed_trial_is_recorded(self):
        self.register("exp_a")
        self.reg.freeze("exp_a")
        self.reg.attach_run("exp_a", "bt_x", data_sha256="data-1")
        self.reg.record_trial("exp_a", outcome="failure", run_id="bt_x",
                              note="信号数为 0")
        exp = self.reg.state()["experiments"]["exp_a"]
        self.assertEqual(len(exp["trials"]), 1)
        self.assertEqual(exp["trials"][0]["outcome"], "failure")

    def test_abandoned_trial_and_experiment_recorded(self):
        self.register("exp_a")
        self.reg.freeze("exp_a")
        self.reg.record_trial("exp_a", outcome="abandoned", note="样本不足")
        self.reg.abandon("exp_a", reason="方向判定错误，整组作废")
        exp = self.reg.state()["experiments"]["exp_a"]
        self.assertEqual(exp["trials"][0]["outcome"], "abandoned")
        self.assertIsNotNone(exp["abandoned"])

    def test_unknown_outcome_rejected(self):
        self.register("exp_a")
        with self.assertRaises(E.ExperimentBlocked):
            self.reg.record_trial("exp_a", outcome="maybe")

    def test_runs_without_trials_blocks_formal(self):
        self.register("exp_a", mode=E.MODE_FORMAL)
        self.reg.freeze("exp_a")
        self.reg.attach_run("exp_a", "bt_x", data_sha256="data-1")
        ok, why = self.reg.formal_eligible("exp_a")
        self.assertFalse(ok)
        self.assertTrue(any("只留赢家" in w for w in why), why)

    def test_formal_eligible_when_complete(self):
        self.register("exp_a", mode=E.MODE_FORMAL)
        self.reg.freeze("exp_a")
        self.reg.attach_run("exp_a", "bt_x", data_sha256="data-1")
        self.reg.record_trial("exp_a", outcome="success", run_id="bt_x",
                              metrics={"net_pnl": 1.0}, role=E.ROLE_VALID)
        ok, why = self.reg.formal_eligible("exp_a")
        self.assertTrue(ok, why)

    def test_run_requires_freeze(self):
        self.register("exp_a")
        with self.assertRaises(E.ExperimentBlocked) as cm:
            self.reg.attach_run("exp_a", "bt_x", data_sha256="data-1")
        self.assertIn("未冻结", "；".join(cm.exception.reasons))

    def test_unknown_data_fingerprint_not_faked(self):
        self.register("exp_a")
        self.reg.freeze("exp_a")
        st = self.reg.attach_run("exp_a", "bt_x", data_sha256=None)
        self.assertIsNone(st["runs"][0]["data_sha256"])


# ══════════════════════════ T07 真实启动入口 ══════════════════════════

class T07_RealEntrypoint(_Base):
    """走 `backtest_api.create_run` / `backtest_runner.execute_run` 本体。"""

    def _api(self):
        import backtest_api as A
        return A

    def _runner(self):
        import backtest_runner as RUN
        return RUN

    def test_create_run_research_mode_registers_and_allows(self):
        A = self._api()
        payload = {"symbols": ["BTCUSDT"],
                   "window_start": "2024-01-01", "window_end": "2024-06-30"}
        with mock.patch.object(A, "_run_in_thread", lambda rid: None):
            resp, code = A.create_run(payload)
        self.assertEqual(code, 202, resp)
        self.assertIsNotNone(resp["run_id"])
        ex = resp["experiment"]
        self.assertEqual(ex["mode"], "research")
        self.assertFalse(ex["formal_eligible"])
        self.assertTrue(any("不具正式实验资格" in x for x in ex["formal_blockers"]))
        # 登记库真的落了一条
        regs = E.get_registry().list_experiments()
        self.assertTrue(any(r["experiment_id"] == ex["experiment_id"] for r in regs))

    def test_create_run_formal_without_freeze_rejected_409(self):
        A = self._api()
        payload = {"symbols": ["BTCUSDT"],
                   "window_start": "2024-01-01", "window_end": "2024-06-30",
                   "experiment": {"mode": "formal", "experiment_id": "exp_nope"}}
        with mock.patch.object(A, "_run_in_thread", lambda rid: None):
            resp, code = A.create_run(payload)
        self.assertEqual(code, 409, resp)
        self.assertIn("实验资格校验未通过", resp["missing_reason"])
        self.assertTrue(resp["errors"])

    def test_create_run_formal_with_frozen_but_unconfigured_split_rejected(self):
        A = self._api()
        # 先按当前真实指纹登记一个「已冻结但分组未配置」的正式实验
        parts = E.current_design_parts(
            sample_scope={"symbols": ["BTCUSDT"], "window_start": "2024-01-01",
                          "window_end": "2024-06-30"},
            split_policy=E.SplitPolicy(), metrics_spec=E.DEFAULT_METRICS_SPEC)
        self.reg.register(design=parts,
                          sample_scope={"symbols": ["BTCUSDT"],
                                        "window_start": "2024-01-01",
                                        "window_end": "2024-06-30"},
                          split_policy=E.SplitPolicy(),
                          metrics_spec=E.DEFAULT_METRICS_SPEC,
                          experiment_id="exp_formal_t", mode=E.MODE_FORMAL)
        self.reg.freeze("exp_formal_t")
        payload = {"symbols": ["BTCUSDT"],
                   "window_start": "2024-01-01", "window_end": "2024-06-30",
                   "experiment": {"mode": "formal", "experiment_id": "exp_formal_t"}}
        with mock.patch.object(A, "_run_in_thread", lambda rid: None):
            resp, code = A.create_run(payload)
        self.assertEqual(code, 409, resp)
        joined = "；".join(resp["errors"])
        self.assertIn("分组未配置", joined)

    def test_create_run_identity_drift_rejected(self):
        A = self._api()
        # 登记时用旧参数指纹；当前指纹不同 ⇒ 身份不一致
        stale = E.current_design_parts(
            sample_scope={"symbols": ["BTCUSDT"], "window_start": "2024-01-01",
                          "window_end": "2024-06-30"},
            split_policy=E.SplitPolicy(), metrics_spec=E.DEFAULT_METRICS_SPEC)
        stale["config_sha256"] = "0" * 64
        self.reg.register(design=stale,
                          sample_scope={"symbols": ["BTCUSDT"],
                                        "window_start": "2024-01-01",
                                        "window_end": "2024-06-30"},
                          split_policy=E.SplitPolicy(),
                          metrics_spec=E.DEFAULT_METRICS_SPEC,
                          experiment_id="exp_stale", mode=E.MODE_FORMAL)
        self.reg.freeze("exp_stale")
        payload = {"symbols": ["BTCUSDT"],
                   "window_start": "2024-01-01", "window_end": "2024-06-30",
                   "experiment": {"mode": "formal", "experiment_id": "exp_stale"}}
        with mock.patch.object(A, "_run_in_thread", lambda rid: None):
            resp, code = A.create_run(payload)
        self.assertEqual(code, 409, resp)
        self.assertIn("与登记身份不一致", "；".join(resp["errors"]))

    def test_create_run_rejects_pinned_window_mismatch(self):
        """请求窗口必须与登记窗口一致 —— 不一致就拒，一致就按请求窗口跑。

        **本用例改写过（2026-10-24），原因必须留在这里：**
        旧版本断言「请求非 2024H1 窗口 ⇒ 409 且错误信息含『写死』」。
        它守护的是当时一个真实缺陷：`backtest_runner._run_event_trend`
        把窗口写死成 `2024-01-01 ~ 2024-06-30`，请求里的窗口在真实链路上不生效。

        那个写死已经删掉了（现在 `backtest_runner.py` 里搜不到这两个日期），
        执行链严格使用请求显式窗口。所以「同窗口却要 409」这个断言前提不存在了 ——
        继续要求 409 等于要求代码保留缺陷。

        改成守护**新契约**，两条都要成立：
          ① 请求窗口 == 登记窗口 ⇒ 受理（202）。若这里又变成 409，
             说明有人把窗口检查写回去误伤了合法请求。
          ② 请求窗口 != 登记窗口 ⇒ 拒绝（409）。若这里变成 202，
             说明样本范围与登记脱钩，跑出来的东西会被当成登记实验的结果 ——
             这是必须拦住的。
        """
        A = self._api()
        scope = {"symbols": ["BTCUSDT"], "window_start": "2025-01-01",
                 "window_end": "2025-06-30"}
        parts = E.current_design_parts(
            sample_scope=scope,
            split_policy=self.policy(), metrics_spec=E.DEFAULT_METRICS_SPEC)
        self.reg.register(design=parts, sample_scope=scope,
                          split_policy=self.policy(),
                          metrics_spec=E.DEFAULT_METRICS_SPEC,
                          experiment_id="exp_pin", mode=E.MODE_FORMAL)
        self.reg.freeze("exp_pin")

        # ① 同窗口 ⇒ 受理
        payload = {"symbols": ["BTCUSDT"],
                   "window_start": "2025-01-01", "window_end": "2025-06-30",
                   "experiment": {"mode": "formal", "experiment_id": "exp_pin"}}
        with mock.patch.object(A, "_run_in_thread", lambda rid: None):
            resp, code = A.create_run(payload)
        self.assertEqual(code, 202, resp)

        # ② 换窗口 ⇒ 拒绝，且理由要指向「身份不一致」（不是含糊的失败）
        other = {"symbols": ["BTCUSDT"],
                 "window_start": "2024-01-01", "window_end": "2024-06-30",
                 "experiment": {"mode": "formal", "experiment_id": "exp_pin"}}
        with mock.patch.object(A, "_run_in_thread", lambda rid: None):
            resp2, code2 = A.create_run(other)
        self.assertEqual(code2, 409, resp2)
        self.assertIn("与登记身份不一致", "；".join(resp2.get("errors") or []))

    # ── execute_run 侧 ──────────────────────────────────
    def _make_run(self, experiment_block=None) -> str:
        import backtest_store as S
        rid = "bt_20260101T000000Z_%08x" % (abs(hash(str(experiment_block))) % (16 ** 8))
        spec = {"strategy_id": "WX_EVENT_TREND_V0_1", "symbols": ["BTCUSDT"],
                "window_start": "2024-01-01", "window_end": "2024-06-30",
                "params": {}, "initial_equity": 2000.0}
        if experiment_block is not None:
            spec["experiment"] = experiment_block
        S.create_run(rid, {"run_id": rid, "spec": spec, "status": S.STATUS_QUEUED,
                           "strategy_id": "WX_EVENT_TREND_V0_1"})
        return rid

    def test_execute_run_without_experiment_blocked(self):
        RUN = self._runner()
        rid = self._make_run()
        doc = RUN.execute_run(rid)
        self.assertEqual(doc["status"], "failed")
        self.assertIn("实验闸门拒绝", doc.get("failure_reason") or "")
        # 未产出任何结果文件
        d = self.runs_root / rid
        self.assertFalse((d / "trades.jsonl").exists())
        self.assertFalse((d / "summary.json").exists())

    def test_execute_run_unregistered_experiment_blocked(self):
        RUN = self._runner()
        rid = self._make_run({"experiment_id": "exp_ghost"})
        doc = RUN.execute_run(rid)
        self.assertEqual(doc["status"], "failed")
        self.assertIn("未登记", doc.get("failure_reason") or "")

    def test_execute_run_unfrozen_experiment_blocked(self):
        RUN = self._runner()
        self.register("exp_nf")           # 只登记，不冻结
        rid = self._make_run({"experiment_id": "exp_nf"})
        doc = RUN.execute_run(rid)
        self.assertEqual(doc["status"], "failed")
        self.assertIn("未冻结", doc.get("failure_reason") or "")

    def test_execute_run_abandoned_experiment_blocked(self):
        RUN = self._runner()
        self.register("exp_ab")
        self.reg.freeze("exp_ab")
        self.reg.abandon("exp_ab", reason="方向错了")
        rid = self._make_run({"experiment_id": "exp_ab"})
        doc = RUN.execute_run(rid)
        self.assertEqual(doc["status"], "failed")
        self.assertIn("已放弃", doc.get("failure_reason") or "")

    def test_guard_execute_passes_when_identity_matches(self):
        """闸门不是「一律拒绝」：身份一致时返回 None（不真跑回测）。"""
        scope = {"symbols": ["BTCUSDT"], "window_start": "2024-01-01",
                 "window_end": "2024-06-30"}
        parts = E.current_design_parts(sample_scope=scope,
                                       split_policy=self.policy(),
                                       metrics_spec=E.DEFAULT_METRICS_SPEC)
        self.reg.register(design=parts, sample_scope=scope,
                          split_policy=self.policy(),
                          metrics_spec=E.DEFAULT_METRICS_SPEC,
                          experiment_id="exp_ok_1", mode=E.MODE_FORMAL)
        self.reg.freeze("exp_ok_1")
        doc = {"spec": {"strategy_id": "WX_EVENT_TREND_V0_1",
                        "symbols": ["BTCUSDT"],
                        "window_start": "2024-01-01", "window_end": "2024-06-30",
                        "experiment": {"experiment_id": "exp_ok_1"}}}
        self.assertIsNone(E.guard_execute("bt_dummy", doc, registry=self.reg))

    def test_guard_execute_identity_drift_writes_invalidate(self):
        scope = {"symbols": ["BTCUSDT"], "window_start": "2024-01-01",
                 "window_end": "2024-06-30"}
        parts = E.current_design_parts(sample_scope=scope,
                                       split_policy=self.policy(),
                                       metrics_spec=E.DEFAULT_METRICS_SPEC)
        parts["config_sha256"] = "0" * 64
        self.reg.register(design=parts, sample_scope=scope,
                          split_policy=self.policy(),
                          metrics_spec=E.DEFAULT_METRICS_SPEC,
                          experiment_id="exp_drift", mode=E.MODE_FORMAL)
        self.reg.freeze("exp_drift")
        doc = {"spec": {"symbols": ["BTCUSDT"],
                        "window_start": "2024-01-01", "window_end": "2024-06-30",
                        "experiment": {"experiment_id": "exp_drift"}}}
        err = E.guard_execute("bt_dummy", doc, registry=self.reg)
        self.assertIsNotNone(err)
        self.assertIn("设计身份漂移", err)
        exp = self.reg.state()["experiments"]["exp_drift"]
        self.assertIsNone(exp["frozen_at"])
        self.assertEqual(len(exp["invalidations"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

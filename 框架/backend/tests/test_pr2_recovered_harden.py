#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR2-RECOVERED-HARDEN-01｜recovered 时效复核 + 同 UID 冲突闸 · 行为测试

跑法（必须在隔离沙盒内，不碰生产库）：

    cd 框架/backend
    /usr/bin/python3 -m unittest tests.test_pr2_recovered_harden -v

沙盒：每个用例 `tempfile.mkdtemp()` 一个临时目录，`LIVE_DB` / `MARKET_DB` /
`PAPER_LEDGER_DIR` / `PAPER_LOCK_DIR` / `PAPER_GATE_FILE` 全部指向它，
`tearDown` 恢复原环境并删除。**不读写任何生产文件。**

为什么是行为测试：每条断言看的都是**落库后的数据**（`executable_state` /
`payload.blocked_reasons` / `payload.recovered_check` / 时间字段原值）与
**路由结论**（`registered` / `paper_pending` 行数），不是源码字符串。

覆盖工单要求：
  工单 A：时效边界前 / 等于 / 后；预热有效但落库过期；replay 对象复用；
          过期、来源不可信、时间证据不足的明确阻断与原因码；不得靠更新当前时间续期。
  工单 B：historical/recovered/live/blocked 四种既有行的冲突矩阵；
          不同来源不互相覆盖；关键事实不一致不覆盖；同来源同事实幂等仍有效；
          冲突必须留可审计原因。
"""

import json
import os
import sqlite3
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
PROJECT_ROOT = BACKEND.parents[1].resolve() if len(BACKEND.parents) >= 2 else BACKEND.parent

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import live_store as L
import paper_engine as PE
import unified_runtime as UR
from 策略.WyckoffMaster.event_trend_v0_1.contracts import CandidateSnapshot

# 复用 WO-02 的沙盒与夹具（只复用**夹具**，不复用它的用例：该基类没有任何 test_ 方法）
from tests.test_pr2_wo_02_fail_closed import (  # noqa: E402
    ARM, H1, M15, T0, FailClosedBase, make_candidate)

LAG = int(PE.MAX_FILL_LAG_MS)          # 3600_000
# 处理时刻用一个**已过去**的绝对锚点，避免「用未来时刻当处理时刻」这种自欺场景
ANCHOR = T0 - 3 * H1                   # 3 小时前
SNAP_OLD = ANCHOR                      # 旧候选的确认时刻
EXEC_OLD = SNAP_OLD + M15              # 旧候选的最早执行点（= 确认 + 15m）
DEADLINE_OLD = EXEC_OLD + LAG          # 既有时效窗口右端


def provenance(explicit_replay=False, **over):
    """重启恢复对象的来源凭据（与 `bootstrap._take()` 写入的形状一致）。"""
    p = {"produced_by": "bootstrap_recovery", "replay_upto_ms": SNAP_OLD,
         "data_source": "hot_only", "explicit_replay": bool(explicit_replay),
         "kept_by": "execution_earliest_ms+MAX_FILL_LAG_MS>=upto_ms"}
    p.update(over)
    return p


class HardenBase(FailClosedBase):
    """在 WO-02 沙盒基础上补几个本单专用夹具。"""

    # ── 夹具 ────────────────────────────────────────────────────────────
    def old_candidate(self, uid="cand_rec_old", **over):
        """一条**旧**候选：确认于 3 小时前，执行点 = 确认 + 15m。"""
        base = dict(candidate_id=uid, snapshot_time_ms=SNAP_OLD, available_at_ms=SNAP_OLD,
                    execution_earliest_ms=EXEC_OLD)
        base.update(over)
        return make_candidate(**base)

    def fresh_candidate(self, uid="cand_rec_fresh", **over):
        """一条**当前**候选：确认于现在，执行点 = 现在 + 15m。"""
        base = dict(candidate_id=uid, snapshot_time_ms=T0, available_at_ms=T0,
                    execution_earliest_ms=T0 + M15)
        base.update(over)
        return make_candidate(**base)

    def persist_recovered(self, cand, *, now_ms, prov=None, provenance_map=None):
        if provenance_map is None:
            prov = provenance() if prov is None else prov
            provenance_map = {cand.candidate_id: prov} if prov is not None else {}
        return self.router.persist_candidates([cand], origin="recovered", now_ms=now_ms,
                                              provenance=provenance_map)

    def insert_row_from_candidate(self, cand, *, origin="live", exec_state="enterable",
                                 fact_over=None, payload_over=None):
        """按**与候选完全一致的事实**写一行 live_signal（精确构造同 UID 冲突）。"""
        facts = {
            "symbol": cand.symbol, "timeframe": UR.TIMEFRAME, "direction": "long",
            "event_time": cand.snapshot_time_ms, "confirmed_at": cand.snapshot_time_ms,
            "available_at": cand.available_at_ms,
            "strategy_version": cand.strategy_version,
        }
        if fact_over:
            facts.update(fact_over)
        payload = cand.to_dict()
        payload.update({"role": UR.ARM_ENTRY_ROLE, "arm": ARM})
        if payload_over:
            payload.update(payload_over)
        con = sqlite3.connect(str(self.iso_live_db))
        try:
            con.execute(
                "INSERT OR REPLACE INTO live_signal("
                "signal_uid, source, origin, exchange, market, symbol, timeframe, "
                "strategy_key, strategy_version, structural_kind, direction, event_time, "
                "confirmed_at, available_at, bar_open_time, bar_close_time, detection_low, "
                "detection_atr, display_state, executable_state, payload_json, "
                "first_seen_at, updated_at) "
                "VALUES(?, 'wyckoff_v0_1', ?, 'binance', 'um', ?, ?, 'wyckoff_v0_1', "
                "?, 'breakout', ?, ?, ?, ?, ?, ?, 50000.0, 500.0, 'displayable', ?, ?, ?, ?)",
                (cand.candidate_id, origin, facts["symbol"], facts["timeframe"],
                 facts["strategy_version"], facts["direction"], facts["event_time"],
                 facts["confirmed_at"], facts["available_at"],
                 facts["event_time"] - H1, facts["event_time"],
                 exec_state, json.dumps(payload, ensure_ascii=False), facts["event_time"],
                 facts["event_time"]))
            con.commit()
        finally:
            con.close()

    def reasons_of(self, uid):
        p = self.payload_of(uid)
        return list(p.get("blocked_reasons") or [])

    def recovered_check_of(self, uid):
        return self.payload_of(uid).get("recovered_check") or {}


# ══════════════════════════════════════════════════════════════════════════
# A-1. 时效边界：前 / 等于 / 后
# ══════════════════════════════════════════════════════════════════════════
class TestRecoveredWindowBoundary(HardenBase):
    """既有时效窗口 = `execution_earliest_ms + MAX_FILL_LAG_MS`。

    边界口径与 `bootstrap._take()` **同一常量、同一方向**：`processed <= deadline`
    算窗内。所以「等于」必须放行、「后 1ms」必须阻断。改这个方向就是放宽既有窗口。
    """

    def test_01_before_deadline_passes(self):
        """边界前 1ms ⇒ 放行（enterable），且带出复核证据。"""
        cand = self.old_candidate("cand_rec_before")
        st = self.persist_recovered(cand, now_ms=DEADLINE_OLD - 1)
        self.assertEqual(st["new"], 1)
        row = self.live_row("cand_rec_before")
        self.assertEqual(row["executable_state"], "enterable",
                         "窗内候选不得被时效复核误伤")
        self.assertEqual(self.reasons_of("cand_rec_before"), [],
                         f"窗内不应有阻断原因，实际 {self.reasons_of('cand_rec_before')}")
        chk = self.recovered_check_of("cand_rec_before")
        self.assertEqual(chk["window_deadline_ms"], DEADLINE_OLD,
                         "复核证据必须写出窗口右端（可核对，不是口头声明）")
        self.assertEqual(chk["lag_ms"], DEADLINE_OLD - 1 - EXEC_OLD)
        self.assertEqual(chk["max_fill_lag_ms"], LAG)
        self.assertFalse(chk["provenance"]["explicit_replay"])

    def test_02_exactly_at_deadline_passes(self):
        """恰好等于窗口右端 ⇒ 放行（与 bootstrap 的 `>=` 同一方向）。"""
        cand = self.old_candidate("cand_rec_equal")
        self.persist_recovered(cand, now_ms=DEADLINE_OLD)
        row = self.live_row("cand_rec_equal")
        self.assertEqual(row["executable_state"], "enterable",
                         "等于边界必须与 bootstrap 的判定一致（放行），不得单方面收紧")
        self.assertNotIn(UR.RC_WINDOW_EXPIRED, self.reasons_of("cand_rec_equal"))
        self.assertEqual(self.recovered_check_of("cand_rec_equal")["lag_ms"], LAG)

    def test_03_after_deadline_blocks(self):
        """边界后 1ms ⇒ 阻断 `recovered_window_expired`，且绝不生成挂单。"""
        cand = self.old_candidate("cand_rec_after")
        st = self.persist_recovered(cand, now_ms=DEADLINE_OLD + 1)
        self.assertEqual(st["new"], 1, "阻断也要落库（否则事后查不到这条为什么被挡）")
        self.assertEqual(st["blocked"], 1)
        row = self.live_row("cand_rec_after")
        self.assertEqual(row["executable_state"], "blocked")
        self.assertIn(UR.RC_WINDOW_EXPIRED, self.reasons_of("cand_rec_after"))
        self.assertIs(self.payload_of("cand_rec_after").get("approved"), False)
        res = self.router.route_pending_orders(now_ms=DEADLINE_OLD + 60_000, origin="recovered")
        self.assertEqual(res["registered"], 0, "过期候选不得登记挂单")
        self.assertEqual(self.pending_count("cand_rec_after"), 0)


# ══════════════════════════════════════════════════════════════════════════
# A-2. 预热有效但落库过期（本单核心缺陷）
# ══════════════════════════════════════════════════════════════════════════
class TestPrewarmValidButExpiredAtPersist(HardenBase):
    """`bootstrap()` 用预热截止时刻判窗，落库用**实际处理时刻**再判。

    缺陷原样：预热时还在窗内 ⇒ 进 `recovered_candidates`；等全池预热跑完再落库时
    早已过期，但旧实现不复核 ⇒ 过期信号被写成 `enterable` 交给路由。
    """

    def test_04_prewarm_valid_but_persist_expired(self):
        cand = self.old_candidate("cand_rec_prewarm")
        upto_ms = DEADLINE_OLD - 10_000        # 预热截止时刻（窗内）
        # ① 前提事实：bootstrap 的判据（同一常量、同一方向）确实会**保留**它
        self.assertTrue(int(cand.execution_earliest_ms) + LAG >= upto_ms,
                        "前提不成立：这条候选在预热时本就不该被保留")
        # ② 实际落库时刻比窗口晚 2 小时（全池预热耗时）
        persist_at = DEADLINE_OLD + 2 * H1
        st = self.persist_recovered(cand, now_ms=persist_at)
        self.assertEqual(st["blocked"], 1, "预热有效但落库过期，必须被阻断")
        self.assertIn(UR.RC_WINDOW_EXPIRED, self.reasons_of("cand_rec_prewarm"))
        self.assertEqual(self.live_row("cand_rec_prewarm")["executable_state"], "blocked")
        chk = self.recovered_check_of("cand_rec_prewarm")
        self.assertEqual(chk["processed_at_ms"], persist_at,
                         "复核必须按**实际处理时刻**，不是预热截止时刻")
        self.assertGreater(chk["lag_ms"], LAG)
        res = self.router.route_pending_orders(now_ms=persist_at + 1000, origin="recovered")
        self.assertEqual(res["registered"], 0)
        self.assertEqual(self.pending_count("cand_rec_prewarm"), 0)


# ══════════════════════════════════════════════════════════════════════════
# A-3. 不得靠「更新当前时间」续期
# ══════════════════════════════════════════════════════════════════════════
class TestNoValidityRenewal(HardenBase):

    def test_05_late_persist_does_not_renew_validity(self):
        """4 天前的候选晚处理：阻断，且库里的时间事实**仍是原值**（没被写成现在）。"""
        snap = T0 - 4 * 24 * H1
        cand = make_candidate(candidate_id="cand_rec_4d", snapshot_time_ms=snap,
                              available_at_ms=snap, execution_earliest_ms=snap + M15)
        now = T0
        self.persist_recovered(cand, now_ms=now)
        row = self.live_row("cand_rec_4d")
        self.assertEqual(row["executable_state"], "blocked")
        self.assertIn(UR.RC_WINDOW_EXPIRED, self.reasons_of("cand_rec_4d"))
        # 关键断言：有效期三字段一个都没被「现在」覆盖
        self.assertEqual(row["event_time"], snap)
        self.assertEqual(row["confirmed_at"], snap)
        self.assertEqual(row["available_at"], snap)
        # 记账时间可以变（那是「什么时候写进来的」，不是「信号什么时候有效」）
        self.assertEqual(row["updated_at"], now)
        self.assertEqual(row["first_seen_at"], now)
        self.assertGreater(row["updated_at"] - row["available_at"], LAG,
                           "落库时刻与信号可用时刻的差距必须如实留着（不许抹平）")

    def test_06_execution_point_tampered_blocks(self):
        """把执行点挪到更晚（续期手法）⇒ `recovered_execution_point_tampered`。"""
        snap = T0 - 4 * 24 * H1
        cand = make_candidate(candidate_id="cand_rec_tamper", snapshot_time_ms=snap,
                              available_at_ms=snap,
                              execution_earliest_ms=T0 + M15)   # 被改到「现在 + 15m」
        self.persist_recovered(cand, now_ms=T0 + 60_000)
        self.assertIn(UR.RC_EXEC_POINT_TAMPERED, self.reasons_of("cand_rec_tamper"))
        self.assertEqual(self.live_row("cand_rec_tamper")["executable_state"], "blocked")
        # 反向对照：同一条候选，执行点按合约等于 snapshot+15m ⇒ 该原因码不出现
        ok = make_candidate(candidate_id="cand_rec_untampered", snapshot_time_ms=snap,
                            available_at_ms=snap, execution_earliest_ms=snap + M15)
        self.persist_recovered(ok, now_ms=T0 + 60_000)
        self.assertNotIn(UR.RC_EXEC_POINT_TAMPERED, self.reasons_of("cand_rec_untampered"))


# ══════════════════════════════════════════════════════════════════════════
# A-4. 来源不可信 / 时间证据不足
# ══════════════════════════════════════════════════════════════════════════
class TestSourceAndEvidence(HardenBase):

    def test_07_provenance_missing_blocks(self):
        """没有来源凭据 ⇒ 阻断（分不清重启恢复还是回放时不许放行）。"""
        cand = self.fresh_candidate("cand_rec_noprov")
        st = self.router.persist_candidates([cand], origin="recovered", now_ms=T0 + 1000,
                                            provenance=None)
        self.assertEqual(st["blocked"], 1)
        self.assertIn(UR.RC_PROVENANCE_MISSING, self.reasons_of("cand_rec_noprov"))
        # 反向对照：同一候选给了凭据就放行
        cand2 = self.fresh_candidate("cand_rec_withprov")
        self.persist_recovered(cand2, now_ms=T0 + 1000)
        self.assertNotIn(UR.RC_PROVENANCE_MISSING, self.reasons_of("cand_rec_withprov"))
        self.assertEqual(self.live_row("cand_rec_withprov")["executable_state"], "enterable")

    def test_08_replay_object_rejected(self):
        """回放对象即使落在成交时效内也不得进入 recovered 可执行写入路径。"""
        cand = self.fresh_candidate("cand_rec_replay")
        st = self.persist_recovered(cand, now_ms=T0 + 1000,
                                    prov=provenance(explicit_replay=True))
        self.assertEqual(st["blocked"], 1)
        self.assertIn(UR.RC_REPLAY_OBJECT, self.reasons_of("cand_rec_replay"))
        self.assertEqual(self.live_row("cand_rec_replay")["executable_state"], "blocked")
        self.assertTrue(self.recovered_check_of("cand_rec_replay")["provenance"]["explicit_replay"],
                        "凭据必须原样留在 payload 里（可审计，不靠推断）")
        res = self.router.route_pending_orders(now_ms=T0 + 60_000, origin="recovered")
        self.assertEqual(res["registered"], 0)
        self.assertEqual(self.pending_count("cand_rec_replay"), 0)

    def test_09_replay_origin_write_is_not_actionable(self):
        """以 `origin='replay'` 写入 ⇒ 落库时就不是可执行状态（写入侧兜底）。"""
        cand = self.fresh_candidate("cand_rec_replay_write")
        self.router.persist_candidates([cand], origin="replay", now_ms=T0 + 1000)
        row = self.live_row("cand_rec_replay_write")
        self.assertEqual(row["origin"], "replay")
        self.assertEqual(row["executable_state"], "blocked",
                         "回放来源不得落成 enterable（读的时候不认，写的时候就不该是）")
        self.assertIn(f"{UR.RC_ORIGIN_UNTRUSTED}:replay", self.reasons_of("cand_rec_replay_write"))

    def test_10_untrusted_origin_in_freshness_check(self):
        """时效复核函数自身对来源白名单外的 origin 也给原因码（防御性契约）。"""
        cand = self.fresh_candidate("cand_rec_untrusted")
        for bad in ("test", "manual", "", None):
            reasons = UR.validate_recovered_freshness(
                cand, origin=bad, processed_at_ms=T0 + 1000, provenance=provenance())
            self.assertTrue(any(r.startswith(UR.RC_ORIGIN_UNTRUSTED) for r in reasons),
                            f"来源 {bad!r} 必须给出 {UR.RC_ORIGIN_UNTRUSTED}，实际 {reasons}")
        # 合法来源不报这条
        self.assertFalse(any(r.startswith(UR.RC_ORIGIN_UNTRUSTED) for r in
                             UR.validate_recovered_freshness(
                                 cand, origin="recovered", processed_at_ms=T0 + 1000,
                                 provenance=provenance())))

    def test_11_time_evidence_missing_or_future(self):
        """时间证据缺失 / available_at 在未来 ⇒ 明确阻断并写清缺哪个字段。"""
        cand = make_candidate(candidate_id="cand_rec_noavail", snapshot_time_ms=T0,
                              available_at_ms=None, execution_earliest_ms=T0 + M15)
        self.persist_recovered(cand, now_ms=T0 + 1000)
        self.assertTrue(any(r.startswith(UR.RC_TIME_EVIDENCE_MISSING)
                            for r in self.reasons_of("cand_rec_noavail")))
        self.assertIn("available_at_ms", " ".join(self.reasons_of("cand_rec_noavail")))

        fut = make_candidate(candidate_id="cand_rec_future", snapshot_time_ms=T0,
                             available_at_ms=T0 + 2 * H1,
                             execution_earliest_ms=T0 + 2 * H1 + M15)
        self.persist_recovered(fut, now_ms=T0 + 1000)
        self.assertIn(UR.RC_AVAILABLE_IN_FUTURE, self.reasons_of("cand_rec_future"))
        self.assertEqual(self.live_row("cand_rec_future")["executable_state"], "blocked")


# ══════════════════════════════════════════════════════════════════════════
# B. 同 UID 冲突矩阵
# ══════════════════════════════════════════════════════════════════════════
class TestUidConflictMatrix(HardenBase):
    """四种既有行 × 多种写入来源，逐格断言「拒绝 / 放行 + 原因码」。

    核心不变量：**同 UID 的行，来源与可执行状态不允许互相矛盾**。
    旧实现的 `ON CONFLICT DO UPDATE` 只改 `executable_state` 不改 `origin`，
    于是 `historical_research` 行可以变成 `executable_state='enterable'` —— 自相矛盾。
    """

    def _assert_refused(self, uid, code_prefix, *, origin_before, state_before):
        row = self.live_row(uid)
        self.assertEqual(row["origin"], origin_before, "被拒绝的冲突不得改动既有行来源")
        self.assertEqual(row["executable_state"], state_before,
                         "被拒绝的冲突不得改动既有行可执行状态")
        return row

    def test_12_historical_row_cannot_be_upgraded(self):
        """既有 `historical_research` 行 + 同 UID 的 recovered 写入 ⇒ 拒绝。"""
        cand = self.fresh_candidate("cand_cf_hist")
        self.insert_row_from_candidate(cand, origin="historical_research",
                                       exec_state="historical_not_executable")
        st = self.persist_recovered(cand, now_ms=T0 + 1000)
        self.assertEqual(st["new"] + st["updated"], 0, "冲突不得产生任何写入")
        self.assertEqual(st["conflict_rejected_n"], 1)
        reasons = st["conflict_rejected"]["cand_cf_hist"]
        self.assertTrue(any(r.startswith(UR.CF_ORIGIN_MISMATCH) for r in reasons), reasons)
        self.assertIn("historical_research", reasons[0])
        self._assert_refused("cand_cf_hist", UR.CF_ORIGIN_MISMATCH,
                             origin_before="historical_research",
                             state_before="historical_not_executable")
        # 而且它仍然不可执行（页面/路由两侧口径都没被污染）
        reasons_after = UR.signal_admission_reasons(
            self.live_row("cand_cf_hist"), origin="historical_research")
        self.assertIn("origin_not_actionable:historical_research", reasons_after)
        self.assertIn("executable_state_not_enterable:historical_not_executable",
                      reasons_after,
                      "被拒绝的冲突不得让历史研究行变得可执行")

    def test_13_replay_row_cannot_be_upgraded(self):
        """既有 `replay` 行 + 同 UID 的 recovered 写入 ⇒ 拒绝。"""
        cand = self.fresh_candidate("cand_cf_replay")
        self.insert_row_from_candidate(cand, origin="replay", exec_state="blocked")
        st = self.persist_recovered(cand, now_ms=T0 + 1000)
        self.assertEqual(st["conflict_rejected_n"], 1)
        self.assertTrue(any(r.startswith(UR.CF_ORIGIN_MISMATCH)
                            for r in st["conflict_rejected"]["cand_cf_replay"]))
        self._assert_refused("cand_cf_replay", UR.CF_ORIGIN_MISMATCH,
                             origin_before="replay", state_before="blocked")

    def test_14_live_row_not_overwritten_by_recovered(self):
        """既有 `live` 行 + 同 UID 的 recovered 写入 ⇒ 拒绝（不同来源不互相覆盖）。"""
        cand = self.fresh_candidate("cand_cf_live")
        self.insert_row_from_candidate(cand, origin="live", exec_state="enterable")
        before = self.live_row("cand_cf_live")
        st = self.persist_recovered(cand, now_ms=T0 + 1000)
        self.assertEqual(st["conflict_rejected_n"], 1)
        self.assertTrue(any(r.startswith(UR.CF_ORIGIN_MISMATCH)
                            for r in st["conflict_rejected"]["cand_cf_live"]))
        after = self._assert_refused("cand_cf_live", UR.CF_ORIGIN_MISMATCH,
                                     origin_before="live", state_before="enterable")
        self.assertEqual(after["payload_json"], before["payload_json"],
                         "被拒绝时既有行一个字节都不许改")

    def test_15_blocked_row_not_silently_upgraded(self):
        """既有 `blocked` 行（同来源同事实）+ 同 UID 的 enterable 写入 ⇒ 拒绝升级。"""
        cand = self.fresh_candidate("cand_cf_blocked")
        self.insert_row_from_candidate(cand, origin="recovered", exec_state="blocked",
                                       payload_over={"approved": False,
                                                     "blocked_reasons": ["manual_hold"]})
        st = self.persist_recovered(cand, now_ms=T0 + 1000)
        self.assertEqual(st["conflict_rejected_n"], 1)
        reasons = st["conflict_rejected"]["cand_cf_blocked"]
        self.assertIn(f"{UR.CF_UPGRADE_REFUSED}:blocked->enterable", reasons)
        self._assert_refused("cand_cf_blocked", UR.CF_UPGRADE_REFUSED,
                             origin_before="recovered", state_before="blocked")

    def test_16_fact_mismatch_refused(self):
        """同来源但关键事实不一致（事件时点/价格）⇒ 拒绝覆盖，并指出是哪个字段。"""
        cand = self.fresh_candidate("cand_cf_fact")
        self.insert_row_from_candidate(cand, origin="live", exec_state="enterable",
                                       fact_over={"event_time": T0 - H1,
                                                  "confirmed_at": T0 - H1,
                                                  "available_at": T0 - H1})
        st = self.router.persist_candidates([cand], origin="live", now_ms=T0 + 1000)
        self.assertEqual(st["conflict_rejected_n"], 1)
        reasons = st["conflict_rejected"]["cand_cf_fact"]
        self.assertIn(f"{UR.CF_FACT_MISMATCH}:event_time", reasons)
        self.assertEqual(self.live_row("cand_cf_fact")["event_time"], T0 - H1,
                         "被拒绝后既有行的时间事实必须原样保留")

        # payload 级事实（价格）不一致同样要挡
        cand2 = self.fresh_candidate("cand_cf_fact_price")
        self.insert_row_from_candidate(cand2, origin="live", exec_state="enterable",
                                       payload_over={"initial_stop_price": 12345.0})
        st2 = self.router.persist_candidates([cand2], origin="live", now_ms=T0 + 1000)
        self.assertIn(f"{UR.CF_FACT_MISMATCH}:initial_stop_price",
                      st2["conflict_rejected"]["cand_cf_fact_price"])

    def test_17_idempotent_write_still_works(self):
        """同来源、同事实的正常幂等写入必须继续有效（不许把正常路径也堵死）。"""
        cand = self.fresh_candidate("cand_cf_idem")
        st1 = self.router.persist_candidates([cand], origin="live", now_ms=T0 + 1000)
        self.assertEqual((st1["new"], st1["updated"]), (1, 0))
        first = self.live_row("cand_cf_idem")
        st2 = self.router.persist_candidates([cand], origin="live", now_ms=T0 + 2000)
        self.assertEqual((st2["new"], st2["updated"]), (0, 1), "第二次必须是幂等更新")
        self.assertEqual(st2["conflict_rejected_n"], 0, "正常幂等不得被判成冲突")
        second = self.live_row("cand_cf_idem")
        self.assertEqual(second["first_seen_at"], first["first_seen_at"],
                         "幂等更新不改首次出现时刻")
        self.assertEqual(second["executable_state"], first["executable_state"])
        self.assertEqual(second["origin"], first["origin"])
        self.assertEqual(second["updated_at"], T0 + 2000)

        # recovered 来源的幂等同样有效（凭据齐、窗内、同事实）
        rcand = self.fresh_candidate("cand_cf_idem_rec")
        st3 = self.persist_recovered(rcand, now_ms=T0 + 1000)
        st4 = self.persist_recovered(rcand, now_ms=T0 + 2000)
        self.assertEqual((st3["new"], st3["updated"]), (1, 0))
        self.assertEqual((st4["new"], st4["updated"]), (0, 1))
        self.assertEqual(st4["conflict_rejected_n"], 0)

    def test_18_matrix_four_existing_kinds(self):
        """矩阵：historical / replay / blocked / live 四种既有行 × 两种写入来源。"""
        cases = [
            # (既有来源, 既有状态, 写入来源, 期望结果, 期望原因码前缀)
            ("historical_research", "historical_not_executable", "recovered", "refused",
             UR.CF_ORIGIN_MISMATCH),
            ("historical_research", "historical_not_executable", "live", "refused",
             UR.CF_ORIGIN_MISMATCH),
            ("replay", "blocked", "recovered", "refused", UR.CF_ORIGIN_MISMATCH),
            ("replay", "blocked", "live", "refused", UR.CF_ORIGIN_MISMATCH),
            ("live", "blocked", "recovered", "refused", UR.CF_ORIGIN_MISMATCH),
            ("live", "blocked", "live", "refused", UR.CF_UPGRADE_REFUSED),
            ("live", "enterable", "recovered", "refused", UR.CF_ORIGIN_MISMATCH),
            ("live", "enterable", "live", "allowed", None),
            ("recovered", "enterable", "recovered", "allowed", None),
            ("recovered", "blocked", "recovered", "refused", UR.CF_UPGRADE_REFUSED),
        ]
        for i, (o0, s0, o1, want, code) in enumerate(cases):
            with self.subTest(i=i, existing=(o0, s0), incoming=o1):
                uid = f"cand_mx_{i}"
                cand = self.fresh_candidate(uid)
                self.insert_row_from_candidate(cand, origin=o0, exec_state=s0)
                if o1 == "recovered":
                    st = self.persist_recovered(cand, now_ms=T0 + 1000)
                else:
                    st = self.router.persist_candidates([cand], origin=o1, now_ms=T0 + 1000)
                if want == "refused":
                    self.assertEqual(st["conflict_rejected_n"], 1,
                                     f"({o0}/{s0}) ← {o1} 必须拒绝，实际 {st}")
                    reasons = st["conflict_rejected"][uid]
                    self.assertTrue(any(r.startswith(code) for r in reasons),
                                    f"原因码必须是 {code}，实际 {reasons}")
                    row = self.live_row(uid)
                    self.assertEqual(row["origin"], o0)
                    self.assertEqual(row["executable_state"], s0)
                else:
                    self.assertEqual(st["conflict_rejected_n"], 0, f"({o0}/{s0}) ← {o1} 应放行")
                    self.assertEqual(st["updated"], 1)
                    row = self.live_row(uid)
                    self.assertEqual(row["origin"], o0)
                    # 允许放行的格子里，行内「来源↔可执行状态」必须仍然自洽
                    if row["executable_state"] == "enterable":
                        self.assertIn(row["origin"], ("live", "recovered"),
                                      "enterable 只能出现在可执行来源上（这正是旧实现破坏的不变量）")


# ══════════════════════════════════════════════════════════════════════════
# C. 端到端：bootstrap 恢复路径 → 落库 → 路由 → 挂单
# ══════════════════════════════════════════════════════════════════════════
class TestRecoveredRouteEndToEnd(HardenBase):
    """用**真实的** `UnifiedRuntime._route_recovered_candidates` 跑恢复路径。

    为什么不用完整 `UnifiedRuntime`：那要拉 526 标的预热与真实行情库，属另一层；
    这里把「引擎侧产物 + 凭据」直接喂给真实的落库+路由方法，验证的是**接线**本身。
    """

    def _stub_runtime(self, candidates, provenance_map):
        rt = types.SimpleNamespace()
        rt.router = self.router
        rt.arm = ARM
        rt.signal_engine = types.SimpleNamespace(
            recovered_candidates=list(candidates),
            recovered_provenance=dict(provenance_map))
        rt.stats = {"orders_routed_total": 0, "orders_rejected_total": 0,
                    "last_error": None, "signal_conflicts": []}
        rt.recovered_route_stats = {}
        rt._SIGNAL_CONFLICT_KEEP = UR.UnifiedRuntime._SIGNAL_CONFLICT_KEEP
        rt._record_signal_conflicts = types.MethodType(
            UR.UnifiedRuntime._record_signal_conflicts, rt)
        rt._route_recovered_candidates = types.MethodType(
            UR.UnifiedRuntime._route_recovered_candidates, rt)
        return rt

    def test_19_fresh_recovered_goes_through_to_pending(self):
        """窗内 + 凭据齐 ⇒ 落库 enterable → 路由 processed=1（有契约则登记挂单）。"""
        cand = self.fresh_candidate("cand_e2e_fresh")
        rt = self._stub_runtime([cand], {cand.candidate_id: provenance()})
        out = rt._route_recovered_candidates(now_ms=T0 + 1000)
        self.assertEqual(out["recovered"], 1)
        self.assertEqual(out["persisted"], 1)
        self.assertEqual(out["blocked"], 0, f"窗内候选不该被阻断：{out}")
        self.assertEqual(self.live_row("cand_e2e_fresh")["executable_state"], "enterable")
        self.assertEqual(out["registered"], 1, f"必须进入撮合登记，实际 {out}")
        self.assertEqual(self.pending_count("cand_e2e_fresh"), 1)

    def test_20_expired_recovered_never_reaches_pending(self):
        """预热有效但落库过期 ⇒ 阻断、路由 0 条、无挂单，且原因可在状态里查到。"""
        cand = self.old_candidate("cand_e2e_expired")
        rt = self._stub_runtime([cand], {cand.candidate_id: provenance()})
        out = rt._route_recovered_candidates(now_ms=DEADLINE_OLD + 2 * H1)
        self.assertEqual(out["persisted"], 1, "阻断也落库，便于事后核对")
        self.assertEqual(out["blocked"], 1)
        self.assertIn(UR.RC_WINDOW_EXPIRED, out["blocked_reasons"]["cand_e2e_expired"])
        self.assertEqual(out["registered"], 0)
        self.assertEqual(self.pending_count("cand_e2e_expired"), 0)
        self.assertEqual(rt.recovered_route_stats["blocked"], 1,
                         "结果必须落在运行时状态上（可被接口读到，不是只在返回值里）")

    def test_21_conflict_rejection_is_auditable_in_runtime(self):
        """同 UID 冲突被拒绝时，运行时审计流水必须留下 uid + 原因码 + 时刻。"""
        cand = self.fresh_candidate("cand_e2e_conflict")
        self.insert_row_from_candidate(cand, origin="historical_research",
                                       exec_state="historical_not_executable")
        rt = self._stub_runtime([cand], {cand.candidate_id: provenance()})
        out = rt._route_recovered_candidates(now_ms=T0 + 1000)
        self.assertEqual(out["conflict_rejected_n"], 1)
        self.assertEqual(out["registered"], 0, "被冲突拒绝的行不得进入路由")
        buf = rt.stats["signal_conflicts"]
        self.assertEqual(len(buf), 1)
        self.assertEqual(buf[0]["signal_uid"], "cand_e2e_conflict")
        self.assertEqual(buf[0]["origin"], "recovered")
        self.assertTrue(any(r.startswith(UR.CF_ORIGIN_MISMATCH) for r in buf[0]["reasons"]))
        self.assertEqual(buf[0]["at_ms"], T0 + 1000)


# ══════════════════════════════════════════════════════════════════════════
# D. live 路径零回归（本单只加 recovered 侧判据）
# ══════════════════════════════════════════════════════════════════════════
class TestLivePathUnchanged(HardenBase):

    def test_22_live_needs_no_provenance_and_stays_enterable(self):
        """live 路径不要求凭据、不写 recovered 复核证据、仍是 enterable。"""
        cand = self.fresh_candidate("cand_live_plain")
        st = self.router.persist_candidates([cand], origin="live", now_ms=T0 + 1000)
        self.assertEqual(st["blocked"], 0)
        row = self.live_row("cand_live_plain")
        self.assertEqual(row["executable_state"], "enterable")
        p = self.payload_of("cand_live_plain")
        self.assertNotIn("recovered_check", p, "live 行不得被塞进 recovered 的复核证据")
        self.assertNotIn("approved", p)
        self.assertEqual(UR.signal_admission_reasons(
            row, origin="live", floor_ms=int(L.effective_floor_ms())), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

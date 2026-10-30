#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR2-WO-02｜信号可执行范围 Fail-Closed 加固 · 行为测试

跑法（必须在隔离沙盒内，不碰生产库）：

    cd 框架/backend
    /usr/bin/python3 -m unittest tests.test_pr2_wo_02_fail_closed -v

为什么这些是**行为测试**而不是源码字符串检查：
  每一条都真实调用生产代码（`persist_candidates` / `route_pending_orders` /
  `live_api.live_signals`），断言的是**落库后的数据**与**接口返回的结论**
  （`executable_state` / `approved` / `resonance_score` / `registered` /
  `blocked_reason` / `items`），而不是「源码里有没有某个词」。

覆盖工单要求的 10 类场景：
  1 共振函数抛异常        2 共振数据缺失        3 信号时间倒挂
  4 available_at 缺失     5 历史信号进入扫描     6 测试 UID 进入扫描
  7 水位不可用            8 正常实时信号保持原行为
  9 API 与路由准入一致    10 非法/缺失方向不生成订单
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
PROJECT_ROOT = BACKEND.parents[1].resolve() if len(BACKEND.parents) >= 2 else BACKEND.parent

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import live_api as API
import live_store as L
import paper_engine as PE
import wyckoff_factor_resonance as WFR
from 策略.WyckoffMaster.event_trend_v0_1.contracts import CandidateSnapshot
import unified_runtime as UR

M15 = 900_000
H1 = 3_600_000
ARM = "3a"

# 基准时刻 = **当前真实时钟对齐到整分钟**。
#
# 为什么不用固定日期：`paper_engine.open_position` 对 `origin='live'` 会用**真实挂钟**
# 作为「首次请求时刻」，若信号时刻远在过去，最早可成交点早已错过 ⇒ 会被
# `earliest_fill_before_first_request_clock` 拒单（那是既有成交时钟纪律，不是本工单的
# 准入判据）。要让「正常实时信号」真的走到登记挂单，信号必须是**刚刚发生**的。
T0 = (int(time.time() * 1000) // 60_000) * 60_000


def create_market_schema(db_path: Path):
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS candle_versions (
            exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
            volume REAL NOT NULL, quote_volume REAL NOT NULL, trade_count INTEGER DEFAULT 0,
            taker_buy_volume REAL DEFAULT 0, taker_buy_quote_volume REAL DEFAULT 0,
            source_id INTEGER NOT NULL, available_at INTEGER NOT NULL,
            is_closed INTEGER DEFAULT 1,
            PRIMARY KEY(exchange, market, symbol, timeframe, open_time, source_id)
        );
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS candle_heads (
            exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, source_id INTEGER NOT NULL,
            PRIMARY KEY(exchange, market, symbol, timeframe, open_time)
        );
        """)
        con.execute("""
        CREATE VIEW IF NOT EXISTS candles AS
        SELECT v.* FROM candle_versions v JOIN candle_heads h
        USING(exchange, market, symbol, timeframe, open_time, source_id);
        """)
        con.commit()
    finally:
        con.close()


def insert_bar(db_path: Path, symbol: str, timeframe: str, open_time: int, close_time: int,
               o: float, h: float, l: float, c: float, v: float = 1000.0):
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT OR REPLACE INTO candle_versions(exchange,market,symbol,timeframe,open_time,"
            "close_time,open,high,low,close,volume,quote_volume,trade_count,taker_buy_volume,"
            "taker_buy_quote_volume,source_id,available_at,is_closed) "
            "VALUES('binance','um',?,?,?,?,?,?,?,?,?,?,10,500,50000,1,?,1)",
            (symbol, timeframe, open_time, close_time, o, h, l, c, v, 100000.0, close_time))
        con.execute(
            "INSERT OR REPLACE INTO candle_heads(exchange,market,symbol,timeframe,open_time,source_id) "
            "VALUES('binance','um',?,?,?,1)", (symbol, timeframe, open_time))
        con.commit()
    finally:
        con.close()


def make_candidate(**over):
    """合法候选快照（可逐字段覆盖以构造缺陷）。"""
    base = dict(
        candidate_id="cand_pr2_normal",
        event_id="evt_pr2_normal",
        strategy_version="wyckoff_v0_1",
        symbol="PR2USDT",
        venue="binance",
        snapshot_time_ms=T0,
        available_at_ms=T0 + 1000,
        box_high_h=52000.0,
        box_low_l=50000.0,
        box_atr_a=500.0,
        box_er=0.5,
        sweep_low_s=49500.0,
        retest_low_lr=50800.0,
        breakout_close=53000.0,
        breakout_volume=2000.0,
        v20_median_volume=1000.0,
        volume_ratio=2.0,
        oi_breakout=100000.0,
        oi_6h_ago=95000.0,
        oi_growth_6h=0.0526,
        initial_stop_price=50750.0,
        initial_risk_r=2250.0,
        max_entry_price=53250.0,
        min_entry_price=52000.0,
        execution_earliest_ms=T0 + M15,
        pit_proven=True,
    )
    base.update(over)
    return CandidateSnapshot(**base)


class FailClosedBase(unittest.TestCase):
    """隔离沙盒：临时 LIVE_DB / 纸面账本 / 行情库 / 闸门文件。"""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="pr2_wo_02_"))
        self.iso_market_db = self.test_dir / "market_iso.sqlite3"
        self.iso_live_db = self.test_dir / "live_iso.sqlite3"
        self.iso_ledger_dir = self.test_dir / "paper"
        self.iso_lock_dir = self.test_dir / "locks"
        for d in (self.iso_ledger_dir, self.iso_lock_dir):
            d.mkdir(parents=True, exist_ok=True)
        create_market_schema(self.iso_market_db)

        self.orig_env = os.environ.copy()
        os.environ.update({
            "MARKET_DB": str(self.iso_market_db),
            "LIVE_DB": str(self.iso_live_db),
            "PAPER_LEDGER_DIR": str(self.iso_ledger_dir),
            "PAPER_LOCK_DIR": str(self.iso_lock_dir),
            "ISOLATED_MODE": "1",
            "PAPER_LEDGER_ALLOW_INIT": "1",
        })
        gate_file = self.test_dir / "gate.json"
        gate_file.write_text(json.dumps({
            "allow_new_entries": True, "reason": "pr2_wo_02_isolated_test",
            "set_by": "test", "since_ms": int(time.time() * 1000)}), encoding="utf-8")
        os.environ["PAPER_GATE_FILE"] = str(gate_file)

        L.ensure_live_schema()
        L.paper_conn(ARM, create=True, initial_equity=100000.0).close()
        self.router = UR.SignalPersistenceAndRouter(arm=ARM)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ── 工具 ────────────────────────────────────────────────────────────
    def live_row(self, uid):
        con = sqlite3.connect(str(self.iso_live_db))
        try:
            con.row_factory = sqlite3.Row
            r = con.execute("SELECT * FROM live_signal WHERE signal_uid=?", (uid,)).fetchone()
            return dict(r) if r else None
        finally:
            con.close()

    def pending_count(self, uid=None):
        con = sqlite3.connect(str(self.iso_ledger_dir / f"paper_{ARM}.sqlite3"))
        try:
            if uid:
                return con.execute("SELECT COUNT(*) FROM paper_pending WHERE signal_uid=?",
                                   (uid,)).fetchone()[0]
            return con.execute("SELECT COUNT(*) FROM paper_pending").fetchone()[0]
        finally:
            con.close()

    def payload_of(self, uid):
        row = self.live_row(uid)
        self.assertIsNotNone(row, f"信号 {uid} 必须已落库")
        return json.loads(row["payload_json"] or "{}")

    def insert_signal_row(self, uid, *, origin="live", exec_state="enterable",
                          direction="long", event_time=None, confirmed_at=None,
                          available_at=None, payload=None, arm=ARM, symbol="PR2USDT"):
        """直接写一行 live_signal（用于精确构造缺陷行，绕过 persist 的自动阻断）。"""
        ev = T0 if event_time is None else event_time
        cf = T0 if confirmed_at is None else confirmed_at
        av = (T0 + 1000) if available_at is None else available_at
        p = {"role": UR.ARM_ENTRY_ROLE, "arm": arm}
        if payload:
            p.update(payload)
        con = sqlite3.connect(str(self.iso_live_db))
        try:
            con.execute(
                "INSERT OR REPLACE INTO live_signal("
                "signal_uid, source, origin, exchange, market, symbol, timeframe, "
                "strategy_key, strategy_version, structural_kind, direction, event_time, "
                "confirmed_at, available_at, bar_open_time, bar_close_time, detection_low, "
                "detection_atr, display_state, executable_state, payload_json, "
                "first_seen_at, updated_at) "
                "VALUES(?, 'wyckoff_v0_1', ?, 'binance', 'um', ?, '1h', 'wyckoff_v0_1', "
                "'wyckoff_v0_1', 'breakout', ?, ?, ?, ?, ?, ?, 50000.0, 500.0, "
                "'displayable', ?, ?, ?, ?)",
                (uid, origin, symbol, direction, ev, cf, av, ev - H1, ev,
                 exec_state, json.dumps(p, ensure_ascii=False), ev, ev))
            con.commit()
        finally:
            con.close()


# ══════════════════════════════════════════════════════════════════════════
# A. 共振评估 fail-closed
# ══════════════════════════════════════════════════════════════════════════
class TestResonanceFailClosed(FailClosedBase):

    def _assert_blocked(self, uid):
        row = self.live_row(uid)
        self.assertIsNotNone(row)
        self.assertEqual(row["executable_state"], "blocked",
                         "评估失败必须落 blocked，不得留 enterable")
        p = self.payload_of(uid)
        self.assertIsNone(p.get("resonance_score"), "评估失败不得伪造分数（旧实现给 60.0）")
        self.assertIsNone(p.get("position_multiplier"), "评估失败不得伪造仓位倍数")
        self.assertIs(p.get("approved"), False, "评估失败必须明确 approved=false")
        self.assertTrue(p.get("blocked_reasons"), "必须逐条记录阻断原因")
        # 不得生成模拟挂单
        self.router.route_pending_orders(now_ms=T0 + 60_000, origin="live")
        self.assertEqual(self.pending_count(uid), 0, "被阻断的信号不得生成模拟挂单")

    def test_01_resonance_exception_blocks(self):
        """1. 共振函数抛异常 ⇒ blocked + approved=false + 不生成挂单。"""
        orig = WFR.evaluate_resonance

        def boom(**kwargs):
            raise RuntimeError("metrics_engine_down")

        WFR.evaluate_resonance = boom
        try:
            self.router.persist_candidates([make_candidate()], origin="live", now_ms=T0 + 1000)
        finally:
            WFR.evaluate_resonance = orig
        self._assert_blocked("cand_pr2_normal")
        self.assertIn("resonance_eval_exception:RuntimeError",
                      self.payload_of("cand_pr2_normal")["blocked_reasons"])

    def test_02_resonance_missing_data_blocks(self):
        """2. 共振数据缺失（候选缺必需字段）⇒ blocked，且**不调用**打分函数。"""
        called = {"n": 0}
        orig = WFR.evaluate_resonance

        def spy(**kwargs):
            called["n"] += 1
            return orig(**kwargs)

        WFR.evaluate_resonance = spy
        try:
            self.router.persist_candidates(
                [make_candidate(candidate_id="cand_pr2_nodata", box_atr_a=0.0)],
                origin="live", now_ms=T0 + 1000)
        finally:
            WFR.evaluate_resonance = orig
        self.assertEqual(called["n"], 0, "缺数据时不该再去调用打分函数")
        self._assert_blocked("cand_pr2_nodata")
        self.assertIn("candidate_box_invalid:box_atr_a<=0",
                      self.payload_of("cand_pr2_nodata")["blocked_reasons"])

    def test_03_resonance_illegal_result_blocks(self):
        """3. 共振返回非法结果（分数越界/倍数为 0）⇒ blocked。"""
        orig = WFR.evaluate_resonance
        for bad in (
            {"resonance_score": 180.0, "position_multiplier": 1.0, "grade": "x",
             "summary": "s", "factors": {}},
            {"resonance_score": 60.0, "position_multiplier": 0.0, "grade": "x",
             "summary": "s", "factors": {}},
            {"resonance_score": None, "position_multiplier": 1.0, "grade": "x",
             "summary": "s", "factors": {}},
            "not-a-dict",
        ):
            WFR.evaluate_resonance = lambda **kw: bad
            try:
                uid = f"cand_pr2_illegal_{abs(hash(str(bad))) % 10000}"
                self.router.persist_candidates([make_candidate(candidate_id=uid)],
                                               origin="live", now_ms=T0 + 1000)
            finally:
                WFR.evaluate_resonance = orig
            self._assert_blocked(uid)


# ══════════════════════════════════════════════════════════════════════════
# B. 信号时钟因果顺序
# ══════════════════════════════════════════════════════════════════════════
class TestSignalClock(FailClosedBase):

    def test_04_inverted_clock_blocks(self):
        """4. 时间倒挂（available_at 早于 confirmed_at）⇒ blocked，不生成挂单。"""
        self.router.persist_candidates(
            [make_candidate(candidate_id="cand_pr2_inverted", available_at_ms=T0 - 5000)],
            origin="live", now_ms=T0 + 1000)
        row = self.live_row("cand_pr2_inverted")
        self.assertEqual(row["executable_state"], "blocked")
        self.assertIn("confirmed_at_after_available_at",
                      self.payload_of("cand_pr2_inverted")["blocked_reasons"])
        self.router.route_pending_orders(now_ms=T0 + 60_000, origin="live")
        self.assertEqual(self.pending_count("cand_pr2_inverted"), 0)

    def test_05_available_at_missing_blocks(self):
        """5. available_at 缺失 ⇒ blocked；扫描层同样拒绝（SQL 与语义双层）。"""
        self.router.persist_candidates(
            [make_candidate(candidate_id="cand_pr2_noavail", available_at_ms=None)],
            origin="live", now_ms=T0 + 1000)
        self.assertEqual(self.live_row("cand_pr2_noavail")["executable_state"], "blocked")
        self.assertIn("available_at_missing",
                      self.payload_of("cand_pr2_noavail")["blocked_reasons"])

        # 绕过 persist 直接写一行「enterable 但 available_at 为 NULL」的脏数据
        self.insert_signal_row("cand_pr2_dirty_noavail", available_at=None)
        con = sqlite3.connect(str(self.iso_live_db))
        try:
            con.execute("UPDATE live_signal SET available_at=NULL WHERE signal_uid=?",
                        ("cand_pr2_dirty_noavail",))
            con.commit()
        finally:
            con.close()
        con = L.live_conn()
        try:
            rows, diag = self.router._scan_actionable_ex(con, origin="live")
        finally:
            con.close()
        self.assertNotIn("cand_pr2_dirty_noavail", [r["signal_uid"] for r in rows],
                         "available_at 为 NULL 的行不得进入可执行扫描")

    def test_06_unmatched_candidate_needs_no_matched_at(self):
        """未撮合的候选**不需要**预先存在 matched_at_ms（工单 B 的明确约束）。"""
        reasons = UR.validate_signal_time_chain(
            event_time=T0, confirmed_at=T0, available_at=T0 + 1000, read_at_ms=T0 + 2000)
        self.assertEqual(reasons, [], "未撮合候选不得因缺 matched_at 被判违规")
        # 但一旦传入了倒挂的 matched_at，必须判违规
        bad = UR.validate_signal_time_chain(
            event_time=T0, confirmed_at=T0, available_at=T0 + 1000,
            read_at_ms=T0 + 5000, matched_at_ms=T0 + 2000)
        self.assertIn("read_at_after_matched_at", bad)


# ══════════════════════════════════════════════════════════════════════════
# C. 历史信号 / 测试信号隔离 + D. 水位
# ══════════════════════════════════════════════════════════════════════════
class TestScopeIsolation(FailClosedBase):

    def test_07_historical_signal_not_routable(self):
        """5'. 历史研究信号即使 origin 被改、标记 enterable，也不得被路由。"""
        self.insert_signal_row("cand_pr2_hist", origin="historical_research")
        con = L.live_conn()
        try:
            rows, diag = self.router._scan_actionable_ex(con, origin="historical_research")
        finally:
            con.close()
        self.assertEqual(rows, [], "历史研究来源不得进入可执行扫描")
        self.assertIn("origin_not_actionable", str(diag["blocked_reason"]))
        # 有人拿 origin='live' 去扫它（冒充实时）也过不了语义层：
        # 行自身的 origin 仍是 historical_research ⇒ 来源不符 + 来源不可执行，两条都报。
        row = self.live_row("cand_pr2_hist")
        self.assertEqual(row["origin"], "historical_research")
        reasons = UR.signal_admission_reasons(row, origin="live", floor_ms=T0 - 10_000_000)
        self.assertIn("origin_mismatch:historical_research!=live", reasons)
        self.assertIn("origin_not_actionable:historical_research", reasons)

    def test_08_test_uid_not_routable(self):
        """6'. 测试 UID（sig_closeloop_* / sig_ui_test_*）无条件排除。"""
        for uid in ("sig_closeloop_abc", "sig_ui_test_xyz"):
            self.insert_signal_row(uid, origin="live")
        con = L.live_conn()
        try:
            rows, diag = self.router._scan_actionable_ex(con, origin="live")
        finally:
            con.close()
        uids = [r["signal_uid"] for r in rows]
        self.assertNotIn("sig_closeloop_abc", uids)
        self.assertNotIn("sig_ui_test_xyz", uids)
        # 语义层同样给出明确原因（SQL 已粗筛，语义层是最终判据）
        for uid in ("sig_closeloop_abc", "sig_ui_test_xyz"):
            self.assertIn("test_signal_uid",
                          UR.signal_admission_reasons(self.live_row(uid), origin="live"))
        self.router.route_pending_orders(now_ms=T0 + 60_000, origin="live")
        self.assertEqual(self.pending_count("sig_closeloop_abc"), 0)
        self.assertEqual(self.pending_count("sig_ui_test_xyz"), 0)

    def test_09_origin_missing_is_not_live(self):
        """origin 缺失 ⇒ 不得被当作实时记录（安全默认）。"""
        # `live_signal.origin` 是 NOT NULL ⇒ 「缺失」在库里表现为空串/空值。
        self.insert_signal_row("cand_pr2_noorigin", origin="")
        row = self.live_row("cand_pr2_noorigin")
        self.assertEqual(row["origin"], "")
        reasons = UR.signal_admission_reasons(row, origin="live", floor_ms=0)
        self.assertIn("origin_missing", reasons)
        # 扫描入口缺 origin ⇒ 直接拒绝，而不是「全库扫描」
        con = L.live_conn()
        try:
            rows, diag = self.router._scan_actionable_ex(con, origin=None)
        finally:
            con.close()
        self.assertEqual(rows, [])
        self.assertEqual(diag["blocked_reason"], "origin_scope_required")

    def test_10_watermark_unavailable_reason(self):
        """7'. 水位不可用 ⇒ 明确原因码，不静默返回空列表。"""
        self.insert_signal_row("cand_pr2_wm", origin="live")
        orig = L.effective_floor_ms

        def boom():
            raise RuntimeError("live_db_unreadable")

        L.effective_floor_ms = boom
        try:
            res = self.router.route_pending_orders(now_ms=T0 + 60_000, origin="live")
            api_res = API.live_signals({"scope": ["current"]})
        finally:
            L.effective_floor_ms = orig
        self.assertEqual(res["blocked_reason"], API.LIVE_WATERMARK_UNAVAILABLE,
                         "路由侧必须给出 live_watermark_unavailable")
        self.assertEqual(res["registered"], 0)
        self.assertEqual(res["scanned"], 0, "水位不可用 ⇒ 本轮没有扫描")
        self.assertEqual(api_res["quality"]["status"], "no_watermark")
        self.assertEqual(api_res["quality"].get("reason"), API.LIVE_WATERMARK_UNAVAILABLE)
        self.assertIn(API.LIVE_WATERMARK_UNAVAILABLE, api_res.get("missing_reason") or "")

    def test_11_floor_boundary_plus_1ms_kept(self):
        """水位边界仍按真实右边界 +1ms 比较（不放宽、不收紧）。"""
        floor = int(L.effective_floor_ms())
        # confirmed_at 比 floor 小 1ms：真实右边界 = confirmed+1 == floor ⇒ 应**保留**
        self.insert_signal_row("cand_pr2_edge_in", origin="live",
                               event_time=floor - 1, confirmed_at=floor - 1,
                               available_at=floor - 1)
        # 再早 1ms：右边界 = floor-1 < floor ⇒ 应排除
        self.insert_signal_row("cand_pr2_edge_out", origin="live",
                               event_time=floor - 2, confirmed_at=floor - 2,
                               available_at=floor - 2)
        con = L.live_conn()
        try:
            rows, _ = self.router._scan_actionable_ex(con, origin="live")
        finally:
            con.close()
        uids = [r["signal_uid"] for r in rows]
        self.assertIn("cand_pr2_edge_in", uids, "恰好压在边界上的正常闭合信号必须保留（+1ms 口径）")
        self.assertNotIn("cand_pr2_edge_out", uids)


# ══════════════════════════════════════════════════════════════════════════
# 正常信号 / 方向 / 读写一致
# ══════════════════════════════════════════════════════════════════════════
class TestNormalAndConsistency(FailClosedBase):

    def test_12_normal_signal_unchanged(self):
        """8'. 正常实时信号：仍 enterable、分数为真实数值、不被 fail-closed 拦截。"""
        if T0 <= int(L.effective_floor_ms()):
            self.skipTest("机器时钟早于有效下限 ⇒ 无法构造「当前」信号")
        self.router.persist_candidates([make_candidate()], origin="live", now_ms=T0 + 1000)
        row = self.live_row("cand_pr2_normal")
        self.assertEqual(row["executable_state"], "enterable",
                         "正常信号不得被 fail-closed 误伤")
        self.assertEqual(row["origin"], "live")
        p = self.payload_of("cand_pr2_normal")
        self.assertIsInstance(p.get("resonance_score"), (int, float))
        self.assertIsInstance(p.get("position_multiplier"), (int, float))
        self.assertNotIn("approved", p, "正常信号 payload 不新增 approved 键（保持原样）")
        self.assertNotIn("blocked_reasons", p)
        self.assertEqual(UR.signal_admission_reasons(
            row, origin="live", floor_ms=int(L.effective_floor_ms())), [],
            "正常信号必须通过全部准入判据")

        res = self.router.route_pending_orders(now_ms=T0 + 60_000, origin="live")
        self.assertEqual(res["blocked_reason"], None)
        self.assertEqual(res["filtered"], {}, "正常信号不得被语义层过滤")
        self.assertEqual(res["processed"], 1, "正常信号必须进入路由处理")
        # 完整落地（写挂单）还需要生效视图在场；视图缺失属环境问题，与本轮改动无关，
        # 显式跳过并说明，不伪装成通过。
        if not PE.approved_params().get("_config_valid"):
            self.skipTest("生效视图不可用 ⇒ paper_engine 契约校验无法通过（环境缺失，非本轮改动）")
        self.assertEqual(res["registered"], 1, "正常信号必须能登记挂单")
        self.assertEqual(res["rejected"], 0)
        self.assertEqual(self.pending_count("cand_pr2_normal"), 1)

    def test_13_illegal_direction_no_order(self):
        """10'. 方向缺失/非法 ⇒ 不生成模拟订单。"""
        self.insert_signal_row("cand_pr2_nodir", origin="live", direction=None)
        self.insert_signal_row("cand_pr2_shortdir", origin="live", direction="short")
        res = self.router.route_pending_orders(now_ms=T0 + 60_000, origin="live")
        self.assertEqual(self.pending_count("cand_pr2_nodir"), 0)
        self.assertEqual(self.pending_count("cand_pr2_shortdir"), 0)
        self.assertEqual(res["registered"], 0)
        # 语义层必须给出明确原因（不是「什么都没说」）
        for uid, want in (("cand_pr2_nodir", "direction_not_long:None"),
                          ("cand_pr2_shortdir", "direction_not_long:short")):
            self.assertIn(want, UR.signal_admission_reasons(self.live_row(uid), origin="live"))

    def test_14_api_and_router_agree(self):
        """9'. 同一批行：API 当前区保留的集合 == 路由准入的集合。"""
        floor = int(L.effective_floor_ms())
        rows = [
            # uid, origin, exec_state, direction, event/confirmed/available, payload
            ("pr2_ok", "live", "enterable", "long", (T0, T0, T0 + 1000), None),
            ("pr2_recovered", "recovered", "enterable", "long", (T0, T0, T0 + 1000), None),
            ("pr2_hist", "historical_research", "enterable", "long", (T0, T0, T0 + 1000), None),
            ("pr2_blocked", "live", "blocked", "long", (T0, T0, T0 + 1000), None),
            ("pr2_inverted", "live", "enterable", "long", (T0, T0, T0 - 5000), None),
            ("pr2_approved_false", "live", "enterable", "long", (T0, T0, T0 + 1000),
             {"approved": False}),
            ("pr2_before_floor", "live", "enterable", "long",
             (floor - 10 * H1, floor - 10 * H1, floor - 10 * H1), None),
            ("pr2_nodir", "live", "enterable", None, (T0, T0, T0 + 1000), None),
            ("sig_ui_test_pr2", "live", "enterable", "long", (T0, T0, T0 + 1000), None),
        ]
        for uid, origin, st, direction, times, payload in rows:
            # 每个夹具用**不同 symbol**：避免展示层的「同买点合并」把多行折成一张卡，
            # 那样比对的就不是准入判据而是聚合结果了。
            self.insert_signal_row(uid, origin=origin, exec_state=st, direction=direction,
                                   event_time=times[0], confirmed_at=times[1],
                                   available_at=times[2], payload=payload,
                                   symbol=f"PR2{uid.upper().replace('_', '')}USDT")

        api_items = API.live_signals({"scope": ["current"]})["items"]
        api_keeps = {i["signal_id"] for i in api_items}

        router_keeps = set()
        con = L.live_conn()
        try:
            for uid, origin, _st, _d, _t, _p in rows:
                got, _diag = self.router._scan_actionable_ex(con, origin=origin or "")
                router_keeps |= {r["signal_uid"] for r in got}
        finally:
            con.close()

        self.assertEqual(api_keeps, router_keeps,
                         f"读写两侧准入必须一致；API={sorted(api_keeps)} 路由={sorted(router_keeps)}")
        self.assertIn("pr2_ok", api_keeps)
        self.assertIn("pr2_recovered", api_keeps)
        for uid in ("pr2_hist", "pr2_blocked", "pr2_inverted", "pr2_approved_false",
                    "pr2_before_floor", "pr2_nodir", "sig_ui_test_pr2"):
            self.assertNotIn(uid, api_keeps, f"{uid} 不应进入当前区")


# ══════════════════════════════════════════════════════════════════════════
# 来源矩阵：未知来源必须**显式拒绝**，合法来源保持原行为
# （PR2-WO-02-REWORK-01 工单 A / C 第 1–6 条）
# ══════════════════════════════════════════════════════════════════════════
class TestOriginMatrix(FailClosedBase):
    """来源白名单 `("live","recovered")` 之外一律拒绝，且必须给出**原因码**。

    为什么单列这一类：旧实现只拦「空来源」和「历史/回放」，其余来源会带着
    `origin=?` 继续扫描，被 SQL 里的 `origin IN ('live','recovered')` 恒假过滤掉，
    于是返回 0 条 + `blocked_reason=None` —— 与「扫过了、确实没有信号」**无法区分**。
    本类断言的就是「这种无法区分必须消失」。
    """

    # 合法来源：必须正常扫描、正常路由
    ACTIONABLE = ("live", "recovered")
    # 已知不可执行来源：原因码沿用原实现 `origin_not_actionable:<x>`（不改语义）
    NON_ACTIONABLE = ("historical_research", "replay")

    def _scan(self, origin):
        con = L.live_conn()
        try:
            return self.router._scan_actionable_ex(con, origin=origin)
        finally:
            con.close()

    def test_15_unknown_origin_test_is_explicitly_rejected(self):
        """1. `origin="test"` ⇒ 明确拒绝 `origin_unknown:test`，不生成挂单。"""
        uid = "cand_pr2_origin_test"
        self.insert_signal_row(uid, origin="test", symbol="PR2ORIGINTESTUSDT")

        # 前提事实：这条行在**语义层**是「干净」的 —— 它不带任何不可执行标记。
        # 正因为它自己看不出问题，才必须在**扫描入口**按来源显式拒绝；
        # 否则它会静默消失在 SQL 过滤里，只留下一个「本轮 0 条」。
        self.assertEqual(UR.signal_admission_reasons(self.live_row(uid), origin="test"),
                         [], "语义层对未知来源本身不报错（这正是需要扫描层显式拒绝的原因）")

        rows, diag = self._scan("test")
        self.assertEqual(rows, [], "未知来源不得扫出任何可执行行")
        self.assertEqual(diag["blocked_reason"], "origin_unknown:test",
                         "未知来源必须给出明确原因码，不得静默返回 0 条")
        self.assertEqual(diag["scanned"], 0, "未知来源不得进入扫描（scanned 必须是 0，不是「扫了没结果」）")

        res = self.router.route_pending_orders(now_ms=T0 + 60_000, origin="test")
        self.assertEqual(res["blocked_reason"], "origin_unknown:test")
        self.assertEqual(res["registered"], 0, "未知来源不得登记挂单")
        self.assertEqual(res["processed"], 0, "未知来源不得进入处理")
        self.assertEqual(res["scanned"], 0)
        self.assertEqual(self.pending_count(uid), 0, "未知来源不得生成模拟挂单")

    def test_16_unknown_origin_other_unknowns_are_rejected(self):
        """未知来源不是特例：任何白名单外的值都走同一条拒绝路径。"""
        for i, bad in enumerate(("signal", "manual", "LIVE", "test_uid")):
            uid = f"cand_pr2_origin_unknown_{i}"
            self.insert_signal_row(uid, origin=bad, symbol=f"PR2UNK{i}USDT")
            rows, diag = self._scan(bad)
            self.assertEqual(rows, [], f"来源 {bad!r} 不得扫出可执行行")
            self.assertEqual(diag["blocked_reason"], f"origin_unknown:{bad.strip()}",
                             f"来源 {bad!r} 必须给出 origin_unknown 原因码")
            res = self.router.route_pending_orders(now_ms=T0 + 60_000, origin=bad)
            self.assertEqual(res["registered"], 0, f"来源 {bad!r} 不得登记挂单")
            self.assertEqual(self.pending_count(uid), 0)

    def test_17_non_actionable_origins_keep_their_reason_code(self):
        """2/3. `historical_research` / `replay` 仍用原有原因码（不被新分支吞掉）。"""
        for bad in self.NON_ACTIONABLE:
            uid = f"cand_pr2_na_{bad}"
            self.insert_signal_row(uid, origin=bad, symbol="PR2NAUSDT")
            rows, diag = self._scan(bad)
            self.assertEqual(rows, [])
            self.assertEqual(diag["blocked_reason"], f"origin_not_actionable:{bad}",
                             "历史/回放来源的既有原因码不得被新分支改写")
            res = self.router.route_pending_orders(now_ms=T0 + 60_000, origin=bad)
            self.assertEqual(res["registered"], 0)
            self.assertEqual(res["blocked_reason"], f"origin_not_actionable:{bad}")
            self.assertEqual(self.pending_count(uid), 0)

    def test_18_empty_origin_is_rejected(self):
        """4. 空来源 / 缺来源 ⇒ `origin_scope_required`（安全默认：不知道来源就不扫）。"""
        self.insert_signal_row("cand_pr2_origin_empty", origin="", symbol="PR2EMPTYUSDT")
        for bad in ("", "   ", None):
            rows, diag = self._scan(bad)
            self.assertEqual(rows, [], f"来源 {bad!r} 不得扫出可执行行")
            self.assertEqual(diag["blocked_reason"], "origin_scope_required",
                             f"来源 {bad!r} 必须明确要求来源，不得退化成全库扫描")
        res = self.router.route_pending_orders(now_ms=T0 + 60_000, origin=None)
        self.assertEqual(res["blocked_reason"], "origin_scope_required")
        self.assertEqual(res["registered"], 0)
        self.assertEqual(self.pending_count("cand_pr2_origin_empty"), 0)

    def test_19_live_and_recovered_still_route(self):
        """5/6. `live` 与 `recovered` 必须保持原行为：正常扫描、正常进入路由。"""
        self.insert_signal_row("cand_pr2_ok_live", origin="live",
                               symbol="PR2OKLIVEUSDT")
        self.insert_signal_row("cand_pr2_ok_recovered", origin="recovered",
                               symbol="PR2OKRECUSDT")

        # ① 扫描按来源各自只看见自己的行（不串味、也不被新分支误伤）
        live_rows, live_diag = self._scan("live")
        rec_rows, rec_diag = self._scan("recovered")
        self.assertIsNone(live_diag["blocked_reason"], "live 不得被拒绝")
        self.assertIsNone(rec_diag["blocked_reason"], "recovered 不得被拒绝")
        self.assertEqual({r["signal_uid"] for r in live_rows}, {"cand_pr2_ok_live"})
        self.assertEqual({r["signal_uid"] for r in rec_rows}, {"cand_pr2_ok_recovered"})

        # ② 两条都必须进入路由处理（processed 计的是「真被处理」，不是「登记成功」）
        res_live = self.router.route_pending_orders(now_ms=T0 + 60_000, origin="live")
        res_rec = self.router.route_pending_orders(now_ms=T0 + 60_000, origin="recovered")
        self.assertIsNone(res_live["blocked_reason"])
        self.assertIsNone(res_rec["blocked_reason"])
        self.assertEqual(res_live["processed"], 1, "live 信号必须进入路由处理")
        self.assertEqual(res_rec["processed"], 1, "recovered 信号必须进入路由处理")
        self.assertNotIn("cand_pr2_ok_live", res_live["filtered"])
        self.assertNotIn("cand_pr2_ok_recovered", res_rec["filtered"])

        # ③ 真正落成挂单还依赖生效视图在场；视图缺失属环境问题，与来源判据无关，
        #    如实跳过并说明，不伪装成通过。
        if not PE.approved_params().get("_config_valid"):
            self.skipTest("生效视图不可用 ⇒ paper_engine 契约校验无法通过（环境缺失，非本轮改动）")
        self.assertEqual(res_live["registered"], 1, "live 信号必须能登记挂单")
        self.assertEqual(res_rec["registered"], 1, "recovered 信号必须能登记挂单")
        self.assertEqual(self.pending_count("cand_pr2_ok_live"), 1)
        self.assertEqual(self.pending_count("cand_pr2_ok_recovered"), 1)

    def test_20_test_uid_still_not_routable_for_legal_origins(self):
        """7. 测试 UID 仍无法路由 —— 且不因新增来源分支而失效。"""
        for origin in self.ACTIONABLE:
            uid = f"sig_closeloop_{origin}"
            self.insert_signal_row(uid, origin=origin, symbol=f"PR2TUID{origin.upper()}USDT")
            rows, diag = self._scan(origin)
            self.assertIsNone(diag["blocked_reason"], f"{origin} 是合法来源，不得被来源判据拒绝")
            self.assertNotIn(uid, {r["signal_uid"] for r in rows},
                             "测试 UID 必须被 SQL 粗筛排除")
            self.assertIn("test_signal_uid",
                          UR.signal_admission_reasons(self.live_row(uid), origin=origin),
                          "语义层必须给出 test_signal_uid 原因")
            self.router.route_pending_orders(now_ms=T0 + 60_000, origin=origin)
            self.assertEqual(self.pending_count(uid), 0, "测试 UID 不得生成挂单")


if __name__ == "__main__":
    unittest.main(verbosity=2)

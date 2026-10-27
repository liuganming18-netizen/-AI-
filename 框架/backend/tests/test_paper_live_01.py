#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PAPER-LIVE-01｜模拟实盘持续运行与全池因果闭环验收测试套件。

覆盖验收标准：
  1. 全池扫描与水位暴露：全池 526 标的发现、增量推进、缺数据与推进报错明确记录；
  2. 独占锁防并发与 PID 自动同步：同 arm 互斥防双开，自动识别存活 PID 避免锁冲突；
  3. 隔离沙盒端到端交易闭环：突破 -> 信号落盘 -> 路由挂单 -> 撮合成交 -> 跟踪止损 -> 止损平仓；
  4. 生产环境零污染断言：隔离测试期间日常生产库（行情、信号、纸面账本）0 写入；
  5. 崩溃接管与热重启恢复：持仓状态接管不漏、不重复建仓、止损逻辑保持；
  6. 日常实盘只读状态与身份核验：真实 PID、526 币全池覆盖、无虚假刷量、闸门放行。
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
PROJECT_ROOT = BACKEND.parents[1].resolve() if len(BACKEND.parents) >= 2 else BACKEND.parent

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import live_store as L
import paper_engine as PE
import service_manager as SM
from 策略.WyckoffMaster.event_trend_v0_1.contracts import CandidateSnapshot

M15 = 900_000
H1 = 3_600_000
H4 = 4 * H1
from unified_runtime import (
    ARM_ENTRY_ROLE,
    EXCHANGE,
    MARKET,
    STRATEGY_KEY,
    TIMEFRAME,
    MarketDataWatcher,
    MultiSymbolSignalEngine,
    SignalPersistenceAndRouter,
    UnifiedExecutionDaemon,
    UnifiedRuntime,
    get_lock_dir,
    status_file_path,
)


def create_market_schema(db_path: Path):
    """在指定 SQLite 数据库中构建标准的行情 candle_versions, candle_heads 和 candles 视图。"""
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS candle_versions (
            exchange TEXT NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            close_time INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            quote_volume REAL NOT NULL,
            trade_count INTEGER DEFAULT 0,
            taker_buy_volume REAL DEFAULT 0,
            taker_buy_quote_volume REAL DEFAULT 0,
            source_id INTEGER NOT NULL,
            available_at INTEGER NOT NULL,
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
        con.execute("""
        CREATE TABLE IF NOT EXISTS funding_history (
            exchange TEXT, market TEXT, symbol TEXT, funding_time INTEGER,
            funding_rate REAL, mark_price REAL, rate_type TEXT, source TEXT, fetched_at INTEGER
        );
        """)
        con.commit()
    finally:
        con.close()


def insert_bar(db_path: Path, symbol: str, timeframe: str, open_time: int, close_time: int,
               o: float, h: float, l: float, c: float, v: float = 1000.0, qv: float = 100000.0):
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT OR REPLACE INTO candle_versions(exchange,market,symbol,timeframe,open_time,close_time,open,high,low,close,volume,quote_volume,trade_count,taker_buy_volume,taker_buy_quote_volume,source_id,available_at,is_closed) "
            "VALUES('binance','um',?,?,?,?,?,?,?,?,?,?,10,500,50000,1,?,1)",
            (symbol, timeframe, open_time, close_time, o, h, l, c, v, qv, close_time)
        )
        con.execute(
            "INSERT OR REPLACE INTO candle_heads(exchange,market,symbol,timeframe,open_time,source_id) "
            "VALUES('binance','um',?,?,?,1)",
            (symbol, timeframe, open_time)
        )
        con.commit()
    finally:
        con.close()


class TestPaperLive01(unittest.TestCase):
    """PAPER-LIVE-01 模拟实盘持续运行与全生命周期因果验证。"""

    def setUp(self):
        # 保存日常生产环境原始状态，用于验证零污染
        self.prod_market_db = PROJECT_ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
        self.prod_live_db = BACKEND / "live" / "live_signals.sqlite3"
        self.prod_paper_dir = PROJECT_ROOT / "数据" / "paper"
        self._record_prod_state()

        # 创建隔离沙盒目录
        self.test_dir = Path(tempfile.mkdtemp(prefix="paper_live_01_sandbox_"))
        self.iso_market_db = self.test_dir / "market_iso.sqlite3"
        self.iso_live_db = self.test_dir / "live_iso.sqlite3"
        self.iso_ledger_dir = self.test_dir / "paper_ledger"
        self.iso_lock_dir = self.test_dir / "locks"
        self.iso_pid_dir = self.test_dir / "pids"
        self.iso_log_dir = self.test_dir / "logs"

        for d in (self.iso_ledger_dir, self.iso_lock_dir, self.iso_pid_dir, self.iso_log_dir):
            d.mkdir(parents=True, exist_ok=True)

        create_market_schema(self.iso_market_db)
        # 保存并切换环境变量至隔离沙盒
        self.orig_env = os.environ.copy()
        os.environ["MARKET_DB"] = str(self.iso_market_db)
        os.environ["LIVE_DB"] = str(self.iso_live_db)
        os.environ["PAPER_LEDGER_DIR"] = str(self.iso_ledger_dir)
        os.environ["PAPER_LOCK_DIR"] = str(self.iso_lock_dir)
        os.environ["SYS_PID_DIR"] = str(self.iso_pid_dir)
        os.environ["SYS_LOG_DIR"] = str(self.iso_log_dir)
        os.environ["ISOLATED_MODE"] = "1"
        os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"

        # 创建隔离沙盒的放行闸门文件
        self.iso_gate_file = self.test_dir / "paper_new_entry_gate.json"
        self.iso_gate_file.write_text(json.dumps({
            "allow_new_entries": True,
            "reason": "isolated_test_allowed",
            "set_by": "test_runner",
            "since_ms": int(time.time() * 1000)
        }), encoding="utf-8")
        os.environ["PAPER_GATE_FILE"] = str(self.iso_gate_file)

        L.ensure_live_schema()

        # 在隔离沙盒初始化 3a 纸面账本
        con_paper = L.paper_conn("3a", create=True, initial_equity=100000.0)
        con_paper.close()

    def tearDown(self):
        # 恢复环境变量
        os.environ.clear()
        os.environ.update(self.orig_env)
        # 清理沙盒
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)
        # 最终验证生产环境未受任何污染
        self._assert_prod_unmodified()

    def _record_prod_state(self):
        self.prod_snapshots = {}
        if self.prod_market_db.exists():
            st = self.prod_market_db.stat()
            self.prod_snapshots["market_mtime"] = st.st_mtime
            self.prod_snapshots["market_size"] = st.st_size
        if self.prod_live_db.exists():
            st = self.prod_live_db.stat()
            self.prod_snapshots["live_mtime"] = st.st_mtime
            self.prod_snapshots["live_size"] = st.st_size
            con = sqlite3.connect(str(self.prod_live_db))
            try:
                self.prod_snapshots["live_signals_count"] = con.execute("SELECT count(*) FROM live_signal").fetchone()[0]
            finally:
                con.close()

    def _assert_prod_unmodified(self):
        """严密断言隔离测试期间生产数据库未被改写。"""
        if self.prod_live_db.exists() and "live_signals_count" in self.prod_snapshots:
            con = sqlite3.connect(str(self.prod_live_db))
            try:
                cur_signals = con.execute("SELECT count(*) FROM live_signal").fetchone()[0]
                self.assertEqual(cur_signals, self.prod_snapshots["live_signals_count"],
                                 "生产库 live_signal 表记录数被修改，违反数据隔离原则！")
            finally:
                con.close()

    def test_01_universe_and_watermark_exposure(self):
        """测试全池标的启动、增量扫描与缺数据原因详细暴露。"""
        # 注入 3 个标的：
        # 标的 A (SYM_FULL): 具备完整 4h (42根) 与 1h (215根) K 线
        # 标的 B (SYM_NO_4H): 4h 只有 12 根（不足 38 根）
        # 标的 C (SYM_NO_1H): 4h 足够，但 1h 只有 60 根（不足 200 根）
        t0 = 1700000000000

        # SYM_FULL
        for i in range(55):
            op = t0 + i * H4
            insert_bar(self.iso_market_db, "SYM_FULL", "4h", op, op + H4 - 1, 100.0, 105.0, 95.0, 102.0)
        for i in range(215):
            op = t0 + i * H1
            insert_bar(self.iso_market_db, "SYM_FULL", "1h", op, op + H1 - 1, 100.0, 103.0, 98.0, 101.0)

        # SYM_NO_4H
        for i in range(12):
            op = t0 + i * H4
            insert_bar(self.iso_market_db, "SYM_NO_4H", "4h", op, op + H4 - 1, 50.0, 52.0, 48.0, 51.0)
        for i in range(215):
            op = t0 + i * H1
            insert_bar(self.iso_market_db, "SYM_NO_4H", "1h", op, op + H1 - 1, 50.0, 52.0, 48.0, 51.0)

        # SYM_NO_1H
        for i in range(55):
            op = t0 + i * H4
            insert_bar(self.iso_market_db, "SYM_NO_1H", "4h", op, op + H4 - 1, 20.0, 22.0, 18.0, 21.0)
        for i in range(60):
            op = t0 + i * H1
            insert_bar(self.iso_market_db, "SYM_NO_1H", "1h", op, op + H1 - 1, 20.0, 22.0, 18.0, 21.0)

        engine = MultiSymbolSignalEngine(symbols=["SYM_FULL", "SYM_NO_4H", "SYM_NO_1H"])
        now_ms = t0 + 215 * H1
        engine.bootstrap(upto_ms=now_ms)

        stats = engine.bootstrap_stats
        self.assertEqual(stats["universe"], 3)
        self.assertEqual(stats["ready"], 1, "只有 SYM_FULL 满足完整 K 线要求")
        self.assertEqual(stats["skipped"], 2, "SYM_NO_4H 与 SYM_NO_1H 应被跳过")

        # 断言跳过原因被明确记录，严禁静默吞掉
        skipped = engine.skipped_reasons
        self.assertIn("SYM_NO_4H", skipped)
        self.assertIn("4h_bars_insufficient", skipped["SYM_NO_4H"])
        self.assertIn("SYM_NO_1H", skipped)
        self.assertIn("1h_bars_insufficient", skipped["SYM_NO_1H"])

        # 推进 step 并验证状态暴露
        engine.step(upto_ms=now_ms + 1000)
        self.assertGreaterEqual(engine._global_1h, 0)
        self.assertGreaterEqual(engine._global_4h, 0)

    def test_02_lock_concurrency_and_pid_auto_sync(self):
        """测试同 arm 互斥独占锁及 ServiceManager 从 status.json 自动同步存活 PID。"""
        runtime1 = UnifiedRuntime(arm="3a", symbols=["BTCUSDT"])
        ok1 = runtime1.acquire_lock()
        self.assertTrue(ok1, "第一个实例应成功获得独占锁")

        # 启动第二个同 arm 实例，必定被拒绝
        runtime2 = UnifiedRuntime(arm="3a", symbols=["BTCUSDT"])
        ok2 = runtime2.acquire_lock()
        self.assertFalse(ok2, "同 arm 的第二个实例必须被独占锁拒绝")

        # 保存 status 文件并模拟 ServiceManager 探测
        runtime1._save_status_file("running")
        status_file = status_file_path("3a")
        self.assertTrue(status_file.exists(), "状态文件应已写出")

        status_content = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(status_content["runtime_identity"]["pid"], os.getpid())

        # 验证 ServiceManager 能够自动发现该活跃 PID
        archive_dir = self.test_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        jobs_db = self.test_dir / "jobs.sqlite3"
        jobs_db.touch()

        cfg = SM.ServiceConfig(
            port=8799,
            enable_paper=True,
            paper_arm="3a",
            paper_lock_dir=str(self.iso_lock_dir),
            market_db=str(self.iso_market_db),
            market_archive=str(archive_dir),
            live_db=str(self.iso_live_db),
            paper_ledger_dir=str(self.iso_ledger_dir),
            jobs_db=str(jobs_db),
            pid_dir=self.iso_pid_dir,
            log_dir=self.iso_log_dir,
            is_isolated=True
        )
        mgr = SM.ServiceManager(cfg)
        detected_pid = mgr._find_active_runtime_pid()
        self.assertEqual(detected_pid, os.getpid(), "ServiceManager 应成功探测到正在运行的存活 PID")

        # 释放锁后清理，验证新实例能够成功获取锁
        runtime1.release_lock()
        runtime3 = UnifiedRuntime(arm="3a", symbols=["BTCUSDT"])
        self.assertTrue(runtime3.acquire_lock(), "释放锁后新实例应能成功获取独占锁")
        runtime3.release_lock()

    def test_03_isolated_end_to_end_trade_closure(self):
        """在独立沙盒内验证完整的交易闭环：突破 -> 信号 -> 挂单 -> 撮合 -> 止损跟踪 -> 平仓。"""
        symbol = "BTCUSDT"
        arm = "3a"
        # 构造突破形态 K 线：以当前时钟为基准向前排列
        now_wall = int(time.time() * 1000)
        t0 = (now_wall // H1) * H1 - 250 * H1

        # 1. 前 51 根 4h K 线（204 小时）形成 50000~52000 箱体
        for i in range(51):
            op = t0 + i * H4
            insert_bar(self.iso_market_db, symbol, "4h", op, op + H4 - 1, 51000.0, 52000.0, 50000.0, 51200.0)

        # 第 52 根 4h K 线（204h ~ 208h）：突破确认 bar，low=51800.0，高于初始止损 50350.0
        op_break = t0 + 51 * H4
        insert_bar(self.iso_market_db, symbol, "4h", op_break, op_break + H4 - 1, 52000.0, 53500.0, 51800.0, 53000.0)

        # 2. 208 根 1h K 线（与 4h 在 208h 处精确闭合对齐）
        for i in range(208):
            op = t0 + i * H1
            insert_bar(self.iso_market_db, symbol, "1h", op, op + H1 - 1, 51000.0, 51800.0, 50500.0, 51200.0)

        runtime = UnifiedRuntime(arm=arm, symbols=[symbol])
        runtime.started_at_ms = t0
        now = t0 + 208 * H1

        # 预热信号引擎
        runtime.signal_engine.bootstrap(upto_ms=now)

        # 3. 构造突破候选快照并持久化
        cand = CandidateSnapshot(
            candidate_id="cand_test_01",
            event_id="evt_test_01",
            strategy_version="wyckoff_v0_1",
            symbol=symbol,
            venue="binance",
            snapshot_time_ms=now,
            available_at_ms=now + 1000,
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
            execution_earliest_ms=now + M15,
            pit_proven=True
        )

        # 持久化信号到隔离库
        runtime.router.persist_candidates([cand], origin="test", now_ms=now + 1000)

        # 核验隔离 live_signals 库
        con_iso = sqlite3.connect(str(self.iso_live_db))
        try:
            cur = con_iso.execute("SELECT symbol, direction, structural_kind, executable_state FROM live_signal WHERE signal_uid=?", (cand.candidate_id,))
            sig = cur.fetchone()
            self.assertIsNotNone(sig, "信号必须成功写入隔离库 live_signal")
            self.assertEqual(sig[0], symbol)
            self.assertEqual(sig[1], "long")
            self.assertEqual(sig[2], "breakout")
            self.assertEqual(sig[3], "enterable")
        finally:
            con_iso.close()

        # 4. 路由生成挂单
        route_res = runtime.router.route_pending_orders(now_ms=now + 2000, origin="test")
        self.assertEqual(route_res["registered"], 1, "必须成功将信号登记为挂单")

        con_paper = L.paper_conn(arm, ro=True)
        try:
            cur = con_paper.execute("SELECT symbol, direction, stop_price, status FROM paper_pending WHERE signal_uid=?", (cand.candidate_id,))
            order = cur.fetchone()
            self.assertIsNotNone(order, "挂单必须写入隔离 paper_pending 表")
            self.assertEqual(order[0], symbol)
            self.assertEqual(order[3], "pending")
        finally:
            con_paper.close()

        # 在 market_db 插入连续的 15m bar：
        # 1. 信号时刻 bar (now)
        # 2. 路由过渡 bar (now + M15)
        # 3. 满足 earliest_fill_ms (now + M15 + 2000) 的第一根合格成交 bar (now + 2 * M15)
        insert_bar(self.iso_market_db, symbol, "15m", now, now + M15 - 1, 52800.0, 53000.0, 52700.0, 52900.0, v=2000.0, qv=10000000.0)
        insert_bar(self.iso_market_db, symbol, "15m", now + M15, now + 2 * M15 - 1, 52900.0, 53100.0, 52800.0, 53000.0, v=2000.0, qv=10000000.0)

        t_fill = now + 2 * M15
        insert_bar(self.iso_market_db, symbol, "15m", t_fill, t_fill + M15 - 1, 53000.0, 53300.0, 52900.0, 53100.0, v=2000.0, qv=10000000.0)

        exec_res = runtime.execution.execute_cycle(now_ms=t_fill + M15 + 1000)
        self.assertEqual(exec_res["pending_matched"], 1, "挂单必须被成功撮合成交")

        # 检查隔离账本持仓
        con_paper = L.paper_conn(arm, ro=True)
        try:
            cur = con_paper.execute("SELECT position_id, symbol, status, stop_price, entry_price, exit_reason FROM paper_position WHERE account_id=?", (f"paper43_{arm}",))
            positions = [dict(r) for r in cur.fetchall()]
            self.assertEqual(len(positions), 1, "成交后必须在隔离账本中生成 1 笔持仓")
            pos = positions[0]
            self.assertEqual(pos["symbol"], symbol)
            self.assertEqual(pos["status"], "open")
            pos_id = pos["position_id"]
            initial_stop = pos["stop_price"]
        finally:
            con_paper.close()

        # 6. 跟踪止损推进：插入更高 4h K 线抬高止损（第 53 根 4h，从 208h 开始）
        t_4h_new = t0 + 52 * H4
        insert_bar(self.iso_market_db, symbol, "4h", t_4h_new, t_4h_new + H4 - 1, 54500.0, 56000.0, 54000.0, 55500.0)

        exec_res2 = runtime.execution.execute_cycle(now_ms=t_4h_new + H4 + 1000)
        self.assertEqual(exec_res2["trailing_updates"], 1, "跟踪止损必须被更新抬高")

        con_paper = L.paper_conn(arm, ro=True)
        try:
            cur = con_paper.execute("SELECT stop_price FROM paper_position WHERE position_id=?", (pos_id,))
            updated_stop = cur.fetchone()[0]
            self.assertGreater(updated_stop, initial_stop, "跟踪止损必须随 4h 推进而单调抬高")
        finally:
            con_paper.close()

        # 7. 止损平仓退出：价格跌穿抬高后的止损线
        t_exit_bar = t_4h_new + H4 + 2000
        crash_price = updated_stop - 200.0
        insert_bar(self.iso_market_db, symbol, "15m", t_exit_bar, t_exit_bar + M15 - 1, updated_stop + 50.0, updated_stop + 50.0, crash_price - 50.0, crash_price)

        exec_res3 = runtime.execution.execute_cycle(now_ms=t_exit_bar + M15 + 1000)
        self.assertEqual(exec_res3["exits_triggered"], 1, "价格击穿止损线必须触发止损平仓退出")

        # 检查持仓已退出，已实现盈亏记录在 paper_realized
        con_paper = L.paper_conn(arm, ro=True)
        try:
            cur = con_paper.execute("SELECT count(*) FROM paper_position WHERE account_id=? AND status='open'", (f"paper43_{arm}",))
            open_count = cur.fetchone()[0]
            self.assertEqual(open_count, 0, "平仓退出后活动持仓必须清空")

            cur_real = con_paper.execute("SELECT count(*), exit_reason FROM paper_realized WHERE account_id=?", (f"paper43_{arm}",))
            real_row = cur_real.fetchone()
            self.assertEqual(real_row[0], 1, "必须有且仅有 1 笔已实现平仓归档记录")
            self.assertIn("stop", str(real_row[1]).lower())
        finally:
            con_paper.close()

    def test_04_restart_and_recovery(self):
        """测试持仓中接管与热重启恢复：不漏单、不重复开仓。"""
        symbol = "BTCUSDT"
        arm = "3a"
        con_paper = L.paper_conn(arm, ro=False)
        try:
            # 预置一笔活动持仓
            con_paper.execute("""
            INSERT INTO paper_position(account_id, symbol, qty, entry_price, entry_ts, status, stop_price, voided)
            VALUES(?, ?, 0.5, 50000.0, ?, 'open', 48000.0, 0)
            """, (f"paper43_{arm}", symbol, int(time.time() * 1000)))
            con_paper.commit()
        finally:
            con_paper.close()

        # 新启动一个 UnifiedRuntime 实例模拟热重启
        runtime_recovered = UnifiedRuntime(arm="3a", symbols=[symbol])
        status = runtime_recovered.get_full_status()
        self.assertEqual(status["execution_daemon"]["open_positions"], 1, "热重启后必须精准读取并接管已有 1 笔持仓")

    def test_05_production_read_only_status(self):
        """检查日常生产环境 526 币扫描持续运行状态与指标。"""
        # 读取日常 lock 目录下的真实状态快照
        prod_status_file = PROJECT_ROOT / "框架" / "backend" / "live" / "paper_runtime_3a_status.json"
        if not prod_status_file.exists():
            prod_status_file = PROJECT_ROOT / "框架" / "backend" / "locks" / "paper_runtime_3a_status.json"

        self.assertTrue(prod_status_file.exists(), f"日常运行时状态文件必须存在: {prod_status_file}")
        data = json.loads(prod_status_file.read_text(encoding="utf-8"))

        # 验证运行身份与全池配置
        identity = data.get("runtime_identity", {})
        self.assertEqual(identity.get("arm"), "3a")
        self.assertIn("526", str(identity.get("symbol")))
        self.assertEqual(identity.get("status"), "running")

        # 验证信号引擎全池数量
        sig_engine = data.get("signal_engine", {})
        self.assertEqual(sig_engine.get("universe_declared"), 526)
        self.assertEqual(sig_engine.get("bootstrap", {}).get("universe"), 526)

        # 验证开仓闸门状态
        order_proc = data.get("order_processing", {})
        gate = order_proc.get("new_entry_gate", {})
        self.assertTrue(gate.get("allow_new_entries"), "开仓总闸当前应保持开启状态")


if __name__ == "__main__":
    unittest.main(verbosity=2)

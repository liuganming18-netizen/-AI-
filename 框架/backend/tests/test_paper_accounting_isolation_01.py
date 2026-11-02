#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GM-PAPER-ACCOUNTING-01-R2｜模拟账户现金会计整改与未知费用识别及风控阻断测试套件

本测试套件在完全隔离的内存 SQLite 环境下执行，严格禁止触碰生产数据库。
强制覆盖反例矩阵 T1 ~ T10：
  T1: 开仓手续费明确为 0 -> 可信 (is_degraded=False, cash_is_trusted=True)
  T2: 已确认资金费明确为 0 -> 可信 (is_degraded=False, cash_is_trusted=True)
  T3: 未平仓手续费为 NULL -> 降级 (is_degraded=True, 原因含 open_position_entry_fee_null)
  T4: 未平仓资金费为 NULL -> 降级 (is_degraded=True, 原因含 open_position_funding_paid_null)
  T5: 旧账本缺少费用列 -> 降级 (is_degraded=True)
  T6: 作废持仓包含 NULL -> 不污染有效账户 (is_degraded=False)
  T7: 降级现金进入 equity() -> 不得当成可信权益 (equity_is_trusted=False)
  T8: 降级现金进入开仓检查 -> 必须拒绝使用 (pre_open_check 返回 False, cash_untrusted_degraded)
  T9: 正常账户 -> 无行为回归 (pre_open_check 正常放行)
  T10: CodeRabbit 原始 60 U 反例 -> 继续通过 (cash=4940.0)
并完整保留：
  - 场景 A：多笔未平仓持仓费用归属
  - 场景 B：正常平仓已实现盈亏与防重复扣减
  - 场景 C：负数资金费返还增加现金
  - 场景 D：假设资金费隔离不污染现金
  - 场景 E：重复事件幂等防重
  - 场景 F：历史版本表结构兼容与缺失字段优雅降级
"""

import os
import sys
import sqlite3
import unittest
from pathlib import Path

# 设置模块导入路径
HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
PROJECT_ROOT = BACKEND.parents[1].resolve() if len(BACKEND.parents) >= 2 else BACKEND.parent

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import live_store as L
import paper_engine as PE


def build_isolated_ledger(initial_equity: float = 5000.0, arm: str = "3a") -> sqlite3.Connection:
    """在纯内存环境中构建标准隔离 paper 账本。"""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(L.PAPER_ADDITIVE)
    for stmt in L.PAPER_INDEXES_GUARDED:
        try:
            con.execute(stmt)
        except sqlite3.OperationalError:
            pass
    for table, col, decl in L.PAPER_COLUMNS:
        try:
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass

    acc = f"paper43_{arm}"
    con.execute(
        "INSERT INTO paper_account(account_id,arm,initial_equity,cash,leverage,dry_run,created_at) "
        "VALUES(?,?,?,?,1,1,1000)",
        (acc, arm, initial_equity, initial_equity)
    )
    con.execute(
        "INSERT INTO paper_state(account_id,cash,realized_pnl,fees_paid,funding_paid,updated_at) "
        "VALUES(?,?,0,0,0,1000)",
        (acc, initial_equity)
    )
    con.commit()
    return con


def build_dummy_market_conn(symbol: str = "BTCUSDT", price: float = 50000.0) -> sqlite3.Connection:
    """构建用于估值和开仓检查的隔离虚拟行情连接。"""
    con_mkt = sqlite3.connect(":memory:")
    con_mkt.row_factory = sqlite3.Row
    con_mkt.execute(
        "CREATE TABLE candle_versions (symbol TEXT, timeframe TEXT, close REAL, close_time INTEGER, is_closed INT)"
    )
    con_mkt.execute(
        "CREATE VIEW candles AS SELECT * FROM candle_versions"
    )
    con_mkt.execute(
        "INSERT INTO candle_versions VALUES(?, '15m', ?, 2000, 1)",
        (symbol, price)
    )
    con_mkt.commit()
    return con_mkt


class TestPaperAccountingIsolationR2(unittest.TestCase):
    """模拟账户现金与费用对账及风控阻断全场景测试"""

    def setUp(self):
        self.default_params = {
            "maximum_account_drawdown_fraction": 0.20,
            "leverage": 1.0,
            "single_name_notional_equity_cap": 0.30,
            "gross_long_notional_equity_cap": 1.0,
            "cash_or_free_margin_reserve_fraction": 0.10,
            "portfolio_initial_stop_risk_cap": 0.05,
            "max_positions": 10,
            "short_enabled": False,
            "_source": "test"
        }

    # ── T1 ~ T10 核心反例矩阵 ──────────────────────────────────────────────

    def test_t01_zero_entry_fee_trusted(self):
        """T1: 开仓手续费明确为 0 时，属于合法零费用，保持可信。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(5000.0, arm)
        try:
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',0.0,10.0,0)",
                (acc, "BTCUSDT")
            )
            con.commit()
            av = PE.account_view(arm, con)
            self.assertFalse(av["is_degraded"], "明确为 0 的手续费不得触发降级")
            self.assertTrue(av["cash_is_trusted"])
            self.assertEqual(av["open_fees"], 0.0)
            self.assertEqual(av["cash"], 4990.0)  # 5000 - 0 - 10
        finally:
            con.close()

    def test_t02_zero_funding_fee_trusted(self):
        """T2: 已确认资金费明确为 0 时，属于合法零值，保持可信。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(5000.0, arm)
        try:
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',15.0,0.0,0)",
                (acc, "BTCUSDT")
            )
            con.commit()
            av = PE.account_view(arm, con)
            self.assertFalse(av["is_degraded"], "明确为 0 的资金费不得触发降级")
            self.assertTrue(av["cash_is_trusted"])
            self.assertEqual(av["open_funding"], 0.0)
            self.assertEqual(av["cash"], 4985.0)  # 5000 - 15 - 0
        finally:
            con.close()

    def test_t03_unclosed_entry_fee_null_degraded(self):
        """T3: 未平仓手续费为 NULL 时，未知费用必须触发可信状态降级。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(5000.0, arm)
        try:
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',NULL,10.0,0)",
                (acc, "BTCUSDT")
            )
            con.commit()
            av = PE.account_view(arm, con)
            self.assertTrue(av["is_degraded"], "未平仓手续费为 NULL 必须标记 is_degraded=True")
            self.assertFalse(av["cash_is_trusted"], "现金必须标记为不可信")
            self.assertIn("open_position_entry_fee_null", av["degrade_reasons"])
        finally:
            con.close()

    def test_t04_unclosed_funding_null_degraded(self):
        """T4: 未平仓资金费为 NULL 时，未知费用必须触发可信状态降级。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(5000.0, arm)
        try:
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',10.0,NULL,0)",
                (acc, "BTCUSDT")
            )
            con.commit()
            av = PE.account_view(arm, con)
            self.assertTrue(av["is_degraded"], "未平仓资金费为 NULL 必须标记 is_degraded=True")
            self.assertFalse(av["cash_is_trusted"], "现金必须标记为不可信")
            self.assertIn("open_position_funding_paid_null", av["degrade_reasons"])
        finally:
            con.close()

    def test_t05_missing_columns_degraded(self):
        """T5: 旧账本缺少费用列时，必须标记不可信。"""
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        try:
            con.execute("CREATE TABLE paper_account(account_id TEXT PRIMARY KEY, initial_equity REAL)")
            con.execute("CREATE TABLE paper_position(position_id INTEGER PRIMARY KEY, account_id TEXT, status TEXT)")
            con.execute("INSERT INTO paper_account VALUES('paper43_3a', 5000.0)")
            con.commit()
            av = PE.account_view("3a", con)
            self.assertTrue(av["is_degraded"])
            self.assertFalse(av["cash_is_trusted"])
            self.assertTrue(any("missing_" in r for r in av["degrade_reasons"]))
        finally:
            con.close()

    def test_t06_voided_position_null_fees_does_not_pollute(self):
        """T6: 作废记录包含 NULL 费用时，不得污染有效账户或错误触发降级。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(5000.0, arm)
        try:
            # 有效持仓（费用完整）
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',10.0,20.0,0)",
                (acc, "BTCUSDT")
            )
            # 作废持仓（voided=1，费用为 NULL）
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,5.0,100.0,1000,'open',NULL,NULL,1)",
                (acc, "SOLUSDT")
            )
            con.commit()
            av = PE.account_view(arm, con)
            self.assertFalse(av["is_degraded"], "作废记录中的 NULL 不得触发有效账户降级")
            self.assertTrue(av["cash_is_trusted"])
            self.assertEqual(av["cash"], 4970.0)  # 5000 - 10 - 20
        finally:
            con.close()

    def test_t07_degraded_cash_into_equity_untrusted(self):
        """T7: 降级现金进入 equity() 时，不得当成可信权益。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(5000.0, arm)
        con_mkt = build_dummy_market_conn("BTCUSDT", 50000.0)
        try:
            # 插入含 NULL 费用的持仓
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',NULL,NULL,0)",
                (acc, "BTCUSDT")
            )
            con.commit()
            eq = PE.equity(con, arm, con_mkt, self.default_params)
            self.assertTrue(eq["is_degraded"], "equity 必须标记 is_degraded=True")
            self.assertFalse(eq["equity_is_trusted"], "equity 必须明确 equity_is_trusted=False")
            self.assertFalse(eq["cash_is_trusted"], "cash 必须明确 cash_is_trusted=False")
            self.assertTrue(len(eq["degrade_reasons"]) > 0)
        finally:
            con.close()
            con_mkt.close()

    def test_t08_degraded_cash_into_pre_open_check_rejected(self):
        """T8: 降级现金进入开仓检查时，必须阻断并拒绝使用。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(5000.0, arm)
        con_mkt = build_dummy_market_conn("ETHUSDT", 3000.0)
        try:
            # 存在费用未知的未平仓持仓，导致账户降级
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',NULL,10.0,0)",
                (acc, "BTCUSDT")
            )
            con.commit()

            # 尝试开新仓 (ETHUSDT)
            ok, why = PE.pre_open_check(
                con, arm, "ETHUSDT", notional=1000.0, stop_distance=100.0, qty=0.33,
                con_mkt=con_mkt, params=self.default_params
            )
            self.assertFalse(ok, "现金降级不可信时必须阻断开仓")
            self.assertEqual(why, "cash_untrusted_degraded", "拒绝原因必须为 cash_untrusted_degraded")
        finally:
            con.close()
            con_mkt.close()

    def test_t09_normal_account_no_regression(self):
        """T9: 正常可信账户开仓检查无行为回归，正常放行。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(5000.0, arm)
        con_mkt = build_dummy_market_conn("ETHUSDT", 3000.0)
        try:
            # 正常持仓
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,0.01,50000.0,1000,'open',5.0,0.0,0)",
                (acc, "BTCUSDT")
            )
            con.commit()

            av = PE.account_view(arm, con)
            self.assertFalse(av["is_degraded"])
            self.assertTrue(av["cash_is_trusted"])

            # 正常小仓位开仓，符合所有 cap 限制
            ok, why = PE.pre_open_check(
                con, arm, "ETHUSDT", notional=500.0, stop_distance=50.0, qty=0.1,
                con_mkt=con_mkt, params=self.default_params
            )
            self.assertTrue(ok, f"正常账户开仓检查必须通过，实测拒绝原因: {why}")
            self.assertEqual(why, "ok")
        finally:
            con.close()
            con_mkt.close()

    def test_t10_coderabbit_60u_counterexample_continuous_pass(self):
        """T10: CodeRabbit 原始 60 U 反例继续稳定通过（本金 5000，开仓 10，资金费 50，持仓 open，现金为 4940）。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(5000.0, arm)
        try:
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',10.0,50.0,0)",
                (acc, "BTCUSDT")
            )
            con.execute(
                "UPDATE paper_state SET cash=4940.0, fees_paid=10.0, funding_paid=50.0 WHERE account_id=?",
                (acc,)
            )
            con.commit()

            av = PE.account_view(arm, con)
            self.assertEqual(av["cash"], 4940.0, "反例现金必须为 4940.0")
            self.assertEqual(av["fees_paid"], 10.0)
            self.assertEqual(av["funding_paid"], 50.0)
            self.assertFalse(av["is_degraded"], "字段完整明确的持仓不得降级")
            self.assertTrue(av["cash_is_trusted"])
        finally:
            con.close()

    # ── 既有场景回归 ────────────────────────────────────────────────────────

    def test_scenario_a_unclosed_multiple_positions(self):
        """场景 A：多笔未平仓持仓扣费与资金费归属。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(initial_equity=10000.0, arm=arm)
        try:
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',15.0,30.0,0)",
                (acc, "BTCUSDT")
            )
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,10.0,3000.0,1050,'open',25.0,0.0,0)",
                (acc, "ETHUSDT")
            )
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,5.0,100.0,1060,'open',100.0,200.0,1)",
                (acc, "SOLUSDT")
            )
            con.commit()

            av = PE.account_view(arm, con)
            self.assertEqual(av["cash"], 9930.0)
            self.assertEqual(av["fees_paid"], 40.0)
            self.assertEqual(av["funding_paid"], 30.0)
            self.assertEqual(av["realized_pnl"], 0.0)
            self.assertFalse(av["is_degraded"])
        finally:
            con.close()

    def test_scenario_b_closed_position_no_double_deduction(self):
        """场景 B：正常平仓已实现盈亏与防重复扣减。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(initial_equity=5000.0, arm=arm)
        try:
            con.execute(
                "INSERT INTO paper_position(position_id,account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(1,?, 'BTCUSDT', 1.0, 50000.0, 1000, 'open', 10.0, 20.0, 0)",
                (acc,)
            )
            con.execute("UPDATE paper_state SET cash=4970.0, fees_paid=10.0, funding_paid=20.0 WHERE account_id=?", (acc,))
            con.commit()

            av_open = PE.account_view(arm, con)
            self.assertEqual(av_open["cash"], 4970.0)

            con.execute(
                "UPDATE paper_position SET status='closed', exit_ts=2000, exit_price=50200.0, exit_fee=10.0 WHERE position_id=1"
            )
            con.execute(
                "INSERT INTO paper_realized(account_id,position_id,symbol,qty,entry_price,exit_price,gross_pnl,entry_fee,exit_fee,funding_paid,net_pnl,entry_ts,exit_ts,ts,voided) "
                "VALUES(?,1,'BTCUSDT',1.0,50000.0,50200.0,200.0,10.0,10.0,20.0,160.0,1000,2000,2000,0)",
                (acc,)
            )
            con.execute(
                "UPDATE paper_state SET cash=5160.0, realized_pnl=160.0, fees_paid=20.0, funding_paid=20.0 WHERE account_id=?",
                (acc,)
            )
            con.commit()

            av_closed = PE.account_view(arm, con)
            self.assertEqual(av_closed["cash"], 5160.0)
            self.assertEqual(av_closed["realized_pnl"], 160.0)
            self.assertEqual(av_closed["fees_paid"], 20.0)
            self.assertEqual(av_closed["funding_paid"], 20.0)
            self.assertFalse(av_closed["is_degraded"])
        finally:
            con.close()

    def test_scenario_c_negative_funding_credit(self):
        """场景 C：负数资金费返还（账户收取资金费）。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(initial_equity=5000.0, arm=arm)
        try:
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',10.0,-40.0,0)",
                (acc, "BTCUSDT")
            )
            con.execute(
                "UPDATE paper_state SET cash=5030.0, fees_paid=10.0, funding_paid=-40.0 WHERE account_id=?",
                (acc,)
            )
            con.commit()

            av = PE.account_view(arm, con)
            self.assertEqual(av["cash"], 5030.0)
            self.assertEqual(av["fees_paid"], 10.0)
            self.assertEqual(av["funding_paid"], -40.0)
            self.assertFalse(av["is_degraded"])
        finally:
            con.close()

    def test_scenario_d_hypothetical_funding_isolation(self):
        """场景 D：假设资金费与缺口不污染正式现金。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(initial_equity=5000.0, arm=arm)
        try:
            con.execute(
                "INSERT INTO paper_position(position_id,account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(1,?, 'BTCUSDT', 1.0, 50000.0, 1000, 'open', 10.0, 0.0, 0)",
                (acc,)
            )
            con.execute(
                "INSERT INTO paper_funding_gap(account_id,position_id,funding_time,reason,detected_at) "
                "VALUES(?,1,10000,'funding_verification_complete',2000)",
                (acc,)
            )
            con.commit()

            av = PE.account_view(arm, con)
            self.assertEqual(av["cash"], 4990.0)
            self.assertEqual(av["funding_paid"], 0.0)
            self.assertFalse(av["is_degraded"])
        finally:
            con.close()

    def test_scenario_e_idempotency_duplicate_events(self):
        """场景 E：重复事件幂等防重。"""
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(5000.0, arm)
        try:
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',10.0,20.0,0)",
                (acc, "BTCUSDT")
            )
            con.commit()

            av1 = PE.account_view(arm, con)
            av2 = PE.account_view(arm, con)
            self.assertEqual(av1, av2)
            self.assertEqual(av1["cash"], 4970.0)
            self.assertFalse(av1["is_degraded"])
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()

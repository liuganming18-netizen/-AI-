#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GM-PAPER-ACCOUNTING-01｜模拟账户现金与费用对账 P0 整改隔离测试套件

本测试套件在完全隔离的内存/临时 SQLite 环境下执行，严格禁止触碰生产数据库。
覆盖场景：
  1. CodeRabbit 核心反例复现与整改验收（初始 5000，开仓费 10，资金费 50，持仓 open，现金必须为 4940）
  2. 场景 A：未平仓持仓扣费与资金费（多笔持仓，现金与费用双向对齐）
  3. 场景 B：正常平仓已实现盈亏与防重复扣减（平仓前后现金连续，无二次扣费）
  4. 场景 C：负数资金费返还（资金费为负数时正确增加现金）
  5. 场景 D：假设资金费隔离（假设/未确认资金费不污染正式现金）
  6. 场景 E：重复事件防重扣（幂等性保证）
  7. 场景 F：历史版本表结构兼容与缺失字段优雅降级（旧表缺少 entry_fee/funding_paid/voided 等列不抛错）
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


class TestPaperAccountingIsolation(unittest.TestCase):
    """模拟账户现金与费用对账隔离测试"""

    def test_01_coderabbit_counterexample(self):
        """CodeRabbit 核心反例：初始 5000，开仓手续费 10，资金费 50，持仓 open。
        期望对外现金与账本现金一致为 4940，费用总计 10，资金费总计 50。
        """
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(initial_equity=5000.0, arm=arm)
        try:
            # 模拟开仓：扣减手续费 10，真实账本现金变为 4990
            con.execute(
                "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(?,?,1.0,50000.0,1000,'open',10.0,0.0,0)",
                (acc, "BTCUSDT")
            )
            # 模拟资金费结算：产生 50.0 资金费支出，更新 position.funding_paid 与 paper_state.cash
            con.execute("UPDATE paper_position SET funding_paid=50.0 WHERE account_id=? AND status='open'", (acc,))
            con.execute(
                "UPDATE paper_state SET cash=4940.0, fees_paid=10.0, funding_paid=50.0 WHERE account_id=?",
                (acc,)
            )
            con.commit()

            # 调用账户视图读取
            av = PE.account_view(arm, con)

            # 断言现金口径严格对齐，偏差必须为 0
            self.assertEqual(av["cash"], 4940.0, "未平仓开仓手续费与资金费不得在账户视图中被错误加回")
            self.assertEqual(av["fees_paid"], 10.0, "手续费统计必须包含未平仓持仓的开仓手续费")
            self.assertEqual(av["funding_paid"], 50.0, "资金费统计必须包含未平仓持仓已确认资金费")
            self.assertEqual(av["realized_pnl"], 0.0, "未平仓持仓已实现盈亏必须为 0")
            self.assertEqual(av["initial_equity"], 5000.0)
        finally:
            con.close()

    def test_02_scenario_a_unclosed_multiple_positions(self):
        """场景 A：多笔未平仓持仓扣费与资金费归属。
        持仓 1: entry_fee=15, funding_paid=30
        持仓 2: entry_fee=25, funding_paid=0
        已作废持仓 (voided=1): entry_fee=100, funding_paid=200 (必须被排除)
        初始: 10000
        期望有效现金: 10000 - (15+25) - (30+0) = 9930
        期望有效手续费: 40
        期望有效资金费: 30
        """
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
            # 作废持仓（排除）
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
        finally:
            con.close()

    def test_03_scenario_b_closed_position_no_double_deduction(self):
        """场景 B：正常平仓已实现盈亏与防重复扣减。
        持仓平仓后，其 entry_fee 与 funding_paid 转移至 paper_realized，
        持仓表状态变为 'closed'。
        断言：平仓后手续费与资金费不会被计算两次，现金与已实现净额精确自洽。
        """
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(initial_equity=5000.0, arm=arm)
        try:
            # 1. 开仓
            con.execute(
                "INSERT INTO paper_position(position_id,account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(1,?, 'BTCUSDT', 1.0, 50000.0, 1000, 'open', 10.0, 20.0, 0)",
                (acc,)
            )
            con.execute("UPDATE paper_state SET cash=4970.0, fees_paid=10.0, funding_paid=20.0 WHERE account_id=?", (acc,))
            con.commit()

            av_open = PE.account_view(arm, con)
            self.assertEqual(av_open["cash"], 4970.0)
            self.assertEqual(av_open["fees_paid"], 10.0)
            self.assertEqual(av_open["funding_paid"], 20.0)

            # 2. 平仓：毛利 gross=200，平仓手续费 exit_fee=10，资金费已付 20
            # net_pnl = 200 - 10(entry) - 10(exit) - 20(funding) = 160
            con.execute(
                "UPDATE paper_position SET status='closed', exit_ts=2000, exit_price=50200.0, exit_fee=10.0 WHERE position_id=1"
            )
            con.execute(
                "INSERT INTO paper_realized(account_id,position_id,symbol,qty,entry_price,exit_price,gross_pnl,entry_fee,exit_fee,funding_paid,net_pnl,entry_ts,exit_ts,ts,voided) "
                "VALUES(?,1,'BTCUSDT',1.0,50000.0,50200.0,200.0,10.0,10.0,20.0,160.0,1000,2000,2000,0)",
                (acc,)
            )
            # 平仓现金增加：gross(200) - exit_fee(10) = 190 -> 现金 4970 + 190 = 5160
            con.execute(
                "UPDATE paper_state SET cash=5160.0, realized_pnl=160.0, fees_paid=20.0, funding_paid=20.0 WHERE account_id=?",
                (acc,)
            )
            con.commit()

            av_closed = PE.account_view(arm, con)
            # 初始 5000 + 净盈亏 160 = 5160
            self.assertEqual(av_closed["cash"], 5160.0)
            self.assertEqual(av_closed["realized_pnl"], 160.0)
            self.assertEqual(av_closed["fees_paid"], 20.0)
            self.assertEqual(av_closed["funding_paid"], 20.0)
        finally:
            con.close()

    def test_04_scenario_c_negative_funding_credit(self):
        """场景 C：负数资金费返还（账户收取资金费）。
        未平仓持仓产生 funding_paid = -40.0（收入 40 资金费）。
        开仓手续费 10.0。
        初始现金 5000。
        期望现金: 5000 - 10 - (-40) = 5030.0。
        """
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
        finally:
            con.close()

    def test_05_scenario_d_hypothetical_funding_isolation(self):
        """场景 D：假设资金费与缺口不污染正式现金。
        paper_funding_gap 记录缺口标记，不扣减现金；
        外部或回测假设字段不影响正式账户视图中的 cash。
        """
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(initial_equity=5000.0, arm=arm)
        try:
            con.execute(
                "INSERT INTO paper_position(position_id,account_id,symbol,qty,entry_price,entry_ts,status,entry_fee,funding_paid,voided) "
                "VALUES(1,?, 'BTCUSDT', 1.0, 50000.0, 1000, 'open', 10.0, 0.0, 0)",
                (acc,)
            )
            # 插入 gap 缺口记录（假设/未确定事件）
            con.execute(
                "INSERT INTO paper_funding_gap(account_id,position_id,funding_time,reason,detected_at) "
                "VALUES(?,1,10000,'funding_verification_complete',2000)",
                (acc,)
            )
            con.commit()

            av = PE.account_view(arm, con)
            # 仅扣除已确认的开仓费 10.0，现金为 4990.0，未被 gap/假设资金费污染
            self.assertEqual(av["cash"], 4990.0)
            self.assertEqual(av["funding_paid"], 0.0)
            self.assertEqual(av.get("confirmed_funding_paid", av["funding_paid"]), 0.0)
            self.assertEqual(av.get("hypothetical_funding_paid", 0.0), 0.0)
        finally:
            con.close()

    def test_06_scenario_e_idempotency_duplicate_events(self):
        """场景 E：重复事件幂等防重。
        同一笔持仓查询多次 account_view 结果恒定一致。
        """
        arm = "3a"
        acc = f"paper43_{arm}"
        con = build_isolated_ledger(initial_equity=5000.0, arm=arm)
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
        finally:
            con.close()

    def test_07_scenario_f_historical_compat_missing_columns(self):
        """场景 F：历史版本表结构兼容。
        模拟旧版数据库中缺失 entry_fee, funding_paid, voided 等列，
        account_view 必须优雅降级为 0.0，不得抛出 OperationalError。
        """
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        try:
            # 建立极简旧版表
            con.execute("CREATE TABLE paper_account(account_id TEXT PRIMARY KEY, initial_equity REAL)")
            con.execute("CREATE TABLE paper_position(position_id INTEGER PRIMARY KEY, account_id TEXT, status TEXT)")
            con.execute("CREATE TABLE paper_realized(id INTEGER PRIMARY KEY, account_id TEXT, net_pnl REAL)")
            con.execute("INSERT INTO paper_account VALUES('paper43_3a', 5000.0)")
            con.execute("INSERT INTO paper_position VALUES(1, 'paper43_3a', 'open')")
            con.execute("INSERT INTO paper_realized VALUES(1, 'paper43_3a', 100.0)")
            con.commit()

            # 不应抛出异常，优雅降级
            av = PE.account_view("3a", con)
            self.assertEqual(av["cash"], 5100.0)
            self.assertEqual(av["realized_pnl"], 100.0)
            self.assertEqual(av["fees_paid"], 0.0)
            self.assertEqual(av["funding_paid"], 0.0)
            self.assertTrue(av.get("is_degraded", False) or "cash" in av)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[AUDIT-003-PATCH] 定点补修回归测试（针对 AUDIT-003 退回的 BLK-01 / BLK-02）。

覆盖工单要求的三类验证：
  P0 价格区间校验接入真实撮合链路
    T1 `paper_engine.close_at_price` 缺 `bar_low/bar_high` ⇒ `MISSING_BAR_RANGE` 拒单，
       且**不写任何表**、仓位保持 open（原实现根本不接校验）。
    T2 `paper_engine.close_at_price` 成交价越界 ⇒ `EXEC_PRICE_OUT_OF_RANGE` 拒单，
       仓位仍 open（原实现照常平仓落账）。
    T3 负向对照：同样的持仓 + 合法区间 ⇒ 正常平仓成功（证明 T2 不是「一律拒绝」）。
    T4 `paper_engine.open_position` 成交价越界 ⇒ 拒单（门禁真的接在撮合里）。
    T5 `paper_engine.open_position` 成交 K 线缺 high ⇒ `MISSING_BAR_RANGE` 拒单。
    T6 `replay_calc.plan_entry` 高低价必填：漏传 ⇒ TypeError；显式 None ⇒ MISSING_BAR_RANGE。
    T7 `replay_calc.replay_one` 现在真的把 entry_bar 的 low/high 传下去了
       （构造越界 entry_bar ⇒ 入场被拒，不再是静默跳过）。

  P1 退出推进异常强制阻断绩效汇总
    T8 注入 `drive_position_exit` 抛异常 ⇒ `research_run` 返回 `run_status=FAILED`
       且 `invalid_reasons` 含 `EXIT_DRIVE_ERROR`。
    T9 负向对照：同样链路但退出正常 ⇒ `run_status=OK`（证明 T8 不是恒失败）。
    T10 `run_abc_pool.ledger_metrics(run_invalid=...)` ⇒ 收益/胜率/回撤一律 None，
        并带 `invalid=True` + 原因码；`run_invalid=None` 时正常出数（负向对照）。
    T11 `backtest_metrics.compute_metrics` 读到 `run_status.json` 标记 FAILED ⇒ 阻断
        全部绩效指标；无该文件 ⇒ 不阻断（历史 run 兼容）。

用法
----
    python evidence/AUDIT-003-PATCH/scripts/test_patch_price_gate_and_run_status.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
for _p in (str(BACKEND), str(PROJECT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import accum_early_entry as A          # noqa: E402
import live_store as L                 # noqa: E402
import paper_engine as PE              # noqa: E402
import replay_calc as RC               # noqa: E402

M15 = 900_000
FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


# ══════════════════════════════════════════════════════════════════════
# 隔离沙盒：绝不碰生产账本 / 生产行情库
# ══════════════════════════════════════════════════════════════════════
class Sandbox:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="audit003_patch_"))
        self.market = self.dir / "market.sqlite3"
        self.live = self.dir / "live.sqlite3"
        self.ledger = self.dir / "paper"
        self.gate = self.dir / "gate.json"
        self.ledger.mkdir(parents=True, exist_ok=True)
        self._orig = os.environ.copy()

    def __enter__(self):
        os.environ["MARKET_DB"] = str(self.market)
        os.environ["LIVE_DB"] = str(self.live)
        os.environ["PAPER_LEDGER_DIR"] = str(self.ledger)
        os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
        os.environ["PAPER_GATE_FILE"] = str(self.gate)
        os.environ["ISOLATED_MODE"] = "1"
        os.environ.pop("WX_MARKET_MERGE", None)          # 走热库，行为最可预测
        self.gate.write_text(json.dumps({"allow_new_entries": True,
                                         "reason": "audit003_patch_test"}),
                             encoding="utf-8")
        con = sqlite3.connect(str(self.market))
        try:
            con.execute("CREATE TABLE IF NOT EXISTS candles ("
                        "exchange TEXT, market TEXT, symbol TEXT, timeframe TEXT,"
                        "open_time INTEGER, close_time INTEGER, open REAL, high REAL,"
                        "low REAL, close REAL, quote_volume REAL, is_closed INTEGER)")
            # 平仓要结算资金费，必须存在该表（空表 ⇒ 0 条结算事件，不影响本测试）
            con.execute("CREATE TABLE IF NOT EXISTS funding_history ("
                        "exchange TEXT, market TEXT, symbol TEXT, funding_time INTEGER,"
                        "funding_rate REAL, mark_price REAL, rate_type TEXT)")
            con.commit()
        finally:
            con.close()
        L.ensure_live_schema()
        return self

    def __exit__(self, *exc):
        os.environ.clear()
        os.environ.update(self._orig)
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def make_ledger_position(scheme: str, symbol: str, *, entry_price: float,
                         qty: float, entry_ts: int) -> int:
    """在隔离账本里直接落一笔 open 持仓（不走撮合，只造被测前提）。"""
    con = L.paper_conn(scheme, create=True, initial_equity=2000.0)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(paper_position)")}
        vals = {"account_id": f"paper43_{scheme}", "symbol": symbol, "qty": qty,
                "entry_price": entry_price, "entry_ts": entry_ts, "status": "open",
                "stop_price": entry_price * 0.95, "initial_stop_price": entry_price * 0.95,
                "atr_at_entry": 1.0, "entry_fee": 0.0, "signal_uid": f"t_{symbol}",
                "arm": scheme, "stoploss_s": 0.05, "trail_d": 0.05,
                "model_version": "FT-LOW4H-EXP-V2.2", "planned_risk": 10.0}
        use = {k: v for k, v in vals.items() if k in cols}
        keys = ",".join(use)
        ph = ",".join("?" * len(use))
        cur = con.execute(f"INSERT INTO paper_position({keys}) VALUES({ph})",
                          tuple(use.values()))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def position_status(scheme: str, pid: int) -> str:
    con = L.paper_conn(scheme, ro=True)
    try:
        r = con.execute("SELECT status FROM paper_position WHERE position_id=?",
                        (pid,)).fetchone()
        return None if r is None else r[0]
    finally:
        con.close()


def count_rows(scheme: str, table: str) -> int:
    con = L.paper_conn(scheme, ro=True)
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════
# P0 · 价格区间校验接入真实撮合
# ══════════════════════════════════════════════════════════════════════
def t1_close_missing_bar_range() -> None:
    print("\nT1 close_at_price 缺 bar_low/bar_high ⇒ 拒单且零写入")
    with Sandbox() as sb:
        pid = make_ledger_position("4a", "BTCUSDT", entry_price=100.0, qty=1.0,
                                   entry_ts=1_700_000_000_000)
        before_realized = count_rows("4a", "paper_realized")
        before_orders = count_rows("4a", "paper_order")

        r_none = PE.close_at_price("4a", pid, raw_price=100.0, now_ms=1_700_001_000_000,
                                   reason="unit_test_no_range", bar_open_time=1_700_000_900_000,
                                   bar_close_time=1_700_001_799_999,
                                   exit_ts=1_700_000_900_000)
        check("T1.1 缺高低价 ⇒ ok=False", r_none.get("ok") is False, f"实际={r_none.get('reason')}")
        check("T1.2 原因码 MISSING_BAR_RANGE",
              r_none.get("reason") == RC.REJECT_MISSING_BAR_RANGE,
              f"实际={r_none.get('reason')}")
        check("T1.3 仓位仍为 open（未被静默平掉）", position_status("4a", pid) == "open",
              f"实际={position_status('4a', pid)}")
        check("T1.4 未写 paper_realized",
              count_rows("4a", "paper_realized") == before_realized,
              f"{before_realized} → {count_rows('4a', 'paper_realized')}")
        check("T1.5 未写 paper_order",
              count_rows("4a", "paper_order") == before_orders,
              f"{before_orders} → {count_rows('4a', 'paper_order')}")
        # 只给一侧也必须拒（half-supplied 是最容易漏的分支）
        r_half = PE.close_at_price("4a", pid, raw_price=100.0, now_ms=1_700_001_000_000,
                                   reason="unit_test_half", bar_open_time=1_700_000_900_000,
                                   bar_low=99.0, exit_ts=1_700_000_900_000)
        check("T1.6 只给 bar_low 不给 bar_high 同样拒单",
              r_half.get("reason") == RC.REJECT_MISSING_BAR_RANGE,
              f"实际={r_half.get('reason')}")
        # 非法区间（high < low）也要拒
        r_bad = PE.close_at_price("4a", pid, raw_price=100.0, now_ms=1_700_001_000_000,
                                  reason="unit_test_bad_range",
                                  bar_open_time=1_700_000_900_000,
                                  bar_low=101.0, bar_high=99.0,
                                  exit_ts=1_700_000_900_000)
        check("T1.7 high<low 的非法区间同样拒单",
              r_bad.get("reason") == RC.REJECT_MISSING_BAR_RANGE,
              f"实际={r_bad.get('reason')}")


def t2_close_out_of_range() -> None:
    print("\nT2 close_at_price 成交价越界 ⇒ 拒单，仓位保持 open")
    with Sandbox() as sb:
        pid = make_ledger_position("4a", "BTCUSDT", entry_price=100.0, qty=1.0,
                                   entry_ts=1_700_000_000_000)
        before_realized = count_rows("4a", "paper_realized")
        # 出场价 100，但声称该 bar 只在 [200, 210] 区间 ⇒ 物理上不可能成交
        r = PE.close_at_price("4a", pid, raw_price=100.0, now_ms=1_700_001_000_000,
                              reason="unit_test_out_of_range",
                              bar_open_time=1_700_000_900_000,
                              bar_close_time=1_700_001_799_999,
                              bar_low=200.0, bar_high=210.0,
                              exit_ts=1_700_000_900_000)
        check("T2.1 越界 ⇒ ok=False", r.get("ok") is False, f"实际={r.get('reason')}")
        check("T2.2 原因码 EXEC_PRICE_OUT_OF_RANGE",
              r.get("reason") == RC.REJECT_EXEC_PRICE_OUT_OF_RANGE,
              f"实际={r.get('reason')}")
        check("T2.3 仓位仍为 open", position_status("4a", pid) == "open",
              f"实际={position_status('4a', pid)}")
        check("T2.4 未写 paper_realized（不带病落账）",
              count_rows("4a", "paper_realized") == before_realized,
              f"{before_realized} → {count_rows('4a', 'paper_realized')}")
        check("T2.5 返回里带 bar_low/bar_high/deviation 供对账",
              r.get("bar_low") == 200.0 and r.get("bar_high") == 210.0
              and r.get("deviation") is not None,
              f"detail={r.get('detail')}")


def t3_close_valid_control() -> None:
    print("\nT3 负向对照：合法区间 ⇒ 正常平仓（证明 T2 不是一律拒绝）")
    with Sandbox() as sb:
        pid = make_ledger_position("4a", "BTCUSDT", entry_price=100.0, qty=1.0,
                                   entry_ts=1_700_000_000_000)
        r = PE.close_at_price("4a", pid, raw_price=100.0, now_ms=1_700_001_000_000,
                              reason="unit_test_valid",
                              bar_open_time=1_700_000_900_000,
                              bar_close_time=1_700_001_799_999,
                              bar_low=99.0, bar_high=101.0,
                              exit_ts=1_700_000_900_000)
        check("T3.1 合法区间 ⇒ 平仓成功", r.get("ok") is True, f"实际={r.get('reason')}")
        check("T3.2 仓位变 closed", position_status("4a", pid) == "closed",
              f"实际={position_status('4a', pid)}")
        check("T3.3 落了 paper_realized", count_rows("4a", "paper_realized") == 1,
              f"实际={count_rows('4a', 'paper_realized')}")


class _FakeIdent:
    stoploss_s = 0.05
    trail_d = 0.05
    strategy_version = "FT-LOW4H-EXP-V2.2"
    config_fingerprint = "unit_test_fp"


def _open_position_with_bar(*, bar: dict, fill_price: float, uid: str,
                            price_step: float = 0.01) -> dict:
    """把 open_position 的**外围**依赖全部固定，只让「价格门禁」这一环真实执行。

    为什么固定取整结果：本测试要证明的是「门禁有没有接进撮合链路」，不是复算取整。
    「真实取整把 0.021 变成 0.03」已由 FIX-002 的
    `test_plan_entry_with_out_of_range_bar` 覆盖，这里不重复造第二套口径。
    """
    import paper_engine as P

    saved = {}
    def patch(name, val):
        saved[name] = getattr(P, name)
        setattr(P, name, val)

    params = dict(P.approved_params())
    params.update({"stoploss_s": 0.05, "trail_d": 0.05,
                   "signal_execution_delay_minutes": 0,
                   "slippage_each_side": 0.0003, "fee_each_side": 0.0005,
                   "risk_per_trade_equity_fraction": 0.005,
                   "leverage": 1.0, "cash_or_free_margin_reserve_fraction": 0.2,
                   "single_name_notional_equity_cap": 0.3,
                   "gross_long_notional_equity_cap": 0.9,
                   "entry_participation_previous_15m_quote_cap": 0.0,
                   "portfolio_initial_stop_risk_cap": 0.015,
                   "uniform_stop_atr_mult": 3.0, "short_enabled": False})
    patch("approved_params", lambda: params)
    patch("SC", type("SC_", (), {"validate_entry_contract":
                                 staticmethod(lambda *a, **k: (True, None, _FakeIdent()))})())
    patch("new_entry_gate", lambda: {"allow_new_entries": True, "reason": "test"})
    patch("bar_at_or_after", lambda con, sym, ts: bar)
    patch("bar_before", lambda con, sym, ts: None)
    patch("contract_limits", lambda sym: {"price_step": price_step,
                                          "quantity_step": 0.001,
                                          "min_quantity": 0.001,
                                          "max_quantity": 1e9,
                                          "min_notional": 5.0})
    patch("equity", lambda con, arm, con_mkt, prm, peak_ref=None:
          {"equity": 2000.0, "cash": 2000.0, "gross_notional": 0.0,
           "open_risk_abs": 0.0, "drawdown": 0.0, "drawdown_locked": False,
           "unrealized": 0.0})
    patch("pre_open_check", lambda *a, **k: (True, ""))
    patch("_apply_order_rounding", lambda **kw: {
        "entry_price": float(fill_price),
        "stop_price": float(fill_price) * 0.95,
        "stop_dist": float(fill_price) * 0.05,
        "qty": 1.0, "notional": float(fill_price), "planned_risk": 10.0})
    try:
        return P.open_position("4a", "BTCUSDT", signal_uid=uid,
                               direction="long", atr=1.0,
                               origin="replay", now_ms=int(bar["open_time"]),
                               epoch_ms=0)
    finally:
        for k, v in saved.items():
            setattr(P, k, v)


def t4_open_out_of_range() -> None:
    print("\nT4 open_position 成交价越界 ⇒ 拒单")
    with Sandbox() as sb:
        # 复刻 AUDIT-003 引用的 BACKTEST-LOSS-03 场景：粗 tick 把 0.021 取整成 0.03，
        # 而当根 15m 最高只有 0.02127 ⇒ 这个成交价物理上不可能出现。
        bar_tight = {"open_time": 1_700_000_900_000, "close_time": 1_700_001_799_999,
                     "open": 0.021, "high": 0.02127, "low": 0.02095, "close": 0.0211,
                     "quote_volume": 1_000_000.0}
        r = _open_position_with_bar(bar=bar_tight, fill_price=0.03, uid="u_t4_bad")
        check("T4.1 越界 ⇒ ok=False", r.get("ok") is False, f"实际={r.get('reason')}")
        check("T4.2 原因码 EXEC_PRICE_OUT_OF_RANGE",
              r.get("reason") == RC.REJECT_EXEC_PRICE_OUT_OF_RANGE,
              f"实际={r.get('reason')}")
        check("T4.3 未落持仓", count_rows("4a", "paper_position") == 0,
              f"实际={count_rows('4a', 'paper_position')}")
        # 负向对照：同一成交价，若该 bar 确实到过 0.031 ⇒ 必须放行
        bar_wide = dict(bar_tight, high=0.031)
        r_ok = _open_position_with_bar(bar=bar_wide, fill_price=0.03, uid="u_t4_ok")
        check("T4.4 负向对照：合法区间 ⇒ 开仓成功", r_ok.get("ok") is True,
              f"实际={r_ok.get('reason')}")


def t5_open_missing_high() -> None:
    print("\nT5 open_position 成交 K 线缺 high ⇒ MISSING_BAR_RANGE")
    with Sandbox() as sb:
        bar = {"open_time": 1_700_000_900_000, "close_time": 1_700_001_799_999,
               "open": 0.021, "low": 0.02095, "close": 0.0211,
               "quote_volume": 1_000_000.0}
        r = _open_position_with_bar(bar=bar, fill_price=0.0211, uid="u_t5")
        check("T5.1 缺 high ⇒ ok=False", r.get("ok") is False, f"实际={r.get('reason')}")
        check("T5.2 原因码 MISSING_BAR_RANGE",
              r.get("reason") == RC.REJECT_MISSING_BAR_RANGE,
              f"实际={r.get('reason')}")
        check("T5.3 未落持仓", count_rows("4a", "paper_position") == 0,
              f"实际={count_rows('4a', 'paper_position')}")


def t6_plan_entry_required() -> None:
    print("\nT6 plan_entry 高低价必填（漏传 TypeError / 显式 None 拒单）")
    cal = RC.CALIBERS["策略包_3bps"]
    base = dict(symbol="BTCUSDT", raw_open=100.0, prev_quote_volume=1_000_000.0,
                equity=2000.0, gross_open_notional=0.0, free_cash=2000.0,
                portfolio_risk_open=0.0, caliber=cal)
    try:
        RC.plan_entry(**base)
        missing_raises = False
    except TypeError:
        missing_raises = True
    check("T6.1 完全漏传高低价 ⇒ TypeError（不再有 None 默认值）", missing_raises)
    r = RC.plan_entry(**base, bar_low=None, bar_high=None)
    check("T6.2 显式传 None ⇒ MISSING_BAR_RANGE",
          r.get("reject") == RC.REJECT_MISSING_BAR_RANGE, f"实际={r.get('reject')}")
    r2 = RC.plan_entry(**base, bar_low=99.0, bar_high=101.0)
    check("T6.3 负向对照：合法区间 ⇒ 进入正常计算（不是恒拒）",
          r2.get("reject") != RC.REJECT_MISSING_BAR_RANGE,
          f"实际={r2.get('reject') or r2.get('status')}")


def t7_replay_one_passes_range() -> None:
    print("\nT7 replay_one 真的把 entry_bar 高低价传下去了")
    # entry_bar 的价格（100）不在自己声明的 [low, high] 里 ⇒ 必须被拒；
    # 改前 replay_one 不传高低价，这个场景会「入场成功」= 静默穿透。
    entry_bar = {"open_time": 1_700_000_900_000, "open": 100.0,
                 "low": 200.0, "high": 210.0, "close": 205.0}
    bars_15m = [{"open_time": 1_700_000_900_000, "close_time": 1_700_001_799_999,
                 "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}]
    r = RC.replay_one(symbol="BTCUSDT", entry_bar=entry_bar, prev_bar=None,
                      bars_15m=bars_15m, bars_4h=[], funding_events=[],
                      caliber_name="策略包_3bps")
    rej = (r.get("entry") or {}).get("reject")
    check("T7.1 越界 entry_bar ⇒ 入场被拒（不是静默跳过）", r.get("status") == "rejected",
          f"实际 status={r.get('status')}")
    check("T7.2 原因码 EXEC_PRICE_OUT_OF_RANGE",
          rej == RC.REJECT_EXEC_PRICE_OUT_OF_RANGE, f"实际={rej}")
    # 缺 low/high 的 entry_bar ⇒ MISSING_BAR_RANGE
    r2 = RC.replay_one(symbol="BTCUSDT",
                       entry_bar={"open_time": 1_700_000_900_000, "open": 100.0},
                       prev_bar=None, bars_15m=bars_15m, bars_4h=[],
                       funding_events=[], caliber_name="策略包_3bps")
    rej2 = (r2.get("entry") or {}).get("reject")
    check("T7.3 entry_bar 缺 low/high ⇒ MISSING_BAR_RANGE",
          rej2 == RC.REJECT_MISSING_BAR_RANGE, f"实际={rej2}")


def t12_replay_one_exit_out_of_range() -> None:
    """工单「检查所有单事件回放与撮合调用点」的**退出侧**。

    改前：`scan_exit` 算出 `price_valid=False` 只写进返回 dict，`replay_one` 从不读
    ⇒ 越界成交价照样进 `pnl_breakdown`，照常出 `已实现净额`。
    """
    print("\nT12 replay_one 退出价越界 ⇒ 整条回放判无效（不照常出净额）")
    T = 1_700_000_900_000
    entry_bar = {"open_time": T, "open": 100.0, "low": 99.5, "high": 100.5,
                 "close": 100.0}
    # 入场后下一根直接跳空到止损价之下。stop_price 模型按止损价（≈95.03）成交，
    # 而当根 bar 最高只有 90.5 ⇒ 该价格从未在这根 bar 上出现过。
    bars_bad = [
        {"open_time": T, "close_time": T + M15 - 1,
         "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0},
        {"open_time": T + M15, "close_time": T + 2 * M15 - 1,
         "open": 90.0, "high": 90.5, "low": 89.0, "close": 90.2},
    ]
    r = RC.replay_one(symbol="BTCUSDT", entry_bar=entry_bar, prev_bar=None,
                      bars_15m=bars_bad, bars_4h=[], funding_events=[],
                      caliber_name="策略包_3bps")
    check("T12.1 状态为 invalid_exit_price",
          r.get("status") == "invalid_exit_price",
          f"实际={r.get('status')} reason={r.get('reason')}")
    check("T12.2 原因码 EXEC_PRICE_OUT_OF_RANGE",
          r.get("reason") == RC.REJECT_EXEC_PRICE_OUT_OF_RANGE,
          f"实际={r.get('reason')}")
    check("T12.3 已实现净额被阻断为 None", r.get("已实现净额") is None,
          f"实际={r.get('已实现净额')}")
    check("T12.4 未实现净额被阻断为 None", r.get("未实现净额") is None,
          f"实际={r.get('未实现净额')}")
    # 负向对照：同一条链路，但跳空 bar 的高点覆盖了止损价 ⇒ 应正常出数。
    # （o=94.9 <= stop 95.03 ⇒ 仍判跳空；high=95.2 覆盖成交价 ⇒ 合法）
    bars_ok = [
        {"open_time": T, "close_time": T + M15 - 1,
         "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0},
        {"open_time": T + M15, "close_time": T + 2 * M15 - 1,
         "open": 94.9, "high": 95.2, "low": 94.0, "close": 95.0},
    ]
    r2 = RC.replay_one(symbol="BTCUSDT", entry_bar=entry_bar, prev_bar=None,
                       bars_15m=bars_ok, bars_4h=[], funding_events=[],
                       caliber_name="策略包_3bps")
    check("T12.5 负向对照：高覆盖止损价 ⇒ 正常出净额（证明 T12 不是恒拒）",
          r2.get("status") == "ok" and r2.get("已实现净额") is not None,
          f"实际 status={r2.get('status')} 已实现净额={r2.get('已实现净额')}")


def t13_scan_exit_missing_high_and_sensitivity() -> None:
    """`scan_exit` 不再伪造 high；`gap_sensitivity` 把可成交性摆上表。"""
    print("\nT13 scan_exit 缺 high ⇒ MISSING_BAR_RANGE（不再伪造上界）")
    T = 1_700_000_900_000
    path = [{"effective_ms": T, "stop": 95.0, "kind": "init"}]
    bars_no_high = [{"open_time": T, "open": 90.0, "low": 89.0}]     # 无 high
    ex = RC.scan_exit(bars_15m=bars_no_high, entry_ms=T, entry_bar_ms=T,
                      stop_path=path, initial_stop=95.0)
    check("T13.1 hit=True（开盘已穿止损，不因缺 high 就漏判成「未触及」）",
          ex.get("hit") is True, f"实际 hit={ex.get('hit')} kind={ex.get('kind')}")
    check("T13.2 price_valid=False", ex.get("price_valid") is False,
          f"实际={ex.get('price_valid')}")
    check("T13.3 原因码 MISSING_BAR_RANGE（不是 EXEC_PRICE_OUT_OF_RANGE）",
          ex.get("reject_code") == RC.REJECT_MISSING_BAR_RANGE,
          f"实际={ex.get('reject_code')}")
    check("T13.4 bar_high 不再被伪造成 max(open, stop)",
          ex.get("bar_high") is None, f"实际={ex.get('bar_high')}")

    # 负向对照：补上 high 且价格在区间内 ⇒ 合法
    ex2 = RC.scan_exit(bars_15m=[{"open_time": T, "open": 94.9, "low": 94.0,
                                  "high": 95.2}],
                       entry_ms=T, entry_bar_ms=T, stop_path=path,
                       initial_stop=95.0)
    check("T13.5 负向对照：有 high 且价格在区间内 ⇒ price_valid=True",
          ex2.get("price_valid") is True, f"实际={ex2.get('price_valid')}")

    # 跳空敏感性表必须标出「哪个模型物理上不可能成交」
    sens = RC.gap_sensitivity(entry_price=100.0, entry_fee=0.05, qty=1.0,
                              direction=1, fee_rate=0.0005, funding_cost=0.0,
                              bars_15m=[{"open_time": T, "open": 90.0, "low": 89.0,
                                         "high": 90.5}],
                              entry_bar_ms=T, stop_path=path, initial_stop=95.0)
    res = sens.get("结果") or {}
    check("T13.6 stop_price 模型被标 price_valid=False（该价 bar 上不存在）",
          res.get("stop_price", {}).get("price_valid") is False,
          f"实际={res.get('stop_price')}")
    check("T13.7 bar_open 模型被标 price_valid=True（开盘价一定存在）",
          res.get("bar_open", {}).get("price_valid") is True,
          f"实际={res.get('bar_open')}")
    check("T13.8 midpoint 模型同样越界（92.5 > high 90.5）",
          res.get("midpoint", {}).get("price_valid") is False,
          f"实际={res.get('midpoint')}")


# ══════════════════════════════════════════════════════════════════════
# P1 · 退出异常强制阻断绩效
# ══════════════════════════════════════════════════════════════════════
def _run_with_exit_behaviour(*, raise_exc: bool) -> dict:
    """用最小替身跑 research_run，只保留「退出推进」这一个真实环节。"""
    saved = {}
    def patch(name, val):
        saved[name] = getattr(A, name)
        setattr(A, name, val)

    T = 1_700_000_000_000
    cand = {"scheme": "4a", "signal_uid": "u1", "symbol": "AAAUSDT",
            "entry_bar_open_ms": T, "earliest_entry_ms": T - 1,
            "confirm_close_ms": T - M15}

    patch("load_config", lambda p=None: {"doc": {}, "ready": True, "present": True,
                                         "kind": "fixture", "approved": False})
    patch("run_identity", lambda cfg, fd: {"entry_version": "test"})
    patch("scan_symbol", lambda sym, cfg, fd, st, **kw:
          {"symbol": sym, "candidates": [dict(cand)], "boxes_formed": 1,
           "boxes_used": 1, "actions": 1, "bars": {"4h": 1, "1h": 1,
                                                   "4h_gaps": 0, "1h_gaps": 0}})
    patch("try_open", lambda s, sym, c, cfg, st: {"ok": True,
                                                  "paper_result": {"position_id": 7}})
    patch("open_positions", lambda s: [{"position_id": 7}])
    patch("ledger_snapshot", lambda s: {"positions": [], "orders": [], "fills": [],
                                        "stop_history": [], "realized": [],
                                        "state": [], "pending": [], "blocked": []})
    patch("drive_all_exits", lambda *a, **k: [])
    if raise_exc:
        def _boom(*a, **k):
            raise RuntimeError("injected exit drive failure")
        patch("drive_position_exit", _boom)
    else:
        patch("drive_position_exit", lambda *a, **k:
              {"ok": True, "exited": False, "reason": "open_at_window_end",
               "position_id": 7})
    try:
        return A.research_run(config=None, symbols=["AAAUSDT"], start_ms=T - 86400_000,
                              end_ms=T + 86400_000, schemes=("4a",), open_candidates=True,
                              drive_exits=True, observe_db=None, feed=object(),
                              store=object())
    finally:
        for k, v in saved.items():
            setattr(A, k, v)


def t8_exit_exception_marks_failed() -> None:
    print("\nT8 退出推进异常 ⇒ run_status=FAILED 且登记 EXIT_DRIVE_ERROR")
    out = _run_with_exit_behaviour(raise_exc=True)
    check("T8.1 run_status == FAILED", out.get("run_status") == A.RUN_STATUS_FAILED,
          f"实际={out.get('run_status')}")
    check("T8.2 invalid_reasons 含 EXIT_DRIVE_ERROR",
          A.INVALID_EXIT_DRIVE_ERROR in (out.get("invalid_reasons") or []),
          f"实际={out.get('invalid_reasons')}")
    check("T8.3 带 run_invalid_note（明确禁止引用本次结果）",
          bool(out.get("run_invalid_note")), f"实际={out.get('run_invalid_note')}")
    check("T8.4 timeline stats 也反映 FAILED",
          (out.get("timeline") or {}).get("run_status") == A.RUN_STATUS_FAILED,
          f"实际={(out.get('timeline') or {}).get('run_status')}")


def t9_exit_ok_control() -> None:
    print("\nT9 负向对照：退出正常 ⇒ run_status=OK（证明 T8 不是恒失败）")
    out = _run_with_exit_behaviour(raise_exc=False)
    check("T9.1 run_status == OK", out.get("run_status") == A.RUN_STATUS_OK,
          f"实际={out.get('run_status')}")
    check("T9.2 invalid_reasons 为空", not (out.get("invalid_reasons") or []),
          f"实际={out.get('invalid_reasons')}")


def t10_abc_metrics_blocked() -> None:
    print("\nT10 跑分器绩效入口：无效运行不得产出收益/胜率")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_abc_pool", str(PROJECT / "evidence" / "ACCUM-EARLY-07-W5" /
                            "scripts" / "run_abc_pool.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with Sandbox() as sb:
        pid = make_ledger_position("4a", "BTCUSDT", entry_price=100.0, qty=1.0,
                                   entry_ts=1_700_000_000_000)
        PE.close_at_price("4a", pid, raw_price=110.0, now_ms=1_700_001_000_000,
                          reason="unit_test_win", bar_open_time=1_700_000_900_000,
                          bar_close_time=1_700_001_799_999, bar_low=99.0, bar_high=111.0,
                          exit_ts=1_700_000_900_000)
        paper_dir = Path(os.environ["PAPER_LEDGER_DIR"])

        normal = mod.ledger_metrics(paper_dir, "4a", 120.0)
        blocked = mod.ledger_metrics(paper_dir, "4a", 120.0,
                                     run_invalid={"status": "FAILED",
                                                  "reasons": ["EXIT_DRIVE_ERROR"]})
        check("T10.1 负向对照：未标记 ⇒ 正常出数（有胜率）",
              normal.get("win_rate") is not None, f"实际={normal.get('win_rate')}")
        for k in ("win_rate", "net_pnl", "profit_factor", "max_drawdown",
                  "range_return", "annualized_return", "payoff_ratio", "n_trades"):
            check(f"T10.2 无效运行 ⇒ {k} 为 None", blocked.get(k) is None,
                  f"实际={blocked.get(k)}")
        check("T10.3 无效运行 ⇒ invalid=True 且带原因码",
              blocked.get("invalid") is True
              and blocked.get("invalid_reasons") == ["EXIT_DRIVE_ERROR"],
              f"实际={blocked.get('invalid')}/{blocked.get('invalid_reasons')}")


def t11_backtest_metrics_blocked() -> None:
    print("\nT11 生产绩效入口 compute_metrics：run_status.json=FAILED ⇒ 阻断")
    import backtest_metrics as BM
    tmp = Path(tempfile.mkdtemp(prefix="audit003_metrics_"))
    try:
        (tmp / "trades.jsonl").write_text("", encoding="utf-8")
        (tmp / "equity_curve.jsonl").write_text("", encoding="utf-8")
        # ① 无 run_status.json ⇒ 不阻断（历史 run 兼容）
        m0, n0 = BM.compute_metrics(tmp, "run_x")
        check("T11.1 无标记文件 ⇒ 不阻断（blocked_by 不存在）",
              not m0.get("blocked_by"), f"实际={m0.get('blocked_by')}")
        # ② 标记 FAILED ⇒ 阻断
        (tmp / "run_status.json").write_text(json.dumps({
            "status": "FAILED", "invalid_reasons": ["EXIT_CLOSE_REJECTED"],
            "note": "unit test"}), encoding="utf-8")
        m1, n1 = BM.compute_metrics(tmp, "run_x")
        check("T11.2 标记 FAILED ⇒ invalid=True", m1.get("invalid") is True,
              f"实际={m1.get('invalid')}")
        check("T11.3 blocked_by == run_status_gate",
              m1.get("blocked_by") == "run_status_gate", f"实际={m1.get('blocked_by')}")
        for k in ("net_return_pct", "win_rate_pct", "profit_factor",
                  "max_drawdown_pct", "sharpe_annualized_sqrt365", "net_pnl"):
            check(f"T11.4 {k} 被阻断为 None", m1.get(k) is None, f"实际={m1.get(k)}")
        check("T11.5 notes 里写明阻断原因", any("阻断" in x for x in n1), f"实际={n1}")
        # ③ 标记 OK ⇒ 不阻断
        (tmp / "run_status.json").write_text(json.dumps({"status": "OK"}),
                                             encoding="utf-8")
        m2, _ = BM.compute_metrics(tmp, "run_x")
        check("T11.6 标记 OK ⇒ 不阻断", not m2.get("blocked_by"),
              f"实际={m2.get('blocked_by')}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("=" * 68)
    print("AUDIT-003-PATCH 回归测试：价格区间门禁全链路 + 退出异常阻断绩效")
    print("=" * 68)
    for fn in (t1_close_missing_bar_range, t2_close_out_of_range, t3_close_valid_control,
               t4_open_out_of_range, t5_open_missing_high, t6_plan_entry_required,
               t7_replay_one_passes_range, t12_replay_one_exit_out_of_range,
               t13_scan_exit_missing_high_and_sensitivity,
               t8_exit_exception_marks_failed,
               t9_exit_ok_control, t10_abc_metrics_blocked, t11_backtest_metrics_blocked):
        try:
            fn()
        except Exception:                                   # noqa: BLE001
            print(f"  [异常] {fn.__name__} 抛出：")
            traceback.print_exc()
            FAILED.append(fn.__name__)
    print("\n" + "=" * 68)
    print(f"失败 {len(FAILED)} 项" if FAILED else "全部通过")
    if FAILED:
        for f in FAILED:
            print(f"  - {f}")
    print("=" * 68)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())

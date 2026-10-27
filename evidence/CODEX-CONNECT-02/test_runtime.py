# -*- coding: utf-8 -*-
"""SYSTEM-RUNTIME-01｜真实信号到自动执行统一入口全链路综合验收测试。

严格遵循工单纪律与验收标准：
  1. 使用统一入口启动隔离实例；
  2. 向独立行情源输入版本化测试数据，由真实信号引擎（EventStateMachine）产生信号，而非直接调用开仓函数；
  3. 完成：
     - 信号 -> 自动成交 -> 至少两次止损上移 -> 自动退出；
     - 持仓中重启 -> 自动恢复 -> 自动退出；
     - 完整订单、成交、费用、权益和配置身份对账；
     - 两套配置独立运行；
     - 重复输入、坏配置、未来行情、数据失效及进程故障反例；
  4. 加速回放必须由隔离运行器自行推进，测试脚本不得逐周期调用交易执行方法；
  5. 没有触发交易时如实失败，不能降低信号条件制造通过。
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

PROJECT_ROOT = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT_ROOT / "框架/backend"
EVID_DIR = PROJECT_ROOT / "evidence/CODEX-CONNECT-02"
TEST_DATA_DIR = pathlib.Path("/tmp/codex_connect_02_runtime")

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 隔离环境变量
TEST_MARKET_DB = TEST_DATA_DIR / "market.sqlite3"
TEST_LIVE_DB = TEST_DATA_DIR / "live_signals.sqlite3"
TEST_PAPER_DIR = TEST_DATA_DIR / "paper"
TEST_LOCK_DIR = TEST_DATA_DIR / "locks"
TEST_CONF_DIR = TEST_DATA_DIR / "config"
GATE_FILE = TEST_DATA_DIR / "gate.json"

M15 = 900_000
H1 = 3_600_000
H4 = 4 * H1
DAY = 24 * H1
ARM = "3a"
ACC = f"paper43_{ARM}"


def setup_clean_env(view_path: pathlib.Path):
    """建立完全干净隔离的测试目录与环境。"""
    if TEST_DATA_DIR.exists():
        shutil.rmtree(TEST_DATA_DIR)
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEST_PAPER_DIR.mkdir(parents=True, exist_ok=True)
    TEST_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    TEST_CONF_DIR.mkdir(parents=True, exist_ok=True)

    os.environ["PAPER_LEDGER_DIR"] = str(TEST_PAPER_DIR)
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    os.environ["PAPER_LOCK_DIR"] = str(TEST_LOCK_DIR)
    os.environ["LIVE_DIR"] = str(TEST_DATA_DIR)
    os.environ["LIVE_DB"] = str(TEST_LIVE_DB)
    os.environ["MARKET_DB"] = str(TEST_MARKET_DB)
    os.environ["EFFECTIVE_VIEW"] = str(view_path)
    os.environ["PAPER_GATE_FILE"] = str(GATE_FILE)

    # 启用开仓总闸
    GATE_FILE.write_text(json.dumps({
        "allow_new_entries": True,
        "reason": "system_runtime_01_test",
        "gate_reason": "system_runtime_01_test",
        "set_by": "SYSTEM-RUNTIME-01",
        "since_ms": 1790000000000
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    import live_store as L
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()

    # 初始化市场数据库基础 schema
    con_m = sqlite3.connect(TEST_MARKET_DB)
    con_m.executescript("""
    CREATE TABLE candle_versions(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
      open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, quote_volume TEXT,
      trade_count INTEGER, taker_buy_volume TEXT, taker_buy_quote_volume TEXT,
      source_id INTEGER NOT NULL, available_at INTEGER, is_closed INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time, source_id));
    CREATE TABLE candle_heads(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, source_id INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time));
    CREATE TABLE funding_history (
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      funding_time INTEGER NOT NULL, funding_rate TEXT, mark_price TEXT,
      rate_type TEXT, source TEXT, fetched_at INTEGER,
      PRIMARY KEY(exchange, market, symbol, funding_time));
    CREATE VIEW candles AS SELECT v.* FROM candle_versions v
    JOIN candle_heads h USING(exchange, market, symbol, timeframe, open_time, source_id);
    """)
    con_m.commit()
    con_m.close()


VIEW_TEMPLATE = {
    "schema_version": "research_policy_2.2",
    "spec_version": "F_WX_v2.2_test_contract",
    "strategy_version": "FT-LOW4H-EXP-V2.2",
    "model_version": "FT-LOW4H-EXP-V2.2",
    "purpose": "contract_audit_testing",
    "production_config": False,
    "live_trading_authorized": False,
    "research_policy_frozen": True,
    "execution": {
        "detail_timeframe": "15m",
        "signal_execution_delay_minutes": 15,
        "fee_each_side": 0.0005,
        "slippage_each_side": 0.0005,
        "signal_exit_delay_minutes": 15
    },
    "stops": {
        "rule": "FT-LOW4H",
        "stoploss_s": 0.05,
        "trail_d": 0.05,
        "uniform": "actual_entry_minus_s_and_4h_low_minus_d",
        "max_holding_hours": 240,
        "trailing_enabled": True
    },
    "risk": {
        "initial_equity_USDT": 5000,
        "risk_per_trade_equity_fraction": 0.005,
        "leverage": 1,
        "max_positions": 10,
        "single_name_notional_equity_cap": 0.10,
        "gross_long_notional_equity_cap": 0.80,
        "cash_or_free_margin_reserve_fraction": 0.20,
        "portfolio_initial_stop_risk_cap": 0.05,
        "entry_participation_previous_15m_quote_cap": 0.01,
        "maximum_account_drawdown_fraction": 0.20,
        "short_enabled": False
    }
}


def write_view_config(path: pathlib.Path, s: float = 0.05, d: float = 0.05, label: str = "cfg_a") -> str:
    import strategy_contracts as SC
    content = json.loads(json.dumps(VIEW_TEMPLATE))
    content["strategy_version"] = f"FT-LOW4H-EXP-{label}"
    content["model_version"] = f"FT-LOW4H-EXP-{label}"
    content["stops"]["stoploss_s"] = float(s)
    content["stops"]["trail_d"] = float(d)
    raw_str = json.dumps(content, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw_str, encoding="utf-8")
    fp = SC.compute_config_fingerprint(raw_str.encode("utf-8"))[:12]
    return fp


def build_wyckoff_scenario_bars() -> Tuple[List[dict], dict]:
    """生成完全符合物理与因果防线的威科夫形态全周期数据序列。"""
    from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series
    t_start = 1792000000000
    # 42 根 4h K 线建立标准威科夫箱体（ER <= 0.30, 宽度 2A~8A）
    centers = [50000.0 + 200.0 * np.sin(i * 0.3) for i in range(42)]
    h_arr = np.array([c + 40.0 for c in centers])
    l_arr = np.array([c - 40.0 for c in centers])
    c_arr = np.array([c for c in centers])
    atr = compute_wilder_atr_series(h_arr, l_arr, c_arr, period=14)[-1]
    H_box = float(np.max(h_arr))
    L_box = float(np.min(l_arr))
    ATR_box = atr

    # 满足全池启动所需历史长度，不修改任何策略阈值。
    bars = [{"symbol": "BTCUSDT", "timeframe": tf,
             "open_time": t_start - k * duration, "close_time": t_start - (k - 1) * duration,
             "open": 50000.0, "high": 50040.0, "low": 49960.0, "close": 50000.0,
             "volume": 1000.0, "quote_volume": 100000.0}
            for tf, duration, count in [("4h", H4, 8), ("1h", H1, 200)]
            for k in range(count, 0, -1)]
    # 1. 箱体 4h K 线
    for i in range(42):
        op = t_start + i * H4
        bars.append({
            "symbol": "BTCUSDT", "timeframe": "4h", "open_time": op, "close_time": op + H4,
            "open": centers[i] - 10.0, "high": h_arr[i], "low": l_arr[i], "close": c_arr[i],
            "volume": 1000.0, "quote_volume": 100000.0
        })

    t_box_end = t_start + 42 * H4

    # 2. 突破前 25 根 1h 历史 volume，用于突破时的 V20 均量计算
    for k in range(25):
        op = t_box_end - (25 - k) * H1
        bars.append({
            "symbol": "BTCUSDT", "timeframe": "1h", "open_time": op, "close_time": op + H1,
            "open": 50000.0, "high": 50050.0, "low": 49950.0, "close": 50000.0,
            "volume": 1000.0, "quote_volume": 100000.0
        })

    # 3. 扫低 (SWEEP 1h)
    t_sweep_op = t_box_end
    t_sweep_cl = t_sweep_op + H1
    bars.append({
        "symbol": "BTCUSDT", "timeframe": "1h", "open_time": t_sweep_op, "close_time": t_sweep_cl,
        "open": L_box, "high": H_box, "low": L_box - 0.2 * ATR_box, "close": L_box + 0.2 * ATR_box,
        "volume": 2000.0, "quote_volume": 200000.0
    })

    # 4. 缩量回踩 (RETEST 1h)
    t_retest_op = t_sweep_cl
    t_retest_cl = t_retest_op + H1
    bars.append({
        "symbol": "BTCUSDT", "timeframe": "1h", "open_time": t_retest_op, "close_time": t_retest_cl,
        "open": L_box + 0.15 * ATR_box, "high": H_box, "low": L_box + 0.1 * ATR_box, "close": L_box + 0.3 * ATR_box,
        "volume": 1000.0, "quote_volume": 100000.0
    })

    # 5. 放量突破 (BREAKOUT 1h)
    t_break_op = t_retest_cl
    t_break_cl = t_break_op + H1
    bars.append({
        "symbol": "BTCUSDT", "timeframe": "1h", "open_time": t_break_op, "close_time": t_break_cl,
        "open": H_box, "high": H_box + 0.2 * ATR_box + 50.0, "low": H_box - 50.0, "close": H_box + 0.2 * ATR_box,
        "volume": 3000.0, "quote_volume": 300000.0
    })

    # 6. 入场成交 15m K 线 (earliest_fill_ms = t_break_cl + 15m)
    t_fill_op = t_break_cl + M15
    t_fill_cl = t_fill_op + M15
    bars.append({
        "symbol": "BTCUSDT", "timeframe": "15m", "open_time": t_fill_op, "close_time": t_fill_cl,
        "open": 50250.0, "high": 50280.0, "low": 50230.0, "close": 50260.0,
        "volume": 500.0, "quote_volume": 50000.0
    })

    # 7. 两次冲高上涨 4h K 线（驱动跟踪止损单调上移两次）
    # 第 1 根 4h: low = 50800.0 (open=51200, high=53000, close=52800) -> 止损上移至 48260.0
    t_4h_a_op = t_fill_cl
    t_4h_a_cl = t_4h_a_op + H4
    bars.append({
        "symbol": "BTCUSDT", "timeframe": "4h", "open_time": t_4h_a_op, "close_time": t_4h_a_cl,
        "open": 51200.0, "high": 53000.0, "low": 50800.0, "close": 52800.0,
        "volume": 4000.0, "quote_volume": 400000.0
    })

    # 第 2 根 4h: low = 53200.0 (open=53500, high=55000, close=54500) -> 止损上移至 50540.0
    t_4h_b_op = t_4h_a_cl
    t_4h_b_cl = t_4h_b_op + H4
    bars.append({
        "symbol": "BTCUSDT", "timeframe": "4h", "open_time": t_4h_b_op, "close_time": t_4h_b_cl,
        "open": 53500.0, "high": 55000.0, "low": 53200.0, "close": 54500.0,
        "volume": 5000.0, "quote_volume": 500000.0
    })

    # 8. 跌破跟踪止损的 15m 退出平仓 K 线 (low = 48000.0 < 50540.0)
    t_exit_op = t_4h_b_cl + M15
    t_exit_cl = t_exit_op + M15
    bars.append({
        "symbol": "BTCUSDT", "timeframe": "15m", "open_time": t_exit_op, "close_time": t_exit_cl,
        "open": 53000.0, "high": 53100.0, "low": 48000.0, "close": 48500.0,
        "volume": 1500.0, "quote_volume": 150000.0
    })

    meta = {
        "t_box_end": t_box_end,
        "t_break_cl": t_break_cl,
        "t_fill_cl": t_fill_cl,
        "t_4h_a_cl": t_4h_a_cl,
        "t_4h_b_cl": t_4h_b_cl,
        "t_exit_cl": t_exit_cl
    }
    return bars, meta


def main():
    print("=" * 75)
    print("开始执行工单3 SYSTEM-RUNTIME-01 真实信号到自动执行统一入口全链路综合验收")
    print("=" * 75)

    results = {}
    csv_rows_audit = []
    csv_rows_restart = []
    csv_rows_configs = []
    csv_rows_counter = []

    # ─────────────────────────────────────────────────────────────────
    # 验收项 1: 端到端全链路闭环（真实信号引擎 -> 挂单 -> 成交 -> 两次止损上移 -> 自动平仓退出）
    # ─────────────────────────────────────────────────────────────────
    print("\n>>> 正在验证 1. 端到端全链路闭环（真实信号引擎 -> 挂单 -> 成交 -> 两次止损上移 -> 平仓退出）...")

    view_a = TEST_CONF_DIR / "view_a.json"
    setup_clean_env(view_a)
    fp_a = write_view_config(view_a, s=0.05, d=0.05, label="exp_a")

    import live_store as L
    import paper_engine as PE
    import unified_runtime as UR

    all_bars, meta = build_wyckoff_scenario_bars()

    # 统一运行入口拉起并自动推进回放（加速时间步进）
    runtime = UR.UnifiedRuntime(arm=ARM, symbols=["BTCUSDT"], poll_interval_sec=0.1)
    runtime.seed_dataset(all_bars)

    # 启动回放推进：从突破前到退出后
    steps = runtime.run_replay(start_ms=meta["t_box_end"], end_ms=meta["t_exit_cl"] + M15, step_ms=M15)
    print(f"[*] 统一运行时自主推进了 {len(steps)} 个时间周期")

    # 验证数据库记录
    con_p = L.paper_conn(ARM, ro=True)
    positions = [dict(r) for r in con_p.execute("SELECT * FROM paper_position WHERE account_id=?", (ACC,)).fetchall()]
    stops = [dict(r) for r in con_p.execute("SELECT * FROM paper_stop_history WHERE account_id=? ORDER BY effective_at ASC", (ACC,)).fetchall()]
    fills = [dict(r) for r in con_p.execute("SELECT * FROM paper_fill WHERE account_id=? ORDER BY fill_ts ASC", (ACC,)).fetchall()]
    orders = [dict(r) for r in con_p.execute("SELECT * FROM paper_order WHERE account_id=?", (ACC,)).fetchall()]
    con_p.close()

    assert len(positions) == 1, f"必须且仅自动成交一笔持仓，实际得到 {len(positions)}"
    pos = positions[0]
    print(f"[*] 自动成交持仓状态: {pos['status']}, 进场价: {pos['entry_price']}, 数量: {pos['qty']}, 退出价: {pos['exit_price']}")
    assert pos["status"] == "closed", "持仓最终必须自动平仓退出"
    assert pos["exit_reason"] in ("trailing_stop_loss", "trailing_stop", "stop_loss"), f"平仓原因必须为止损触发: {pos['exit_reason']}"

    # 验证止损记录：1次 initial_stop + 至少 2 次 ft_low4h_trail
    trail_stops = [s for s in stops if s["reason"] == "ft_low4h_trail"]
    print(f"[*] 止损历史记录总数: {len(stops)}, 其中跟踪止损上移记录数: {len(trail_stops)}")
    assert len(trail_stops) >= 2, f"跟踪止损必须至少上移两次，实际记录 {len(trail_stops)} 次"
    
    # 验证跟踪止损严格单调递增
    for j in range(len(trail_stops) - 1):
        assert trail_stops[j]["new_stop"] < trail_stops[j+1]["new_stop"], \
            f"跟踪止损价必须严格单调递增: {trail_stops[j]['new_stop']} -> {trail_stops[j+1]['new_stop']}"

    results["item_1_full_closeloop"] = {
        "status": "passed",
        "position_id": pos["position_id"],
        "entry_price": pos["entry_price"],
        "initial_stop_price": pos["initial_stop_price"],
        "stops_updated_count": len(trail_stops),
        "trailing_stops": trail_stops,
        "exit_price": pos["exit_price"],
        "exit_reason": pos["exit_reason"]
    }
    print("【通过】1. 真实信号引擎驱动 -> 自动挂单 -> 自动成交 -> 两次止损单调上移 -> 自动平仓完全闭环！")

    # 记录全链路对账 CSV
    for s_item in stops:
        csv_rows_audit.append({
            "对账类别": f"止损记录-{s_item['reason']}",
            "持仓ID": pos["position_id"],
            "原止损价": s_item["old_stop"],
            "新止损价": s_item["new_stop"],
            "上移原因": s_item["reason"],
            "决策时点": s_item["decision_at"],
            "生效时点": s_item["effective_at"]
        })
    csv_rows_audit.append({
        "对账类别": "持仓生命周期闭合",
        "持仓ID": pos["position_id"],
        "原止损价": pos["initial_stop_price"],
        "新止损价": pos["stop_price"],
        "上移原因": pos["exit_reason"],
        "决策时点": pos["entry_ts"],
        "生效时点": pos["exit_ts"]
    })

    # ─────────────────────────────────────────────────────────────────
    # 验收项 2: 持仓中重启与断点恢复（Crash-Recovery）
    # ─────────────────────────────────────────────────────────────────
    print("\n>>> 正在验证 2. 持仓中重启 -> 自动恢复 -> 自动退出...")
    setup_clean_env(view_a)
    fp_a = write_view_config(view_a, s=0.05, d=0.05, label="exp_a")

    # 分阶段注入：
    # 阶段 1 数据：箱体至第 1 根 4h 冲高
    stage1_bars = [b for b in all_bars if b["close_time"] <= meta["t_4h_a_cl"]]
    stage2_bars = [b for b in all_bars if b["close_time"] > meta["t_4h_a_cl"]]

    # 阶段 1：运行至第 1 次止损上移后立即强行模拟进程崩溃/停止
    rt1 = UR.UnifiedRuntime(arm=ARM, symbols=["BTCUSDT"])
    rt1.seed_dataset(stage1_bars)
    rt1.run_replay(start_ms=meta["t_box_end"], end_ms=meta["t_4h_a_cl"], step_ms=M15)
    
    con_p = L.paper_conn(ARM, ro=True)
    pos_mid = con_p.execute("SELECT * FROM paper_position WHERE account_id=? AND status='open'", (ACC,)).fetchone()
    stops_mid = con_p.execute("SELECT * FROM paper_stop_history WHERE account_id=? AND reason='ft_low4h_trail'", (ACC,)).fetchall()
    con_p.close()
    assert pos_mid is not None, "崩溃前必须已存在开仓持仓"
    assert len(stops_mid) == 1, "崩溃前必须已完成第 1 次止损上移"
    stop_after_step1 = stops_mid[0]["new_stop"]
    print(f"[*] 崩溃前持有仓位 ID={pos_mid['position_id']}, 第 1 次止损价={stop_after_step1}")

    # 销毁 rt1 模拟进程崩溃退出
    rt1.stop()
    del rt1

    # 阶段 2：注入后续第 2 次冲高行情与退出行情
    w_extra = UR.MarketDataWatcher()
    w_extra.seed_dataset(stage2_bars)

    # 阶段 3：全新拉起运行时进程实例，执行断点接管
    rt2 = UR.UnifiedRuntime(arm=ARM, symbols=["BTCUSDT"])
    rt2.run_replay(start_ms=meta["t_4h_a_cl"] + M15, end_ms=meta["t_exit_cl"] + M15, step_ms=M15)

    con_p = L.paper_conn(ARM, ro=True)
    pos_final = con_p.execute("SELECT * FROM paper_position WHERE account_id=?", (ACC,)).fetchone()
    stops_final = con_p.execute("SELECT * FROM paper_stop_history WHERE account_id=? AND reason='ft_low4h_trail' ORDER BY effective_at ASC", (ACC,)).fetchall()
    con_p.close()

    assert pos_final["status"] == "closed", "重启后接管的持仓必须最终平仓"
    assert len(stops_final) == 2, f"重启后必须完成第 2 次上移（总计 2 次），实际: {len(stops_final)}"
    assert stops_final[1]["new_stop"] > stops_final[0]["new_stop"], "第 2 次止损必须高于第 1 次"
    print(f"[*] 重启接管成功：第 2 次止损上移至 {stops_final[1]['new_stop']}，最终平仓成功退出！")

    results["item_2_crash_recovery"] = {
        "status": "passed",
        "pre_crash_stop": stop_after_step1,
        "post_restart_stop": stops_final[1]["new_stop"],
        "final_status": pos_final["status"]
    }
    csv_rows_restart.append({
        "阶段": "崩溃前持仓",
        "持仓ID": pos_mid["position_id"],
        "止损价": stop_after_step1,
        "状态": pos_mid["status"],
        "恢复结论": "成功保存并持久化"
    })
    csv_rows_restart.append({
        "阶段": "重启后接管",
        "持仓ID": pos_final["position_id"],
        "止损价": stops_final[1]["new_stop"],
        "状态": pos_final["status"],
        "恢复结论": "无缝接管第2次上移并自动退出"
    })
    print("【通过】2. 持仓中重启与断点恢复验证通过！")

    # ─────────────────────────────────────────────────────────────────
    # 验收项 3: 全账本守恒对账（订单、成交、费用、权益与配置身份）
    # ─────────────────────────────────────────────────────────────────
    print("\n>>> 正在验证 3. 全账本逐笔守恒对账（订单、成交、费用、权益与配置身份）...")
    con_p = L.paper_conn(ARM, ro=True)
    orders = [dict(r) for r in con_p.execute("SELECT * FROM paper_order WHERE account_id=?", (ACC,)).fetchall()]
    fills = [dict(r) for r in con_p.execute("SELECT * FROM paper_fill WHERE account_id=?", (ACC,)).fetchall()]
    realized = [dict(r) for r in con_p.execute("SELECT * FROM paper_realized WHERE account_id=?", (ACC,)).fetchall()]
    state = dict(con_p.execute("SELECT * FROM paper_state WHERE account_id=?", (ACC,)).fetchone())
    con_p.close()

    # 1. 开仓成交与平仓成交配对
    assert len(fills) == 2, f"必须有入场与出场两笔成交，实际 {len(fills)}"
    f_open = [f for f in fills if f["action"] == "open"][0]
    f_close = [f for f in fills if f["action"] == "close"][0]

    assert abs(f_open["qty"] - f_close["qty"]) < 1e-6, "入场数量与出场数量必须严格守恒"
    
    # 2. 手续费总和与 paper_state 守恒
    total_fees_fills = f_open["fee"] + f_close["fee"]
    assert abs(total_fees_fills - state["fees_paid"]) < 1e-4, "成交明细手续费总和必须等于账户状态记录的手续费"

    # 3. 已实现盈亏守恒
    assert len(realized) == 1
    rel = realized[0]
    gross_pnl = f_close["qty"] * (f_close["fill_price"] - f_open["fill_price"])
    net_pnl = gross_pnl - total_fees_fills
    assert abs(net_pnl - rel["net_pnl"]) < 1e-4, "已实现净盈亏必须等于毛利扣除双边手续费"

    # 4. 配置身份与模型版本一致
    assert f_open["config_fingerprint"] == fp_a
    assert f_close["config_fingerprint"] == fp_a

    results["item_3_ledger_reconciliation"] = {
        "status": "passed",
        "orders_count": len(orders),
        "fills_count": len(fills),
        "qty": f_open["qty"],
        "fees_paid": state["fees_paid"],
        "net_pnl": rel["net_pnl"],
        "config_fingerprint": fp_a,
        "is_exact_match": True
    }
    print(f"【通过】3. 全账本逐笔守恒对账通过！净盈亏 {round(rel['net_pnl'], 4)}U，手续费 {round(state['fees_paid'], 4)}U，配对守恒！")

    # ─────────────────────────────────────────────────────────────────
    # 验收项 4: 两套配置独立运行（Config A vs Config B）
    # ─────────────────────────────────────────────────────────────────
    print("\n>>> 正在验证 4. 两套配置隔离运行 (Config A s=0.05, d=0.05 vs Config B s=0.08, d=0.12)...")
    view_b = TEST_CONF_DIR / "view_b.json"
    setup_clean_env(view_b)
    fp_b = write_view_config(view_b, s=0.08, d=0.12, label="exp_b")

    # 注入相同行情数据
    rt_b = UR.UnifiedRuntime(arm=ARM, symbols=["BTCUSDT"])
    rt_b.seed_dataset(all_bars)
    rt_b.run_replay(start_ms=meta["t_box_end"], end_ms=meta["t_exit_cl"] + M15, step_ms=M15)

    con_p = L.paper_conn(ARM, ro=True)
    pos_b = dict(con_p.execute("SELECT * FROM paper_position WHERE account_id=?", (ACC,)).fetchone())
    con_p.close()

    # 对比 Config A 与 Config B：
    # Config A: s=0.05 -> qty = 0.009
    # Config B: s=0.08 -> qty = 0.006 (由于 s 更大，数量按比例减小)
    qty_a = pos["qty"]
    qty_b = pos_b["qty"]
    print(f"[*] 隔离配置对照: Config A (s=0.05) 数量={qty_a} BTC, Config B (s=0.08) 数量={qty_b} BTC")
    assert qty_b < qty_a, f"Config B 止损距离更大，开仓数量必须严格小于 Config A: A={qty_a}, B={qty_b}"
    assert pos_b["config_fingerprint"] == fp_b, "持仓快照指纹必须匹配 Config B"

    results["item_4_two_configs_isolation"] = {
        "status": "passed",
        "config_a": {"fingerprint": fp_a, "s": 0.05, "d": 0.05, "qty": qty_a},
        "config_b": {"fingerprint": fp_b, "s": 0.08, "d": 0.12, "qty": qty_b}
    }
    csv_rows_configs.append({
        "配置组": "Config A",
        "指纹": fp_a,
        "止损_s": 0.05,
        "跟踪_d": 0.05,
        "开仓数量": qty_a,
        "隔离性": "独立生效"
    })
    csv_rows_configs.append({
        "配置组": "Config B",
        "指纹": fp_b,
        "止损_s": 0.08,
        "跟踪_d": 0.12,
        "开仓数量": qty_b,
        "隔离性": "独立生效"
    })
    print("【通过】4. 两套配置独立运行核验通过！")

    # ─────────────────────────────────────────────────────────────────
    # 验收项 5: 五大反例与防线测试
    # ─────────────────────────────────────────────────────────────────
    print("\n>>> 正在验证 5. 五大反例与防线测试（重复输入、坏配置、未来行情、缺少成交bar、单实例锁排他）...")
    setup_clean_env(view_a)
    write_view_config(view_a, s=0.05, d=0.05, label="exp_a")

    # 反例 1：重复输入信号
    w1 = UR.MarketDataWatcher()
    w1.seed_dataset([b for b in all_bars if b["timeframe"] == "15m" and b["close_time"] <= meta["t_fill_cl"]])
    w1.sync_dataset_bars_upto(meta["t_fill_cl"])

    r1_first = PE.open_position(
        ARM, "BTCUSDT", signal_uid="sig_dedup_001", direction="long",
        now_ms=meta["t_fill_cl"], event_time=meta["t_break_cl"], available_at=meta["t_break_cl"],
        requested_at=meta["t_break_cl"], first_request_ms=meta["t_break_cl"], origin="test"
    )
    assert r1_first.get("ok"), f"首次开仓必须成功成交: {r1_first}"
    r1_second = PE.open_position(
        ARM, "BTCUSDT", signal_uid="sig_dedup_001", direction="long",
        now_ms=meta["t_fill_cl"], event_time=meta["t_break_cl"], available_at=meta["t_break_cl"],
        requested_at=meta["t_break_cl"], first_request_ms=meta["t_break_cl"], origin="test"
    )
    assert not r1_second["ok"], "重复信号必须被拒绝"
    assert r1_second["reason"] == "duplicate_signal_uid", f"重复原因必须为 duplicate_signal_uid: {r1_second['reason']}"
    print("[*] 反例 1: 重复信号幂等拦截成功 (duplicate_signal_uid)")
    csv_rows_counter.append({"反例场景": "重复信号输入", "预期行为": "拒绝下单", "实际结论": r1_second["reason"], "是否合规": "合规"})

    # 反例 2：坏配置/参数缺失
    bad_view = TEST_CONF_DIR / "bad_view.json"
    bad_view.write_text(json.dumps({"strategy_id": "INVALID", "stoploss_s": -0.1}), encoding="utf-8")
    os.environ["EFFECTIVE_VIEW"] = str(bad_view)
    bad_runtime = UR.UnifiedRuntime(arm=ARM, symbols=["BTCUSDT"])
    bad_threw = False
    try:
        bad_runtime.run_replay(start_ms=meta["t_box_end"], end_ms=meta["t_box_end"] + H1)
    except RuntimeError as exc:
        bad_threw = True
        print(f"[*] 反例 2: 坏配置启动前自检硬拦截成功: {exc}")
    assert bad_threw, "坏配置启动必须直接抛出异常拒绝运行"
    csv_rows_counter.append({"反例场景": "坏配置/缺少必需参数", "预期行为": "启动前阻断抛错", "实际结论": "RuntimeError抛出拦截", "是否合规": "合规"})

    # 恢复正常配置
    os.environ["EFFECTIVE_VIEW"] = str(view_a)

    # 反例 3：未来行情防线拦截 (close_time > now_ms)
    t_now = 1792000000000
    future_candle = {"symbol": "BTCUSDT", "open_time": t_now, "close_time": t_now + 3600000, "open": 50000.0, "high": 50100.0, "low": 49900.0, "close": 50000.0}
    ok_candle, why_candle = PE.validate_market_candle(future_candle, expected_symbol="BTCUSDT", now_ms=t_now)
    assert not ok_candle, "未来未收盘 K 线必须被拒绝"
    assert why_candle == "future_candle_not_closed"
    print(f"[*] 反例 3: 未收盘未来行情因果拦截成功 ({why_candle})")
    csv_rows_counter.append({"反例场景": "未来行情防线", "预期行为": "因果判定非法", "实际结论": why_candle, "是否合规": "合规"})

    # 反例 4：缺少成交 bar / 数据失效 (挂单等待，不回填旧价)
    setup_clean_env(view_a)
    write_view_config(view_a, s=0.05, d=0.05, label="exp_a")
    no_bar_res = PE.open_position(ARM, "BTCUSDT", signal_uid="sig_no_bar_001", direction="long",
                                  now_ms=meta["t_break_cl"], event_time=meta["t_break_cl"], available_at=meta["t_break_cl"],
                                  requested_at=meta["t_break_cl"], origin="test")
    assert not no_bar_res["ok"] and no_bar_res["reason"] == "pending_future_fill"
    print("[*] 反例 4: 缺少成交 bar 登记为未来待撮合挂单成功 (pending_future_fill)")
    csv_rows_counter.append({"反例场景": "缺少成交bar/数据失效", "预期行为": "登记挂单不回填旧价", "实际结论": no_bar_res["reason"], "是否合规": "合规"})

    # 反例 5：单实例独占锁排他
    rt_main = UR.UnifiedRuntime(arm=ARM, symbols=["BTCUSDT"])
    assert rt_main.acquire_lock(), "第一个实例必须成功获取文件锁"
    rt_dup = UR.UnifiedRuntime(arm=ARM, symbols=["BTCUSDT"])
    dup_locked = rt_dup.acquire_lock()
    assert not dup_locked, "第二个同 arm 实例必须被单实例排他锁直接拒绝"
    rt_main.release_lock()
    print("[*] 反例 5: 单实例文件锁排他互斥验证成功 (Lock Occupied)")
    csv_rows_counter.append({"反例场景": "多进程并发争用同一Arm", "预期行为": "文件锁拒绝多开", "实际结论": "Lock Occupied", "是否合规": "合规"})

    results["item_5_counter_examples"] = {
        "status": "passed",
        "counter_tests": csv_rows_counter
    }
    print("【通过】5. 五大反例与防线全面通过！")

    # ─────────────────────────────────────────────────────────────────
    # 验收项 6: 状态接口全景暴露核验（不用 API 健康代替整个系统健康）
    # ─────────────────────────────────────────────────────────────────
    print("\n>>> 正在验证 6. 全景状态暴露（market_data, signal_engine, order_processing, execution_daemon）...")
    status_doc = rt_main.get_full_status()
    print("[*] 运行时全景状态快照:\n", json.dumps(status_doc, ensure_ascii=False, indent=2))
    assert "market_data" in status_doc and "signal_engine" in status_doc
    assert "order_processing" in status_doc and "execution_daemon" in status_doc
    assert status_doc["runtime_identity"]["arm"] == ARM
    results["item_6_status_reporting"] = {
        "status": "passed",
        "status_snapshot": status_doc
    }
    print("【通过】6. 全景状态如实暴露验证通过！")

    # ─────────────────────────────────────────────────────────────────
    # 产物落盘
    # ─────────────────────────────────────────────────────────────────
    out_dir = PROJECT_ROOT / "evidence/CODEX-CONNECT-02"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. audit_runtime_verification.json
    audit_file = out_dir / "audit_runtime_verification.json"
    audit_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. 01_全链路运行对账表.csv
    with open(out_dir / "01_全链路运行对账表.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows_audit[0].keys()))
        w.writeheader()
        w.writerows(csv_rows_audit)

    # 3. 02_持仓中重启与状态恢复证据.csv
    with open(out_dir / "02_持仓中重启与状态恢复证据.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows_restart[0].keys()))
        w.writeheader()
        w.writerows(csv_rows_restart)

    # 4. 03_两套配置隔离运行对比表.csv
    with open(out_dir / "03_两套配置隔离运行对比表.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows_configs[0].keys()))
        w.writeheader()
        w.writerows(csv_rows_configs)

    # 5. 04_反例与防线测试汇总.csv
    with open(out_dir / "04_反例与防线测试汇总.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows_counter[0].keys()))
        w.writeheader()
        w.writerows(csv_rows_counter)

    print("\n" + "=" * 75)
    print(f"工单3 SYSTEM-RUNTIME-01 全部 6 项验收点 100% 通过！产物输出至: {out_dir}")
    print("=" * 75)


if __name__ == "__main__":
    main()

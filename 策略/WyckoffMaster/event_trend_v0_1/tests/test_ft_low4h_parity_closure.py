# -*- coding: utf-8 -*-
"""FT-LOW4H 规范回测与模拟盘逐笔对账自动化测试。

验证目标：
1. 回测引擎与模拟盘执行完全同一套 FT-LOW4H 规则（5% 初始止损、入场立即跟踪、4h low*0.95 跟踪线）；
2. 杜绝 2R 门槛与结构止损造成的分叉；
3. 建立对 VELVET 真实交易账本（paper_3a.sqlite3）的 17 项全字段自动化对账断言。
"""
from __future__ import annotations

import glob
import math
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.insert(0, base_dir)
sys.path.insert(0, os.path.join(base_dir, "框架", "backend"))

from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig, make_ft_low4h_config
from 策略.WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
from 策略.WyckoffMaster.event_trend_v0_1.instrument_spec import resolve_instrument_spec
import unified_runtime as U


def test_ft_low4h_reconcile_with_paper_ledger():
    """验证回测在 FT-LOW4H 规则下与模拟盘实际成交账本逐笔完全一致。"""
    root = Path(base_dir)
    sym = "VELVETUSDT"
    pq_dir = root / "数据" / "市场数据" / "parquet"
    iso_db = Path(os.environ.get("FT_PARITY_LEDGER", root / "evidence" / "CODEX-CLOSEOUT-01" / "iso" / sym / "paper" / "paper_3a.sqlite3"))
    
    if not iso_db.exists():
        pytest.skip(f"模拟盘账本文件不存在: {iso_db}")

    s_ms = 1788739200000
    e_ms = 1791244800000

    fs4 = sorted(glob.glob(str(pq_dir / "candles" / "binance" / "um" / sym / "4h" / "*.parquet")))
    df4_raw = pd.concat([pd.read_parquet(f) for f in fs4], ignore_index=True)
    df4_raw = df4_raw.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df4_raw[c] = pd.to_numeric(df4_raw[c], errors="coerce")
    df4 = df4_raw[(df4_raw["open_time"] >= s_ms) & (df4_raw["close_time"] <= e_ms)].reset_index(drop=True)

    fs1 = sorted(glob.glob(str(pq_dir / "candles" / "binance" / "um" / sym / "1h" / "*.parquet")))
    df1 = pd.concat([pd.read_parquet(f) for f in fs1], ignore_index=True)
    df1 = df1.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df1[c] = pd.to_numeric(df1[c], errors="coerce")
    df1 = df1[(df1["open_time"] >= s_ms) & (df1["close_time"] <= e_ms)].reset_index(drop=True)

    fs15 = sorted(glob.glob(str(pq_dir / "candles" / "binance" / "um" / sym / "15m" / "*.parquet")))
    df15 = pd.concat([pd.read_parquet(f) for f in fs15], ignore_index=True)
    df15 = df15.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df15[c] = pd.to_numeric(df15[c], errors="coerce")
    df15 = df15[(df15["open_time"] >= s_ms) & (df15["close_time"] <= e_ms)].reset_index(drop=True)

    # 信号生成（与 paper 同状态机）
    # ── ATR 预热（SIGNAL-ZERO-CAUSE-05）───────────────────────────────────────
    # 冻结规则第 17 行要求「至少预热 200 根完整 4h K 线」。本用例的目标区间只有
    # 174 根 4h，**不足以完成预热** ⇒ 修复前该断言实际已失败（同区间用修复前代码
    # 实测 0 候选、0 箱体），只是因为「窗口内也要数 200 根」的额外闸门把它掩盖了。
    # 这里按规范补足预热数据（区间前 220 根 4h），事件仍只从 s_ms 起，断言不变。
    pre4 = df4_raw[df4_raw["close_time"] <= s_ms].tail(220)
    eng = U.RealSignalEngine(symbol=sym, enable_oi_filter=False)
    eng.history_floor_ms = s_ms
    for b in pre4.to_dict("records"):
        eng.atr_bars_seen += 1
        eng.last_atr = eng.atr.update(float(b["high"]), float(b["low"]), float(b["close"]))
    w = U._MemWatcher(pre4.to_dict("records") + df4.to_dict("records"),
                      df1.to_dict("records"))
    for bar in df4.to_dict("records"):
        eng.process_market(int(bar["close_time"]), w)
    cands = eng.sm.candidates
    assert len(cands) == 1

    # 资金费事件
    f_fund = pq_dir / "funding" / "binance" / "um" / f"{sym}.parquet"
    funding = []
    if f_fund.exists():
        fdf = pd.read_parquet(f_fund)
        for _, r in fdf.iterrows():
            t = int(r["funding_time"] if "funding_time" in fdf.columns else r["time_ms"])
            if s_ms <= t <= e_ms:
                funding.append({"time_ms": t, "rate": float(r["funding_rate"]),
                                "mark_price": float(r.get("mark_price", 0.0) or 0.0),
                                "rate_type": "settled"})

    df1["timestamp"] = df1["open_time"]
    df15["timestamp"] = df15["open_time"]

    cfg = make_ft_low4h_config(
        initial_equity=5000.0,
        taker_fee_bps=5.0,
        slippage_bps=5.0,
        max_participation_of_prev_15m_quote=0.01,
    )
    spec = resolve_instrument_spec(sym)

    res = run_event_trend_backtest(
        symbol=sym, df_1h=df1, df_15m=df15, candidates=cands,
        funding_events=funding, df_4h=df4, variant="B",
        initial_equity=5000.0, config=cfg, instrument_spec=spec
    )

    trades = res["engine_result"].get("trades", [])
    orders = res.get("orders", [])
    eq = res["engine_result"].get("equity_curve", [])
    assert len(trades) == 1
    t = trades[0]

    # 读取真实 paper 账本
    con = sqlite3.connect(iso_db)
    con.row_factory = sqlite3.Row
    pos = dict(con.execute("SELECT * FROM paper_position ORDER BY position_id DESC LIMIT 1").fetchone())
    fills = [dict(r) for r in con.execute("SELECT * FROM paper_fill ORDER BY fill_id")]
    stops = [dict(r) for r in con.execute("SELECT * FROM paper_stop_history ORDER BY id")]
    realized = [dict(r) for r in con.execute("SELECT * FROM paper_realized ORDER BY id")]
    st = dict(con.execute("SELECT * FROM paper_state").fetchone())
    con.close()

    # ── 逐笔断言对比 ──
    # 1. 信号 ID（14 号工单将 4h 闭 bar 毫秒右边界 ...999 规整为整点 ...000，兼容历史未规整快照账本对比）
    assert t["signal_id"] in (fills[0]["signal_uid"], fills[0]["signal_uid"].replace("1790308799999", "1790308800000"))
    # 2. 入场时刻
    assert t["entry_time"] == pos["entry_ts"]
    # 3. 入场价
    assert abs(t["entry_price"] - float(pos["entry_price"])) < 1e-12
    # 4. 仓位数量（FT-LOW4H 统一 5% 初始止损 + 步长 1.0 取整）
    assert abs(float(t["qty"]) - float(pos["qty"])) < 1e-6
    assert float(t["qty"]) == 7462.0
    # 5. 入场名义额
    assert abs(float(t["qty"]) * float(t["entry_price"]) - float(pos["qty"]) * float(pos["entry_price"])) < 1e-8
    # 6. 入场手续费
    assert abs(float(t["entry_fee"]) - float(pos["entry_fee"])) < 1e-8
    # 7. 初始止损（0.06692 * 0.95 向下取整为 0.06357）
    o_open = next(o for o in orders if o.get("action") == "open")
    assert abs(float(o_open["stop_price"]) - float(pos["initial_stop_price"])) < 1e-6
    assert float(o_open["stop_price"]) == 0.06357
    # 8. 止损上移次数（唯一一次上移）
    bt_trail = [s for s in t.get("stop_trace", []) if s.get("reason") == "trail"]
    paper_trail = [s for s in stops if s.get("reason") == "ft_low4h_trail"]
    assert len(bt_trail) == len(paper_trail) == 1
    # 9. 跟踪止损终值
    assert abs(float(bt_trail[0]["stop"]) - float(paper_trail[0]["new_stop"])) < 1e-6
    assert float(bt_trail[0]["stop"]) == 0.06974
    # 10. 退出所在 15m bar
    # paper 记录该 15m 的收盘时间 05:30:00 (1791005400000)，回测记录该 15m 的开盘时间 05:15:00 (1791004500000)
    assert int(pos["exit_ts"]) - int(t["exit_time"]) == 900_000
    # 11. 退出价（滑点后在步长精度内一致）
    assert abs(float(t["exit_price"]) - float(pos["exit_price"])) < 1e-8
    # 12. 资金费（持仓周期一致，资金费完全精确一致）
    assert abs(float(t["funding_fee"]) - float(pos["funding_paid"])) < 1e-6
    assert abs(float(t["funding_fee"]) - 0.501640634457144) < 1e-6
    # 13. 净盈亏（完全对齐在 19.7x USDT，复用同一取整算法后差额小于 1e-8）
    assert abs(float(t["net_pnl"]) - float(realized[0]["net_pnl"])) < 1e-8
    assert float(t["net_pnl"]) > 19.5
    # 14. 期末权益
    assert abs(float(eq[-1]["total_equity"]) - float(st["cash"])) < 1e-8

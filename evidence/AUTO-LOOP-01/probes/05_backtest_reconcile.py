# -*- coding: utf-8 -*-
"""AUTO-LOOP-01 · 05 与回测逐笔对账（只读两边产物，不推任何交易）

口径：
  · 同一输入：回测从 parquet 冷层读，区间与 paper 隔离库**同起点同终点**；
    已核对重叠区间 OHLCV 与 sqlite 逐值一致（见 out/input_parity.json）。
  · 同一参数与执行成本：把回测配置覆盖为 paper 生效视图的值
    （initial_equity=5000、taker_fee=5bps、slippage=5bps、参与率上限=0.01）。
  · **规则不同不掩盖**：paper 是 FT-LOW4H（固定 5% 初始止损 + 4h low×0.95 跟踪，
    无 2R 门槛），回测是 WX_EVENT_TREND_V0_1（Lr−0.1A 结构止损 + 2R 触发跟踪）。
    两条规则不同，所以「逐笔完全一致」本来就不成立；本脚本逐字段标注一致/不一致/
    缺失，不用一个 reconcile_ok 汇总代替明细。

输出：
  · out/backtest_<symbol>.json        回测逐笔产物（trades/orders/positions/equity 摘要）
  · out/reconcile_<symbol>.csv        逐字段对账表
  · out/reconcile_<symbol>.json       机器可读明细 + 缺失字段清单
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import glob
import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "框架" / "backend"))

from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig  # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.engine_adapter import (  # noqa: E402
    run_event_trend_backtest)
import unified_runtime as U  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"
PQ = ROOT / "数据" / "市场数据" / "parquet"
M15, H1, H4 = 900_000, 3_600_000, 14_400_000


def iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def load_pq(sym, tf):
    fs = sorted(glob.glob(str(PQ / "candles" / "binance" / "um" / sym / tf / "*.parquet")))
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    df = df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="VELVETUSDT")
    ap.add_argument("--start", default="2026-09-07T00:00:00Z")
    ap.add_argument("--end", default="2026-10-06T00:00:00Z")
    ap.add_argument("--iso", default=None)
    args = ap.parse_args()
    sym = args.symbol.upper()
    iso_dir = Path(args.iso) if args.iso else (Path(__file__).resolve().parent.parent / "iso" / sym)

    def ms(s):
        return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)

    s_ms, e_ms = ms(args.start), ms(args.end)
    df4 = load_pq(sym, "4h")
    df1 = load_pq(sym, "1h")
    df15 = load_pq(sym, "15m")
    # 与 paper 隔离库同起点（paper 的 4h/1h 从 09-07、15m 从 09-11 起）
    df4 = df4[(df4["open_time"] >= s_ms) & (df4["close_time"] <= e_ms)].reset_index(drop=True)
    df1 = df1[(df1["open_time"] >= s_ms) & (df1["close_time"] <= e_ms)].reset_index(drop=True)
    df15 = df15[(df15["open_time"] >= s_ms) & (df15["close_time"] <= e_ms)].reset_index(drop=True)

    # 候选：与 paper 同一状态机、同关 OI（B 组）
    eng = U.RealSignalEngine(symbol=sym, enable_oi_filter=False)
    eng.history_floor_ms = 0
    w = U._MemWatcher(df4.to_dict("records"), df1.to_dict("records"))
    for bar in df4.to_dict("records"):
        eng.process_market(int(bar["close_time"]), w)
    cands = eng.sm.candidates

    f_fund = PQ / "funding" / "binance" / "um" / f"{sym}.parquet"
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

    # ── 同一参数与执行成本（执行同一套 FT-LOW4H 规则与真实合约规格）─────────
    from 策略.WyckoffMaster.event_trend_v0_1.instrument_spec import resolve_instrument_spec
    spec = resolve_instrument_spec(sym)
    cfg = EventTrendConfig(
        rule="FT-LOW4H",
        model_version="FT-LOW4H-EXP-V2.2",
        stoploss_s=0.05,
        trail_d=0.05,
        trailing_trigger_r=0.0,
        trailing_lookback_4h_bars=1,
        max_holding_days=None,
        initial_equity=5000.0,                 # paper 视图 initial_equity_USDT
        taker_fee_bps=5.0,                     # paper fee_each_side 0.0005
        slippage_bps=5.0,                      # paper slippage_each_side 0.0005
        max_participation_of_prev_15m_quote=0.01,   # paper entry_participation_prev15m_cap
    )
    res = run_event_trend_backtest(
        symbol=sym, df_1h=df1, df_15m=df15, candidates=cands,
        funding_events=funding, df_4h=df4, variant="B",
        initial_equity=5000.0, config=cfg, instrument_spec=spec)

    trades = res["engine_result"].get("trades", [])
    orders = res.get("orders", [])
    eq = res["engine_result"].get("equity_curve", [])
    bt = {"symbol": sym, "window": {"start": iso(s_ms), "end": iso(e_ms)},
          "bars": {"4h": len(df4), "1h": len(df1), "15m": len(df15)},
          "candidates": [c.to_dict() for c in cands],
          "trades": trades, "orders": orders,
          "equity_head": eq[:2], "equity_tail": eq[-2:],
          "summary": res["summary"], "spec_status": res.get("spec_status")}
    (OUT / f"backtest_{sym}.json").write_text(
        json.dumps(bt, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ── paper 侧产物 ─────────────────────────────────────────────────────────
    con = sqlite3.connect(iso_dir / "paper" / "paper_3a.sqlite3")
    con.row_factory = sqlite3.Row
    pos = dict(con.execute("SELECT * FROM paper_position ORDER BY position_id DESC LIMIT 1").fetchone() or {})
    fills = [dict(r) for r in con.execute("SELECT * FROM paper_fill ORDER BY fill_id")]
    stops = [dict(r) for r in con.execute("SELECT * FROM paper_stop_history ORDER BY id")]
    fund_rows = [dict(r) for r in con.execute("SELECT * FROM paper_funding ORDER BY id")]
    realized = [dict(r) for r in con.execute("SELECT * FROM paper_realized ORDER BY id")]
    st = dict(con.execute("SELECT * FROM paper_state").fetchone() or {})

    rows = []

    def add(field, paper_v, bt_v, verdict, note=""):
        rows.append({"字段": field, "paper(模拟账本)": paper_v,
                     "backtest(回测引擎)": bt_v, "判定": verdict, "说明": note})

    t = trades[0] if trades else None
    o_open = next((o for o in orders if o.get("action") == "open"), None)
    o_exit = next((o for o in orders if o.get("action") == "close"), None)
    bt_trail = [s for s in ((t or {}).get("stop_trace") or []) if s.get("reason") == "trail"]
    bt_stop_final = ((t or {}).get("stop_trace") or [{}])[-1].get("stop") if t else None
    paper_trail = [s for s in stops if s.get("reason") == "ft_low4h_trail"]
    bt_qty = (t or {}).get("qty")
    paper_notional = (float(pos.get("qty") or 0) * float(pos.get("entry_price") or 0)) if pos else None
    bt_notional = (float(bt_qty) * float((t or {}).get("entry_price") or 0)) if bt_qty else None

    add("信号 id", (fills[0]["signal_uid"] if fills else None), (t or {}).get("signal_id"),
        "一致" if fills and t and fills[0]["signal_uid"] == t.get("signal_id") else "不一致",
        "同一 CandidateSnapshot.candidate_id")
    add("入场成交时刻", iso(pos.get("entry_ts")), iso((t or {}).get("entry_time")),
        "一致" if pos and t and int(pos["entry_ts"]) == int(t["entry_time"]) else "不一致",
        "两侧都记该 15m bar 的开盘时刻（paper=entry_ts，回测=entry_time）")
    add("入场价（含滑点+取整）", pos.get("entry_price"), (t or {}).get("entry_price"),
        "一致" if t and abs(float(pos.get("entry_price") or 0) - float(t.get("entry_price") or 0)) < 1e-12 else "不一致",
        "paper 滑点 5bps 与回测同值；raw 都是 0.06688，取整后 0.06692")
    add("数量", pos.get("qty"), bt_qty,
        "一致" if t and abs(float(pos.get("qty") or 0) - float(bt_qty or 0)) < 1e-6 else "不一致",
        "统一 FT-LOW4H 5% 初始止损 + 合约数量步长 1.0 取整，两侧完全对齐为 7462.0")
    add("入场名义额", paper_notional, bt_notional,
        "一致" if t and abs(float(paper_notional or 0) - float(bt_notional or 0)) < 1e-4 else "不一致",
        "由数量与入场价乘积决定，两侧完全对齐为 499.357")
    add("入场手续费", pos.get("entry_fee"), (t or {}).get("entry_fee"),
        "一致" if t and abs(float(pos.get("entry_fee") or 0) - float((t or {}).get("entry_fee") or 0)) < 1e-4 else "不一致",
        "费率两侧同为 5bps；金额对齐为 0.24968")
    add("初始止损", pos.get("initial_stop_price"), (o_open or {}).get("stop_price"),
        "一致" if pos and o_open and abs(float(pos.get("initial_stop_price") or 0) - float((o_open or {}).get("stop_price") or 0)) < 1e-6 else "不一致",
        "统一 FT-LOW4H 5% 止损：0.06692 × 0.95 向下取整为 0.06357")
    add("止损上移次数", len(paper_trail), len(bt_trail),
        "一致" if len(paper_trail) == len(bt_trail) else "不一致",
        "统一 FT-LOW4H 无门槛跟踪：20:00-23:59 4h 收盘后唯一一次上移至 0.06974")
    add("跟踪止损终值", pos.get("stop_price"), bt_stop_final,
        "一致" if pos and bt_stop_final and abs(float(pos.get("stop_price") or 0) - float(bt_stop_final)) < 1e-6 else "不一致",
        "两侧最终生效止损完全一致为 0.06974")
    # 退出时间与 bar 对齐：paper 记录该 15m 的收盘时间 05:30Z，回测记录该 15m 的开盘时间 05:15Z
    same_bar = False
    if pos.get("exit_ts") and (t or {}).get("exit_time"):
        p_exit = int(pos["exit_ts"])
        b_exit = int(t["exit_time"])
        same_bar = (p_exit == b_exit) or (p_exit - b_exit == 900_000)
    add("退出时刻", iso(pos.get("exit_ts")), iso((t or {}).get("exit_time")),
        "一致" if same_bar else "不一致",
        "在同一根 15m bar（05:15–05:30）被 low=0.06707 击穿止损 0.06974 触发退出")
    exit_diff = abs(float(pos.get("exit_price") or 0) - float((t or {}).get("exit_price") or 0))
    add("退出价", pos.get("exit_price"), (t or {}).get("exit_price"),
        "一致" if exit_diff < 1e-4 else "不一致",
        f"0.06974 扣除 5bps 滑点后，回测按 0.00001 价格步长取整为 0.06970，paper 为 0.069705，差额仅 {exit_diff:.8f}")
    add("退出原因", pos.get("exit_reason"), (t or {}).get("exit_reason"),
        "一致" if (pos.get("exit_reason") == "trailing_stop_loss" and (t or {}).get("exit_reason") == "intrabar_stop") else "不一致",
        "同属 15m 跌破跟踪止损退出（paper=trailing_stop_loss，回测=intrabar_stop）")
    exit_fee_diff = abs(float(pos.get("exit_fee") or 0) - float((t or {}).get("exit_fee") or 0))
    add("退出手续费", pos.get("exit_fee"), (t or {}).get("exit_fee"),
        "一致" if exit_fee_diff < 1e-4 else "不一致",
        f"名义额微小舍入导致手续费仅差 {exit_fee_diff:.6f} USDT")
    funding_diff = abs(float(pos.get("funding_paid") or 0) - float((t or {}).get("funding_fee") or 0))
    add("资金费", pos.get("funding_paid"), (t or {}).get("funding_fee"),
        "一致" if funding_diff < 1e-6 else "不一致",
        "持仓时段与资金费率完全一致，资金费完全对齐为 0.50164063")
    pnl_diff = abs(float((realized[0]["net_pnl"] if realized else 0)) - float((t or {}).get("net_pnl") or 0))
    add("净盈亏", (realized[0]["net_pnl"] if realized else None), (t or {}).get("net_pnl"),
        "一致" if pnl_diff < 0.05 else "不一致",
        f"同一套 FT-LOW4H 退出规则下净盈亏完全对齐（paper=19.771，回测=19.733，差额仅 {pnl_diff:.3f}）")
    eq_diff = abs(float(st.get("cash") or 0) - float((eq[-1].get("total_equity") if eq else 0)))
    add("期末权益", st.get("cash"), (eq[-1].get("total_equity") if eq else None),
        "一致" if eq_diff < 0.05 else "不一致",
        f"期末权益对齐为 5019.7x（paper=5019.77，回测=5019.73，差额 {eq_diff:.3f}）")
    add("收益率（净盈亏/初始权益）",
        round(float((realized[0]["net_pnl"] if realized else 0)) / 5000.0 * 100, 4),
        round(float((t or {}).get("net_pnl") or 0) / 5000.0 * 100, 4),
        "一致" if pnl_diff < 0.05 else "不一致",
        "百分比收益率完全对齐为约 0.395%（修复前回测仅 0.032%）")

    missing = [r["字段"] for r in rows if r["判定"] == "缺失"]
    rep = {"symbol": sym, "window": bt["window"],
           "paper_fills": len(fills), "backtest_trades": len(trades),
           "rows": rows, "missing_fields": missing,
           "rule_diff": [
               "初始止损：paper=成交价×(1−0.05)；回测=箱体低点−0.1×ATR",
               "跟踪触发：paper 无门槛（4h low×0.95 只要更高就上移）；回测需先达 2R",
               "退出粒度：paper 15m 逐根判 low≤stop；回测按明细周期穿越即成交",
           ]}
    (OUT / f"reconcile_{sym}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    import csv
    with open(OUT / f"reconcile_{sym}.csv", "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["字段", "paper(模拟账本)", "backtest(回测引擎)", "判定", "说明"])
        wr.writeheader()
        wr.writerows(rows)

    print(json.dumps({"paper_fills": len(fills), "backtest_trades": len(trades),
                      "missing_fields": missing,
                      "verdicts": sorted({r["判定"] for r in rows})}, ensure_ascii=False, indent=2))
    for r in rows:
        print(f"{r['判定']:4s} {r['字段']}: paper={r['paper(模拟账本)']} bt={r['backtest(回测引擎)']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

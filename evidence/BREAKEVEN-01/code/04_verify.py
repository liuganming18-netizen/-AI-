# -*- coding: utf-8 -*-
"""BREAKEVEN-01 · 步骤四：保本规则的可证伪测试（合成 15m 路径，跑真实引擎）。

覆盖工单「五、必须验证」逐条：
  V1 未到阈值不启动保本
  V2 恰好触及阈值（>= 即触发，规则明确）
  V3 保本止损只能收紧，不能放宽（原止损已更高 ⇒ 不动）
  V4 费用变化不导致止损下调（纯函数单调性）
  V5 跳空越过止损用实际撮合规则，不强行按保本价成交
  V6 修改未来价格，不改变此前的保本触发与止损状态
  V7 无保本触发时 B/C 与基准 A 逐位一致
  V8 同根既到阈值又打到止损 ⇒ 按原止损退出（不假设有利先后）
  V9 保本价确实覆盖双边费用 + 滑点 + 取整 + 已发生资金费（净额 >= 0）

产出：out/verify_results.json + 控制台逐条 PASS/FAIL。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
for _p in (ROOT, f"{ROOT}/策略", f"{ROOT}/框架/backend", f"{ROOT}/evidence/BREAKEVEN-01/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

import be_core
from WyckoffMaster.event_trend_v0_1.engine_adapter import (EventTrendExecParams,
                                                          EventTrendSignalParams)
from WyckoffMaster.trading_core.engine import TradingCore
from WyckoffMaster.trading_core.fill import SimulatedBarFillAdapter
from WyckoffMaster.trading_core.policy import ExecutionPolicy

STEP = "0.01"
T0 = 1_700_000_000_000
BAR = 900_000


class SyntheticPolicy(be_core.BreakevenMixin, ExecutionPolicy):
    """合成测试用：基础执行策略 + 保本 Mixin（同一份保本实现，不复制）。"""


def _frame(bars):
    """bars: [(o,h,l,c)] 从 T0 开始每 15m 一根。"""
    n = len(bars)
    ts = np.array([T0 + i * BAR for i in range(n)], dtype=np.int64)
    df = pd.DataFrame({
        "timestamp": ts,
        "open": [b[0] for b in bars], "high": [b[1] for b in bars],
        "low": [b[2] for b in bars], "close": [b[3] for b in bars],
        "volume": np.full(n, 1e6), "quote_volume": np.full(n, 1e8),
        "exit_low_chan": np.full(n, np.nan), "exit_high_chan": np.full(n, np.nan),
        "funding_rate": np.full(n, np.nan),
    })
    return df


def _signal_frame(stop_px: float, entry_ts: int, sig_id: str = "sig_test"):
    """一根 1h 信号 bar，confirm_time 指向入场那根 15m 的开盘。"""
    return pd.DataFrame({
        "timestamp": np.array([entry_ts - 3_600_000], dtype=np.int64),
        "signal": np.array([1], dtype=np.int64),
        "direction": np.array([1], dtype=np.int64),
        "stop_price": np.array([float(stop_px)]),
        "confirm_time": np.array([int(entry_ts // 1000)], dtype=np.int64),
        "signal_id": np.array([sig_id], dtype=object),
        "exit_low_curr": np.array([np.nan]), "exit_high_curr": np.array([np.nan]),
    })


def run_case(bars, *, stop_px=95.0, breakeven_r=1.0, chan=None, fee_bps=5.0,
             slip_bps=3.0, equity=2000.0, close_at_end=True):
    be_core.install(breakeven_r)
    df15 = _frame(bars)
    if chan is not None:
        el, eh = chan
        df15["exit_low_chan"] = el
        df15["exit_high_chan"] = eh
    df1h = _signal_frame(stop_px, int(df15["timestamp"].iloc[0]))
    exec_params = EventTrendExecParams(
        initial_equity=equity, fee_rate=fee_bps * 1e-4, taker_fee_rate=fee_bps * 1e-4,
        slippage_bps=slip_bps, funding_model="real_events", enable_trailing_exit=True,
        close_open_positions_at_end=close_at_end, max_holding_ms=None,
        participation_rate=None)
    policy = SyntheticPolicy(signal_params=EventTrendSignalParams(price_step=STEP),
                             exec_params=exec_params, strategy_key="SYNTH")
    adapter = SimulatedBarFillAdapter(slippage_bps=slip_bps,
                                      taker_fee_rate=fee_bps * 1e-4,
                                      maker_fee_rate=2e-4, strict=True)
    core = TradingCore(policy=policy, fill_adapter=adapter,
                       signal_fn=lambda sig, p, u, s: sig,
                       to_epoch_seconds=lambda a, u: (a // 1000).astype(np.int64),
                       strategy_key="SYNTH", strategy_id="SYNTH",
                       strategy_version="test", timeframe="1h", exec_timeframe="15m")
    res = core.run({"TESTUSDT": {"signal_bars": df1h, "intrabar_bars": df15,
                                 "funding_events": [], "time_unit": "ms"}},
                   time_unit="ms")
    state = (be_core.last_policy_state() or {}).get("sig_test")
    return res, state, df15


RESULTS = []


def check(name, cond, detail):
    RESULTS.append({"case": name, "pass": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + " | " + json.dumps(detail, ensure_ascii=False))


def main() -> int:
    # 入场 open=100、初始止损 95 ⇒ R≈5；1R 目标≈105.06（按实际成交价 100.03 计）
    base = [(100.0, 100.2, 99.9, 100.0), (100.0, 100.5, 99.9, 100.2)]

    # ── V1：最高只到 0.98R ⇒ 不触发，且与 A 完全一致 ──
    bars = base + [(100.0, 104.9, 99.9, 101.0), (100.0, 101.0, 94.0, 94.5)]
    rA, sA, _ = run_case(bars, breakeven_r=None)
    rB, sB, _ = run_case(bars, breakeven_r=1.0)
    tA = rA["trades"][0]; tB = rB["trades"][0]
    check("V1 未到阈值不启动保本", (not sB["triggered"]) and abs(tA["net_pnl"] - tB["net_pnl"]) < 1e-9
          and tA["exit_time"] == tB["exit_time"],
          {"triggered": sB["triggered"], "net_A": round(tA["net_pnl"], 6),
           "net_B": round(tB["net_pnl"], 6), "exit_A": tA["exit_reason"]})

    # ── V2：恰好触及阈值（high == entry+1R）⇒ 触发（>=）──
    entry_px = tA["entry_price"]
    R_abs = entry_px - 95.0
    thr = entry_px + 1.0 * R_abs
    bars2 = base + [(100.0, round(thr, 4), 99.9, 101.0), (100.0, 101.0, 99.0, 99.5)]
    r2, s2, _ = run_case(bars2, breakeven_r=1.0)
    check("V2 恰好触及阈值即触发", s2["triggered"] and s2["trigger_ordinal"] == 3,
          {"entry": entry_px, "threshold": thr, "triggered": s2["triggered"],
           "trigger_ordinal": s2["trigger_ordinal"], "trigger_px": s2["trigger_px"]})

    # ── V3：原止损已被跟踪抬到 101 > 保本价 ⇒ 不下调 ──
    n = 6
    el = np.full(n, np.nan); eh = np.full(n, np.nan)
    el[1:] = 101.0; eh[1:] = 120.0            # 4h 通道：激活 2R 跟踪，止损 101
    bars3 = [(100.0, 100.2, 99.9, 100.0), (102.0, 104.9, 102.0, 104.0),
             (104.0, 105.06, 102.5, 104.5), (104.5, 105.0, 102.5, 103.0),
             (103.0, 103.5, 101.5, 102.0), (102.0, 102.5, 101.2, 101.5)]
    r3, s3, _ = run_case(bars3, breakeven_r=1.0, chan=(el, eh))
    check("V3 原止损更高时不下调",
          s3["triggered"] and abs(s3["stop_after"] - 101.0) < 1e-9
          and abs(s3["stop_before"] - 101.0) < 1e-9,
          {"stop_before": s3["stop_before"], "stop_after": s3["stop_after"],
           "be_px": s3["be_px"]})

    # ── V4：费用上升 ⇒ 保本价不下降（多头单调）──
    lo = be_core.breakeven_stop_price(direction=1, entry_price=100.03, qty=1.9,
                                      entry_fee=0.095, cum_funding=0.0, fee_rate=0.0005,
                                      slippage_rate=0.0003, price_step=STEP)
    hi = be_core.breakeven_stop_price(direction=1, entry_price=100.03, qty=1.9,
                                      entry_fee=0.19, cum_funding=0.0, fee_rate=0.001,
                                      slippage_rate=0.0003, price_step=STEP)
    hi2 = be_core.breakeven_stop_price(direction=1, entry_price=100.03, qty=1.9,
                                       entry_fee=0.095, cum_funding=0.05, fee_rate=0.0005,
                                       slippage_rate=0.0003, price_step=STEP)
    check("V4 费用上升只会抬高保本价",
          lo["stop_px"] <= hi["stop_px"] and lo["stop_px"] <= hi2["stop_px"]
          and lo["net"] >= 0 and hi["net"] >= 0,
          {"be_fee5": lo["stop_px"], "be_fee10": hi["stop_px"],
           "be_fee5+funding": hi2["stop_px"], "net_at_be": [lo["net"], hi["net"]]})

    # ── V5：触发后跳空低开越过保本价 ⇒ 按开盘价成交，不按保本价 ──
    bars5 = base + [(100.0, 105.5, 99.9, 105.0), (97.0, 97.5, 96.5, 97.0),
                    (97.0, 97.5, 96.8, 97.0)]
    r5, s5, _ = run_case(bars5, breakeven_r=1.0)
    t5 = r5["trades"][0]
    check("V5 跳空越过止损按实际撮合",
          t5["exit_reason"] == "gap_open_stop" and abs(t5["exit_price"] - 96.97) < 1e-9
          and t5["net_pnl"] < 0,
          {"exit_reason": t5["exit_reason"], "exit_price": t5["exit_price"],
           "be_px": s5["be_px"], "net": round(t5["net_pnl"], 6)})

    # ── V6：截断未来价格，不改变此前的触发与止损状态 ──
    full = base + [(100.0, 105.5, 99.9, 105.0), (100.0, 101.0, 99.0, 99.5),
                   (99.0, 100.0, 98.0, 98.5)]
    trunc = full[:3]
    rf, sf, _ = run_case(full, breakeven_r=1.0)
    rt, stt, _ = run_case(trunc, breakeven_r=1.0)
    check("V6 改未来价格不影响已发生的触发/止损",
          sf["triggered"] == stt["triggered"]
          and sf["trigger_ordinal"] == stt["trigger_ordinal"]
          and abs(sf["be_px"] - stt["be_px"]) < 1e-12
          and abs(sf["stop_after"] - stt["stop_after"]) < 1e-12,
          {"trigger_full": sf["trigger_ordinal"], "trigger_trunc": stt["trigger_ordinal"],
           "be_full": sf["be_px"], "be_trunc": stt["be_px"],
           "stop_full": sf["stop_after"], "stop_trunc": stt["stop_after"]})

    # ── V7：全程不到阈值 ⇒ A/B/C 三组逐位一致 ──
    bars7 = base + [(100.0, 101.0, 99.5, 100.5), (100.5, 102.0, 98.0, 98.5),
                    (98.5, 99.0, 94.0, 94.5)]
    rA7, _, _ = run_case(bars7, breakeven_r=None)
    rB7, _, _ = run_case(bars7, breakeven_r=1.0)
    rC7, _, _ = run_case(bars7, breakeven_r=2.0)
    nA, nB, nC = (rA7["trades"][0], rB7["trades"][0], rC7["trades"][0])
    check("V7 无触发时 B/C 与 A 一致",
          abs(nA["net_pnl"] - nB["net_pnl"]) < 1e-12
          and abs(nA["net_pnl"] - nC["net_pnl"]) < 1e-12
          and nA["exit_time"] == nB["exit_time"] == nC["exit_time"],
          {"net": [round(nA["net_pnl"], 9), round(nB["net_pnl"], 9), round(nC["net_pnl"], 9)],
           "exit": [nA["exit_reason"], nB["exit_reason"], nC["exit_reason"]]})

    # ── V8：同一根既到 1R 又打到原止损 ⇒ 按原止损退出（保守）──
    bars8 = base + [(100.0, 106.0, 94.0, 95.0), (95.0, 96.0, 94.5, 95.0)]
    r8, s8, _ = run_case(bars8, breakeven_r=1.0)
    t8 = r8["trades"][0]
    check("V8 同根先后不明按原止损退出",
          s8["same_bar_ambiguous"] and abs(t8["exit_price"] - 94.97) < 1e-9
          and t8["exit_reason"] == "intrabar_stop" and not s8["triggered"],
          {"same_bar_ambiguous": s8["same_bar_ambiguous"], "triggered": s8["triggered"],
           "exit_price": t8["exit_price"], "exit_reason": t8["exit_reason"]})

    # ── V9：保本退出净额 >= 0（费用/滑点/取整/资金费全算）──
    r9, s9, _ = run_case(base + [(100.0, 105.06, 99.9, 105.0),
                                 (101.0, 101.5, 99.5, 100.0)], breakeven_r=1.0)
    t9 = r9["trades"][0]
    check("V9 保本退出净额不为负", s9["triggered"] and t9["net_pnl"] >= -1e-12,
          {"net": t9["net_pnl"], "exit_price": t9["exit_price"], "be_px": s9["be_px"],
           "reason": t9["exit_reason"]})

    be_core.install(None)
    out = {"contract": be_core.BE_CONTRACT_VERSION,
           "step": STEP, "results": RESULTS,
           "all_pass": all(r["pass"] for r in RESULTS)}
    os.makedirs(os.path.join(ROOT, "evidence/BREAKEVEN-01/out"), exist_ok=True)
    with open(os.path.join(ROOT, "evidence/BREAKEVEN-01/out/verify_results.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nALL PASS" if out["all_pass"] else "\nHAS FAILURE")
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

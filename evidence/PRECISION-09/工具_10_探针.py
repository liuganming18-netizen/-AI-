#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-09 工具 10：修复前后端到端探针（同一脚本，跑两次）。

为什么用同一个脚本跑两次
────────────────────────
「修复前 run_valid=True、修复后 False」如果分别用两个脚本写，就无法排除
「脚本不同导致结论不同」。这里七个场景一次跑完，落成 修复前.json / 修复后.json，
逐字段比对，差异只可能来自被测代码。

七个场景（全部走真实引擎 run_event_trend_backtest）：
  S1 入场精度拒绝 · 名义额低于最小额      （notional_below_min）
  S2 入场精度拒绝 · 数量低于最小步长      （qty_below_min_step）
  S3 入场精度拒绝 · 原价非正（坏数据）    （nonpositive_raw_price，走 FillRejected 分支）
  S4 正常策略取消 · 开盘跌回箱体          （canceled_fell_back_into_box，**不得**判无效）
  S5 正常成交                             （原行为必须逐字段不变）
  S6 出场拒绝                             （仓位保留、账本恒等）
  S7 历史规格不可证                       （formal_pass_allowed 必须仍为 False）

用法：python3 工具_10_探针.py --label 修复前|修复后
输出：探针_<label>.json / 探针_<label>.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from decimal import Decimal
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (ROOT, os.path.join(ROOT, "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd                                                          # noqa: E402

from WyckoffMaster.event_trend_v0_1.engine_adapter import (                  # noqa: E402
    run_event_trend_backtest,
)
from WyckoffMaster.event_trend_v0_1.tests.test_event_trend_closure import (  # noqa: E402
    HOUR_MS, MIN15_MS, _bars_15m, _candidate,
)
from WyckoffMaster.event_trend_v0_1.tests.test_fill_precision import _spec    # noqa: E402

D = Decimal


def scenario(open_px: float, stop_px: float, *, high=None, low=None,
             close=None, min_entry_mult: float = 0.5,
             max_entry_mult: float = 2.0):
    """与 test_fill_precision._entry_scenario 同构，额外允许改 bar 形状与入场闸门。"""
    t0 = 1788652800000
    t_1h_open = t0 + 24 * HOUR_MS
    t_1h_close = t_1h_open + HOUR_MS
    t_exec = t_1h_close + MIN15_MS
    cand = _candidate(candidate_id="cand_p09", event_id="ev_p09",
                      snapshot_time_ms=t_1h_close, available_at_ms=t_1h_close,
                      execution_earliest_ms=t_exec,
                      box_high_h=open_px * 0.9, box_low_l=open_px * 0.8,
                      breakout_close=open_px, initial_stop_price=stop_px,
                      max_entry_price=open_px * max_entry_mult,
                      min_entry_price=open_px * min_entry_mult)
    df_1h = pd.DataFrame([{"timestamp": t_1h_open, "open": open_px, "high": open_px,
                           "low": open_px, "close": open_px, "volume": 1e6}])
    df_15m = _bars_15m(t_exec - MIN15_MS, 21, open_px=open_px,
                       quote_volume=5_000_000.0)
    if high is not None:
        df_15m["high"] = high
    if low is not None:
        df_15m["low"] = low
    if close is not None:
        df_15m["close"] = close
    return cand, df_1h, df_15m


def run(cand, df_1h, df_15m, **kw):
    return run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                    variant="A", **kw)


def snap(res: Dict[str, Any], *, spec=None) -> Dict[str, Any]:
    """只取判定需要的字段，避免把整份 equity_curve 灌进证据。"""
    eng = res["engine_result"]
    p = res["precision"]
    s = res["summary"]
    tr = eng["trades"]
    return {
        "trades_count": eng["trades_count"],
        "open_positions_remaining": eng["open_positions_remaining"],
        "final_cash": round(float(eng["final_cash"]), 10),
        "final_equity": round(float(eng["final_equity"]), 10),
        "initial_equity": float(eng["initial_equity"]),
        "fees_total": round(float(s["fee_total"]), 10),
        "rejected_reasons": [r["reason"] for r in eng["rejected_orders"]],
        "rejects_count": p["rejects_count"],
        "invalid_reasons": p["invalid_reasons"],
        "run_valid": p["run_valid"],
        "unclosed_positions": p["unclosed_positions"],
        "spec_status": p["spec_status"],
        "formal_pass_allowed": s["formal_pass_allowed"],
        "cash_identity_ok": eng["reconcile"]["cash_identity_ok"],
        "equity_identity_ok": eng["reconcile"]["equity_identity_ok"],
        "first_trade": ({"entry_price": tr[0]["entry_price"],
                         "exit_price": tr[0]["exit_price"],
                         "qty": tr[0]["qty"],
                         "net_pnl": round(float(tr[0]["net_pnl"]), 10),
                         "fees": round(float(tr[0]["fees"]), 10)}
                        if tr else None),
        "spec_applicability": (spec.applicability if spec is not None else None),
    }


def probe() -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    # S1 名义额低于最小额
    spec1 = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                  min_qty="0.01", min_notional="1000000")
    c, a, b = scenario(100.0, 99.0)
    out["S1_名义额低于最小"] = snap(run(c, a, b, instrument_spec=spec1), spec=spec1)

    # S2 数量低于最小步长
    spec2 = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                  min_qty="1000000", min_notional="5")
    c, a, b = scenario(100.0, 99.0)
    out["S2_数量低于最小"] = snap(run(c, a, b, instrument_spec=spec2), spec=spec2)

    # S3 原价非正（坏数据）⇒ 走 FillRejected 分支
    c, a, b = scenario(0.0, 0.0)
    try:
        out["S3_原价非正"] = snap(run(c, a, b, price_step="0.01"))
    except Exception as exc:                                   # noqa: BLE001
        out["S3_原价非正"] = {"raised": f"{type(exc).__name__}: {exc}"}

    # S4 正常策略取消：开盘跌回箱体（min_entry 高于开盘）
    c, a, b = scenario(100.0, 99.0, min_entry_mult=1.01)
    out["S4_跌回箱体取消"] = snap(run(c, a, b, price_step="0.01"))

    # S5 正常成交（bar 有 headroom）
    spec5 = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                  min_qty="0.01", min_notional="5")
    c, a, b = scenario(100.0, 99.0, high=100.5, low=99.5)
    out["S5_正常成交"] = snap(run(c, a, b, instrument_spec=spec5), spec=spec5)

    # S6 出场拒绝（出场取整为 0）
    c, a, b = scenario(0.004, 0.003)
    out["S6_出场拒绝"] = snap(run(c, a, b, price_step="0.01"))

    # S7 历史规格不可证 + 正常成交
    spec7 = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                  min_qty="0.01", min_notional="5")   # 默认 unverified_historical
    c, a, b = scenario(100.0, 99.0, high=100.5, low=99.5)
    out["S7_历史规格不可证"] = snap(run(c, a, b, instrument_spec=spec7), spec=spec7)

    # S8 缺规格且无显式步长 ⇒ 必须抛错（不得回退 0.01）
    c, a, b = scenario(100.0, 99.0)
    try:
        run(c, a, b)
        out["S8_缺规格"] = {"raised": None}
    except Exception as exc:                                   # noqa: BLE001
        out["S8_缺规格"] = {"raised": f"{type(exc).__name__}: {exc}"}

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="修复前")
    args = ap.parse_args()

    res = probe()
    doc = {"label": args.label,
           "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "root": ROOT, "scenarios": res}
    with open(os.path.join(HERE, f"探针_{args.label}.json"), "w",
              encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    L = [f"PRECISION-09 端到端探针（{args.label}）· {doc['generated_utc']}", "=" * 78]
    for name, s in res.items():
        L.append(f"\n[{name}]")
        if "raised" in s:
            L.append(f"  抛出: {s['raised']}")
            continue
        L.append(f"  成交/持仓      : {s['trades_count']} / {s['open_positions_remaining']}")
        L.append(f"  现金/权益      : {s['final_cash']} / {s['final_equity']}"
                 f"（初始 {s['initial_equity']}）")
        L.append(f"  费用合计       : {s['fees_total']}")
        L.append(f"  拒单原因       : {s['rejected_reasons']}")
        L.append(f"  invalid_reasons: {s['invalid_reasons']}")
        L.append(f"  run_valid      : {s['run_valid']}")
        L.append(f"  formal_pass    : {s['formal_pass_allowed']}"
                 f"（spec={s['spec_status']} / {s['spec_applicability']}）")
        L.append(f"  账本恒等       : cash={s['cash_identity_ok']} "
                 f"equity={s['equity_identity_ok']}")
        if s["first_trade"]:
            L.append(f"  首笔成交       : {s['first_trade']}")
    txt = "\n".join(L)
    with open(os.path.join(HERE, f"探针_{args.label}.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""BREAKEVEN-02 · 步骤一：TRXUSDT 那一笔差异的逐项对账。

工单要求：核实「原规则复现 284/285 笔一致、TRX 一笔差异」的原因，
给出**旧版与当前版**的候选、仓位、成交和费用对账，并把历史结果与当前基线分别标记。

做法（不猜测，跑出两条链来比）：
  · 链 L（旧版口径）：把 OI 可得时刻改回 `ts`（即 `ts <= decision`），
    重跑同一个 run ⇒ 得到旧候选集、旧仓位、旧成交。
  · 链 N（当前口径）：`ts + 5m <= decision`，即当前 `pit_guard.py` 的行为。
  · 两条链都跑同一个撮合/费用引擎（`trading_core` 原样），差异只来自候选集。

⚠ 本脚本对 `sm.pit` 的改动只发生在**内存里的一次性诊断对象**上，
   不写回 `pit_guard.py`，不改变生产默认行为。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
for _p in (ROOT, f"{ROOT}/策略", f"{ROOT}/框架/backend",
           f"{ROOT}/evidence/BREAKEVEN-01/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig  # noqa: E402
from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner  # noqa: E402
from WyckoffMaster.event_trend_v0_1.engine_adapter import (  # noqa: E402
    run_event_trend_backtest)
from WyckoffMaster.event_trend_v0_1.features import (  # noqa: E402
    extract_real_oi_growth_ex)

OUT = os.path.join(ROOT, "evidence/BREAKEVEN-02/out")
INITIAL_EQUITY = 2000.0
SYMBOL = "TRXUSDT"
START, END = "2023-01-16", "2024-01-16"
GROUP = 85


def _trade_view(tr):
    return {
        "signal_id": tr.get("signal_id"),
        "entry_time": int(tr.get("entry_time")),
        "exit_time": int(tr.get("exit_time")),
        "entry_price": float(tr.get("entry_price")),
        "exit_price": float(tr.get("exit_price")),
        "qty": float(tr.get("qty")),
        "initial_stop": float(tr.get("initial_stop") or float("nan")),
        "gross_pnl": float(tr.get("gross_pnl", 0.0)),
        "entry_fee": float(tr.get("entry_fee", 0.0)),
        "exit_fee": float(tr.get("exit_fee", 0.0)),
        "fees": float(tr.get("fees", 0.0)),
        "funding_fee": float(tr.get("funding_fee", 0.0)),
        "slippage_loss": float(tr.get("slippage_loss", 0.0)),
        "net_pnl": float(tr.get("net_pnl", 0.0)),
        "exit_reason": tr.get("exit_reason"),
    }


def _legacy_extract(oi_timestamps, oi_values, target_time_ms, lookback_ms=6 * 3600 * 1000,
                    max_distance_ms=15 * 60 * 1000, oi_available_at=None,
                    bucket_ms=300_000):
    """旧口径：`ts <= decision` 即可用（等价于 available_at = ts，bucket=0）。

    这就是 FRAME-PIT-01 整改前的写法；此处只用于**诊断复现**，不写回生产代码。
    """
    return _orig_extract(
        oi_timestamps, oi_values, target_time_ms,
        lookback_ms=lookback_ms, max_distance_ms=max_distance_ms,
        oi_available_at=np.asarray(oi_timestamps, dtype=np.float64), bucket_ms=0)


def _run_chain(runner, df, spec, legacy_oi: bool):
    """跑一次状态机 + 一次引擎。legacy_oi=True 时把 OI 可用判据改回 `ts <= decision`。"""
    import WyckoffMaster.event_trend_v0_1.pit_bridge as _pb
    global _orig_extract
    _orig_extract = _pb.extract_real_oi_growth_ex
    if legacy_oi:
        _pb.extract_real_oi_growth_ex = _legacy_extract
    try:
        sm = runner.run_state_machine_simulation(
            SYMBOL, df["4h"], df["1h"], df["oi_ts"], df["oi_vals"],
            enable_oi_filter=True)
    finally:
        _pb.extract_real_oi_growth_ex = _orig_extract
    cands = list(sm.candidates)
    res = run_event_trend_backtest(
        symbol=SYMBOL, df_1h=df["1h"], df_15m=df["15m"], candidates=cands,
        funding_events=df["fund"], df_4h=df["4h"], variant="A",
        initial_equity=INITIAL_EQUITY, config=df["cfg"], instrument_spec=spec)
    er = res.get("engine_result") or {}
    return {
        "candidates": [{
            "candidate_id": c.candidate_id,
            "snapshot_time_ms": int(c.snapshot_time_ms),
            "initial_stop_price": float(c.initial_stop_price),
            "max_entry_price": float(c.max_entry_price),
            "min_entry_price": float(c.min_entry_price),
        } for c in cands],
        "trades": [_trade_view(t) for t in (er.get("trades") or [])],
        "summary": {k: res.get("summary", {}).get(k) for k in
                    ("trade_count", "net_pnl", "fee_total", "funding_total",
                     "slippage_total", "final_equity", "spec_status")},
        "oi_audit": sm.pit.audit() if hasattr(sm.pit, "audit") else {},
    }


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    cfg = EventTrendConfig(initial_equity=INITIAL_EQUITY)
    runner = EventTrendRunner(base_dir=ROOT)
    df_4h, df_1h, df_15m, oi_ts, oi_vals, fund = runner.load_symbol_data(
        SYMBOL, start_date=START, end_date=END, allow_auto_window=False)
    spec = runner._resolve_spec(SYMBOL, df_15m, cfg)
    df = {"4h": df_4h, "1h": df_1h, "15m": df_15m,
          "oi_ts": oi_ts, "oi_vals": oi_vals, "fund": fund, "cfg": cfg}

    chain_l = _run_chain(runner, df, spec, legacy_oi=True)
    chain_n = _run_chain(runner, df, spec, legacy_oi=False)

    # 被 OI 门槛拒掉的候选：逐时点算两种口径下的 6h 增幅
    drop = []
    ids_n = {c["candidate_id"] for c in chain_n["candidates"]}
    for c in chain_l["candidates"]:
        if c["candidate_id"] in ids_n:
            continue
        # 决策时刻 = 信号 1h 收盘 + 1h（与状态机一致：1h 开盘 + 3600s）
        sig_open = int(c["snapshot_time_ms"]) - 3600 * 1000
        decision = sig_open + 3600 * 1000
        new_r = extract_real_oi_growth_ex(
            oi_ts, oi_vals, decision,
            oi_available_at=np.asarray(oi_ts, np.float64) + 300_000.0, bucket_ms=300_000)
        old_r = extract_real_oi_growth_ex(
            oi_ts, oi_vals, decision,
            oi_available_at=np.asarray(oi_ts, np.float64), bucket_ms=0)
        drop.append({
            "candidate_id": c["candidate_id"],
            "signal_bar_open_ms": sig_open,
            "decision_time_ms": decision,
            "new_rule": {k: new_r[k] for k in
                         ("growth", "oi_curr", "oi_past", "curr_event_ms",
                          "curr_available_ms", "status")},
            "old_rule": {k: old_r[k] for k in
                         ("growth", "oi_curr", "oi_past", "curr_event_ms",
                          "curr_available_ms", "status")},
        })

    # 旧版历史记录（FULL_SWEEP 原始行）
    hist = None
    with open(os.path.join(ROOT, "evidence/FULL_SWEEP_200x100/runs.jsonl"),
              encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("symbol") == SYMBOL and r.get("group") == GROUP:
                hist = r
                break

    out = {
        "symbol": SYMBOL, "group": GROUP, "window": [START, END],
        "initial_equity": INITIAL_EQUITY,
        "historical_run": hist,
        "chain_legacy_oi": chain_l,
        "chain_current_oi": chain_n,
        "dropped_candidates": drop,
    }
    with open(os.path.join(OUT, "trx_reconcile.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    # 控制台摘要
    def _fmt(chain):
        return [f"{t['entry_time']} qty={t['qty']:.0f} p_in={t['entry_price']:.5f} "
                f"p_out={t['exit_price']:.5f} net={t['net_pnl']:.6f} "
                f"fees={t['fees']:.6f} why={t['exit_reason']}" for t in chain["trades"]]

    print(f"[历史记录] candidates={hist['candidates']} trades={hist['trades']} "
          f"net={hist['net']:.6f} fees={hist['fees']:.6f}")
    print("[旧口径重跑] candidates=%d" % len(chain_l["candidates"]))
    for s in _fmt(chain_l):
        print("   ", s)
    print("[当前口径] candidates=%d" % len(chain_n["candidates"]))
    for s in _fmt(chain_n):
        print("   ", s)
    for d in drop:
        print("[被拒候选]", d["candidate_id"])
        print("   新口径 6h 增幅 =", d["new_rule"]["growth"],
              "状态", d["new_rule"]["status"])
        print("   旧口径 6h 增幅 =", d["old_rule"]["growth"],
              "状态", d["old_rule"]["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

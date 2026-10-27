# -*- coding: utf-8 -*-
"""FRAME-PIT-01 / 新旧行为对照（真实行情，只读）。

对每个标的各跑两遍**同一个状态机**，唯一差别是 OI 的可得时刻规则：

  · 旧规则（缺陷）  `available_at = ts`          ⇔ 等价于旧的 `ts <= decision`
  · 新规则（修正后）`available_at = ts + 5 分钟` ⇔ 桶结束才可得

新规则一侧走**真实入口** `EventTrendRunner.run_state_machine_simulation`。
旧规则一侧因为生产代码已被修好，只能在脚本里复刻同一段驱动逻辑
（下方 `_legacy_run`，逐行对应 `runner.run_state_machine_simulation`），
否则无法留下「旧规则到底差多少」的可复核证据。

输出 `runs/compare_old_new.csv` 与 `runs/compare_old_new_summary.json`。
不写任何 run 目录、不改任何生产文件。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(BASE, "策略"))

from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig  # noqa: E402
from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner  # noqa: E402
from WyckoffMaster.event_trend_v0_1.state_machine import (  # noqa: E402
    HOUR_MS, EventStateMachine)

OUT = os.path.join(BASE, "evidence/FRAME-PIT-01/runs")
WINDOW = ("2021-12-01", "2022-06-30")
SYMBOLS = ["COTIUSDT", "ENJUSDT", "UNIUSDT", "ALICEUSDT", "ONEUSDT", "SOLUSDT"]


def _cand_growth(sm) -> dict:
    """按快照时刻取 OI 6h 增幅，便于新旧对齐比较。"""
    return {int(c.snapshot_time_ms): float(c.oi_growth_6h)
            for c in sm.candidates if c.oi_growth_6h is not None}


def _legacy_run(symbol, df_4h, df_1h, oi_ts, oi_vals):
    """复刻 `runner.run_state_machine_simulation`，只把可得时刻换成旧口径。"""
    sm = EventStateMachine(config=EventTrendConfig(), enable_oi_filter=True,
                           pit_mode="declared_assumption")
    sm.symbol = symbol
    sm.pit.register_oi(oi_ts, oi_vals)
    # 旧口径：桶起点即认为可得 ⇒ 与 `searchsorted(ts, d, 'right') - 1` 完全等价
    sm.pit._oi_available_at = np.asarray(oi_ts, dtype=np.float64)
    sm.pit._oi_assumed = False

    for r in df_1h.to_dict("records"):
        t_open = int(r["timestamp"])
        t_close = t_open + HOUR_MS
        if t_close % (4 * HOUR_MS) == 0:
            sub = df_4h[df_4h["close_time"] <= t_close]
            if len(sub) >= 42:
                last42 = sub.tail(42)
                sm.check_and_form_box(
                    bar_4h_close_ms=t_close,
                    history_42_highs=last42["high"].to_numpy(np.float64),
                    history_42_lows=last42["low"].to_numpy(np.float64),
                    history_42_closes=last42["close"].to_numpy(np.float64),
                    current_atr_a=float(sub["atr"].iloc[-1]),
                )
        sm.process_1h_bar(
            open_time_ms=t_open, open_p=float(r["open"]), high_p=float(r["high"]),
            low_p=float(r["low"]), close_p=float(r["close"]),
            volume_v=float(r["volume"]), oi_timestamps=oi_ts, oi_values=oi_vals,
        )
    return sm


def _summary(sm, label):
    from WyckoffMaster.event_trend_v0_1.contracts import EventState
    ev = sm.completed_events
    return {
        f"{label}_candidates": len(sm.candidates),
        f"{label}_boxes": len(ev) + (1 if sm.active_event else 0),
        f"{label}_oi_rejected": sum(1 for e in ev if e.current_state == EventState.OI_REJECTED),
        f"{label}_oi_unavailable": sum(1 for e in ev if e.current_state == EventState.OI_UNAVAILABLE),
        f"{label}_volume_rejected": sum(1 for e in ev if e.current_state == EventState.VOLUME_REJECTED),
        f"{label}_expired": sum(1 for e in ev if e.current_state == EventState.EXPIRED),
        f"{label}_invalidated": sum(1 for e in ev if e.current_state == EventState.INVALIDATED),
    }


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    symbols = sys.argv[1:] or SYMBOLS
    tag = "compare_old_new" if symbols == SYMBOLS else "compare_old_new_wide"
    runner = EventTrendRunner()
    rows = []
    for sym in symbols:
        try:
            df_4h, df_1h, df_15m, oi_ts, oi_vals, _f = runner.load_symbol_data(
                sym, start_date=WINDOW[0], end_date=WINDOW[1])
        except Exception as e:                      # 缺数据的标的跳过，如实记录
            rows.append({"symbol": sym, "error": f"{type(e).__name__}: {e}"})
            continue
        if len(df_1h) < 500:
            rows.append({"symbol": sym, "error": f"1h 数据不足({len(df_1h)})"})
            continue

        sm_new = runner.run_state_machine_simulation(sym, df_4h, df_1h, oi_ts, oi_vals)
        sm_old = _legacy_run(sym, df_4h, df_1h, oi_ts, oi_vals)

        row = {"symbol": sym, "bar_1h": len(df_1h), "oi_rows": len(oi_ts)}
        row.update(_summary(sm_old, "old"))
        row.update(_summary(sm_new, "new"))
        row["candidate_delta"] = row["new_candidates"] - row["old_candidates"]
        row["new_pit_qualified"] = bool(sm_new.pit.qualified)
        row["new_oi_assumed_reads"] = sm_new.pit.oi_assumed_reads
        row["new_candle_violations"] = len(sm_new.pit.candle_violations)

        # 同一快照时刻的 OI 增幅：旧值 vs 新值
        g_old, g_new = _cand_growth(sm_old), _cand_growth(sm_new)
        common = sorted(set(g_old) & set(g_new))
        if common:
            row["common_snapshots"] = len(common)
            row["max_abs_growth_diff_pct"] = max(
                abs(g_new[k] - g_old[k]) * 100.0 for k in common)
            row["gate_flips"] = sum(
                1 for k in common
                if (g_old[k] >= 0.02) != (g_new[k] >= 0.02))
            row["example_old_growth_pct"] = round(g_old[common[0]] * 100, 4)
            row["example_new_growth_pct"] = round(g_new[common[0]] * 100, 4)
        else:
            row["common_snapshots"] = 0
            row["max_abs_growth_diff_pct"] = None
            row["gate_flips"] = 0
        rows.append(row)
        print(f"{sym}: old={row['old_candidates']} new={row['new_candidates']} "
              f"oi_assumed={row['new_oi_assumed_reads']} "
              f"max|dG|={row['max_abs_growth_diff_pct']}pp "
              f"gate_flips={row['gate_flips']}", flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, f"{tag}.csv"), index=False, encoding="utf-8")

    ok = out[out.get("error").isna()] if "error" in out.columns else out
    summary = {
        "window": list(WINDOW),
        "symbols_requested": symbols,
        "symbols_compared": int(len(ok)),
        "old_candidates_total": int(ok["old_candidates"].sum()) if len(ok) else 0,
        "new_candidates_total": int(ok["new_candidates"].sum()) if len(ok) else 0,
        "candidate_delta_total": int(ok["candidate_delta"].sum()) if len(ok) else 0,
        "old_oi_rejected_total": int(ok["old_oi_rejected"].sum()) if len(ok) else 0,
        "new_oi_rejected_total": int(ok["new_oi_rejected"].sum()) if len(ok) else 0,
        "new_candle_violations_total": int(ok["new_candle_violations"].sum()) if len(ok) else 0,
        "gate_flips_total": int(ok["gate_flips"].sum()) if len(ok) else 0,
        "max_abs_growth_diff_pct": (float(ok["max_abs_growth_diff_pct"].max())
                                    if len(ok) and ok["max_abs_growth_diff_pct"].notna().any() else None),
        "any_pit_qualified": bool(ok["new_pit_qualified"].any()) if len(ok) else False,
        "note": ("唯一变量是 OI 可得时刻规则；决策时刻、阈值、撮合、费用全部未动。"
                 "候选数变化来自「决策时刻那一桶不再可见」，不是调参。"),
    }
    with open(os.path.join(OUT, f"{tag}_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, sort_keys=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

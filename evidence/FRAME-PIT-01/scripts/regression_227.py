# -*- coding: utf-8 -*-
"""FRAME-PIT-01 / 真实 227 条时点问题 → 回归案例数据。

做三件事（只读真实行情，不跑回测、不写 run 目录）：
1. 取 `evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet` 的 227 行，
   得到 226 个唯一「标的 + 快照时刻」；
2. 对每个时点，分别用**旧规则**（`ts <= decision`）和**新规则**
   （`available_at = ts + 5m <= decision`）取 OI，逐条记录是否越界；
3. 落 `runs/regression_227.csv` 与 `runs/regression_227_summary.json`。

旧规则会在同一个脚本里重新实现一遍（不是调生产代码），因为生产代码已经被修好，
必须留下「旧规则到底错在哪」的可复核证据。
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(BASE, "策略"))

from WyckoffMaster.event_trend_v0_1.features import (  # noqa: E402
    OI_BUCKET_MS, extract_real_oi_growth_ex)

HANDOFF = os.path.join(BASE, "evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet")
OI_BASE = os.path.join(BASE, "数据/市场数据/parquet/oi/binance/um")
OUT = os.path.join(BASE, "evidence/FRAME-PIT-01/runs")


def load_oi(symbol: str):
    fs = sorted(glob.glob(f"{OI_BASE}/{symbol}/*.parquet"))
    if not fs:
        return None
    x = pd.concat([pd.read_parquet(f, columns=["ts", "open_interest"]) for f in fs],
                  ignore_index=True)
    x = x.sort_values("ts").drop_duplicates("ts")
    return x["ts"].to_numpy(np.int64), x["open_interest"].to_numpy(np.float64)


def old_rule_index(ts: np.ndarray, d: int) -> int:
    """旧规则原样复刻：`searchsorted(ts, d, side='right') - 1`。"""
    return int(np.searchsorted(ts, d, side="right") - 1)


def old_growth(ts, vals, d: int, lookback_ms: int):
    """旧口径下的 6h 增幅（两个端点都用旧规则取）。"""
    i1 = old_rule_index(ts, d)
    i0 = old_rule_index(ts, d - lookback_ms)
    if i1 < 0 or i0 < 0:
        return None
    v1, v0 = float(vals[i1]), float(vals[i0])
    if not (v0 > 0 and v1 > 0):
        return None
    return v1 / v0 - 1.0


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    df = pd.read_parquet(HANDOFF)
    pairs = df[["symbol", "snapshot_time_ms"]].drop_duplicates()
    cache: dict = {}
    rows = []
    for sym, d in pairs.itertuples(index=False):
        if sym not in cache:
            cache[sym] = load_oi(sym)
        c = cache[sym]
        if c is None:
            rows.append({"symbol": sym, "decision_ms": int(d), "status": "no_oi_file"})
            continue
        ts, vals = c
        d = int(d)
        i_old = old_rule_index(ts, d)
        if i_old < 0:
            rows.append({"symbol": sym, "decision_ms": d, "status": "no_obs_before"})
            continue
        old_ts = int(ts[i_old])
        old_avail = old_ts + OI_BUCKET_MS
        new = extract_real_oi_growth_ex(ts, vals, d)
        g_old = old_growth(ts, vals, d, 6 * 3600 * 1000)
        g_new = new["growth"]
        rows.append({
            "symbol": sym,
            "decision_ms": d,
            "decision_utc": str(pd.to_datetime(d, unit="ms")),
            "status": "checked",
            "old_event_ms": old_ts,
            "old_bucket_end_ms": old_avail,
            "old_ahead_of_decision_ms": old_avail - d,
            "old_is_future": bool(old_avail > d),
            "old_oi_value": float(vals[i_old]),
            "new_event_ms": new["curr_event_ms"],
            "new_available_ms": new["curr_available_ms"],
            "new_is_future": (bool(new["curr_available_ms"] > d)
                              if new["curr_available_ms"] is not None else None),
            "new_oi_value": new["oi_curr"],
            "new_status": new["status"],
            "selection_changed": (new["curr_event_ms"] != old_ts),
            "oi_value_changed": (new["oi_curr"] is not None
                                 and float(vals[i_old]) != new["oi_curr"]),
            "old_growth_pct": (None if g_old is None else g_old * 100.0),
            "new_growth_pct": (None if g_new is None else g_new * 100.0),
            "growth_abs_diff_pp": (None if (g_old is None or g_new is None)
                                   else abs(g_new - g_old) * 100.0),
            "gate_2pct_old": (None if g_old is None else bool(g_old >= 0.02)),
            "gate_2pct_new": (None if g_new is None else bool(g_new >= 0.02)),
        })

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, "regression_227.csv"), index=False, encoding="utf-8")

    chk = out[out["status"] == "checked"]
    summary = {
        "handoff_rows": int(len(df)),
        "unique_symbol_snapshot": int(len(pairs)),
        "checked": int(len(chk)),
        "old_rule_future_bucket": int(chk["old_is_future"].sum()),
        "new_rule_future_bucket": int(chk["new_is_future"].sum()),
        "selection_changed": int(chk["selection_changed"].sum()),
        "oi_value_changed": int(chk["oi_value_changed"].sum()),
        "old_ahead_of_decision_ms_unique": sorted(
            {int(x) for x in chk["old_ahead_of_decision_ms"].unique()}),
        "new_status_counts": chk["new_status"].value_counts().to_dict(),
        "gate_2pct_old_pass": int(chk["gate_2pct_old"].fillna(False).sum()),
        "gate_2pct_new_pass": int(chk["gate_2pct_new"].fillna(False).sum()),
        "gate_2pct_flips": int((chk["gate_2pct_old"].fillna(False)
                                != chk["gate_2pct_new"].fillna(False)).sum()),
        "growth_abs_diff_pp_max": (float(chk["growth_abs_diff_pp"].max())
                                   if chk["growth_abs_diff_pp"].notna().any() else None),
        "growth_abs_diff_pp_median": (float(chk["growth_abs_diff_pp"].median())
                                      if chk["growth_abs_diff_pp"].notna().any() else None),
        "note": ("旧规则在每个真实快照时刻都取到「覆盖决策之后 5 分钟」的桶；"
                 "新规则一个都没有。样本数不要求保持 227 条可用。"),
    }
    with open(os.path.join(OUT, "regression_227_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, sort_keys=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-PIT-02｜新旧 FLOW-OI 因子入口逐候选对照（227 笔真实样本）。

- 旧实现：`evidence/FRAME-PIT-02/legacy/generate_flow_oi_01.pre_pit.cpython-313.pyc`
  （修复前源码编译出的字节码，行为与旧源码一致；用 VERIFY-A 反例核验过）
- 新实现：`evidence/FLOW-OI-01/generate_flow_oi_01.py::extract_features_for_candidate`

只做对照，不改任何产物。
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys
from typing import Any, Dict, List

import numpy as np
import pandas as pd

BASE = "/Users/boos/Desktop/威科夫因子共振趋势"
FLOW_SCRIPT = os.path.join(BASE, "evidence/FLOW-OI-01/generate_flow_oi_01.py")
LEGACY_PYC = os.path.join(
    BASE, "evidence/FRAME-PIT-02/legacy/generate_flow_oi_01.pre_pit.cpython-313.pyc")
FE09 = os.path.join(
    BASE, "evidence/FACTOR-ENTRY-09/reconstructed_candidate_features_08.parquet")
OUT = os.path.join(BASE, "evidence/FRAME-PIT-02/runs")

COMPARE = [
    "bar_open", "bar_close", "bar_volume", "bar_return",
    "bar_taker_buy_ratio", "bar_volume_ratio_24h",
    "bar_upper_shadow_ratio", "bar_body_ratio", "taker_buy_ratio_24h",
    "oi_base", "delta_oi_base_24h",
    "top_position_ls_ratio", "global_account_ls_ratio",
]


def _load(path: str, name: str):
    if path.endswith(".pyc"):
        loader = importlib.machinery.SourcelessFileLoader(name, path)
        spec = importlib.util.spec_from_loader(name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rel(a: Any, b: Any):
    if a is None or b is None:
        return None
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(fa) and np.isfinite(fb)):
        return None
    return abs(fb - fa) / (abs(fa) + 1e-12)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    old = _load(LEGACY_PYC, "legacy_flow_cmp")
    new = _load(FLOW_SCRIPT, "new_flow_cmp")
    df = pd.read_parquet(FE09)

    rows: List[Dict[str, Any]] = []
    for i, row in df.iterrows():
        try:
            o = old.extract_features_for_candidate(row)
        except Exception as exc:                      # noqa: BLE001
            o = {"__error__": f"{type(exc).__name__}:{exc}"}
        try:
            n = new.extract_features_for_candidate(row)
        except Exception as exc:                      # noqa: BLE001
            n = {"__error__": f"{type(exc).__name__}:{exc}"}

        rec: Dict[str, Any] = {
            "candidate_id": row["candidate_id"],
            "symbol": row["symbol"],
            "datetime_utc": row["datetime_utc"],
            "snapshot_time_ms": int(row["snapshot_time_ms"]),
            "old_error": o.get("__error__"),
            "new_error": n.get("__error__"),
            "old_coverage": o.get("coverage_status"),
            "new_coverage": n.get("coverage_status"),
            "new_pit_status": n.get("pit_status"),
            "new_pit_qualified": n.get("pit_qualified"),
            "new_formal_eligible": n.get("formal_eligible"),
            "new_bar_open_time_ms": n.get("bar_open_time_ms"),
        }
        # 旧实现不输出 bar_open_time_ms，只能反查它到底取了哪一根：
        # 把 open_time == 决策时刻 的那根拿来比 close/volume，一致即证明它读了"决策才开始"的整根。
        ts = int(row["snapshot_time_ms"])
        try:
            dfc = old.load_symbol_data(row["symbol"], "candles", "1h")
            sel = dfc[dfc["open_time"] == ts]
            if len(sel) and o.get("bar_close") is not None:
                b = sel.iloc[-1]
                rec["old_picked_bar_starting_at_decision"] = bool(
                    abs(float(b["close"]) - float(o["bar_close"])) < 1e-9
                    and abs(float(b["volume"]) - float(o["bar_volume"])) < 1e-9)
            else:
                rec["old_picked_bar_starting_at_decision"] = None
        except Exception:                              # noqa: BLE001
            rec["old_picked_bar_starting_at_decision"] = None
        rec["new_picked_previous_hour_bar"] = bool(
            n.get("bar_open_time_ms") == ts - 3_600_000)
        for k in COMPARE:
            rec[f"old_{k}"] = o.get(k)
            rec[f"new_{k}"] = n.get(k)
            rec[f"rel_{k}"] = _rel(o.get(k), n.get(k))
        for g in ("pass_g2", "pass_g3", "pass_g4"):
            rec[f"old_{g}"] = o.get(g)
            rec[f"new_{g}"] = n.get(g)
            rec[f"flip_{g}"] = (o.get(g) != n.get(g))
        rows.append(rec)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(df)}", flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, "compare_old_new_227.csv"), index=False, encoding="utf-8")

    ok = out[out["old_error"].isna() & out["new_error"].isna()]
    summary = {
        "candidates": int(len(out)),
        "errors_old": int(out["old_error"].notna().sum()),
        "errors_new": int(out["new_error"].notna().sum()),
        "old_coverage_counts": out["old_coverage"].value_counts().to_dict(),
        "new_coverage_counts": out["new_coverage"].value_counts().to_dict(),
        "new_pit_status_counts": out["new_pit_status"].value_counts().to_dict(),
        "new_pit_qualified": int(out["new_pit_qualified"].fillna(False).sum()),
        "new_formal_eligible": int(out["new_formal_eligible"].fillna(False).sum()),
        # 旧实现取的是"决策时刻刚开始"的那一根，新实现取上一根已收盘
        "old_picked_bar_starting_at_decision": int(
            out["old_picked_bar_starting_at_decision"].fillna(False).sum()),
        "new_picked_previous_hour_bar": int(
            out["new_picked_previous_hour_bar"].fillna(False).sum()),
        "flip_pass_g2": int(ok["flip_pass_g2"].sum()),
        "flip_pass_g3": int(ok["flip_pass_g3"].sum()),
        "flip_pass_g4": int(ok["flip_pass_g4"].sum()),
        "rel_diff": {},
    }
    for k in COMPARE:
        col = ok[f"rel_{k}"].dropna()
        if len(col):
            summary["rel_diff"][k] = {
                "n": int(len(col)),
                "median": float(col.median()),
                "p90": float(col.quantile(0.9)),
                "max": float(col.max()),
                "changed_gt_1e-9": int((col > 1e-9).sum()),
            }
    with open(os.path.join(OUT, "compare_old_new_227_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

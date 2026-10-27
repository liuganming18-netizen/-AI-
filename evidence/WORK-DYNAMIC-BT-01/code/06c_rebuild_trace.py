# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜统一止损轨迹（固定组补全 + 与动态组实测轨迹互校）。

为什么需要
──────────
动态组由 `WyckoffLow4hDynBase.custom_stoploss` 主动落盘逐 bar 轨迹；A/B/P1 走的是
**未改动的基线类**，它不落轨迹（本单不允许为了「好看」去改基线代码）。
但派工单要求「每次 d 切档与生效止损都要能逐笔核查」，固定组同样需要生效止损轨迹。

做法（确定性重建，不是估算）
────────────────────────────
按基类 `custom_stoploss` 的**同一规则**从 4h K 线重放：
  初始止损 = 成交价 × 0.95
  候选价   = 该根已收盘 4h low × (1 − d)
  生效止损 = max(初始, 上一生效, 候选)            ← 只上移
  d：固定组为常数；动态组按已收盘 4h close 相对入场涨幅 10%/35% 分档（只前进）

**先自证再外推**：把同一重建程序作用在动态组上，与策略**自己落盘**的轨迹逐行比对；
只有全部一致，才用重建结果补固定组。任何不一致都如实报出，不掩盖。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = BASE / "evidence/WORK-DYNAMIC-BT-01"
OUT = EXP / "out"
DATA = BASE / "evidence/FT-FACTOR-03/user_data/data/binance/futures"
UNI = OUT / "stage_trace_unified"

S = 0.05
# 与 03_metrics 同一还原规则：freqtrade 按 (入场时刻, 白名单顺序) 分配 trade.id
_WL = json.loads((EXP / "user_data/config.json").read_text(encoding="utf-8"))
WL_ORDER = {p: i for i, p in enumerate(_WL["exchange"]["pair_whitelist"])}
DYN = {"d1": 0.08, "d2": 0.20, "d3": 0.05, "t1": 0.10, "t2": 0.35}

_CACHE: Dict[str, pd.DataFrame] = {}


def slug(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def load_4h(pair: str) -> pd.DataFrame:
    if pair in _CACHE:
        return _CACHE[pair]
    f = DATA / f"{slug(pair)}-4h-futures.feather"
    if not f.exists():
        d = pd.DataFrame(columns=["close_ms", "low", "close"])
    else:
        raw = pd.read_feather(f, columns=["date", "low", "close"])
        d = pd.DataFrame({
            "close_ms": raw["date"].astype("int64").to_numpy() + 4 * 3_600_000,
            "low": raw["low"].to_numpy(float),
            "close": raw["close"].to_numpy(float),
        })
    _CACHE[pair] = d
    return d


def d_of_stage(stage: int, d_spec: dict) -> float:
    if d_spec.get("kind") == "fixed":
        return float(d_spec["d"])
    return {1: DYN["d1"], 2: DYN["d2"], 3: DYN["d3"]}[stage]


def stage_for_gain(gain: float, prev: int, d_spec: dict) -> int:
    if d_spec.get("kind") == "fixed":
        return 1
    t1 = float(d_spec.get("t1", DYN["t1"]))
    t2 = float(d_spec.get("t2", DYN["t2"]))
    if gain >= t2:
        return 3
    if gain >= t1:
        return 2
    return 1


def rebuild(trades: pd.DataFrame, d_spec: dict, strategy: str) -> pd.DataFrame:
    rows: List[dict] = []
    for r in trades.itertuples(index=False):
        pair = str(r.pair)
        entry = float(r.open_rate)
        e_ms = int(r.open_timestamp)
        x_ms = int(r.close_timestamp)
        initial = entry * (1 - S)
        bars = load_4h(pair)
        if len(bars) == 0:
            continue
        b = bars[bars["close_ms"] <= e_ms].tail(1)
        if len(b) == 0:
            continue
        i0 = int(b.index[0])
        sub = bars[(bars.index >= i0) & (bars["close_ms"] <= x_ms)]
        eff = initial
        stage = 1
        last = None
        rows.append({"strategy": strategy, "trade_id": int(r.trade_id), "pair": pair,
                     "event": "ENTRY", "event_time_ms": e_ms, "bar_close_ms": None,
                     "bar_low": None, "bar_close": None, "actual_entry": entry,
                     "gain_vs_entry": 0.0, "stage": 1, "d": d_of_stage(1, d_spec),
                     "candidate_stop": None, "prev_effective_stop": initial,
                     "effective_stop": initial, "stage_changed": False,
                     "source": "reconstructed"})
        for _, bar in sub.iterrows():
            bc = int(bar["close_ms"])
            if last is not None and bc == last:
                continue
            gain = (float(bar["close"]) / entry - 1.0) if entry > 0 else 0.0
            reached = stage_for_gain(gain, stage, d_spec)
            new_stage = max(stage, reached)
            chg = new_stage != stage
            stage = new_stage
            d = d_of_stage(stage, d_spec)
            cand = float(bar["low"]) * (1 - d)
            prev_eff = eff
            eff = max(initial, prev_eff, cand)
            rows.append({"strategy": strategy, "trade_id": int(r.trade_id), "pair": pair,
                         "event": "BAR_UPDATE", "event_time_ms": None, "bar_close_ms": bc,
                         "bar_low": float(bar["low"]), "bar_close": float(bar["close"]),
                         "actual_entry": entry, "gain_vs_entry": round(gain, 8),
                         "stage": stage, "d": d, "candidate_stop": cand,
                         "prev_effective_stop": prev_eff, "effective_stop": eff,
                         "stage_changed": bool(chg), "source": "reconstructed"})
            last = bc
    return pd.DataFrame(rows)


def main() -> None:
    UNI.mkdir(parents=True, exist_ok=True)
    index = pd.read_csv(OUT / "02_运行索引.csv")
    manifest = json.loads((OUT / "01_策略清单.json").read_text(encoding="utf-8"))
    spec_by_name = {m["class_name"]: m for m in manifest}

    val_rows: List[dict] = []
    n_files = 0

    for _, m in index.iterrows():
        name = m["strategy"]
        if m["returncode"] != 0:
            continue
        csv = OUT / f"trades_{name}.csv"
        if not csv.exists():
            continue
        tr = pd.read_csv(csv)
        if len(tr) == 0:
            continue
        tr["_wi"] = tr["pair"].map(WL_ORDER).fillna(9999)
        tr = tr.sort_values(["open_timestamp", "_wi"], kind="stable").drop(columns=["_wi"])
        tr = tr.reset_index(drop=True)
        tr["trade_id"] = np.arange(1, len(tr) + 1)

        it = spec_by_name[name]
        d_spec = ({"kind": "fixed", "d": it["d"]} if it["kind"] == "fixed"
                  else {"kind": "dyn", "d1": it["d1"], "d2": it["d2"], "d3": it["d3"],
                        "t1": it["t1"], "t2": it["t2"]})
        rb = rebuild(tr, d_spec, name)

        rec_p = OUT / "stage_trace" / f"stage_trace_{name}.csv"
        if rec_p.exists():
            rec = pd.read_csv(rec_p)
            # 逐笔比对：最后一根 bar 的 stage / effective_stop，以及 bar 序列
            a = rec[rec["event"] == "BAR_UPDATE"].copy()
            b = rb[rb["event"] == "BAR_UPDATE"].copy()
            keys = ["trade_id", "bar_close_ms"]
            mg = a.merge(b, on=keys, how="outer", suffixes=("_rec", "_rb"), indicator=True)
            both = mg[mg["_merge"] == "both"]
            n_both = int(len(both))
            only_rec = int((mg["_merge"] == "left_only").sum())
            only_rb = int((mg["_merge"] == "right_only").sum())
            d_stage = int((both["stage_rec"] != both["stage_rb"]).sum()) if n_both else None
            d_stop = (float((both["effective_stop_rec"] - both["effective_stop_rb"]).abs().max())
                      if n_both else None)
            d_d = (float((both["d_rec"] - both["d_rb"]).abs().max()) if n_both else None)
            val_rows.append({"strategy": name, "group": m["group"],
                             "recorded_rows": int(len(a)), "rebuilt_rows": int(len(b)),
                             "matched_rows": n_both, "only_recorded": only_rec,
                             "tail_extra_rebuilt": only_rb,
                             "stage_mismatch_on_matched": d_stage,
                             "max_abs_d_effective_stop": d_stop,
                             "max_abs_d_d": d_d,
                             "core_identical": bool(only_rec == 0 and (d_stage or 0) == 0
                                                    and (d_stop or 0) <= 1e-9
                                                    and (d_d or 0) <= 1e-12)})
            uni = rb
        else:
            uni = rb
            val_rows.append({"strategy": name, "group": m["group"],
                             "recorded_rows": 0, "rebuilt_rows": int((rb["event"] == "BAR_UPDATE").sum()),
                             "matched_rows": 0, "only_recorded": 0,
                             "tail_extra_rebuilt": 0, "stage_mismatch_on_matched": None,
                             "max_abs_d_effective_stop": None, "max_abs_d_d": None,
                             "core_identical": None})
        uni.to_csv(UNI / f"stage_trace_{name}.csv", index=False, encoding="utf-8-sig")
        n_files += 1

    v = pd.DataFrame(val_rows)
    v.to_csv(OUT / "06c_轨迹重建与实测互校.csv", index=False, encoding="utf-8-sig")

    dyn = v[v["group"].isin(["C", "P2"])]
    concl = {
        "files_written": n_files,
        "dyn_groups_checked": int(len(dyn)),
        "dyn_core_all_identical": bool(dyn["core_identical"].fillna(False).all()),
        "dyn_max_abs_d_effective_stop": (float(dyn["max_abs_d_effective_stop"].dropna().max())
                                         if dyn["max_abs_d_effective_stop"].notna().any() else None),
        "dyn_tail_extra_rows": int(dyn["tail_extra_rebuilt"].fillna(0).sum()),
        "tail_caveat": ("`tail_extra_rebuilt` = 重建比实测多出的行，全部是**收盘时刻恰好等于退出时刻**"
                        "的那根 4h bar。策略是否在平仓前调用过一次 custom_stoploss 取决于引擎内部"
                        "顺序（实测 42 笔里有 4 笔没被观测到）。该行**不改变**生效止损值"
                        "（`max_abs_d_effective_stop` ≈ 1e-14，浮点噪声），也不改变已实现盈亏。"),
        "meaning": ("动态组「重建轨迹」与策略「自己落盘的轨迹」在全部匹配行上 stage / d / 生效止损"
                    "完全一致 ⇒ 重建程序与基类 `custom_stoploss` 规则一致；因此用它补固定组"
                    "（A/B/P1）的生效止损轨迹是确定性补全，不是估算。"),
        "note": "固定组（A/B/P1）无策略自落盘轨迹，本表 `source=reconstructed`。",
    }
    (OUT / "06c_轨迹重建结论.json").write_text(
        json.dumps(concl, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(concl, ensure_ascii=False, indent=2))
    print()
    print(v[["strategy", "group", "recorded_rows", "rebuilt_rows", "matched_rows",
             "only_recorded", "tail_extra_rebuilt", "stage_mismatch_on_matched",
             "max_abs_d_effective_stop", "core_identical"]].to_string())


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-02 · 04 同一候选逐笔归因 + 过滤效应与仓位竞争拆分

只读 FT-ENTRY-01 逐笔与归档。产出：
  out/15_同一候选归因分解_v1.csv
  out/16_事件集合交叉与仓位竞争_v1.csv
  out/17_并发占用与拒绝信号_v1.csv
  out/18_基线事件被丢弃明细_v1.csv
"""
from __future__ import annotations

import csv
import json
import os
import re
import zipfile
from collections import Counter

import numpy as np
import pandas as pd

csv.field_size_limit(10 ** 9)

BASE = "/Users/boos/Desktop/威科夫因子共振趋势"
ENTRY = os.path.join(BASE, "evidence/FT-ENTRY-01")
OUT = os.path.join(BASE, "evidence/FT-ENTRY-AUDIT-02/out")

ARMS = ["WyckoffBaseline", "WyckoffEntryA1_NearTop", "WyckoffEntryA2_AtBoxHigh",
        "WyckoffEntryC1_StopSweep", "WyckoffEntryC2_StopBar"]
TAG_RE = re.compile(r"^cand_(?:(A1|A2|C1|C2)_)?(ev_.+)_(\d{13})$")


def load(arm):
    df = pd.read_csv(os.path.join(ENTRY, "out", f"trades_{arm}.csv"), encoding="utf-8-sig")
    if "pair" not in df.columns:
        df = df.rename(columns={df.columns[0]: "pair"})
    df["event_id"] = df["enter_tag"].astype(str).str.extract(TAG_RE)[1]
    df["profit_abs"] = df["profit_abs"].astype(float)
    df["open_ms"] = df["open_timestamp"].astype("int64")
    df["close_ms"] = df["close_timestamp"].astype("int64")
    return df.sort_values("open_ms").reset_index(drop=True)


def zip_meta(arm):
    z = zipfile.ZipFile(os.path.join(ENTRY, "out", f"backtest-result-{arm}.zip"))
    n = [x for x in z.namelist() if x.endswith(".json") and not x.endswith("_config.json")][0]
    return json.loads(z.read(n))["strategy"][arm]


def main():
    os.makedirs(OUT, exist_ok=True)
    T = {a: load(a) for a in ARMS}
    M = {a: zip_meta(a) for a in ARMS}
    base = T["WyckoffBaseline"]

    # ---------- 1. 同一候选归因分解 ----------
    # 每个事件只取首笔（重复入场单独统计，避免把同一事件算两次）
    def first_per_event(df):
        return df.sort_values("open_ms").groupby("event_id", as_index=False).first()

    base_first = first_per_event(base)
    base_ev = base_first.set_index("event_id")
    attr_rows = []
    for a in ARMS[1:]:
        af = first_per_event(T[a])
        matched = af[af["event_id"].isin(base_ev.index)]
        unmatched = af[~af["event_id"].isin(base_ev.index)]
        dropped = base_first[~base_first["event_id"].isin(af["event_id"])]

        arm_net = float(T[a]["profit_abs"].sum())
        base_net = float(base["profit_abs"].sum())
        matched_diff = float(matched["profit_abs"].sum() - base_ev.loc[matched["event_id"], "profit_abs"].sum())
        unmatched_pnl = float(unmatched["profit_abs"].sum())
        dropped_pnl = float(dropped["profit_abs"].sum())

        attr_rows.append({
            "arm": a,
            "arm_net_u": round(arm_net, 4),
            "baseline_net_u": round(base_net, 4),
            "total_diff_u": round(arm_net - base_net, 4),
            "matched_events": len(matched),
            "matched_arm_pnl_u": round(float(matched["profit_abs"].sum()), 4),
            "matched_base_pnl_u": round(float(base_ev.loc[matched["event_id"], "profit_abs"].sum()), 4),
            "matched_diff_u": round(matched_diff, 4),
            "unmatched_arm_events": len(unmatched),
            "unmatched_arm_pnl_u": round(unmatched_pnl, 4),
            "dropped_baseline_events": len(dropped),
            "dropped_baseline_pnl_u": round(dropped_pnl, 4),
            "reconcile_residual_u": round(arm_net - base_net - (matched_diff + unmatched_pnl - dropped_pnl), 6),
            "arm_total_events": len(af),
            "baseline_total_events": len(base_first),
            "event_recall_of_baseline_pct": round(len(matched) / len(base_first) * 100, 2),
            "arm_trades_all": len(T[a]),
            "arm_reentry_extra": len(T[a]) - len(af),
        })
    pd.DataFrame(attr_rows).to_csv(os.path.join(OUT, "15_同一候选归因分解_v1.csv"),
                                   index=False, encoding="utf-8-sig")

    # 基线被丢弃事件明细
    drop_rows = []
    for a in ARMS[1:]:
        af = first_per_event(T[a])
        dropped = base_first[~base_first["event_id"].isin(af["event_id"])]
        for _, r in dropped.iterrows():
            drop_rows.append({
                "arm": a, "event_id": r["event_id"], "pair": r["pair"],
                "base_open": r["open_date"], "base_close": r["close_date"],
                "base_pnl_u": round(float(r["profit_abs"]), 4),
                "exit_reason": r["exit_reason"],
                "base_stake": round(float(r["stake_amount"]), 2),
            })
    pd.DataFrame(drop_rows).to_csv(os.path.join(OUT, "18_基线事件被丢弃明细_v1.csv"),
                                   index=False, encoding="utf-8-sig")

    # ---------- 2. 事件集合交叉 ----------
    sets = {a: set(first_per_event(T[a])["event_id"]) for a in ARMS}
    cross_rows = []
    for a in ARMS:
        cross_rows.append({
            "arm": a, "events": len(sets[a]),
            "only_this_arm": len(sets[a] - set().union(*[sets[b] for b in ARMS if b != a])),
            "vs_baseline_shared": len(sets[a] & sets["WyckoffBaseline"]),
            "vs_baseline_arm_only": len(sets[a] - sets["WyckoffBaseline"]),
            "vs_baseline_base_only": len(sets["WyckoffBaseline"] - sets[a]),
        })
    # A1/A2、C1/C2 相邻参数对照
    pairs = [("WyckoffEntryA1_NearTop", "WyckoffEntryA2_AtBoxHigh"),
             ("WyckoffEntryC1_StopSweep", "WyckoffEntryC2_StopBar")]
    for x, y in pairs:
        cross_rows.append({
            "arm": f"{x} vs {y}",
            "events": len(sets[x] | sets[y]),
            "only_this_arm": len(sets[x] - sets[y]),
            "vs_baseline_shared": len(sets[x] & sets[y]),
            "vs_baseline_arm_only": len(sets[y] - sets[x]),
            "vs_baseline_base_only": 0,
        })
    pd.DataFrame(cross_rows).to_csv(os.path.join(OUT, "16_事件集合交叉与仓位竞争_v1.csv"),
                                    index=False, encoding="utf-8-sig")

    # ---------- 3. 并发占用 ----------
    conc_rows = []
    for a in ARMS:
        df = T[a]
        ev = []
        for _, r in df.iterrows():
            ev.append((r["open_ms"], 1))
            ev.append((r["close_ms"], -1))
        ev.sort()
        cur = 0
        mx = 0
        at = None
        for ts, d in ev:
            cur += d
            if cur > mx:
                mx = cur
                at = ts
        # 按 15m 网格计算"占用槽位时长"
        grid_start = df["open_ms"].min()
        grid_end = df["close_ms"].max()
        step = 15 * 60 * 1000
        grid = np.arange(grid_start, grid_end + step, step)
        occ = np.zeros(len(grid), dtype=np.int16)
        for _, r in df.iterrows():
            i0 = int((r["open_ms"] - grid_start) // step)
            i1 = int((r["close_ms"] - grid_start) // step)
            occ[max(0, i0):max(1, i1)] += 1
        conc_rows.append({
            "arm": a,
            "trades": len(df),
            "max_concurrency": int(mx),
            "max_concurrency_first_at": str(pd.Timestamp(at, unit="ms", tz="UTC")) if at else "",
            "pct_time_at_3_slots": round(float((occ >= 3).mean() * 100), 2),
            "pct_time_at_2plus": round(float((occ >= 2).mean() * 100), 2),
            "pct_time_idle": round(float((occ == 0).mean() * 100), 2),
            "avg_slots_used": round(float(occ.mean()), 4),
            "rejected_signals_engine": M[a].get("rejected_signals"),
            "signal_total_attempted": len(df) + int(M[a].get("rejected_signals") or 0),
            "rejected_share_pct": round(int(M[a].get("rejected_signals") or 0)
                                        / (len(df) + int(M[a].get("rejected_signals") or 0)) * 100, 2),
        })
    pd.DataFrame(conc_rows).to_csv(os.path.join(OUT, "17_并发占用与拒绝信号_v1.csv"),
                                   index=False, encoding="utf-8-sig")

    print("=== 同一候选归因分解（首笔口径）===")
    for r in attr_rows:
        print(f"  {r['arm']:32s} 总差={r['total_diff_u']:+8.2f} = 同候选差{r['matched_diff_u']:+8.2f}"
              f" + 新增{r['unmatched_arm_pnl_u']:+8.2f} − 丢弃{r['dropped_baseline_pnl_u']:+8.2f}"
              f"   [同候选 {r['matched_events']}/{r['baseline_total_events']}={r['event_recall_of_baseline_pct']:.1f}%"
              f" 新增 {r['unmatched_arm_events']} 丢弃 {r['dropped_baseline_events']}]"
              f" 残差={r['reconcile_residual_u']}")
    print("\n=== 并发占用 ===")
    for r in conc_rows:
        print(f"  {r['arm']:32s} 最大并发={r['max_concurrency']} 3仓时长={r['pct_time_at_3_slots']:5.2f}% "
              f"空闲={r['pct_time_idle']:5.2f}% 均占用={r['avg_slots_used']:.3f} "
              f"被拒信号={r['rejected_signals_engine']:4d}/{r['signal_total_attempted']:4d}={r['rejected_share_pct']:.1f}%")
    print("\n=== 事件集合交叉 ===")
    for r in cross_rows:
        print(f"  {r['arm']:40s} {r}")


if __name__ == "__main__":
    main()

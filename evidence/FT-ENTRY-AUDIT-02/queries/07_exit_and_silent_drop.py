# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-02 · 07 退出原因分解 + 基线事件"静默丢弃"判别

产出：
  out/27_退出原因分解_v1.csv
  out/28_静默丢弃判别_v1.csv
  out/29_时点集中度_v1.csv
"""
from __future__ import annotations

import csv
import os
import re

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
    df["o"] = df["open_timestamp"].astype("int64")
    df["c"] = df["close_timestamp"].astype("int64")
    df["open_dt"] = pd.to_datetime(df["open_date"], utc=True)
    return df


def main():
    os.makedirs(OUT, exist_ok=True)
    T = {a: load(a) for a in ARMS}

    # ---------- 退出原因分解 ----------
    rows = []
    for a in ARMS:
        df = T[a]
        for reason, sub in df.groupby("exit_reason"):
            rows.append({
                "arm": a, "exit_reason": reason, "trades": len(sub),
                "share_pct": round(len(sub) / len(df) * 100, 2),
                "pnl_u": round(float(sub["profit_abs"].sum()), 4),
                "avg_pnl_u": round(float(sub["profit_abs"].mean()), 4),
                "win_rate_pct": round(float((sub["profit_abs"] > 0).mean() * 100), 2),
            })
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "27_退出原因分解_v1.csv"),
                              index=False, encoding="utf-8-sig")

    # ---------- 静默丢弃判别 ----------
    base = T["WyckoffBaseline"]
    drop = pd.read_csv(os.path.join(OUT, "18_基线事件被丢弃明细_v1.csv"), encoding="utf-8-sig")
    drows = []
    for a in ARMS[1:]:
        arm = T[a]
        sub = drop[drop["arm"] == a]
        for _, r in sub.iterrows():
            t = int(pd.Timestamp(r["base_open"]).timestamp() * 1000)
            occ = int(((arm["o"] <= t) & (arm["c"] > t)).sum())
            drows.append({
                "arm": a, "pair": r["pair"], "base_open": r["base_open"],
                "base_pnl_u": round(float(r["base_pnl_u"]), 4),
                "arm_open_positions_at_that_moment": occ,
                "can_be_slot_competition": bool(occ >= 3),
                "verdict": "可能是槽位挤占" if occ >= 3 else "槽位未满 → 只能是规则/成交窗静默丢弃",
            })
    pd.DataFrame(drows).to_csv(os.path.join(OUT, "28_静默丢弃判别_v1.csv"),
                               index=False, encoding="utf-8-sig")

    # ---------- 时点集中度（按平仓季度）----------
    qrows = []
    for a in ARMS:
        df = T[a]
        df = df.assign(q=df["open_dt"].dt.to_period("Q").astype(str))
        net = float(df["profit_abs"].sum())
        g = df.groupby("q")["profit_abs"].sum().sort_values(ascending=False)
        qrows.append({
            "arm": a, "quarters_with_trades": int((df.groupby("q").size() > 0).sum()),
            "total_quarters": int(df["q"].nunique()),
            "best_quarter": g.index[0] if len(g) else "",
            "best_quarter_u": round(float(g.iloc[0]), 4) if len(g) else 0.0,
            "worst_quarter": g.index[-1] if len(g) else "",
            "worst_quarter_u": round(float(g.iloc[-1]), 4) if len(g) else 0.0,
            "net_u": round(net, 4),
            "net_ex_best_quarter": round(net - (float(g.iloc[0]) if len(g) else 0.0), 4),
            "positive_quarters": int((g > 0).sum()),
            "negative_quarters": int((g < 0).sum()),
        })
    pd.DataFrame(qrows).to_csv(os.path.join(OUT, "29_时点集中度_v1.csv"),
                               index=False, encoding="utf-8-sig")

    print("=== 退出原因分解 ===")
    for a in ARMS:
        s = [r for r in rows if r["arm"] == a]
        txt = " | ".join(f"{r['exit_reason']}: {r['trades']}笔 {r['share_pct']:.1f}% {r['pnl_u']:+.2f}U"
                         for r in sorted(s, key=lambda x: -x["trades"]))
        print(f"  {a:32s} {txt}")
    print("\n=== 静默丢弃判别 ===")
    dd = pd.DataFrame(drows)
    for a in ARMS[1:]:
        s = dd[dd["arm"] == a]
        print(f"  {a:32s} 丢弃 {len(s)} 笔，其中槽位已满(≥3)的 {int(s['can_be_slot_competition'].sum())} 笔，"
              f"槽位未满的 {int((~s['can_be_slot_competition']).sum())} 笔，合计 {s['base_pnl_u'].sum():+.2f}U")
    print("\n=== 时点集中度 ===")
    for r in qrows:
        print(f"  {r['arm']:32s} 最好季={r['best_quarter']}({r['best_quarter_u']:+.2f}) "
              f"最差季={r['worst_quarter']}({r['worst_quarter_u']:+.2f}) "
              f"正季={r['positive_quarters']} 负季={r['negative_quarters']} 去最好季={r['net_ex_best_quarter']:+.2f}")


if __name__ == "__main__":
    main()

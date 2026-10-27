# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-02 · 05 集中度、跨币、跨时间与相邻参数稳健性

只读 FT-ENTRY-01 逐笔。产出：
  out/19_集中度与留一法_v1.csv
  out/20_年度与季度切片_v1.csv
  out/21_入场价优势分布_v1.csv
  out/22_相邻参数对照_v1.csv
  out/23_逐币留一影响_v1.csv
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
    df["open_dt"] = pd.to_datetime(df["open_date"], utc=True)
    df["close_dt"] = pd.to_datetime(df["close_date"], utc=True)
    df["hold_h"] = (df["close_dt"] - df["open_dt"]).dt.total_seconds() / 3600
    return df


def main():
    os.makedirs(OUT, exist_ok=True)
    T = {a: load(a) for a in ARMS}
    base = T["WyckoffBaseline"]
    base_ev = base.groupby("event_id").first()

    # ---------- 1. 集中度与留一法 ----------
    rows = []
    for a in ARMS:
        df = T[a]
        p = df["profit_abs"].sort_values(ascending=False).reset_index(drop=True)
        net = float(p.sum())
        by_sym = df.groupby("pair")["profit_abs"].sum().sort_values(ascending=False)
        by_ev = df.groupby("event_id")["profit_abs"].sum().sort_values(ascending=False)
        rows.append({
            "arm": a, "trades": len(df), "symbols": df["pair"].nunique(),
            "net_u": round(net, 4),
            "top1_trade_u": round(float(p.iloc[0]), 4),
            "top1_share_of_net_pct": round(float(p.iloc[0]) / net * 100, 2) if net != 0 else float("nan"),
            "top3_trades_u": round(float(p.head(3).sum()), 4),
            "net_ex_top1": round(net - float(p.iloc[0]), 4),
            "net_ex_top3": round(net - float(p.head(3).sum()), 4),
            "top1_symbol": by_sym.index[0] if len(by_sym) else "",
            "top1_symbol_u": round(float(by_sym.iloc[0]), 4) if len(by_sym) else 0.0,
            "net_ex_top1_symbol": round(net - float(by_sym.iloc[0]), 4) if len(by_sym) else net,
            "top3_symbols_share_pct": round(float(by_sym.head(3).sum()) / abs(net) * 100, 2) if net != 0 else float("nan"),
            "win_rate_pct": round(float((df["profit_abs"] > 0).mean() * 100), 2),
            "expectancy_u": round(net / len(df), 4),
            "avg_hold_h": round(float(df["hold_h"].mean()), 2),
            "median_hold_h": round(float(df["hold_h"].median()), 2),
            "exit_timeout_share_pct": round(float((df["exit_reason"] == "timeout_240h").mean() * 100), 2),
            "exit_stop_share_pct": round(float((df["exit_reason"] == "stop_loss").mean() * 100), 2),
        })
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "19_集中度与留一法_v1.csv"),
                              index=False, encoding="utf-8-sig")

    # ---------- 2. 逐币留一 ----------
    sym_rows = []
    all_pairs = sorted(set().union(*[set(T[a]["pair"]) for a in ARMS]))
    for a in ARMS:
        df = T[a]
        net = float(df["profit_abs"].sum())
        for s in all_pairs:
            sub = df[df["pair"] == s]
            if len(sub) == 0:
                continue
            sym_rows.append({
                "arm": a, "pair": s, "trades": len(sub),
                "pnl_u": round(float(sub["profit_abs"].sum()), 4),
                "net_ex_symbol_u": round(net - float(sub["profit_abs"].sum()), 4),
                "sign_flip_if_dropped": int((net > 0) != (net - float(sub["profit_abs"].sum()) > 0)),
            })
    pd.DataFrame(sym_rows).to_csv(os.path.join(OUT, "23_逐币留一影响_v1.csv"),
                                  index=False, encoding="utf-8-sig")

    # ---------- 3. 年度 / 季度切片 ----------
    yr_rows = []
    for a in ARMS:
        df = T[a]
        for (y, q), sub in df.groupby([df["open_dt"].dt.year, df["open_dt"].dt.quarter]):
            yr_rows.append({
                "arm": a, "period_type": "quarter", "period": f"{y}Q{q}",
                "trades": len(sub), "net_u": round(float(sub["profit_abs"].sum()), 4),
                "win_rate_pct": round(float((sub["profit_abs"] > 0).mean() * 100), 2),
                "expectancy_u": round(float(sub["profit_abs"].mean()), 4),
            })
        for y, sub in df.groupby(df["open_dt"].dt.year):
            yr_rows.append({
                "arm": a, "period_type": "year", "period": str(y),
                "trades": len(sub), "net_u": round(float(sub["profit_abs"].sum()), 4),
                "win_rate_pct": round(float((sub["profit_abs"] > 0).mean() * 100), 2),
                "expectancy_u": round(float(sub["profit_abs"].mean()), 4),
            })
    pd.DataFrame(yr_rows).to_csv(os.path.join(OUT, "20_年度与季度切片_v1.csv"),
                                 index=False, encoding="utf-8-sig")

    # ---------- 4. 入场价优势分布（同一候选） ----------
    adv_rows = []
    for a in ARMS[1:]:
        af = T[a].groupby("event_id").first()
        common = af.index.intersection(base_ev.index)
        adv = []
        for e in common:
            ae = float(af.loc[e, "open_rate"])
            be = float(base_ev.loc[e, "open_rate"])
            adv.append((ae - be) / be * 100)
        adv = np.array(adv)
        adv_rows.append({
            "arm": a, "matched_events": len(adv),
            "mean_price_advantage_pct": round(float(-adv.mean()), 3),
            "median_price_advantage_pct": round(float(-np.median(adv)), 3),
            "min_adv_pct": round(float(-adv.max()), 3),
            "max_adv_pct": round(float(-adv.min()), 3),
            "share_cheaper_pct": round(float((adv < 0).mean() * 100), 2),
            "share_cheaper_or_equal_pct": round(float((adv <= 0).mean() * 100), 2),
            "matched_arm_net_u": round(float(af.loc[common, "profit_abs"].sum()), 4),
            "matched_base_net_u": round(float(base_ev.loc[common, "profit_abs"].sum()), 4),
        })
    pd.DataFrame(adv_rows).to_csv(os.path.join(OUT, "21_入场价优势分布_v1.csv"),
                                  index=False, encoding="utf-8-sig")

    # ---------- 5. 相邻参数对照 ----------
    # A1(H-0.5A) vs A2(H)；C1(S-0.1A) vs C2(low-0.1A)
    adj_rows = []
    for x, y in [("WyckoffEntryA1_NearTop", "WyckoffEntryA2_AtBoxHigh"),
                 ("WyckoffEntryC1_StopSweep", "WyckoffEntryC2_StopBar")]:
        ex = set(T[x]["event_id"])
        ey = set(T[y]["event_id"])
        adj_rows.append({
            "comparison": f"{x} vs {y}",
            "x_trades": len(T[x]), "y_trades": len(T[y]),
            "x_events": len(ex), "y_events": len(ey),
            "shared_events": len(ex & ey),
            "x_only_events": len(ex - ey), "y_only_events": len(ey - ex),
            "jaccard": round(len(ex & ey) / len(ex | ey), 4) if ex | ey else 0.0,
            "x_net_u": round(float(T[x]["profit_abs"].sum()), 4),
            "y_net_u": round(float(T[y]["profit_abs"].sum()), 4),
            "x_win_rate_pct": round(float((T[x]["profit_abs"] > 0).mean() * 100), 2),
            "y_win_rate_pct": round(float((T[y]["profit_abs"] > 0).mean() * 100), 2),
            "x_avg_hold_h": round(float(T[x]["hold_h"].mean()), 2),
            "y_avg_hold_h": round(float(T[y]["hold_h"].mean()), 2),
        })
    # 同一事件的收益差（仅共有事件，首笔口径）
    for x, y in [("WyckoffEntryA1_NearTop", "WyckoffEntryA2_AtBoxHigh"),
                 ("WyckoffEntryC1_StopSweep", "WyckoffEntryC2_StopBar")]:
        fx = T[x].groupby("event_id").first()
        fy = T[y].groupby("event_id").first()
        sh = fx.index.intersection(fy.index)
        d = fx.loc[sh, "profit_abs"] - fy.loc[sh, "profit_abs"]
        adj_rows.append({
            "comparison": f"{x} − {y}（共有事件逐笔差）",
            "x_trades": len(sh), "y_trades": len(sh),
            "x_events": len(sh), "y_events": len(sh),
            "shared_events": len(sh),
            "x_only_events": 0, "y_only_events": 0, "jaccard": 1.0,
            "x_net_u": round(float(d.sum()), 4),
            "y_net_u": round(float(d.mean()), 4),
            "x_win_rate_pct": round(float((d > 0).mean() * 100), 2),
            "y_win_rate_pct": 0.0,
            "x_avg_hold_h": 0.0, "y_avg_hold_h": 0.0,
        })
    pd.DataFrame(adj_rows).to_csv(os.path.join(OUT, "22_相邻参数对照_v1.csv"),
                                  index=False, encoding="utf-8-sig")

    print("=== 集中度与留一法 ===")
    for r in rows:
        print(f"  {r['arm']:32s} net={r['net_u']:+9.2f} 最大单={r['top1_trade_u']:+8.2f} "
              f"去最大单={r['net_ex_top1']:+9.2f} 去前三={r['net_ex_top3']:+9.2f} "
              f"最大币={r['top1_symbol']}({r['top1_symbol_u']:+.2f}) 去该币={r['net_ex_top1_symbol']:+9.2f} "
              f"持仓中位={r['median_hold_h']:.1f}h 超时占={r['exit_timeout_share_pct']:.1f}%")
    print("\n=== 入场价优势 ===")
    for r in adv_rows:
        print(f"  {r['arm']:32s} 同候选={r['matched_events']:2d} 均优势={r['mean_price_advantage_pct']:6.3f}% "
              f"中位={r['median_price_advantage_pct']:6.3f}% 更便宜占比={r['share_cheaper_or_equal_pct']:6.2f}% "
              f"同候选净={r['matched_arm_net_u']:+8.2f} vs 基线 {r['matched_base_net_u']:+8.2f}")
    print("\n=== 相邻参数 ===")
    for r in adj_rows:
        print(f"  {r['comparison'][:60]:60s} {r}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-02 · 09 稳健性阶梯（逐级剔除极端值后的净收益与相对基准差）

产出：
  out/34_稳健性阶梯_v1.csv
  out/35_年度差异_v1.csv
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


def load(a):
    df = pd.read_csv(os.path.join(ENTRY, "out", f"trades_{a}.csv"), encoding="utf-8-sig")
    if "pair" not in df.columns:
        df = df.rename(columns={df.columns[0]: "pair"})
    df["profit_abs"] = df["profit_abs"].astype(float)
    df["open_dt"] = pd.to_datetime(df["open_date"], utc=True)
    return df


def cuts(df):
    """返回若干剔除口径下的净收益。"""
    p = df["profit_abs"]
    net = float(p.sum())
    top = p.sort_values(ascending=False).reset_index(drop=True)
    by_sym = df.groupby("pair")["profit_abs"].sum().sort_values(ascending=False)
    by_year = df.groupby(df["open_dt"].dt.year)["profit_abs"].sum().sort_values(ascending=False)
    return {
        "raw": net,
        "ex_top1": net - float(top.iloc[0]),
        "ex_top3": net - float(top.head(3).sum()),
        "ex_top5": net - float(top.head(5).sum()),
        "ex_top_symbol": net - float(by_sym.iloc[0]),
        "ex_best_year": net - float(by_year.iloc[0]),
        "ex_worst_year": net - float(by_year.iloc[-1]),
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    T = {a: load(a) for a in ARMS}
    base = cuts(T["WyckoffBaseline"])

    rows = []
    for a in ARMS:
        c = cuts(T[a])
        r = {"arm": a}
        for k, v in c.items():
            r[k] = round(v, 4)
        for k in c:
            r[f"diff_{k}_vs_base"] = round(c[k] - base[k], 4)
        rows.append(r)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "34_稳健性阶梯_v1.csv"),
                              index=False, encoding="utf-8-sig")

    # 年度差异
    yrows = []
    years = sorted(set().union(*[set(T[a]["open_dt"].dt.year.unique()) for a in ARMS]))
    for y in years:
        r = {"year": int(y)}
        for a in ARMS:
            sub = T[a][T[a]["open_dt"].dt.year == y]
            r[f"net_{a}"] = round(float(sub["profit_abs"].sum()), 4)
            r[f"n_{a}"] = len(sub)
        for a in ARMS[1:]:
            r[f"diff_{a}_vs_base"] = round(r[f"net_{a}"] - r["net_WyckoffBaseline"], 4)
        yrows.append(r)
    pd.DataFrame(yrows).to_csv(os.path.join(OUT, "35_年度差异_v1.csv"),
                               index=False, encoding="utf-8-sig")

    print("=== 稳健性阶梯 ===")
    hdr = f"  {'arm':32s} {'raw':>9s} {'ex_top1':>9s} {'ex_top3':>9s} {'ex_top5':>9s} {'ex_best_sym':>11s} {'ex_best_yr':>10s}"
    print(hdr)
    for r in rows:
        print(f"  {r['arm']:32s} {r['raw']:+9.2f} {r['ex_top1']:+9.2f} {r['ex_top3']:+9.2f} "
              f"{r['ex_top5']:+9.2f} {r['ex_top_symbol']:+11.2f} {r['ex_best_year']:+10.2f}")
    print("\n=== 相对基准的差（同一剔除口径）===")
    for r in rows[1:]:
        print(f"  {r['arm']:32s} raw={r['diff_raw_vs_base']:+8.2f} ex_top1={r['diff_ex_top1_vs_base']:+8.2f} "
              f"ex_top3={r['diff_ex_top3_vs_base']:+8.2f} ex_top5={r['diff_ex_top5_vs_base']:+8.2f} "
              f"ex_best_sym={r['diff_ex_top_symbol_vs_base']:+8.2f} ex_best_yr={r['diff_ex_best_year_vs_base']:+8.2f}")
    print("\n=== 年度 ===")
    short = {a: a.replace("WyckoffEntry", "").replace("Wyckoff", "") for a in ARMS}
    for r in yrows:
        print(f"  {r['year']}  " + "  ".join(
            f"{short[a]:<14s}={r[f'net_{a}']:+8.2f}(n={r[f'n_{a}']})" for a in ARMS))


if __name__ == "__main__":
    main()

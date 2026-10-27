# -*- coding: utf-8 -*-
"""06 C 组机制诊断：最大有利/不利偏移（MFE/MAE）+ 网格梯度 + 退出结构。

用途：区分「止损太紧/成本太高（实现问题）」与「入场后根本没有跟随（方向问题）」。
不提出新阈值、不搜索新参数。
输出：out/06_*.csv
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, CELLS_A, CELLS_C, OUT, jdump, load_trades, write_csv,
)


def add_excursions(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    r = df["r_dist"].to_numpy()
    df["mfe_r"] = (df["max_rate"] - df["open_rate"]) / r
    df["mae_r"] = (df["open_rate"] - df["min_rate"]) / r
    df["mfe_pct"] = (df["max_rate"] / df["open_rate"] - 1) * 100
    df["mae_pct"] = (1 - df["min_rate"] / df["open_rate"]) * 100
    return df


def main() -> None:
    rows = []
    ref = [("BASE", BASELINE)] + [(c, s) for c, s, _, _ in CELLS_A] + [(c, s) for c, s, _, _ in CELLS_C]
    for code, strat in ref:
        df = add_excursions(load_trades(strat))
        losers = df[df["profit_abs"] <= 0]
        winners = df[df["profit_abs"] > 0]
        rows.append({
            "code": code, "strategy": strat,
            "trades": len(df),
            "loser_share_pct": round(100 * len(losers) / len(df), 2),
            "loser_median_mfe_r": round(float(losers["mfe_r"].median()), 3) if len(losers) else None,
            "loser_share_mfe_lt_0p5R": round(100 * float((losers["mfe_r"] < 0.5).mean()), 2) if len(losers) else None,
            "loser_share_mfe_lt_1R": round(100 * float((losers["mfe_r"] < 1.0).mean()), 2) if len(losers) else None,
            "loser_share_mfe_gt_1R": round(100 * float((losers["mfe_r"] > 1.0).mean()), 2) if len(losers) else None,
            "winner_median_mfe_r": round(float(winners["mfe_r"].median()), 3) if len(winners) else None,
            "winner_median_mae_r": round(float(winners["mae_r"].median()), 3) if len(winners) else None,
            "all_median_mfe_pct": round(float(df["mfe_pct"].median()), 3),
            "all_median_mae_pct": round(float(df["mae_pct"].median()), 3),
            "median_r_dist_pct": round(float((df["r_dist"] / df["open_rate"] * 100).median()), 3),
        })
    exc = pd.DataFrame(rows)
    write_csv(exc, "06_MFE_MAE诊断_v1.csv")
    print(exc.to_string(index=False))

    # C 网格梯度（独立重算，不引用对方）
    ks = [0.4, 0.6, 0.8]
    zs = [0.2, 0.4, 0.6]
    net = {}
    gross = {}
    for code, strat, k, z in CELLS_C:
        df = load_trades(strat)
        net[(k, z)] = float(df["profit_abs"].sum())
        gross[(k, z)] = float(df["gross_pnl"].sum())
    net_mat = pd.DataFrame([[round(net[(k, z)], 2) for z in zs] for k in ks],
                           index=[f"k_Vs={k}" for k in ks], columns=[f"Δz={z}A" for z in zs])
    gross_mat = pd.DataFrame([[round(gross[(k, z)], 2) for z in zs] for k in ks],
                             index=[f"k_Vs={k}" for k in ks], columns=[f"Δz={z}A" for z in zs])
    write_csv(net_mat.reset_index().rename(columns={"index": "k_vs"}), "06_C网格净收益_v1.csv")
    write_csv(gross_mat.reset_index().rename(columns={"index": "k_vs"}), "06_C网格毛利_v1.csv")
    print("\nC 网格净收益(U)\n", net_mat.to_string())
    print("\nC 网格毛利(U)\n", gross_mat.to_string())

    grad = []
    for i in range(len(ks) - 1):
        for z in zs:
            grad.append({"direction": "k_Vs", "from": ks[i], "to": ks[i + 1], "held_zone": z,
                         "net_step": round(net[(ks[i + 1], z)] - net[(ks[i], z)], 2),
                         "gross_step": round(gross[(ks[i + 1], z)] - gross[(ks[i], z)], 2)})
    for j in range(len(zs) - 1):
        for k in ks:
            grad.append({"direction": "zone", "from": zs[j], "to": zs[j + 1], "held_zone": k,
                         "net_step": round(net[(k, zs[j + 1])] - net[(k, zs[j])], 2),
                         "gross_step": round(gross[(k, zs[j + 1])] - gross[(k, zs[j])], 2)})
    gdf = pd.DataFrame(grad)
    gdf["sign_flip"] = False
    for direction, key in (("k_Vs", "held_zone"), ("zone", "held_zone")):
        sub = gdf[gdf["direction"] == direction]
        for _, g in sub.groupby(key):
            if (g["net_step"] > 0).any() and (g["net_step"] < 0).any():
                gdf.loc[g.index, "sign_flip"] = True
    write_csv(gdf, "06_C相邻梯度_v1.csv")
    print("\nC 相邻梯度\n", gdf.to_string(index=False))

    # 退出结构 × 毛利
    exit_rows = []
    for code, strat, _, _ in CELLS_C:
        df = load_trades(strat)
        for reason, g in df.groupby("exit_reason"):
            exit_rows.append({"code": code, "strategy": strat, "exit_reason": reason, "trades": len(g),
                              "net": round(float(g["profit_abs"].sum()), 2),
                              "gross": round(float(g["gross_pnl"].sum()), 2),
                              "avg_mfe_r": round(float(g["max_rate"].sub(g["open_rate"]).div(g["r_dist"]).mean()), 3),
                              "avg_mae_r": round(float(g["open_rate"].sub(g["min_rate"]).div(g["r_dist"]).mean()), 3)})
    write_csv(pd.DataFrame(exit_rows), "06_C退出结构_v1.csv")

    jdump(os.path.join(OUT, "06_机制诊断摘要_v1.json"), {
        "excursions": exc.to_dict("records"),
        "c_grid_net": {f"{k}_{z}": v for (k, z), v in net.items()},
        "c_grid_gross": {f"{k}_{z}": v for (k, z), v in gross.items()},
        "c_gradient": gdf.to_dict("records"),
        "definitions": {
            "mfe_r": "(该笔最高价 - 入场价) / 初始风险距离；>1R 表示曾达到 1 倍风险距离的浮盈",
            "mae_r": "(入场价 - 该笔最低价) / 初始风险距离",
            "r_dist": "由 stake=风险预算/r_dist*price 反推的初始风险距离（价格单位）",
        },
    })
    print("完成")


if __name__ == "__main__":
    main()

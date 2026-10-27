# -*- coding: utf-8 -*-
"""03 A 组稳健性：同事件归因 / 相邻参数 / 跨币 / 跨期 / 极值剔除 / 稳定区判据。

输出：out/03_*.csv
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, CELLS_A, OUT, jdump, load_trades, write_csv,
)

# 预先写死的稳定区判据（本脚本运行前确定，不事后调整）
STABLE_CRITERIA = {
    "C1_expectancy_gt_baseline": "每笔净期望 > 基线每笔净期望",
    "C2_drop_top3_positive": "剔除该组前 3 大盈利单后净收益仍 > 0",
    "C3_drop_top_coin_positive": "剔除最大贡献币后净收益仍 > 0",
    "C4_years_2of3_positive": "2022/2023/2024 至少 2 年为净正",
    "C5_neighbors_same_sign": "相邻 4 格（Δ±0.2A、k_vol±0.2）与本格同号且同为正",
    "C6_block_ci_lower_gt0": "相对基线的 30 天块配对 Bootstrap 95% CI 下界 > 0",
    "C7_top_coin_share_lt50": "最大贡献币占总净收益 < 50%",
}


def first_per_event(df: pd.DataFrame) -> pd.DataFrame:
    """同一候选若有多笔成交，事件级金额取该事件全部成交之和。"""
    out = df.groupby("event_id", as_index=True).agg(
        profit_abs=("profit_abs", "sum"),
        open_ts=("open_ts", "min"),
        open_rate=("open_rate", "mean"),
        pair=("pair", "first"),
    )
    return out.reset_index()


def main() -> None:
    base = load_trades(BASELINE)
    base_ev = first_per_event(base)
    base_map = base_ev.set_index("event_id")["profit_abs"].to_dict()

    cells = {}
    for code, strat, delta, kvol in CELLS_A:
        cells[code] = load_trades(strat)

    # ── 3.1 同事件归因 ─────────────────────────────────────────────────
    attr_rows = []
    for code, strat, delta, kvol in CELLS_A:
        df = cells[code]
        ev = first_per_event(df)
        ev_map = ev.set_index("event_id")["profit_abs"].to_dict()
        common = sorted(set(ev_map) & set(base_map))
        new_ev = sorted(set(ev_map) - set(base_map))
        missed = sorted(set(base_map) - set(ev_map))
        same_improve = sum(ev_map[e] - base_map[e] for e in common)
        new_pnl = sum(ev_map[e] for e in new_ev)
        dropped_pnl = sum(base_map[e] for e in missed)
        diff = float(df["profit_abs"].sum()) - float(base["profit_abs"].sum())
        # 同事件入场折价与时间前移
        discounts, shifts = [], []
        for e in common:
            a = ev[ev["event_id"] == e].iloc[0]
            b = base_ev[base_ev["event_id"] == e].iloc[0]
            discounts.append((a["open_rate"] / b["open_rate"] - 1) * 100)
            shifts.append((a["open_ts"] - b["open_ts"]) / 60000.0)
        attr_rows.append({
            "code": code, "strategy": strat,
            "net_A": round(float(df["profit_abs"].sum()), 4),
            "net_base": round(float(base["profit_abs"].sum()), 4),
            "total_diff": round(diff, 4),
            "common_events": len(common),
            "same_event_improve": round(same_improve, 4),
            "new_events": len(new_ev),
            "new_events_pnl": round(new_pnl, 4),
            "missed_baseline_events": len(missed),
            "missed_baseline_pnl": round(dropped_pnl, 4),
            "residual": round(diff - (same_improve + new_pnl - dropped_pnl), 6),
            "median_entry_discount_pct": round(float(np.median(discounts)), 3) if discounts else None,
            "median_time_shift_min": round(float(np.median(shifts)), 1) if shifts else None,
        })
    attr = pd.DataFrame(attr_rows)
    write_csv(attr, "03_同事件归因_v1.csv")
    print(attr.to_string(index=False))

    # ── 3.2 相邻参数网格 ───────────────────────────────────────────────
    grid_net = {}
    grid_exp = {}
    for code, strat, delta, kvol in CELLS_A:
        grid_net[(delta, kvol)] = float(cells[code]["profit_abs"].sum())
        grid_exp[(delta, kvol)] = float(cells[code]["profit_abs"].mean())
    deltas = [0.6, 0.4, 0.2]
    kvols = [0.8, 1.0, 1.2]

    net_mat = pd.DataFrame([[round(grid_net[(d, k)], 4) for k in kvols] for d in deltas],
                           index=[f"Δ={d}A" for d in deltas], columns=[f"k_vol={k}" for k in kvols])
    exp_mat = pd.DataFrame([[round(grid_exp[(d, k)], 4) for k in kvols] for d in deltas],
                           index=[f"Δ={d}A" for d in deltas], columns=[f"k_vol={k}" for k in kvols])
    write_csv(net_mat.reset_index().rename(columns={"index": "delta"}), "03_网格净收益矩阵_v1.csv")
    write_csv(exp_mat.reset_index().rename(columns={"index": "delta"}), "03_网格期望矩阵_v1.csv")

    grad_rows = []
    for i in range(len(deltas) - 1):
        for k in kvols:
            d_hi, d_lo = deltas[i], deltas[i + 1]
            grad_rows.append({
                "direction": "delta", "from": f"{d_hi}A", "to": f"{d_lo}A", "held": f"k_vol={k}",
                "net_step_u": round(grid_net[(d_lo, k)] - grid_net[(d_hi, k)], 4),
                "exp_step_u": round(grid_exp[(d_lo, k)] - grid_exp[(d_hi, k)], 4),
            })
    for j in range(len(kvols) - 1):
        for d in deltas:
            k_lo, k_hi = kvols[j], kvols[j + 1]
            grad_rows.append({
                "direction": "k_vol", "from": f"{k_lo}", "to": f"{k_hi}", "held": f"Δ={d}A",
                "net_step_u": round(grid_net[(d, k_hi)] - grid_net[(d, k_lo)], 4),
                "exp_step_u": round(grid_exp[(d, k_hi)] - grid_exp[(d, k_lo)], 4),
            })
    grad = pd.DataFrame(grad_rows)
    grad["sign_flip_within_direction"] = False
    for direction in ("delta", "k_vol"):
        sub = grad[grad["direction"] == direction]
        # 同一 held 档下若有正有负即视为翻符号
        for held, g in sub.groupby("held"):
            if (g["net_step_u"] > 0).any() and (g["net_step_u"] < 0).any():
                grad.loc[g.index, "sign_flip_within_direction"] = True
    write_csv(grad, "03_相邻参数梯度_v1.csv")
    print("\n净收益矩阵(U)\n", net_mat.to_string())
    print("\n相邻梯度\n", grad.to_string(index=False))

    # ── 3.3 跨币 ───────────────────────────────────────────────────────
    coin_rows = []
    for code, strat, _, _ in [("BASE", BASELINE, 0, 0)] + CELLS_A:
        df = base if code == "BASE" else cells[code]
        total = float(df["profit_abs"].sum())
        by_coin = df.groupby("pair")["profit_abs"].agg(["sum", "count"]).sort_values("sum", ascending=False)
        top_coin = by_coin.index[0] if len(by_coin) else None
        top_coin_pnl = float(by_coin["sum"].iloc[0]) if len(by_coin) else 0.0
        # 留一币：剔除某币后净收益转负的币数
        flip = 0
        for c in by_coin.index:
            if total - float(by_coin.loc[c, "sum"]) <= 0 < total:
                flip += 1
        coin_rows.append({
            "code": code, "strategy": strat,
            "coins_traded": int(df["pair"].nunique()),
            "trades": len(df),
            "top_coin": top_coin,
            "top_coin_pnl": round(top_coin_pnl, 4),
            "top_coin_share_pct": round(100 * top_coin_pnl / total, 2) if abs(total) > 1e-9 else None,
            "coins_whose_removal_flips_negative": flip,
            "drop_top_coin_net": round(total - top_coin_pnl, 4),
            "drop_top_coin_expectancy": round((total - top_coin_pnl) / max(len(df) - int(by_coin["count"].iloc[0]), 1), 4),
        })
    coin = pd.DataFrame(coin_rows)
    write_csv(coin, "03_跨币集中度_v1.csv")
    print("\n跨币\n", coin.to_string(index=False))

    # ── 3.4 跨期 ───────────────────────────────────────────────────────
    def year_slice(df):
        y = pd.to_datetime(df["close_ts"], unit="ms", utc=True).dt.year
        g = df.groupby(y)["profit_abs"].agg(["sum", "count"])
        return {int(k): (round(float(v["sum"]), 4), int(v["count"])) for k, v in g.iterrows()}

    year_rows = []
    for code, strat, _, _ in [("BASE", BASELINE, 0, 0)] + CELLS_A:
        df = base if code == "BASE" else cells[code]
        ys = year_slice(df)
        for yr in (2021, 2022, 2023, 2024):
            pnl, n = ys.get(yr, (0.0, 0))
            year_rows.append({"code": code, "strategy": strat, "year": yr, "trades": n, "net_pnl": pnl})
    yr_df = pd.DataFrame(year_rows)
    write_csv(yr_df, "03_分年_v1.csv")

    half_rows = []
    for code, strat, _, _ in [("BASE", BASELINE, 0, 0)] + CELLS_A:
        df = base if code == "BASE" else cells[code]
        dt = pd.to_datetime(df["close_ts"], unit="ms", utc=True)
        key = dt.dt.year.astype(str) + "H" + ((dt.dt.month - 1) // 6 + 1).astype(str)
        g = df.groupby(key)["profit_abs"].agg(["sum", "count"])
        for k, v in g.iterrows():
            half_rows.append({"code": code, "strategy": strat, "half": k, "trades": int(v["count"]),
                              "net_pnl": round(float(v["sum"]), 4)})
    half = pd.DataFrame(half_rows)
    write_csv(half, "03_分半年_v1.csv")

    # 分年方向一致性（相对基线）
    base_year = {r["year"]: r["net_pnl"] for _, r in yr_df[yr_df["code"] == "BASE"].iterrows()}
    dir_rows = []
    for code, strat, _, _ in CELLS_A:
        sub = yr_df[yr_df["code"] == code]
        diffs = {int(r["year"]): round(r["net_pnl"] - base_year[r["year"]], 4) for _, r in sub.iterrows()}
        pos = sum(1 for y in (2022, 2023, 2024) if diffs.get(y, 0) > 0)
        dir_rows.append({"code": code, "strategy": strat,
                         **{f"diff_{y}": diffs.get(y, 0.0) for y in (2021, 2022, 2023, 2024)},
                         "years_positive_vs_base_2022_2024": pos})
    dirs = pd.DataFrame(dir_rows)
    write_csv(dirs, "03_分年相对基线_v1.csv")
    print("\n分年相对基线\n", dirs.to_string(index=False))

    # ── 3.5 极值剔除阶梯 ───────────────────────────────────────────────
    ladder_rows = []
    for code, strat, _, _ in [("BASE", BASELINE, 0, 0)] + CELLS_A:
        df = (base if code == "BASE" else cells[code]).sort_values("profit_abs", ascending=False)
        n = len(df)
        total = float(df["profit_abs"].sum())
        ladder_rows.append({
            "code": code, "strategy": strat, "trades": n,
            "raw_net": round(total, 4),
            "raw_exp": round(total / n, 4),
            "drop_top1_net": round(total - float(df["profit_abs"].iloc[:1].sum()), 4),
            "drop_top1_exp": round((total - float(df["profit_abs"].iloc[:1].sum())) / max(n - 1, 1), 4),
            "drop_top3_net": round(total - float(df["profit_abs"].iloc[:3].sum()), 4),
            "drop_top3_exp": round((total - float(df["profit_abs"].iloc[:3].sum())) / max(n - 3, 1), 4),
            "drop_top5_net": round(total - float(df["profit_abs"].iloc[:5].sum()), 4),
            "drop_top5_exp": round((total - float(df["profit_abs"].iloc[:5].sum())) / max(n - 5, 1), 4),
        })
    ladder = pd.DataFrame(ladder_rows)
    write_csv(ladder, "03_极值剔除阶梯_v1.csv")

    # 配对口径：按 A 组前 k 大盈利事件，从两组同时剔除
    paired_rows = []
    for code, strat, _, _ in CELLS_A:
        ev = first_per_event(cells[code])
        order = ev.sort_values("profit_abs", ascending=False)["event_id"].tolist()
        for k in (1, 3, 5):
            drop = set(order[:k])
            a_net = float(cells[code].loc[~cells[code]["event_id"].isin(drop), "profit_abs"].sum())
            b_net = float(base.loc[~base["event_id"].isin(drop), "profit_abs"].sum())
            paired_rows.append({"code": code, "strategy": strat, "k": k,
                                "A_net_after": round(a_net, 4), "base_net_after": round(b_net, 4),
                                "diff_after": round(a_net - b_net, 4)})
    paired = pd.DataFrame(paired_rows)
    write_csv(paired, "03_配对剔除差异_v1.csv")
    print("\n极值剔除\n", ladder.to_string(index=False))
    print("\n配对剔除\n", paired.pivot(index='code', columns='k', values='diff_after').to_string())

    # ── 3.6 稳定区判据评估 ─────────────────────────────────────────────
    crit_rows = []
    for code, strat, delta, kvol in CELLS_A:
        df = cells[code]
        lad = ladder[ladder["code"] == code].iloc[0]
        cny = coin[coin["code"] == code].iloc[0]
        dy = dirs[dirs["code"] == code].iloc[0]
        # 相邻 4 格
        di = deltas.index(delta)
        ki = kvols.index(kvol)
        neighbors = []
        for dd in (-1, 1):
            if 0 <= di + dd < len(deltas):
                neighbors.append(grid_net[(deltas[di + dd], kvol)])
        for kk in (-1, 1):
            if 0 <= ki + kk < len(kvols):
                neighbors.append(grid_net[(delta, kvols[ki + kk])])
        crit_rows.append({
            "code": code, "strategy": strat, "delta": delta, "k_vol": kvol,
            "C1_expectancy_gt_baseline": bool(float(df["profit_abs"].mean()) > float(base["profit_abs"].mean())),
            "C2_drop_top3_positive": bool(lad["drop_top3_net"] > 0),
            "C3_drop_top_coin_positive": bool(cny["drop_top_coin_net"] > 0),
            "C4_years_2of3_positive": bool(int(dy["years_positive_vs_base_2022_2024"]) >= 2),
            "C5_neighbors_same_sign": bool(all(n > 0 for n in neighbors)),
            "C5_neighbor_min_net": round(float(min(neighbors)), 4) if neighbors else None,
            "C6_block_ci_lower_gt0": None,  # 由 04 回填
            "C7_top_coin_share_lt50": bool(cny["top_coin_share_pct"] is not None and cny["top_coin_share_pct"] < 50),
        })
    crit = pd.DataFrame(crit_rows)
    write_csv(crit, "03_稳定区判据_v1.csv")
    print("\n稳定区判据（C6 待 04 回填）\n", crit.to_string(index=False))

    jdump(os.path.join(OUT, "03_稳健性摘要_v1.json"), {
        "criteria_definition": STABLE_CRITERIA,
        "attribution": attr.to_dict("records"),
        "grid_net": {f"{d}_{k}": v for (d, k), v in grid_net.items()},
        "grid_exp": {f"{d}_{k}": v for (d, k), v in grid_exp.items()},
        "criteria_eval": crit.to_dict("records"),
    })
    print("完成")


if __name__ == "__main__":
    main()

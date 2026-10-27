# -*- coding: utf-8 -*-
"""05 收益来源分解：区分「胜率（准确率）提高」与「平均盈亏改善」。

工单要求：不能把平均盈亏改善带来的收益提高混称为准确率提升。
本脚本做三件事：

1. 恒等式拆解
   net = n × [ w·AW − (1−w)·AL ]
   其中 n=笔数、w=胜率、AW=平均盈利（正数）、AL=平均亏损（正数绝对值）。
2. 精确 Shapley 分解（4 个因子 n / w / AW / AL，枚举 4! = 24 种加入顺序取平均）
   ⇒ 每个因子对 Δnet（A − 基线）的边际贡献，顺序无关、加总严格等于 Δnet。
3. 胜率差的统计：Newcombe 混合评分 95% CI + Fisher 精确检验（双尾）。

输出：out/05_*.csv
"""
from __future__ import annotations

import itertools
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, CELLS_A, OUT, jdump, load_trades, wilson_ci, write_csv,
)


def net_of(n: float, w: float, aw: float, al: float) -> float:
    return n * (w * aw - (1 - w) * al)


def shapley_4(base: dict, cell: dict) -> dict:
    """4 因子精确 Shapley：枚举全部 24 种加入顺序。"""
    factors = ["n", "w", "aw", "al"]
    total = {f: 0.0 for f in factors}
    perms = list(itertools.permutations(factors))
    for perm in perms:
        cur = dict(base)
        prev_val = net_of(**cur)
        for f in perm:
            cur[f] = cell[f]
            new_val = net_of(**cur)
            total[f] += new_val - prev_val
            prev_val = new_val
    return {f: total[f] / len(perms) for f in factors}


def newcombe_diff_ci(x1: int, n1: int, x2: int, n2: int, z: float = 1.96):
    """两比例差 (p1 − p2) 的 Newcombe 混合评分 95% CI。"""
    p1, p2 = x1 / n1, x2 / n2
    l1, u1 = wilson_ci(x1, n1, z)
    l2, u2 = wilson_ci(x2, n2, z)
    diff = p1 - p2
    lo = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return lo, hi


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2×2 Fisher 精确检验（双尾）。a/b = 组1 胜/负，c/d = 组2 胜/负。"""
    n1, n2 = a + b, c + d
    k, n = a + c, n1 + n2
    def pmf(x):
        return math.comb(n1, x) * math.comb(n2, k - x) / math.comb(n, k)
    p_obs = pmf(a)
    lo = max(0, k - n2)
    hi = min(n1, k)
    return float(min(1.0, sum(pmf(x) for x in range(lo, hi + 1) if pmf(x) <= p_obs + 1e-12)))


def main() -> None:
    base = load_trades(BASELINE)
    nb = len(base)
    wb = float((base["profit_abs"] > 0).sum()) / nb
    awb = float(base.loc[base["profit_abs"] > 0, "profit_abs"].mean())
    alb = float(-base.loc[base["profit_abs"] < 0, "profit_abs"].mean())
    base_params = {"n": nb, "w": wb, "aw": awb, "al": alb}
    print(f"基线：n={nb} w={wb:.4f} AW={awb:.4f} AL={alb:.4f} "
          f"⇒ net={net_of(**base_params):.4f}（实际 {float(base['profit_abs'].sum()):.4f}）")

    # ── 5.1 参数与恒等式 ───────────────────────────────────────────────
    rows, shap_rows = [], []
    for code, strat, delta, kvol in CELLS_A:
        df = load_trades(strat)
        n = len(df)
        wins = int((df["profit_abs"] > 0).sum())
        losses = int((df["profit_abs"] < 0).sum())
        w = wins / n
        aw = float(df.loc[df["profit_abs"] > 0, "profit_abs"].mean())
        al = float(-df.loc[df["profit_abs"] < 0, "profit_abs"].mean())
        cell = {"n": n, "w": w, "aw": aw, "al": al}
        net_id = net_of(**cell)
        net_act = float(df["profit_abs"].sum())
        sh = shapley_4(base_params, cell)
        dnet = net_act - float(base["profit_abs"].sum())
        lo, hi = newcombe_diff_ci(wins, n, int((base["profit_abs"] > 0).sum()), nb)
        p_fish = fisher_exact_two_sided(wins, losses, int((base["profit_abs"] > 0).sum()),
                                       int((base["profit_abs"] < 0).sum()))
        rows.append({
            "code": code, "strategy": strat, "delta": delta, "k_vol": kvol,
            "n": n, "wins": wins, "losses": losses,
            "win_rate_pct": round(w * 100, 4),
            "win_rate_pp_vs_base": round((w - wb) * 100, 4),
            "win_rate_diff_ci_lo_pct": round(lo * 100, 4),
            "win_rate_diff_ci_hi_pct": round(hi * 100, 4),
            "win_rate_diff_significant": bool(lo > 0 or hi < 0),
            "fisher_p": round(p_fish, 4),
            "avg_win_u": round(aw, 4), "avg_win_ratio_vs_base": round(aw / awb, 4),
            "avg_loss_u": round(al, 4), "avg_loss_ratio_vs_base": round(al / alb, 4),
            "payoff_ratio": round(aw / al, 4),
            "breakeven_win_rate_pct": round(100 * al / (aw + al), 4),
            "net_identity": round(net_id, 4), "net_actual": round(net_act, 4),
            "identity_gap": round(net_id - net_act, 8),
            "delta_net_u": round(dnet, 4),
            "shapley_n_u": round(sh["n"], 4),
            "shapley_w_u": round(sh["w"], 4),
            "shapley_aw_u": round(sh["aw"], 4),
            "shapley_al_u": round(sh["al"], 4),
            "shapley_sum": round(sum(sh.values()), 4),
            "shapley_residual": round(sum(sh.values()) - dnet, 6),
        })
        shap_rows.append({
            "code": code, "strategy": strat, "delta": delta, "k_vol": kvol,
            "delta_net_u": round(dnet, 4),
            "from_trade_count_pct": round(100 * sh["n"] / dnet, 2) if abs(dnet) > 1e-9 else None,
            "from_win_rate_pct": round(100 * sh["w"] / dnet, 2) if abs(dnet) > 1e-9 else None,
            "from_avg_win_pct": round(100 * sh["aw"] / dnet, 2) if abs(dnet) > 1e-9 else None,
            "from_avg_loss_pct": round(100 * sh["al"] / dnet, 2) if abs(dnet) > 1e-9 else None,
            "win_rate_contribution_u": round(sh["w"], 4),
            "payoff_contribution_u": round(sh["aw"] + sh["al"], 4),
            "count_contribution_u": round(sh["n"], 4),
        })
    dec = pd.DataFrame(rows)
    write_csv(dec, "05_收益来源分解_v1.csv")
    print()
    print(dec[["code", "n", "win_rate_pct", "win_rate_pp_vs_base", "win_rate_diff_ci_lo_pct",
               "win_rate_diff_ci_hi_pct", "fisher_p", "avg_win_u", "avg_loss_u", "payoff_ratio",
               "breakeven_win_rate_pct", "delta_net_u"]].to_string(index=False))

    shap = pd.DataFrame(shap_rows)
    write_csv(shap, "05_Shapley分解_v1.csv")
    print("\nShapley 分解（U 与占 Δnet 百分比）")
    print(shap.to_string(index=False))

    # ── 5.2 事件分桶：重合事件 / 新增事件 / 被丢弃基线事件 ──────────────
    base_ev = base.groupby("event_id")["profit_abs"].sum()
    base_map = base_ev.to_dict()
    bucket_rows = []
    for code, strat, _, _ in CELLS_A:
        df = load_trades(strat)
        ev = df.groupby("event_id")["profit_abs"].sum()
        common = set(ev.index) & set(base_map)
        new_ev = set(ev.index) - set(base_map)
        missed = set(base_map) - set(ev.index)
        for label, idx, src in (("common", common, ev), ("new", new_ev, ev), ("missed", missed, base_ev)):
            sub = src.loc[sorted(idx)]
            wins = int((sub > 0).sum())
            bucket_rows.append({
                "code": code, "strategy": strat, "bucket": label,
                "events": int(len(sub)),
                "net_pnl": round(float(sub.sum()), 4),
                "expectancy": round(float(sub.mean()), 4) if len(sub) else None,
                "win_rate_pct": round(100 * wins / len(sub), 2) if len(sub) else None,
                "avg_win": round(float(sub[sub > 0].mean()), 4) if wins else None,
                "avg_loss": round(float(sub[sub < 0].mean()), 4) if (sub < 0).any() else None,
            })
    buckets = pd.DataFrame(bucket_rows)
    write_csv(buckets, "05_事件分桶_v1.csv")
    print("\n事件分桶（common=与基线同事件；new=A 独有事件；missed=基线有而 A 没有）")
    print(buckets.pivot(index="code", columns="bucket", values="net_pnl").to_string())

    # ── 5.3 结论用的判定 ───────────────────────────────────────────────
    any_wr_up = bool((dec["win_rate_pp_vs_base"] > 0).any())
    n_wr_up = int((dec["win_rate_pp_vs_base"] > 0).sum())
    n_wr_sig = int(dec["win_rate_diff_significant"].sum())
    payoff_up = int((dec["avg_win_ratio_vs_base"] > 1.2).sum())
    summary = {
        "baseline_params": {k: round(v, 6) for k, v in base_params.items()},
        "n_groups_win_rate_above_baseline": n_wr_up,
        "any_win_rate_above_baseline": any_wr_up,
        "max_win_rate_gain_pp": round(float(dec["win_rate_pp_vs_base"].max()), 4),
        "n_groups_win_rate_diff_significant": n_wr_sig,
        "n_groups_avg_win_ratio_gt_1_2": payoff_up,
        "max_avg_win_ratio": round(float(dec["avg_win_ratio_vs_base"].max()), 4),
        "identity_max_gap": float(dec["identity_gap"].abs().max()),
        "shapley_max_residual": float(dec["shapley_residual"].abs().max()),
        "verdict": ("胜率没有任何一组高于基线" if not any_wr_up else f"{n_wr_up} 组胜率高于基线"),
        "note": "净收益恒等式 net = n·[w·AW − (1−w)·AL]；Shapley 4 因子精确分解，24 种顺序平均，残差≈0",
    }
    jdump(os.path.join(OUT, "05_分解摘要_v1.json"), {
        "summary": summary,
        "decomposition": dec.to_dict("records"),
        "shapley": shap.to_dict("records"),
        "buckets": buckets.to_dict("records"),
    })
    print("\n" + "=" * 78)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("完成")


import json  # noqa: E402

if __name__ == "__main__":
    main()

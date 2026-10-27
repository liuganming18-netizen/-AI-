# -*- coding: utf-8 -*-
"""05 C 组失败归因：区分「当前实现不支持」与「提前回踩方向无效」。

只做分解，不提出新阈值、不搜索新参数。
输出：out/05_*.csv
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, CELLS_C, OUT, jdump, load_trades, metrics, write_csv,
)


def main() -> None:
    base = load_trades(BASELINE)
    base_ev = set(base["event_id"])
    base_map = base.groupby("event_id")["profit_abs"].sum().to_dict()

    rows = []
    split_rows = []
    arith_rows = []
    for code, strat, kvs, zone in CELLS_C:
        df = load_trades(strat)
        m = metrics(df)
        n = len(df)
        gross = float(df["gross_pnl"].sum())
        fee = float(df["fee_est"].sum())
        fund = float(df["funding"].sum())
        net = float(df["profit_abs"].sum())
        wins = df.loc[df["profit_abs"] > 0, "profit_abs"]
        losses = df.loc[df["profit_abs"] < 0, "profit_abs"]
        avg_win = float(wins.mean()) if len(wins) else float("nan")
        avg_loss = float(-losses.mean()) if len(losses) else float("nan")
        w = len(wins) / n if n else float("nan")
        need_payoff = (1 - w) / w if w > 0 else float("inf")
        actual_payoff = avg_win / avg_loss if avg_loss and avg_loss > 0 else float("nan")

        rows.append({
            "code": code, "strategy": strat, "k_vs": kvs, "zone": zone,
            "trades": n, "unique_events": m["unique_events"], "extra_fills": n - m["unique_events"],
            "win_rate": round(m["win_rate"] * 100, 2),
            "net_pnl": round(net, 4),
            "expectancy": round(net / n, 4),
            "gross_pnl": round(gross, 4),
            "gross_expectancy": round(gross / n, 4),
            "fee": round(fee, 4), "funding": round(fund, 4),
            "cost_total": round(fee - fund, 4),
            "fee_per_trade": round(fee / n, 4),
            "profit_factor": round(m["profit_factor"], 4),
            "avg_stake_pct": round(m["avg_stake_pct"] * 100, 2),
            "avg_stop_dist_pct": round(m["avg_stop_dist_pct"] * 100, 2),
            "avg_hold_h": round(m["avg_hold_h"], 2),
            "max_dd_realized": round(m["max_dd_realized"], 2),
            "avg_win": round(avg_win, 4), "avg_loss": round(avg_loss, 4),
            "required_payoff": round(need_payoff, 3), "actual_payoff": round(actual_payoff, 3),
            "payoff_gap": round(actual_payoff - need_payoff, 3),
            "stop_loss_share": round(100 * (df["exit_reason"] == "stop_loss").mean(), 2),
        })

        # 与基线事件的重合 / 未重合拆分
        ov = df[df["event_id"].isin(base_ev)]
        nov = df[~df["event_id"].isin(base_ev)]
        split_rows.append({
            "code": code, "strategy": strat,
            "overlap_events": int(ov["event_id"].nunique()),
            "overlap_trades": len(ov),
            "overlap_net": round(float(ov["profit_abs"].sum()), 4),
            "overlap_expectancy": round(float(ov["profit_abs"].mean()), 4) if len(ov) else None,
            "overlap_baseline_net_same_events": round(float(sum(base_map[e] for e in ov["event_id"].unique())), 4),
            "nonoverlap_events": int(nov["event_id"].nunique()),
            "nonoverlap_trades": len(nov),
            "nonoverlap_net": round(float(nov["profit_abs"].sum()), 4),
            "nonoverlap_expectancy": round(float(nov["profit_abs"].mean()), 4) if len(nov) else None,
            "nonoverlap_gross": round(float(nov["gross_pnl"].sum()), 4),
            "share_of_trades_nonoverlap": round(100 * len(nov) / len(df), 2),
            "share_of_loss_nonoverlap": round(100 * float(nov["profit_abs"].sum()) / net, 2) if abs(net) > 1e-9 else None,
        })

        # 零成本反事实（只做分解，不重跑）
        arith_rows.append({
            "code": code,
            "net": round(net, 4),
            "gross_if_zero_cost": round(gross, 4),
            "gross_positive": bool(gross > 0),
            "cost_u": round(fee - fund, 4),
            "cost_as_pct_of_abs_net": round(100 * (fee - fund) / abs(net), 2) if abs(net) > 1e-9 else None,
            "net_if_zero_cost_positive": bool(gross > 0),
        })

    met = pd.DataFrame(rows)
    # 成本按 A 组仓位规模折算的算术反事实（只做比例换算，不重跑）
    base_stake = float(base["stake_amount"].mean() / base["equity_before"].mean())
    met["cost_scaled_to_base_stake_exp"] = (
        met["gross_expectancy"]
        - met["fee_per_trade"] * (base_stake / (met["avg_stake_pct"] / 100))
        + met["funding"] / met["trades"] * (base_stake / (met["avg_stake_pct"] / 100))
    ).round(4)
    met["base_stake_pct"] = round(base_stake * 100, 2)
    write_csv(met, "05_C组指标与算术_v1.csv")
    split = pd.DataFrame(split_rows)
    write_csv(split, "05_C组事件拆分_v1.csv")
    arith = pd.DataFrame(arith_rows)
    write_csv(arith, "05_C组零成本反事实_v1.csv")

    print(met.to_string(index=False))
    print()
    print(split.to_string(index=False))

    # 与基线同事件逐笔对照（C 是否真的更便宜）
    cmp_rows = []
    for code, strat, kvs, zone in CELLS_C:
        df = load_trades(strat)
        ev = df.groupby("event_id").agg(pnl=("profit_abs", "sum"), open_rate=("open_rate", "mean"),
                                        open_ts=("open_ts", "min")).reset_index()
        common = set(ev["event_id"]) & base_ev
        disc = []
        shift = []
        for e in common:
            a = ev[ev["event_id"] == e].iloc[0]
            b = base[base["event_id"] == e].iloc[0]
            disc.append((a["open_rate"] / b["open_rate"] - 1) * 100)
            shift.append((a["open_ts"] - b["open_ts"]) / 60000.0)
        cmp_rows.append({
            "code": code, "strategy": strat,
            "common_events": len(common),
            "median_entry_discount_pct": round(float(np.median(disc)), 3) if disc else None,
            "median_time_shift_min": round(float(np.median(shift)), 1) if shift else None,
            "common_net_C": round(float(ev[ev["event_id"].isin(common)]["pnl"].sum()), 4),
            "common_net_base": round(float(sum(base_map[e] for e in common)), 4),
        })
    cmp_df = pd.DataFrame(cmp_rows)
    write_csv(cmp_df, "05_C与基线同事件对照_v1.csv")
    print()
    print(cmp_df.to_string(index=False))

    jdump(os.path.join(OUT, "05_C归因摘要_v1.json"), {
        "metrics": met.to_dict("records"),
        "event_split": split.to_dict("records"),
        "zero_cost": arith.to_dict("records"),
        "common_event_compare": cmp_df.to_dict("records"),
        "note": "零成本反事实只是把已实现毛利与成本分开列示，不重跑、不改参数",
    })
    print("完成")


if __name__ == "__main__":
    main()

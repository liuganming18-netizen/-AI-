# -*- coding: utf-8 -*-
"""02 新基线 + 9 组 A：逐笔独立重算与自报对账。

只读 out/trades_*.csv、归档 zip、自报总表；不引用对方任何汇总结论。
输出：out/02_*.csv
"""
from __future__ import annotations

import json
import os
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, CELLS_A, OUT, OUT02, jdump, load_trades, metrics, write_csv,
)


def main() -> None:
    rows = []
    exit_rows = []
    cost_rows = []
    all_codes = [("BASE", BASELINE)] + [(c, s) for c, s, _, _ in CELLS_A]
    for code, strat in all_codes:
        df = load_trades(strat)
        m = metrics(df)
        rows.append({"code": code, "strategy": strat, **m})

        # 退出原因分解
        for reason, g in df.groupby("exit_reason"):
            exit_rows.append({
                "code": code, "strategy": strat, "exit_reason": reason,
                "trades": len(g), "net_pnl": round(float(g["profit_abs"].sum()), 4),
                "avg_pnl": round(float(g["profit_abs"].mean()), 4),
            })

        # 成本分解（价格口径毛利 vs 净额）
        cost_rows.append({
            "code": code,
            "strategy": strat,
            "trades": len(df),
            "gross_pnl_price": round(float(df["gross_pnl"].sum()), 4),
            "fee_est": round(float(df["fee_est"].sum()), 4),
            "funding": round(float(df["funding"].sum()), 4),
            "cost_total_identity": round(float(df["cost_total"].sum()), 4),
            "net_pnl": round(float(df["profit_abs"].sum()), 4),
            "identity_gap": round(float((df["gross_pnl"] - df["fee_est"] + df["funding"]).sum() - df["profit_abs"].sum()), 6),
            "fee_share_of_gross": round(float(df["fee_est"].sum() / abs(df["gross_pnl"].sum())) * 100, 2) if abs(float(df["gross_pnl"].sum())) > 1e-9 else None,
            "avg_stake_u": round(float(df["stake_amount"].mean()), 2),
            "avg_stake_pct": round(float(df["stake_pct"].mean()) * 100, 2),
            "avg_stop_dist_pct": round(float(df["stop_dist_pct"].mean()) * 100, 2),
        })

    met = pd.DataFrame(rows)
    write_csv(met, "02_独立重算指标_v1.csv")
    write_csv(pd.DataFrame(exit_rows), "02_退出原因分解_v1.csv")
    write_csv(pd.DataFrame(cost_rows), "02_成本分解_v1.csv")

    # ── 与自报总表对账 ─────────────────────────────────────────────────
    self_rep = pd.read_csv(os.path.join(OUT02, "01_参数探索总表.csv"), encoding="utf-8-sig")
    self_rep = self_rep.set_index("strategy_name")
    check_rows = []
    for code, strat in all_codes:
        m = met[met["code"] == code].iloc[0]
        sr = self_rep.loc[strat]
        check_rows.append({
            "code": code,
            "strategy": strat,
            "trade_count_self": int(sr["trade_count"]),
            "trade_count_ours": int(m["trades"]),
            "trade_count_match": int(sr["trade_count"]) == int(m["trades"]),
            "net_self": round(float(sr["net_pnl"]), 4),
            "net_ours": round(float(m["net_pnl"]), 4),
            "net_gap": round(float(m["net_pnl"]) - float(sr["net_pnl"]), 6),
            "win_rate_self": round(float(sr["win_rate"]), 4),
            "win_rate_ours": round(float(m["win_rate"]) * 100, 4),
            "win_rate_gap_pp": round(float(m["win_rate"]) * 100 - float(sr["win_rate"]), 6),
            "expectancy_self": round(float(sr["expectancy_u"]), 4),
            "expectancy_ours": round(float(m["expectancy"]), 4),
            "expectancy_gap": round(float(m["expectancy"]) - float(sr["expectancy_u"]), 6),
            "pf_self": round(float(sr["profit_factor"]), 4),
            "pf_ours": round(float(m["profit_factor"]), 4),
            "pf_gap": round(float(m["profit_factor"]) - float(sr["profit_factor"]), 6),
            "dd_self_csv": round(float(sr["max_drawdown_closed_equity_u"]), 4),
            "dd_ours": round(float(m["max_dd_realized"]), 4),
            "dd_gap": round(float(m["max_dd_realized"]) - float(sr["max_drawdown_closed_equity_u"]), 6),
        })
    chk = pd.DataFrame(check_rows)
    write_csv(chk, "02_自报对账_v1.csv")
    print(chk.to_string(index=False))

    # ── 归档元数据（rejected / p_value / market_change） ───────────────
    arch_rows = []
    for code, strat in all_codes:
        zp = os.path.join(OUT02, f"backtest-result-{strat}.zip")
        z = zipfile.ZipFile(zp)
        inner = [n for n in z.namelist() if n.endswith(".json") and "config" not in n][0]
        d = json.loads(z.read(inner))["strategy"][strat]
        arch_rows.append({
            "code": code, "strategy": strat,
            "total_trades": d["total_trades"],
            "rejected_signals": d["rejected_signals"],
            "rejected_per_trade": round(d["rejected_signals"] / max(d["total_trades"], 1), 3),
            "p_value": d.get("p_value"),
            "market_change": round(float(d["market_change"]), 6),
            "profit_total_pct": round(float(d["profit_total"]) * 100, 4),
            "cagr_pct": round(float(d["cagr"]) * 100, 4),
            "sharpe": d.get("sharpe"),
            "calmar": d.get("calmar"),
            "sqn": d.get("sqn"),
            "drawdown_account_pct": round(float(d["max_drawdown_account"]) * 100, 4),
            "holding_avg_h": round(float(d["holding_avg_s"]) / 3600, 2),
            "best_pair": d.get("best_pair"),
            "worst_pair": d.get("worst_pair"),
            "results_per_enter_tag_n": len(d.get("results_per_enter_tag", {}) or {}),
        })
    arch = pd.DataFrame(arch_rows)
    write_csv(arch, "02_归档绩效元数据_v1.csv")

    jdump(os.path.join(OUT, "02_重算摘要_v1.json"), {
        "metrics": met.to_dict("records"),
        "selfreport_check": {
            "all_trade_count_match": bool(chk["trade_count_match"].all()),
            "max_abs_net_gap": float(chk["net_gap"].abs().max()),
            "max_abs_expectancy_gap": float(chk["expectancy_gap"].abs().max()),
            "max_abs_pf_gap": float(chk["pf_gap"].abs().max()),
            "max_abs_dd_gap": float(chk["dd_gap"].abs().max()),
        },
    })
    print("\n最大偏差：净收益 %.6f / 期望 %.6f / PF %.6f / 回撤 %.6f" % (
        chk["net_gap"].abs().max(), chk["expectancy_gap"].abs().max(),
        chk["pf_gap"].abs().max(), chk["dd_gap"].abs().max()))
    print("完成")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""02 新版基线 + 9 组 A：逐笔独立重算、自报对账、跨批旧值核对。

只读 out/trades_*.csv、归档 zip、自报总表、FT-ENTRY-02 旧逐笔；不引用对方任何汇总结论。
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
    BASELINE, CELLS_A, OUT, OUT02, OUT03, jdump, load_trades, metrics, write_csv,
)

ALL_CODES = [("BASE", BASELINE)] + [(c, s) for c, s, _, _ in CELLS_A]


def main() -> None:
    rows, exit_rows, cost_rows = [], [], []
    for code, strat in ALL_CODES:
        df = load_trades(strat)
        rows.append({"code": code, "strategy": strat, **metrics(df)})

        for reason, g in df.groupby("exit_reason"):
            exit_rows.append({
                "code": code, "strategy": strat, "exit_reason": reason,
                "trades": len(g), "net_pnl": round(float(g["profit_abs"].sum()), 4),
                "avg_pnl": round(float(g["profit_abs"].mean()), 4),
                "wins": int((g["profit_abs"] > 0).sum()),
            })

        cost_rows.append({
            "code": code, "strategy": strat, "trades": len(df),
            "gross_pnl_price": round(float(df["gross_pnl"].sum()), 4),
            "fee_est": round(float(df["fee_est"].sum()), 4),
            "funding": round(float(df["funding"].sum()), 4),
            "net_pnl": round(float(df["profit_abs"].sum()), 4),
            "identity_gap": round(float((df["gross_pnl"] - df["fee_est"] + df["funding"]).sum()
                                        - df["profit_abs"].sum()), 6),
            "avg_stake_u": round(float(df["stake_amount"].mean()), 2),
            "avg_stake_pct": round(float(df["stake_pct"].mean()) * 100, 2),
            "avg_stop_dist_pct": round(float(df["stop_dist_pct"].mean()) * 100, 2),
        })

    met = pd.DataFrame(rows)
    write_csv(met, "02_独立重算指标_v1.csv")
    write_csv(pd.DataFrame(exit_rows), "02_退出原因分解_v1.csv")
    write_csv(pd.DataFrame(cost_rows), "02_成本分解_v1.csv")
    print(met[["code", "strategy", "trades", "wins", "flats", "losses", "win_rate",
               "wilson_lo", "wilson_hi", "net_pnl", "expectancy", "profit_factor",
               "avg_win", "avg_loss", "payoff_ratio", "max_dd_realized"]].to_string(index=False))

    # ── 与自报总表对账 ─────────────────────────────────────────────────
    self_rep = pd.read_csv(os.path.join(OUT03, "01_严格突破前参数总表.csv"), encoding="utf-8-sig")
    self_rep = self_rep.set_index("strategy_name")
    chk_rows = []
    for code, strat in ALL_CODES:
        m = met[met["code"] == code].iloc[0]
        sr = self_rep.loc[strat]
        chk_rows.append({
            "code": code, "strategy": strat,
            "trades_self": int(sr["trade_count"]), "trades_ours": int(m["trades"]),
            "trades_match": int(sr["trade_count"]) == int(m["trades"]),
            "wins_self": int(sr["win_count"]), "wins_ours": int(m["wins"]),
            "flats_self": int(sr["even_count"]), "flats_ours": int(m["flats"]),
            "losses_self": int(sr["loss_count"]), "losses_ours": int(m["losses"]),
            "win_rate_self": round(float(sr["win_rate"]), 4),
            "win_rate_ours": round(float(m["win_rate"]) * 100, 4),
            "win_rate_gap_pp": round(float(m["win_rate"]) * 100 - float(sr["win_rate"]), 6),
            "net_self": round(float(sr["net_pnl"]), 4), "net_ours": round(float(m["net_pnl"]), 4),
            "net_gap": round(float(m["net_pnl"]) - float(sr["net_pnl"]), 6),
            "exp_self": round(float(sr["expectancy_u"]), 4),
            "exp_ours": round(float(m["expectancy"]), 4),
            "exp_gap": round(float(m["expectancy"]) - float(sr["expectancy_u"]), 6),
            "pf_self": round(float(sr["profit_factor"]), 4),
            "pf_ours": round(float(m["profit_factor"]), 4),
            "pf_gap": round(float(m["profit_factor"]) - float(sr["profit_factor"]), 6),
            "avg_win_self": round(float(sr["avg_profit_u"]), 4), "avg_win_ours": round(float(m["avg_win"]), 4),
            "avg_win_gap": round(float(m["avg_win"]) - float(sr["avg_profit_u"]), 6),
            "avg_loss_self": round(float(sr["avg_loss_u"]), 4), "avg_loss_ours": round(float(m["avg_loss"]), 4),
            "avg_loss_gap": round(float(m["avg_loss"]) - float(sr["avg_loss_u"]), 6),
            "dd_self": round(float(sr["max_drawdown_closed_equity_u"]), 4),
            "dd_ours": round(float(m["max_dd_realized"]), 4),
            "dd_gap": round(float(m["max_dd_realized"]) - float(sr["max_drawdown_closed_equity_u"]), 6),
        })
    chk = pd.DataFrame(chk_rows)
    write_csv(chk, "02_自报对账_v1.csv")
    print("\n自报对账最大偏差：笔数全同=%s / 净收益 %.6f / 期望 %.6f / PF %.6f / 均盈 %.6f / 均亏 %.6f / 回撤 %.6f" % (
        bool(chk["trades_match"].all()),
        chk["net_gap"].abs().max(), chk["exp_gap"].abs().max(), chk["pf_gap"].abs().max(),
        chk["avg_win_gap"].abs().max(), chk["avg_loss_gap"].abs().max(), chk["dd_gap"].abs().max()))

    # ── 跨批核对：对方「旧值」列是否等于 FT-ENTRY-02 实际结果 ────────────
    old_new = pd.read_csv(os.path.join(OUT03, "05_新旧结果对比与边界变化拆解表.csv"), encoding="utf-8-sig")
    old_new = old_new.set_index("strategy_name")
    old_actual = {}
    for code, strat in ALL_CODES:
        d = load_trades(strat, src=OUT02)
        old_actual[strat] = {
            "trades": int(len(d)),
            "win_rate": round(float((d["profit_abs"] > 0).mean()) * 100, 4),
            "net": round(float(d["profit_abs"].sum()), 4),
            "exp": round(float(d["profit_abs"].mean()), 4),
            "pf": round(float(d.loc[d["profit_abs"] > 0, "profit_abs"].sum()
                              / max(-d.loc[d["profit_abs"] < 0, "profit_abs"].sum(), 1e-12)), 4),
        }
    xb_rows = []
    for code, strat in ALL_CODES:
        r = old_new.loc[strat]
        a = old_actual[strat]
        m = met[met["code"] == code].iloc[0]
        xb_rows.append({
            "code": code, "strategy": strat,
            "old_trades_claimed": int(r["旧交易数"]), "old_trades_actual_02": a["trades"],
            "old_trades_match": int(r["旧交易数"]) == a["trades"],
            "old_win_claimed": round(float(str(r["旧胜率"]).strip("%")), 4) if isinstance(r["旧胜率"], str) else round(float(r["旧胜率"]), 4),
            "old_win_actual_02": a["win_rate"],
            "old_net_claimed": round(float(r["旧净收益(U)"]), 4), "old_net_actual_02": a["net"],
            "old_net_gap": round(float(r["旧净收益(U)"]) - a["net"], 4),
            "old_exp_claimed": round(float(r["旧净期望(U)"]), 4), "old_exp_actual_02": a["exp"],
            "old_pf_claimed": round(float(r["旧利润因子"]), 4), "old_pf_actual_02": a["pf"],
            "new_trades_claimed": int(r["新交易数"]), "new_trades_ours": int(m["trades"]),
            "new_net_claimed": round(float(r["新净收益(U)"]), 4), "new_net_ours": round(float(m["net_pnl"]), 4),
            "delta_claimed": round(float(r["净收益差额(U)"]), 4),
            "delta_ours": round(float(m["net_pnl"]) - a["net"], 4),
        })
    xb = pd.DataFrame(xb_rows)
    write_csv(xb, "02_跨批旧新核对_v1.csv")
    print("\n跨批核对（对方「旧值」 vs FT-ENTRY-02 实际）")
    print(xb[["code", "old_trades_claimed", "old_trades_actual_02", "old_net_claimed",
              "old_net_actual_02", "old_net_gap", "delta_claimed", "delta_ours"]].to_string(index=False))

    # ── 归档绩效元数据 ─────────────────────────────────────────────────
    arch_rows = []
    for code, strat in ALL_CODES:
        zp = os.path.join(OUT03, f"backtest-result-{strat}.zip")
        with zipfile.ZipFile(zp) as z:
            inner = [n for n in z.namelist() if n.endswith(".json") and "config" not in n][0]
            d = json.loads(z.read(inner))["strategy"][strat]
        arch_rows.append({
            "code": code, "strategy": strat,
            "total_trades": d["total_trades"], "rejected_signals": d["rejected_signals"],
            "rejected_per_trade": round(d["rejected_signals"] / max(d["total_trades"], 1), 3),
            "p_value": d.get("p_value"), "market_change": round(float(d["market_change"]), 6),
            "profit_total_pct": round(float(d["profit_total"]) * 100, 4),
            "cagr_pct": round(float(d["cagr"]) * 100, 4),
            "sharpe": d.get("sharpe"), "calmar": d.get("calmar"), "sqn": d.get("sqn"),
            "expectancy_ratio": d.get("expectancy_ratio"),
            "drawdown_account_pct": round(float(d["max_drawdown_account"]) * 100, 4),
            "drawdown_abs_u": round(float(d["max_drawdown_abs"]), 4),
            "holding_avg_h": round(float(d["holding_avg_s"]) / 3600, 2),
            "best_pair": d.get("best_pair"), "worst_pair": d.get("worst_pair"),
            "wins": d.get("wins"), "draws": d.get("draws"), "losses": d.get("losses"),
        })
    arch = pd.DataFrame(arch_rows)
    write_csv(arch, "02_归档绩效元数据_v1.csv")

    jdump(os.path.join(OUT, "02_重算摘要_v1.json"), {
        "metrics": met.to_dict("records"),
        "selfreport_check": {
            "all_trade_count_match": bool(chk["trades_match"].all()),
            "all_wins_match": bool((chk["wins_self"] == chk["wins_ours"]).all()),
            "all_losses_match": bool((chk["losses_self"] == chk["losses_ours"]).all()),
            "max_abs_net_gap": float(chk["net_gap"].abs().max()),
            "max_abs_expectancy_gap": float(chk["exp_gap"].abs().max()),
            "max_abs_pf_gap": float(chk["pf_gap"].abs().max()),
            "max_abs_avg_win_gap": float(chk["avg_win_gap"].abs().max()),
            "max_abs_avg_loss_gap": float(chk["avg_loss_gap"].abs().max()),
            "max_abs_dd_gap": float(chk["dd_gap"].abs().max()),
        },
        "cross_batch_check": {
            "old_trades_all_match": bool(xb["old_trades_match"].all()),
            "max_abs_old_net_gap": float(xb["old_net_gap"].abs().max()),
            "max_abs_delta_gap": float((xb["delta_claimed"] - xb["delta_ours"]).abs().max()),
        },
        "cost_identity_max_gap": float(pd.DataFrame(cost_rows)["identity_gap"].abs().max()),
    })
    print("完成")


if __name__ == "__main__":
    main()

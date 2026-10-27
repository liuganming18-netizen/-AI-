# -*- coding: utf-8 -*-
"""07 身份一致性、重复入场、集中度明细（补充核对）。

输出：out/07_*.csv
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, CELLS_A, CELLS_C, OUT, jdump, load_trades, write_csv,
)


def main() -> None:
    rows = []
    all_codes = [("BASE", BASELINE)] + [(c, s) for c, s, _, _ in CELLS_A] + [(c, s) for c, s, _, _ in CELLS_C]
    for code, strat in all_codes:
        df = load_trades(strat)
        # event_id 里的标的应与 pair 一致（ev_{strategy}_{venue}_{symbol}_{box_ms}）
        sym_in_tag = df["event_id"].str.rsplit("_", n=1).str[0].str.split("_").str[-1]
        sym_in_pair = df["pair"].str.split("/").str[0].str.replace("1000", "", regex=False) + "USDT"
        mismatch = int((sym_in_tag != sym_in_pair).sum())
        g = df.groupby("event_id").size()
        rows.append({
            "code": code, "strategy": strat,
            "trades": len(df),
            "unique_events": int(df["event_id"].nunique()),
            "extra_fills_same_event": len(df) - int(df["event_id"].nunique()),
            "dup_events": int((g > 1).sum()),
            "dup_fills_pnl": round(float(df[df["event_id"].isin(g[g > 1].index)]["profit_abs"].sum()), 4),
            "tag_symbol_mismatch": mismatch,
            "prefix_A": int((df["tag_prefix"] == "cand_A_").sum()),
            "prefix_C": int((df["tag_prefix"] == "cand_C_").sum()),
            "prefix_base": int((df["tag_prefix"] == "cand_").sum()),
            "trades_outside_timerange": int(((df["open_ts"] < 1638316800000) | (df["open_ts"] >= 1735689600000)).sum()),
        })
    integ = pd.DataFrame(rows)
    write_csv(integ, "07_身份与完整性_v1.csv")
    print(integ.to_string(index=False))

    # A 组利润集中度：前 k 笔占比
    conc_rows = []
    for code, strat, _, _ in [("BASE", BASELINE, 0, 0)] + CELLS_A:
        df = load_trades(strat).sort_values("profit_abs", ascending=False).reset_index(drop=True)
        total = float(df["profit_abs"].sum())
        rec = {"code": code, "strategy": strat, "trades": len(df), "net": round(total, 4)}
        for k in (1, 2, 3, 5):
            s = float(df["profit_abs"].iloc[:k].sum())
            rec[f"top{k}_sum"] = round(s, 4)
            rec[f"top{k}_share_pct"] = round(100 * s / total, 2) if abs(total) > 1e-9 else None
        conc_rows.append(rec)
    conc = pd.DataFrame(conc_rows)
    write_csv(conc, "07_利润集中度_v1.csv")
    print("\n", conc.to_string(index=False))

    # A 组前 5 大盈利单明细
    top_rows = []
    for code, strat, _, _ in [("BASE", BASELINE, 0, 0)] + CELLS_A:
        df = load_trades(strat).sort_values("profit_abs", ascending=False).head(5)
        for rank, (_, r) in enumerate(df.iterrows(), 1):
            top_rows.append({
                "code": code, "rank": rank, "pair": r["pair"],
                "open_date": str(r["open_date"])[:16], "close_date": str(r["close_date"])[:16],
                "profit_abs": round(float(r["profit_abs"]), 4),
                "exit_reason": r["exit_reason"], "event_id": r["event_id"],
            })
    write_csv(pd.DataFrame(top_rows), "07_A前五大盈利单_v1.csv")

    jdump(os.path.join(OUT, "07_完整性摘要_v1.json"), {
        "integrity": integ.to_dict("records"),
        "concentration": conc.to_dict("records"),
        "checks": {
            "all_tag_symbol_match": bool((integ["tag_symbol_mismatch"] == 0).all()),
            "all_within_timerange": bool((integ["trades_outside_timerange"] == 0).all()),
            "A_and_BASE_no_dup_fills": bool((integ.loc[integ["code"].str.startswith(("A", "B")), "extra_fills_same_event"] == 0).all()),
            "C_dup_fills_total": int(integ.loc[integ["code"].str.startswith("C"), "extra_fills_same_event"].sum()),
        },
    })
    print("完成")


if __name__ == "__main__":
    main()

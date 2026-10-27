# -*- coding: utf-8 -*-
"""02 成交约束的独立静态反事实预测（不依赖 FT-ENTRY-04 的实现结果）。

方法：
- 底座 = FT-ENTRY-03 的 10 组逐笔 CSV（独立重算，不引用对方任何自报指标）；
- 冻结 H 与每笔成交价 = FT-ENTRY-AUDIT-04/out/01_A入场价与箱顶H逐笔_v1.csv
  （该表已由 AUDIT-04 用 4h feather 独立回算 H/L/A，与 03 逐笔逐行对齐）；
- 反事实规则：对 A 路线，剔除「成交价 >= 冻结 H」的成交，其余逐笔完全不动；
  Baseline 不受约束（与 04 的实现一致：只有 timing_mode=="A" 走新约束分支）。

重要口径声明（写入产物，避免误读）：
1. 本脚本是「静态剔除」口径，**不重放撮合**。真实回测中拒单会释放槽位，
   可能让原本因满仓未成交的其它候选补入（或反之），这部分由 04 脚本单独双向核查；
2. 因此本表给出的是「成交约束的纯价格效应」，不是「整轮回测的最终结果」；
3. 结论以 FT-ENTRY-04 实际回测为准，本表用于独立预测与事后对账。

输出：out/02_*.csv
"""
from __future__ import annotations

import os
import sys
from typing import List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    AUDIT04_OUT, BASELINE, CELLS_A, OUT, OUT03, OUT04,
    jdump, load_trades, metrics, write_csv,
)

H_TABLE = os.path.join(AUDIT04_OUT, "01_A入场价与箱顶H逐笔_v1.csv")


def load_h_table() -> pd.DataFrame:
    df = pd.read_csv(H_TABLE, encoding="utf-8-sig")
    df["code"] = df["code"].astype(str)
    return df


def main() -> None:
    h = load_h_table()
    print(f"H 表载入：{len(h)} 行，组别 {sorted(h['code'].unique())}")

    # 1) 逐组独立重算 03 基线，并建立「成交价 >= H」标记
    rows: List[dict] = []
    reject_detail: List[dict] = []
    for code, strat, delta, kvol in [("BASE", BASELINE, 0.0, 0.0)] + CELLS_A:
        df = load_trades(strat, OUT03)
        if df.empty:
            continue
        # 与 H 表按 (pair, open_rate) 对齐取 H
        hh = h[h["code"] == code][["pair", "open_rate", "H", "at_or_above_H",
                                  "open_rate_minus_H_in_ATR", "event_id"]].copy()
        hh = hh.rename(columns={"H": "frozen_H", "open_rate_minus_H_in_ATR": "price_minus_h_in_atr",
                                "event_id": "h_event_id"})
        m = df.merge(hh, on=["pair", "open_rate"], how="left", validate="one_to_one")
        unmatched = int(m["frozen_H"].isna().sum())
        m["violates_constraint"] = m["frozen_H"].notna() & (m["open_rate"] >= m["frozen_H"])
        is_a = code != "BASE"

        m3 = metrics(m)
        kept = m[~m["violates_constraint"]] if is_a else m
        m4 = metrics(kept)

        rows.append({
            "code": code, "strategy": strat, "route": "Baseline" if code == "BASE" else "A",
            "delta_atr": delta, "k_vol": kvol,
            "trades_03": m3["trades"], "wins_03": m3["wins"], "losses_03": m3["losses"],
            "win_rate_pct_03": m3["win_rate_pct"],
            "expectancy_u_03": m3["expectancy_u"], "net_pnl_u_03": m3["net_pnl_u"],
            "profit_factor_03": m3["profit_factor"],
            "avg_win_u_03": m3["avg_win_u"], "avg_loss_u_03": m3["avg_loss_u"],
            "payoff_ratio_03": m3["payoff_ratio"],
            "max_dd_realized_03": m3["max_dd_realized"],
            "n_rejected_by_constraint": int(m["violates_constraint"].sum()) if is_a else 0,
            "reject_pct": round(100.0 * m["violates_constraint"].sum() / len(m), 4) if is_a and len(m) else 0.0,
            "trades_04_expected": m4["trades"], "wins_04_expected": m4["wins"],
            "losses_04_expected": m4["losses"],
            "win_rate_pct_04_expected": m4["win_rate_pct"],
            "expectancy_u_04_expected": m4["expectancy_u"], "net_pnl_u_04_expected": m4["net_pnl_u"],
            "profit_factor_04_expected": m4["profit_factor"],
            "avg_win_u_04_expected": m4["avg_win_u"], "avg_loss_u_04_expected": m4["avg_loss_u"],
            "payoff_ratio_04_expected": m4["payoff_ratio"],
            "max_dd_realized_04_expected": m4["max_dd_realized"],
            "d_trades": m4["trades"] - m3["trades"],
            "d_win_rate_pp": (round(m4["win_rate_pct"] - m3["win_rate_pct"], 4)
                              if m3["win_rate_pct"] is not None and m4["win_rate_pct"] is not None else None),
            "d_expectancy_u": (round(m4["expectancy_u"] - m3["expectancy_u"], 4)
                               if m3["expectancy_u"] is not None and m4["expectancy_u"] is not None else None),
            "d_net_pnl_u": (round(m4["net_pnl_u"] - m3["net_pnl_u"], 4)
                            if m3["net_pnl_u"] is not None and m4["net_pnl_u"] is not None else None),
            "h_unmatched_rows": unmatched,
        })

        # 拒单明细（仅 A 路线）
        if is_a:
            rej = m[m["violates_constraint"]]
            for _, r in rej.iterrows():
                reject_detail.append({
                    "code": code, "strategy": strat, "pair": r["pair"],
                    "event_id": r["event_id"], "enter_tag": r["enter_tag"],
                    "open_ts": r["open_ts"], "close_ts": r["close_ts"],
                    "open_rate": r["open_rate"], "frozen_H": r["frozen_H"],
                    "price_minus_h": round(float(r["open_rate"] - r["frozen_H"]), 8),
                    "price_minus_h_in_atr": r["price_minus_h_in_atr"],
                    "profit_abs": r["profit_abs"],
                    "is_win": bool(r["is_win"]),
                    "gross_pnl": r["gross_pnl"], "cost_total": r["cost_total"],
                })

    res = pd.DataFrame(rows)
    write_csv(res, "02_静态反事实预测_v1.csv")
    print("\n== 静态反事实预测（03 底座，剔除成交价>=H）==")
    cols = ["code", "trades_03", "n_rejected_by_constraint", "reject_pct",
            "trades_04_expected", "win_rate_pct_03", "win_rate_pct_04_expected",
            "d_win_rate_pp", "expectancy_u_03", "expectancy_u_04_expected",
            "d_expectancy_u", "net_pnl_u_03", "net_pnl_u_04_expected", "d_net_pnl_u"]
    print(res[cols].to_string(index=False))

    rej = pd.DataFrame(reject_detail)
    write_csv(rej, "02_拒单明细_v1.csv")
    print(f"\n拒单明细共 {len(rej)} 笔")

    if not rej.empty:
        g = rej.groupby("code").agg(
            rejected=("profit_abs", "size"),
            rejected_wins=("is_win", "sum"),
            rejected_net_u=("profit_abs", "sum"),
            rejected_avg_u=("profit_abs", "mean"),
        ).reset_index()
        g["rejected_win_rate_pct"] = (100 * g["rejected_wins"] / g["rejected"]).round(4)
        g["loss_avoided_u"] = g["rejected_net_u"].apply(lambda v: -v if v < 0 else 0.0)
        g["profit_given_up_u"] = g["rejected_net_u"].apply(lambda v: v if v > 0 else 0.0)
        write_csv(g, "02_拒单盈亏归因_v1.csv")
        print("\n== 拒单交易的盈亏归因 ==")
        print(g.to_string(index=False))

    jdump(os.path.join(OUT, "02_静态反事实预测摘要_v1.json"), {
        "method": "静态剔除成交价>=冻结H的成交（不重放撮合）",
        "h_source": "evidence/FT-ENTRY-AUDIT-04/out/01_A入场价与箱顶H逐笔_v1.csv",
        "base_source": "evidence/FT-ENTRY-03/out/trades_*.csv",
        "rows": res.to_dict("records"),
        "caveat": "本表是纯价格效应的独立预测，不含拒单释放槽位后的撮合重放；"
                  "最终结论以 FT-ENTRY-04 实际回测 + 本审核的独立重算为准",
    })
    print("\n完成")


if __name__ == "__main__":
    main()

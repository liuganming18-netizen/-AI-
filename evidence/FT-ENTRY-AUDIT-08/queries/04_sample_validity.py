# -*- coding: utf-8 -*-
"""04 74 币逐币有效运行区间（工单第 3 项）。

复用 `evidence/FT-ENTRY-AUDIT-02/out/02_币池与数据覆盖_v1.csv`（74 币逐币数据首末时间）
与 `evidence/FT-ENTRY-03/01_输入资格版本与样本核验.md` 的 L1 资格口径：
研究期内 4h（200 根预热）∩ 1h ∩ 15m 三层交集非空。

对每币给出：
- 数据首末（15m / 1h / 4h）与研究期交集；
- 200 根 4h 预热完成时刻（q_start，资格起点）；
- 策略自身箱体最低需求 42 根 4h（box_ready，7 天）作为敏感性参照；
- 不合格区间（研究期起点 → q_start）；
- 该区间内是否发生 FT-ENTRY-07 的 27 组交易（逐笔列示）。

纪律：不删除任何交易、不生成"清洗后账户结果"；只标注区间与受影响交易。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    AUDIT02, B4_MS, END_MS, OUT, START_MS, jdump, load_trades, write_csv,
)

COVER = os.path.join(AUDIT02, "out/02_币池与数据覆盖_v1.csv")
RESEARCH_START = pd.Timestamp(START_MS, unit="ms", tz="UTC")
RESEARCH_END = pd.Timestamp(END_MS, unit="ms", tz="UTC")
L1_WARMUP_4H = 200
BOX_BARS_4H = 42


def main() -> None:
    cov = pd.read_csv(COVER, encoding="utf-8-sig")
    cov["symbol"] = cov["pair"].str.split("/").str[0]
    cov["m15_first"] = pd.to_datetime(cov["15m_first"], utc=True)
    cov["m15_last"] = pd.to_datetime(cov["15m_last"], utc=True)
    cov["h1_first"] = pd.to_datetime(cov["1h_first"], utc=True)
    cov["h1_last"] = pd.to_datetime(cov["1h_last"], utc=True)
    cov["h4_first"] = pd.to_datetime(cov["4h_first"], utc=True)
    cov["h4_last"] = pd.to_datetime(cov["4h_last"], utc=True)
    cov["oi_first"] = pd.to_datetime(cov["oi_first"], utc=True)
    cov["oi_last"] = pd.to_datetime(cov["oi_last"], utc=True)

    rows = []
    for rec in cov.to_dict("records"):
        bars_before = int(rec["4h_bars_before_research_start"])
        h4_first = rec["h4_first"]
        if bars_before >= L1_WARMUP_4H:
            q_start = RESEARCH_START
        else:
            q_start = max(RESEARCH_START, h4_first + pd.Timedelta(milliseconds=L1_WARMUP_4H * B4_MS))
        box_ready = max(RESEARCH_START, h4_first + pd.Timedelta(milliseconds=BOX_BARS_4H * B4_MS))
        q_end = min(RESEARCH_END, rec["m15_last"], rec["h1_last"],
                    rec["h4_last"] + pd.Timedelta(hours=4))
        qualified = bool(q_start < q_end)
        rows.append({
            "symbol": rec["symbol"], "pair": rec["pair"],
            "15m_first": rec["m15_first"], "15m_last": rec["m15_last"],
            "1h_first": rec["h1_first"], "1h_last": rec["h1_last"],
            "4h_first": rec["h4_first"], "4h_last": rec["h4_last"],
            "4h_bars_before_research_start": bars_before,
            "L1_q_start_200bars": q_start, "box_ready_42bars": box_ready,
            "oi_first": rec["oi_first"], "oi_last": rec["oi_last"],
            "q_end": q_end,
            "qualified_in_research": qualified,
            "unqualified_start_gap_days": round((q_start - RESEARCH_START).total_seconds() / 86400, 2),
            "qualified_window_days": round((q_end - q_start).total_seconds() / 86400, 2) if qualified else 0.0,
            "layer": ("研究期起点即合格" if bars_before >= L1_WARMUP_4H
                      else ("研究期内后段才合格" if qualified else "研究期内不合格")),
        })
    per = pd.DataFrame(rows)
    per = per.sort_values(["qualified_in_research", "L1_q_start_200bars"],
                          ascending=[False, True])
    write_csv(per, "04_74币逐币有效区间_v1.csv")

    # 交易核对：27 组全部逐笔
    tr = []
    for mode in ("C", "C1", "C2"):
        d = load_trades(mode)
        d = d[["mode", "grid", "strategy", "symbol", "pair", "open_date", "close_date",
               "open_timestamp", "close_timestamp", "profit_abs", "enter_tag"]].copy()
        tr.append(d)
    tr = pd.concat(tr, ignore_index=True)
    tr["open_ts"] = pd.to_datetime(tr["open_timestamp"], unit="ms", utc=True)
    tr["close_ts"] = pd.to_datetime(tr["close_timestamp"], unit="ms", utc=True)
    tr = tr.merge(per[["symbol", "L1_q_start_200bars", "box_ready_42bars", "qualified_in_research",
                       "q_end", "oi_first", "oi_last"]], on="symbol", how="left")
    tr["before_L1_q_start"] = tr["open_ts"] < tr["L1_q_start_200bars"]
    tr["before_box_ready"] = tr["open_ts"] < tr["box_ready_42bars"]
    tr["after_q_end"] = tr["close_ts"] > tr["q_end"]
    tr["before_oi_first"] = tr["open_ts"] < tr["oi_first"]
    tr["after_oi_last"] = tr["close_ts"] > tr["oi_last"]
    affected = tr[tr["before_L1_q_start"] | tr["before_box_ready"] | tr["after_q_end"]].copy()
    write_csv(affected, "04_不合格区间涉及交易_v1.csv")

    # 逐币交易统计
    agg = (tr.groupby("symbol")
           .agg(trades=("profit_abs", "size"), net_u=("profit_abs", "sum"),
                first_open=("open_ts", "min"), last_close=("close_ts", "max"))
           .reset_index())
    agg = per[["symbol", "qualified_in_research", "L1_q_start_200bars", "box_ready_42bars",
               "layer"]].merge(agg, on="symbol", how="left")
    agg["trades"] = agg["trades"].fillna(0).astype(int)
    agg["net_u"] = agg["net_u"].fillna(0.0)
    agg["first_open_before_L1_q_start"] = agg["first_open"] < agg["L1_q_start_200bars"]
    write_csv(agg, "04_逐币交易与区间_v1.csv")

    # 汇总
    n74 = len(per)
    n_at_start = int((per["layer"] == "研究期起点即合格").sum())
    n_late = int((per["layer"] == "研究期内后段才合格").sum())
    n_unq = int((per["layer"] == "研究期内不合格").sum())
    unq_list = per[per["layer"] != "研究期起点即合格"][
        ["symbol", "layer", "L1_q_start_200bars", "4h_bars_before_research_start",
         "unqualified_start_gap_days"]]
    traded = set(tr["symbol"])
    zero_trade = sorted(set(per["symbol"]) - traded)
    # 受影响交易的分模式净额（仅算术标注，不是清洗后回测/账户结果）
    aff_by_mode = []
    for mode in ("C", "C1", "C2"):
        a = affected[affected["mode"] == mode]
        allm = tr[tr["mode"] == mode]
        aff_by_mode.append({
            "mode": mode, "affected_trades": int(len(a)),
            "affected_net_u": round(float(a["profit_abs"].sum()), 4),
            "mode_trades": int(len(allm)),
            "mode_net_u": round(float(allm["profit_abs"].sum()), 4),
            "affected_share_of_mode_trades_pct": round(100 * len(a) / len(allm), 3) if len(allm) else None,
            "note": "算术标注；删除后不是重新回测，也不是账户结果",
        })

    summary = {
        "universe": n74,
        "qualified_from_start_200bars": n_at_start,
        "qualified_late_in_period": n_late,
        "unqualified_whole_period": n_unq,
        "late_or_unqualified_coins": unq_list.to_dict("records"),
        "coins_with_zero_trades": zero_trade,
        "zero_trade_count": len(zero_trade),
        "trades_total_27cells": int(len(tr)),
        "trades_before_L1_q_start": int(tr["before_L1_q_start"].sum()),
        "trades_before_box_ready_42bars": int(tr["before_box_ready"].sum()),
        "trades_after_q_end": int(tr["after_q_end"].sum()),
        "trades_before_oi_first": int(tr["before_oi_first"].sum()),
        "trades_after_oi_last": int(tr["after_oi_last"].sum()),
        "affected_trades": int(len(affected)),
        "affected_net_u": round(float(affected["profit_abs"].sum()), 4),
        "affected_by_symbol": affected.groupby("symbol").size().to_dict() if len(affected) else {},
        "affected_by_mode": aff_by_mode,
        "rule": ("资格起点取 4h 200 根预热完成（FT-ENTRY-03 L1）；策略自身箱体最低需求 42 根 4h "
                 "作为敏感性参照。交易按开仓时间核对，未删除任何交易。"),
    }
    jdump(os.path.join(OUT, "04_样本有效区间摘要_v1.json"), summary)

    print(f"74 币：起点合格 {n_at_start}；期内后段合格 {n_late}；全程不合格 {n_unq}")
    print("\n非起点合格币：")
    print(unq_list.to_string(index=False))
    print(f"\n0 成交币 {len(zero_trade)}：{zero_trade}")
    print("受影响交易分模式：")
    print(pd.DataFrame(aff_by_mode).to_string(index=False))
    print(f"OI 覆盖：早于 oi_first {int(tr['before_oi_first'].sum())}；晚于 oi_last {int(tr['after_oi_last'].sum())}")
    print(f"27 组逐笔 {len(tr)}；早于 L1 资格起点 {int(tr['before_L1_q_start'].sum())}；"
          f"早于 42 根箱体 {int(tr['before_box_ready'].sum())}；晚于区间末 {int(tr['after_q_end'].sum())}")
    if len(affected):
        print("\n受影响交易：")
        print(affected[["symbol", "mode", "grid", "open_ts", "profit_abs",
                        "before_L1_q_start", "before_box_ready"]].to_string(index=False))
    print("\n完成 04")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""03 逐笔独立重算 + 资格违规核对（工单交付后第 2 项）。

- 从 FT-ENTRY-09 实际成交逐笔独立重算：胜负平、胜率、平均盈亏、净期望、净收益、快速止损率；
- 逐笔核对是否落在统一准入区间 [q_start, q_end] 内，违规数必须为 0；
- 与上游自报指标表逐格对拍（若存在）；
- 不重跑回测、不删除任何交易。
"""
from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    ENTRY09, GRIDS, OUT, OUT09, jdump, load_admission, load_trades,
    qualify_trades, write_csv,
)

MODES = ("C", "C1", "C2")


def metrics_cell(d: pd.DataFrame) -> dict:
    p = d["profit_abs"].to_numpy(float)
    wins, losses = int((p > 0).sum()), int((p < 0).sum())
    flats = int((p == 0).sum())
    gross_win = float(p[p > 0].sum())
    gross_loss = float(-p[p < 0].sum())
    qs = d["quick_stop"].to_numpy(bool)
    return {
        "trades": int(len(d)),
        "wins": wins, "losses": losses, "flats": flats,
        "win_rate_pct": round(100 * wins / len(d), 4) if len(d) else None,
        "avg_trade_u": round(float(p.mean()), 4) if len(d) else None,
        "avg_win_u": round(float(p[p > 0].mean()), 4) if wins else None,
        "avg_loss_u": round(float(p[p < 0].mean()), 4) if losses else None,
        "net_pnl_u": round(float(p.sum()), 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 1e-12 else None,
        "quick_stop_count": int(qs.sum()),
        "quick_stop_rate_pct": round(100 * qs.mean(), 4) if len(d) else None,
        "unique_events": int(d["event_key"].nunique()),
        "net_expectancy_per_trade_u": round(float(p.mean()), 4) if len(d) else None,
    }


def find_self_report() -> pd.DataFrame:
    """找上游 27 格自报指标表（容错：指标总表 / 全部运行索引）。"""
    cands = []
    for base in (ENTRY09, OUT09):
        for pat in ("*指标总表*.csv", "*全部运行索引*.csv", "*模式汇总*.csv"):
            cands += glob.glob(os.path.join(base, pat))
    for p in sorted(set(cands)):
        try:
            d = pd.read_csv(p, encoding="utf-8-sig")
        except Exception:
            continue
        if "strategy" in d.columns and any(c in d.columns for c in
                                           ("total_profit_usdt", "net_pnl", "total_profit",
                                            "net_profit_usdt")):
            d["__src"] = os.path.basename(p)
            return d
    return pd.DataFrame()


def main() -> None:
    adm = load_admission()
    rows, viol_rows = [], []

    for g, s0, s1, s2, vs, z in GRIDS:
        for mode, strat in (("C", s0), ("C1", s1), ("C2", s2)):
            d = load_trades(mode)
            d = d[d["grid"] == g] if not d.empty else d
            if d.empty:
                rows.append({"grid": g, "mode": mode, "strategy": strat, "trades": 0,
                             "missing": True})
                continue
            r = metrics_cell(d)
            r.update({"grid": g, "mode": mode, "strategy": strat,
                      "vs": vs, "z": z, "missing": False})
            rows.append(r)
            q = qualify_trades(d, adm)
            v = q[q["violation"]]
            if len(v):
                viol_rows.append(v[["grid", "mode", "strategy", "pair", "open_date",
                                    "open_timestamp", "close_timestamp", "profit_abs",
                                    "enter_tag", "q_start_ms", "q_end_ms",
                                    "before_q_start", "after_q_end", "qualified"]])

    res = pd.DataFrame(rows)
    front = ["grid", "mode", "strategy", "vs", "z", "trades", "wins", "losses", "flats",
             "win_rate_pct", "avg_trade_u", "avg_win_u", "avg_loss_u", "net_pnl_u",
             "profit_factor", "quick_stop_count", "quick_stop_rate_pct",
             "unique_events", "net_expectancy_per_trade_u", "missing"]
    res = res[[c for c in front if c in res.columns]]
    write_csv(res, "03_27组独立重算_v1.csv")

    # 模式汇总
    mode_rows = []
    for mode in MODES:
        d = load_trades(mode)
        if d.empty:
            continue
        r = metrics_cell(d)
        r["mode"] = mode
        mode_rows.append(r)
    modes = pd.DataFrame(mode_rows)
    write_csv(modes, "03_模式汇总独立重算_v1.csv")

    # 资格违规
    if viol_rows:
        viol = pd.concat(viol_rows, ignore_index=True)
    else:
        viol = pd.DataFrame(columns=["grid", "mode", "strategy", "pair", "open_date",
                                     "open_timestamp", "close_timestamp", "profit_abs",
                                     "enter_tag", "q_start_ms", "q_end_ms",
                                     "before_q_start", "after_q_end", "qualified"])
    write_csv(viol, "03_资格违规逐笔_v1.csv")

    # 全量逐笔资格核对（不区分模式）
    allt = pd.concat([load_trades(m) for m in MODES], ignore_index=True)
    qa = qualify_trades(allt, adm)
    viol_n = int(qa["violation"].sum())
    before_n = int(qa["before_q_start"].sum())
    after_n = int(qa["after_q_end"].sum())
    sig_before_n = int(qa["sig_before_q_start"].sum())
    conf_before_n = int(qa["confirm_before_q_start"].sum())
    conf_after_n = int(qa["confirm_after_q_end"].sum())
    unq_n = int((~qa["qualified"].fillna(False)).sum())
    close_after = int((qa["close_timestamp"] > qa["q_end_ms"]).sum())
    box_before = int((qa["box_before_q_start"] & ~qa["before_q_start"]).sum())

    # 与自报对拍
    selfrep = find_self_report()
    cmp_rows = []
    if not selfrep.empty:
        col_strat = "strategy"
        col_net = next((c for c in ("total_profit_usdt", "net_pnl", "total_profit",
                                    "net_profit_usdt") if c in selfrep), None)
        col_tr = next((c for c in ("trades_count", "trades", "total_trades") if c in selfrep), None)
        col_win = next((c for c in ("win_rate", "win_rate_pct") if c in selfrep), None)
        for r in res.itertuples():
            m = selfrep[selfrep[col_strat] == r.strategy]
            if m.empty:
                continue
            m = m.iloc[0]
            cmp_rows.append({
                "strategy": r.strategy,
                "trades_self": float(m[col_tr]) if col_tr else None,
                "trades_mine": r.trades,
                "net_self": float(m[col_net]) if col_net else None,
                "net_mine": r.net_pnl_u,
                "win_rate_self": float(m[col_win]) if col_win else None,
                "win_rate_mine": r.win_rate_pct,
                "net_diff": round(r.net_pnl_u - float(m[col_net]), 4) if col_net else None,
                "fast_stop_self": (float(m["fast_stoploss_count"])
                                   if "fast_stoploss_count" in selfrep.columns else None),
                "fast_stop_mine": r.quick_stop_count,
                "fast_stop_note": "自报口径=24h且亏损；本方=720min+亏损+MFE<3%（AUDIT-07更正口径）",
                "source": m["__src"],
            })
    cmp = pd.DataFrame(cmp_rows)
    if not cmp.empty:
        write_csv(cmp, "03_与自报指标对拍_v1.csv")

    info = {
        "cells_expected": 27,
        "cells_found": int((~res["missing"]).sum()) if "missing" in res else 0,
        "cells_missing": int(res["missing"].sum()) if "missing" in res else 27,
        "trades_total_27cells": int(len(allt)),
        "qualification_violations": viol_n,
        "violations_before_q_start": before_n,
        "violations_signal_before_q_start": sig_before_n,
        "violations_confirm_before_q_start": conf_before_n,
        "violations_confirm_after_q_end": conf_after_n,
        "violations_after_q_end_open": after_n,
        "violations_close_after_q_end": close_after,
        "trades_on_unqualified_symbols": unq_n,
        "candidates_box_before_q_start_signal_after": box_before,
        "self_report_compared": bool(not cmp.empty),
        "self_report_net_max_abs_diff": (round(float(cmp["net_diff"].abs().max()), 4)
                                         if not cmp.empty else None),
        "note": "违规逐笔列示、保留交易，不删除、不重算账户；开仓区间为主口径，另报平仓越界。",
    }
    jdump(os.path.join(OUT, "03_重算与资格核对摘要_v1.json"), info)

    print("== 27 组 ==")
    print(res.to_string(index=False))
    print("\n== 模式汇总 ==")
    print(modes.to_string(index=False))
    print("\n== 资格 ==")
    print(f"违规 {viol_n}（执行早于 q_start {before_n}、执行晚于 q_end {after_n}、"
          f"信号早于 q_start {sig_before_n}、确认早于 {conf_before_n}、确认晚于 {conf_after_n}、"
          f"不合格币 {unq_n}、平仓越界 {close_after}）；箱体早于起点但信号在期内 {box_before}")
    if not cmp.empty:
        print(f"自报对拍：最大净额差 {info['self_report_net_max_abs_diff']}U")
    print("完成 03")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-07 · 06：27 组指标独立重算并与反重力更正结果对拍。

对 27 组 trades_*.csv 独立计算：笔数/胜负/胜率/净收益/单笔均盈亏/均盈%/均亏%/
净期望/快速止损(≤12h 且亏 且 MFE<3%)/PF/回撤（已实现权益峰值口径 + 官方 zip 口径）。
与 01_全部运行索引与指标总表.csv（更正版）、04 报告表对拍，逐项标差异。
不重跑、不改上游。
"""
from __future__ import annotations

import json
import os
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import OUT, jdump, write_csv  # noqa: E402

FT07 = os.path.abspath(os.path.join(os.path.dirname(OUT), "..", "FT-ENTRY-07"))
GRID = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06", "Vs06_Z02", "Vs06_Z04",
        "Vs06_Z06", "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
MODES = [("C0", "Wyckoff_C_{g}"), ("C1", "Wyckoff_C1_{g}"), ("C2", "Wyckoff_C2_{g}")]


def realized_dd(df: pd.DataFrame, wallet: float = 2000.0) -> float:
    """已实现权益峰值回撤（按平仓时间累计已实现盈亏；AUDIT-03 既定口径）。"""
    if df.empty:
        return None
    d = df.sort_values("close_timestamp")
    eq = wallet + d["profit_abs"].cumsum()
    peak = eq.cummax()
    return float((peak - eq).max())


def official_dd(zip_path: str, strat: str):
    if not os.path.isfile(zip_path):
        return None
    z = zipfile.ZipFile(zip_path)
    for n in z.namelist():
        if n.endswith(".json") and not n.endswith(("_config.json",)) and strat in n or n.endswith(".json"):
            try:
                m = json.loads(z.read(n))
            except Exception:
                continue
            s = m.get("strategy", {})
            if strat in s:
                return float(s[strat].get("max_drawdown_abs") or 0.0)
    return None


def group_metrics(mode: str, tpl: str, g: str) -> dict:
    p = os.path.join(FT07, "out", f"trades_{tpl.format(g=g)}.csv")
    d = pd.read_csv(p, encoding="utf-8-sig")
    if d.empty:
        return {"mode": mode, "grid": g, "trades": 0}
    pnl = d["profit_abs"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    dur_min = d["trade_duration"].astype(float)
    mfe = (d["max_rate"].astype(float) - d["open_rate"].astype(float)) / d["open_rate"].astype(float)
    quick = (dur_min <= 720) & (pnl < 0) & (mfe < 0.03)
    return {
        "mode": mode, "grid": g,
        "trades": int(len(d)),
        "wins": int(len(wins)), "losses": int(len(losses)),
        "win_rate": round(100.0 * len(wins) / len(d), 2),
        "net": round(float(pnl.sum()), 2),
        "avg_trade": round(float(pnl.mean()), 2),
        "avg_win_pct": round(float(d.loc[pnl > 0, "profit_ratio"].astype(float).mean() * 100), 2),
        "avg_loss_pct": round(float(d.loc[pnl < 0, "profit_ratio"].astype(float).mean() * 100), 2),
        "net_ev_pct": round(float(d["profit_ratio"].astype(float).mean() * 100), 2),
        "expectancy_u": round(float(pnl.mean()), 4),
        "pf": round(float(wins.sum() / (-losses.sum())), 2) if len(losses) and losses.sum() < 0 else None,
        "quick_stop_n": int(quick.sum()),
        "quick_stop_ratio_all": round(100.0 * quick.sum() / len(d), 2),
        "quick_stop_ratio_loss": round(100.0 * quick.sum() / max(len(losses), 1), 2),
        "dd_realized": round(realized_dd(d), 2),
        "dd_official": round(official_dd(os.path.join(FT07, "out", f"backtest-result-{tpl.format(g=g)}.zip"),
                                          tpl.format(g=g)) or 0.0, 2),
        "first_open": str(d["open_date"].min()), "last_close": str(d["close_date"].max()),
    }


def main() -> None:
    rows = []
    for mode, tpl in MODES:
        for g in GRID:
            rows.append(group_metrics(mode, tpl, g))
    df = pd.DataFrame(rows)
    write_csv(df, "06_27组独立重算_v1.csv")

    # 汇总
    agg = {}
    for mode in ("C0", "C1", "C2"):
        sub = df[df["mode"] == mode]
        agg[mode] = {
            "trades": int(sub["trades"].sum()),
            "wins": int(sub["wins"].sum()), "losses": int(sub["losses"].sum()),
            "win_rate": round(100.0 * sub["wins"].sum() / sub["trades"].sum(), 2),
            "net": round(float(sub["net"].sum()), 2),
            "quick_stop_n": int(sub["quick_stop_n"].sum()),
            "quick_stop_ratio_all": round(100.0 * sub["quick_stop_n"].sum() / sub["trades"].sum(), 2),
            "quick_stop_ratio_loss": round(100.0 * sub["quick_stop_n"].sum() / sub["losses"].sum(), 2),
        }

    # 与对方 01 CSV（更正版）对拍
    theirs = pd.read_csv(os.path.join(FT07, "01_全部运行索引与指标总表.csv"), encoding="utf-8-sig")
    tm = {r["strategy"]: r for _, r in theirs.iterrows()}
    diffs = []
    for _, r in df.iterrows():
        strat = f"Wyckoff_{r['mode']}_{r['grid']}" if r["mode"] != "C0" else f"Wyckoff_C_{r['grid']}"
        t = tm.get(strat)
        if t is None:
            diffs.append({"strategy": strat, "问题": "对方表无此行"})
            continue
        for mine, th, label in [
            (r["trades"], t["trades_count"], "笔数"),
            (r["wins"], t["win_trades"], "胜笔"),
            (r["win_rate"], t["win_rate"], "胜率"),
            (r["net"], t["total_profit_usdt"], "净收益"),
            (r["avg_trade"], t["avg_trade_usdt"], "单笔均盈亏"),
            (r["net_ev_pct"], t["net_ev_pct"], "净期望%"),
            (r["pf"], t["profit_factor"], "PF"),
            (r["quick_stop_n"], t["quick_stop_count"], "快速止损笔数"),
            (r["quick_stop_ratio_all"], t["quick_stop_ratio_all"], "快速止损占比"),
            (r["dd_realized"], t["max_drawdown_usdt"], "最大回撤(对方CSV)"),
        ]:
            if th is None or (isinstance(th, float) and np.isnan(th)):
                continue
            if abs(float(mine) - float(th)) > max(0.02, abs(float(th)) * 0.005):
                diffs.append({"strategy": strat, "指标": label,
                              "本轮": mine, "对方01CSV": th})
    ddif = pd.DataFrame(diffs)
    write_csv(ddif, "06_与对方指标差异_v1.csv")

    # 与 04 报告表对拍（PF 与回撤两列）
    rep_diffs = [
        {"组": "Vs04_Z02/C0", "报告PF": 0.44, "本轮PF": float(df[(df["mode"] == "C0") & (df["grid"] == "Vs04_Z02")]["pf"].iloc[0])},
        {"组": "Vs04_Z04/C0", "报告PF": 0.65, "本轮PF": float(df[(df["mode"] == "C0") & (df["grid"] == "Vs04_Z04")]["pf"].iloc[0])},
        {"组": "Vs06_Z04/C1", "报告PF": 1.56, "本轮PF": float(df[(df["mode"] == "C1") & (df["grid"] == "Vs06_Z04")]["pf"].iloc[0])},
    ]
    jdump(os.path.join(OUT, "06_27组重算摘要_v1.json"), {
        "27组": df.to_dict("records"),
        "三模式汇总": agg,
        "与对方01CSV差异": diffs,
        "与报告PF抽对": rep_diffs,
        "回撤口径说明": "本轮 dd_realized=已实现权益峰值回撤（既定口径）；dd_official=freqtrade zip max_drawdown_abs（含浮盈盯市）；"
                       "对方 01CSV 的 max_drawdown_usdt 与报告表数值互不相同且与两者都不等",
    })
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    print(f"\n与对方01CSV差异 {len(diffs)} 项：")
    print(ddif.to_string(index=False) if not ddif.empty else "（无）")
    print("\nPF 抽对（报告 vs 本轮）：", rep_diffs)


if __name__ == "__main__":
    main()

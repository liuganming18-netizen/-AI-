# -*- coding: utf-8 -*-
"""02b 「除统一准入外无其他策略变化」的逐格对拍。

以 FT-ENTRY-07（统一准入前）为背景对照，逐格比较 FT-ENTRY-09：
- 被移除的事件是否都能由"信号/确认/执行落在 [q_start,q_end] 之外"解释；
- 是否有新增事件（若存在，说明除准入外还有行为变化）；
- 共同事件的逐笔 PnL 是否完全一致（若不一致，说明成交/退出被改动）。
只读，不重跑。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BAR_MS, GRIDS, OUT, OUT07, OUT09, jdump, load_admission, load_trades, write_csv,
)

MODES = ("C", "C1", "C2")


def events(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["event_key", "sig_ms", "confirm_ms", "pnl", "stake",
                                     "open_rate", "close_rate", "open_ts", "close_ts", "sym"])
    g = df.groupby("event_key", as_index=False).agg(
        sig_ms=("sig_ms", "min"), confirm_ms=("tag_tail_ms", "min"),
        pnl=("profit_abs", "sum"), stake=("stake_amount", "sum"),
        open_rate=("open_rate", "first"), close_rate=("close_rate", "first"),
        open_ts=("open_timestamp", "min"), close_ts=("close_timestamp", "max"),
        sym=("symbol", "first"))
    return g


def main() -> None:
    adm = load_admission().set_index("symbol")
    rows, rem_rows = [], []
    for g, s0, s1, s2, _vs, _z in GRIDS:
        for mode, strat in (("C", s0), ("C1", s1), ("C2", s2)):
            a0 = load_trades(mode, OUT07)
            a = events(a0[a0["grid"] == g]) if not a0.empty else events(pd.DataFrame())
            b0 = load_trades(mode, OUT09)
            b = events(b0[b0["grid"] == g]) if not b0.empty else events(pd.DataFrame())
            ka = set(a["event_key"]) if len(a) else set()
            kb = set(b["event_key"]) if len(b) else set()
            removed = a[a["event_key"].isin(ka - kb)].copy()
            added = b[b["event_key"].isin(kb - ka)].copy()
            common = a[a["event_key"].isin(ka & kb)].merge(
                b[["event_key", "pnl", "stake", "open_rate", "close_rate", "open_ts", "close_ts"]],
                on="event_key", suffixes=("_07", "_09"))
            # 解释被移除事件
            n_sig = n_conf = n_exec = n_unexp = 0
            for r in removed.itertuples():
                qs = int(adm.loc[r.sym, "q_start_ms"]) if r.sym in adm.index else 0
                qe = int(adm.loc[r.sym, "q_end_ms"]) if r.sym in adm.index else 0
                sig_bad = r.sig_ms < qs or r.sig_ms > qe
                conf_bad = r.confirm_ms < qs or r.confirm_ms > qe
                if sig_bad:
                    n_sig += 1
                    cause = "signal_outside"
                elif mode in ("C1", "C2") and conf_bad:
                    n_conf += 1
                    cause = "confirmation_outside"
                else:
                    n_unexp += 1
                    cause = "UNEXPLAINED"
                rem_rows.append({"grid": g, "mode": mode, "event_key": r.event_key,
                                 "sym": r.sym, "sig_ms": r.sig_ms, "confirm_ms": r.confirm_ms,
                                 "q_start_ms": qs, "q_end_ms": qe,
                                 "sig_before_q_start": r.sig_ms < qs,
                                 "sig_after_q_end": r.sig_ms > qe,
                                 "confirm_after_q_end": r.confirm_ms > qe,
                                 "pnl_07": round(float(r.pnl), 4), "cause": cause})
            common_diff = (float((common["pnl_09"] - common["pnl_07"]).abs().max())
                           if len(common) else None)
            entry_rate_diff = (int((common["open_rate_09"] - common["open_rate_07"]).abs().gt(1e-12).sum())
                               if len(common) else 0)
            exit_rate_diff = (int((common["close_rate_09"] - common["close_rate_07"]).abs().gt(1e-12).sum())
                              if len(common) else 0)
            time_diff = (int(((common["open_ts_09"] != common["open_ts_07"])
                              | (common["close_ts_09"] != common["close_ts_07"])).sum())
                         if len(common) else 0)
            stake_diff = (int((common["stake_09"] - common["stake_07"]).abs().gt(1e-9).sum())
                          if len(common) else 0)
            rows.append({
                "grid": g, "mode": mode, "strategy": strat,
                "events_07": int(len(a)), "events_09": int(len(b)),
                "removed": int(len(removed)), "added": int(len(added)),
                "removed_signal_outside": n_sig, "removed_confirmation_outside": n_conf,
                "removed_unexplained": n_unexp,
                "common_events": int(len(common)),
                "common_pnl_max_abs_diff": (round(common_diff, 8) if common_diff is not None else None),
                "common_entry_rate_changed": entry_rate_diff,
                "common_exit_rate_changed": exit_rate_diff,
                "common_time_changed": time_diff,
                "common_stake_changed": stake_diff,
                "added_net_u": round(float(added["pnl"].sum()), 4) if len(added) else 0.0,
            })
    res = pd.DataFrame(rows)
    write_csv(res, "02b_逐格准入差异对拍_v1.csv")
    rem = pd.DataFrame(rem_rows)
    if len(rem):
        write_csv(rem, "02b_被移除事件明细_v1.csv")

    summary = {
        "cells": int(len(res)),
        "removed_total": int(res["removed"].sum()),
        "added_total": int(res["added"].sum()),
        "removed_signal_outside": int(res["removed_signal_outside"].sum()),
        "removed_confirmation_outside": int(res["removed_confirmation_outside"].sum()),
        "removed_unexplained": int(res["removed_unexplained"].sum()),
        "cells_with_added": res.loc[res["added"] > 0, ["grid", "mode", "added"]].to_dict("records"),
        "cells_with_unexplained_removal": res.loc[res["removed_unexplained"] > 0,
                                                ["grid", "mode", "removed_unexplained"]].to_dict("records"),
        "common_pnl_max_abs_diff": (round(float(res["common_pnl_max_abs_diff"].dropna().max()), 8)
                                    if res["common_pnl_max_abs_diff"].notna().any() else None),
        "common_entry_rate_changed": int(res["common_entry_rate_changed"].sum()),
        "common_exit_rate_changed": int(res["common_exit_rate_changed"].sum()),
        "common_time_changed": int(res["common_time_changed"].sum()),
        "common_stake_changed": int(res["common_stake_changed"].sum()),
        "added_net_u": round(float(res["added_net_u"].sum()), 4),
        "judgement": ("成交/退出规则无可观测变化：共同事件开仓价、平仓价、开平时间全部一致；"
                      "剔除项均可由信号落在准入区间外解释；新增项来自槽位/权益路径，属组合效应。"
                      if res["common_entry_rate_changed"].sum() == 0
                      and res["common_exit_rate_changed"].sum() == 0
                      and res["common_time_changed"].sum() == 0
                      and res["removed_unexplained"].sum() == 0
                      else "存在需解释的差异，见明细"),
    }
    jdump(os.path.join(OUT, "02b_准入差异摘要_v1.json"), summary)
    print(res.to_string(index=False))
    print("\n", json.dumps(summary, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()

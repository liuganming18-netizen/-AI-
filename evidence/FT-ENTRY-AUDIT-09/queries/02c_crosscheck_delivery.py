# -*- coding: utf-8 -*-
"""02c 与反重力交付对账：115 笔违规清单 / 2 笔路径新增成交。

独立复算的"被移除事件"与交付 `out/01_旧版115笔违规交易清单.csv`、
`out/02_账户路径释放后新增成交清单.csv` 逐项对拍；同时给出逐笔口径差异
（事件级 115 vs 逐笔 116 的跨边界复入）。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import ENTRY09, OUT, OUT07, jdump, load_trades, write_csv  # noqa: E402


def main() -> None:
    rem = pd.read_csv(os.path.join(OUT, "02b_被移除事件明细_v1.csv"), encoding="utf-8-sig")
    # 用 FT-07 逐笔还原被移除事件对应的成交（可能 1 事件多笔）
    rows = []
    for mode in ("C", "C1", "C2"):
        d = load_trades(mode, OUT07)
        if d.empty:
            continue
        d["event_key"] = d["symbol"] + "|" + d["event_id"]
        sub = d[d["event_key"].isin(set(rem[rem["mode"] == mode]["event_key"]))]
        rows.append(sub[["strategy", "pair", "open_date", "open_timestamp", "event_key",
                         "profit_abs"]])
    mine = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    mine_keys = set(mine["strategy"] + "|" + mine["pair"] + "|" + mine["open_date"])

    their = pd.read_csv(os.path.join(ENTRY09, "out/01_旧版115笔违规交易清单.csv"),
                        encoding="utf-8-sig")
    their_keys = set(their["strategy"].astype(str) + "|" + their["pair"].astype(str)
                     + "|" + their["open_date"].astype(str))
    matched = mine_keys & their_keys
    extra = sorted(mine_keys - their_keys)
    missing = sorted(their_keys - mine_keys)

    added = pd.read_csv(os.path.join(ENTRY09, "out/02_账户路径释放后新增成交清单.csv"),
                        encoding="utf-8-sig")
    # 02b 口径的"新增事件"：FT-09 有、FT-07 无（逐格）
    a07 = load_trades("C", OUT07)
    a09 = load_trades("C", os.path.join(ENTRY09, "out"))
    a07["event_key"] = a07["symbol"] + "|" + a07["event_id"]
    a09["event_key"] = a09["symbol"] + "|" + a09["event_id"]
    added_rows = []
    for strat, grp in a09.groupby("strategy"):
        old = a07[a07["strategy"] == strat]
        new_keys = set(grp["event_key"]) - set(old["event_key"])
        for r in grp[grp["event_key"].isin(new_keys)].itertuples():
            added_rows.append({"strategy": r.strategy, "pair": r.pair,
                               "open_date": r.open_date, "profit_abs": r.profit_abs})
    add_df = pd.DataFrame(added_rows)
    added_keys = set(add_df["strategy"] + "|" + add_df["pair"] + "|" + add_df["open_date"])
    delivery_added_keys = set(added["strategy"].astype(str) + "|" + added["pair"].astype(str)
                              + "|" + added["open_date"].astype(str))

    summary = {
        "delivery_removed_rows": int(len(their)),
        "our_removed_grid_events": int(len(rem)),
        "our_removed_unique_events": int(rem["event_key"].nunique()),
        "our_removed_ft07_trades": int(len(mine)),
        "matched_event_trades": int(len(matched)),
        "delivery_only": len(missing),
        "ours_only_trade_level": len(extra),
        "ours_only_detail": [{"key": x} for x in extra[:5]],
        "delivery_added_rows": int(len(added)),
        "our_added_matched_rows": int(len(add_df)),
        "added_keys_match_delivery": int(len(added_keys & delivery_added_keys)),
        "added_keys_ours_only": sorted(added_keys - delivery_added_keys),
        "added_keys_delivery_only": sorted(delivery_added_keys - added_keys),
        "delivery_added_net_u": round(float(added["profit_abs"].sum()), 4),
        "our_added_net_u": round(float(add_df["profit_abs"].sum()), 4) if len(add_df) else None,
        "note": ("交付 115 为事件级清单；逐笔口径为 116 笔，多出的 1 笔是同一事件内的第二笔复入"
                 "（CKB C0 Vs06_Z02，其自身信号在准入内、但事件首个信号在准入前，属候选跨界）。"),
    }
    jdump(os.path.join(OUT, "02c_交付对账摘要_v1.json"), summary)
    pd.DataFrame([summary]).to_csv(os.path.join(OUT, "02c_交付对账_v1.csv"),
                                   index=False, encoding="utf-8-sig")
    print(summary)


if __name__ == "__main__":
    main()

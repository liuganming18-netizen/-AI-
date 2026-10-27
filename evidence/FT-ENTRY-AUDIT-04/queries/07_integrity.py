# -*- coding: utf-8 -*-
"""07 身份一致性、重复入场、逐组逐笔同一性、集中度明细。

新增（相对 AUDIT-03）：逐组两两比对 trades 键集合，确认「数值完全相同」的组
（A07/A08）到底是同一批成交还是巧合，避免把重复结果当成两个独立证据。

输出：out/07_*.csv
"""
from __future__ import annotations

import hashlib
import itertools
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, CELLS_A, OUT, OUT03, jdump, load_trades, write_csv,
)

ALL_CODES = [("BASE", BASELINE)] + [(c, s) for c, s, _, _ in CELLS_A]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    rows = []
    for code, strat in ALL_CODES:
        df = load_trades(strat)
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
            "trades_outside_timerange": int(((df["open_ts"] < 1638316800000) |
                                             (df["open_ts"] >= 1735689600000)).sum()),
            "trades_after_end_close": int((df["close_ts"] > 1735689600000).sum()),
            "is_short_any": bool(df["is_short"].any()),
            "leverage_values": sorted(df["leverage"].unique().tolist()),
        })
    integ = pd.DataFrame(rows)
    write_csv(integ, "07_身份与完整性_v1.csv")
    print(integ.to_string(index=False))

    # ── 7.2 逐组两两同一性 ──────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("07-2 组间逐笔同一性（同一批成交 = 不是两个独立证据）")
    print("=" * 78)
    keys = {}
    for code, strat in ALL_CODES:
        df = load_trades(strat)
        keys[code] = {(r["pair"], int(r["open_ts"]), round(float(r["profit_abs"]), 6))
                      for _, r in df.iterrows()}
    pair_rows = []
    for a, b in itertools.combinations([c for c, _ in ALL_CODES], 2):
        ka, kb = keys[a], keys[b]
        inter = len(ka & kb)
        pair_rows.append({
            "code_a": a, "code_b": b,
            "trades_a": len(ka), "trades_b": len(kb),
            "identical_trades": inter,
            "jaccard": round(inter / len(ka | kb), 4) if (ka | kb) else None,
            "fully_identical": ka == kb,
        })
    pairs = pd.DataFrame(pair_rows)
    write_csv(pairs, "07_组间逐笔同一性_v1.csv")
    ident = pairs[pairs["fully_identical"]]
    print("完全相同的组对：", ident[["code_a", "code_b", "trades_a"]].to_dict("records") or "无")
    near = pairs[(pairs["jaccard"] >= 0.9) & (~pairs["fully_identical"])]
    if len(near):
        print("高度重叠（Jaccard≥0.9 但非完全相同）：")
        print(near.to_string(index=False))

    # 逐笔 CSV 字节级哈希（含 BOM 差异，仅作登记）
    csv_hash = pd.DataFrame([{
        "code": code, "strategy": strat,
        "trades_csv_sha256": sha256(os.path.join(OUT03, f"trades_{strat}.csv")),
    } for code, strat in ALL_CODES])
    write_csv(csv_hash, "07_逐笔文件哈希_v1.csv")

    # ── 7.3 利润集中度 ─────────────────────────────────────────────────
    conc_rows = []
    for code, strat in ALL_CODES:
        df = load_trades(strat).sort_values("profit_abs", ascending=False).reset_index(drop=True)
        total = float(df["profit_abs"].sum())
        rec = {"code": code, "strategy": strat, "trades": len(df), "net": round(total, 4)}
        for k in (1, 2, 3, 5):
            s = float(df["profit_abs"].iloc[:k].sum())
            rec[f"top{k}_sum"] = round(s, 4)
            rec[f"top{k}_share_pct"] = round(100 * s / total, 2) if abs(total) > 1e-9 else None
        # 赚钱需要几笔：累计净额首次 > 0 所需笔数（按盈亏排序）
        cum = df["profit_abs"].cumsum()
        first_pos = int((cum > 0).idxmax() + 1) if (cum > 0).any() else None
        rec["trades_needed_to_turn_positive"] = first_pos
        conc_rows.append(rec)
    conc = pd.DataFrame(conc_rows)
    write_csv(conc, "07_利润集中度_v1.csv")
    print("\n利润集中度\n", conc.to_string(index=False))

    top_rows = []
    for code, strat in ALL_CODES:
        df = load_trades(strat).sort_values("profit_abs", ascending=False).head(5)
        for rank, (_, r) in enumerate(df.iterrows(), 1):
            top_rows.append({
                "code": code, "rank": rank, "pair": r["pair"],
                "open_date": str(r["open_date"])[:16], "close_date": str(r["close_date"])[:16],
                "profit_abs": round(float(r["profit_abs"]), 4),
                "exit_reason": r["exit_reason"], "event_id": r["event_id"],
            })
    write_csv(pd.DataFrame(top_rows), "07_前五大盈利单_v1.csv")

    jdump(os.path.join(OUT, "07_完整性摘要_v1.json"), {
        "integrity": integ.to_dict("records"),
        "pairwise_identity": pairs.to_dict("records"),
        "fully_identical_pairs": ident[["code_a", "code_b"]].to_dict("records"),
        "concentration": conc.to_dict("records"),
        "checks": {
            "all_tag_symbol_match": bool((integ["tag_symbol_mismatch"] == 0).all()),
            "all_within_timerange": bool((integ["trades_outside_timerange"] == 0).all()),
            "no_dup_fills_any_group": bool((integ["extra_fills_same_event"] == 0).all()),
            "no_short_any": bool(~integ["is_short_any"].any()),
            "all_leverage_1": bool(integ["leverage_values"].apply(lambda v: v == [1.0]).all()),
        },
    })
    print("\n完成")


if __name__ == "__main__":
    main()

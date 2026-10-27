# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-02 · 02 预定组合完整性、归档一致性与重复入场核查

只读 FT-ENTRY-01。产出：
  out/06_归档与逐笔一致性_v1.csv
  out/07_同候选重复入场_v1.csv
  out/08_逐币成交分布_v1.csv
  out/09_回测元数据与引擎自报_v1.csv
  out/10_汇总表与对齐表重建校验_v1.csv
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import zipfile
from collections import Counter, defaultdict

import pandas as pd

csv.field_size_limit(10 ** 9)

BASE = "/Users/boos/Desktop/威科夫因子共振趋势"
ENTRY = os.path.join(BASE, "evidence/FT-ENTRY-01")
OUT = os.path.join(BASE, "evidence/FT-ENTRY-AUDIT-02/out")

ARMS = ["WyckoffBaseline", "WyckoffEntryA1_NearTop", "WyckoffEntryA2_AtBoxHigh",
        "WyckoffEntryC1_StopSweep", "WyckoffEntryC2_StopBar"]

TAG_RE = re.compile(r"^cand_(?:(A1|A2|C1|C2)_)?(ev_.+)_(\d{13})$")


def load(arm):
    df = pd.read_csv(os.path.join(ENTRY, "out", f"trades_{arm}.csv"), encoding="utf-8-sig")
    if "pair" not in df.columns:
        df = df.rename(columns={df.columns[0]: "pair"})
    df["event_id"] = df["enter_tag"].astype(str).str.extract(TAG_RE)[1]
    df["profit_abs"] = df["profit_abs"].astype(float)
    return df


def zip_json(arm):
    z = zipfile.ZipFile(os.path.join(ENTRY, "out", f"backtest-result-{arm}.zip"))
    n = [x for x in z.namelist() if x.endswith(".json") and not x.endswith("_config.json")][0]
    return json.loads(z.read(n))["strategy"][arm], z


def main():
    os.makedirs(OUT, exist_ok=True)
    trades = {a: load(a) for a in ARMS}
    meta = {a: zip_json(a)[0] for a in ARMS}

    # ---------- 归档 vs 逐笔 ----------
    cons_rows = []
    for a in ARMS:
        m, z = zip_json(a)
        zt = m["trades"]
        csvt = trades[a]
        zk = {(t["pair"], int(t["open_timestamp"]), int(t["close_timestamp"])): round(float(t["profit_abs"]), 8) for t in zt}
        ck = {(t["pair"], int(t["open_timestamp"]), int(t["close_timestamp"])): round(float(t["profit_abs"]), 8)
              for t in csvt.to_dict("records")}
        only_z = set(zk) - set(ck)
        only_c = set(ck) - set(zk)
        diff_val = [k for k in set(zk) & set(ck) if abs(zk[k] - ck[k]) > 1e-6]
        cons_rows.append({
            "arm": a,
            "zip_trades": len(zt), "csv_trades": len(csvt),
            "identical_keyset": len(only_z) == 0 and len(only_c) == 0,
            "only_in_zip": len(only_z), "only_in_csv": len(only_c),
            "pnl_mismatch": len(diff_val),
            "zip_final_balance": round(m["final_balance"], 6),
            "csv_pnl_sum_plus_2000": round(2000 + csvt["profit_abs"].sum(), 6),
            "balance_reconciles": abs(m["final_balance"] - (2000 + csvt["profit_abs"].sum())) < 1e-4,
        })
    pd.DataFrame(cons_rows).to_csv(os.path.join(OUT, "06_归档与逐笔一致性_v1.csv"),
                                   index=False, encoding="utf-8-sig")

    # ---------- 同候选重复入场 ----------
    dup_rows = []
    dup_detail = []
    for a in ARMS:
        df = trades[a]
        c = Counter(df["event_id"])
        dups = {k: v for k, v in c.items() if v > 1}
        extra = sum(v - 1 for v in dups.values())
        extra_pnl = 0.0
        for k, v in dups.items():
            sub = df[df["event_id"] == k].sort_values("open_timestamp")
            # 首次成交之后的都算"同候选重复"
            extra_pnl += float(sub["profit_abs"].iloc[1:].sum())
            for _, r in sub.iterrows():
                dup_detail.append({
                    "arm": a, "event_id": k, "pair": r["pair"],
                    "open_date": r["open_date"], "close_date": r["close_date"],
                    "profit_abs": round(float(r["profit_abs"]), 4),
                    "exit_reason": r["exit_reason"],
                    "stake_amount": round(float(r["stake_amount"]), 4),
                    "is_extra_reentry": int(r["open_timestamp"] != sub["open_timestamp"].min()),
                })
        dup_rows.append({
            "arm": a, "trades": len(df), "unique_events": len(c),
            "dup_events": len(dups), "extra_trades": extra,
            "extra_trades_share_pct": round(extra / len(df) * 100, 2),
            "extra_trades_pnl_sum": round(extra_pnl, 4),
            "max_trades_per_event": max(c.values()),
        })
    pd.DataFrame(dup_rows).to_csv(os.path.join(OUT, "07_同候选重复入场_v1.csv"),
                                  index=False, encoding="utf-8-sig")
    pd.DataFrame(dup_detail).to_csv(os.path.join(OUT, "07b_同候选重复入场明细_v1.csv"),
                                    index=False, encoding="utf-8-sig")

    # ---------- 逐币成交分布 ----------
    pairs = sorted(set().union(*[set(t["pair"]) for t in trades.values()]))
    pair_rows = []
    for p in pairs:
        row = {"pair": p}
        for a in ARMS:
            sub = trades[a][trades[a]["pair"] == p]
            row[f"n_{a}"] = len(sub)
            row[f"pnl_{a}"] = round(float(sub["profit_abs"].sum()), 4)
        pair_rows.append(row)
    pd.DataFrame(pair_rows).to_csv(os.path.join(OUT, "08_逐币成交分布_v1.csv"),
                                   index=False, encoding="utf-8-sig")

    # ---------- 引擎自报元数据 ----------
    keys = ["total_trades", "timerange", "backtest_start", "backtest_end", "backtest_days",
            "starting_balance", "final_balance", "max_open_trades_setting", "rejected_signals",
            "timedout_entry_orders", "canceled_trade_entries", "max_drawdown_account",
            "max_drawdown_abs", "trades_per_day", "winrate", "profit_factor", "expectancy",
            "p_value", "sqn", "sharpe", "sortino", "calmar", "market_change",
            "enable_protections", "timeframe", "timeframe_detail", "use_custom_stoploss"]
    meta_rows = []
    for a in ARMS:
        m = meta[a]
        r = {"arm": a}
        for k in keys:
            r[k] = m.get(k)
        meta_rows.append(r)
    pd.DataFrame(meta_rows).to_csv(os.path.join(OUT, "09_回测元数据与引擎自报_v1.csv"),
                                   index=False, encoding="utf-8-sig")

    # ---------- 汇总表/对齐表重建校验 ----------
    rebuild = []

    # 01 总表：逐字段重算
    deliv = pd.read_csv(os.path.join(ENTRY, "out/01_提前入场双路线对照总表.csv"), encoding="utf-8-sig")
    for _, r in deliv.iterrows():
        a = r["strategy_name"]
        df = trades[a]
        p = df["profit_abs"]
        rebuild.append({
            "table": "01", "arm": a,
            "delivered_trades": int(r["trade_count"]), "recomputed_trades": len(df),
            "trades_match": int(r["trade_count"]) == len(df),
            "delivered_net": round(float(r["net_pnl"]), 4),
            "recomputed_net": round(float(p.sum()), 4),
            "net_match": abs(float(r["net_pnl"]) - p.sum()) < 1e-4,
            "delivered_wins": int(r["win_count"]), "recomputed_wins": int((p > 0).sum()),
            "delivered_losses": int(r["loss_count"]), "recomputed_losses": int((p < 0).sum()),
            "zero_pnl_trades": int((p == 0).sum()),
            "delivered_pf": round(float(r["profit_factor"]), 4),
            "recomputed_pf": round(float(p[p > 0].sum() / abs(p[p < 0].sum())), 4),
        })

    # 02 对齐表
    al = pd.read_csv(os.path.join(ENTRY, "out/02_同一候选逐笔对齐核对表.csv"), encoding="utf-8-sig")
    base = trades["WyckoffBaseline"].set_index("event_id")
    for a in ARMS[1:]:
        sub = al[al["strategy"] == a]
        exp = trades[a][trades[a]["event_id"].isin(base.index)]
        rebuild.append({
            "table": "02", "arm": a,
            "delivered_rows": len(sub), "recomputed_rows": len(exp),
            "rows_match": len(sub) == len(exp),
            "delivered_pnl_sum": round(float(sub["early_net_pnl"].sum()), 4),
            "recomputed_pnl_sum": round(float(exp["profit_abs"].sum()), 4),
            "pnl_match": abs(float(sub["early_net_pnl"].sum()) - exp["profit_abs"].sum()) < 1e-3,
        })

    # 03 未匹配表
    un = pd.read_csv(os.path.join(ENTRY, "out/03_基准未入场之提前交易表.csv"), encoding="utf-8-sig")
    for a in ARMS[1:]:
        sub = un[un["strategy"] == a]
        exp = trades[a][~trades[a]["event_id"].isin(base.index)]
        rebuild.append({
            "table": "03", "arm": a,
            "delivered_rows": len(sub), "recomputed_rows": len(exp),
            "rows_match": len(sub) == len(exp),
            "delivered_pnl_sum": round(float(sub["net_pnl_u"].sum()), 4),
            "recomputed_pnl_sum": round(float(exp["profit_abs"].sum()), 4),
            "pnl_match": abs(float(sub["net_pnl_u"].sum()) - exp["profit_abs"].sum()) < 1e-3,
        })
    pd.DataFrame(rebuild).to_csv(os.path.join(OUT, "10_汇总表与对齐表重建校验_v1.csv"),
                                 index=False, encoding="utf-8-sig")

    # ---------- 控制台 ----------
    print("=== 归档 vs 逐笔 ===")
    for r in cons_rows:
        print(f"  {r['arm']:32s} zip={r['zip_trades']:4d} csv={r['csv_trades']:4d} "
              f"同集合={r['identical_keyset']} 余额对账={r['balance_reconciles']}")
    print("\n=== 同候选重复入场 ===")
    for r in dup_rows:
        print(f"  {r['arm']:32s} trades={r['trades']:4d} events={r['unique_events']:4d} "
              f"重复事件={r['dup_events']:3d} 多出={r['extra_trades']:3d} 多出笔盈亏={r['extra_trades_pnl_sum']:+9.3f}")
    print("\n=== 引擎自报 rejected_signals / p_value ===")
    for r in meta_rows:
        print(f"  {r['arm']:32s} rejected={r['rejected_signals']:4d} p={r['p_value']:.4f} "
              f"market_change={r['market_change']*100:.2f}%")
    print("\n=== 表重建 ===")
    for r in rebuild:
        ok = r.get("trades_match", r.get("rows_match"))
        print(f"  {r['table']:3s} {r['arm']:32s} 一致={ok} "
              f"笔数={r.get('delivered_trades', r.get('delivered_rows'))}/{r.get('recomputed_trades', r.get('recomputed_rows'))}")


if __name__ == "__main__":
    main()

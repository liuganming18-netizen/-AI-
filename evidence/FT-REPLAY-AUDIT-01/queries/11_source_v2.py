# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 / 11：源账本独立复算 + 27 组 / 4,325 笔的分组与去重关系（v2）。

不信任何自报汇总：全部从 CSV / jsonl 逐行重算。
三份口径对照：
  A 源账本     evidence/FT-ENTRY-09/out/trades_<策略>.csv（27 份）
  B 组级登记   回测日志/.../runs_ft_entry_09_research/run_*/trades.jsonl（27 个）
  C 币级登记   回测日志/.../runs_ft_replay_01/run_*/trades.jsonl（1065 个，软链到主运行根）
"""
from __future__ import annotations

import glob
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
E09 = os.path.join(ROOT, "evidence/FT-ENTRY-09")
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
BT = os.path.join(ROOT, "回测日志/event_trend_v0_1")
GRID_DIR = os.path.join(BT, "runs_ft_entry_09_research")
SYM_DIR = os.path.join(BT, "runs_ft_replay_01")
GRIDS = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06", "Vs06_Z02", "Vs06_Z04",
         "Vs06_Z06", "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
MODES = ["C", "C1", "C2"]


def read_jsonl(p):
    rows = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    # ── A 源账本 ────────────────────────────────────────────────
    src_parts = []
    for mode in MODES:
        for g in GRIDS:
            st = "Wyckoff_%s_%s" % (mode, g)
            p = os.path.join(E09, "out", "trades_%s.csv" % st)
            d = pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
            d["strategy"] = st
            d["mode"] = mode
            d["grid"] = g
            src_parts.append(d)
    src = pd.concat(src_parts, ignore_index=True)
    src["symbol"] = (src["pair"].astype(str).str.split("/").str[0]
                    + src["pair"].astype(str).str.split("/").str[1].str.split(":").str[0])
    src["open_ms"] = pd.to_numeric(src["open_timestamp"], errors="coerce").astype("int64")
    src["close_ms"] = pd.to_numeric(src["close_timestamp"], errors="coerce").astype("int64")
    src["net"] = pd.to_numeric(src["profit_abs"], errors="coerce")
    src["trade_key"] = src["strategy"] + "|" + src["pair"].astype(str) + "|" + src["open_ms"].astype(str)

    # 合并表独立复核（不经过 27 份单表）
    cons = pd.read_csv(os.path.join(E09, "out", "00_all_27_trades_consolidated.csv"),
                       encoding="utf-8-sig", low_memory=False)
    cons["net"] = pd.to_numeric(cons["profit_abs"], errors="coerce")

    # ── B 组级登记 ──────────────────────────────────────────────
    grid_rows = []
    for p in sorted(glob.glob(os.path.join(GRID_DIR, "run_FT_ENTRY_09_*"))):
        rid = os.path.basename(p)
        tr = read_jsonl(os.path.join(p, "trades.jsonl"))
        for t in tr:
            grid_rows.append({
                "run_id": rid,
                "strategy": rid.replace("run_FT_ENTRY_09_", ""),
                "trade_id": t.get("trade_id"),
                "symbol": t.get("symbol"),
                "entry_ms": t.get("entry_time_ms"),
                "exit_ms": t.get("exit_time_ms"),
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "qty": t.get("qty"),
                "P0": t.get("P0"),
                "net": t.get("net_pnl"),
                "mode": t.get("mode"),
            })
    grid = pd.DataFrame(grid_rows)

    # ── C 币级登记 ──────────────────────────────────────────────
    sym_rows = []
    for p in sorted(glob.glob(os.path.join(SYM_DIR, "run_FT_ENTRY_09_*"))):
        rid = os.path.basename(p)
        for t in read_jsonl(os.path.join(p, "trades.jsonl")):
            sym_rows.append({
                "run_id": rid,
                "trade_id": t.get("trade_id"),
                "symbol": t.get("symbol"),
                "entry_ms": t.get("entry_time_ms"),
                "exit_ms": t.get("exit_time_ms"),
                "net": t.get("net_pnl"),
                "mode": t.get("mode"),
            })
    sym = pd.DataFrame(sym_rows)
    sym["strategy"] = sym["run_id"].str.replace("run_FT_ENTRY_09_", "", regex=False)
    # run_id 形如 run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT ⇒ 去掉末尾 _<SYMBOL>，
    # 再补回源账本用的 "Wyckoff_" 前缀（run_id 里没有这个前缀，两边口径必须对齐）
    sym["strategy"] = sym.apply(
        lambda r: "Wyckoff_" + r["run_id"].replace("run_FT_ENTRY_09_", "")[:-(len(str(r["symbol"])) + 1)],
        axis=1)
    sym["trade_key"] = sym["strategy"] + "|" + sym["symbol"].astype(str) + "|" + \
        pd.to_numeric(sym["entry_ms"], errors="coerce").astype("int64").astype(str)

    # ── 逐组对照 ────────────────────────────────────────────────
    rows = []
    for mode in MODES:
        for g in GRIDS:
            st = "Wyckoff_%s_%s" % (mode, g)
            a = src[src.strategy == st]
            b = grid[grid.strategy == st]
            c = sym[sym.strategy == st]
            rows.append({
                "mode": mode,
                "参数组": g,
                "策略": st,
                "A_源账本笔数": len(a),
                "B_组级登记笔数": len(b),
                "C_币级登记笔数": len(c),
                "C_币级run数": c.run_id.nunique(),
                "A_源账本净额": round(float(a.net.sum()), 6),
                "B_组级净额": round(float(b.net.astype(float).sum()), 6),
                "C_币级净额": round(float(c.net.astype(float).sum()), 6),
                "笔数三方一致": len(a) == len(b) == len(c),
                "净额三方一致": abs(float(a.net.sum()) - float(b.net.astype(float).sum())) < 1e-6
                                and abs(float(a.net.sum()) - float(c.net.astype(float).sum())) < 1e-6,
            })
    per_group = pd.DataFrame(rows)
    per_group.to_csv(os.path.join(OUT, "11_逐组三方对照_v2.csv"), index=False, encoding="utf-8-sig")

    # ── 去重 ────────────────────────────────────────────────────
    dup_src_within = int(src.duplicated(subset=["trade_key"]).sum())
    dup_grid = int(grid.duplicated(subset=["trade_id"]).sum())
    dup_sym = int(sym.duplicated(subset=["trade_key"]).sum())
    # 同一笔在 B、C 两套登记里是否各出现一次（同一笔被登记两次 ⇒ 两套口径合计会翻倍）
    both = set(grid.trade_id.dropna()) & set(sym.trade_id.dropna())
    # 源账本里同一 pair+open_ms 在不同策略组重复出现（合法：不同参数组）
    cross_group_same_event = int(src.duplicated(subset=["pair", "open_ms"]).sum())
    # 同一事件在不同模式/参数组 = 不同实验，不算重复；但同一组内必须唯一
    dup_in_group = src.groupby("strategy").apply(
        lambda d: int(d.duplicated(subset=["pair", "open_ms"]).sum()), include_groups=False)
    dup_in_group_total = int(dup_in_group.sum())

    # ── 合并表核对 ──────────────────────────────────────────────
    cons_vs_src = {
        "合并表笔数": int(len(cons)),
        "27份单表合计笔数": int(len(src)),
        "笔数一致": int(len(cons)) == int(len(src)),
        "合并表净额": round(float(cons.net.sum()), 6),
        "27份单表净额": round(float(src.net.sum()), 6),
        "净额差": round(abs(float(cons.net.sum()) - float(src.net.sum())), 9),
    }

    # ── 批次自报 vs 实算 ────────────────────────────────────────
    gm = json.load(open(os.path.join(GRID_DIR, "batch_meta.json"), encoding="utf-8"))
    sm = json.load(open(os.path.join(SYM_DIR, "batch_meta.json"), encoding="utf-8"))
    batch_check = {
        "组级自报runs": gm.get("runs_total"),
        "组级实际run目录": int(grid.run_id.nunique()),
        "组级自报trades": gm.get("trades_total"),
        "组级实际trades": int(len(grid)),
        "组级自报净额": gm.get("net_pnl_sum"),
        "币级自报runs": sm.get("runs_total"),
        "币级实际run目录": int(sym.run_id.nunique()),
        "币级自报trades": sm.get("trades_total"),
        "币级实际trades": int(len(sym)),
        "币级自报净额": sm.get("net_pnl_sum"),
        "币级实际净额": round(float(sym.net.astype(float).sum()), 8),
        "币级自报symbols": sm.get("symbols_count"),
        "币级实际symbols": int(sym.symbol.nunique()),
        "币级自报groups": sm.get("groups"),
        "币级实际groups": int(sym.strategy.nunique()),
    }

    summary = {
        "A_源账本": {
            "笔数": int(len(src)),
            "组数": int(src.strategy.nunique()),
            "币种数": int(src.symbol.nunique()),
            "净额": round(float(src.net.sum()), 6),
            "按模式": {m: {"笔数": int((src["mode"] == m).sum()),
                           "净额": round(float(src[src["mode"] == m].net.sum()), 6)} for m in MODES},
            "组内重复键": dup_in_group_total,
            "组间同币同开仓时刻": cross_group_same_event,
        },
        "B_组级登记": {"笔数": int(len(grid)), "run数": int(grid.run_id.nunique()),
                       "净额": round(float(grid.net.astype(float).sum()), 6),
                       "重复trade_id": dup_grid},
        "C_币级登记": {"笔数": int(len(sym)), "run数": int(sym.run_id.nunique()),
                       "净额": round(float(sym.net.astype(float).sum()), 6),
                       "重复键": dup_sym},
        "合并表核对": cons_vs_src,
        "去重": {
            "源账本全局重复键": dup_src_within,
            "B与C同时登记的trade_id数": len(both),
            "说明": "B、C 是同一批 4325 笔的两种登记粒度（组级 27 / 币级 1065），"
                    "不是两批数据；两套口径各自合计都等于 4325，但**跨批次相加会翻倍**。",
        },
        "批次自报vs实算": batch_check,
        "逐组全部一致": bool(per_group["笔数三方一致"].all() and per_group["净额三方一致"].all()),
        "不一致组": per_group[~(per_group["笔数三方一致"] & per_group["净额三方一致"])].to_dict("records"),
    }
    with open(os.path.join(OUT, "11_分组关系_v2.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)

    print(per_group.to_string(index=False))
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()

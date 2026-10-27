# -*- coding: utf-8 -*-
"""02 开跑前预检（工单「开跑前」三项 + 交付完整性）。

1. 反重力准入表 vs AUDIT-08 逐币区间（74 币，逐币 q_start/q_end/layer）；
2. 200 根 4h 预热收盘时间、边界处理、候选跨界规则（从原始覆盖表重算 + 代码核对）；
3. 除统一准入外无其他策略变化（基类逐行 diff + config 键级 diff + 27 个包装策略参数）；
4. 27 组完整性（trades + backtest zip）。

只读上游；不重跑回测；结果未齐时明确标 PENDING，不用部分结果代替。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    ADMISSION, AUDIT02, ENTRY07, ENTRY09, GRIDS, OUT, OUT09, jdump,
    load_admission, sha256_file, write_csv,
)

AUDIT08 = os.path.join(os.path.dirname(os.path.dirname(OUT)), "FT-ENTRY-AUDIT-08")
COVER = os.path.join(AUDIT02, "out/02_币池与数据覆盖_v1.csv")
BASE09 = os.path.join(ENTRY09, "user_data/strategies/WyckoffParamExplorationBase.py")
BASE07 = os.path.join(ENTRY07, "user_data/strategies/WyckoffParamExplorationBase.py")
CFG09 = os.path.join(ENTRY09, "user_data/config.json")
CFG07 = os.path.join(ENTRY07, "user_data/config.json")
RULES = os.path.join(ENTRY09, "user_data/strategies/pair_eligibility_rules.json")
RESEARCH_START = pd.Timestamp("2021-12-01", tz="UTC")
RESEARCH_END = pd.Timestamp("2025-01-01", tz="UTC")


def admission_check() -> tuple[pd.DataFrame, dict]:
    adm = load_admission()
    old = pd.read_csv(os.path.join(AUDIT08, "out/04_74币逐币有效区间_v1.csv"),
                     encoding="utf-8-sig")
    old = old[["symbol", "L1_q_start_200bars", "q_end", "layer"]]
    cov = pd.read_csv(COVER, encoding="utf-8-sig")
    cov["symbol"] = cov["pair"].str.split("/").str[0]
    cov["4h_first"] = pd.to_datetime(cov["4h_first"], utc=True)
    m = adm.merge(old, on="symbol", suffixes=("_new", "_old"))
    m = m.merge(cov[["symbol", "4h_first", "4h_bars_before_research_start"]], on="symbol")
    rows = []
    for r in m.to_dict("records"):
        bars_before = int(r["4h_bars_before_research_start"])
        q_calc = (RESEARCH_START if bars_before >= 200 else
                  max(RESEARCH_START, r["4h_first"] + pd.Timedelta(hours=4 * 200)))
        q_new = pd.Timestamp(r["L1_q_start_ms"], unit="ms", tz="UTC") if r["L1_q_start_ms"] else None
        q_old = pd.to_datetime(r["L1_q_start_200bars"], utc=True)
        qe_new = pd.Timestamp(r["q_end_ms"], unit="ms", tz="UTC") if r["q_end_ms"] else None
        qe_old = pd.to_datetime(r["q_end"], utc=True)
        rows.append({
            "symbol": r["symbol"], "layer_new": r["layer_new"], "layer_old": r["layer_old"],
            "qualified_new": bool(r["qualified_in_research"]),
            "q_start_new": q_new, "q_start_old": q_old, "q_start_recalc": q_calc,
            "q_end_new": qe_new, "q_end_old": qe_old,
            "layer_match": r["layer_new"] == r["layer_old"],
            "start_match_old": (q_new == q_old) if q_new is not None else str(r["L1_q_start_200bars_utc"]) == "NEVER",
            "end_match_old": (qe_new == qe_old) if qe_new is not None else str(r["q_end_utc"]) == "NEVER",
            "start_match_recalc": (q_new == q_calc) if q_new is not None else str(r["L1_q_start_200bars_utc"]) == "NEVER",
        })
    df = pd.DataFrame(rows)
    qualified = df[df["qualified_new"]]
    unqualified = df[~df["qualified_new"]]
    summary = {
        "rows": int(len(df)),
        "qualified": int(len(qualified)),
        "unqualified": int(len(unqualified)),
        "layer_mismatch": int((~df["layer_match"]).sum()),
        "qualified_start_mismatch_vs_AUDIT08": int((~qualified["start_match_old"]).sum()),
        "qualified_end_mismatch_vs_AUDIT08": int((~qualified["end_match_old"]).sum()),
        "qualified_start_mismatch_vs_raw_coverage": int((~qualified["start_match_recalc"]).sum()),
        "unqualified_never_qstart": sorted(unqualified["symbol"].tolist()),
        "q_end_unique_qualified": sorted({str(x) for x in qualified["q_end_new"].unique()}),
    }
    return df, summary


def rules_check() -> dict:
    if not os.path.isfile(RULES):
        return {"status": "PENDING", "reason": "pair_eligibility_rules.json 未到达"}
    rules = json.load(open(RULES, encoding="utf-8"))
    adm = load_admission().set_index("pair")
    bad = []
    for pair, r in rules.items():
        if pair not in adm.index:
            bad.append((pair, "not_in_admission"))
            continue
        a = adm.loc[pair]
        if int(r.get("q_start_ms", 0)) != int(a["q_start_ms"]):
            bad.append((pair, "q_start", r.get("q_start_ms"), int(a["q_start_ms"])))
        if int(r.get("q_end_ms", 0)) != int(a["q_end_ms"]):
            bad.append((pair, "q_end", r.get("q_end_ms"), int(a["q_end_ms"])))
        if bool(r.get("qualified")) != bool(a["qualified"]):
            bad.append((pair, "qualified"))
    return {"status": "OK" if not bad else "MISMATCH", "pairs": len(rules),
            "mismatches": bad[:20], "n_mismatches": len(bad)}


def code_check() -> dict:
    if not os.path.isfile(BASE09):
        return {"status": "PENDING", "reason": "FT-ENTRY-09 基类未到达"}
    if not os.path.isfile(BASE07):
        return {"status": "MISSING_BASELINE", "reason": "FT-ENTRY-07 基类缺失"}
    a = open(BASE07, encoding="utf-8").read().splitlines()
    b = open(BASE09, encoding="utf-8").read().splitlines()
    import difflib
    diff = list(difflib.unified_diff(a, b, fromfile="FT-ENTRY-07", tofile="FT-ENTRY-09",
                                     lineterm="", n=3))
    added = sum(1 for x in diff if x.startswith("+") and not x.startswith("+++"))
    removed = sum(1 for x in diff if x.startswith("-") and not x.startswith("---"))
    # 关键函数区段的差异（用函数名定位）
    funcs = ["custom_stake_amount", "custom_stoploss", "custom_exit",
             "populate_entry_trend", "populate_exit_trend", "informative_pairs",
             "confirm_trade_entry", "populate_indicators"]
    func_changed = {}
    for fn in funcs:
        ia = next((i for i, l in enumerate(a) if re.match(rf"\s*def {fn}\(", l)), None)
        ib = next((i for i, l in enumerate(b) if re.match(rf"\s*def {fn}\(", l)), None)
        if ia is None or ib is None:
            func_changed[fn] = "missing"
            continue
        # 粗略取函数体到下一个同级 def
        def block(lines, start):
            out = [lines[start]]
            for l in lines[start + 1:]:
                if re.match(r"\s*def \w+\(", l) and len(l) - len(l.lstrip()) <= len(lines[start]) - len(lines[start].lstrip()):
                    break
                out.append(l)
            return out
        func_changed[fn] = (block(a, ia) != block(b, ib))
    # 准入相关关键词
    kw = {k: sum(1 for l in b if k in l) for k in
          ("q_start_ms", "q_end_ms", "eligibility", "admitted", "qualified")}
    outpath = os.path.join(OUT, "02_基类差分_v1.patch")
    with open(outpath, "w", encoding="utf-8") as f:
        f.write("\n".join(diff))
    return {"status": "OK", "diff_lines": len(diff), "added": added, "removed": removed,
            "function_changed": func_changed, "admission_keywords": kw,
            "patch": os.path.relpath(outpath, os.path.dirname(OUT)),
            "sha256_09": sha256_file(BASE09), "sha256_07": sha256_file(BASE07)}


def config_check() -> dict:
    if not os.path.isfile(CFG09):
        return {"status": "PENDING"}
    a = json.load(open(CFG07, encoding="utf-8"))
    b = json.load(open(CFG09, encoding="utf-8"))
    flat = {}

    def walk(prefix, x, y):
        keys = set(x) | set(y)
        for k in keys:
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(x.get(k), dict) and isinstance(y.get(k), dict):
                walk(p, x[k], y[k])
            elif x.get(k) != y.get(k):
                flat[p] = {"07": x.get(k), "09": y.get(k)}

    walk("", a, b)
    # 只允许 user_data_dir/datadir 差异
    allowed = {"user_data_dir", "datadir"}
    unexpected = {k: v for k, v in flat.items() if k not in allowed}
    return {"status": "OK" if not unexpected else "DIFF", "all_diffs": flat,
            "unexpected_diffs": unexpected,
            "whitelist_09": len(b.get("exchange", {}).get("pair_whitelist", []))}


def completeness_check() -> dict:
    rows = []
    for g, s0, s1, s2, _vs, _z in GRIDS:
        for mode, strat in (("C", s0), ("C1", s1), ("C2", s2)):
            p = os.path.join(OUT09, f"trades_{strat}.csv")
            z = os.path.join(OUT09, f"backtest-result-{strat}.zip")
            n = 0
            if os.path.isfile(p):
                try:
                    n = len(pd.read_csv(p, encoding="utf-8-sig"))
                except Exception:
                    n = -1
            rows.append({"grid": g, "mode": mode, "strategy": strat,
                         "trades_exists": os.path.isfile(p), "trades": n,
                         "zip_exists": os.path.isfile(z),
                         "trades_sha256": sha256_file(p), "zip_sha256": sha256_file(z)})
    df = pd.DataFrame(rows)
    return {"cells": 27, "trades_present": int(df["trades_exists"].sum()),
            "zip_present": int(df["zip_exists"].sum()),
            "cells_missing_trades": df.loc[~df["trades_exists"], "strategy"].tolist(),
            "cells_missing_zip": df.loc[~df["zip_exists"], "strategy"].tolist(),
            "table": df}


def main() -> None:
    adm_df, adm_sum = admission_check()
    write_csv(adm_df, "02_准入对拍_v1.csv")
    rules = rules_check()
    code = code_check()
    cfg = config_check()
    comp = completeness_check()
    comp_table = comp.pop("table")
    write_csv(comp_table, "02_27组完整性_v1.csv")

    info = {"admission": adm_sum, "rules": rules, "code": code, "config": cfg,
            "completeness": comp,
            "status": ("READY" if comp["trades_present"] == 27 and comp["zip_present"] == 27
                       and code.get("status") == "OK" else "PENDING")}
    jdump(os.path.join(OUT, "02_预检_v1.json"), info)

    print("== 准入对拍 ==")
    print(json.dumps(adm_sum, ensure_ascii=False, indent=1))
    print("== rules ==", json.dumps(rules, ensure_ascii=False))
    print("== code ==", json.dumps({k: v for k, v in code.items() if k != "function_changed"},
                                   ensure_ascii=False))
    print("function_changed =", code.get("function_changed"))
    print("== config ==", json.dumps(cfg, ensure_ascii=False))
    print("== completeness ==", json.dumps(comp, ensure_ascii=False))
    print("STATUS =", info["status"])


if __name__ == "__main__":
    main()

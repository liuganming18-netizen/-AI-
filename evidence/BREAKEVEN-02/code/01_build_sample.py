# -*- coding: utf-8 -*-
"""BREAKEVEN-02 · 步骤一：从既有全扫描记录里挑出「覆盖全部唯一交易」的最小运行集。

输出：out/sample_plan.json
"""
from __future__ import annotations

import json
import os

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
SRC = os.path.join(ROOT, "evidence/FULL_SWEEP_200x100/runs.jsonl")
OUT = os.path.join(ROOT, "evidence/BREAKEVEN-02/out/sample_plan.json")


def trade_key(t: dict) -> tuple:
    return (t["symbol"], int(t["t_in"]), int(t["t_out"]),
            round(float(t["p_in"]), 10), round(float(t["p_out"]), 10))


def main() -> int:
    recs = [json.loads(line) for line in open(SRC, encoding="utf-8")]
    universe = set()
    for r in recs:
        for t in r.get("trade_list") or []:
            universe.add(trade_key(t))
    cand = []
    for r in recs:
        ks = {trade_key(t) for t in (r.get("trade_list") or [])}
        if ks:
            cand.append((ks, r))
    uncovered = set(universe)
    chosen = []
    while uncovered:
        best = None
        for ks, r in cand:
            new = len(ks & uncovered)
            if new == 0:
                continue
            score = (new, -int(r.get("bars15") or 0))
            if best is None or score > best[0]:
                best = (score, ks, r)
        if best is None:
            break
        _, ks, r = best
        chosen.append(r)
        uncovered -= ks

    plan = []
    for r in chosen:
        plan.append({
            "group": r["group"], "symbol": r["symbol"],
            "start": r["start"], "end": r["end"],
            "orig_bars15": r.get("bars15"), "orig_trades": len(r.get("trade_list") or []),
            "orig_trade_list": r.get("trade_list") or [],
        })
    doc = {
        "source": "evidence/FULL_SWEEP_200x100/runs.jsonl",
        "source_initial_equity": 2000.0,
        "runs_total": len(recs),
        "unique_trades": len(universe),
        "runs_with_trades": len(cand),
        "runs_selected": len(plan),
        "uncovered": len(uncovered),
        "plan": plan,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: doc[k] for k in
                      ("runs_total", "unique_trades", "runs_with_trades",
                       "runs_selected", "uncovered")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

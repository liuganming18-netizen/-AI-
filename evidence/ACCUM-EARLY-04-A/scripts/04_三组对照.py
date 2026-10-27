# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-A｜04 三组对照（共同观察范围 / 新增机会 / 新增失败）。

读 01 的扫描产物，回答四个问题：
  1. 四条规则在**同一批箱体候选**上各自抓到多少机会？
  2. 三组之间有多少是「同一件事」（配对），多少是各自独有？
  3. 相对原策略，三组**新增**了多少机会，其中多少是亏的（新增失败）？
  4. 原策略抓到而三组没抓到的，有多少？

纪律
----
· 「重叠」判定用**同一标的 + 时间窗相交**，不用「看起来像」；
· 新增机会与新增失败必须同时报，只报一边就是挑好看的；
· 结果一律用 **R 倍数**（净收益率 / 5%），绕开本金与仓位口径冲突；
· 不含资金费（已在报告里单列）。
"""
from __future__ import annotations

import calendar
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
OUT = HERE.parents[1] / "out"
MS_H = 3_600_000
MS_D = 86_400_000


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def load(tag):
    fp = OUT / f"01_共同观察范围_{tag}.json"
    if not fp.exists():
        return None
    return json.loads(fp.read_text(encoding="utf-8"))


def trades_of(rep, scheme):
    rows = []
    for r in rep["per_symbol"]:
        sc = (r.get("schemes") or {}).get(scheme) or {}
        for t in (sc.get("trades") or []):
            rows.append(t)
    return rows


def cands_of(rep):
    rows = []
    for r in rep["per_symbol"]:
        sc = (r.get("schemes") or {}).get("baseline") or {}
        for c in (sc.get("candidates") or []):
            rows.append(c)
    return rows


def stats(rs):
    rs = [x for x in rs if x is not None]
    if not rs:
        return {"n": 0}
    win = [x for x in rs if x > 0]
    lose = [x for x in rs if x <= 0]
    return {
        "n": len(rs),
        "R合计": round(sum(rs), 4),
        "R均值": round(sum(rs) / len(rs), 4),
        "R中位": round(statistics.median(rs), 4),
        "胜率": round(len(win) / len(rs), 4),
        "盈利笔均R": round(sum(win) / len(win), 4) if win else None,
        "亏损笔均R": round(sum(lose) / len(lose), 4) if lose else None,
        "盈亏比": (round((sum(win) / len(win)) / abs(sum(lose) / len(lose)), 3)
                if win and lose else None),
        "最好R": round(max(rs), 4),
        "最差R": round(min(rs), 4),
        "R≥2的笔数": sum(1 for x in rs if x >= 2),
    }


def overlap(trade, cands_by_sym):
    """成交事件是否与某个原策略候选重叠：同标的 + 时间窗相交。"""
    lo = trade["box_created_ms"]
    hi = trade.get("exit_ms") or (trade.get("confirm_ms") or lo) + 30 * MS_D
    for c in cands_by_sym.get(trade["symbol"], []):
        if lo <= c["decided_ms"] <= hi + 7 * MS_D:
            return c
    return None


def main() -> int:
    tags = sys.argv[1:] or ["research"]
    rep_out = {"probe": "04_三组对照", "generated_at_utc": iso(time.time() * 1000),
               "windows": {}}
    for tag in tags:
        rep = load(tag)
        if rep is None:
            rep_out["windows"][tag] = {"skip": "扫描产物不存在"}
            continue
        cands = cands_of(rep)
        by_sym = {}
        for c in cands:
            by_sym.setdefault(c["symbol"], []).append(c)
        w = {"window": rep["window"], "pool_n": rep["pool"]["n"],
             "box_candidates": rep["aggregate"]["box_candidates_total"],
             "baseline": {"candidates": len(cands),
                          "stats": stats([t["r_multiple"] for t in
                                          trades_of(rep, "baseline")
                                          if t.get("status") == "filled"])},
             "schemes": {}}
        base_trades = trades_of(rep, "baseline")
        for sch in ("4a", "4b", "4c"):
            tr = trades_of(rep, sch)
            filled = [t for t in tr if t.get("status") == "filled"]
            new, same, new_loss = [], [], []
            for t in filled:
                o = overlap(t, by_sym)
                if o is None:
                    new.append(t)
                    if t["r_multiple"] <= 0:
                        new_loss.append(t)
                else:
                    same.append(t)
            w["schemes"][sch] = {
                "确认": len(tr), "成交": len(filled),
                "结果": stats([t["r_multiple"] for t in filled]),
                "与原策略候选重叠": len(same),
                "新增机会": len(new),
                "新增机会_结果": stats([t["r_multiple"] for t in new]),
                "新增失败": len(new_loss),
                "新增失败_R合计": round(sum(t["r_multiple"] for t in new_loss), 4),
                "重叠笔_结果": stats([t["r_multiple"] for t in same]),
                "平均持仓小时": (round(statistics.mean(
                    [t["held_hours"] for t in filled if t.get("held_hours")]), 1)
                    if any(t.get("held_hours") for t in filled) else None),
            }
        # 原策略抓到、三组都没抓到的
        covered = set()
        for sch in ("4a", "4b", "4c"):
            for t in trades_of(rep, sch):
                if t.get("status") != "filled":
                    continue
                o = overlap(t, by_sym)
                if o is not None:
                    covered.add((o["symbol"], o["decided_ms"]))
        missed = [c for c in cands if (c["symbol"], c["decided_ms"]) not in covered]
        w["baseline"]["三组都没覆盖的候选"] = len(missed)
        w["baseline"]["没覆盖的候选明细"] = [
            {"symbol": c["symbol"], "candidate_utc": iso(c["decided_ms"])}
            for c in sorted(missed, key=lambda x: x["decided_ms"])[:20]]
        rep_out["windows"][tag] = w
    (OUT / "04_三组对照.json").write_text(
        json.dumps(rep_out, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(json.dumps(rep_out, ensure_ascii=False, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

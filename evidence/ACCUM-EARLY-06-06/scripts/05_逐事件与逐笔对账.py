#!/usr/bin/env python3
"""05 · 逐事件 / 逐笔对账（验收条件「补齐变化可逐事件对账」的直接证据）

两层对账，缺一层都不算对完
--------------------------
L1 事件层：把适配器与「只读热库」逐键比对
   · 只增不减：热库已有的键，适配器必须全部保留（不允许补齐过程中丢事件）
   · 新增的每一条都要能指名道姓：哪个标的、哪个结算时刻、rate/mark 来自冷档哪一行
   · 新增的每一条 rate 与 mark 都必须非空（不许出现「补了半条」）
   · 新增总数必须等于 02 独立算出的 hot_missing（跨脚本对账）

L2 交易层：拿 05-C 的 51 笔真实候选，用两套事件集各算一次资金费
   · 只读热库臂 应复现 05-C 的 sum_funding_cost
   · 适配器臂 应复现 06-05 报的 −5.895109066 / Δ +0.659199640
   · 逐笔给出差额，并数出「哪几笔被补到」

产出
  out/05_逐事件补齐明细.csv     新增的每一条事件（一行一条）
  out/05_逐笔资金费对账_51笔.csv 51 笔的两臂资金费与差额
  out/05_逐事件与逐笔对账.json   自检与汇总
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evidence" / "ACCUM-EARLY-06-06" / "adapter"))
sys.path.insert(0, str(ROOT / "框架" / "backend"))

import funding_events_adapter as A  # noqa: E402
from replay_calc import settle_funding_events  # noqa: E402

HOUR = 3600000
EPS = 1e-6


def utc(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    t0 = time.time()
    anchor = json.loads((OUT / "00_范围与身份锚定.json").read_text(encoding="utf-8"))
    p02 = json.loads((OUT / "02_缺口段与分区行为.json").read_text(encoding="utf-8"))
    symbols = anchor["A_窗口与母集"]["symbols"]
    start_ms = anchor["A_窗口与母集"]["start_ms"]
    end_ms = anchor["A_窗口与母集"]["end_ms"]
    GAP_LO = p02["缺口段_重算"]["gap_lo_ms"]
    GAP_HI = p02["缺口段_重算"]["gap_hi_ms"]

    repo = A._get_repo(None, None)
    full = {s: A.load_funding_events("binance", "um", s, start_ms, end_ms, repo=repo)
            for s in symbols}
    hotonly = {s: A.load_funding_events("binance", "um", s, start_ms, end_ms, repo=repo,
                                        include_cold=False) for s in symbols}

    # ---------- L1 事件层 ----------
    added_rows = []
    n_lost = 0
    lost_samples = []
    n_partial = 0
    partial_samples = []
    for s in symbols:
        hk = {e["funding_time"]: e for e in hotonly[s]["events"]}
        fk = {e["funding_time"]: e for e in full[s]["events"]}
        lost = set(hk) - set(fk)
        if lost:
            n_lost += len(lost)
            lost_samples.extend([{"symbol": s, "t": t} for t in sorted(lost)[:3]])
        for t in sorted(set(fk) - set(hk)):
            e = fk[t]
            if e["rate"] is None or e["mark_price"] is None:
                n_partial += 1
                partial_samples.append({"symbol": s, "t": t,
                                        "rate": e["rate"], "mark": e["mark_price"]})
            if GAP_LO <= t <= GAP_HI:
                region = "gap"
            elif t < GAP_LO:
                region = "pre"
            else:
                region = "post"
            added_rows.append({
                "symbol": s,
                "funding_time_ms": t,
                "funding_time_utc": utc(t),
                "region": region,
                "rate": e["rate"],
                "mark_price": e["mark_price"],
                "source_cold": e["source_cold"],
                "rate_type_cold": e["rate_type_cold"],
                "observed_in": e["observed_in"],
                "status": e["status"],
                "availability": e["availability"],
                "cold_hi_utc": utc(full[s]["coverage_observed"]["cold_hi"]),
            })

    with open(OUT / "05_逐事件补齐明细.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(added_rows[0].keys()))
        w.writeheader()
        w.writerows(added_rows)

    by_month, by_region, by_source = {}, {}, {}
    for r in added_rows:
        m = r["funding_time_utc"][:7]
        by_month[m] = by_month.get(m, 0) + 1
        by_region[r["region"]] = by_region.get(r["region"], 0) + 1
        sc = r["source_cold"] or "(空)"
        by_source[sc] = by_source.get(sc, 0) + 1

    # ---------- L2 交易层 ----------
    src = json.loads((ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out"
                      / "02_逐事件基线.json").read_text(encoding="utf-8"))
    pe = src["calibers"]["生效视图_5bps"]["per_event"]

    trade_rows = []
    dir_bad = []
    sum_hot = sum_full = sum_base = 0.0
    n_changed = 0
    max_abs_delta = 0.0
    for r in pe:
        entry_ms, exit_ms = int(r["entry_ms"]), int(r["exit_ms"])
        qty = float(r["qty"])
        # 05-C 的 per_event 没有 direction 字段 ⇒ 用 gross 反解，并断言一致（不假设）
        denom = (float(r["exit_price"]) - float(r["entry_price"])) * qty
        d = 1 if (float(r["gross"]) >= 0) == (denom >= 0) else -1
        if denom == 0:
            dir_bad.append({"candidate_id": r["candidate_id"], "why": "denom=0"})
        s = r["symbol"]
        a = settle_funding_events(symbol=s, qty=qty, direction=d, entry_ms=entry_ms,
                                 exit_ms=exit_ms,
                                 funding_events=A.to_replay_events(hotonly[s]))
        b = settle_funding_events(symbol=s, qty=qty, direction=d, entry_ms=entry_ms,
                                 exit_ms=exit_ms,
                                 funding_events=A.to_replay_events(full[s]))
        delta = b["成本合计"] - a["成本合计"]
        # 把差额逐条归因：新增事件里落在 (entry, exit] 的，逐条单独结算
        hot_keys = {e["funding_time"] for e in hotonly[s]["events"]}
        added_here = [e for e in full[s]["events"]
                      if e["funding_time"] not in hot_keys
                      and entry_ms < e["funding_time"] <= exit_ms]
        per_add_cost = []
        for e in added_here:
            one = settle_funding_events(symbol=s, qty=qty, direction=d, entry_ms=entry_ms,
                                        exit_ms=exit_ms,
                                        funding_events=A.to_replay_events(
                                            {"events": [e]}))
            per_add_cost.append(one["成本合计"])
        attributed = sum(per_add_cost)
        sum_hot += a["成本合计"]
        sum_full += b["成本合计"]
        sum_base += float(r["funding_cost"])
        if abs(delta) > EPS:
            n_changed += 1
        max_abs_delta = max(max_abs_delta, abs(delta))
        trade_rows.append({
            "candidate_id": r["candidate_id"], "symbol": s, "direction": d,
            "entry_utc": r["entry_utc"], "exit_utc": r["exit_utc"],
            "hold_hours": r["hold_hours"],
            "settled_hot": a["已结算"], "gap_hot": a["缺口数"],
            "funding_hot": round(a["成本合计"], 9),
            "settled_adapter": b["已结算"], "gap_adapter": b["缺口数"],
            "funding_adapter": round(b["成本合计"], 9),
            "delta": round(delta, 9),
            "added_events_in_hold": len(added_here),
            "delta_attributed": round(attributed, 9),
            "delta_attribution_diff": round(delta - attributed, 12),
            "baseline_05C": round(float(r["funding_cost"]), 9),
            "hot_vs_05C": round(a["成本合计"] - float(r["funding_cost"]), 9),
        })

    with open(OUT / "05_逐笔资金费对账_51笔.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(trade_rows[0].keys()))
        w.writeheader()
        w.writerows(trade_rows)

    # ---------- 对账锚点（06-05 报的值，登记为 claim 而非常量）----------
    C_06_05 = {"基线": -6.554308706, "适配器": -5.895109066, "delta": 0.659199640,
               "热库漏算笔数": 6}

    checks = []

    def add(name, ok, detail):
        checks.append({"检查": name, "通过": bool(ok), "证据": detail})

    add("L1a 只增不减：热库已有的键在适配器结果里一条不少",
        n_lost == 0, {"丢失": n_lost, "样例": lost_samples[:5]})
    add("L1b 新增事件全部 rate 与 mark 非空（没有「补了半条」）",
        n_partial == 0, {"半条": n_partial, "样例": partial_samples[:5]})
    add("L1c 新增总数 == 02 独立算出的 hot_missing（跨脚本对账）",
        len(added_rows) == p02["汇总"]["hot_missing"],
        {"05": len(added_rows), "02": p02["汇总"]["hot_missing"]})
    add("L1d 新增事件的 observed_in 必须全部是 cold（来源可指名）",
        all(r["observed_in"] == "cold" for r in added_rows),
        {"分布": {k: sum(1 for r in added_rows if r["observed_in"] == k)
                  for k in {r["observed_in"] for r in added_rows}}})
    add("L1e 新增事件的 status 必须全部是 resolved（不是 pending）",
        all(r["status"] == "resolved" for r in added_rows),
        {"分布": {k: sum(1 for r in added_rows if r["status"] == k)
                  for k in {r["status"] for r in added_rows}}})
    add("L1f 新增事件全部带冷档来源串（不是空来源）",
        all(r["source_cold"] for r in added_rows), {"来源分布": by_source})

    add("L2a 方向反解自洽（51 笔全部可解，无 denom=0）",
        not dir_bad, {"异常": dir_bad[:5], "方向分布": {k: sum(1 for r in trade_rows
                                                        if r["direction"] == k)
                                                  for k in {r["direction"] for r in trade_rows}}})
    add("L2b 只读热库臂复现 05-C 基线（逐笔合计差 ≤1e-6）",
        abs(sum_hot - sum_base) <= EPS * max(1, abs(sum_base)),
        {"05-C 基线": round(sum_base, 9), "本单热库臂": round(sum_hot, 9),
         "差": round(sum_hot - sum_base, 12),
         "逐笔最大差": max(abs(r["hot_vs_05C"]) for r in trade_rows)})
    add("L2c 适配器臂复现 06-05 报的合计与差额（≤1e-6）",
        abs(sum_full - C_06_05["适配器"]) <= EPS
        and abs((sum_full - sum_hot) - C_06_05["delta"]) <= EPS,
        {"06-05 适配器": C_06_05["适配器"], "本单适配器": round(sum_full, 9),
         "差": round(sum_full - C_06_05["适配器"], 12),
         "06-05 Δ": C_06_05["delta"], "本单 Δ": round(sum_full - sum_hot, 9)})
    n_zero = sum(1 for r in trade_rows
                 if int(r["settled_hot"]) == 0 and int(r["settled_adapter"]) > 0)
    n_short = sum(1 for r in trade_rows
                  if int(r["settled_hot"]) > 0 and abs(float(r["delta"])) > EPS)
    add("L2d 「热库完全没算」的笔数复现 06-05 的 6 笔",
        n_zero == C_06_05["热库漏算笔数"],
        {"完全没算(settled_hot=0)": n_zero, "06-05 自报": C_06_05["热库漏算笔数"],
         "另有「算少了」的笔数": n_short,
         "说明": "06-05 的 6 笔口径 = 热库完全没结算的笔；本单另有 1 笔是「算少了」"
                 "（部分结算落在缺口段内），所以「资金费发生变化的笔数」是 "
                 f"{n_zero + n_short} 而不是 6。两个数都对，但含义不同，不能混用。"})
    add("L2e 每笔差额都能逐条归因到新增事件（Δ == Σ 新增事件逐条成本，误差 ≤1e-9）",
        all(abs(float(r["delta_attribution_diff"])) <= 1e-9 for r in trade_rows),
        {"最大归因残差": max(abs(float(r["delta_attribution_diff"])) for r in trade_rows),
         "说明": "不能只报一个总额差额；必须能拆成「哪几条事件、各多少钱」"})
    add("L2f 新增事件全部落在持仓区间 (entry, exit] 内（没有凭空加到区间外）",
        all(int(r["added_events_in_hold"]) == int(r["settled_adapter"]) - int(r["settled_hot"])
            for r in trade_rows),
        {"不成立的笔数": sum(1 for r in trade_rows
                        if int(r["added_events_in_hold"])
                        != int(r["settled_adapter"]) - int(r["settled_hot"]))})
    add("L2g 差额符号不作约束（资金费率可正可负，补齐可能让净支付上升也可能下降）",
        True,
        {"delta>0 笔数": sum(1 for r in trade_rows if float(r["delta"]) > EPS),
         "delta<0 笔数": sum(1 for r in trade_rows if float(r["delta"]) < -EPS),
         "负差额样例": [{"symbol": r["symbol"], "delta": r["delta"],
                        "解释": "该笔在缺口段内被补入的结算为「收取」（负费率），"
                                "所以补齐后净支付下降 —— 这是经济上正确的结果，不是错误方向"}
                       for r in trade_rows if float(r["delta"]) < -EPS][:3]})

    n_pass = sum(1 for c in checks if c["通过"])
    out = {
        "task": "ACCUM-EARLY-06-06",
        "probe": "05_逐事件与逐笔对账",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "L1_事件层": {
            "新增事件": len(added_rows),
            "按区": by_region,
            "按来源": by_source,
            "按月份": dict(sorted(by_month.items())),
            "丢失": n_lost,
            "半条": n_partial,
            "半条_定义": "新增事件里 rate 或 mark_price 为 None 的条数；必须为 0",
            "CSV": "05_逐事件补齐明细.csv",
        },
        "L2_交易层": {
            "笔数": len(trade_rows),
            "只读热库臂合计": round(sum_hot, 9),
            "适配器臂合计": round(sum_full, 9),
            "差额": round(sum_full - sum_hot, 9),
            "05-C 基线合计": round(sum_base, 9),
            "资金费变化笔数": n_changed,
            "完全没算笔数": n_zero,
            "算少了笔数": n_short,
            "最大单笔绝对差": round(max_abs_delta, 9),
            "CSV": "05_逐笔资金费对账_51笔.csv",
            "06-05 自报值": C_06_05,
        },
        "checks": checks,
        "summary": {"total": len(checks), "pass": n_pass,
                    "verdict": "通过" if n_pass == len(checks) else "不通过"},
        "elapsed_sec": round(time.time() - t0, 1),
    }
    (OUT / "05_逐事件与逐笔对账.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    for c in checks:
        print(("PASS " if c["通过"] else "FAIL ") + c["检查"])
    print("\nL1 新增", len(added_rows), "按区", by_region, "按来源", by_source)
    print("L2 热库臂", round(sum_hot, 9), "适配器臂", round(sum_full, 9),
          "Δ", round(sum_full - sum_hot, 9), "被补", n_changed, "笔")
    print(out["summary"], "elapsed", out["elapsed_sec"], "s")
    return 0 if n_pass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

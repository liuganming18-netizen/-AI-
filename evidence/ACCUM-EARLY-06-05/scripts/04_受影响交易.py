# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-05｜04 受影响交易：热库漏了几次结算、冷档补回几次、还剩几次没证据。

对象 = 05-C 的 51 条原策略候选（`生效视图_5bps` 逐笔），它们的 `funding_cost` 是用
**只读热库**的 `load_funding` 算出来的（`05-C/scripts/02` 第 71 行）。本脚本做三件事：

1. **逐笔重算**：用热库事件、冷档事件、两源并集分别算一次
   `cost = Σ 结算标记价 × 数量 × 费率 × 方向`（多头 direction=+1，与 `settle_funding` 同式）；
2. **判「该结算却没记录」**：按该标的**实测周期** step 给期望次数上下界
   `floor(L/step)` ~ `ceil(L/step)`，不用「持仓 8 小时」这种固定门槛；
3. **指出能补 / 不能补**：`热库漏的` − `并集漏的` = 冷档补回；并集仍缺的 = **仍无证据**。

纪律：不补零（缺 mark/rate 记 gap，不计成本）、不跨交易所、不改 05-C 任何产物。
05-F 的 96 笔「真缺口」在这里**重算一遍**，看它的 8 小时门槛差在哪。
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD_DIR = ROOT / "数据" / "市场数据" / "parquet" / "funding" / "binance" / "um"
E05C = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out"

HOUR = 3_600_000


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_trades() -> tuple[list, dict]:
    d = json.loads((E05C / "02_逐事件基线.json").read_text(encoding="utf-8"))
    out, meta = [], {}
    for cal in ("生效视图_5bps", "策略包_3bps"):
        blk = d["calibers"].get(cal) or {}
        rows = blk.get("per_event") or []
        meta[cal] = {"n": len(rows),
                     "caliber_params": blk.get("caliber_params"),
                     "sum_funding_cost": blk.get("sum_funding_cost"),
                     "source": "05-C/out/02_逐事件基线.json"}
        if cal == "生效视图_5bps":
            out = rows
    return out, meta


def main():
    import duckdb
    t0 = time.time()
    anchor = json.loads((OUT / "00_范围锚定.json").read_text(encoding="utf-8"))
    p2 = json.loads((OUT / "02_原生结算周期与缺口段.json").read_text(encoding="utf-8"))
    step = {s: r["step_h"] for s, r in p2["per_symbol_all"].items()}
    reliable = {s: r["grid_reliable"] for s, r in p2["per_symbol_all"].items()}

    trades, meta = load_trades()
    syms = sorted({t["symbol"] for t in trades})
    lit_syms = "[" + ",".join("'" + s + "'" for s in syms) + "]"
    files = sorted(str(p) for p in COLD_DIR.glob("*.parquet"))
    lit = "[" + ",".join("'" + f.replace("'", "''") + "'" for f in files) + "]"

    d = duckdb.connect()
    d.execute(f"ATTACH '{HOT}' AS hot (READ_ONLY)")
    d.execute(f"""
        CREATE TABLE ce AS
        SELECT regexp_extract(filename,'um/([^/]+)\\.parquet',1) sym, funding_time,
               funding_rate AS rate, mark_price AS mark, 'cold' AS layer
        FROM read_parquet({lit}, filename=true)""")
    d.execute(f"""
        CREATE TABLE he AS
        SELECT symbol AS sym, funding_time, CAST(funding_rate AS DOUBLE) rate,
               CAST(mark_price AS DOUBLE) mark, 'hot' AS layer
        FROM hot.funding_history
        WHERE exchange='binance' AND market='um' AND rate_type='settled'""")
    cold_ev = defaultdict(list)
    hot_ev = defaultdict(list)
    for sym, t, r, m in d.execute(
            "SELECT sym, funding_time, rate, mark FROM ce ORDER BY sym, funding_time").fetchall():
        cold_ev[sym].append((int(t), r, m))
    for sym, t, r, m in d.execute(
            "SELECT sym, funding_time, rate, mark FROM he ORDER BY sym, funding_time").fetchall():
        hot_ev[sym].append((int(t), r, m))

    def cost(events: list, qty: float, entry_ms: int, exit_ms: int, direction: int = 1) -> dict:
        """与 trading_core.funding.settle_funding 同式：mark × qty × rate × dir。"""
        n, tot, gaps, uncertain = 0, 0.0, 0, 0
        for t, r, m in events:
            if not (entry_ms < t <= exit_ms):
                continue
            n += 1
            if r is None or m is None or float(m) <= 0:
                gaps += 1
                continue
            tot += float(m) * float(qty) * float(r) * direction
            if abs(t - exit_ms) <= 15 * 60 * 1000:
                uncertain += 1
        return {"n": n, "cost": tot, "gaps": gaps, "uncertain": uncertain}

    rows = []
    for t in trades:
        sym = t["symbol"]
        entry_ms, exit_ms = int(t["entry_ms"]), int(t["exit_ms"])
        qty = float(t["qty"] or 0.0)
        st = float(step.get(sym, 8.0))
        rel = bool(reliable.get(sym, False))
        L = exit_ms - entry_ms
        exp_min = int(L // int(st * HOUR))
        exp_max = int(math.ceil(L / (st * HOUR)))
        h = cost(hot_ev.get(sym, []), qty, entry_ms, exit_ms)
        c = cost(cold_ev.get(sym, []), qty, entry_ms, exit_ms)
        # 并集：同刻以热库为准（与 MarketDataRepository._merge_dedup 的优先级一致）
        uni = {}
        for tt, r, m in cold_ev.get(sym, []):
            uni[tt] = (r, m)
        for tt, r, m in hot_ev.get(sym, []):
            uni[tt] = (r, m)
        u = cost(sorted((tt, r, m) for tt, (r, m) in uni.items()), qty, entry_ms, exit_ms)
        base = t.get("funding_cost")
        rows.append({
            "symbol": sym, "entry_utc": t["entry_utc"], "exit_utc": t["exit_utc"],
            "hold_hours": t.get("hold_hours"), "qty": qty,
            "step_h": st, "grid_reliable": rel,
            "expected_settlements_min": exp_min, "expected_settlements_max": exp_max,
            "n_hot": h["n"], "n_cold": c["n"], "n_union": u["n"],
            "cost_hot": round(h["cost"], 9), "cost_cold": round(c["cost"], 9),
            "cost_union": round(u["cost"], 9),
            "baseline_funding_cost_05C": base,
            "delta_vs_baseline": (round(u["cost"] - float(base), 9)
                                  if base is not None else None),
            "hot_gap_events": h["gaps"], "union_gap_events": u["gaps"],
            "filled_by_cold_events": u["n"] - h["n"],
            "residual_missing_min": max(0, exp_min - u["n"]),
            "residual_missing_max": max(0, exp_max - u["n"]),
            "hot_zero_but_expected": (h["n"] == 0 and exp_min >= 1),
            "union_zero_but_expected": (u["n"] == 0 and exp_min >= 1),
            "funding_order_uncertain": u["uncertain"],
            "notional": t.get("notional"),
            "realized_net": t.get("realized_net"),
        })

    def s(key):
        return round(sum(float(r[key] or 0) for r in rows), 9)

    n = len(rows)
    hot_zero = [r for r in rows if r["hot_zero_but_expected"]]
    union_zero = [r for r in rows if r["union_zero_but_expected"]]
    filled = [r for r in rows if r["filled_by_cold_events"] > 0]
    residual = [r for r in rows if r["residual_missing_min"] > 0]

    # 05-F 的 C3 口径复算：0 次结算 + 持仓 ≥8h 视为「真缺口」；再换成实测周期看差多少
    old_def = [r for r in rows if r["n_hot"] == 0 and (r["hold_hours"] or 0) >= 8.0]
    new_def = [r for r in rows if r["n_hot"] == 0 and r["expected_settlements_min"] >= 1]
    both = [r for r in old_def if r in new_def]
    only_new = [r for r in new_def if r not in old_def]

    # ── 04-A 两段区间的成交：复核 05-F 的「96 笔真缺口」 ──────────────────
    # 05-F 的 96 笔来自 04-A 验证区间（1523 笔），**不是**上面 51 笔候选。
    # 两个种群不能混算，这里分开。
    def review_04a() -> dict:
        res = {}
        for tag, fn in (("research_final", "01_共同观察范围_research_final.json"),
                        ("holdout_final", "01_共同观察范围_holdout_final.json")):
            p = ROOT / "evidence" / "ACCUM-EARLY-04-A" / "out" / fn
            if not p.exists():
                res[tag] = {"applicable": False, "why": "04-A 产物缺失"}
                continue
            d0 = json.loads(p.read_text(encoding="utf-8"))
            fills = []
            for s in d0["per_symbol"]:
                for sch, v in (s.get("schemes") or {}).items():
                    for tr in (v.get("trades") or []):
                        if tr.get("status") != "filled":
                            continue
                        if tr.get("entry_bar_open_ms") is None or tr.get("exit_ms") is None:
                            continue
                        fills.append((s["symbol"], int(tr["entry_bar_open_ms"]),
                                      int(tr["exit_ms"]), tr.get("held_hours")))
            n_hot0_8h = n_hot0_native = n_cold_fill = n_residual = 0
            dsum_hot = dsum_uni = 0.0
            examples = []
            for sym, e, x, held in fills:
                st = float(step.get(sym, 8.0))
                exp_min = int((x - e) // int(st * HOUR))
                h = cost(hot_ev.get(sym, []), 1.0, e, x)      # qty=1 ⇒ 只取 Σ(mark×rate)
                uni = {}
                for tt, r, m in cold_ev.get(sym, []):
                    uni[tt] = (r, m)
                for tt, r, m in hot_ev.get(sym, []):
                    uni[tt] = (r, m)
                u = cost(sorted((tt, r, m) for tt, (r, m) in uni.items()), 1.0, e, x)
                dsum_hot += h["cost"]; dsum_uni += u["cost"]
                if h["n"] == 0 and (held or 0) >= 8.0:
                    n_hot0_8h += 1
                if h["n"] == 0 and exp_min >= 1:
                    n_hot0_native += 1
                    if u["n"] > 0:
                        n_cold_fill += 1
                    else:
                        n_residual += 1
                        if len(examples) < 8:
                            examples.append({"symbol": sym, "entry_utc": iso(e),
                                             "exit_utc": iso(x), "step_h": st,
                                             "expected_min": exp_min, "held_hours": held})
            # ΔR = −ΔΣ(mark×rate)/0.05 只是**量级对照**：04-A 用 R 绕开名义额
            res[tag] = {
                "fills": len(fills),
                "口径A_热库0结算且持仓≥8h": n_hot0_8h,
                "口径B_热库0结算且应有≥1次(实测周期)": n_hot0_native,
                "口径B_中冷档能补回": n_cold_fill,
                "口径B_中并集仍无记录": n_residual,
                "样例_并集仍无记录": examples,
                "Σmark×rate_热库": round(dsum_hot, 8),
                "Σmark×rate_并集": round(dsum_uni, 8),
                "差额_并集减热库": round(dsum_uni - dsum_hot, 8),
                "说明": ("qty 取 1 ⇒ 这里比的是 Σ(mark×rate)，与 05-F 的 Σrate 不同口径："
                       "它只加 rate、不看 mark。差额符号说明并集让成本往哪边动"),
            }
        return res

    review = review_04a()

    out = {
        "task": "ACCUM-EARLY-06-05",
        "probe": "04_受影响交易",
        "generated_at_utc": iso(int(time.time() * 1000)),
        "对象": {
            "来源": "05-C/out/02_逐事件基线.json#calibers.生效视图_5bps.per_event",
            "笔数": n, "标的数": len(syms),
            "口径参数": meta.get("生效视图_5bps", {}).get("caliber_params"),
            "为什么用这一套": ("51 条候选的 funding_cost 就是用只读热库的 load_funding 算的；"
                        "另一套 策略包_3bps 只有 50 笔成交 + 1 拒单，不重复算"),
            "策略包_3bps": meta.get("策略包_3bps"),
        },
        "汇总": {
            "热库结算事件合计": sum(r["n_hot"] for r in rows),
            "冷档结算事件合计": sum(r["n_cold"] for r in rows),
            "并集结算事件合计": sum(r["n_union"] for r in rows),
            "冷档补回事件合计": sum(r["filled_by_cold_events"] for r in rows),
            "资金费_热库": s("cost_hot"),
            "资金费_并集": s("cost_union"),
            "资金费差额_并集减热库": round(s("cost_union") - s("cost_hot"), 9),
            "基线合计_05C": s("baseline_funding_cost_05C"),
            "并集与基线最大单笔绝对差": (round(max((abs(float(r["delta_vs_baseline"]))
                                          for r in rows if r["delta_vs_baseline"] is not None),
                                         default=0.0), 9)),
            "热库漏结算的笔数": len(hot_zero),
            "其中冷档补回（并集不再是 0）": len([r for r in hot_zero
                                    if r["n_union"] > 0]),
            "并集仍 0 结算但应有结算": len(union_zero),
            "并集仍有残余缺口（下界>0）的笔数": len(residual),
        },
        "能补与不能补": {
            "能补": [{"symbol": r["symbol"], "entry_utc": r["entry_utc"],
                     "补回次数": r["filled_by_cold_events"],
                     "资金费变化": round(r["cost_union"] - r["cost_hot"], 9)}
                    for r in filled],
            "不能补": [{"symbol": r["symbol"], "entry_utc": r["entry_utc"],
                     "残余缺口_下界": r["residual_missing_min"],
                     "残余缺口_上界": r["residual_missing_max"],
                     "原因": "并集里也没有该次结算 ⇒ 冷档与热库同时无记录，本单不补零、不外推"}
                    for r in residual],
            "判据": ("能补 = 并集比热库多出结算事件；不能补 = 并集仍缺（下界 >0）。"
                   "「不能补」不等于「没有资金费」，只等于「没有证据」"),
        },
        "05F口径复算": {
            "原口径（0 次结算且持仓 ≥8h）笔数": len(old_def),
            "换成实测周期（0 次结算且应有 ≥1 次）笔数": len(new_def),
            "两口径都判为缺口": len(both),
            "只有新口径判出（持仓 <8h 但 ≥ 一个周期）": len(only_new),
            "样例_只有新口径": [{"symbol": r["symbol"], "hold_hours": r["hold_hours"],
                            "step_h": r["step_h"], "expected_min": r["expected_settlements_min"]}
                           for r in only_new[:10]],
            "为什么改": ("持仓 4~8 小时的笔在 4 小时周期下**应该**结算过；用 8 小时门槛会把它们"
                     "整批判成「持仓太短、本该 0 次」⇒ 05-F 的 96 笔是**偏少**的估计"),
            "05F报告值": 96,
        },
        "04A复核": review,
        "逐笔": rows,
        "elapsed_sec": round(time.time() - t0, 2),
    }
    (OUT / "04_受影响交易.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out["汇总"][k] for k in out["汇总"]}, ensure_ascii=False, indent=2))
    print(json.dumps(out["05F口径复算"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

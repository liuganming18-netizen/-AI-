# -*- coding: utf-8 -*-
"""BREAKEVEN-01 · 步骤三：配对、汇总、时序不明清单。

关键口径（本步骤修正了两处容易出错的地方）
────────────────────────────────────────
1. **必须在同一个 run 内配对**：同一候选信号会出现在多个重叠窗口里，
   窗口末尾会把它强制平仓（`force_close_eod`），于是同一个 signal_id 会有
   不同退出结果。若跨 run 混取 A/B/C，就会把「窗口截断差异」误当成「保本差异」。
2. **独立信号只有一个**：按 signal_id 去重，并在多个实例中优先选
   「未被窗口末尾强平」的那一个（其次选持有更久的），作为该信号的规范实例。

产出：
    out/paired_trades.csv        逐笔 A/B/C 配对表
    out/summary.json             汇总指标 + 差异分解
    out/ambiguity.csv            同根先后不明清单
    out/signal_instances.csv     同一信号的多窗口实例（口径披露）
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics as st
from collections import defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/BREAKEVEN-01/out")


def _ts(ms):
    import datetime as dt
    if ms in (None, -1):
        return ""
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _pf(vals):
    g = sum(v for v in vals if v > 0)
    l = -sum(v for v in vals if v < 0)
    return (g / l) if l > 0 else None


def _q(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _counts(it):
    d = {}
    for x in it:
        d[str(x)] = d.get(str(x), 0) + 1
    return d


def block(rows):
    nets = [r["net_pnl"] for r in rows]
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v < 0]
    holds = [r["hold_hours"] for r in rows]
    return {
        "n": len(rows),
        "net_sum": round(sum(nets), 6),
        "net_mean": round(sum(nets) / len(nets), 6) if nets else None,
        "win_rate": round(len(wins) / len(nets), 4) if nets else None,
        "avg_win": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 6) if losses else None,
        "profit_factor": (round(_pf(nets), 4) if _pf(nets) is not None else None),
        "worst": round(min(nets), 6) if nets else None,
        "best": round(max(nets), 6) if nets else None,
        "worst5_sum": round(sum(sorted(nets)[:5]), 6) if nets else None,
        "loss_tail_sum": round(sum(losses), 6) if losses else None,
        "p05": round(_q(nets, 0.05), 6) if nets else None,
        "hold_mean_h": round(sum(holds) / len(holds), 3) if holds else None,
        "hold_median_h": round(st.median(holds), 3) if holds else None,
        "net_R_sum": round(sum(r["net_R"] for r in rows if r["net_R"] is not None), 6),
        "net_R_mean": (round(sum(r["net_R"] for r in rows if r["net_R"] is not None)
                             / max(1, sum(1 for r in rows if r["net_R"] is not None)), 4)),
        "exit_reason_counts": _counts(r["exit_reason"] for r in rows),
    }


def main() -> int:
    recs = [json.loads(l) for l in open(os.path.join(OUT, "raw_runs.jsonl"), encoding="utf-8")]
    ok = [r for r in recs if r["status"] == "ok"]

    # ── ① 同一 run 内配对：instance = (run_idx, signal_id) ──
    inst = []
    for ri, r in enumerate(ok):
        bysig = defaultdict(dict)
        for v in ("A", "B", "C"):
            for t in r["trades"][v]:
                bysig[t["signal_id"]][v] = t
        for sid, d in bysig.items():
            inst.append({"run_idx": ri, "signal_id": sid, "symbol": r["symbol"],
                         "group": r["group"], "window": f"{r['start']}~{r['end']}",
                         "A": d.get("A"), "B": d.get("B"), "C": d.get("C")})

    # ── ② 每个信号选规范实例：优先未被窗口末尾强平，其次持有更久 ──
    bysig = defaultdict(list)
    for x in inst:
        bysig[x["signal_id"]].append(x)

    def score(x):
        a = x["A"]
        if a is None:
            return (-1, -1)
        return (0 if a["exit_reason"] == "force_close_eod" else 1, a["hold_hours"])

    canon = {}
    multi = []
    for sid, lst in bysig.items():
        best = max(lst, key=score)
        canon[sid] = best
        outs = {(a["exit_reason"], round(a["net_pnl"], 6)) for a in
                (x["A"] for x in lst) if a is not None}
        if len(lst) > 1 and len(outs) > 1:
            multi.append({"signal_id": sid, "symbol": best["symbol"],
                          "instances": len(lst),
                          "distinct_A_outcomes": [
                              {"window": x["window"], "exit_reason": x["A"]["exit_reason"],
                               "net": round(x["A"]["net_pnl"], 6)} for x in lst]})

    paired = [x for x in canon.values() if x["A"] and x["B"] and x["C"]]
    missing = [x["signal_id"] for x in canon.values()
               if not (x["A"] and x["B"] and x["C"])]

    # ── ③ 一致性：未触发保本 ⇒ 与 A 逐位一致（同 run 内）──
    # 注意「串行资金耦合」：同一 run 里若前一笔被保本提前平掉，后一笔的权益/仓位会变，
    # 即便它自己没触发保本，净额也会不同。这类必须与「实现不一致」分开统计。
    first_trig = {}
    for ri, r in enumerate(ok):
        trig_entries = [t["entry_time"] for v in ("B", "C") for t in r["trades"][v]
                        if t["be_triggered"]]
        if trig_entries:
            first_trig[ri] = min(trig_entries)

    consist = {"B_checked": 0, "B_bad": [], "C_checked": 0, "C_bad": [],
               "B_cascade": [], "C_cascade": []}
    for x in paired:
        a, b, c = x["A"], x["B"], x["C"]
        for v, t, key in (("B", b, "B"), ("C", c, "C")):
            if t["be_triggered"]:
                continue
            consist[f"{key}_checked"] += 1
            same = (abs(t["net_pnl"] - a["net_pnl"]) <= 1e-9
                    and t["exit_time"] == a["exit_time"])
            if same:
                continue
            ft = first_trig.get(x["run_idx"])
            if ft is not None and a["entry_time"] > ft:
                consist[f"{key}_cascade"].append({
                    "signal_id": x["signal_id"], "symbol": a["symbol"],
                    "entry_time_utc": _ts(a["entry_time"]),
                    "net_A": round(a["net_pnl"], 6), "net_variant": round(t["net_pnl"], 6),
                    "qty_A": a["qty"], "qty_variant": t["qty"],
                    "原因": "同 run 内前一笔被保本提前平仓 ⇒ 本笔权益/仓位改变（串行资金耦合）"})
            else:
                consist[f"{key}_bad"].append(x["signal_id"])

    # ── ④ 逐笔配对表 ──
    rows = []
    for x in paired:
        a, b, c = x["A"], x["B"], x["C"]
        row = {
            "signal_id": x["signal_id"], "symbol": a["symbol"], "group": a["group"],
            "run_idx": x["run_idx"],
            "window": x["window"], "direction": a["direction"],
            "entry_time_utc": _ts(a["entry_time"]), "entry_time_ms": a["entry_time"],
            "entry_price": a["entry_price"], "qty": a["qty"],
            "initial_stop": a["initial_stop"], "R_abs": round(a["R_abs"], 10),
            "R_pct": round(a["R_pct"], 6) if a["R_pct"] is not None else None,
            "R_money": round(a["R_abs"] * a["qty"], 6),
        }
        for v, t in (("A", a), ("B", b), ("C", c)):
            row[f"{v}_exit_time_utc"] = _ts(t["exit_time"])
            row[f"{v}_exit_price"] = t["exit_price"]
            row[f"{v}_net"] = round(t["net_pnl"], 6)
            row[f"{v}_R"] = round(t["net_R"], 4) if t["net_R"] is not None else None
            row[f"{v}_reason"] = t["exit_reason"]
            row[f"{v}_hold_h"] = round(t["hold_hours"], 3)
            row[f"{v}_mfe_R"] = (round(t["mfe"] * t["entry_price"] / t["R_abs"], 4)
                                 if t["R_abs"] > 0 else None)
            row[f"{v}_be_triggered"] = t["be_triggered"]
            if v != "A":
                row[f"{v}_trigger_utc"] = _ts(t["be_trigger_time"])
                row[f"{v}_trigger_px"] = (round(t["be_trigger_px"], 10)
                                          if t["be_trigger_px"] is not None else None)
                row[f"{v}_be_px"] = (round(t["be_price"], 10)
                                     if t["be_price"] is not None else None)
                row[f"{v}_applied_utc"] = _ts(t["be_applied_time"])
                row[f"{v}_update_utc"] = _ts(t["be_update_time"])
                row[f"{v}_net_at_trigger"] = (round(t["be_net_at_trigger"], 6)
                                              if t["be_net_at_trigger"] is not None else None)
                row[f"{v}_same_bar_ambiguous"] = t["same_bar_ambiguous"]
        row["B_delta"] = round(b["net_pnl"] - a["net_pnl"], 6)
        row["C_delta"] = round(c["net_pnl"] - a["net_pnl"], 6)
        row["B_saved_loss"] = round(max(0.0, row["B_delta"]), 6)
        row["B_extra_loss"] = round(max(0.0, -row["B_delta"]), 6)
        row["C_saved_loss"] = round(max(0.0, row["C_delta"]), 6)
        row["C_extra_loss"] = round(max(0.0, -row["C_delta"]), 6)
        row["B_forgone_profit"] = (round(a["net_pnl"] - b["net_pnl"], 6)
                                   if (b["be_triggered"] and a["net_pnl"] > 0
                                       and b["net_pnl"] < a["net_pnl"]) else 0.0)
        row["C_forgone_profit"] = (round(a["net_pnl"] - c["net_pnl"], 6)
                                   if (c["be_triggered"] and a["net_pnl"] > 0
                                       and c["net_pnl"] < a["net_pnl"]) else 0.0)
        rows.append(row)

    rows.sort(key=lambda r: (r["symbol"], r["entry_time_ms"]))
    with open(os.path.join(OUT, "paired_trades.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    with open(os.path.join(OUT, "signal_instances.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["signal_id", "symbol", "instances",
                                          "distinct_A_outcomes"])
        w.writeheader()
        for m in multi:
            w.writerow({**m, "distinct_A_outcomes": json.dumps(
                m["distinct_A_outcomes"], ensure_ascii=False)})

    # ── ⑤ 时序不明清单 ──
    amb = []
    for x in paired:
        for v in ("B", "C"):
            t = x[v]
            if t["same_bar_ambiguous"]:
                amb.append({"signal_id": x["signal_id"], "variant": v, "symbol": t["symbol"],
                            "entry_time_utc": _ts(t["entry_time"]),
                            "net_A": round(x["A"]["net_pnl"], 6),
                            "net_variant": round(t["net_pnl"], 6),
                            "处理": "按原止损退出（不假设阈值先到）"})
    with open(os.path.join(OUT, "ambiguity.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["signal_id", "variant", "symbol",
                                          "entry_time_utc", "net_A", "net_variant", "处理"])
        w.writeheader()
        w.writerows(amb)

    # ── ⑥ 汇总 ──
    A = [x["A"] for x in paired]
    B = [x["B"] for x in paired]
    C = [x["C"] for x in paired]

    def diff_block(V, base):
        d = [v["net_pnl"] - a["net_pnl"] for v, a in zip(V, base)]
        trig = [v for v in V if v["be_triggered"]]
        early = [(v, a) for v, a in zip(V, base)
                 if v["be_triggered"] and v["exit_time"] < a["exit_time"]]
        early_profit = [(v, a) for v, a in early if a["net_pnl"] > 0]
        be_neg = [v for v in V if v["be_triggered"] and v["net_pnl"] < 0]
        # 保本触发但实际退出原因不是止损（跳空/最长持仓/期末）
        non_stop_exit = [v for v in V if v["be_triggered"]
                         and v["exit_reason"] != "intrabar_stop"]
        return {
            "triggered_n": len(trig),
            "triggered_rate": round(len(trig) / len(V), 4) if V else None,
            "triggered_A_profit_n": sum(1 for v, a in zip(V, base)
                                        if v["be_triggered"] and a["net_pnl"] > 0),
            "triggered_A_loss_n": sum(1 for v, a in zip(V, base)
                                      if v["be_triggered"] and a["net_pnl"] <= 0),
            "triggered_delta_mean": (round(sum(v["net_pnl"] - a["net_pnl"]
                                               for v, a in zip(V, base)
                                               if v["be_triggered"]) / len(trig), 6)
                                     if trig else None),
            "delta_sum": round(sum(d), 6),
            "delta_mean": round(sum(d) / len(d), 6) if d else None,
            "saved_loss_sum": round(sum(max(0.0, x) for x in d), 6),
            "extra_loss_sum": round(sum(max(0.0, -x) for x in d), 6),
            "improved_n": sum(1 for x in d if x > 1e-9),
            "worsened_n": sum(1 for x in d if x < -1e-9),
            "unchanged_n": sum(1 for x in d if abs(x) <= 1e-9),
            "early_exit_n": len(early),
            "early_exit_and_A_profit_n": len(early_profit),
            "forgone_profit_sum": round(sum(a["net_pnl"] - v["net_pnl"]
                                            for v, a in early_profit), 6),
            "be_trigger_but_net_negative_n": len(be_neg),
            "be_trigger_but_net_negative_sum": round(sum(v["net_pnl"] for v in be_neg), 6),
            "be_trigger_but_not_stop_exit_n": len(non_stop_exit),
            "hold_mean_h": round(sum(v["hold_hours"] for v in V) / len(V), 3) if V else None,
            "loss_tail_sum": round(sum(v["net_pnl"] for v in V if v["net_pnl"] < 0), 6),
            "worst5_sum": round(sum(sorted(v["net_pnl"] for v in V)[:5]), 6),
            "worst": round(min(v["net_pnl"] for v in V), 6) if V else None,
        }

    summary = {
        "sample": {
            "runs_rerun": len(ok),
            "trade_instances_A": sum(len(r["trades"]["A"]) for r in ok),
            "unique_signals": len(bysig),
            "paired_signals": len(paired),
            "missing_variant_signals": len(missing),
            "signals_with_multiple_window_outcomes": len(multi),
            "note": ("同一候选信号在多个重叠窗口里会得到不同退出（窗口末尾强平）。"
                     "本表按 signal_id 去重，每个信号只保留一个规范实例，"
                     "且 A/B/C 必须取自同一个 run。"),
        },
        "A": block(A), "B": block(B), "C": block(C),
        "B_vs_A": diff_block(B, A),
        "C_vs_A": diff_block(C, A),
        "consistency": {"B_checked": consist["B_checked"], "B_bad_n": len(consist["B_bad"]),
                        "C_checked": consist["C_checked"], "C_bad_n": len(consist["C_bad"]),
                        "B_bad_ids": consist["B_bad"][:10],
                        "C_bad_ids": consist["C_bad"][:10],
                        "B_cascade_n": len(consist["B_cascade"]),
                        "C_cascade_n": len(consist["C_cascade"]),
                        "B_cascade": consist["B_cascade"],
                        "C_cascade": consist["C_cascade"]},
        "ambiguity_n": len(amb),
        "runs": {"total": len(recs), "ok": len(ok),
                 "error": [{"symbol": r["symbol"], "err": r["err"]}
                           for r in recs if r["status"] != "ok"]},
        "repro": {"checked": sum(r["repro"]["checked"] for r in recs),
                  "matched": sum(r["repro"]["matched"] for r in recs),
                  "mismatch_n": sum(len(r["repro"]["mismatch"]) for r in recs),
                  "mismatch_examples": [dict(m, group=r["group"], symbol=r["symbol"])
                                        for r in recs for m in r["repro"]["mismatch"]][:10]},
        "drawdown_note": ("每个 run 是单标的独立 2000U 账户，彼此不共享资金；"
                          "本单不计算、也不声明「账户最大回撤」。"
                          "逐笔净额累加只能作样本内描述，不得当组合净值。"),
    }
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print(json.dumps({k: summary[k] for k in
                      ("sample", "consistency", "repro", "ambiguity_n")},
                     ensure_ascii=False, indent=1))
    for v in ("A", "B", "C"):
        print(v, json.dumps(summary[v], ensure_ascii=False))
    print("B_vs_A", json.dumps(summary["B_vs_A"], ensure_ascii=False))
    print("C_vs_A", json.dumps(summary["C_vs_A"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

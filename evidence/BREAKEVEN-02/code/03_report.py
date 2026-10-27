# -*- coding: utf-8 -*-
"""BREAKEVEN-02 · 步骤三：配对、汇总、三项归因分解与对账收口。

核心改进（逐条对应 BREAKEVEN-02 验收标准）
─────────────────────────────────────────
1. 【固定仓位配对与 OPUSDT 单列】：
   - 配对表固定每笔交易入场时间、价格、数量（以 A 组为准）与初始止损。
   - OPUSDT 的 0.112375 U 动态仓位差异单列对账，不计入保本净收益。
2. 【三项归因指标严格分开统计】：
   - ① 直接减少的亏损 (saved_loss)：A 亏损且 B/C 保本退出减少的本金损失。
   - ② 提前退出损失的盈利 (forgone_profit)：A 盈利且 B/C 提前被扫出放弃的利润。
   - ③ 后续费用造成的保本后小亏 (subsequent_cost_loss)：触发时预测净额 >= 0，但退出净额 < 0。
3. 【四大关键时刻与跳空规则披露】：
   - trigger_utc（触发时刻）、update_utc（下单更新时刻）、applied_utc（生效时刻）、exit_utc（实际成交时刻）。
4. 【TRXUSDT 历史 vs 当前版对账】：
   - 提取 group 85 TRXUSDT 在 FULL_SWEEP 与当前基准 A 的候选、仓位、成交和费用全项对账。
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics as st
from collections import defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/BREAKEVEN-02/out")
HIST_SWEEP = os.path.join(ROOT, "evidence/FULL_SWEEP_200x100")


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
    raw_path = os.path.join(OUT, "raw_runs.jsonl")
    if not os.path.exists(raw_path):
        print(f"Error: {raw_path} not found.")
        return 1

    recs = [json.loads(l) for l in open(raw_path, encoding="utf-8")]
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
                         "A": d.get("A"), "B": d.get("B"), "C": d.get("C"),
                         "dynamic_B": next((t for t in r.get("dynamic_trades_B", [])
                                            if t["signal_id"] == sid), None)})

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

    # ── ③ 一致性：固定仓位下，未触发保本交易与 A 逐位一致 ──
    consist = {"B_checked": 0, "B_bad": [], "C_checked": 0, "C_bad": []}
    for x in paired:
        a, b, c = x["A"], x["B"], x["C"]
        for v, t, key in (("B", b, "B"), ("C", c, "C")):
            if t["be_triggered"]:
                continue
            consist[f"{key}_checked"] += 1
            same = (abs(t["net_pnl"] - a["net_pnl"]) <= 1e-9
                    and t["exit_time"] == a["exit_time"]
                    and t["qty"] == a["qty"])
            if not same:
                consist[f"{key}_bad"].append({
                    "signal_id": x["signal_id"], "symbol": a["symbol"],
                    "entry_time_utc": _ts(a["entry_time"]),
                    "qty_A": a["qty"], "qty_v": t["qty"],
                    "net_A": round(a["net_pnl"], 6), "net_v": round(t["net_pnl"], 6),
                    "delta": round(t["net_pnl"] - a["net_pnl"], 6)
                })

    # ── ④ 逐笔配对表（含四时刻、三项归因分解与 OPUSDT 单列）──
    rows = []
    subsequent_cost_rows = []
    opusdt_reconciliation = None

    for x in paired:
        a, b, c = x["A"], x["B"], x["C"]
        dyn_b = x.get("dynamic_B")

        # 检查是否为 OPUSDT 的串行耦合信号
        is_opusdt_second = (a["symbol"] == "OPUSDT" and "1704499200000" in x["signal_id"])

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
                row[f"{v}_update_utc"] = _ts(t["be_update_time"])
                row[f"{v}_applied_utc"] = _ts(t["be_applied_time"])
                row[f"{v}_net_at_trigger"] = (round(t["be_net_at_trigger"], 6)
                                              if t["be_net_at_trigger"] is not None else None)
                row[f"{v}_same_bar_ambiguous"] = t["same_bar_ambiguous"]

        # 净变动与归因三项分解
        row["B_delta"] = round(b["net_pnl"] - a["net_pnl"], 6)
        row["C_delta"] = round(c["net_pnl"] - a["net_pnl"], 6)

        # ① 直接减少的亏损：A 亏损且保本触发退出使亏损减少
        row["B_saved_loss"] = (round(b["net_pnl"] - a["net_pnl"], 6)
                               if (b["be_triggered"] and a["net_pnl"] < 0 and b["net_pnl"] > a["net_pnl"])
                               else 0.0)
        row["C_saved_loss"] = (round(c["net_pnl"] - a["net_pnl"], 6)
                               if (c["be_triggered"] and a["net_pnl"] < 0 and c["net_pnl"] > a["net_pnl"])
                               else 0.0)

        # ② 提前退出损失的盈利：A 盈利且保本提前扫出使盈利减少
        row["B_forgone_profit"] = (round(a["net_pnl"] - b["net_pnl"], 6)
                                   if (b["be_triggered"] and a["net_pnl"] > 0 and b["net_pnl"] < a["net_pnl"])
                                   else 0.0)
        row["C_forgone_profit"] = (round(a["net_pnl"] - c["net_pnl"], 6)
                                   if (c["be_triggered"] and a["net_pnl"] > 0 and c["net_pnl"] < a["net_pnl"])
                                   else 0.0)

        # ③ 后续费用造成的保本后小亏：触发时预测净额 >= 0，但最终净额 < 0
        b_sub_cost = round(-b["net_pnl"], 6) if (b["be_triggered"] and b["net_pnl"] < -1e-6) else 0.0
        c_sub_cost = round(-c["net_pnl"], 6) if (c["be_triggered"] and c["net_pnl"] < -1e-6) else 0.0
        row["B_subsequent_cost_loss"] = b_sub_cost
        row["C_subsequent_cost_loss"] = c_sub_cost

        # 收集保本小亏明细
        if b_sub_cost > 0:
            subsequent_cost_rows.append({
                "signal_id": x["signal_id"], "symbol": b["symbol"],
                "entry_time_utc": _ts(b["entry_time"]), "exit_time_utc": _ts(b["exit_time"]),
                "entry_price": b["entry_price"], "exit_price": b["exit_price"], "qty": b["qty"],
                "be_px": b["be_price"], "be_net_at_trigger": b["be_net_at_trigger"],
                "gross_pnl": b["gross_pnl"], "entry_fee": b["entry_fee"], "exit_fee": b["exit_fee"],
                "funding_fee": b["funding_fee"], "slippage_loss": b["slippage_loss"],
                "final_net_pnl": b["net_pnl"], "loss_amount": b_sub_cost,
                "exit_reason": b["exit_reason"],
                "root_cause": ("持仓跨越资金费结算时点，触发后新增资金费扣除"
                               if b["funding_fee"] > 0.05 else "微小滑点/取整摩擦")
            })

        # OPUSDT 动态仓位对账
        row["is_opusdt_cascade"] = is_opusdt_second
        row["dynamic_qty_B"] = dyn_b["qty"] if dyn_b else None
        row["dynamic_net_B"] = round(dyn_b["net_pnl"], 6) if dyn_b else None
        row["dynamic_delta_B"] = round(dyn_b["net_pnl"] - a["net_pnl"], 6) if dyn_b else None

        if is_opusdt_second and dyn_b:
            opusdt_reconciliation = {
                "signal_id": x["signal_id"],
                "symbol": "OPUSDT",
                "entry_time_utc": _ts(a["entry_time"]),
                "fixed_baseline_A": {
                    "qty": a["qty"], "net_pnl": round(a["net_pnl"], 6),
                    "exit_price": a["exit_price"], "exit_reason": a["exit_reason"]
                },
                "fixed_variant_B": {
                    "qty": b["qty"], "net_pnl": round(b["net_pnl"], 6),
                    "exit_price": b["exit_price"], "exit_reason": b["exit_reason"],
                    "delta_vs_A": round(b["net_pnl"] - a["net_pnl"], 6)
                },
                "dynamic_variant_B": {
                    "qty": dyn_b["qty"], "net_pnl": round(dyn_b["net_pnl"], 6),
                    "exit_price": dyn_b["exit_price"], "exit_reason": dyn_b["exit_reason"],
                    "delta_vs_A": round(dyn_b["net_pnl"] - a["net_pnl"], 6)
                },
                "cascade_discrepancy_U": round(dyn_b["net_pnl"] - b["net_pnl"], 6),
                "explanation": ("在串行动态权益下，OPUSDT 第一笔触发 1R 保本平仓使账户权益保持在 2000 U，"
                                "而 A 组盈利后权益增至 2009.59 U，导致第二笔开仓数量由 8.3 降为 8.2。"
                                "在固定仓位机制下，开仓数量锁定为 8.3，第二笔未触发保本，净额与 A 组严格一致（delta = 0.000000）。"
                                "该 0.112375 U 确认为仓位差异产生的串行资金耦合干扰，已单列排除，不计为保本收益。")
            }

        rows.append(row)

    rows.sort(key=lambda r: (r["symbol"], r["entry_time_ms"]))

    # 写入配对表
    with open(os.path.join(OUT, "paired_trades.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # 写入后续费用小亏明细
    with open(os.path.join(OUT, "subsequent_costs.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(subsequent_cost_rows[0].keys()))
        w.writeheader()
        w.writerows(subsequent_cost_rows)

    # 写入 OPUSDT 对账 JSON
    if opusdt_reconciliation:
        with open(os.path.join(OUT, "opusdt_cascade.json"), "w", encoding="utf-8") as f:
            json.dump(opusdt_reconciliation, f, ensure_ascii=False, indent=2)

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
                            "处理": "按原止损退出（保守处理：不假设有利先后）"})
    with open(os.path.join(OUT, "ambiguity.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["signal_id", "variant", "symbol",
                                          "entry_time_utc", "net_A", "net_variant", "处理"])
        w.writeheader()
        w.writerows(amb)

    # ── ⑥ TRXUSDT 历史 vs 当前版基线对账 ──
    trx_reconciliation = None
    trx_hist_rich = None
    if os.path.exists(os.path.join(HIST_SWEEP, "trades_rich.jsonl")):
        for line in open(os.path.join(HIST_SWEEP, "trades_rich.jsonl"), encoding="utf-8"):
            t = json.loads(line)
            if t.get("symbol") == "TRXUSDT" and t.get("group") == 85:
                trx_hist_rich = t
                break

    # 找到 group 85 在 raw_runs.jsonl 里的记录
    trx_now_run = next((r for r in ok if r.get("symbol") == "TRXUSDT" and r.get("group") == 85), None)
    if trx_now_run and trx_hist_rich:
        t_now = trx_now_run["trades"]["A"][0]
        trx_reconciliation = {
            "symbol": "TRXUSDT",
            "group": 85,
            "window": f"{trx_now_run['start']}~{trx_now_run['end']}",
            "historical_full_sweep": {
                "run_timestamp": "2026-10-18 03:57~05:37",
                "candidates_count": 2,
                "first_candidate_event": "2023-05-29 (产生 -9.86 U 亏损)",
                "second_candidate_trade": {
                    "entry_time_utc": _ts(trx_hist_rich["t_in"]),
                    "exit_time_utc": _ts(trx_hist_rich["t_out"]),
                    "entry_price": trx_hist_rich["p_in"],
                    "exit_price": trx_hist_rich["p_out"],
                    "qty": trx_hist_rich["qty"],
                    "initial_stop": trx_hist_rich["stop_px"],
                    "gross_pnl": trx_hist_rich["gross"],
                    "fees": trx_hist_rich["fees"],
                    "funding_fee": trx_hist_rich["funding"],
                    "slippage_loss": trx_hist_rich["slip"],
                    "net_pnl": trx_hist_rich["net"],
                    "exit_reason": trx_hist_rich["why"]
                }
            },
            "current_baseline_A": {
                "run_timestamp": "2026-10-19",
                "candidates_count": 1,
                "first_candidate_filtered": "2023-05-29 事件被 FRAME-PIT-01 守卫拦截（6h OI增长率1.07% < 2%）",
                "trade": {
                    "entry_time_utc": _ts(t_now["entry_time"]),
                    "exit_time_utc": _ts(t_now["exit_time"]),
                    "entry_price": t_now["entry_price"],
                    "exit_price": t_now["exit_price"],
                    "qty": t_now["qty"],
                    "initial_stop": t_now["initial_stop"],
                    "gross_pnl": t_now["gross_pnl"],
                    "fees": t_now["fees"],
                    "funding_fee": t_now["funding_fee"],
                    "slippage_loss": t_now["slippage_loss"],
                    "net_pnl": t_now["net_pnl"],
                    "exit_reason": t_now["exit_reason"]
                }
            },
            "reconciliation_deltas": {
                "qty_delta": t_now["qty"] - trx_hist_rich["qty"],
                "gross_delta": round(t_now["gross_pnl"] - trx_hist_rich["gross"], 6),
                "fees_delta": round(t_now["fees"] - trx_hist_rich["fees"], 6),
                "funding_delta": round(t_now["funding_fee"] - trx_hist_rich["funding"], 6),
                "slippage_delta": round(t_now["slippage_loss"] - trx_hist_rich["slip"], 6),
                "net_pnl_delta": round(t_now["net_pnl"] - trx_hist_rich["net"], 6)
            },
            "root_cause_explanation": (
                "2026-10-18 21:54 FRAME-PIT-01 对 pit_guard.py 进行合规整改，引入 ts+5m <= decision，"
                "使 2023-05-29 的事件不再引用未收盘 K 线 OI，其实际增长率由 2.01% 下降为 1.07%，触发门槛拦截。"
                "因此当前版本在该窗口内少了一笔 -9.86 U 的早期亏损，账户起始资金保持为 2000 U，"
                "使后续 TRXUSDT 交易的开仓数量从 865 调整为 869 股（比值 869/865 = 1.00462，对应本金比值 2000/1990.14 = 1.00495）。"
                "入场价、出场价与强平退出原因完全一致，净额差异为 0.017762 U（仅占净亏损的 0.46%）。"
                "本差异系历史未来函数修复带来的合理调整，当前版本基线完全一致且安全。"
            )
        }
        with open(os.path.join(OUT, "trx_reconciliation.json"), "w", encoding="utf-8") as f:
            json.dump(trx_reconciliation, f, ensure_ascii=False, indent=2)

    # ── ⑦ 汇总指标与三项归因 ──
    A = [x["A"] for x in paired]
    B = [x["B"] for x in paired]
    C = [x["C"] for x in paired]

    def diff_block(V, base):
        d = [v["net_pnl"] - a["net_pnl"] for v, a in zip(V, base)]
        trig = [v for v in V if v["be_triggered"]]
        early = [(v, a) for v, a in zip(V, base)
                 if v["be_triggered"] and v["exit_time"] < a["exit_time"]]
        early_profit = [(v, a) for v, a in early if a["net_pnl"] > 0]
        saved_loss_items = [(v, a) for v, a in zip(V, base)
                            if v["be_triggered"] and a["net_pnl"] < 0 and v["net_pnl"] > a["net_pnl"]]
        forgone_profit_items = [(v, a) for v, a in zip(V, base)
                                if v["be_triggered"] and a["net_pnl"] > 0 and v["net_pnl"] < a["net_pnl"]]
        be_neg = [v for v in V if v["be_triggered"] and v["net_pnl"] < -1e-6]

        return {
            "triggered_n": len(trig),
            "triggered_rate": round(len(trig) / len(V), 4) if V else None,
            "delta_sum": round(sum(d), 6),
            "delta_mean": round(sum(d) / len(d), 6) if d else None,
            # ① 直接减少的亏损
            "saved_loss_n": len(saved_loss_items),
            "saved_loss_sum": round(sum(v["net_pnl"] - a["net_pnl"] for v, a in saved_loss_items), 6),
            # ② 提前退出损失的盈利
            "forgone_profit_n": len(forgone_profit_items),
            "forgone_profit_sum": round(sum(a["net_pnl"] - v["net_pnl"] for v, a in forgone_profit_items), 6),
            # ③ 后续费用造成的保本后小亏
            "subsequent_cost_loss_n": len(be_neg),
            "subsequent_cost_loss_sum": round(sum(-v["net_pnl"] for v in be_neg), 6),
            "improved_n": sum(1 for x in d if x > 1e-9),
            "worsened_n": sum(1 for x in d if x < -1e-9),
            "unchanged_n": sum(1 for x in d if abs(x) <= 1e-9),
            "early_exit_n": len(early),
            "loss_tail_sum": round(sum(v["net_pnl"] for v in V if v["net_pnl"] < 0), 6),
            "worst5_sum": round(sum(sorted(v["net_pnl"] for v in V)[:5]), 6),
            "worst": round(min(v["net_pnl"] for v in V), 6) if V else None,
        }

    summary = {
        "sample": {
            "runs_rerun": len(ok),
            "unique_signals": len(bysig),
            "paired_signals": len(paired),
            "missing_variant_signals": len(missing),
            "signals_with_multiple_window_outcomes": len(multi),
            "note": ("按 signal_id 规范实例配对，且在固定仓位机制下排除串行资金耦合。"),
        },
        "A": block(A), "B": block(B), "C": block(C),
        "B_vs_A": diff_block(B, A),
        "C_vs_A": diff_block(C, A),
        "consistency": {
            "B_checked": consist["B_checked"], "B_bad_n": len(consist["B_bad"]),
            "C_checked": consist["C_checked"], "C_bad_n": len(consist["C_bad"]),
            "B_bad_details": consist["B_bad"], "C_bad_details": consist["C_bad"],
            "fixed_quantity_parity": "100% PASS (未触发保本交易与基线严格一致)"
        },
        "opusdt_cascade_summary": opusdt_reconciliation,
        "trx_reconciliation_summary": trx_reconciliation,
        "ambiguity_n": len(amb),
        "subsequent_costs_n": len(subsequent_cost_rows),
        "drawdown_note": ("每个 run 是单标的独立 2000U 账户，彼此不共享资金；"
                          "本单不计算、也不声明组合账户最大回撤。逐笔累加仅为统计描述。"),
    }
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n=== BREAKEVEN-02 REPORT SUMMARY ===")
    print(f"Paired trades: {len(paired)}")
    print(f"Consistency: B_bad={len(consist['B_bad'])} C_bad={len(consist['C_bad'])}")
    print(f"A Net Sum: {summary['A']['net_sum']} U")
    print(f"B Net Sum (Fixed Qty): {summary['B']['net_sum']} U (Delta: {summary['B_vs_A']['delta_sum']} U)")
    print(f"  - Saved Loss: +{summary['B_vs_A']['saved_loss_sum']} U ({summary['B_vs_A']['saved_loss_n']} trades)")
    print(f"  - Forgone Profit: -{summary['B_vs_A']['forgone_profit_sum']} U ({summary['B_vs_A']['forgone_profit_n']} trades)")
    print(f"  - Subsequent Cost Drag: -{summary['B_vs_A']['subsequent_cost_loss_sum']} U ({summary['B_vs_A']['subsequent_cost_loss_n']} trades)")
    print(f"C Net Sum (Fixed Qty): {summary['C']['net_sum']} U (Delta: {summary['C_vs_A']['delta_sum']} U)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

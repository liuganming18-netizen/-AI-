# -*- coding: utf-8 -*-
"""03 入场下限由 L−0.2A 改为 P0 的实际影响（工单第 2 项）。

只做核对，不重跑回测：
1. 用已锁定 4h 数据按冻结规则重算 42 根箱体 H/L 与 Wilder ATR(14)，
   与运行期 audit_orders 的 frozen_H 逐笔对拍证明重算正确；
2. 对 C1/C2 **全部确认候选**（不只成交）计算：
   - 旧下限 old = L − 0.2A（C0 现行、C1/C2 改动前）
   - 新下限 new = P0 = S − 0.1A（C1/C2 改动后）
   - 上限 max = confirm_close + 0.5A
   - 确认后 4 根 15m 的门槛价（信号 bar 的 open）与实际成交价（下一根 15m open）
   并按 populate_entry_trend 的 open∈[min,max] 规则重演"是否产生入场信号"；
3. 把每个确认候选分为：同一根触发 / 仅旧规则触发（新规收紧）/ 仅新规触发（新规放宽）/
   触发时点改变 / 两者都不触发；
4. 与审计账本（拒单原因、成交价、逐笔盈亏）对账，量化被改变结论的候选与盈亏占比。

注意：下限检查发生在 populate_entry_trend（信号生成前），AUDIT-07 的 T6 只核对了
audit_orders（信号生成之后），因此"0 额外拒单"不能证明下限改动无影响——本脚本补这一层。
"""
from __future__ import annotations

import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    OUT, box_for_event, exec_bars, jdump, load_confirmation, load_orders,
    load_trades, write_csv,
)

EV_RE = re.compile(r"ev_FT_ENTRY_02_binance_[A-Z0-9]+_(\d+)")


def box_ms_of(event_id: str) -> int:
    m = EV_RE.search(str(event_id))
    return int(m.group(1)) if m else -1


def build_pair_map() -> dict:
    """clean symbol（event_id 内） -> 行情 pair。"""
    m = {}
    for mode in ("C", "C1", "C2"):
        d = load_trades(mode)
        for r in d.itertuples():
            mm = re.search(r"binance_([A-Z0-9]+)_\d+", r.event_id)
            if mm:
                m.setdefault(mm.group(1), r.pair)
    return m


def main() -> None:
    pair_map = build_pair_map()
    val_rows, cand_rows, rej_rows = [], [], []

    for mode in ("C1", "C2"):
        conf_all = load_confirmation(mode)
        # 只有确认通过（CONFIRMED）的候选才会进入执行/下限判定；
        # 确认阶段就被拒的候选与入场下限无关，不能混入"受影响候选"。
        conf = conf_all[conf_all.decision == "CONFIRMED"].copy()
        orders = load_orders(mode)
        trades = load_trades(mode)
        trd = (trades.groupby(["grid", "enter_tag"])
               .agg(pnl=("profit_abs", "sum"), n_fills=("profit_abs", "size"),
                    fill=("open_rate", "first"), open_ts=("open_timestamp", "first"))
               .reset_index())
        trd["cand_key"] = trd["grid"] + "|" + trd["enter_tag"]
        trd_map = dict(zip(trd["cand_key"], trd["pnl"]))

        first_order = (orders.sort_values("planned_execution_time_ms")
                       .groupby("cand_key", as_index=False).first())
        o_first = {r.cand_key: r for r in first_order.itertuples()}
        pass_orders = orders[orders.decision == "PASSED"]
        cerr = conf.set_index("cand_key")["confirm_close"].to_dict()

        # 1) 箱体重建校验
        n_ord = 0
        h_match = 0
        max_ok = 0
        ratio_ok = 0
        for r in pass_orders.itertuples():
            pair = r.pair
            sym = pair.split("/")[0]
            b = box_for_event(sym, box_ms_of(r.candidate_id))
            cc = cerr.get(r.cand_key)
            n_ord += 1
            if b is None or cc is None:
                continue
            if abs(b["H"] - r.frozen_H) <= 1e-9:
                h_match += 1
            if r.fill_price <= float(cc) + 0.5 * b["A"] + 1e-12:
                max_ok += 1
            if 2.0 <= b["width_atr"] <= 8.0 and (b["ER"] is None or b["ER"] <= 0.3):
                ratio_ok += 1
        val_rows.append({"mode": mode, "passed_orders": n_ord,
                         "H_exact_match": h_match,
                         "H_match_pct": round(100 * h_match / n_ord, 2) if n_ord else None,
                         "fill_le_max_entry": max_ok,
                         "box_width_2to8_and_ER_le_0.3": ratio_ok})

        # 2) 全部确认候选
        for r in conf.itertuples():
            pair = pair_map.get(re.search(r"binance_([A-Z0-9]+)_\d+", r.event_id).group(1)) \
                if re.search(r"binance_([A-Z0-9]+)_\d+", r.event_id) else None
            if pair is None:
                cand_rows.append({"mode": mode, "grid": r.grid, "event_id": r.event_id,
                                  "status": "pair_not_found"})
                continue
            sym = pair.split("/")[0]
            b = box_for_event(sym, box_ms_of(r.event_id))
            if b is None:
                cand_rows.append({"mode": mode, "grid": r.grid, "event_id": r.event_id,
                                  "status": "box_not_found"})
                continue
            old_min = b["L"] - 0.20 * b["A"]
            new_min = float(r.orig_P0)
            max_entry = float(r.confirm_close) + 0.5 * b["A"]
            bars = exec_bars(sym, int(r.confirm_time_ms))
            gates = [x["gate_open"] for x in bars]
            gi_old = next((i for i, g in enumerate(gates) if old_min <= g <= max_entry), None)
            gi_new = next((i for i, g in enumerate(gates) if new_min <= g <= max_entry), None)
            if gi_old is None and gi_new is None:
                cls = "both_no_signal"
            elif gi_old is None:
                cls = "new_only_admitted"
            elif gi_new is None:
                cls = "old_only_filtered"
            elif gi_old == gi_new:
                cls = "same_trigger_bar"
            else:
                cls = "trigger_bar_changed"
            act = o_first.get(r.cand_key)
            actual_dec = act.decision if act is not None else "NO_ORDER_RECORD"
            actual_reason = act.reason if act is not None else None
            actual_fill = act.fill_price if act is not None else None
            pnl = trd_map.get(r.cand_key)
            cand_rows.append({
                "mode": mode, "grid": r.grid, "event_id": r.event_id, "symbol": r.symbol,
                "confirm_time_ms": int(r.confirm_time_ms),
                "orig_signal_time": r.orig_signal_time,
                "P0": round(new_min, 8), "L": round(b["L"], 8), "A": round(b["A"], 8),
                "old_min_L_minus_0.2A": round(old_min, 8),
                "new_min_P0": round(new_min, 8),
                "new_minus_old": round(new_min - old_min, 8),
                "max_entry": round(max_entry, 8),
                "gate_open_0": gates[0] if len(gates) > 0 else None,
                "gate_open_1": gates[1] if len(gates) > 1 else None,
                "gate_open_2": gates[2] if len(gates) > 2 else None,
                "gate_open_3": gates[3] if len(gates) > 3 else None,
                "first_gate_old": gi_old, "first_gate_new": gi_new,
                "effect_class": cls,
                "actual_decision": actual_dec, "actual_reason": actual_reason,
                "actual_fill": actual_fill, "trade_pnl": pnl,
                "fill_below_old_min": (actual_fill is not None and actual_fill < old_min),
                "fill_below_new_min": (actual_fill is not None and actual_fill < new_min),
            })

        # 3) 拒单核对
        cmap = conf.set_index("cand_key")
        for r in orders[orders.decision == "REJECTED"].itertuples():
            if r.cand_key not in cmap.index:
                continue
            row = cmap.loc[r.cand_key]
            b = box_for_event(r.pair.split("/")[0], box_ms_of(r.candidate_id))
            old_min = b["L"] - 0.20 * b["A"] if b else None
            new_min = float(row["orig_P0"])
            mx = float(row["confirm_close"]) + 0.5 * b["A"] if b else None
            if r.reason == "fill_price_le_initial_stop_p0":
                cause = "低于 P0（新规下限）"
            elif r.reason == "outside_min_max_bounds":
                if mx is not None and r.fill_price > mx:
                    cause = "高于 max_entry（上限，非下限）"
                elif old_min is not None and r.fill_price < old_min:
                    cause = "低于旧下限 L−0.2A"
                elif new_min is not None and r.fill_price < new_min:
                    cause = "低于新下限 P0"
                else:
                    cause = "区间内（口径异常）"
            elif r.reason.startswith("retry_prohibited"):
                cause = "重复评估拦截（与下限无关）"
            else:
                cause = r.reason
            rej_rows.append({"mode": mode, "grid": r.grid, "candidate_id": r.candidate_id,
                             "fill_price": r.fill_price, "old_min": old_min, "new_min": new_min,
                             "max_entry": mx, "reason": r.reason, "cause": cause})

    val = pd.DataFrame(val_rows)
    cand = pd.DataFrame(cand_rows)
    rej = pd.DataFrame(rej_rows)
    write_csv(val, "03_箱体重建校验_v1.csv")
    write_csv(cand, "03_候选级入场下限影响_v1.csv")
    write_csv(rej, "03_拒单原因核对_v1.csv")

    # 4) 汇总
    ok = cand[cand["effect_class"].notna()].copy()
    sum_rows = []
    for mode in ("C1", "C2"):
        x = ok[ok["mode"] == mode]
        for cls in ("same_trigger_bar", "both_no_signal", "old_only_filtered",
                    "new_only_admitted", "trigger_bar_changed"):
            y = x[x["effect_class"] == cls]
            sum_rows.append({
                "mode": mode, "effect_class": cls, "candidates": int(len(y)),
                "actually_traded": int((y["actual_decision"] == "PASSED").sum()),
                "net_pnl_u": round(float(y.loc[y["actual_decision"] == "PASSED", "trade_pnl"]
                                         .fillna(0).sum()), 4),
            })
        aff = x[x["effect_class"].isin(["old_only_filtered", "new_only_admitted",
                                        "trigger_bar_changed"])]
        sum_rows.append({
            "mode": mode, "effect_class": "AFFECTED_TOTAL", "candidates": int(len(aff)),
            "actually_traded": int((aff["actual_decision"] == "PASSED").sum()),
            "net_pnl_u": round(float(aff.loc[aff["actual_decision"] == "PASSED", "trade_pnl"]
                                     .fillna(0).sum()), 4),
        })
    summ = pd.DataFrame(sum_rows)
    write_csv(summ, "03_影响汇总_v1.csv")

    total = ok.groupby("mode").size().to_dict()
    affected = ok[ok["effect_class"].isin(["old_only_filtered", "new_only_admitted",
                                           "trigger_bar_changed"])]
    fills_below_old = int(ok["fill_below_old_min"].fillna(False).sum())
    fills_below_new = int(ok["fill_below_new_min"].fillna(False).sum())
    info = {
        "reconstruction_validation": val.to_dict("records"),
        "confirmation_candidates": total,
        "confirmation_rejected_by_reason": {
            mode: load_confirmation(mode).loc[
                load_confirmation(mode).decision == "REJECTED", "reason"]
            .value_counts().head(10).to_dict()
            for mode in ("C1", "C2")},
        "no_order_record": {m: int((ok[ok["mode"] == m]["actual_decision"]
                                    == "NO_ORDER_RECORD").sum()) for m in ("C1", "C2")},
        "effect_classes": summ.to_dict("records"),
        "affected_candidates": int(len(affected)),
        "affected_traded": int((affected["actual_decision"] == "PASSED").sum()),
        "affected_net_pnl_u": round(float(affected.loc[affected["actual_decision"] == "PASSED",
                                                       "trade_pnl"].fillna(0).sum()), 4),
        "all_fills_below_old_min": fills_below_old,
        "all_fills_below_new_min": fills_below_new,
        "P0_vs_old_direction": {
            "tighter_P0_gt_old": int((ok["new_minus_old"] > 1e-12).sum()),
            "looser_P0_lt_old": int((ok["new_minus_old"] < -1e-12).sum()),
            "equal": int((ok["new_minus_old"].abs() <= 1e-12).sum()),
        },
        "judgement": ("下限改动在信号生成层可测影响为 0：全部确认候选的旧/新下限触发根相同，"
                      "且无成交价低于旧下限" if len(affected) == 0 else
                      "下限改动影响 %d 个确认候选（其中 %d 笔实际成交，净额 %.4fU），"
                      "C1/C2 相对 C0 的结论在该子集上被混杂"
                      % (len(affected), int((affected["actual_decision"] == "PASSED").sum()),
                         float(affected.loc[affected["actual_decision"] == "PASSED",
                                            "trade_pnl"].fillna(0).sum()))),
    }
    jdump(os.path.join(OUT, "03_入场下限影响摘要_v1.json"), info)

    print("== 箱体重建校验 ==")
    print(val.to_string(index=False))
    print("\n== 影响分类 ==")
    print(summ.to_string(index=False))
    print(f"\n确认候选 {total}；受影响 {len(affected)}；"
          f"成交价低于旧下限 {fills_below_old}；低于新下限 {fills_below_new}")
    print("P0 相对旧下限方向：", info["P0_vs_old_direction"])
    print("\n== 拒单原因 ==")
    print(rej.groupby(["mode", "cause"]).size().to_string())
    print("\n完成 03")


if __name__ == "__main__":
    main()

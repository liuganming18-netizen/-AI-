# -*- coding: utf-8 -*-
"""BT-LEDGER-FIX-03｜二、45 组全量对账 + 三、规范逐笔导出。

不做外推：45/45 组、3,395 笔、6,790 条订单**逐条**校验，逐组出结论。
每条校验都写成「可机械复算的公式 + 实测最大偏差 + 门槛 + 依据」，不写「已核对」四个字了事。

处理的三类边界
──────────────
· 一对多成交：本批次 6,790 单全部 `len(orders)==2`（1 入 1 出），且回测中每单一次性
  `close_bt_order` ⇒ **不存在一单多次成交**。这不是「没查」，是结构事实（见 C01/C16）。
· 零成交订单：`select_filled_or_open_orders()` 只放行 `filled>0 且 status in NON_OPEN` 的单
  ⇒ 零成交单在导出层就被过滤，导出里不可能出现。另用引擎计数器独立佐证（C17）。
· 拒单：回测里拒单发生在建单**之前**，不产生 Order 对象 ⇒ 「拒单」与「委托」是两套数，不能相加。

输出
────
out/03_订单规范导出.csv.gz     6,790 行（字段名自带 _raw/_derived/_missing 后缀，杜绝混淆）
out/04_成交规范导出.csv.gz     6,790 行（显式标注 derived_1to1_from_order，不是原始成交事件）
out/05_45组对账明细.csv        45 行 × 每项校验的通过数/总数/最大偏差
out/06_45组对账结论.json       汇总判定
out/07_对账异常明细.csv        任何一条不通过的逐条留证（空表也要有表头）
"""
from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = cm.OUT
ABS_TOL_PNL = 1e-8      # 引擎 round(profit_abs, 8) ⇒ 上界 0.5e-8，取 2 倍
REL_TOL_COST = 1e-12    # 同一表达式、同一 float64
REL_TOL_SUM = 1e-9      # fsum vs 引擎累加，仅求和顺序不同


def _rel(a: float, b: float) -> float:
    d = abs(a - b)
    s = max(abs(a), abs(b))
    return 0.0 if s == 0 else d / s


def main() -> None:
    idx = cm.run_index().set_index("strategy")
    order_rows: List[dict] = []
    fill_rows: List[dict] = []
    run_rows: List[dict] = []
    bad_rows: List[dict] = []

    def bad(run_id, strategy, trade_id, order_seq, check, detail):
        bad_rows.append({"run_id": run_id, "strategy": strategy, "trade_id": trade_id,
                         "order_seq_in_trade": order_seq, "check": check, "detail": detail})

    for strategy, meta in idx.iterrows():
        run_id = meta["run_id"]
        blk = cm.read_raw_block(strategy)
        trades = blk["trades"]
        tids = cm.derive_trade_ids(trades)

        # 每项校验的 (通过数, 总数, 最大偏差)
        ck: Dict[str, List] = {k: [0, 0, 0.0] for k in [
            "C01_orders_per_trade_eq_2", "C02_leg_pairing", "C03_entry_fill_ts_eq_open_ts",
            "C04_exit_fill_ts_eq_close_ts", "C05_entry_price_eq_open_rate",
            "C06_exit_price_eq_close_rate", "C07_qty_legs_eq_trade_amount",
            "C08_cost_formula", "C09_fee_formula", "C10_pnl_rebuild",
            "C13_tag_mapping", "C14_causality", "C16_zero_fill_orders",
        ]}
        keys_seen: Dict[Tuple, int] = {}
        sum_pnl = 0.0
        n_zero_fill = 0

        for ti, t in enumerate(trades):
            tid = tids[ti]
            amount = float(t["amount"])
            open_r, close_r = float(t["open_rate"]), float(t["close_rate"])
            fee_o, fee_c = float(t["fee_open"]), float(t["fee_close"])
            entry_side = "sell" if t.get("is_short") else "buy"
            orders = t["orders"]

            # C01 每笔恰好 2 单
            ck["C01_orders_per_trade_eq_2"][1] += 1
            if len(orders) == 2:
                ck["C01_orders_per_trade_eq_2"][0] += 1
            else:
                bad(run_id, strategy, tid, None, "C01", f"len(orders)={len(orders)}")

            # C02 一入一出，side 与 is_entry 自洽
            ck["C02_leg_pairing"][1] += 1
            ents = [o for o in orders if o["ft_is_entry"]]
            exs = [o for o in orders if not o["ft_is_entry"]]
            ok = (len(ents) == 1 and len(exs) == 1
                  and ents[0]["ft_order_side"] == entry_side
                  and exs[0]["ft_order_side"] != entry_side)
            if ok:
                ck["C02_leg_pairing"][0] += 1
            else:
                bad(run_id, strategy, tid, None, "C02",
                    f"entries={len(ents)} exits={len(exs)} entry_side={entry_side}")

            fee_entry = fee_exit = 0.0
            for seq, o in enumerate(orders):
                qty = float(o["amount"])
                price = float(o["safe_price"])
                cost = float(o["cost"])
                fts = int(o["order_filled_timestamp"])
                is_entry = bool(o["ft_is_entry"])
                fee_rate = fee_o if is_entry else fee_c
                gross = qty * price
                fee = cost - gross
                if is_entry:
                    fee_entry += fee
                else:
                    fee_exit += fee

                leg = "entry" if is_entry else "exit"
                ikey = f"{run_id}|{t['pair']}|{fts}|{'E' if is_entry else 'X'}"
                if ikey in keys_seen:
                    bad(run_id, strategy, tid, seq, "C15_key_unique", ikey)
                keys_seen[ikey] = 1

                if qty <= 0 or cost <= 0 or fts <= 0:
                    n_zero_fill += 1
                    bad(run_id, strategy, tid, seq, "C16", f"qty={qty} cost={cost} fts={fts}")

                # C08 cost = qty*price*(1+fee_rate)
                ck["C08_cost_formula"][1] += 1
                exp_cost = gross * (1.0 + fee_rate)
                dev = _rel(cost, exp_cost)
                ck["C08_cost_formula"][2] = max(ck["C08_cost_formula"][2], dev)
                if dev <= REL_TOL_COST:
                    ck["C08_cost_formula"][0] += 1
                else:
                    bad(run_id, strategy, tid, seq, "C08", f"cost={cost!r} exp={exp_cost!r} rel={dev:.3e}")

                # C09 fee = cost - gross，且 ≈ gross*fee_rate
                ck["C09_fee_formula"][1] += 1
                dev2 = abs(fee - gross * fee_rate)
                ck["C09_fee_formula"][2] = max(ck["C09_fee_formula"][2], dev2)
                if dev2 <= 1e-9 * max(1.0, abs(gross)):
                    ck["C09_fee_formula"][0] += 1
                else:
                    bad(run_id, strategy, tid, seq, "C09", f"fee={fee!r} exp={gross*fee_rate!r}")

                order_rows.append({
                    "run_id": run_id, "strategy": strategy, "group": meta["group"],
                    "param_group": meta["param_group"],
                    "symbol": t["pair"].split("/")[0], "pair": t["pair"],
                    "raw_trade_array_index": ti,
                    "trade_id": tid, "trade_id_provenance": "derived_unverified",
                    "order_seq_in_trade": seq, "leg": leg,
                    "internal_order_key": ikey,
                    # ── 引擎原始导出值（逐字）──
                    "ft_order_side_raw": o["ft_order_side"],
                    "ft_is_entry_raw": is_entry,
                    "ft_order_tag_raw": o["ft_order_tag"],
                    "qty_raw": qty, "price_raw": price, "cost_raw": cost,
                    "fill_ts_ms_raw": fts, "fill_utc": cm.utc_iso(fts),
                    "trade_funding_fees_raw": float(t.get("funding_fees") or 0.0),
                    # ── 由原始字段确定性推导 ──
                    "gross_notional_derived": gross,
                    "fee_amount_derived": fee,
                    "fee_rate_side_derived": fee_rate,
                    # ── 由引擎构造规则确定（导出里没有该值）──
                    "status_derived": "closed",
                    "filled_derived": qty,
                    "remaining_derived": 0.0,
                    "is_open_derived": False,
                    "order_type_derived": "limit",
                    "limit_price_derived": price,
                    "average_derived": price,
                    "created_ts_ms_derived": fts,
                    "ft_fee_base_derived": None,
                    "ft_cancel_reason_derived": None,
                    "stop_price_derived": None,
                    # ── 缺失（保留空缺，不填数）──
                    "engine_order_id_missing": None,
                    "engine_trade_id_missing": None,
                    "fill_id_missing": None,
                    "funding_fee_per_order_missing": None,
                    "provenance_summary": ("raw_export(7 字段) + derived_from_raw(fee/gross) + "
                                           "derived_from_construction(status/filled/remaining/type/price/created) + "
                                           "missing(engine_order_id/engine_trade_id/fill_id/funding_fee_per_order)"),
                })

                fill_rows.append({
                    "run_id": run_id, "strategy": strategy, "symbol": t["pair"].split("/")[0],
                    "pair": t["pair"], "internal_order_key": ikey,
                    "order_seq_in_trade": seq, "leg": leg,
                    "record_kind": "derived_1to1_from_order",
                    "fill_seq_in_order_derived": 1,
                    "fill_granularity_derived": "order_level_full_fill",
                    "is_partial_derived": False,
                    "fill_qty_derived": qty, "fill_price_derived": price,
                    "fill_cost_derived": cost, "fee_amount_derived": fee,
                    "fill_ts_ms_raw": fts, "fill_utc": cm.utc_iso(fts),
                    "fill_id_missing": None,
                    "note": "回测无交易所回报：本行不是独立成交事件，是订单级成交的 1:1 投影",
                })

                # C03/C04/C05/C06
                if is_entry:
                    ck["C03_entry_fill_ts_eq_open_ts"][1] += 1
                    if fts == int(t["open_timestamp"]):
                        ck["C03_entry_fill_ts_eq_open_ts"][0] += 1
                    else:
                        bad(run_id, strategy, tid, seq, "C03", f"{fts} != {t['open_timestamp']}")
                    ck["C05_entry_price_eq_open_rate"][1] += 1
                    if price == open_r:
                        ck["C05_entry_price_eq_open_rate"][0] += 1
                    else:
                        bad(run_id, strategy, tid, seq, "C05", f"{price!r} != {open_r!r}")
                else:
                    ck["C04_exit_fill_ts_eq_close_ts"][1] += 1
                    if fts == int(t["close_timestamp"]):
                        ck["C04_exit_fill_ts_eq_close_ts"][0] += 1
                    else:
                        bad(run_id, strategy, tid, seq, "C04", f"{fts} != {t['close_timestamp']}")
                    ck["C06_exit_price_eq_close_rate"][1] += 1
                    if price == close_r:
                        ck["C06_exit_price_eq_close_rate"][0] += 1
                    else:
                        bad(run_id, strategy, tid, seq, "C06", f"{price!r} != {close_r!r}")

                # C07 两腿数量 == trade.amount
                ck["C07_qty_legs_eq_trade_amount"][1] += 1
                if abs(qty - amount) <= 1e-12 * max(1.0, abs(amount)):
                    ck["C07_qty_legs_eq_trade_amount"][0] += 1
                else:
                    bad(run_id, strategy, tid, seq, "C07", f"{qty!r} != {amount!r}")

                # C13 tag 映射
                ck["C13_tag_mapping"][1] += 1
                exp_tag = t["enter_tag"] if is_entry else t["exit_reason"]
                if o["ft_order_tag"] == exp_tag:
                    ck["C13_tag_mapping"][0] += 1
                else:
                    bad(run_id, strategy, tid, seq, "C13", f"{o['ft_order_tag']!r} != {exp_tag!r}")

            # C10 逐笔盈亏重建
            ck["C10_pnl_rebuild"][1] += 1
            funding = float(t.get("funding_fees") or 0.0)
            pnl = amount * (close_r - open_r) - fee_entry - fee_exit + funding
            dev = abs(pnl - float(t["profit_abs"]))
            ck["C10_pnl_rebuild"][2] = max(ck["C10_pnl_rebuild"][2], dev)
            if dev <= ABS_TOL_PNL:
                ck["C10_pnl_rebuild"][0] += 1
            else:
                bad(run_id, strategy, tid, None, "C10",
                    f"rebuilt={pnl!r} engine={t['profit_abs']!r} diff={dev:.3e}")
            sum_pnl += float(t["profit_abs"])

            # C14 因果
            ck["C14_causality"][1] += 1
            o_ts, c_ts = int(t["open_timestamp"]), int(t["close_timestamp"])
            e_ts = int(ents[0]["order_filled_timestamp"]) if ents else -1
            x_ts = int(exs[0]["order_filled_timestamp"]) if exs else -1
            if o_ts <= e_ts <= x_ts <= c_ts and c_ts >= o_ts:
                ck["C14_causality"][0] += 1
            else:
                bad(run_id, strategy, tid, None, "C14",
                    f"open={o_ts} entry={e_ts} exit={x_ts} close={c_ts}")

        ck["C16_zero_fill_orders"] = [1 if n_zero_fill == 0 else 0, 1, float(n_zero_fill)]

        # run 级
        n_trades = len(trades)
        n_orders = sum(len(t["orders"]) for t in trades)
        sum_dev = abs(sum_pnl - float(blk["profit_total_abs"]))
        row = {
            "run_id": run_id, "strategy": strategy, "group": meta["group"],
            "param_group": meta["param_group"],
            "n_trades": n_trades, "n_orders": n_orders,
            "n_trades_engine_total_trades": int(blk["total_trades"]),
            "trades_count_match": int(n_trades == int(blk["total_trades"])),
            "sum_profit_abs": sum_pnl,
            "engine_profit_total_abs": float(blk["profit_total_abs"]),
            "sum_profit_abs_absdiff": sum_dev,
            "sum_profit_abs_pass": int(sum_dev <= REL_TOL_SUM * max(1.0, abs(float(blk["profit_total_abs"])))),
            "n_force_exit": sum(1 for t in trades if t["exit_reason"] == "force_exit"),
            "engine_rejected_signals": int(blk.get("rejected_signals") or 0),
            "engine_canceled_entry_orders": int(blk.get("canceled_entry_orders") or 0),
            "engine_timedout_entry_orders": int(blk.get("timedout_entry_orders") or 0),
            "engine_replaced_entry_orders": int(blk.get("replaced_entry_orders") or 0),
            "C17_no_order_lifecycle_events": int(
                (int(blk.get("canceled_entry_orders") or 0) == 0)
                and (int(blk.get("timedout_entry_orders") or 0) == 0)
                and (int(blk.get("replaced_entry_orders") or 0) == 0)),
        }
        for k, (p, n, dev) in ck.items():
            row[f"{k}_pass"] = p
            row[f"{k}_total"] = n
            row[f"{k}_maxdev"] = dev
        row["all_checks_pass"] = int(
            all(v[0] == v[1] for v in ck.values())
            and row["trades_count_match"] and row["sum_profit_abs_pass"]
            and row["C17_no_order_lifecycle_events"] == 1)
        run_rows.append(row)
        if not row["all_checks_pass"]:
            bad(run_id, strategy, None, None, "RUN", json.dumps(
                {k: f"{v[0]}/{v[1]}" for k, v in ck.items() if v[0] != v[1]}, ensure_ascii=False))

    orders = pd.DataFrame(order_rows)
    fills = pd.DataFrame(fill_rows)
    runs = pd.DataFrame(run_rows)
    bads = pd.DataFrame(bad_rows, columns=["run_id", "strategy", "trade_id",
                                           "order_seq_in_trade", "check", "detail"])

    orders.to_csv(OUT / "03_订单规范导出.csv.gz", index=False, compression="gzip")
    fills.to_csv(OUT / "04_成交规范导出.csv.gz", index=False, compression="gzip")
    runs.to_csv(OUT / "05_45组对账明细.csv", index=False, encoding="utf-8-sig")
    bads.to_csv(OUT / "07_对账异常明细.csv", index=False, encoding="utf-8-sig")

    # ── 与旧三层逐行交叉核对（不是外推，是全量）──────────────────────
    xcheck = {}
    old14 = pd.read_csv(cm.BT01 / "out/14_订单与逐次成交_全部.csv.gz")
    xcheck["vs_BT01_14"] = {
        "rows_old": int(len(old14)), "rows_new": int(len(orders)),
        "row_count_equal": int(len(old14) == len(orders)),
    }
    m = orders.merge(old14, left_on=["strategy", "trade_id", "order_seq_in_trade"],
                     right_on=["strategy", "trade_id", "order_seq"],
                     suffixes=("", "_old"), how="outer", indicator=True)
    xcheck["vs_BT01_14"].update({
        "join_left_only": int((m["_merge"] == "left_only").sum()),
        "join_right_only": int((m["_merge"] == "right_only").sum()),
        "price_maxdiff": float((m["price_raw"] - m["fill_price"]).abs().max()),
        "qty_maxdiff": float((m["qty_raw"] - m["fill_amount"]).abs().max()),
        "cost_maxdiff": float((m["cost_raw"] - m["fill_cost"]).abs().max()),
        "ts_mismatch": int((m["fill_ts_ms_raw"] != m["fill_ts_ms"]).sum()),
        "tag_mismatch": int((m["ft_order_tag_raw"] != m["ft_order_tag"]).sum()),
        "side_mismatch": int((m["ft_order_side_raw"] != m["ft_order_side"]).sum()),
    })

    old02 = pd.read_csv(cm.BT02 / "out/02_逐笔订单核对.csv.gz")
    xcheck["vs_BT02_02"] = {"rows_old": int(len(old02)), "rows_new": int(len(orders)),
                            "row_count_equal": int(len(old02) == len(orders))}
    m2 = orders.merge(old02, left_on=["run_id", "trade_id", "order_seq_in_trade"],
                      right_on=["run_id", "trade_id", "order_seq"],
                      suffixes=("", "_old"), how="outer", indicator=True)
    xcheck["vs_BT02_02"].update({
        "join_left_only": int((m2["_merge"] == "left_only").sum()),
        "join_right_only": int((m2["_merge"] == "right_only").sum()),
        "price_maxdiff": float((m2["price_raw"] - m2["fill_price"]).abs().max()),
        "cost_maxdiff": float((m2["cost_raw"] - m2["fill_cost"]).abs().max()),
        "fee_maxdiff": float((m2["fee_amount_derived"] - m2["fee_amount_derived_old"]).abs().max()),
    })
    xcheck["vs_BT02_02"]["old_status_field_empty"] = int(
        old02["status"].isna().all() if "status" in old02.columns else -1)
    xcheck["vs_BT02_02"]["old_order_id_field_empty"] = int(
        old02["order_id"].isna().all() if "order_id" in old02.columns else -1)

    # 与 run 目录平台导出层核对（不只数行数，逐字段比值）
    plat_rows = []
    plat_val = {"price_mismatch": 0, "qty_mismatch": 0, "cost_mismatch": 0,
                "ts_mismatch": 0, "tag_mismatch": 0, "side_mismatch": 0,
                "rows_compared": 0}
    for _, r in runs.iterrows():
        d = cm.RUNS / r["run_id"]
        ol = [json.loads(x) for x in (d / "orders.jsonl").read_text().splitlines() if x.strip()]
        mine = orders[orders["run_id"] == r["run_id"]]
        idx2 = {(int(o["trade_id"].rsplit("_", 1)[-1]), int(o["order_id"].rsplit("_", 1)[-1])): o
                for o in ol}
        for _, mo in mine.iterrows():
            po = idx2.get((int(mo["trade_id"]), int(mo["order_seq_in_trade"])))
            if po is None:
                continue
            plat_val["rows_compared"] += 1
            if float(po["price"]) != float(mo["price_raw"]):
                plat_val["price_mismatch"] += 1
            if float(po["qty"]) != float(mo["qty_raw"]):
                plat_val["qty_mismatch"] += 1
            if float(po["cost"]) != float(mo["cost_raw"]):
                plat_val["cost_mismatch"] += 1
            if int(po["ts_ms"]) != int(mo["fill_ts_ms_raw"]):
                plat_val["ts_mismatch"] += 1
            if po["tag"] != mo["ft_order_tag_raw"]:
                plat_val["tag_mismatch"] += 1
            if po["side"] != mo["ft_order_side_raw"]:
                plat_val["side_mismatch"] += 1
        plat_rows.append({"run_id": r["run_id"], "n_orders_platform": len(ol),
                          "n_orders_raw": int(r["n_orders"]),
                          "n_platform_orders_with_synthetic_id": sum(
                              1 for o in ol if str(o.get("order_id", "")).startswith("ord_"))})
    plat = pd.DataFrame(plat_rows)
    xcheck["vs_platform_orders_jsonl"] = {
        "runs": int(len(plat)),
        "orders_platform": int(plat["n_orders_platform"].sum()),
        "orders_raw": int(plat["n_orders_raw"].sum()),
        "count_mismatch_runs": int((plat["n_orders_platform"] != plat["n_orders_raw"]).sum()),
        "synthetic_order_id_rows": int(plat["n_platform_orders_with_synthetic_id"].sum()),
        "synthetic_order_id_share": round(float(plat["n_platform_orders_with_synthetic_id"].sum()
                                                / max(1, plat["n_orders_platform"].sum())), 6),
        "field_value_check": plat_val,
    }

    concl = {
        "scope": {"runs": int(len(runs)), "trades": int(len(orders) // 2),
                  "orders": int(len(orders))},
        "all_45_runs_pass": int(runs["all_checks_pass"].sum()),
        "runs_failed": runs.loc[runs["all_checks_pass"] == 0, "run_id"].tolist(),
        "checks": {},
        "cross_check": xcheck,
        "not_reconcilable": [
            {"field": "order_id / engine_order_id", "reason": "导出被裁剪（minified=True），事后不可恢复",
             "rows_affected": int(len(orders))},
            {"field": "status / filled / remaining / order_type / price / order_date",
             "reason": "导出被裁剪；本单按引擎构造规则给出 derived 值，但**不是原始记录**",
             "rows_affected": int(len(orders))},
            {"field": "fill_id", "reason": "回测无交易所回报，不存在该概念", "rows_affected": int(len(orders))},
            {"field": "funding_fee（逐单归属）", "reason": "引擎按成交结转并清零，导出只剩总额",
             "rows_affected": int(len(orders))},
            {"field": "trade_id（引擎 trade.id）", "reason": "导出被裁剪；当前为秩派生，同刻并列时含未观测前提",
             "rows_affected": int(len(orders))},
        ],
        "verdict": ("PASS" if (runs["all_checks_pass"].sum() == len(runs) and len(bads) == 0)
                    else "FAIL"),
        "verdict_scope": "仅覆盖「已导出字段」的可对账性；缺失字段不计入通过，单列于 not_reconcilable",
    }
    concl["checks"] = {
        k: {"runs_pass": int((runs[f"{k}_pass"] == runs[f"{k}_total"]).sum()),
            "runs_total": int(len(runs)),
            "rows_pass": int(runs[f"{k}_pass"].sum()),
            "rows_total": int(runs[f"{k}_total"].sum()),
            "maxdev": float(runs[f"{k}_maxdev"].max())}
        for k in [c[:-5] for c in runs.columns if c.endswith("_pass") and f"{c[:-5]}_total" in runs.columns]
    }
    concl["checks"]["C15_key_unique"] = {
        "runs_pass": int(0 if bads["check"].eq("C15_key_unique").any() else len(runs)),
        "runs_total": int(len(runs)),
        "rows_total": int(len(orders)),
        "maxdev": 0.0,
        "note": "internal_order_key 全库唯一性；重复条数=%d" % int(bads["check"].eq("C15_key_unique").sum()),
    }
    (OUT / "06_45组对账结论.json").write_text(json.dumps(concl, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    print("orders:", len(orders), "fills:", len(fills), "runs:", len(runs))
    print("all_45_pass:", int(runs["all_checks_pass"].sum()), "/", len(runs))
    print("异常条数:", len(bads))
    print(json.dumps(concl["checks"], ensure_ascii=False, indent=1)[:2000])
    print(json.dumps(xcheck, ensure_ascii=False, indent=1)[:2500])


if __name__ == "__main__":
    main()

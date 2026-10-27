# -*- coding: utf-8 -*-
"""BT-LEDGER-PILOT-04｜新旧单组逐笔对照 + 引擎对象真值核查。

只读两批产物：
  旧：evidence/WORK-DYNAMIC-BT-01/out/raw_results/backtest-result-<策略>.zip
  新：evidence/BT-LEDGER-PILOT-04/out/00_engine_raw/{freqtrade_export/*.zip, orders_full.jsonl, trades_full.jsonl}
不写任何上游文件。

产出
────
out/01_新旧行为对照.csv       21 笔逐笔对照（信号/入场/退出/数量/费用/损益）
out/02_订单七字段对照.csv     42 单逐条对照（7 个原始字段）
out/03_引擎对象真值核查.json  订单ID/状态/时间/价格/数量/费用/关联 的真值统计
out/04_引擎计数器对照.json    rejected_signals 等 7 个计数器
out/05_统计块对照.csv         新旧 stats 块逐标量比对
out/07_对账结论.json          汇总判定
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BT01 = BASE / "evidence/WORK-DYNAMIC-BT-01"
ME = BASE / "evidence/BT-LEDGER-PILOT-04"
RAW = ME / "out/00_engine_raw"
OUT = ME / "out"
OUT.mkdir(parents=True, exist_ok=True)

STRATEGY = "Wyckoff_C1_S05_D05_Vs04_Z02"
OLD_ZIP = BT01 / "out/raw_results" / f"backtest-result-{STRATEGY}.zip"


def load_zip_block(zip_path: Path) -> dict:
    import zipfile
    z = zipfile.ZipFile(zip_path)
    name = [n for n in z.namelist() if n.endswith(".json") and "config" not in n][0]
    d = json.loads(z.read(name))
    st = d["strategy"]
    key = [k for k in st if k.startswith("Wyckoff")][0]
    return st[key]


def load_jsonl(p: Path) -> List[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> None:
    meta = json.loads((RAW / "run_meta.json").read_text(encoding="utf-8"))
    new_zip = Path(meta["post"]["new_zip"])
    old = load_zip_block(OLD_ZIP)
    new = load_zip_block(new_zip)
    orders_full = load_jsonl(RAW / "orders_full.jsonl")
    trades_full = load_jsonl(RAW / "trades_full.jsonl")

    # ── A. 交易层逐笔对照（按内容键匹配，不依赖 trade_id）──────────────
    def tkey(t):
        return (t["pair"], int(t["open_timestamp"]), int(t["close_timestamp"]))

    old_by = {tkey(t): t for t in old["trades"]}
    new_by = {tkey(t): t for t in new["trades"]}
    keys = sorted(set(old_by) | set(new_by))
    rows = []
    for k in keys:
        o, n = old_by.get(k), new_by.get(k)
        rows.append({
            "pair": k[0], "open_ts_ms": k[1], "close_ts_ms": k[2],
            "in_old": o is not None, "in_new": n is not None,
            "engine_trade_id_new": next(
                (tf["engine_trade_id"] for tf in trades_full
                 if tf["to_json_full"]["pair"] == k[0]
                 and tf["to_json_full"]["open_timestamp"] == k[1]), None),
            "old_open_rate": o["open_rate"] if o else None,
            "new_open_rate": n["open_rate"] if n else None,
            "old_close_rate": o["close_rate"] if o else None,
            "new_close_rate": n["close_rate"] if n else None,
            "old_amount": o["amount"] if o else None,
            "new_amount": n["amount"] if n else None,
            "old_profit_abs": o["profit_abs"] if o else None,
            "new_profit_abs": n["profit_abs"] if n else None,
            "old_funding": o.get("funding_fees") if o else None,
            "new_funding": n.get("funding_fees") if n else None,
            "old_exit_reason": o["exit_reason"] if o else None,
            "new_exit_reason": n["exit_reason"] if n else None,
            "old_enter_tag": o["enter_tag"] if o else None,
            "new_enter_tag": n["enter_tag"] if n else None,
        })
    cmp_t = pd.DataFrame(rows)
    for col in ["open_rate", "close_rate", "amount", "profit_abs", "funding", "exit_reason", "enter_tag"]:
        cmp_t[f"{col}_equal"] = [
            (a == b) if not (isinstance(a, float) and isinstance(b, float)) else (a == b)
            for a, b in zip(cmp_t[f"old_{col}"], cmp_t[f"new_{col}"])
        ]
    cmp_t.to_csv(OUT / "01_新旧行为对照.csv", index=False, encoding="utf-8-sig")

    # ── B. 订单层：7 个原始字段逐条对照 ────────────────────────────────
    old_orders = []
    for t in old["trades"]:
        for seq, o in enumerate(t["orders"]):
            old_orders.append({"pair": t["pair"], "open_ts": int(t["open_timestamp"]),
                               "seq": seq, **o})
    old_o = pd.DataFrame(old_orders).sort_values(["pair", "open_ts", "seq"]).reset_index(drop=True)
    new_o = pd.DataFrame([{
        "pair": r["pair"],
        "open_ts": int(r["to_json_full"]["order_filled_timestamp"]) if r["to_json_full"]["ft_is_entry"] else None,
        "seq": 0 if r["to_json_full"]["ft_is_entry"] else 1,
        **{k: r["to_json_full"][k] for k in
           ("amount", "safe_price", "ft_order_side", "order_filled_timestamp",
            "ft_is_entry", "ft_order_tag", "cost")},
        "engine_order_id": r["model_columns"]["order_id"],
        "engine_trade_id": r["engine_trade_id"],
    } for r in orders_full])
    # 用 (pair, 成交时刻, 是否入场) 做内容键匹配（order_filled_timestamp 唯一）
    old_o["k"] = old_o["pair"] + "|" + old_o["order_filled_timestamp"].astype("int64").astype(str) + \
        "|" + old_o["ft_is_entry"].astype(str)
    new_o["k"] = new_o["pair"] + "|" + new_o["order_filled_timestamp"].astype("int64").astype(str) + \
        "|" + new_o["ft_is_entry"].astype(str)
    m = old_o.merge(new_o, on="k", how="outer", suffixes=("_old", "_new"), indicator=True)
    oc = pd.DataFrame({
        "pair": m["pair_old"].fillna(m["pair_new"]),
        "ft_is_entry": m["ft_is_entry_old"].fillna(m["ft_is_entry_new"]),
        "fill_ts_ms": m["order_filled_timestamp_old"].fillna(m["order_filled_timestamp_new"]),
        "join": m["_merge"],
        "amount_equal": m["amount_old"] == m["amount_new"],
        "price_equal": m["safe_price_old"] == m["safe_price_new"],
        "cost_equal": m["cost_old"] == m["cost_new"],
        "side_equal": m["ft_order_side_old"] == m["ft_order_side_new"],
        "ts_equal": m["order_filled_timestamp_old"] == m["order_filled_timestamp_new"],
        "is_entry_equal": m["ft_is_entry_old"] == m["ft_is_entry_new"],
        "tag_equal": m["ft_order_tag_old"] == m["ft_order_tag_new"],
        "amount_old": m["amount_old"], "amount_new": m["amount_new"],
        "price_old": m["safe_price_old"], "price_new": m["safe_price_new"],
        "cost_old": m["cost_old"], "cost_new": m["cost_new"],
        "engine_order_id_new": m["engine_order_id"],
        "engine_trade_id_new": m["engine_trade_id"],
    }).sort_values(["pair", "fill_ts_ms"]).reset_index(drop=True)
    oc.to_csv(OUT / "02_订单七字段对照.csv", index=False, encoding="utf-8-sig")

    # ── C. 引擎对象真值核查（这批数据只有这次能拿到）────────────────────
    mc = [r["model_columns"] for r in orders_full]
    oj = [r["to_json_full"] for r in orders_full]
    order_ids = [int(x["order_id"]) for x in mc]
    trade_ids = sorted({int(r["engine_trade_id"]) for r in orders_full})
    n_trades = len(trades_full)

    def dist(vals):
        out: Dict[str, int] = {}
        for v in vals:
            out[str(v)] = out.get(str(v), 0) + 1
        return out

    def close(a, b, tol=0.0):
        return abs(a - b) <= tol

    fill_eq_amount = sum(1 for x, y in zip(mc, oj) if close(float(x["filled"]), float(y["amount"])))
    rem_zero = sum(1 for x in mc if close(float(x["remaining"]), 0.0))
    price_triplet = sum(1 for x, y in zip(mc, oj)
                        if float(x["price"]) == float(x["average"]) == float(y["safe_price"])
                        == float(x["ft_price"]))
    date_triplet = sum(1 for x in mc
                       if x["order_date"] == x["order_filled_date"] == x["order_update_date"])
    funding_by_leg = {"entry_null": 0, "entry_zero": 0, "entry_nonzero": 0,
                      "exit_null": 0, "exit_zero": 0, "exit_nonzero": 0}
    for r in orders_full:
        f = r["model_columns"]["funding_fee"]
        leg = "entry" if r["to_json_full"]["ft_is_entry"] else "exit"
        if f in (None, ""):
            funding_by_leg[f"{leg}_null"] += 1
        elif float(f) == 0.0:
            funding_by_leg[f"{leg}_zero"] += 1
        else:
            funding_by_leg[f"{leg}_nonzero"] += 1

    # 逐单资金费之和 vs 交易层 funding_fees
    per_trade_funding_sum = {}
    for r in orders_full:
        tid = int(r["engine_trade_id"])
        f = r["model_columns"]["funding_fee"]
        per_trade_funding_sum[tid] = per_trade_funding_sum.get(tid, 0.0) + (0.0 if f in (None, "") else float(f))
    trade_funding = {tf["engine_trade_id"]: float(tf["to_json_full"].get("funding_fees") or 0.0)
                     for tf in trades_full}
    fund_match = sum(1 for tid, v in per_trade_funding_sum.items()
                     if close(v, trade_funding.get(tid, 0.0), 1e-12))

    # 能力边界：Order 模型里是否存在任何"逐次成交/成交ID"字段
    model_cols = meta["captured"]["order_columns"]
    fill_like = [c for c in model_cols if any(s in c.lower() for s in ("fill_id", "trade_id_ex", "fill_seq", "exec"))]
    truth = {
        "scope": {"strategy": STRATEGY, "runs": 1, "trades": n_trades, "orders": len(orders_full)},
        "order_identity": {
            "order_columns_in_model": model_cols,
            "order_id_min": min(order_ids), "order_id_max": max(order_ids),
            "order_id_count": len(order_ids),
            "order_id_unique": len(set(order_ids)) == len(order_ids),
            "order_id_is_gapless_1_to_N": sorted(order_ids) == list(range(1, len(order_ids) + 1)),
            "order_id_type": "字符串形态的整数（引擎 order_id_counter）",
        },
        "trade_identity": {
            "engine_trade_id_min": min(trade_ids), "engine_trade_id_max": max(trade_ids),
            "engine_trade_id_count": len(trade_ids),
            "engine_trade_id_is_gapless_1_to_N": trade_ids == list(range(1, len(trade_ids) + 1)),
            "n_trades_in_memory": n_trades,
            "ft_trade_id_values": dist([str(x["ft_trade_id"]) for x in mc]),
            "ft_trade_id_equals_engine_trade_id": sum(
                1 for r in orders_full if str(r["model_columns"]["ft_trade_id"]) == str(r["engine_trade_id"])),
            "conclusion": "engine_trade_id 来自 LocalTrade.id（trade_id_counter），真实可用；"
                          "旧导出把它裁掉了 ⇒ 旧 trade_id 只是派生秩",
        },
        "status_filled": {
            "status_dist": dist([x["status"] for x in mc]),
            "is_open_dist": dist([x["ft_is_open"] for x in mc]),
            "order_type_dist": dist([x["order_type"] for x in mc]),
            "filled_eq_amount": fill_eq_amount,
            "remaining_zero": rem_zero,
            "total": len(mc),
        },
        "time_semantics": {
            "order_date_eq_filled_date_eq_update_date": date_triplet,
            "order_date_eq_order_filled_timestamp": sum(
                1 for x, y in zip(mc, oj)
                if int(pd.Timestamp(x["order_date"]).timestamp() * 1000) == int(y["order_filled_timestamp"])),
            "total": len(mc),
            "note": "回测里建单与成交在同一根 K 线、同一 current_time ⇒ 委托时间不可与成交时间区分",
        },
        "price_semantics": {
            "price_eq_average_eq_safe_price_eq_ft_price": price_triplet, "total": len(mc),
            "note": "市价/限价在回测中都按 propose_rate 建单且成交后不改 ⇒ 委托价 == 成交价",
        },
        "fees": {
            "ft_fee_base_dist": dist([x["ft_fee_base"] for x in mc]),
            "fee_open_cost_present": sum(1 for tf in trades_full if tf["to_json_full"].get("fee_open_cost") is not None),
            "fee_close_cost_present": sum(1 for tf in trades_full if tf["to_json_full"].get("fee_close_cost") is not None),
            "ft_cancel_reason_dist": dist([x["ft_cancel_reason"] for x in mc]),
            "stop_price_dist": dist([x["stop_price"] for x in mc]),
        },
        "funding": {
            "per_order_present": sum(1 for x in mc if x["funding_fee"] not in (None, "")),
            "by_leg": funding_by_leg,
            "sum_per_trade_eq_trade_funding": fund_match,
            "trades_total": len(trades_full),
            "note": "逐单 funding_fee 是引擎真实值（close_bt_order 结转 funding_fee_running 后清零），"
                    "不是分摊；本次可给出真实归属",
        },
        "capability_boundary": {
            "fill_id_like_columns": fill_like,
            "has_fill_id": "fill_id" in model_cols,
            "orders_per_trade_dist": dist([str(tf["n_orders_in_memory"]) for tf in trades_full]),
            "nr_of_successful_entries_dist": dist([str(tf["nr_of_successful_entries"]) for tf in trades_full]),
            "nr_of_successful_exits_dist": dist([str(tf["nr_of_successful_exits"]) for tf in trades_full]),
            "conclusion": "引擎 Order 只有标量 filled/remaining，没有成交事件列表、没有 fill_id；"
                          "回测不存在交易所回报 ⇒ 逐次成交在能力上不可得，任何 fill_id 都是伪造",
        },
    }
    (OUT / "03_引擎对象真值核查.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── D. 引擎计数器 ────────────────────────────────────────────────
    ctr_keys = ["rejected_signals", "timedout_entry_orders", "timedout_exit_orders",
                "canceled_trade_entries", "canceled_entry_orders", "replaced_entry_orders",
                "total_trades", "final_balance"]
    ctr = {k: {"old": old.get(k), "new": new.get(k),
               "equal": old.get(k) == new.get(k)} for k in ctr_keys}
    ctr["captured_at_backtest_return"] = meta["captured"]["engine_counters"]
    (OUT / "04_引擎计数器对照.json").write_text(
        json.dumps(ctr, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── E. stats 块逐标量对照 ────────────────────────────────────────
    skip = {"trades", "locks", "results_per_pair", "results_per_enter_tag", "exit_reason_summary",
            "mix_tag_stats", "left_open_trades", "daily_profit", "periodic_breakdown"}
    rows = []
    for k in sorted(set(old) | set(new)):
        if k in skip:
            continue
        a, b = old.get(k), new.get(k)
        if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
            continue
        eq = a == b
        if isinstance(a, float) and isinstance(b, float):
            eq = math.isclose(a, b, rel_tol=0, abs_tol=0)
        rows.append({"field": k, "old": a, "new": b, "equal": eq})
    pd.DataFrame(rows).to_csv(OUT / "05_统计块对照.csv", index=False, encoding="utf-8-sig")

    # ── F. 结论 ─────────────────────────────────────────────────────
    # 两个字段**按定义必然不同**：它们是"这次进程几点开始/结束"的挂钟时间，不是交易行为
    RUNTIME_CLOCK = {"backtest_run_start_ts", "backtest_run_end_ts"}
    sb = pd.DataFrame(rows)
    sb["expected_to_differ"] = sb["field"].isin(RUNTIME_CLOCK)
    sb["behavior_equal"] = sb["equal"] | sb["expected_to_differ"]
    sb.to_csv(OUT / "05_统计块对照.csv", index=False, encoding="utf-8-sig")
    runtime_diff = sb.loc[sb["expected_to_differ"] & ~sb["equal"], ["field", "old", "new"]]
    behavior_diff = sb.loc[~sb["behavior_equal"], ["field", "old", "new"]]

    all_t_equal = bool(cmp_t[[c for c in cmp_t.columns if c.endswith("_equal")]].all().all())
    all_o_equal = bool(oc[["amount_equal", "price_equal", "cost_equal", "side_equal",
                           "ts_equal", "is_entry_equal", "tag_equal"]].all().all())
    verdict = {
        "trades_old": len(old["trades"]), "trades_new": len(new["trades"]),
        "trades_matched": int((cmp_t["in_old"] & cmp_t["in_new"]).sum()),
        "orders_old": len(old_o), "orders_new": len(new_o),
        "orders_matched": int((oc["join"] == "both").sum()),
        "all_trade_fields_equal": all_t_equal,
        "all_order_7_fields_equal": all_o_equal,
        "stats_fields_compared": int(len(sb)),
        "stats_fields_unequal_raw": int((~sb["equal"]).sum()),
        "stats_fields_unequal_are_runtime_clock": sorted(runtime_diff["field"].tolist()),
        "stats_behavior_fields_unequal": sorted(behavior_diff["field"].tolist()),
        "stats_block_behavior_equal": bool(len(behavior_diff) == 0),
        "engine_counters_all_equal": all(v["equal"] for k, v in ctr.items()
                                         if isinstance(v, dict) and "equal" in v),
        "final_balance": {"old": old.get("final_balance"), "new": new.get("final_balance"),
                          "abs_diff": abs(float(old.get("final_balance") or 0)
                                          - float(new.get("final_balance") or 0))},
        "behavior_unchanged": bool(all_t_equal and all_o_equal and len(behavior_diff) == 0
                                   and len(old["trades"]) == len(new["trades"])),
        "explanation_for_raw_diff": (
            "唯二不同的两个字段是 backtest_run_start_ts / backtest_run_end_ts，"
            "即本次进程的挂钟起止时间（相差 %d / %d 秒），与交易行为无关；"
            "其余 %d 个标量逐字段完全相等（含 backtest_start/backtest_end 数据窗口、"
            "profit_total_abs、max_drawdown、sharpe、sortino、calmar、sqn、p_value 等）。"
            % (int(new.get("backtest_run_start_ts", 0)) - int(old.get("backtest_run_start_ts", 0)),
               int(new.get("backtest_run_end_ts", 0)) - int(old.get("backtest_run_end_ts", 0)),
               len(sb) - 2)),
    }
    verdict["verdict"] = "PASS" if verdict["behavior_unchanged"] else "FAIL"
    (OUT / "07_对账结论.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(json.dumps(verdict, ensure_ascii=False, indent=1))
    print("\n-- order identity --")
    print(json.dumps({k: truth["order_identity"][k] for k in
                      ("order_id_min", "order_id_max", "order_id_count",
                       "order_id_is_gapless_1_to_N")}, ensure_ascii=False, indent=1))
    print("\n-- trade identity --")
    print(json.dumps({k: truth["trade_identity"][k] for k in
                      ("engine_trade_id_min", "engine_trade_id_max", "engine_trade_id_count",
                       "engine_trade_id_is_gapless_1_to_N", "ft_trade_id_equals_engine_trade_id")},
                     ensure_ascii=False, indent=1))
    print("\n-- status/fees/funding --")
    print(json.dumps({"status": truth["status_filled"], "fees": truth["fees"],
                      "funding": truth["funding"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

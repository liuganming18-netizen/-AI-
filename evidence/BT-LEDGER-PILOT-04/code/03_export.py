# -*- coding: utf-8 -*-
"""BT-LEDGER-PILOT-04｜生成可导出的订单/成交/交易汇总 + 真实关联核对 + K线/止损/切档对应。

输入只有本次试点的进程内捕获（out/00_engine_raw/），以及**只读**的上游 BT-01 复盘包与轨迹。
不改任何上游文件。

产出
────
out/06_K线止损切档对应.csv   21 笔 → replay 行号 / K线根数 / 止损轨迹行数 / 切档事件数
out/08_订单_规范导出.csv     42 单 × 全字段（含真实 engine_order_id / status / filled / funding_fee）
out/09_成交_规范导出.csv     42 行，显式 order_level_full_fill（不是独立成交事件）
out/10_交易汇总.csv          21 笔
out/11_关联核对.json         订单↔交易↔K线↔止损轨迹 的真实关联一致性
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Dict, List

import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BT01 = BASE / "evidence/WORK-DYNAMIC-BT-01"
ME = BASE / "evidence/BT-LEDGER-PILOT-04"
RAW = ME / "out/00_engine_raw"
OUT = ME / "out"

STRATEGY = "Wyckoff_C1_S05_D05_Vs04_Z02"
REPLAY = BT01 / "replay" / f"{STRATEGY}.jsonl.gz"
TRACE = BT01 / "out/stage_trace_unified" / f"stage_trace_{STRATEGY}.csv"


def load_jsonl(p: Path) -> List[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def utc(ms) -> str:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    meta = json.loads((RAW / "run_meta.json").read_text(encoding="utf-8"))
    orders_full = load_jsonl(RAW / "orders_full.jsonl")
    trades_full = load_jsonl(RAW / "trades_full.jsonl")

    # ── 1. 订单规范导出（全部字段，来源逐列标注）────────────────────────
    orows = []
    for r in orders_full:
        j, mc, sp = r["to_json_full"], r["model_columns"], r["safe_props"]
        qty, price, cost = float(j["amount"]), float(j["safe_price"]), float(j["cost"])
        fee = cost - qty * price
        fee_rate = (fee / (qty * price)) if qty * price else None
        orows.append({
            # 身份
            "run_id": "run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02",
            "strategy": STRATEGY,
            "pair": r["pair"],
            "symbol": r["pair"].split("/")[0],
            "engine_trade_id": int(r["engine_trade_id"]),          # 真实（LocalTrade.id）
            "engine_order_id": str(mc["order_id"]),                # 真实（order_id_counter）
            "order_id_kind": "engine_internal_counter_string",     # 明确：不是交易所ID
            "internal_order_key": f"{r['pair']}|{j['order_filled_timestamp']}|{'E' if j['ft_is_entry'] else 'X'}",
            "leg": "entry" if j["ft_is_entry"] else "exit",
            "order_seq_in_trade": 0 if j["ft_is_entry"] else 1,
            # 引擎真实字段
            "ft_order_side": j["ft_order_side"],
            "ft_is_entry": j["ft_is_entry"],
            "ft_order_tag": j["ft_order_tag"],
            "amount": qty,
            "safe_price": price,
            "cost": cost,
            "status": mc["status"],
            "filled": mc["filled"],
            "remaining": mc["remaining"],
            "is_open": mc["ft_is_open"],
            "order_type": mc["order_type"],
            "price": mc["price"],
            "average": mc["average"],
            "ft_price": mc["ft_price"],
            "ft_amount": mc["ft_amount"],
            "order_date_utc": mc["order_date"],
            "order_filled_date_utc": mc["order_filled_date"],
            "order_update_date_utc": mc["order_update_date"],
            "order_filled_timestamp": int(j["order_filled_timestamp"]),
            "order_filled_utc": utc(j["order_filled_timestamp"]),
            "ft_fee_base": mc["ft_fee_base"],
            "ft_cancel_reason": mc["ft_cancel_reason"],
            "stop_price": mc["stop_price"],
            "funding_fee": mc["funding_fee"],
            # 派生（算出来的，不是原始）
            "fee_amount_derived": fee,
            "fee_rate_derived": fee_rate,
            "gross_notional_derived": qty * price,
            "stake_amount_filled_derived": sp["stake_amount_filled"],
            # 回测不存在
            "exchange_order_id": None,
            "fill_id": None,
            "fill_id_status": "nonexistent_in_backtest",
            # 关联
            "trade_open_timestamp": r["trade_open_timestamp"],
            "trade_close_timestamp": r["trade_close_timestamp"],
        })
    o = pd.DataFrame(orows).sort_values(["engine_trade_id", "order_seq_in_trade"])
    o.to_csv(OUT / "08_订单_规范导出.csv", index=False, encoding="utf-8-sig")

    # ── 2. 成交规范导出（订单级 1:1 投影，不伪造逐次）──────────────────
    frows = []
    for r in orders_full:
        j, mc = r["to_json_full"], r["model_columns"]
        frows.append({
            "engine_trade_id": int(r["engine_trade_id"]),
            "engine_order_id": str(mc["order_id"]),
            "pair": r["pair"],
            "leg": "entry" if j["ft_is_entry"] else "exit",
            "record_kind": "order_level_full_fill",
            "fill_seq_in_order": 1,
            "is_partial": False,
            "fill_qty": float(mc["filled"]),
            "fill_price": float(j["safe_price"]),
            "fill_cost": float(j["cost"]),
            "fill_ts_ms": int(j["order_filled_timestamp"]),
            "fill_utc": utc(j["order_filled_timestamp"]),
            "remaining_after": float(mc["remaining"]),
            "status": mc["status"],
            "fill_id": None,
            "fill_id_status": "nonexistent_in_backtest",
            "note": "引擎 Order 只有标量 filled/remaining，无成交事件列表 ⇒ 本行是订单级成交投影，非独立成交事件",
        })
    pd.DataFrame(frows).to_csv(OUT / "09_成交_规范导出.csv", index=False, encoding="utf-8-sig")

    # ── 3. 交易汇总 ──────────────────────────────────────────────────
    trows = []
    for tf in trades_full:
        j = tf["to_json_full"]
        trows.append({
            "engine_trade_id": tf["engine_trade_id"],
            "pair": j["pair"],
            "enter_tag": j["enter_tag"],
            "exit_reason": j["exit_reason"],
            "open_timestamp": j["open_timestamp"], "open_utc": utc(j["open_timestamp"]),
            "close_timestamp": j["close_timestamp"], "close_utc": utc(j["close_timestamp"]),
            "open_rate": j["open_rate"], "close_rate": j["close_rate"],
            "amount": j["amount"],
            "stake_amount": j["stake_amount"], "max_stake_amount": j["max_stake_amount"],
            "fee_open": j["fee_open"], "fee_close": j["fee_close"],
            "fee_open_cost": j.get("fee_open_cost"), "fee_close_cost": j.get("fee_close_cost"),
            "funding_fees": j.get("funding_fees"),
            "profit_abs": j["profit_abs"], "profit_ratio": j["profit_ratio"],
            "exit_order_status": j.get("exit_order_status"),
            "nr_of_successful_entries": tf["nr_of_successful_entries"],
            "nr_of_successful_exits": tf["nr_of_successful_exits"],
            "has_open_orders": tf["has_open_orders"],
            "n_orders_in_memory": tf["n_orders_in_memory"],
            "initial_stop_loss_abs": j.get("initial_stop_loss_abs"),
            "stop_loss_abs": j.get("stop_loss_abs"),
            "stoploss_last_update_timestamp": j.get("stoploss_last_update_timestamp"),
            "min_rate": j.get("min_rate"), "max_rate": j.get("max_rate"),
            "is_open": j.get("is_open"), "is_short": j.get("is_short"),
            "leverage": j.get("leverage"),
        })
    t = pd.DataFrame(trows).sort_values("engine_trade_id")
    t.to_csv(OUT / "10_交易汇总.csv", index=False, encoding="utf-8-sig")

    # ── 4. K线 / 止损轨迹 / 切档 对应（内容键匹配，不依赖 trade_id）────
    replay = [json.loads(x) for x in gzip.open(REPLAY, "rt", encoding="utf-8")]
    rep_by = {(r["pair"], int(r["entry"]["t"]), int(r["exit"]["t"])): r for r in replay}
    trace = pd.read_csv(TRACE)
    trace_entry = trace[trace["event"] == "ENTRY"].set_index("pair")

    krows = []
    for tf in trades_full:
        j = tf["to_json_full"]
        k = (j["pair"], int(j["open_timestamp"]), int(j["close_timestamp"]))
        rp = rep_by.get(k)
        tr = trace[trace["pair"] == j["pair"]]
        tr_one = tr[tr["trade_id"] == (rp["trade_id"] if rp else -1)]
        krows.append({
            "engine_trade_id": tf["engine_trade_id"],
            "pair": j["pair"],
            "bt01_trade_id_in_replay": rp["trade_id"] if rp else None,
            "trade_id_match_engine": (rp["trade_id"] == tf["engine_trade_id"]) if rp else None,
            "replay_file": str(REPLAY),
            "replay_line": replay.index(rp) + 1 if rp else None,
            "bars_4h": len(rp["timeframes"]["4h"]) if rp else None,
            "bars_1h": len(rp["timeframes"]["1h"]) if rp else None,
            "bars_15m": len(rp["timeframes"]["15m"]) if rp else None,
            "replay_entry_ts": rp["entry"]["t"] if rp else None,
            "replay_entry_price": rp["entry"]["price"] if rp else None,
            "replay_exit_ts": rp["exit"]["t"] if rp else None,
            "replay_exit_price": rp["exit"]["price"] if rp else None,
            "entry_ts_match": (int(rp["entry"]["t"]) == int(j["open_timestamp"])) if rp else None,
            "exit_ts_match": (int(rp["exit"]["t"]) == int(j["close_timestamp"])) if rp else None,
            "entry_price_match": (float(rp["entry"]["price"]) == float(j["open_rate"])) if rp else None,
            "exit_price_match": (float(rp["exit"]["price"]) == float(j["close_rate"])) if rp else None,
            "stop_trace_rows": len(tr_one),
            "stop_trace_has_entry_event": bool((tr_one["event"] == "ENTRY").any()),
            "stage_events_in_replay": len(rp["stage_events"]) if rp else None,
            "stop_trace_final_effective_stop": (float(tr_one["effective_stop"].iloc[-1])
                                                if len(tr_one) else None),
            "trade_stop_loss_abs": j.get("stop_loss_abs"),
        })
    kd = pd.DataFrame(krows).sort_values("engine_trade_id")
    kd.to_csv(OUT / "06_K线止损切档对应.csv", index=False, encoding="utf-8-sig")

    # ── 5. 关联核对 ──────────────────────────────────────────────────
    # 订单 ↔ 交易
    per_trade_orders = o.groupby("engine_trade_id").size().to_dict()
    order_cnt_ok = sum(1 for tf in trades_full
                       if per_trade_orders.get(tf["engine_trade_id"], 0) == 2
                       == (tf["nr_of_successful_entries"] + tf["nr_of_successful_exits"]))
    # 资金费：逐单之和 == 交易层总额
    fund_sum = o.groupby("engine_trade_id")["funding_fee"].apply(
        lambda s: float(pd.to_numeric(s, errors="coerce").fillna(0.0).sum())).to_dict()
    fund_ok = sum(1 for tf in trades_full
                  if abs(fund_sum.get(tf["engine_trade_id"], 0.0)
                         - float(tf["to_json_full"].get("funding_fees") or 0.0)) <= 1e-12)
    # 费用：cost 公式
    cost_dev = float((o["cost"] - o["amount"] * o["safe_price"] * (1 + o["fee_rate_derived"].fillna(0))).abs().max())
    # 盈亏重建
    ent = o[o["leg"] == "entry"].set_index("engine_trade_id")
    exi = o[o["leg"] == "exit"].set_index("engine_trade_id")
    devs = []
    for tf in trades_full:
        tid = tf["engine_trade_id"]
        j = tf["to_json_full"]
        fee_e = float(ent.loc[tid, "fee_amount_derived"])
        fee_x = float(exi.loc[tid, "fee_amount_derived"])
        fund = float(j.get("funding_fees") or 0.0)
        pnl = float(j["amount"]) * (float(j["close_rate"]) - float(j["open_rate"])) - fee_e - fee_x + fund
        devs.append(abs(pnl - float(j["profit_abs"])))
    # 时间因果
    causal_ok = sum(1 for tf in trades_full
                    if int(tf["to_json_full"]["open_timestamp"]) <= int(tf["to_json_full"]["close_timestamp"]))
    # 费用双路交叉：order.cost−amount×price  vs  trade.fee_open_cost（= fee_open×max_stake_amount）
    fee_route_dev = []
    for tf in trades_full:
        tid = tf["engine_trade_id"]
        j = tf["to_json_full"]
        a = float(ent.loc[tid, "fee_amount_derived"])
        b = float(j.get("fee_open_cost") or 0.0)
        fee_route_dev.append(abs(a - b))
    # 出场费是否存在于任何交易层字段（应为"不存在"，只能推导）
    exit_fee_available = int(t["fee_close_cost"].notna().sum())
    # order_id 与成交时刻的单调性（计数器在"建单"时自增）
    mono = o.sort_values("order_filled_timestamp")
    order_id_monotonic = bool(mono["engine_order_id"].astype(int).is_monotonic_increasing)
    rel = {
        "orders": int(len(o)), "trades": int(len(t)), "runs": 1,
        "order_trade_link": {"checked": len(trades_full), "ok": int(order_cnt_ok),
                             "rule": "每笔 2 单，且 == nr_of_successful_entries + nr_of_successful_exits"},
        "funding_per_order_sum_eq_trade_total": {"checked": len(trades_full), "ok": int(fund_ok),
                                                 "tol": 1e-12,
                                                 "rule": "Σ(逐单 funding_fee) == trade.funding_fees"},
        "cost_formula_max_abs_dev": cost_dev,
        "pnl_rebuild_max_abs_dev": float(max(devs)),
        "pnl_tolerance": 1e-8,
        "pnl_ok": int(sum(1 for d in devs if d <= 1e-8)),
        "causality_ok": int(causal_ok),
        "kline_link": {"checked": int(len(kd)), "replay_found": int(kd["replay_line"].notna().sum()),
                       "entry_ts_match": int(kd["entry_ts_match"].fillna(False).sum()),
                       "exit_ts_match": int(kd["exit_ts_match"].fillna(False).sum()),
                       "entry_price_match": int(kd["entry_price_match"].fillna(False).sum()),
                       "exit_price_match": int(kd["exit_price_match"].fillna(False).sum()),
                       "trace_rows_total": int(kd["stop_trace_rows"].sum()),
                       "trade_id_match_engine": int(kd["trade_id_match_engine"].fillna(False).sum())},
        "order_id": {"count": int(len(o)), "unique": int(o["engine_order_id"].nunique()),
                     "gapless_1_to_N": sorted(o["engine_order_id"].astype(int).tolist())
                     == list(range(1, len(o) + 1)),
                     "monotonic_with_fill_ts": order_id_monotonic,
                     "note": "order_id 是建单时自增的计数器；回测建单即成交 ⇒ 与成交时刻同序"},
        "fee_cross_check": {
            "route_a_order_cost_minus_notional_maxdev": float(max(fee_route_dev)),
            "route_b_trade_fee_open_cost_available": int(t["fee_open_cost"].notna().sum()),
            "entry_fee_two_routes_agree": int(sum(1 for d in fee_route_dev if d <= 1e-12)),
            "exit_fee_cost_available_in_trade_layer": exit_fee_available,
            "note": "出场手续费在交易层**没有**字段（fee_close_cost 恒 None，回测不写该列），"
                    "只能用 order.cost − amount×price 推导",
        },
        "verdict": None,
    }
    rel["verdict"] = ("PASS" if (order_cnt_ok == len(trades_full) and fund_ok == len(trades_full)
                                 and cost_dev <= 1e-12 and rel["pnl_ok"] == len(trades_full)
                                 and causal_ok == len(trades_full)
                                 and rel["kline_link"]["replay_found"] == len(kd))
                      else "FAIL")
    (OUT / "11_关联核对.json").write_text(json.dumps(rel, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(json.dumps(rel, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

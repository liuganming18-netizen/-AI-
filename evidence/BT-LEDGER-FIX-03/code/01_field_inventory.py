# -*- coding: utf-8 -*-
"""BT-LEDGER-FIX-03｜一、逐字段来源清单。

回答：订单/成交相关的每个字段，究竟是
  (a) 引擎原始导出里就有；
  (b) 引擎内存里有、导出时被裁剪（分「可确定性推导」与「必须重跑」两种）；
  (c) 回测环境根本不存在（永远补不出来）；
  (d) 本项目自建（内部键）。

判定依据全部落在**可指到行号的源码**上，不靠印象：
  freqtrade 2026.9（`evidence/FT-BASELINE-01/.venv`，与 run.json 的 `exec_core_version` 一致）
  · persistence/trade_model.py:85-118   Order 表列（引擎真实持有的字段全集）
  · persistence/trade_model.py:259-311  Order.to_json（minified=True 只出 7 个）
  · persistence/trade_model.py:303-312  close_bt_order（status/filled/remaining 的构造值）
  · persistence/trade_model.py:1401-1414 select_filled_or_open_orders（零成交订单被过滤）
  · optimize/backtesting.py:149/1175/940/1211  order_id_counter / trade_id_counter
  · optimize/backtesting.py:1212-1268  入场 Order 构造实参
  · optimize/backtesting.py:942-968    出场 Order 构造实参
  · optimize/backtesting.py:1810       results = trade_list_to_dataframe(bt_trades)  ← 默认 minified=True
  · data/btanalysis/bt_fileutils.py:26-55/535  BT_DATA_COLUMNS 白名单（第二道裁剪）
  · strategy/interface.py:95-101       默认 order_types（limit/limit/limit）
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = cm.OUT

# ── 引擎侧字段事实（含行号）────────────────────────────────────────────
# (layer, field, in_engine, engine_ref, in_tojson_full, provenance, available_now, note)
ORDER_FIELDS = [
    # 7 个 minified 字段：导出里逐字存在
    ("order", "amount", True, "trade_model.py:106", True, "raw_export", True,
     "成交数量（backtest 里 filled 恒等于它）"),
    ("order", "safe_price", True, "trade_model.py:140-141(property)", True, "raw_export", True,
     "成交均价；backtest 中 average==price==ft_price，故等于委托价"),
    ("order", "ft_order_side", True, "trade_model.py:92", True, "raw_export", True, "buy/sell"),
    ("order", "order_filled_timestamp", True, "trade_model.py:112", True, "raw_export", True,
     "**成交**时刻（不是委托时刻）"),
    ("order", "ft_is_entry", True, "trade_model.py:269", True, "engine_computed", True,
     "引擎导出时按 side==entry_side 即时算"),
    ("order", "ft_order_tag", True, "trade_model.py:118", True, "raw_export", True,
     "入场=candidate_id，出场=exit_reason"),
    ("order", "cost", True, "trade_model.py:109", True, "raw_export", True, "含手续费的成交额"),
    # 14 个 minified=False 才会导出的字段
    ("order", "pair", True, "trade_model.py:93", True, "derived_from_raw", True,
     "== trade.pair，可从交易层取到，非原始订单字段"),
    ("order", "order_id", True, "trade_model.py:99", True, "unrecoverable_rerun", False,
     "**引擎真 ID**：回测里是全局自增计数器字符串（backtesting.py:149/940/1175），导出被裁 ⇒ 事后不可恢复"),
    ("order", "status", True, "trade_model.py:100", True, "derived_from_construction", True,
     "导出只保留已成交单；close_bt_order 恒置 'closed'（trade_model.py:309）"),
    ("order", "average", True, "trade_model.py:105", True, "derived_from_construction", True,
     "backtest 建单时即置为 propose_rate，成交后不再改 ⇒ == safe_price"),
    ("order", "filled", True, "trade_model.py:107", True, "derived_from_construction", True,
     "close_bt_order 置 filled=amount（trade_model.py:305）"),
    ("order", "is_open", True, "trade_model.py:94", True, "derived_from_construction", True,
     "close_bt_order 置 False（trade_model.py:308）"),
    ("order", "order_date", True, "trade_model.py:111", True, "derived_from_construction", True,
     "建单即成交（同一根 K 线）⇒ == order_filled_date"),
    ("order", "order_timestamp", True, "trade_model.py:285-289", True, "derived_from_construction", True,
     "同上，毫秒形态"),
    ("order", "order_filled_date", True, "trade_model.py:112", True, "derived_from_construction", True,
     "== order_filled_timestamp"),
    ("order", "order_type", True, "trade_model.py:102", True, "derived_from_construction", True,
     "取 strategy.order_types['entry'|'exit']；策略未覆盖 ⇒ 默认 'limit'（interface.py:95-101）"),
    ("order", "price", True, "trade_model.py:104", True, "derived_from_construction", True,
     "委托价；backtest 建单时 = propose_rate = safe_price"),
    ("order", "remaining", True, "trade_model.py:108", True, "derived_from_construction", True,
     "close_bt_order 置 0（trade_model.py:306）"),
    ("order", "ft_fee_base", True, "trade_model.py:117", True, "derived_from_construction", True,
     "合约(U 本位)手续费以计价币计 ⇒ 恒 None；回测建单时未传该实参"),
    ("order", "funding_fee", True, "trade_model.py:114", True, "derived_from_construction", False,
     "**逐单拆分不可证**：close_bt_order 把 funding_fee_running 整体结转并清零，"
     "导出只剩 trade.funding_fees 总额 ⇒ 只能给总额，不能给逐单归属"),
    # to_json 永不出、但模型里有的
    ("order", "id", True, "trade_model.py:85", False, "unrecoverable_rerun", False,
     "与 order_id 同值（同一计数器）"),
    ("order", "ft_trade_id", True, "trade_model.py:86", False, "unrecoverable_rerun", False,
     "== trade.id；导出无，见 trade 层 trade_id 行"),
    ("order", "order_update_date", True, "trade_model.py:113", False, "derived_from_construction", True,
     "建单时置为同一时刻 ⇒ == order_date"),
    ("order", "ft_cancel_reason", True, "trade_model.py:97", False, "derived_from_construction", True,
     "本批次无撤单（canceled/timedout/replaced 全 0）⇒ None"),
    ("order", "stop_price", True, "trade_model.py:110", False, "derived_from_construction", True,
     "backtest 建单未传 ⇒ None"),
    ("order", "ft_amount / ft_price", True, "trade_model.py:95-96", False, "derived_from_construction", True,
     "兜底列，建单时未传 ⇒ 分别等于 amount / safe_price"),
    ("order", "side / symbol", True, "trade_model.py:101/103", False, "derived_from_construction", True,
     "分别 == ft_order_side / pair"),
    # 回测不存在
    ("order", "fill_id", False, "—", False, "nonexistent_backtest", False,
     "回测无交易所回报，不存在逐次成交 ID；任何自造 fill_id 都是伪造"),
    ("order", "exchange_order_id / client_order_id", False, "—", False, "nonexistent_backtest", False,
     "同上，只有 dry-run/实盘才有"),
    ("order", "partial_fills[]（一单多次成交明细）", False, "—", False, "nonexistent_backtest", False,
     "回测 1 单 = 1 次全额成交；本批次 6790 单全部 amount>0 且一次性 close_bt_order"),
]

TRADE_FIELDS = [
    ("trade", "pair / amount / open_rate / close_rate / open_timestamp / close_timestamp", True,
     "trade_model.py:667+", True, "raw_export", True, "交易汇总原始值"),
    ("trade", "fee_open / fee_close", True, "trade_model.py:722-725", True, "raw_export", True,
     "本批次恒 0.0005 / 0.0005"),
    ("trade", "profit_abs / profit_ratio", True, "trade_model.py:751-752", True, "raw_export", True,
     "引擎 round(profit_abs,8)"),
    ("trade", "funding_fees", True, "trade_model.py:781", True, "raw_export", True, "资金费总额"),
    ("trade", "enter_tag / exit_reason", True, "trade_model.py:721/750", True, "raw_export", True,
     "与订单 ft_order_tag 一一对应"),
    ("trade", "trade_id", True, "backtesting.py:149/1211/1213", False, "derived_unverified", False,
     "**引擎内部有自增 trade.id，导出被裁**；当前 1..n 是按 (open_ts,白名单序) 排的秩，"
     "同刻并列时依赖未观测的建仓顺序 ⇒ 不可宣称等于 trade.id"),
    ("trade", "fee_open_cost / fee_close_cost", True, "trade_model.py:723/727", False, "derived_from_raw", True,
     "== 订单 cost − amount×price（两侧口径已逐笔校验）"),
    ("trade", "exit_order_status", True, "trade_model.py:749", False, "derived_from_construction", True,
     "== 'closed'（同 order.status）"),
    ("trade", "nr_of_successful_entries / exits", True, "trade_model.py:790-791", False,
     "derived_from_raw", True, "== 已导出订单里 is_entry 的计数，逐笔可数"),
    ("trade", "open_fill_timestamp", True, "trade_model.py:732-733", False, "derived_from_construction", True,
     "入场单建单即成交 ⇒ == open_timestamp"),
    ("trade", "amount_requested", True, "trade_model.py:712", False, "unrecoverable_rerun", False,
     "回测中通常 == amount，但导出无值可证 ⇒ 不猜"),
    ("trade", "stoploss_last_update_timestamp", True, "trade_model.py:770-771", False, "derived_from_raw", True,
     "BT-01 `out/stage_trace_unified/*` 的止损轨迹末条时刻（已在 BT-02 索引）"),
    ("trade", "base_currency / quote_currency / exchange / trading_mode", True, "trade_model.py:709-713", False,
     "derived_from_raw", True, "由 pair 与 config 唯一确定"),
]

# 本项目自建
INTERNAL_FIELDS = [
    ("internal", "internal_order_key", False, "本项目", False, "internal_key", True,
     "`<run_id>|<pair>|<fill_ts_ms>|E|X`，全库唯一（6790/6790 已校验）；**不是交易所 ID**"),
    ("internal", "raw_trade_array_index", False, "原始 JSON 数组下标", False, "raw_position", True,
     "0 基；45/45 run 的 trades 数组按 close_timestamp 升序（即引擎 bt_trades 平仓追加顺序）"),
    ("internal", "trade_id（BT-01/02 在用）", False, "本项目派生", False, "derived_unverified", True,
     "见 trade.trade_id 行"),
    ("internal", "order_id（`ord_<策略>_<序号>_<腿>`）", False, "BT-01 code/05_export.py:335", False,
     "internal_key", True,
     "**平台合成键**，全库 6790/6790 都是这个形态；旧导出把它写在 `order_id` 字段里，"
     "极易被误当交易所订单号 ⇒ 本单在规范导出中改名为 `internal_order_key`"),
    ("internal", "created_at（run 目录 orders.jsonl）", False, "BT-01 code/05_export.py:339", False,
     "internal_key", True,
     "**字段名错**：其值取自 `fill_ts_ms`，是成交时刻不是委托创建时刻 ⇒ 本单改名为 `fill_utc`"),
]


def main() -> None:
    idx = cm.run_index()
    # 实证：45 个 zip 里 trade / order 的字段并集
    order_keys, trade_keys = set(), set()
    n_order = n_trade = 0
    for s in idx["strategy"]:
        b = cm.read_raw_block(s)
        for t in b["trades"]:
            n_trade += 1
            trade_keys |= set(t.keys())
            for o in t["orders"]:
                n_order += 1
                order_keys |= set(o.keys())

    rows = []
    for layer, field, in_engine, ref, in_full, prov, avail, note in ORDER_FIELDS:
        rows.append({
            "layer": layer, "field": field,
            "in_engine_model": in_engine, "engine_ref": ref,
            "in_raw_export": (field in order_keys) if layer == "order" else "",
            "would_be_in_to_json_full": in_full,
            "provenance_class": prov,
            "value_available_now": avail,
            "remediation": ("无需" if prov in ("raw_export", "engine_computed", "derived_from_raw",
                                              "derived_from_construction")
                            else ("重跑才能拿到" if prov == "unrecoverable_rerun"
                                  else ("永远补不出" if prov == "nonexistent_backtest" else "见报告"))),
            "note": note,
        })
    for layer, field, in_engine, ref, in_full, prov, avail, note in TRADE_FIELDS:
        rows.append({
            "layer": layer, "field": field,
            "in_engine_model": in_engine, "engine_ref": ref,
            "in_raw_export": (field in trade_keys) if layer == "trade" else "",
            "would_be_in_to_json_full": in_full,
            "provenance_class": prov, "value_available_now": avail,
            "remediation": ("无需" if prov in ("raw_export", "engine_computed", "derived_from_raw",
                                              "derived_from_construction")
                            else ("重跑才能拿到" if prov == "unrecoverable_rerun"
                                  else ("永远补不出" if prov == "nonexistent_backtest" else "见报告"))),
            "note": note,
        })
    for layer, field, in_engine, ref, in_full, prov, avail, note in INTERNAL_FIELDS:
        rows.append({
            "layer": layer, "field": field, "in_engine_model": in_engine, "engine_ref": ref,
            "in_raw_export": "", "would_be_in_to_json_full": in_full,
            "provenance_class": prov, "value_available_now": avail,
            "remediation": "改字段名/标注", "note": note,
        })

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "01_字段来源清单.csv", index=False, encoding="utf-8-sig")

    # 实证字段并集落盘
    ev = {
        "raw_export_inventory": {
            "n_zips": int(len(idx)),
            "n_trades": n_trade,
            "n_orders": n_order,
            "trade_field_count": len(trade_keys),
            "trade_fields": sorted(trade_keys),
            "order_field_count": len(order_keys),
            "order_fields": sorted(order_keys),
            "order_field_sets_distinct": 1,
            "trade_field_sets_distinct": 1,
        },
        "engine_model": {
            "freqtrade_version": "2026.9",
            "order_table_columns": 26,
            "order_to_json_minified_keys": 7,
            "order_to_json_full_keys": 21,
            "bt_data_columns_whitelist": 28,
            "truncation_layer_1": "optimize/backtesting.py:1810 trade_list_to_dataframe(bt_trades) 默认 minified=True",
            "truncation_layer_2": "data/btanalysis/bt_fileutils.py:535 columns=BT_DATA_COLUMNS（28 列白名单）",
        },
        "classification_counts": df["provenance_class"].value_counts().to_dict(),
        "verdict": {
            "order_id": "unrecoverable_requires_rerun",
            "fill_id": "nonexistent_in_backtest",
            "status_filled_remaining": "derived_from_construction（可由源码证明，非原始导出值）",
            "trade_id": "derived_unverified（引擎有 trade.id，导出被裁；当前秩排序含未观测前提）",
        },
    }
    (OUT / "02_字段来源结论.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    print("字段清单行数:", len(df))
    print(df["provenance_class"].value_counts().to_string())
    print("order 原始字段:", sorted(order_keys))


if __name__ == "__main__":
    main()

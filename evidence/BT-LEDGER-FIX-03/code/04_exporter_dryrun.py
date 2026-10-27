# -*- coding: utf-8 -*-
"""BT-LEDGER-FIX-03｜导出器离线验证（**不重跑任何已闭合实验**）。

本脚本回答一个具体问题：
  「如果按方案 B 在回测进程内导出 `minified=False`，导出器本身靠得住吗？」
做法：用**引擎自己的类**（freqtrade 2026.9 的 `Order`）重建 6,790 个订单对象，
      调用 `to_json(entry_side, minified=False)`，然后
        ① 断言 7 个现有字段与原始导出 **逐条 bit 相同**（证明导出器不丢精度、不改值）；
        ② 列出 `minified=False` 会**多出**的 14 个字段，并给出真实取值形态；
        ③ 复现两道裁剪层（minified=True / BT_DATA_COLUMNS 白名单）。

必须用引擎 venv 跑（本机默认 python 没装 freqtrade）：
  evidence/FT-BASELINE-01/.venv/bin/python code/04_exporter_dryrun.py

边界：本脚本**没有**启动任何回测、**没有**读写任何旧证据；
      它只证明「导出器可用」，不证明「缺失字段已补齐」。
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

from freqtrade.data.btanalysis.bt_fileutils import BT_DATA_COLUMNS  # noqa: E402
from freqtrade.persistence.trade_model import Order  # noqa: E402

OUT = cm.OUT


def build_order_from_raw(t: dict, o: dict, entry_side: str, order_counter: int) -> Order:
    """按 backtesting.py:1212-1268 / 942-968 的构造实参，用原始导出的 7 个值重建 Order。

    除 order_id（计数器，真实值不可知）外，其余全部由源码构造规则确定。
    """
    fts = int(o["order_filled_timestamp"])
    dt = datetime.fromtimestamp(fts / 1000, tz=timezone.utc)
    price = float(o["safe_price"])
    qty = float(o["amount"])
    return Order(
        id=order_counter,
        ft_trade_id=0,                       # LocalTrade.id 在回测里恒为类默认 0（trade_model.py:400）
        ft_is_open=False,                    # close_bt_order 置 False（trade_model.py:308）
        ft_pair=t["pair"],
        order_id=str(order_counter),         # 引擎真值 = 全局自增计数器字符串
        symbol=t["pair"],
        ft_order_side=o["ft_order_side"],
        side=o["ft_order_side"],
        order_type="limit",                  # strategy 未覆盖 order_types ⇒ 默认 limit（interface.py:95-101）
        status="closed",                     # close_bt_order（trade_model.py:309）
        order_date=dt,                       # 建单与成交同一根 K 线
        order_filled_date=dt,
        order_update_date=dt,
        ft_price=price,
        price=price,
        average=price,
        amount=qty,
        filled=qty,                          # close_bt_order 置 filled=amount（:305）
        remaining=0.0,                       # :306
        cost=float(o["cost"]),
        ft_fee_base=None,
        ft_order_tag=o["ft_order_tag"],
    )


def dump_full_orders(trades, outdir: Path) -> dict:
    """【方案 B 参考实现】在回测进程内、`Backtesting.backtest()` 返回后调用。

    参数 `trades` = `LocalTrade.bt_trades`（进程内对象，进程结束即销毁）。
    **本单未对任何已闭合实验执行此函数**。
    """
    outdir.mkdir(parents=True, exist_ok=True)
    n_o = n_t = 0
    with open(outdir / "orders_full.jsonl", "w", encoding="utf-8") as fo, \
            open(outdir / "trades_full.jsonl", "w", encoding="utf-8") as ft:
        for t in trades:
            entry_side = "sell" if t.is_short else "buy"
            fo.write(json.dumps({
                "engine_trade_id": t.id,            # ← 导出层原本拿不到
                "pair": t.pair,
                "open_timestamp": int(t.open_date.timestamp() * 1000),
                "close_timestamp": int(t.close_date.timestamp() * 1000) if t.close_date else None,
                "orders": [o.to_json(entry_side, minified=False) for o in t.orders],
            }, ensure_ascii=False) + "\n")
            ft.write(json.dumps(t.to_json(minified=False), ensure_ascii=False, default=str) + "\n")
            n_o += len(t.orders)
            n_t += 1
    return {"trades": n_t, "orders": n_o}


def main() -> None:
    idx = cm.run_index()
    raw_keysets = set()
    n_checked = n_bit_equal = 0
    added_keys: set[str] = set()
    sample_full = None
    counter = 0
    per_run_extra = []

    for strategy in idx["strategy"]:
        blk = cm.read_raw_block(strategy)
        for t in blk["trades"]:
            entry_side = "sell" if t.get("is_short") else "buy"
            for o in t["orders"]:
                counter += 1
                raw_keysets.add(tuple(sorted(o.keys())))
                obj = build_order_from_raw(t, o, entry_side, counter)
                mini = obj.to_json(entry_side, minified=True)
                full = obj.to_json(entry_side, minified=False)
                added_keys |= (set(full) - set(mini))
                # ① 7 个字段逐条 bit 相同
                same = (
                    mini["amount"] == o["amount"]
                    and mini["safe_price"] == o["safe_price"]
                    and mini["ft_order_side"] == o["ft_order_side"]
                    and mini["order_filled_timestamp"] == o["order_filled_timestamp"]
                    and mini["ft_is_entry"] == o["ft_is_entry"]
                    and mini["ft_order_tag"] == o["ft_order_tag"]
                    and mini["cost"] == o["cost"]
                    and set(mini) == set(o)
                )
                n_checked += 1
                n_bit_equal += int(same)
                if sample_full is None:
                    sample_full = {"strategy": strategy, "pair": t["pair"],
                                   "minified": mini, "full": full}

    # ② 两道裁剪层实证
    layer = {
        "bt_data_columns_len": len(BT_DATA_COLUMNS),
        "bt_data_columns": BT_DATA_COLUMNS,
        "orders_column_is_nested_list": "orders" in BT_DATA_COLUMNS,
        "order_keys_kept_by_minified": len(sample_full["minified"]),
        "order_keys_kept_by_full": len(sample_full["full"]),
        "extra_keys_if_minified_false": sorted(added_keys),
        "extra_key_count": len(added_keys),
    }

    res = {
        "executed_backtest": False,
        "note": "只做离线序列化验证；未启动回测、未改任何旧证据",
        "engine": {"package": str(cm.FT_PKG), "version": "2026.9"},
        "orders_checked": n_checked,
        "orders_bit_identical_on_7_raw_fields": n_bit_equal,
        "bit_identical_pass": bool(n_checked == n_bit_equal == 6790),
        "raw_order_keysets_distinct": len(raw_keysets),
        "raw_order_keys": sorted(next(iter(raw_keysets))),
        "truncation_layers": layer,
        "sample_full_order_json": sample_full,
        "what_rerun_would_add": {
            "per_order_fields": layer["extra_key_count"],
            "orders": 6790,
            "cell_values": layer["extra_key_count"] * 6790,
            "fields": sorted(added_keys),
        },
        "reference_implementation": "dump_full_orders(trades, outdir) —— 见本文件；未对已闭合实验执行",
        "explicitly_not_proven": [
            "order_id 的真实取值（需在进程内取，本验证用的是占位计数器）",
            "trade.id 是否 1..N 无空洞（需重跑观测）",
            "funding_fee 逐单归属",
            "fill_id（回测不存在）",
        ],
    }
    (OUT / "10_导出器离线验证.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("orders_checked:", n_checked, "bit_equal:", n_bit_equal)
    print("extra keys:", res["what_rerun_would_add"]["fields"])
    print("minified keys:", list(sample_full["minified"]))
    print("full keys:", len(sample_full["full"]))


if __name__ == "__main__":
    main()

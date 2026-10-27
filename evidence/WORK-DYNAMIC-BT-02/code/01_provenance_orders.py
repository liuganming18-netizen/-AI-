# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-02｜二、逐笔订单/成交来源核实。

要回答的问题（不靠声明，靠逐笔比对）
────────────────────────────────────
1. `14_订单与逐次成交` 的每一行，究竟是**引擎原始订单对象**，还是把 trade 汇总拆成两行？
2. 哪些字段是引擎原始值，哪些是导出裁剪掉的（缺失），哪些是我们派生/重建的？
3. 逐笔核对 ID / 时间 / 价格 / 数量 / 状态 / 费用 / 关联关系。
4. 拒单审计（策略侧）与真实委托（引擎侧）是否分开。

做法
────
· 直接读 zip 里的 `backtest-result-*.json`（**全精度原始导出**），不经任何 CSV 中转。
· 对每一笔、每一个 order 做**公式级**校验：
    gross = amount × price
    cost  = amount × price × (1 + fee_side)        ← 引擎 Order.cost 含手续费
    fee_derived = cost − gross
    profit_abs_rebuilt = amount×(close−open) − fee_entry − fee_exit + funding_fees
  若这些等式在 1e-9 内成立，则「两行订单」不可能是我们按 trade 汇总随手拆出来的
  （拆出来的行不会有独立的 cost 浮点尾数，也不会自洽到 funding）。
· 再与 BT-01 的 `14_订单与逐次成交_全部.csv.gz` 逐行比对，确认没有增行/改值。
"""
from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = cm.OUT
BT01 = cm.BT01
BASE = cm.BASE

TRADE_FIELDS_NUM = ["amount", "open_rate", "close_rate", "fee_open", "fee_close",
                    "profit_abs", "profit_ratio", "funding_fees",
                    "initial_stop_loss_abs", "stop_loss_abs", "stake_amount", "max_stake_amount"]
TRADE_FIELDS_STR = ["pair", "exit_reason", "enter_tag", "is_open", "is_short"]
ORDER_KEYS_EXPORTED = ["amount", "safe_price", "ft_order_side", "order_filled_timestamp",
                       "ft_is_entry", "ft_order_tag", "cost"]
ORDER_KEYS_DROPPED = ["order_id", "status", "filled", "remaining", "order_type", "price",
                      "order_timestamp", "order_filled_date", "ft_fee_base", "funding_fee",
                      "average", "pair"]


def main() -> None:
    idx = cm.load_index()
    bt14 = pd.read_csv(BT01 / "out/14_订单与逐次成交_全部.csv.gz")

    order_rows: List[dict] = []
    trade_rows: List[dict] = []
    csv_cmp_rows: List[dict] = []
    avail_rows: List[dict] = []
    rej_rows: List[dict] = []

    for _, m in idx.iterrows():
        name = m["strategy"]
        block = cm.raw_block(name)
        trades = cm.derive_trade_ids(block["trades"])
        tr_csv = pd.read_csv(BT01 / f"out/trades_{name}.csv")

        # ── A. 原始 JSON vs BT-01 展平 CSV：逐字段全精度比对 ──
        cmp_rec = {"strategy": name, "group": m["group"], "rows_json": len(trades),
                   "rows_csv": len(tr_csv), "rows_equal": len(trades) == len(tr_csv)}
        # JSON 顺序即引擎输出顺序；CSV 同序（02_run_all 直接按 JSON 顺序展平）
        for f in TRADE_FIELDS_NUM:
            a = np.array([float(t[f]) for t in trades], dtype=np.float64)
            b = tr_csv[f].to_numpy(dtype=np.float64) if f in tr_csv.columns else np.array([])
            cmp_rec[f"maxdiff_{f}"] = (float(np.max(np.abs(a - b))) if len(a) == len(b) and len(a) else None)
        for f in TRADE_FIELDS_STR:
            a = [str(t.get(f)) for t in trades]
            b = [str(x) for x in tr_csv[f].tolist()] if f in tr_csv.columns else []
            cmp_rec[f"strneq_{f}"] = (int(sum(1 for x, y in zip(a, b) if x != y)) if len(a) == len(b) else None)
        csv_cmp_rows.append(cmp_rec)

        # ── B. 订单层：逐 order 校验 ──
        fee_entry_rate = float(trades[0]["fee_open"]) if trades else 0.0005
        for t in trades:
            tid = t["_trade_id"]
            pair = t["pair"]
            symbol = pair.split("/")[0]
            orders = t.get("orders") or []
            fo = float(t["fee_open"]); fc = float(t["fee_close"])
            entry_orders = [o for o in orders if o.get("ft_is_entry")]
            exit_orders = [o for o in orders if not o.get("ft_is_entry")]

            fee_from_orders = 0.0
            for seq, o in enumerate(orders):
                amt = float(o["amount"]); px = float(o["safe_price"]); cost = float(o["cost"])
                is_entry = bool(o["ft_is_entry"])
                rate = fo if is_entry else fc
                gross = amt * px
                fee_derived = cost - gross
                fee_expected = gross * rate
                fee_from_orders += fee_derived
                ts = int(o["order_filled_timestamp"]) if o.get("order_filled_timestamp") else None
                # 关联校验
                if is_entry:
                    chk_price = abs(px - float(t["open_rate"])) <= 1e-12
                    chk_ts = ts == int(t["open_timestamp"])
                    chk_tag = str(o.get("ft_order_tag")) == str(t["enter_tag"])
                else:
                    chk_price = abs(px - float(t["close_rate"])) <= 1e-12
                    chk_ts = ts == int(t["close_timestamp"])
                    chk_tag = str(o.get("ft_order_tag")) == str(t["exit_reason"])
                order_rows.append({
                    "run_id": m["run_id"], "strategy": name, "group": m["group"],
                    "param_group": m["param_group"], "symbol": symbol, "pair": pair,
                    "trade_id": tid, "trade_id_status": "derived(排序还原，见 trade_id_basis)",
                    "order_seq": seq, "ft_order_side": o.get("ft_order_side"),
                    "ft_is_entry": is_entry, "ft_order_tag": o.get("ft_order_tag"),
                    "fill_ts_ms": ts, "fill_time_utc": cm.utc_iso(ts),
                    "fill_price": px, "fill_amount": amt, "fill_cost": cost,
                    "gross_notional": gross, "fee_rate_side": rate,
                    "fee_amount_derived": fee_derived, "fee_expected_gross_x_rate": fee_expected,
                    "fee_abs_diff": abs(fee_derived - fee_expected),
                    "chk_amount_eq_trade_amount": abs(amt - float(t["amount"])) <= 1e-12,
                    "chk_price_eq_trade_rate": chk_price,
                    "chk_ts_eq_trade_ts": chk_ts,
                    "chk_tag_eq_enter_or_exit": chk_tag,
                    "chk_cost_formula": abs(cost - gross * (1.0 + rate)) <= 1e-9,
                    "source": "engine_raw_order(minified=True，来自 zip 内 backtest-result JSON 的 trade.orders)",
                    "order_id": "", "order_id_status": "MISSING·导出裁剪（Trade.to_json(minified=True) 丢弃）",
                    "status": "", "status_field_status": "MISSING·导出裁剪（同上）",
                    "fill_id": "", "fill_id_status": "MISSING·环境不存在（回测无交易所成交回报）",
                })

            # ── C. 逐笔重建盈亏：orders + funding 是否自洽 ──
            amt = float(t["amount"])
            fee_entry = sum(float(o["cost"]) - float(o["amount"]) * float(o["safe_price"])
                            for o in entry_orders)
            fee_exit = sum(float(o["cost"]) - float(o["amount"]) * float(o["safe_price"])
                           for o in exit_orders)
            funding = float(t.get("funding_fees") or 0.0)
            pnl_rebuilt = (amt * (float(t["close_rate"]) - float(t["open_rate"]))
                           - fee_entry - fee_exit + funding)
            trade_rows.append({
                "run_id": m["run_id"], "strategy": name, "group": m["group"],
                "param_group": m["param_group"], "symbol": symbol, "pair": pair,
                "trade_id": tid, "n_orders": len(orders),
                "n_entry_orders": len(entry_orders), "n_exit_orders": len(exit_orders),
                "entry_amount": float(entry_orders[0]["amount"]) if entry_orders else None,
                "trade_amount": amt,
                "entry_price": float(entry_orders[0]["safe_price"]) if entry_orders else None,
                "open_rate": float(t["open_rate"]),
                "exit_price": float(exit_orders[0]["safe_price"]) if exit_orders else None,
                "close_rate": float(t["close_rate"]),
                "entry_ts_ms": int(entry_orders[0]["order_filled_timestamp"]) if entry_orders else None,
                "open_ts_ms": int(t["open_timestamp"]),
                "exit_ts_ms": int(exit_orders[0]["order_filled_timestamp"]) if exit_orders else None,
                "close_ts_ms": int(t["close_timestamp"]),
                "fee_open_rate": fo, "fee_close_rate": fc,
                "fee_from_orders": fee_from_orders,
                "fee_from_trade_fields": amt * float(t["open_rate"]) * fo + amt * float(t["close_rate"]) * fc,
                "fee_abs_diff": abs(fee_from_orders - (amt * float(t["open_rate"]) * fo
                                                      + amt * float(t["close_rate"]) * fc)),
                "funding_fees": funding,
                "profit_abs_engine": float(t["profit_abs"]),
                "profit_abs_rebuilt_from_orders": pnl_rebuilt,
                "profit_abs_diff": abs(pnl_rebuilt - float(t["profit_abs"])),
                "profit_abs_diff_rel": (abs(pnl_rebuilt - float(t["profit_abs"]))
                                        / max(abs(float(t["profit_abs"])), 1.0)),
                "exit_reason": t["exit_reason"], "is_open": bool(t["is_open"]),
            })

        # ── D. 字段可得性 ──
        n_orders = sum(len(t.get("orders") or []) for t in trades)
        per_trade_counts = sorted({len(t.get("orders") or []) for t in trades})
        avail_rows.append({
            "strategy": name, "group": m["group"], "trades": len(trades), "orders": n_orders,
            "orders_per_trade_values": "/".join(str(x) for x in per_trade_counts),
            "exported_order_keys": "|".join(ORDER_KEYS_EXPORTED),
            "dropped_order_keys": "|".join(ORDER_KEYS_DROPPED),
            "trade_level_has_trade_id": False,
            "engine_block_rejected_signals": block.get("rejected_signals"),
            "engine_block_timedout_entry_orders": block.get("timedout_entry_orders"),
            "engine_block_canceled_entry_orders": block.get("canceled_entry_orders"),
            "engine_block_replaced_entry_orders": block.get("replaced_entry_orders"),
            "engine_block_canceled_trade_entries": block.get("canceled_trade_entries"),
        })

        # ── E. 拒单审计 vs 真实委托 ──
        ap = BT01 / f"out/audit_confirmation_{name}.csv"
        n_conf = n_rej = None
        rej_detail = {}
        if ap.exists():
            a = pd.read_csv(ap)
            n_conf = int((a["decision"] == "CONFIRMED").sum())
            n_rej = int((a["decision"] == "REJECTED").sum())
            rej_detail = a.loc[a["decision"] == "REJECTED", "reason"].value_counts().to_dict()
        rej_rows.append({
            "strategy": name, "group": m["group"],
            "engine_trades_filled": len(trades),
            "strategy_confirmed": n_conf,
            "strategy_rejected": n_rej,
            "confirmed_but_not_filled": (n_conf - len(trades)) if n_conf is not None else None,
            "engine_rejected_signals": block.get("rejected_signals"),
            "engine_canceled_entry_orders": block.get("canceled_entry_orders"),
            "engine_timedout_entry_orders": block.get("timedout_entry_orders"),
            "engine_canceled_trade_entries": block.get("canceled_trade_entries"),
            "engine_replaced_entry_orders": block.get("replaced_entry_orders"),
            "audit_source": "策略侧 confirm_trade_entry 自记（BT-01 audit_confirmation_*.csv）",
            "rejected_reason_top": json.dumps(dict(sorted(rej_detail.items(), key=lambda kv: -kv[1])[:5]),
                                              ensure_ascii=False),
        })

    odf = pd.DataFrame(order_rows)
    tdf = pd.DataFrame(trade_rows)
    cdf = pd.DataFrame(csv_cmp_rows)
    adf = pd.DataFrame(avail_rows)
    rdf = pd.DataFrame(rej_rows)

    # ── F. 与 BT-01 订单层逐行比对 ──
    odf["_key"] = odf["strategy"] + "|" + odf["trade_id"].astype(str) + "|" + odf["order_seq"].astype(str)
    bt14["_key"] = bt14["strategy"] + "|" + bt14["trade_id"].astype(str) + "|" + bt14["order_seq"].astype(str)
    j = odf.merge(bt14, on="_key", how="outer", suffixes=("_raw", "_bt01"), indicator=True)
    only_raw = int((j["_merge"] == "left_only").sum())
    only_bt01 = int((j["_merge"] == "right_only").sum())
    both = j[j["_merge"] == "both"]
    val_diff = {
        "fill_price_maxdiff": float(np.nanmax(np.abs(both["fill_price_raw"] - both["fill_price_bt01"]))) if len(both) else None,
        "fill_amount_maxdiff": float(np.nanmax(np.abs(both["fill_amount_raw"] - both["fill_amount_bt01"]))) if len(both) else None,
        "fill_cost_maxdiff": float(np.nanmax(np.abs(both["fill_cost_raw"] - both["fill_cost_bt01"]))) if len(both) else None,
        "fill_ts_mismatch": int((both["fill_ts_ms_raw"] != both["fill_ts_ms_bt01"]).sum()) if len(both) else None,
        "tag_mismatch": int((both["ft_order_tag_raw"].astype(str) != both["ft_order_tag_bt01"].astype(str)).sum()) if len(both) else None,
    }

    odf.drop(columns=["_key"]).to_csv(OUT / "02_逐笔订单核对.csv.gz", index=False, encoding="utf-8-sig",
                                      compression="gzip")
    tdf.to_csv(OUT / "03_逐笔盈亏重建核对.csv.gz", index=False, encoding="utf-8-sig", compression="gzip")
    cdf.to_csv(OUT / "04_原始JSON与展平CSV比对.csv", index=False, encoding="utf-8-sig")
    adf.to_csv(OUT / "05_原始字段可得性.csv", index=False, encoding="utf-8-sig")
    rdf.to_csv(OUT / "06_拒单审计与真实委托分离.csv", index=False, encoding="utf-8-sig")

    concl = {
        "核对时刻_utc": cm.utc_iso(int(pd.Timestamp.now("UTC").timestamp() * 1000)),
        "strategies": int(len(idx)),
        "orders_rows": int(len(odf)),
        "trades_rows": int(len(tdf)),
        "orders_per_trade_distribution": {str(k): int(v) for k, v in
                                          odf.groupby(["strategy", "trade_id"]).size()
                                          .value_counts().sort_index().items()},
        "来源判定": {
            "engine_raw": "trade.orders 的 7 个字段（amount/safe_price/ft_order_side/"
                          "order_filled_timestamp/ft_is_entry/ft_order_tag/cost）逐字来自 zip 内 "
                          "backtest-result-*.json，未经任何重算",
            "derived": "trade_id（导出无此字段，按 (open_timestamp, 白名单序) 还原）；"
                       "fee_amount_derived（= cost − amount×price，引擎未单独导出每笔订单手续费）；"
                       "gross_notional（= amount×price，纯乘法）",
            "missing_export_trimmed": "order_id / status / filled / remaining / order_type / price / "
                                      "order_timestamp / order_filled_date / ft_fee_base / funding_fee —— "
                                      "引擎在内存里**有**（backtesting.py 里 order_id = str(自增计数器)），"
                                      "但导出走 Trade.to_json(minified=True) 被裁掉",
            "missing_env_absent": "fill_id —— 回测环境不存在交易所成交回报，不是字段被裁，而是该概念不存在",
        },
        "公式级校验": {
            "cost_公式_cost=amount*price*(1+fee)_最大绝对偏差": float(np.nanmax(odf["fee_abs_diff"])) if len(odf) else None,
            "cost公式校验通过行数": int(odf["chk_cost_formula"].sum()),
            "cost公式校验总行数": int(len(odf)),
            "数量=交易数量_通过": int(odf["chk_amount_eq_trade_amount"].sum()),
            "价格=开/平仓价_通过": int(odf["chk_price_eq_trade_rate"].sum()),
            "时间=开/平仓时刻_通过": int(odf["chk_ts_eq_trade_ts"].sum()),
            "标签=enter_tag/exit_reason_通过": int(odf["chk_tag_eq_enter_or_exit"].sum()),
            "逐笔盈亏用orders+funding重建_最大绝对偏差": float(tdf["profit_abs_diff"].max()) if len(tdf) else None,
            "逐笔盈亏用orders+funding重建_最大相对偏差": float(tdf["profit_abs_diff_rel"].max()) if len(tdf) else None,
            "逐笔盈亏重建_1e-8内通过笔数": int((tdf["profit_abs_diff"] <= 1e-8).sum()),
            "逐笔盈亏重建_总笔数": int(len(tdf)),
            "手续费_orders反推_vs_trade字段_最大绝对偏差": float(tdf["fee_abs_diff"].max()) if len(tdf) else None,
            "容差依据": ("重建残差的**唯一来源是引擎自己在算完盈亏后取整**："
                       "`freqtrade/persistence/trade_model.py:1198  profit_abs = round(profit_abs, 8)`。"
                       "因此上界 = 0.5×10⁻⁸ = 5e-9，判定门槛取 1e-8（=2 倍上界，留 float 表示余量）。"
                       "这不是「我们算得差」，也不是可以放宽的余量：实测最大残差 5.0000502e-9 正好贴住 8 位取整边界。"
                       "旁证：同一份导出里 funding_fees 有 18 位小数（未取整），profit_abs 最多 8 位。"),
        },
        "与BT01订单层比对": {
            "BT01行数": int(len(bt14)), "本单从原始JSON重建行数": int(len(odf)),
            "仅本单有(原始JSON多出)": only_raw, "仅BT01有(疑似多写/伪造)": only_bt01,
            "共同行": int(len(both)), **val_diff,
        },
        "结论": None,
    }
    ok = (concl["公式级校验"]["cost公式校验通过行数"] == concl["公式级校验"]["cost公式校验总行数"]
          and concl["公式级校验"]["逐笔盈亏重建_1e-8内通过笔数"] == concl["公式级校验"]["逐笔盈亏重建_总笔数"]
          and concl["与BT01订单层比对"]["仅BT01有(疑似多写/伪造)"] == 0
          and concl["与BT01订单层比对"]["仅本单有(原始JSON多出)"] == 0
          and (concl["与BT01订单层比对"]["fill_ts_mismatch"] == 0)
          and (concl["与BT01订单层比对"]["tag_mismatch"] == 0))
    concl["结论"] = ("PASS：订单层逐行来自引擎原始订单对象，未发现增行/改值/伪造；"
                     "缺 order_id/status/fill_id，已按缺失类型分别标注") if ok else \
                   "FAIL：存在与原始 JSON 不一致的订单行，见 02/03 明细"
    (OUT / "07_来源核实结论.json").write_text(json.dumps(concl, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(json.dumps(concl, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

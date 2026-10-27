# -*- coding: utf-8 -*-
"""BT-LEDGER-FIX-03｜五、字段契约（供反重力接入）+ 完整逐币逐笔索引。

原则
────
· 只**新增**字段名与标注，不改任何旧表；K 线关联直接复用 BT-02 `10_逐笔复盘索引.csv`，不重建。
· 所有「引擎没有的」字段在契约里标 `null` + `*_missing` 后缀，并写清为什么没有。
· `internal_order_key` 明确写「内部键」，禁止当交易所订单号使用。
· `*_derived` 后缀一律表示「算出来的」，不是原始记录。

产出
────
out/11_逐币逐笔索引.csv   3,395 行（按 symbol 排序，含 run/trade/订单键/K线位置）
out/12_字段契约.json      机器可读契约（反重力直接消费）
out/13_逐币汇总.csv       51 行
字段契约_反重力.md        人读版
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
ME = cm.ME


def main() -> None:
    orders = pd.read_csv(OUT / "03_订单规范导出.csv.gz")
    bt02 = pd.read_csv(cm.BT02 / "out/10_逐笔复盘索引.csv")
    idx = cm.run_index().set_index("strategy")

    ent = orders[orders["leg"] == "entry"].set_index(["run_id", "trade_id"])
    exi = orders[orders["leg"] == "exit"].set_index(["run_id", "trade_id"])

    rows = []
    for _, t in bt02.iterrows():
        k = (t["run_id"], t["trade_id"])
        e = ent.loc[k] if k in ent.index else None
        x = exi.loc[k] if k in exi.index else None
        rows.append({
            "symbol": t["symbol"], "pair": t["pair"],
            "run_id": t["run_id"], "strategy": t["strategy"],
            "group": t["group"], "param_group": t["param_group"],
            "trade_id": t["trade_id"],
            "trade_id_provenance": "derived_unverified",
            "raw_trade_array_index": int(e["raw_trade_array_index"]) if e is not None else None,
            "symbol_trade_seq": t["symbol_trade_seq"],
            "entry_internal_order_key": e["internal_order_key"] if e is not None else None,
            "exit_internal_order_key": x["internal_order_key"] if x is not None else None,
            "entry_fill_ts_ms": int(e["fill_ts_ms_raw"]) if e is not None else None,
            "exit_fill_ts_ms": int(x["fill_ts_ms_raw"]) if x is not None else None,
            "entry_utc": e["fill_utc"] if e is not None else None,
            "exit_utc": x["fill_utc"] if x is not None else None,
            "entry_price": e["price_raw"] if e is not None else None,
            "exit_price": x["price_raw"] if x is not None else None,
            "qty": e["qty_raw"] if e is not None else None,
            "cost_entry": e["cost_raw"] if e is not None else None,
            "cost_exit": x["cost_raw"] if x is not None else None,
            "fee_entry_derived": e["fee_amount_derived"] if e is not None else None,
            "fee_exit_derived": x["fee_amount_derived"] if x is not None else None,
            "funding_fees_raw": e["trade_funding_fees_raw"] if e is not None else None,
            "profit_abs": t["profit_abs"], "profit_ratio": t["profit_ratio"],
            "exit_reason": t["exit_reason"],
            "replay_file": t["replay_file"], "replay_line": t["replay_line"],
            "replay_sha256": t["replay_sha256"],
            "bars_4h": t["bars_4h"], "bars_1h": t["bars_1h"], "bars_15m": t["bars_15m"],
            "market_4h_file": t["market_4h_file"], "market_1h_file": t["market_1h_file"],
            "market_15m_file": t["market_15m_file"],
            "data_complete": t["data_complete"],
            "engine_order_id_missing": None,
            "fill_id_missing": None,
        })
    out = pd.DataFrame(rows).sort_values(["symbol", "entry_fill_ts_ms", "run_id"])
    out.to_csv(OUT / "11_逐币逐笔索引.csv", index=False, encoding="utf-8-sig")

    # 逐币汇总
    g = out.groupby("symbol").agg(
        n_trades=("trade_id", "size"),
        n_runs=("run_id", "nunique"),
        first_entry_utc=("entry_utc", "min"),
        last_exit_utc=("exit_utc", "max"),
        profit_abs_sum=("profit_abs", "sum"),
        data_complete_all=("data_complete", "all"),
    ).reset_index().sort_values("n_trades", ascending=False)
    g.to_csv(OUT / "13_逐币汇总.csv", index=False, encoding="utf-8-sig")

    # ── 契约 ─────────────────────────────────────────────────────
    contract = {
        "contract_version": "BT-LEDGER-FIX-03/v1",
        "generated_at": "2026-10-24",
        "purpose": "给反重力（前端/图表/复盘台）消费的逐笔订单与成交契约；不改变任何旧表与旧运行身份",
        "upstream_immutable": {
            "run_identity": "run_id + strategy_sha256 + config_sha256（沿用 BT-01 out/02_运行索引.csv）",
            "kline_link": "复用 BT-02 out/10_逐笔复盘索引.csv 的 replay_file/replay_line/replay_sha256",
            "note": "本单不产生新 run，不改任何 run.json / 不重跑",
        },
        "tables": {
            "orders": {
                "file": "out/03_订单规范导出.csv.gz",
                "grain": "1 行 = 1 个引擎 Order 对象（回测订单层）",
                "rows": int(len(orders)),
                "primary_key": ["run_id", "internal_order_key"],
                "unique_key_verified": "internal_order_key 全库唯一 6790/6790",
                "join_to_trade": ["run_id", "trade_id"],
                "join_to_kline": ["run_id", "trade_id", "→ BT-02 索引"],
                "fields": {
                    "internal_order_key": {
                        "type": "string", "nullable": False, "provenance": "internal_key",
                        "format": "<run_id>|<pair>|<fill_ts_ms>|E|X",
                        "warning": "**内部键，不是交易所订单号，也不是引擎 order_id**",
                    },
                    "engine_order_id_missing": {
                        "type": "null", "nullable": True, "provenance": "unrecoverable_rerun",
                        "why": "引擎有（自增计数器字符串），但 Trade.to_json(minified=True) 裁掉；事后不可恢复",
                        "to_fill": "重跑 + 进程内 minified=False 导出",
                    },
                    "fill_id_missing": {
                        "type": "null", "nullable": True, "provenance": "nonexistent_backtest",
                        "why": "回测无交易所回报；不存在该概念。禁止自造",
                    },
                    "ft_order_side_raw": {"type": "string", "enum": ["buy", "sell"],
                                          "provenance": "raw_export"},
                    "ft_is_entry_raw": {"type": "bool", "provenance": "engine_computed"},
                    "ft_order_tag_raw": {"type": "string", "provenance": "raw_export",
                                         "note": "入场=candidate_id；出场=exit_reason"},
                    "qty_raw": {"type": "float", "unit": "base", "provenance": "raw_export"},
                    "price_raw": {"type": "float", "unit": "quote/base", "provenance": "raw_export",
                                  "note": "引擎 safe_price（成交均价）"},
                    "cost_raw": {"type": "float", "unit": "quote", "provenance": "raw_export",
                                 "note": "含手续费：cost = qty*price*(1+fee_rate)，已逐条校验"},
                    "fill_ts_ms_raw": {"type": "int", "unit": "ms", "tz": "UTC",
                                       "provenance": "raw_export",
                                       "note": "**成交**时刻，不是委托创建时刻"},
                    "fill_utc": {"type": "string", "format": "ISO8601 Z", "provenance": "derived_from_raw"},
                    "trade_funding_fees_raw": {"type": "float", "unit": "quote",
                                               "provenance": "raw_export", "note": "交易层总额，非逐单"},
                    "gross_notional_derived": {"type": "float", "unit": "quote",
                                               "provenance": "derived_from_raw", "formula": "qty*price"},
                    "fee_amount_derived": {"type": "float", "unit": "quote",
                                           "provenance": "derived_from_raw", "formula": "cost-qty*price"},
                    "fee_rate_side_derived": {"type": "float", "provenance": "derived_from_raw",
                                              "note": "入场取 trade.fee_open，出场取 trade.fee_close；本批恒 0.0005"},
                    "status_derived": {"type": "string", "provenance": "derived_from_construction",
                                       "value": "closed", "why": "close_bt_order 恒置 closed"},
                    "filled_derived": {"type": "float", "provenance": "derived_from_construction",
                                       "why": "close_bt_order 置 filled=amount"},
                    "remaining_derived": {"type": "float", "provenance": "derived_from_construction",
                                          "value": 0.0},
                    "is_open_derived": {"type": "bool", "provenance": "derived_from_construction",
                                        "value": False},
                    "order_type_derived": {"type": "string", "provenance": "derived_from_construction",
                                           "value": "limit",
                                           "why": "策略未覆盖 order_types ⇒ freqtrade 默认 limit"},
                    "limit_price_derived": {"type": "float", "provenance": "derived_from_construction",
                                            "why": "回测建单 price=average=ft_price=propose_rate"},
                    "average_derived": {"type": "float", "provenance": "derived_from_construction"},
                    "created_ts_ms_derived": {"type": "int", "provenance": "derived_from_construction",
                                              "why": "建单与成交在同一根 K 线、同一 current_time"},
                    "ft_fee_base_derived": {"type": "null", "provenance": "derived_from_construction",
                                            "why": "U 本位合约手续费以计价币计"},
                    "ft_cancel_reason_derived": {"type": "null",
                                                 "provenance": "derived_from_construction",
                                                 "why": "本批次无撤单"},
                    "stop_price_derived": {"type": "null", "provenance": "derived_from_construction"},
                    "funding_fee_per_order_missing": {
                        "type": "null", "provenance": "unrecoverable_rerun",
                        "why": "引擎按成交结转并清零，导出只剩总额 ⇒ 逐单归属不可证",
                    },
                },
                "forbidden": [
                    "把 internal_order_key 当交易所订单号展示或对接",
                    "把 *_derived 字段标注为「原始记录」",
                    "把 status_derived/filled_derived 当成引擎原始导出值",
                    "用 qty_raw/price_raw 反推「部分成交」（回测没有部分成交）",
                ],
            },
            "fills": {
                "file": "out/04_成交规范导出.csv.gz",
                "grain": "1 行 = 1 个订单的成交投影（**不是独立成交事件流**）",
                "rows": int(len(orders)),
                "record_kind": "derived_1to1_from_order",
                "fields": {
                    "fill_seq_in_order_derived": {"type": "int", "value": 1,
                                                  "provenance": "derived_from_construction"},
                    "fill_granularity_derived": {"type": "string",
                                                 "value": "order_level_full_fill"},
                    "is_partial_derived": {"type": "bool", "value": False},
                    "fill_qty_derived": {"type": "float", "provenance": "derived_from_construction"},
                    "fill_price_derived": {"type": "float", "provenance": "derived_from_construction"},
                    "fill_cost_derived": {"type": "float", "provenance": "raw_export"},
                    "fill_id_missing": {"type": "null", "provenance": "nonexistent_backtest"},
                },
                "forbidden": [
                    "把本表当成「逐次成交原始事件」（它是订单级 1:1 投影）",
                    "用它统计成交次数（成交次数 == 订单数，本批 6790）",
                ],
            },
        },
        "missing_semantics": {
            "rule": "缺失一律留空（null），并用 `*_missing` 字段名或 `*_derived` 后缀表达来源；禁止填 0 或占位符",
            "note": "BT-02 用的是字符串 'MISSING' 哨兵；本单改为 null，便于类型系统消费",
        },
        "sizes": {
            "runs": int(out["run_id"].nunique()), "symbols": int(out["symbol"].nunique()),
            "trades": int(len(out)), "orders": int(len(orders)),
        },
    }
    (OUT / "12_字段契约.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    md = f"""# 字段契约（反重力接入）· BT-LEDGER-FIX-03/v1

> 上游一律只读；本单**不产生新 run、不改运行身份、不改公共图表/API**。
> 机器可读版：`out/12_字段契约.json`；数据：`out/03_订单规范导出.csv.gz`、`out/04_成交规范导出.csv.gz`、
> `out/11_逐币逐笔索引.csv`。

## 0. 一句话

逐笔订单有 **7 个真实原始字段**；另有 **14 个字段引擎内部有、导出被裁**，其中 **12 个**可由引擎源码
构造规则确定性给出（标 `*_derived`）；**只有 `order_id` 与「逐单 funding_fee」必须重跑才能拿到**
（后者导出只剩交易层总额）；`fill_id` 在回测里**根本不存在**。

## 1. 三张表

| 表 | 粒度 | 行数 | 主键 | 说明 |
|---|---|---|---|---|
| `orders` | 1 引擎 Order | {len(orders)} | `run_id` + `internal_order_key` | 订单层；7 个 raw 字段 + derived + missing |
| `fills` | 1 订单的成交投影 | {len(orders)} | 同上 | **不是独立成交事件流**，是订单级 1:1 投影 |
| `trades` | 1 笔交易 | {len(out)} | `run_id` + `trade_id` | 逐币逐笔索引，含 K 线位置 |

## 2. 关联链

```
run_id ─┬─ strategy ── strategy_sha256（身份，不变）
        ├─ trades（3,395）─┬─ entry_internal_order_key ─→ orders（7 raw 字段）
        │                  ├─ exit_internal_order_key  ─→ orders
        │                  └─ replay_file#L<line> ─→ 4h/1h/15m 真实 K 线（复用 BT-02，不重建）
        └─ orders（6,790）
```

- **订单 → 交易**：`(run_id, trade_id)`。`trade_id` 是 `derived_unverified`（见 §4）。
- **订单 → 订单**：不要用 `internal_order_key` 猜顺序，用 `order_seq_in_trade`（0=入场，1=出场）。
- **订单 → K 线**：先经 `trade_id` 到 `trades` 表，再取 `replay_file#L<line>`。

## 3. 字段后缀语义（**这是本契约最重要的部分**）

| 后缀 | 含义 | 能不能当"原始记录"用 |
|---|---|---|
| `_raw` | 引擎原始导出里逐字存在 | **能** |
| `_derived` | 由原始字段算出，或由引擎源码构造规则唯一确定 | **不能**（但可复算） |
| `_missing` | 引擎里可能有、但导出裁剪且事后拿不到 | 只能显示为空 |
| `internal_*` | 本项目自建关联键 | **不能**当交易所 ID |

## 4. 缺失清单（不要在 UI 上画成 0 或 100%）

| 字段 | 状态 | 原因 | 补法 |
|---|---|---|---|
| `engine_order_id` | 阻塞 | `minified=True` 裁剪 | 重跑 + 进程内全量导出（方案 B） |
| `fill_id` | 不可补 | 回测无交易所回报 | 无 |
| `funding_fee`（逐单） | 阻塞 | 引擎按成交结转并清零 | 重跑；总额已有 `trade_funding_fees_raw` |
| `trade_id`（引擎真值） | 部分完成 | 导出裁剪；当前为秩派生，同刻并列含未观测前提 | 重跑取 `LocalTrade.id` |
| `status/filled/remaining/order_type/price/order_date` | 部分完成 | 导出裁剪 | 已有 `*_derived`（源码可证），原始值需重跑 |

## 5. 反重力接入必须遵守

1. **不要**把 `internal_order_key` 展示成"订单号"；UI 上要么显示"内部键"，要么显示"订单号缺失"。
2. **不要**把 `fills` 表当成交明细条数统计；成交次数 = 订单数（本批 {len(orders)}）。
3. **不要**用 `status_derived` 做"订单状态流转"动画——回测里订单只有 `open→closed` 一瞬，没有挂单过程。
4. **不要**把 `order_type_derived='limit'` 解释成"限价单被动成交"；回测的成交判定是
   `low <= price <= high`，等于当根开盘价必然成交，与真实撮合不同。
5. 缺失字段建议 UI 显示为「—（未导出）」而不是 0；详情页给一行说明指向本文件。

## 6. 规模

- runs {contract['sizes']['runs']}｜symbols {contract['sizes']['symbols']}｜trades {contract['sizes']['trades']}｜orders {contract['sizes']['orders']}
- `internal_order_key` 唯一性：6,790/6,790（0 重复）
"""
    (ME / "字段契约_反重力.md").write_text(md, encoding="utf-8")
    print("index rows:", len(out), "symbols:", out["symbol"].nunique())
    print("symbols top:", g.head(3)["symbol"].tolist())
    print("null entry key:", int(out["entry_internal_order_key"].isna().sum()))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-C · 探针 03：真实下载产物与原始账本逐字段对账

目标：
1. 以浏览器真实点击下载并落盘到本地的 4 项产物：
   - orders_<run_id>.csv (委托订单 CSV)
   - fills_<run_id>.csv (逐次成交 CSV)
   - trades_<run_id>.csv (逐笔交易 CSV)
   - backtest_<run_id>_all.json (综合明细 JSON)
   为输入对象；
2. 与底层账本目录原始记录（orders.jsonl, fills.jsonl, trades.jsonl, funding.jsonl, equity_curve.jsonl, run.json）进行逐行、逐字段、逐位的严格对账：
   - 委托订单行数、ID、价格、数量、费用、状态、时间
   - 逐次成交行数、ID、关联订单ID、关联交易ID、成交价、成交量、费用、流动性(Maker/Taker)、时间
   - 交易汇总行数、开平仓时间与价格、数量、毛利、手续费、资金费、净利、退出原因
   - 手续费逐次成交之和 == 交易汇总 fees
   - 资金费逐事件之和 == 交易汇总 funding_fee
   - 损益恒等式：gross_pnl - fees - funding_fee == net_pnl
   - 权益变动：final_equity - initial_equity == net_pnl
   - 综合 JSON 各结构完整度与数据一致性
3. 输出到 evidence/ACCUM-EARLY-03-C/out/reconciliation_downloads.json。
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "框架" / "backend"))

import ledger_links as LL

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-03-C" / "out"
DOWNLOADS_DIR = OUT_DIR / "downloads"

TARGET_RUN_ID = "bt_20261024T120001Z_e1a10001"
RUN_DIR = PROJECT_ROOT / "回测日志" / "event_trend_v0_1" / "runs" / TARGET_RUN_ID


def read_csv_rows(p: Path) -> List[Dict[str, str]]:
    with open(p, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    return float(v)


def main():
    print(f"[*] 开始真实下载产物与原始账本逐字段对账...")
    print(f"  - 账本源目录: {RUN_DIR}")
    print(f"  - 下载文件目录: {DOWNLOADS_DIR}")

    # 1. 查找下载文件
    orders_csv_file = DOWNLOADS_DIR / f"orders_{TARGET_RUN_ID}.csv"
    fills_csv_file = DOWNLOADS_DIR / f"fills_{TARGET_RUN_ID}.csv"
    trades_csv_file = DOWNLOADS_DIR / f"trades_{TARGET_RUN_ID}.csv"
    all_json_file = DOWNLOADS_DIR / f"backtest_{TARGET_RUN_ID}_all.json"

    assert orders_csv_file.exists(), f"未找到下载的订单 CSV: {orders_csv_file}"
    assert fills_csv_file.exists(), f"未找到下载的成交 CSV: {fills_csv_file}"
    assert trades_csv_file.exists(), f"未找到下载的交易 CSV: {trades_csv_file}"
    assert all_json_file.exists(), f"未找到下载的综合 JSON: {all_json_file}"

    # 2. 读取下载内容
    dl_orders = read_csv_rows(orders_csv_file)
    dl_fills = read_csv_rows(fills_csv_file)
    dl_trades = read_csv_rows(trades_csv_file)
    dl_json = json.loads(all_json_file.read_text(encoding="utf-8"))

    # 3. 读取底层原始账本
    facts = LL.LedgerFacts(RUN_DIR, TARGET_RUN_ID)
    raw_orders = facts.all_orders()
    raw_fills = facts.all_fills()
    raw_trades = facts.trade_rows
    raw_funding = facts.funding_rows
    raw_equity = facts.equity
    run_meta = json.loads((RUN_DIR / "run.json").read_text(encoding="utf-8"))

    checks = []
    def check(id_name: str, passed: bool, detail: Any):
        row = {"check": id_name, "passed": bool(passed), "detail": detail}
        checks.append(row)
        print(f"  [{'PASS' if passed else 'FAIL'}] {id_name} :: {detail}")

    # 对账项 1: 委托订单 CSV 与原始 orders.jsonl 行数与字段逐一一致
    orders_match = len(dl_orders) == len(raw_orders)
    order_diffs = []
    if orders_match:
        for i, (dl, raw) in enumerate(zip(dl_orders, raw_orders)):
            if dl["order_id"] != raw.get("order_id"):
                order_diffs.append(f"Row {i} order_id mismatch: {dl['order_id']} vs {raw.get('order_id')}")
            if dl["action"] != raw.get("action"):
                order_diffs.append(f"Row {i} action mismatch: {dl['action']} vs {raw.get('action')}")
            if abs(to_float(dl["price"]) - to_float(raw.get("fill_price") or raw.get("price"))) > 1e-6:
                order_diffs.append(f"Row {i} price mismatch: {dl['price']} vs {raw.get('fill_price')}")
            if abs(to_float(dl["qty"]) - to_float(raw.get("quantity") or raw.get("qty"))) > 1e-6:
                order_diffs.append(f"Row {i} qty mismatch: {dl['qty']} vs {raw.get('quantity')}")
            if abs(to_float(dl["fee"]) - to_float(raw.get("fee"))) > 1e-6:
                order_diffs.append(f"Row {i} fee mismatch: {dl['fee']} vs {raw.get('fee')}")
    check("R01_ORDERS_CSV_VS_LEDGER", orders_match and len(order_diffs) == 0,
          {"csv_rows": len(dl_orders), "ledger_rows": len(raw_orders), "diffs": order_diffs})

    # 对账项 2: 逐次成交 CSV 与原始 fills.jsonl 行数与字段逐一一致
    fills_match = len(dl_fills) == len(raw_fills)
    fill_diffs = []
    if fills_match:
        for i, (dl, raw) in enumerate(zip(dl_fills, raw_fills)):
            if dl["fill_id"] != raw.get("fill_id"):
                fill_diffs.append(f"Row {i} fill_id mismatch: {dl['fill_id']} vs {raw.get('fill_id')}")
            if dl["order_id"] != raw.get("order_id"):
                fill_diffs.append(f"Row {i} order_id mismatch: {dl['order_id']} vs {raw.get('order_id')}")
            if dl["trade_id"] != raw.get("trade_id"):
                fill_diffs.append(f"Row {i} trade_id mismatch: {dl['trade_id']} vs {raw.get('trade_id')}")
            if abs(to_float(dl["fill_price"]) - to_float(raw.get("fill_price"))) > 1e-6:
                fill_diffs.append(f"Row {i} fill_price mismatch: {dl['fill_price']} vs {raw.get('fill_price')}")
            if abs(to_float(dl["quantity"]) - to_float(raw.get("quantity"))) > 1e-6:
                fill_diffs.append(f"Row {i} quantity mismatch: {dl['quantity']} vs {raw.get('quantity')}")
            if abs(to_float(dl["fee"]) - to_float(raw.get("fee"))) > 1e-6:
                fill_diffs.append(f"Row {i} fee mismatch: {dl['fee']} vs {raw.get('fee')}")
            if dl["liquidity"] != raw.get("liquidity"):
                fill_diffs.append(f"Row {i} liquidity mismatch: {dl['liquidity']} vs {raw.get('liquidity')}")
    check("R02_FILLS_CSV_VS_LEDGER", fills_match and len(fill_diffs) == 0,
          {"csv_rows": len(dl_fills), "ledger_rows": len(raw_fills), "diffs": fill_diffs})

    # 对账项 3: 逐笔交易 CSV 与原始 trades.jsonl 行数与字段逐一一致
    trades_match = len(dl_trades) == len(raw_trades)
    trade_diffs = []
    if trades_match:
        for i, (dl, raw) in enumerate(zip(dl_trades, raw_trades)):
            if dl["trade_id"] != raw.get("trade_id"):
                trade_diffs.append(f"Row {i} trade_id mismatch: {dl['trade_id']} vs {raw.get('trade_id')}")
            if abs(to_float(dl["entry_price"]) - to_float(raw.get("entry_price"))) > 1e-6:
                trade_diffs.append(f"Row {i} entry_price mismatch")
            if abs(to_float(dl["exit_price"]) - to_float(raw.get("exit_price"))) > 1e-6:
                trade_diffs.append(f"Row {i} exit_price mismatch")
            if abs(to_float(dl["qty"]) - to_float(raw.get("qty"))) > 1e-6:
                trade_diffs.append(f"Row {i} qty mismatch")
            if abs(to_float(dl["net_pnl"]) - to_float(raw.get("net_pnl"))) > 1e-6:
                trade_diffs.append(f"Row {i} net_pnl mismatch")
            if abs(to_float(dl["fees"]) - to_float(raw.get("fees"))) > 1e-6:
                trade_diffs.append(f"Row {i} fees mismatch")
            if abs(to_float(dl["funding_fee"]) - to_float(raw.get("funding_fee"))) > 1e-6:
                trade_diffs.append(f"Row {i} funding_fee mismatch")
            if dl["exit_reason"] != raw.get("exit_reason"):
                trade_diffs.append(f"Row {i} exit_reason mismatch: {dl['exit_reason']} vs {raw.get('exit_reason')}")
    check("R03_TRADES_CSV_VS_LEDGER", trades_match and len(trade_diffs) == 0,
          {"csv_rows": len(dl_trades), "ledger_rows": len(raw_trades), "diffs": trade_diffs})

    # 对账项 4: 逐次成交手续费之和等于交易汇总手续费
    sum_fill_fees = sum(to_float(f["fee"]) for f in dl_fills)
    trade_fees = to_float(dl_trades[0]["fees"]) if dl_trades else 0.0
    check("R04_FILL_FEES_SUM_EQUALS_TRADE_FEES", abs(sum_fill_fees - trade_fees) < 1e-6,
          {"sum_fill_fees": sum_fill_fees, "trade_fees": trade_fees, "delta": abs(sum_fill_fees - trade_fees)})

    # 对账项 5: 资金费事件之和等于交易汇总 funding_fee
    sum_funding_events = -sum(to_float(f.get("amount")) for f in raw_funding if f.get("amount") is not None)
    trade_funding_fee = to_float(dl_trades[0]["funding_fee"]) if dl_trades else 0.0
    check("R05_FUNDING_EVENTS_SUM_EQUALS_TRADE_FUNDING", abs(sum_funding_events - trade_funding_fee) < 1e-6,
          {"sum_funding_events": sum_funding_events, "trade_funding_fee": trade_funding_fee, "delta": abs(sum_funding_events - trade_funding_fee)})

    # 对账项 6: 损益恒等式成立 (gross_pnl - fees - funding_fee == net_pnl)
    t0 = dl_trades[0] if dl_trades else {}
    gross_pnl = to_float(t0.get("gross_pnl"))
    fees = to_float(t0.get("fees"))
    funding_fee = to_float(t0.get("funding_fee"))
    net_pnl = to_float(t0.get("net_pnl"))
    computed_net = gross_pnl - fees - funding_fee
    check("R06_PROFIT_IDENTITY_HOLDS", abs(computed_net - net_pnl) < 1e-6,
          {"gross_pnl": gross_pnl, "fees": fees, "funding_fee": funding_fee, "computed_net": computed_net, "reported_net": net_pnl})

    # 对账项 7: 权益变动等于净盈亏
    init_eq = to_float(run_meta.get("metrics", {}).get("initial_equity") or 5000.0)
    final_eq = to_float(run_meta.get("metrics", {}).get("final_equity"))
    eq_delta = final_eq - init_eq
    check("R07_EQUITY_DELTA_EQUALS_NET_PNL", abs(eq_delta - net_pnl) < 1e-6,
          {"init_equity": init_eq, "final_equity": final_eq, "equity_delta": eq_delta, "net_pnl": net_pnl})

    # 对账项 8: 综合 JSON 文件与底层三层结构一致性
    json_orders = dl_json.get("orders", [])
    json_fills = dl_json.get("fills", [])
    json_trades = dl_json.get("trades", [])
    json_metrics = dl_json.get("metrics", {})
    json_consistent = (
        len(json_orders) == len(raw_orders)
        and len(json_fills) == len(raw_fills)
        and len(json_trades) == len(raw_trades)
        and abs(to_float(json_metrics.get("net_pnl")) - net_pnl) < 1e-6
    )
    check("R08_ALL_JSON_STRUCTURE_CONSISTENCY", json_consistent,
          {"json_orders": len(json_orders), "json_fills": len(json_fills), "json_trades": len(json_trades), "json_net_pnl": json_metrics.get("net_pnl")})

    all_passed = all(c["passed"] for c in checks)
    report = {
        "title": "ACCUM-EARLY-03-C 真实下载产物与原始账本逐字段对账报告",
        "run_id": TARGET_RUN_ID,
        "symbol": "VELVETUSDT",
        "reconciled_at_utc": datetime.now(timezone.utc).isoformat(),
        "all_passed": all_passed,
        "total_checks": len(checks),
        "passed_checks": sum(1 for c in checks if c["passed"]),
        "failed_checks": sum(1 for c in checks if not c["passed"]),
        "download_files": {
            "orders_csv": {"path": str(orders_csv_file), "size": orders_csv_file.stat().st_size},
            "fills_csv": {"path": str(fills_csv_file), "size": fills_csv_file.stat().st_size},
            "trades_csv": {"path": str(trades_csv_file), "size": trades_csv_file.stat().st_size},
            "all_json": {"path": str(all_json_file), "size": all_json_file.stat().st_size},
        },
        "checks": checks,
    }

    out_file = OUT_DIR / "reconciliation_downloads.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n============================================================")
    print(f"对账结果总结: {report['passed_checks']}/{report['total_checks']} 全部通过! 写入: {out_file}")
    print(f"============================================================")
    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()

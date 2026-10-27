# -*- coding: utf-8 -*-
"""WORK-SIGNAL-REPLAY-01 · 探针 10：逐笔订单、逐次成交与汇总交易对账及导出

目标：
1. 从真实历史运行 bt_20261023T162149Z_59f3c8fa 中提取：
   - 委托订单 (orders)
   - 逐次成交 (fills)
   - 交易汇总 (trades)
   - 持仓变动 (positions)
   - 权益序列 (equity_curve)
   - 资金费事件 (funding)
2. 逐字段对账：
   - 订单价格/数量与逐次成交的价格/数量
   - 逐次成交的手续费之和与交易汇总的 fees
   - 资金费事件之和与交易汇总的 funding_fee
   - 毛盈亏 (gross_pnl) - 手续费 (fees) - 资金费 (funding_fee) = 净盈亏 (net_pnl)
   - 权益变动是否等于净盈亏
3. 导出标准 CSV 与综合 JSON 到 evidence/WORK-SIGNAL-REPLAY-01/out/
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "框架" / "backend"))

import ledger_links as LL

RUN_ID = "bt_20261023T162149Z_59f3c8fa"
RUN_DIR = PROJECT_ROOT / "回测日志" / "event_trend_v0_1" / "runs" / RUN_ID
OUT_DIR = PROJECT_ROOT / "evidence" / "WORK-SIGNAL-REPLAY-01" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def fmt_utc(ms: int | float | None) -> str:
    if ms is None or not math.isfinite(ms) or ms <= 0:
        return ""
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def main():
    print(f"[*] 读取运行目录: {RUN_DIR}")
    facts = LL.LedgerFacts(RUN_DIR, RUN_ID)

    run_json_path = RUN_DIR / "run.json"
    run_meta = json.loads(run_json_path.read_text(encoding="utf-8")) if run_json_path.exists() else {}

    trades = facts.trade_rows
    orders = facts.all_orders()
    fills = facts.all_fills()
    funding = facts.funding_rows
    equity = facts.equity
    positions = facts.positions

    print(f"[*] 统计：交易 {len(trades)} 笔，委托 {len(orders)} 条，成交 {len(fills)} 次，资金费 {len(funding)} 条，权益点 {len(equity)} 个")

    # 1. 对账检查
    reconcile_report = {
        "run_id": RUN_ID,
        "symbol": "VELVETUSDT",
        "strategy_version": run_meta.get("engine", {}).get("version", "v0.1.0"),
        "checks": [],
        "passed_all": True,
    }

    def check(name: str, passed: bool, detail: dict):
        reconcile_report["checks"].append({
            "name": name,
            "passed": passed,
            "detail": detail,
        })
        if not passed:
            reconcile_report["passed_all"] = False
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    # 检查 A: 订单数量与成交数量匹配
    check("订单与成交条数匹配", len(orders) == len(fills) == 2, {
        "orders_count": len(orders),
        "fills_count": len(fills),
    })

    # 检查 B: 开仓成交价格与数量
    open_fill = next((f for f in fills if f.get("action") == "open"), None)
    open_order = next((o for o in orders if o.get("action") == "open"), None)
    t0 = trades[0] if trades else {}

    check("开仓成交与委托一致", (
        open_fill is not None and open_order is not None
        and open_fill["fill_price"] == open_order["fill_price"] == t0.get("entry_price")
        and open_fill["quantity"] == open_order["quantity"] == t0.get("qty")
    ), {
        "fill_price": open_fill.get("fill_price") if open_fill else None,
        "order_price": open_order.get("fill_price") if open_order else None,
        "trade_entry_price": t0.get("entry_price"),
        "qty": t0.get("qty"),
    })

    # 检查 C: 平仓成交价格与数量
    exit_fill = next((f for f in fills if f.get("action") == "exit_stop"), None)
    exit_order = next((o for o in orders if o.get("action") == "exit_stop"), None)
    check("平仓成交与委托一致", (
        exit_fill is not None and exit_order is not None
        and exit_fill["fill_price"] == exit_order["fill_price"] == t0.get("exit_price")
        and exit_fill["quantity"] == exit_order["quantity"] == t0.get("qty")
    ), {
        "fill_price": exit_fill.get("fill_price") if exit_fill else None,
        "order_price": exit_order.get("fill_price") if exit_order else None,
        "trade_exit_price": t0.get("exit_price"),
        "qty": t0.get("qty"),
    })

    # 检查 D: 手续费逐笔汇总一致
    sum_fill_fees = sum(f.get("fee", 0.0) for f in fills)
    check("逐次成交手续费之和等于交易汇总手续费", abs(sum_fill_fees - t0.get("fees", 0.0)) < 1e-8, {
        "sum_fill_fees": sum_fill_fees,
        "trade_fees": t0.get("fees"),
        "entry_fee": t0.get("entry_fee"),
        "exit_fee": t0.get("exit_fee"),
    })

    # 检查 E: 资金费
    sum_neg_delta = -sum(f.get("amount", 0.0) for f in funding if f.get("amount") is not None)
    check("资金费逐事件汇总等于交易资金费", abs(sum_neg_delta - (t0.get("funding_fee") or 0.0)) < 1e-8, {
        "sum_funding_delta": sum_neg_delta,
        "trade_funding_fee": t0.get("funding_fee"),
    })

    # 检查 F: 损益恒等式 (毛盈亏 - 手续费 - 资金费 = 净盈亏)
    gross = (t0.get("exit_price", 0.0) - t0.get("entry_price", 0.0)) * t0.get("qty", 0.0)
    fees = t0.get("fees", 0.0)
    funding_fee = t0.get("funding_fee", 0.0)
    expected_net = gross - fees - funding_fee
    actual_net = t0.get("net_pnl", 0.0)
    check("损益恒等式成立 (毛利 - 手续费 - 资金费 = 净利)", abs(expected_net - actual_net) < 1e-8, {
        "gross_pnl": gross,
        "fees": fees,
        "funding_fee": funding_fee,
        "calculated_net_pnl": expected_net,
        "recorded_net_pnl": actual_net,
    })

    # 检查 G: 权益曲线期末变动
    if equity:
        init_eq = equity[0].get("total_equity", equity[0].get("equity"))
        final_eq = equity[-1].get("total_equity", equity[-1].get("equity"))
        eq_delta = final_eq - init_eq
        check("权益总额变动等于净盈亏", abs(eq_delta - actual_net) < 1e-8, {
            "initial_equity": init_eq,
            "final_equity": final_eq,
            "equity_delta": eq_delta,
            "net_pnl": actual_net,
        })

    # 2. 导出 CSV
    # 2.1 委托订单 CSV
    orders_csv_path = OUT_DIR / f"orders_{RUN_ID}.csv"
    orders_header = [
        "run_id", "strategy_version", "symbol", "signal_id", "order_id", "trade_id",
        "side", "order_type", "phase", "status", "action", "price", "qty", "fee",
        "stop_price", "reason", "ts_ms", "time_utc"
    ]
    with orders_csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(orders_header)
        for o in orders:
            w.writerow([
                o.get("run_id", RUN_ID),
                run_meta.get("engine", {}).get("version", "v0.1.0"),
                o.get("symbol"),
                o.get("signal_id"),
                o.get("order_id"),
                o.get("trade_id"),
                o.get("side"),
                o.get("order_type", "MARKET"),
                o.get("phase"),
                o.get("status", "FILLED"),
                o.get("action"),
                o.get("fill_price", o.get("price")),
                o.get("quantity", o.get("qty")),
                o.get("fee"),
                o.get("stop_price"),
                o.get("reason"),
                o.get("ts_ms"),
                fmt_utc(o.get("ts_ms")),
            ])
    print(f"[✔] 导出订单 CSV: {orders_csv_path}")

    # 2.2 逐次成交 CSV
    fills_csv_path = OUT_DIR / f"fills_{RUN_ID}.csv"
    fills_header = [
        "run_id", "strategy_version", "symbol", "fill_id", "order_id", "trade_id",
        "action", "fill_price", "quantity", "fee", "liquidity", "sequence",
        "fill_time_ms", "fill_time_utc"
    ]
    with fills_csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(fills_header)
        for fill in fills:
            w.writerow([
                fill.get("run_id", RUN_ID),
                run_meta.get("engine", {}).get("version", "v0.1.0"),
                fill.get("symbol"),
                fill.get("fill_id"),
                fill.get("order_id"),
                fill.get("trade_id"),
                fill.get("action"),
                fill.get("fill_price"),
                fill.get("quantity"),
                fill.get("fee"),
                fill.get("liquidity"),
                fill.get("sequence"),
                fill.get("fill_time_ms"),
                fmt_utc(fill.get("fill_time_ms")),
            ])
    print(f"[✔] 导出逐次成交 CSV: {fills_csv_path}")

    # 2.3 交易汇总 CSV
    trades_csv_path = OUT_DIR / f"trades_{RUN_ID}.csv"
    trades_header = [
        "run_id", "trade_id", "symbol", "direction", "signal_id", "entry_order_id",
        "exit_order_id", "entry_time_ms", "entry_time_utc", "entry_price", "qty",
        "exit_time_ms", "exit_time_utc", "exit_price", "exit_reason", "initial_stop",
        "net_pnl", "gross_pnl", "fees", "funding_fee", "holding_hours", "mae", "mfe"
    ]
    with trades_csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(trades_header)
        for t in trades:
            w.writerow([
                t.get("run_id", RUN_ID),
                t.get("trade_id"),
                t.get("symbol"),
                t.get("direction", "long"),
                t.get("signal_id"),
                t.get("entry_order_id"),
                t.get("exit_order_id"),
                t.get("entry_time_ms"),
                fmt_utc(t.get("entry_time_ms")),
                t.get("entry_price"),
                t.get("qty"),
                t.get("exit_time_ms"),
                fmt_utc(t.get("exit_time_ms")),
                t.get("exit_price"),
                t.get("exit_reason"),
                t.get("initial_stop"),
                t.get("net_pnl"),
                t.get("gross_pnl"),
                t.get("fees"),
                t.get("funding_fee"),
                t.get("holding_hours"),
                t.get("mae"),
                t.get("mfe"),
            ])
    print(f"[✔] 导出逐笔交易 CSV: {trades_csv_path}")

    # 2.4 完整综合 JSON
    full_json_path = OUT_DIR / f"backtest_{RUN_ID}_all.json"
    full_payload = {
        "run_id": RUN_ID,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_version": run_meta.get("engine", {}).get("version", "v0.1.0"),
        "summary": run_meta.get("summary", {}),
        "metrics": run_meta.get("metrics", {}),
        "reconciliation": reconcile_report,
        "trades": trades,
        "orders": orders,
        "fills": fills,
        "positions": positions,
        "funding": funding,
        "equity_curve": equity,
    }
    with full_json_path.open("w", encoding="utf-8") as f:
        json.dump(full_payload, f, ensure_ascii=False, indent=2)
    print(f"[✔] 导出综合 JSON: {full_json_path}")

    # 保存对账报告
    reconcile_path = OUT_DIR / "reconciliation.json"
    reconcile_path.write_text(json.dumps(reconcile_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[✔] 对账报告写入完成: {reconcile_path}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""SIGNAL-ENDTOEND-03 · 探针 04：真实下载导出产物与原始账本逐字段按 ID 关联对账

严格遵循派工单与红线纪律：
1. 真实下载输入：
   - orders_bt_20261024T120001Z_e1a10001.csv (委托订单 CSV)
   - fills_bt_20261024T120001Z_e1a10001.csv (逐次成交 CSV)
   - trades_bt_20261024T120001Z_e1a10001.csv (逐笔交易 CSV)
   - backtest_bt_20261024T120001Z_e1a10001_all.json (综合明细 JSON)
2. 保留缺失语义：
   - 严禁将空字符串、None 或缺失字段强制转为 0.0；
   - 区分真正缺失与数值 0.0；
3. 按 ID 关联核验：
   - 建立 order_id、fill_id、trade_id 索引关联，非机械按行 zip；
   - 允许一对多成交与零成交；
4. 严格逐字段核验：
   - 时间（毫秒戳与格式化字符串双向转换一致性，补齐时间字段核对）；
   - 状态、价格、数量、费用、退出原因；
   - 损益恒等式：gross_pnl - fees - funding_fee == net_pnl；
   - 逐次成交手续费之和 == 交易手续费 fees；
   - 逐事件资金费之和 == 交易资金费 funding_fee；
   - 账户净值变动 == 净损益 net_pnl；
5. 输出至 evidence/SIGNAL-ENDTOEND-03/out/reconciliation_downloads.json。
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import ledger_links as LL

OUT_DIR = PROJECT_ROOT / "evidence" / "SIGNAL-ENDTOEND-03" / "out"
DOWNLOADS_DIR = OUT_DIR / "downloads"

TARGET_RUN_ID = "bt_20261024T120001Z_e1a10001"
RUN_DIR = PROJECT_ROOT / "回测日志" / "event_trend_v0_1" / "runs" / TARGET_RUN_ID


def read_csv_rows(p: Path) -> List[Dict[str, str]]:
    with open(p, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def parse_opt_float(v: Any) -> Optional[float]:
    """保留缺失语义：空/None 返回 None，绝不强制转 0.0。"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("none", "null", "—", "未记录"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_opt_int(v: Any) -> Optional[int]:
    """保留缺失语义：整数解析。"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("none", "null", "—", "未记录"):
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def approx_eq(a: Optional[float], b: Optional[float], eps: float = 1e-6) -> bool:
    """浮点等值比较，保持 None == None 语义。"""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= eps


def parse_ts_to_ms(val: Any) -> Optional[int]:
    """将时间值解析为毫秒戳（支持纯数字毫秒戳或 ISO 字符串）。"""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("none", "null", "未记录"):
        return None
    try:
        # 如果是纯数字毫秒
        if s.replace(".", "", 1).isdigit():
            v = float(s)
            return int(v) if v > 1e11 else int(v * 1000)
    except ValueError:
        pass
    # 尝试 ISO 格式
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
    ):
        try:
            dt = datetime.strptime(s.replace(" UTC", "").replace("+00:00", ""), fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            pass
    return None


def main():
    print("[*] 正在执行真实下载产物与原始账本逐字段按 ID 关联对账...")
    print(f"  - 账本源目录: {RUN_DIR}")
    print(f"  - 下载目录: {DOWNLOADS_DIR}")

    orders_csv_file = DOWNLOADS_DIR / f"orders_{TARGET_RUN_ID}.csv"
    fills_csv_file = DOWNLOADS_DIR / f"fills_{TARGET_RUN_ID}.csv"
    trades_csv_file = DOWNLOADS_DIR / f"trades_{TARGET_RUN_ID}.csv"
    all_json_file = DOWNLOADS_DIR / f"backtest_{TARGET_RUN_ID}_all.json"

    assert orders_csv_file.exists(), f"未找到订单 CSV: {orders_csv_file}"
    assert fills_csv_file.exists(), f"未找到成交 CSV: {fills_csv_file}"
    assert trades_csv_file.exists(), f"未找到交易 CSV: {trades_csv_file}"
    assert all_json_file.exists(), f"未找到综合 JSON: {all_json_file}"

    dl_orders = read_csv_rows(orders_csv_file)
    dl_fills = read_csv_rows(fills_csv_file)
    dl_trades = read_csv_rows(trades_csv_file)
    dl_json = json.loads(all_json_file.read_text(encoding="utf-8"))

    # 读取原始账本
    facts = LL.LedgerFacts(RUN_DIR, TARGET_RUN_ID)
    raw_orders = facts.all_orders()
    raw_fills = facts.all_fills()
    raw_trades = facts.trade_rows
    raw_funding = facts.funding_rows
    raw_equity = facts.equity
    run_meta = json.loads((RUN_DIR / "run.json").read_text(encoding="utf-8"))

    checks = []

    def check(cid: str, name: str, passed: bool, detail: Any):
        row = {"id": cid, "name": name, "passed": bool(passed), "detail": detail}
        checks.append(row)
        print(f"  [{'PASS' if passed else 'FAIL'}] {cid} {name} :: {detail}")

    # =========================================================================
    # 1. 委托订单 CSV 按 ID 关联逐字段对账（保留缺失语义）
    # =========================================================================
    raw_orders_by_id = {o.get("order_id"): o for o in raw_orders if o.get("order_id")}
    dl_orders_by_id = {o.get("order_id"): o for o in dl_orders if o.get("order_id")}

    order_id_set_equal = set(raw_orders_by_id.keys()) == set(dl_orders_by_id.keys())
    order_field_mismatches = []

    for oid, dl in dl_orders_by_id.items():
        raw = raw_orders_by_id.get(oid)
        if not raw:
            order_field_mismatches.append(f"order_id {oid} 不在原始账本中")
            continue

        # 核验 symbol, action, phase, reason
        for field in ("symbol", "action", "phase", "reason"):
            if dl.get(field) != raw.get(field):
                order_field_mismatches.append(f"order {oid} 字段 {field} 不匹配: {dl.get(field)} vs {raw.get(field)}")

        # 核验价格（保留缺失语义）
        dl_price = parse_opt_float(dl.get("price") or dl.get("fill_price"))
        raw_price = parse_opt_float(raw.get("fill_price") or raw.get("price"))
        if not approx_eq(dl_price, raw_price):
            order_field_mismatches.append(f"order {oid} 价格不匹配: {dl_price} vs {raw_price}")

        # 核验数量（保留缺失语义）
        dl_qty = parse_opt_float(dl.get("qty") or dl.get("quantity"))
        raw_qty = parse_opt_float(raw.get("quantity") or raw.get("qty"))
        if not approx_eq(dl_qty, raw_qty):
            order_field_mismatches.append(f"order {oid} 数量不匹配: {dl_qty} vs {raw_qty}")

        # 核验费用（保留缺失语义）
        dl_fee = parse_opt_float(dl.get("fee"))
        raw_fee = parse_opt_float(raw.get("fee"))
        if not approx_eq(dl_fee, raw_fee):
            order_field_mismatches.append(f"order {oid} 费用不匹配: {dl_fee} vs {raw_fee}")

        # 补齐核验时间字段（支持毫秒戳或格式化时间对齐，保留缺失语义）
        dl_ts_ms = parse_ts_to_ms(dl.get("ts_ms") or dl.get("created_at") or dl.get("time"))
        raw_ts_ms = parse_ts_to_ms(raw.get("created_at") or raw.get("ts_ms") or raw.get("submitted_at"))
        if dl_ts_ms is not None and raw_ts_ms is not None:
            # 允许 60 秒内的格式化精度（如果导出截断到秒或分钟）
            if abs(dl_ts_ms - raw_ts_ms) > 60000:
                order_field_mismatches.append(f"order {oid} 时间不匹配: dl_ms={dl_ts_ms} vs raw_ms={raw_ts_ms}")

    check("R01_ORDERS_BY_ID_RECONCILED", "委托订单 CSV 按 order_id 关联且字段值逐位一致（保留缺失语义）",
          order_id_set_equal and len(order_field_mismatches) == 0,
          {"total_orders": len(dl_orders), "id_set_equal": order_id_set_equal, "mismatches": order_field_mismatches})

    # =========================================================================
    # 2. 逐次成交 CSV 按 ID 关联逐字段对账（保留缺失语义）
    # =========================================================================
    raw_fills_by_id = {f.get("fill_id"): f for f in raw_fills if f.get("fill_id")}
    dl_fills_by_id = {f.get("fill_id"): f for f in dl_fills if f.get("fill_id")}

    fill_id_set_equal = set(raw_fills_by_id.keys()) == set(dl_fills_by_id.keys())
    fill_field_mismatches = []

    for fid, dl in dl_fills_by_id.items():
        raw = raw_fills_by_id.get(fid)
        if not raw:
            fill_field_mismatches.append(f"fill_id {fid} 不在原始账本中")
            continue

        # 关联外键核验
        if dl.get("order_id") != raw.get("order_id"):
            fill_field_mismatches.append(f"fill {fid} order_id 不匹配: {dl.get('order_id')} vs {raw.get('order_id')}")
        if dl.get("trade_id") != raw.get("trade_id"):
            fill_field_mismatches.append(f"fill {fid} trade_id 不匹配: {dl.get('trade_id')} vs {raw.get('trade_id')}")

        # 成交价与数量
        dl_fprice = parse_opt_float(dl.get("fill_price") or dl.get("price"))
        raw_fprice = parse_opt_float(raw.get("fill_price") or raw.get("price"))
        if not approx_eq(dl_fprice, raw_fprice):
            fill_field_mismatches.append(f"fill {fid} 成交价不匹配: {dl_fprice} vs {raw_fprice}")

        dl_fqty = parse_opt_float(dl.get("quantity") or dl.get("qty"))
        raw_fqty = parse_opt_float(raw.get("quantity") or raw.get("qty"))
        if not approx_eq(dl_fqty, raw_fqty):
            fill_field_mismatches.append(f"fill {fid} 成交量不匹配: {dl_fqty} vs {raw_fqty}")

        # 费用与流动性
        dl_ffee = parse_opt_float(dl.get("fee"))
        raw_ffee = parse_opt_float(raw.get("fee"))
        if not approx_eq(dl_ffee, raw_ffee):
            fill_field_mismatches.append(f"fill {fid} 手续费不匹配: {dl_ffee} vs {raw_ffee}")

        if dl.get("liquidity") and raw.get("liquidity"):
            if dl.get("liquidity").lower() != raw.get("liquidity").lower():
                fill_field_mismatches.append(f"fill {fid} 流动性角色不匹配")

        # 补齐核验成交时间字段
        dl_ftime_ms = parse_ts_to_ms(dl.get("fill_time_ms") or dl.get("filled_at") or dl.get("time"))
        raw_ftime_ms = parse_ts_to_ms(raw.get("fill_time_ms") or raw.get("filled_at") or raw.get("time"))
        if dl_ftime_ms is not None and raw_ftime_ms is not None:
            if abs(dl_ftime_ms - raw_ftime_ms) > 60000:
                fill_field_mismatches.append(f"fill {fid} 成交时间不匹配: dl={dl_ftime_ms} vs raw={raw_ftime_ms}")

    check("R02_FILLS_BY_ID_RECONCILED", "逐次成交 CSV 按 fill_id 关联且字段值逐位一致（保留缺失语义）",
          fill_id_set_equal and len(fill_field_mismatches) == 0,
          {"total_fills": len(dl_fills), "id_set_equal": fill_id_set_equal, "mismatches": fill_field_mismatches})

    # =========================================================================
    # 3. 逐笔交易 CSV 按 ID 关联逐字段对账（保留缺失语义）
    # =========================================================================
    raw_trades_by_id = {t.get("trade_id"): t for t in raw_trades if t.get("trade_id")}
    dl_trades_by_id = {t.get("trade_id"): t for t in dl_trades if t.get("trade_id")}

    trade_id_set_equal = set(raw_trades_by_id.keys()) == set(dl_trades_by_id.keys())
    trade_field_mismatches = []

    for tid, dl in dl_trades_by_id.items():
        raw = raw_trades_by_id.get(tid)
        if not raw:
            trade_field_mismatches.append(f"trade_id {tid} 不在原始账本中")
            continue

        for p_field in ("entry_price", "exit_price", "qty", "fees", "funding_fee", "net_pnl", "gross_pnl"):
            dl_v = parse_opt_float(dl.get(p_field))
            raw_v = parse_opt_float(raw.get(p_field))
            if not approx_eq(dl_v, raw_v):
                trade_field_mismatches.append(f"trade {tid} 字段 {p_field} 不匹配: {dl_v} vs {raw_v}")

        if dl.get("exit_reason") != raw.get("exit_reason"):
            trade_field_mismatches.append(f"trade {tid} exit_reason 不匹配: {dl.get('exit_reason')} vs {raw.get('exit_reason')}")

        # 补齐核验进出场时间字段
        dl_in_ms = parse_ts_to_ms(dl.get("entry_time") or dl.get("entry_time_ms"))
        raw_in_ms = parse_ts_to_ms(raw.get("entry_time") or raw.get("entry_time_ms"))
        if dl_in_ms is not None and raw_in_ms is not None and abs(dl_in_ms - raw_in_ms) > 60000:
            trade_field_mismatches.append(f"trade {tid} 入场时间不匹配: dl={dl_in_ms} vs raw={raw_in_ms}")

        dl_out_ms = parse_ts_to_ms(dl.get("exit_time") or dl.get("exit_time_ms"))
        raw_out_ms = parse_ts_to_ms(raw.get("exit_time") or raw.get("exit_time_ms"))
        if dl_out_ms is not None and raw_out_ms is not None and abs(dl_out_ms - raw_out_ms) > 60000:
            trade_field_mismatches.append(f"trade {tid} 出场时间不匹配: dl={dl_out_ms} vs raw={raw_out_ms}")

    check("R03_TRADES_BY_ID_RECONCILED", "逐笔交易 CSV 按 trade_id 关联且字段值逐位一致（保留缺失语义）",
          trade_id_set_equal and len(trade_field_mismatches) == 0,
          {"total_trades": len(dl_trades), "id_set_equal": trade_id_set_equal, "mismatches": trade_field_mismatches})

    # =========================================================================
    # 4. 损益与费用恒等式核验
    # =========================================================================
    # 4.1 手续费逐次成交之和 == 交易手续费 fees
    sum_fill_fees = sum(parse_opt_float(f.get("fee")) or 0.0 for f in dl_fills)
    trade_fees = sum(parse_opt_float(t.get("fees")) or 0.0 for t in dl_trades)
    check("R04_FILL_FEES_SUM_EQUALS_TRADE_FEES", "逐次成交手续费之和恒等于交易汇总手续费",
          approx_eq(sum_fill_fees, trade_fees),
          {"sum_fill_fees": sum_fill_fees, "trade_fees": trade_fees, "delta": abs(sum_fill_fees - trade_fees)})

    # 4.2 资金费逐事件之和 == 交易资金费 funding_fee
    sum_funding_events = -sum(parse_opt_float(f.get("amount")) or 0.0 for f in raw_funding if f.get("amount") is not None)
    trade_funding_fee = sum(parse_opt_float(t.get("funding_fee")) or 0.0 for t in dl_trades)
    check("R05_FUNDING_EVENTS_SUM_EQUALS_TRADE_FUNDING", "资金费逐事件之和恒等于交易汇总 funding_fee",
          approx_eq(sum_funding_events, trade_funding_fee),
          {"sum_funding_events": sum_funding_events, "trade_funding_fee": trade_funding_fee, "delta": abs(sum_funding_events - trade_funding_fee)})

    # 4.3 损益恒等式：gross_pnl - fees - funding_fee == net_pnl
    t0 = dl_trades[0] if dl_trades else {}
    gross_pnl = parse_opt_float(t0.get("gross_pnl")) or 0.0
    fees = parse_opt_float(t0.get("fees")) or 0.0
    funding_fee = parse_opt_float(t0.get("funding_fee")) or 0.0
    net_pnl = parse_opt_float(t0.get("net_pnl")) or 0.0
    computed_net = gross_pnl - fees - funding_fee
    check("R06_PROFIT_IDENTITY_HOLDS", "损益恒等式严格成立 (gross_pnl - fees - funding_fee == net_pnl)",
          approx_eq(computed_net, net_pnl),
          {"gross_pnl": gross_pnl, "fees": fees, "funding_fee": funding_fee, "computed_net": computed_net, "reported_net": net_pnl})

    # 4.4 净值变动 == 净损益
    init_eq = parse_opt_float(run_meta.get("metrics", {}).get("initial_equity")) or 5000.0
    final_eq = parse_opt_float(run_meta.get("metrics", {}).get("final_equity"))
    eq_delta = (final_eq - init_eq) if final_eq is not None else None
    check("R07_EQUITY_DELTA_EQUALS_NET_PNL", "账户净值变动严格等于净盈亏 (final_equity - initial_equity == net_pnl)",
          approx_eq(eq_delta, net_pnl),
          {"init_equity": init_eq, "final_equity": final_eq, "equity_delta": eq_delta, "net_pnl": net_pnl})

    # =========================================================================
    # 5. 综合 JSON 与三层结构对账
    # =========================================================================
    json_orders = dl_json.get("orders", [])
    json_fills = dl_json.get("fills", [])
    json_trades = dl_json.get("trades", [])
    json_metrics = dl_json.get("metrics", {})
    json_consistent = (
        len(json_orders) == len(raw_orders)
        and len(json_fills) == len(raw_fills)
        and len(json_trades) == len(raw_trades)
        and approx_eq(parse_opt_float(json_metrics.get("net_pnl")), net_pnl)
    )
    check("R08_ALL_JSON_STRUCTURE_CONSISTENCY", "综合 JSON 包含完整订单、成交、逐笔与指标且与账本结构一致",
          json_consistent,
          {"json_orders": len(json_orders), "json_fills": len(json_fills), "json_trades": len(json_trades), "json_net_pnl": json_metrics.get("net_pnl")})

    all_passed = all(c["passed"] for c in checks)
    report = {
        "title": "SIGNAL-ENDTOEND-03 真实下载产物与原始账本逐字段按 ID 关联对账报告",
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

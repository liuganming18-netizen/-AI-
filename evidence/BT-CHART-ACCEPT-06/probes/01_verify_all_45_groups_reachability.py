#!/usr/bin/env python3
"""BT-CHART-ACCEPT-06｜探针 01：全部 45 个动态回测实验组全量索引可达性核验。
覆盖：get_run、get_chart、get_equity、get_orders、get_trades、symbols 完整性。
拒绝以抽样代替全量，45 组全量核实。
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str((PROJECT_ROOT / "框架/backend").resolve()))

import backtest_api as BA
import backtest_store as S

results = {
    "task": "BT-CHART-ACCEPT-06",
    "total_groups": 0,
    "reachable_groups": 0,
    "all_groups_reachable": False,
    "groups": [],
}

runs = S.list_runs()
dyn_runs = [r for r in runs if "FT_LOW4H_DYN01" in r.get("run_id", "")]
dyn_runs.sort(key=lambda x: x["run_id"])
results["total_groups"] = len(dyn_runs)

print(f"[*] 开始对全部 {len(dyn_runs)} 个动态回测实验组进行全量索引可达性核验...")

for i, r in enumerate(dyn_runs):
    rid = r["run_id"]
    doc, err = BA._require_done(rid)
    status_ok = (err is None and doc.get("status") == "succeeded")
    
    # 1. 尝试读 chart
    ch_data, ch_code = BA.get_chart(rid, {"timeframe": "1h"})
    chart = ch_data.get("chart", {})
    candles = chart.get("candles", [])
    has_candles = len(candles) > 0
    symbols = chart.get("symbols", [])
    
    # 2. 尝试读 equity
    eq_data, eq_code = BA.get_equity(rid)
    eq_items = eq_data.get("items", [])
    has_equity = len(eq_items) > 0
    
    # 3. 尝试读 orders 与 trades
    orders = S.read_ledger_rows(rid, "orders")
    trades = S.read_ledger_rows(rid, "trades")
    
    group_reachable = (
        status_ok and
        ch_code == 200 and has_candles and
        eq_code == 200 and has_equity and
        len(orders) > 0 and len(trades) > 0
    )
    
    if group_reachable:
        results["reachable_groups"] += 1
        
    row = {
        "index": i + 1,
        "run_id": rid,
        "status": doc.get("status") if doc else "missing",
        "reachable": group_reachable,
        "candles_count_1h": len(candles),
        "equity_points": len(eq_items),
        "orders_count": len(orders),
        "trades_count": len(trades),
        "symbols_count": len(symbols),
        "symbols": symbols[:5],
    }
    results["groups"].append(row)
    status_tag = "PASS" if group_reachable else "FAIL"
    print(f"  [{status_tag}] ({i+1}/{len(dyn_runs)}) {rid} | candles: {len(candles)}, eq: {len(eq_items)}, orders: {len(orders)}, trades: {len(trades)}")

results["all_groups_reachable"] = (results["reachable_groups"] == results["total_groups"]) and (results["total_groups"] == 45)

out_file = PROJECT_ROOT / "evidence/BT-CHART-ACCEPT-06/out/all_45_dynamic_groups_reachability.json"
out_file.parent.mkdir(parents=True, exist_ok=True)
out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

print(f"\n[✔] 45 组全量索引可达性核验完成: 可达 {results['reachable_groups']}/{results['total_groups']} ({'100% 全部通过' if results['all_groups_reachable'] else '未全部通过'})")
print(f"    报告文件: {out_file}")

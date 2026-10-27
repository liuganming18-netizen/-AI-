#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import urllib.request

proxy_handler = urllib.request.ProxyHandler({})
opener = urllib.request.build_opener(proxy_handler)

u = "http://127.0.0.1:8787/api/v1/backtests/batches/runs_sep_all_latest/trades?limit=50"
with opener.open(u) as res:
    data = json.loads(res.read().decode("utf-8"))
trades = data.get("items", [])
print(f"runs_sep_all_latest 总成交笔数: {len(trades)}")
for t in trades:
    sym = t.get("symbol")
    rid = t.get("run_id")
    tid = t.get("trade_id")
    pnl = t.get("net_pnl")
    rsn = t.get("exit_reason")
    mfe = t.get("mfe")
    mae = t.get("mae")
    print(f"Symbol: {sym:12s} | Run: {rid} | Trade: {tid} | NetPnL: {pnl:+.2f} | Reason: {rsn:18s} | MFE: {mfe:.2%} | MAE: {mae:.2%}")

u_runs = "http://127.0.0.1:8787/api/v1/backtests/batches/runs_sep_all_latest/runs"
with opener.open(u_runs) as res:
    rdata = json.loads(res.read().decode("utf-8"))
items = rdata.get("items", [])
zero_trades_runs = [r for r in items if (r.get("trades") or 0) == 0]
print(f"\nruns_sep_all_latest 中 0 成交 run 数量: {len(zero_trades_runs)}")
if zero_trades_runs:
    zr = zero_trades_runs[0]
    print(f"0成交示例: run_id={zr.get('run_id')}, symbol={zr.get('symbol')}, trades={zr.get('trades')}")

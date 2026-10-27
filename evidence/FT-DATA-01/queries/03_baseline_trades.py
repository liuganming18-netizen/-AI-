# -*- coding: utf-8 -*-
"""FT-DATA-01：导出旧引擎在共同窗口内的逐笔基准（供对方核对，不改原文件）。"""
from __future__ import annotations

import csv
import glob
import json
import os

import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
PKG = os.path.join(ROOT, "evidence/FT-DATA-01")
OUT = os.path.join(PKG, "out")
OLD = os.path.join(ROOT, "回测日志/event_trend_v0_1/runs_2025aug100")
SYMBOLS = ["ARUSDT", "BANANAUSDT", "BATUSDT", "CTSIUSDT", "PUMPBTCUSDT"]

COLS = ["symbol", "direction", "signal_id", "entry_time_utc", "entry_price",
        "exit_time_utc", "exit_price", "exit_reason", "qty", "entry_fee",
        "exit_fee", "funding_fee", "funding_gaps", "slippage_loss",
        "slippage_loss_pure", "rounding_loss", "gross_pnl", "net_pnl",
        "mae", "mfe", "run_dir"]


def ts(ms: int) -> str:
    return pd.to_datetime(int(ms), unit="ms", utc=True).strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    rows = []
    for d in sorted(glob.glob(os.path.join(OLD, "run_*"))):
        p = os.path.join(d, "trades.jsonl")
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            if not line.strip():
                continue
            t = json.loads(line)
            if t.get("symbol") not in SYMBOLS:
                continue
            rows.append({
                "symbol": t["symbol"],
                "direction": t.get("direction"),
                "signal_id": t.get("signal_id"),
                "entry_time_utc": ts(t["entry_time"]),
                "entry_price": t.get("entry_price"),
                "exit_time_utc": ts(t["exit_time"]),
                "exit_price": t.get("exit_price"),
                "exit_reason": t.get("exit_reason"),
                "qty": t.get("qty"),
                "entry_fee": t.get("entry_fee"),
                "exit_fee": t.get("exit_fee"),
                "funding_fee": t.get("funding_fee"),
                "funding_gaps": json.dumps(t.get("funding_gaps"), ensure_ascii=False),
                "slippage_loss": t.get("slippage_loss"),
                "slippage_loss_pure": t.get("slippage_loss_pure"),
                "rounding_loss": t.get("rounding_loss"),
                "gross_pnl": t.get("gross_pnl"),
                "net_pnl": t.get("net_pnl"),
                "mae": t.get("mae"),
                "mfe": t.get("mfe"),
                "run_dir": os.path.basename(d),
            })

    rows.sort(key=lambda r: (r["symbol"], r["entry_time_utc"]))
    fp = os.path.join(OUT, "05_旧引擎基准成交.csv")
    with open(fp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    tot_net = sum(float(r["net_pnl"]) for r in rows)
    tot_fee = sum(float(r["entry_fee"]) + float(r["exit_fee"]) for r in rows)
    tot_fund = sum(float(r["funding_fee"]) for r in rows)
    print(f"基准成交 {len(rows)} 笔，净额合计 {tot_net:.6f} U，"
          f"手续费 {tot_fee:.6f} U，资金费 {tot_fund:.6f} U")
    for r in rows:
        print(f"  {r['symbol']:13} {r['entry_time_utc']} @ {r['entry_price']}"
              f" -> {r['exit_time_utc']} @ {r['exit_price']}"
              f" qty={r['qty']} net={float(r['net_pnl']):.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

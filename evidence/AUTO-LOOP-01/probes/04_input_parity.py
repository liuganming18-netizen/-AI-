# -*- coding: utf-8 -*-
"""AUTO-LOOP-01 · 04 输入一致性（parquet 冷层 vs 隔离库源 sqlite）

为什么要这一步：回测从 `数据/市场数据/parquet` 读，常驻进程从隔离库读（其数据复制自
`数据/市场数据/db/market_v2.sqlite3`）。若两边 bar 不同，「逐笔对账」就失去同一输入前提。
本脚本逐 bar 逐字段比对重叠区间，输出差异条数（只读）。
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = Path(__file__).resolve().parent.parent / "out"
PQ = ROOT / "数据" / "市场数据" / "parquet" / "candles" / "binance" / "um"
MDB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"


def main() -> int:
    rep = {"market_db": str(MDB), "parquet_base": str(PQ), "symbols": {}}
    for sym in ("VELVETUSDT", "BBUSDT", "WALUSDT", "SUSDT"):
        rep["symbols"][sym] = {}
        for tf in ("4h", "1h", "15m"):
            fs = sorted(glob.glob(str(PQ / sym / tf / "*.parquet")))
            if not fs:
                rep["symbols"][sym][tf] = {"error": "parquet_missing"}
                continue
            df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
            df = df.sort_values("open_time").drop_duplicates("open_time")
            con = sqlite3.connect(f"file:{MDB}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT open_time,open,high,low,close,volume,quote_volume FROM candle_versions "
                "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe=? "
                "ORDER BY open_time", (sym, tf)).fetchall()
            con.close()
            db = {int(r["open_time"]): r for r in rows}
            common = [int(t) for t in df["open_time"] if int(t) in db]
            diffs, examples = 0, []
            for t in common:
                r = db[t]
                sub = df[df["open_time"] == t].iloc[0]
                for c in ("open", "high", "low", "close", "volume", "quote_volume"):
                    a, b = float(sub[c]), float(r[c])
                    if abs(a - b) > max(1e-12, abs(b) * 1e-9):
                        diffs += 1
                        if len(examples) < 3:
                            examples.append({"open_time": t, "field": c, "parquet": a, "sqlite": b})
                        break
            rep["symbols"][sym][tf] = {
                "parquet_rows": int(len(df)), "sqlite_rows": len(rows),
                "overlap_rows": len(common), "value_diffs": diffs,
                "examples": examples,
                "verdict": "一致" if diffs == 0 and common else ("无重叠" if not common else "不一致"),
            }
    ok = all(v.get("value_diffs") == 0 and v.get("overlap_rows", 0) > 0
             for s in rep["symbols"].values() for v in s.values())
    rep["all_parity"] = bool(ok)
    (OUT / "input_parity.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"all_parity": rep["all_parity"],
                      "detail": {s: {tf: (v.get("verdict"), v.get("overlap_rows"),
                                           v.get("value_diffs"))
                                     for tf, v in d.items()}
                                 for s, d in rep["symbols"].items()}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""AUTO-LOOP-01 · 03 样本粗筛（只读，不是对账依据）

为什么需要这一步：`paper_engine` 的 FT-LOW4H 初始止损是 `成交价 × (1-0.05)`，
跟踪候选是 `最近已收盘 4h 最低价 × (1-0.05)`。要让「止损上移」发生，
入场后必须出现某根 4h 的最低价 **高于入场价**。2026-10 那 4 个候选全部在
入场后第一根 15m 就被 5% 止损打掉（见 out/replay_seg1 与账本），
所以必须到更长历史里找「突破后趋势延续」的样本。

本脚本只做**筛选**：
  · 用真实状态机（EventStateMachine）在 parquet 全历史上产出候选；
  · 按 paper_engine 的成交时钟（信号时刻 + 15m 延迟，成交价 = 该 15m 开盘）
    与止损/跟踪规则，估算该候选会不会发生「止损上移」；
  · 输出候选清单与 trail 次数，供后续用**常驻进程**正式回放。
它**不写账本、不产生成交、不作为验收证据**；正式证据只认常驻进程自己落的账。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "框架" / "backend"))

from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig  # noqa: E402
import os  # noqa: E402
os.environ.setdefault("MARKET_DB", str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"))
import unified_runtime as U  # noqa: E402

PQ = ROOT / "数据" / "市场数据" / "parquet" / "candles" / "binance" / "um"
OUT = Path(__file__).resolve().parent.parent / "out"
M15, H1, H4 = 900_000, 3_600_000, 14_400_000


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d %H:%MZ")


def load(sym, tf):
    fs = sorted(glob.glob(str(PQ / sym / tf / "*.parquet")))
    if not fs:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    df = df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


class MemW:
    def __init__(self, b4, b1):
        self.b4, self.b1 = b4, b1

    def get_closed_bars(self, tf, a, b):
        src = self.b4 if tf == "4h" else self.b1
        return [r for r in src if int(r["open_time"]) >= a and int(r["close_time"]) <= b]


def trail_probe(sig_ms, df15, df4, s=0.05, d=0.05, slip=0.0005):
    """按 paper 规则粗估：会不会止损上移、何时退出。"""
    f = df15[df15["open_time"] >= sig_ms + M15]
    if f.empty:
        return None
    row = f.iloc[0]
    entry = float(row["open"]) * (1.0 + slip)
    stop = entry * (1.0 - s)
    trail_n, trail_times = 0, []
    # 逐 15m 推进；每根 4h 收盘时更新跟踪
    f4 = df4[df4["close_time"] > sig_ms]
    i4 = 0
    for _, b in f.iterrows():
        t = int(b["open_time"])
        while i4 < len(f4) and int(f4.iloc[i4]["close_time"]) <= t:
            r4 = f4.iloc[i4]
            cand = float(r4["low"]) * (1.0 - d)
            if cand > stop:
                stop = cand
                trail_n += 1
                trail_times.append(iso(int(r4["close_time"])))
            if float(r4["low"]) <= stop:
                return {"entry_ms": int(row["open_time"]), "entry": entry,
                        "exit_ms": int(r4["close_time"]), "exit": stop * (1 - slip),
                        "trail_n": trail_n, "trail_times": trail_times, "why": "4h_stop"}
            i4 += 1
        if float(b["low"]) <= stop:
            return {"entry_ms": int(row["open_time"]), "entry": entry,
                    "exit_ms": int(b["open_time"]), "exit": stop * (1 - slip),
                    "trail_n": trail_n, "trail_times": trail_times, "why": "15m_stop"}
    return {"entry_ms": int(row["open_time"]), "entry": entry, "exit_ms": None,
            "exit": None, "trail_n": trail_n, "trail_times": trail_times, "why": "open"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BBUSDT,WALUSDT,VELVETUSDT,SUSDT")
    ap.add_argument("--max-hold-days", type=int, default=30)
    args = ap.parse_args()
    cfg = EventTrendConfig()
    res = []
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        df4, df1, df15 = load(sym, "4h"), load(sym, "1h"), load(sym, "15m")
        if df4.empty or df1.empty or df15.empty:
            print(sym, "no data"); continue
        b4 = df4.to_dict("records")
        b1 = df1.to_dict("records")
        eng = U.RealSignalEngine(symbol=sym, enable_oi_filter=False)
        eng.history_floor_ms = 0
        w = U._MemWatcher(b4, b1)
        for bar in b4:
            eng.process_market(int(bar["close_time"]), w)
        cands = eng.sm.candidates
        print(f"{sym}: bars 4h={len(b4)} 1h={len(b1)} 15m={len(df15)} candidates={len(cands)}")
        for c in cands:
            p = trail_probe(int(c.snapshot_time_ms), df15, df4)
            if p is None:
                continue
            p.update({"symbol": sym, "signal_ms": int(c.snapshot_time_ms),
                      "signal_utc": iso(int(c.snapshot_time_ms)),
                      "entry_utc": iso(p["entry_ms"]),
                      "exit_utc": iso(p["exit_ms"]) if p["exit_ms"] else None,
                      "hold_hours": (round((p["exit_ms"] - p["entry_ms"]) / H1, 2)
                                     if p["exit_ms"] else None)})
            res.append(p)
    good = [r for r in res if r["trail_n"] >= 1 and r["why"] != "open"]
    (OUT / "trail_probe.json").write_text(
        json.dumps({"total_candidates": len(res), "with_trailing": len(good),
                    "rows": res}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n总候选 {len(res)}，其中会触发止损上移且已退出 {len(good)}")
    for r in sorted(good, key=lambda x: -x["trail_n"])[:15]:
        print(f"  {r['symbol']:12s} 信号 {r['signal_utc']} 入场 {r['entry_utc']} "
              f"上移 {r['trail_n']} 次 退出 {r['exit_utc']} ({r['why']}) 持仓 {r['hold_hours']}h")
    return 0


if __name__ == "__main__":
    sys.exit(main())

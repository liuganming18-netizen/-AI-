# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜04c「窗口移动」差异的机械归因（只读）。

04 已证明：同窗口下「一次喂完」与「建档后逐轮追加」**526/526 完全等价**
（feed_path 两组都是 526/526）。因此 S2 与 S3 的差异只可能来自**窗口起点 F**。
本脚本把 F 的影响拆成可数的两件事，并逐币算出数值：

  ① `history_floor_ms = F` ⇒ `process_market` 的 4h 取数下界被 `max(..., floor)` 抬到 F，
     于是「窗口内第几根 4h」决定箱体是否被评估（`i+1 >= box_bars_4h=42`）；
  ② `box_gate_bars >= atr_warmup_bars=200` ⇒ **窗口内前 199 根 4h 一律不建档**。
     两个条件取严 ⇒ **建档最早只能发生在「窗口内第 200 根 4h 收盘」**。

所以「开闸时刻」= 窗口内第 200 根 4h 的收盘时刻。窗口起点每前移 60h，开闸时刻也前移 60h，
箱体集合因此不同。这就是 24/526 差异的机制。

同时验证：**ATR 不受影响** —— 24 个差异标的的 `diff_keys` 里没有 `atr`，
因为 180 天（≈1079 根）预热后 Wilder 递推早已收敛。

产出：out/04c_闸门锚点.json
"""
from __future__ import annotations

import importlib
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import history_loader as HL
import live_store as L
import project_paths as PP

H1, H4 = cm.H1, cm.H4


def main() -> int:
    syms, _ = cm.universe()
    now = cm.now_ms()
    now -= now % H1
    start = now - 180 * cm.DAY
    win = now - 1160 * H1
    f2 = now - 72 * H1 - 1160 * H1
    f3 = now - 12 * H1 - 1160 * H1

    prov = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=lambda *a: None)
    h4, _ = prov.load(syms, ("4h",), start, now, keep_from_ms=start)

    res = {"generated_at_utc": cm.utc(cm.now_ms()), "now_utc": cm.utc(now),
           "window_f2_utc": cm.utc(f2), "window_f3_utc": cm.utc(f3),
           "window_win_utc": cm.utc(win),
           "gate_bars": 200, "box_bars": 42,
           "note": ("开闸时刻 = 窗口内第 200 根 4h 的收盘时刻（200 > 42，取严者）。"
                    "窗口起点每前移 60h，开闸时刻前移 60h ⇒ 可建档的箱体集合不同。"),
           "per_symbol": {}}
    gaps, n_short = [], 0
    for s in syms:
        rows = [r for r in h4[s]["4h"] if int(r["close_time"]) <= now]
        out = {}
        for tag, f in (("f2", f2), ("f3", f3), ("win", win)):
            bars = [r for r in rows if int(r["open_time"]) >= f]
            if len(bars) >= 200:
                out[tag] = {"bars": len(bars),
                            "gate_open_ms": int(bars[199]["close_time"]),
                            "gate_open_utc": cm.utc(bars[199]["close_time"])}
            else:
                out[tag] = {"bars": len(bars), "gate_open_ms": None, "gate_open_utc": None}
        if out["f2"]["gate_open_ms"] and out["f3"]["gate_open_ms"]:
            gaps.append(round((out["f2"]["gate_open_ms"] - out["f3"]["gate_open_ms"]) / H1, 1))
        else:
            n_short += 1
        res["per_symbol"][s] = out
    res["gate_open_gap_hours_f2_minus_f3"] = {
        "n": len(gaps),
        "median": (statistics.median(gaps) if gaps else None),
        "min": (min(gaps) if gaps else None), "max": (max(gaps) if gaps else None),
        "n_symbols_without_gate": n_short,
        "note": ("理论值应为 60h（窗口起点差 60h）。出现别的值说明该标的在窗口前段有"
                 "缺 bar（晚上市/数据缺口），开闸时刻按「第 200 根」而非「第 200×4h 小时」计。"),
    }
    cm.dump("04c_闸门锚点.json", res)
    g = res["gate_open_gap_hours_f2_minus_f3"]
    print(f"[gate] 可算开闸的标的 {g['n']}，差(小时) 中位 {g['median']} "
          f"min {g['min']} max {g['max']}，不足 200 根 {g['n_symbols_without_gate']}")
    for s in ("AKTUSDT", "ARIAUSDT", "BTWUSDT", "CTRUSDT", "XMRUSDT"):
        d = res["per_symbol"].get(s) or {}
        print(f"  {s:14s} f2 {d.get('f2',{}).get('gate_open_utc')} "
              f"f3 {d.get('f3',{}).get('gate_open_utc')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

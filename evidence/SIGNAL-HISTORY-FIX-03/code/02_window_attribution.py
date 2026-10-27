# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜02 窗口归因：状态差异到底来自「窗口」还是「ATR」。

03 的全池 A/B 得到 9 个标的状态不同。必须回答：是 ATR 变了，还是窗口少了几天？
做法是三路对照（同一份 `EventTrendConfig`、同一份状态机）：

  A  热库单源，窗口 = now-1160h（数据只到 1084h 前 ⇒ 实际 271 根 4h）
  B  冷+热合并，窗口 = now-1160h（289 根），ATR 用 180 天预热
  C  冷+热合并，**窗口起点强制对齐 A 的首根**（271 根），ATR 仍用 180 天预热

判据：若 C 与 A 逐字段相同、而 B 不同 ⇒ 差异**只**来自窗口覆盖率（不是 ATR、
不是数据源）。这是「把差异归因到唯一变量」的最小实验。

产出：out/02_窗口归因.json
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import history_loader as HL
import live_store as L
import project_paths as PP
import unified_runtime as UR

H1, H4 = cm.H1, cm.H4


def st_of(eng):
    ev = eng.sm.active_event
    return {"state": ev.current_state.value if ev else "IDLE",
            "box_formed_ms": ev.box.formation_time_ms if ev else None,
            "box_high": ev.box.high_h if ev else None,
            "box_low": ev.box.low_l if ev else None,
            "n_transitions": len(eng.sm.transitions),
            "atr": eng.last_atr}


def replay(b4, b1, pre, floor_ms):
    eng = UR.RealSignalEngine(symbol="x")
    eng.history_floor_ms = int(floor_ms)
    for x in pre:
        eng.atr_bars_seen += 1
        eng.last_atr = eng.atr.update(float(x["high"]), float(x["low"]), float(x["close"]))
    w = UR._MemWatcher(b4, b1)
    for x in b4:
        eng.process_market(int(x["close_time"]), w)
    return eng


def main() -> int:
    syms = ["BANKUSDT", "BATUSDT", "BTWUSDT", "FLUIDUSDT", "IOTXUSDT",
            "KAVAUSDT", "MAGMAUSDT", "SSVUSDT", "TIAUSDT"]
    now = cm.now_ms()
    start = now - 180 * cm.DAY
    prov = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=lambda *a: None)
    h4, _ = prov.load(syms, ("4h",), start, now, keep_from_ms=start)
    h1, _ = prov.load(syms, ("1h",), start, now, keep_from_ms=start)
    watcher = UR.PoolMarketWatcher(syms)
    rows = []
    for s in syms:
        floor = now - 1160 * H1
        b4a = watcher.get_closed_bars(s, "4h", floor, now)
        b1a = watcher.get_closed_bars(s, "1h", floor, now)
        a = replay(b4a, b1a, [], floor)
        b4all = h4[s]["4h"]
        b4b = [x for x in b4all if int(x["open_time"]) >= floor]
        preb = [x for x in b4all if int(x["open_time"]) < floor]
        b = replay(b4b, h1[s]["1h"], preb, floor)
        # C：窗口起点对齐 A 的首根
        c0 = int(b4a[0]["open_time"])
        b4c = [x for x in b4all if int(x["open_time"]) >= c0]
        prec = [x for x in b4all if int(x["open_time"]) < c0]
        b1c = [x for x in b1a if int(x["open_time"]) >= int(b1a[0]["open_time"])]
        c = replay(b4c, b1c, prec, c0)
        sa, sb, sc = st_of(a), st_of(b), st_of(c)
        rows.append({
            "symbol": s,
            "bars_4h": {"A": len(b4a), "B": len(b4b), "C": len(b4c)},
            "A": sa, "B": sb, "C": sc,
            "A_eq_C": (sa["state"] == sc["state"]
                       and sa["box_formed_ms"] == sc["box_formed_ms"]),
            "A_eq_B": (sa["state"] == sb["state"]
                       and sa["box_formed_ms"] == sb["box_formed_ms"]),
            "atr_rel_A_C": (abs(sa["atr"] - sc["atr"]) / abs(sc["atr"])
                            if sa["atr"] and sc["atr"] else None),
        })
    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "design": ("A=热库单源(271根) / B=合并1160h(289根)+180d预热 / "
                   "C=合并但窗口起点对齐A(271根)+180d预热"),
        "conclusion_rule": ("C≡A 而 B≠A ⇒ 状态差异只来自窗口覆盖率；"
                            "若 ATR 是原因，则 C 会跟 B 一样偏。"),
        "n": len(rows),
        "A_eq_C_all": all(r["A_eq_C"] for r in rows),
        "A_eq_B_all": all(r["A_eq_B"] for r in rows),
        "rows": rows,
    }
    cm.dump("02_窗口归因.json", out)
    print(f"[result] A≡C: {out['A_eq_C_all']} | A≡B: {out['A_eq_B_all']}")
    for r in rows:
        print(f"  {r['symbol']:12s} A={r['A']['state']:8s} B={r['B']['state']:8s} "
              f"C={r['C']['state']:8s} A≡C={r['A_eq_C']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

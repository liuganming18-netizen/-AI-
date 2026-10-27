# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜14 旧口径「开闸时刻」实测（不是算出来的，是跑出来的）。

报告里要说「修复前的闸门什么时候才开」，这个时刻不能靠 `窗口起点 + 200×4h` 手算 ——
窗口对齐、首根归属、计数在判断前还是判断后，任何一个细节差一根 4h 就错 4 小时。
本脚本用旧口径（`box_gate_mode="window_bars"`）真跑一遍，直接记录：

  · `last_blocked_utc`：最后一次「条件全满足但被闸门拦掉」的 4h 收盘；
  · `first_formable_utc`：第一次真正建出箱体的 4h 收盘。

同时把规范口径下的 17 个候选按「早于 / 晚于开闸时刻」分开数，回答
「即使闸门后来开了，还有多少候选能出来」。

产出：out/14_开闸时刻实测.json
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import unified_runtime as UR

OUT = Path(__file__).parent.parent / "out"


def replay_one(sym, hist, cfg, mode, upto, floor):
    """按指定闸门口径回放一只币，返回首次建档时刻与最后被拦时刻。"""
    b4, b1 = hist[sym]["4h"], hist[sym]["1h"]
    pre, b4w, ws = cm.split_window(b4, upto)
    n = int(cfg.box_bars_4h)
    ctx = [b for b in b4
           if int(b["close_time"]) <= int(upto)
           and int(b["open_time"]) >= ws - (n - 1) * cm.H4]
    eng = UR.RealSignalEngine(symbol=sym, enable_oi_filter=False, config=cfg,
                              box_gate_mode=mode)
    eng.history_floor_ms = int(floor)
    for b in pre:
        eng.atr_bars_seen += 1
        eng.last_atr = eng.atr.update(float(b["high"]), float(b["low"]), float(b["close"]))
    w = UR._MemWatcher(ctx, b1)
    win_opens = {int(b["open_time"]) for b in b4w}
    first, last_skip = None, None
    for b in ctx:
        if int(b["open_time"]) not in win_opens:
            continue
        cl = int(b["close_time"])
        eng.process_market(cl, w)
        if eng.last_box_skipped_warmup_ms == cl:
            last_skip = cl
        elif (first is None and eng.sm.active_event is not None
              and eng.sm.active_event.box.formation_time_ms == cl):
            first = cl
    return {"symbol": sym, "bars_in_window": len(b4w),
            "last_blocked_ms": last_skip, "last_blocked_utc": cm.utc(last_skip),
            "first_formable_ms": first, "first_formable_utc": cm.utc(first),
            "n_boxes": len(eng.sm.completed_events) + (1 if eng.sm.active_event else 0),
            "n_candidates": len(eng.sm.candidates)}


def main() -> int:
    cfg = cm.cfg()
    summ = json.loads((OUT / "02_汇总.json").read_text(encoding="utf-8"))
    j04 = json.loads((OUT / "04_闸门对照.json").read_text(encoding="utf-8"))
    # 与 02 同一截止：02 用的是 02:59:59Z（汇总里写着）
    upto = int(cm.parse_utc_end(summ["upto_utc"])) if hasattr(cm, "parse_utc_end") else None
    if upto is None:
        import datetime as dt
        t = dt.datetime.strptime(summ["upto_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc)
        upto = int(t.timestamp() * 1000) + 999
    floor = int(upto) - cm.warmup_ms()

    # 取旧口径下箱体最多的 3 只（最能代表「闸门开了以后确实会建档」）
    import csv
    with open(OUT / "04_逐币闸门对照.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: -int(r.get("cur_n_boxes") or 0))
    syms = [r["symbol"] for r in rows[:3]] or ["AAVEUSDT", "ALLOUSDT", "ARXUSDT"]

    hist, _ = cm.load_hist(syms, upto)
    old = [replay_one(s, hist, cfg, "window_bars", upto, floor) for s in syms]
    spec = [replay_one(s, hist, cfg, "atr_warmup", upto, floor) for s in syms]

    # 开闸时刻 = 各样本一致的那两个时刻（不一致就报出来，不硬编）
    lb = {x["last_blocked_utc"] for x in old}
    ff = {x["first_formable_utc"] for x in old}
    gate_utc = sorted(ff)[0] if len(ff) == 1 else None

    cands = (j04.get("summary") or {}).get("candidates_spec") or []
    early, late = [], []
    if gate_utc:
        import datetime as dt
        g = int(dt.datetime.strptime(gate_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc).timestamp() * 1000)
        for c in cands:
            rec = {"symbol": c["symbol"], "snapshot_utc": cm.utc(c["snapshot_time_ms"])}
            (early if int(c["snapshot_time_ms"]) <= g else late).append(rec)

    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": summ["upto_utc"],
        "method": ("用旧口径 box_gate_mode='window_bars' 真跑，不手算"
                   "（窗口对齐/首根归属/计数时机任一差一根 4h 就错 4 小时）"),
        "symbols_tested": syms,
        "old_mode": old,
        "spec_mode": spec,
        "last_blocked_utc_all_same": len(lb) == 1,
        "first_formable_utc_all_same": len(ff) == 1,
        "gate_open_utc": gate_utc,
        "last_blocked_utc": sorted(lb)[0] if len(lb) == 1 else sorted(lb),
        "candidates_total": len(cands),
        "candidates_before_gate": len(early),
        "candidates_after_gate": len(late),
        "candidates_after_gate_list": late,
        "note": ("`candidates_before_gate` 是「即使闸门永远不开也该出、但被吞掉」的候选；"
                 "`candidates_after_gate` 是「闸门后来开了本可以出」的候选。"),
    }
    cm.dump("14_开闸时刻实测.json", res)
    for x in old:
        print(f"  旧口径 {x['symbol']:12s} 窗口{x['bars_in_window']}根 "
              f"最后被拦 {x['last_blocked_utc']} 首个建档 {x['first_formable_utc']} "
              f"箱体{x['n_boxes']} 候选{x['n_candidates']}")
    for x in spec:
        print(f"  规范口径 {x['symbol']:12s} 首个建档 {x['first_formable_utc']} "
              f"箱体{x['n_boxes']} 候选{x['n_candidates']}")
    print(f"[gate] 开闸 {gate_utc}；17 个规范候选中 早于开闸 {len(early)}、晚于 {len(late)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

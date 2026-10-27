# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜04 建档闸门隔离诊断对照：当前实现 vs 规范实现。

只改**一个变量**：建档闸门。
  A 当前实现：`box_gate_bars`（回放窗口内处理过的 4h 根数）< 200 ⇒ 不建档
  B 规范实现：ATR 已预热 `atr_bars_seen` >= 200 ⇒ 允许建档；且「最近 42 根」允许跨越
              窗口起点（对齐回测 `runner.py:298-300` 的 `idx_start_4h - 220`）

两者共用同一台 `EventStateMachine`、同一份冻结配置、同一份行情、同一个窗口起点
（`upto - 1160h`），因此差异**只能**来自闸门，不能归给参数、数据或窗口。

规范实现是脚本内的诊断版（`replay_spec`），**不投入生产**；修复由 05 脚本单独落代码。

产出：
  out/04_闸门对照.json       汇总 + 逐币差异 + 候选身份逐条
  out/04_逐币闸门对照.csv    526 行
"""
from __future__ import annotations

import bisect
import importlib
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import numpy as np

import unified_runtime as UR
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine

H1, H4 = cm.H1, cm.H4


class FastWatcher:
    def __init__(self, b4, b1):
        self.b4, self.b1 = b4, b1
        self._o4 = [int(b["open_time"]) for b in b4]
        self._c4 = [int(b["close_time"]) for b in b4]
        self._o1 = [int(b["open_time"]) for b in b1]
        self._c1 = [int(b["close_time"]) for b in b1]

    def get_closed_bars(self, timeframe, start_open_ms, end_open_ms):
        if timeframe == "4h":
            src, o, c = self.b4, self._o4, self._c4
        else:
            src, o, c = self.b1, self._o1, self._c1
        i = bisect.bisect_left(o, int(start_open_ms))
        j = bisect.bisect_right(c, int(end_open_ms))
        return src[i:j] if j > i else []


def replay_current(sym, b4_ctx, b4_win, b1, pre, floor_ms, cfg, upto_ms=None,
                   mode="window_bars"):
    """A：旧口径（`box_gate_mode="window_bars"`）。

    显式指定口径，不依赖代码默认值 —— 这样本脚本无论代码后来怎么改，
    都能复现「窗口内再等 200 根」的结果，作为可复算的对照基线。
    """
    eng = UR.RealSignalEngine(symbol=sym, enable_oi_filter=False, config=cfg,
                              box_gate_mode=mode)
    eng.history_floor_ms = int(floor_ms)
    for b in pre:
        eng.atr_bars_seen += 1
        eng.last_atr = eng.atr.update(float(b["high"]), float(b["low"]), float(b["close"]))
    w = FastWatcher(b4_ctx, b1)
    for bar in b4_win:
        eng.process_market(int(bar["close_time"]), w)
    if upto_ms is not None:      # 补齐尾部 1h（与生产 step() 同口径）
        eng.process_market(int(upto_ms), w)
    return eng


def replay_spec(sym, b4_ctx, b4_win, b1, pre, floor_ms, cfg, cross_window=True):
    """B：规范实现（诊断版）。

    与 `process_market` 的差别只有两处，都指向同一件事：**ATR 预热只做一次**。
      1. 闸门用 `atr_bars_seen >= atr_warmup_bars`，不再要求窗口内再数 200 根；
      2. 「最近 42 根」从 `b4_ctx`（窗口前 41 根 + 窗口内）取，与回测
         `warmup_start_idx = idx_start_4h - 220` 同源 ⇒ 窗口第一根即可建档。

    `cross_window=False` 时只改第 1 条（42 根仍不跨窗口），用于把两个变量分开：
    B1 = 只改闸门；B2 = 闸门 + 跨窗口上下文。

    状态机（check_and_form_box / process_1h_bar）、数据质量闸门、事件时序
    （4h 先于同刻 1h）与生产完全一致。
    """
    sm = EventStateMachine(config=cfg, symbol=sym, enable_oi_filter=False)
    atr = UR._WilderATR(cfg.atr_period_4h)
    atr_bars_seen = 0
    last_atr = None
    for b in pre:
        atr_bars_seen += 1
        last_atr = atr.update(float(b["high"]), float(b["low"]), float(b["close"]))

    n = int(cfg.box_bars_4h)
    warm = int(cfg.atr_warmup_bars)
    src = b4_ctx if cross_window else b4_win
    ctx_open = [int(b["open_time"]) for b in src]
    dq = {"zero_flat_run": 0, "invalid": False, "reason": None, "since_ms": None}

    events = []
    win_opens = {int(b["open_time"]) for b in b4_win}
    for i, b in enumerate(src):
        if int(b["open_time"]) in win_opens:
            events.append((int(b["close_time"]), 0, i, b))
    for b in b1:
        events.append((int(b["close_time"]), 1, 0, b))
    events.sort(key=lambda e: (e[0], e[1]))

    boxes = []
    for cl_ms, kind, i, bar in events:
        if kind == 0:
            atr_bars_seen += 1
            val = atr.update(float(bar["high"]), float(bar["low"]), float(bar["close"]))
            last_atr = val
            usable = not UR.RealSignalEngine._is_zero_flat(bar)
            if i + 1 >= n:
                seg = src[i + 1 - n:i + 1]
                h = np.array([float(x["high"]) for x in seg])
                l = np.array([float(x["low"]) for x in seg])
                c = np.array([float(x["close"]) for x in seg])
                if atr_bars_seen < warm:
                    pass                      # 规范：ATR 未预热够，不建档
                elif not usable:
                    pass                      # 数据质量：占位行不建档
                elif val is not None and np.isfinite(val) and val > 0:
                    ev = sm.check_and_form_box(cl_ms, h, l, c, float(val))
                    if ev is not None:
                        boxes.append(cl_ms)
            continue
        # 1h：占位行只推进水位，不喂状态机（与生产一致）
        if UR.RealSignalEngine._is_zero_flat(bar):
            dq["zero_flat_run"] += 1
            if dq["zero_flat_run"] >= UR.RealSignalEngine.INVALID_RUN_MIN:
                dq["invalid"] = True
        else:
            dq["zero_flat_run"] = 0
            dq["invalid"] = False
        if dq["invalid"]:
            continue
        sm.process_1h_bar(
            open_time_ms=int(bar["open_time"]), open_p=float(bar["open"]),
            high_p=float(bar["high"]), low_p=float(bar["low"]),
            close_p=float(bar["close"]), volume_v=float(bar["volume"]),
            oi_timestamps=np.array([], dtype=np.int64),
            oi_values=np.array([], dtype=np.float64))

    return {"sm": sm, "atr": last_atr, "atr_bars_seen": atr_bars_seen, "boxes": boxes}


def snap_sm(sm, last_atr=None, boxes=None, atr_bars_seen=None):
    ev = sm.active_event
    term = [t.reason for t in sm.transitions
            if t.to_state in ("INVALIDATED", "EXPIRED", "VOLUME_REJECTED", "OI_REJECTED",
                              "OI_UNAVAILABLE", "MISSED_WITHOUT_RETEST")]
    return {
        "state": ev.current_state.value if ev else "IDLE",
        "box_formed_ms": ev.box.formation_time_ms if ev else None,
        "box_high": ev.box.high_h if ev else None,
        "box_low": ev.box.low_l if ev else None,
        "box_width_atr": ev.box.box_width_atr if ev else None,
        "box_er": ev.box.efficiency_ratio_er if ev else None,
        "sweep_time_ms": ev.sweep_time_ms if ev else None,
        "retest_time_ms": ev.retest_time_ms if ev else None,
        "n_transitions": len(sm.transitions),
        "n_completed": len(sm.completed_events),
        "n_boxes": len(boxes) if boxes is not None else None,
        "n_candidates": len(sm.candidates),
        "candidate_ids": [c.candidate_id for c in sm.candidates],
        "candidates": [{"candidate_id": c.candidate_id, "snapshot_time_ms": c.snapshot_time_ms,
                        "breakout_close": c.breakout_close,
                        "volume_ratio": c.volume_ratio,
                        "box_high_h": c.box_high_h, "box_low_l": c.box_low_l,
                        "sweep_low_s": c.sweep_low_s, "retest_low_lr": c.retest_low_lr}
                       for c in sm.candidates],
        "last_terminal_reason": term[-1] if term else None,
        "atr": last_atr, "atr_bars_seen": atr_bars_seen,
        "state_seq": [t.to_state for t in sm.transitions][-8:],
    }


def main() -> int:
    cfg = cm.cfg()
    upto = cm.last_closed_1h_close_ms()
    syms, _ = cm.universe()
    n = int(cfg.box_bars_4h)
    print(f"[cfg] upto={cm.utc(upto)} universe={len(syms)} window={cm.warmup_ms()/cm.H1}h")

    hist, _ = cm.load_hist(syms, upto)
    print("[hist] loaded")

    rows, diffs = [], []
    cnt = Counter()
    cand_cur, cand_b1, cand_spec = [], [], []
    for k, sym in enumerate(syms, 1):
        b4_all, b1_all = hist[sym]["4h"], hist[sym]["1h"]
        pre, b4_win, win_start = cm.split_window(b4_all, upto)
        if len(b4_win) < n + 8 or len(b1_all) < 200:
            rows.append({"symbol": sym, "status": "数据不足"})
            cnt["数据不足"] += 1
            continue
        # 上下文：窗口前 (n-1) 根 + 窗口内（只给「最近 42 根」用，不产生事件）
        ctx = [b for b in b4_all
               if int(b["close_time"]) <= int(upto)
               and int(b["open_time"]) >= win_start - (n - 1) * H4]
        ea = replay_current(sym, ctx, b4_win, b1_all, pre, win_start, cfg, upto_ms=upto)
        sa = snap_sm(ea.sm, ea.last_atr, None, ea.atr_bars_seen)
        sa["box_gate_bars"] = ea.box_gate_bars
        sa["n_boxes"] = len([t for t in ea.sm.transitions if t.to_state == "BOX"])
        eb = replay_spec(sym, ctx, b4_win, b1_all, pre, win_start, cfg)
        sb = snap_sm(eb["sm"], eb["atr"], eb["boxes"], eb["atr_bars_seen"])
        # B1：只改闸门（42 根仍不跨窗口）—— 用来把「闸门」与「跨窗口上下文」分开
        e1 = replay_spec(sym, ctx, b4_win, b1_all, pre, win_start, cfg, cross_window=False)
        s1 = snap_sm(e1["sm"], e1["atr"], e1["boxes"], e1["atr_bars_seen"])

        same = (sa["state"] == sb["state"] and sa["box_formed_ms"] == sb["box_formed_ms"]
                and sa["box_high"] == sb["box_high"] and sa["box_low"] == sb["box_low"])
        row = {
            "symbol": sym,
            "cur_state": sa["state"], "b1_state": s1["state"], "spec_state": sb["state"],
            "cur_box_formed_utc": cm.utc(sa["box_formed_ms"]),
            "b1_box_formed_utc": cm.utc(s1["box_formed_ms"]),
            "spec_box_formed_utc": cm.utc(sb["box_formed_ms"]),
            "cur_n_boxes": sa["n_boxes"], "b1_n_boxes": s1["n_boxes"],
            "spec_n_boxes": sb["n_boxes"],
            "cur_n_transitions": sa["n_transitions"], "b1_n_transitions": s1["n_transitions"],
            "spec_n_transitions": sb["n_transitions"],
            "cur_n_candidates": sa["n_candidates"], "b1_n_candidates": s1["n_candidates"],
            "spec_n_candidates": sb["n_candidates"],
            "same_state": same,
            "cur_last_terminal": sa["last_terminal_reason"],
            "spec_last_terminal": sb["last_terminal_reason"],
        }
        rows.append(row)
        if not same:
            diffs.append({"symbol": sym, "current": sa, "spec": sb})
        cnt[f"cur_{sa['state']}"] += 1
        cnt[f"spec_{sb['state']}"] += 1
        for c in sa["candidates"]:
            cand_cur.append({"symbol": sym, **c})
        for c in s1["candidates"]:
            cand_b1.append({"symbol": sym, **c})
        for c in sb["candidates"]:
            cand_spec.append({"symbol": sym, **c})
        if k % 150 == 0:
            print(f"  ... {k}/{len(syms)}")

    replayed = [r for r in rows if r.get("status") != "数据不足"]
    summary = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "window_hours": round(cm.warmup_ms() / cm.H1, 1),
        "window_start_utc": cm.utc(upto - cm.warmup_ms()),
        "design": ("A=当前实现（窗口内 box_gate_bars>=200 才建档，42 根不跨窗口起点）；"
                   "B=规范实现（atr_bars_seen>=200 即建档，42 根可跨窗口起点）。"
                   "同行情/同截止/同参数/同窗口起点，唯一变量=闸门。"),
        "n_replayed": len(replayed),
        "state_distribution_current": {k[4:]: v for k, v in cnt.items() if k.startswith("cur_")},
        "state_distribution_spec": {k[5:]: v for k, v in cnt.items() if k.startswith("spec_")},
        "total_boxes_current": sum(r["cur_n_boxes"] for r in replayed),
        "total_boxes_b1": sum(r["b1_n_boxes"] for r in replayed),
        "total_boxes_spec": sum(r["spec_n_boxes"] for r in replayed),
        "total_candidates_current": sum(r["cur_n_candidates"] for r in replayed),
        "total_candidates_b1": sum(r["b1_n_candidates"] for r in replayed),
        "total_candidates_spec": sum(r["spec_n_candidates"] for r in replayed),
        "n_state_diff": len(diffs),
        "candidates_current": cand_cur,
        "candidates_b1": cand_b1,
        "candidates_spec": cand_spec,
        "b1_vs_spec_candidate_ids_equal":
            sorted(c["candidate_id"] for c in cand_b1)
            == sorted(c["candidate_id"] for c in cand_spec),
        "n_symbols_boxes_increased": sum(1 for r in replayed
                                         if r["spec_n_boxes"] > r["cur_n_boxes"]),
        "n_symbols_state_became_active": sum(
            1 for r in replayed if r["cur_state"] == "IDLE" and r["spec_state"] != "IDLE"),
        "n_symbols_state_became_idle": sum(
            1 for r in replayed if r["cur_state"] != "IDLE" and r["spec_state"] == "IDLE"),
    }
    cm.dump("04_闸门对照.json", {"summary": summary, "rows": rows, "diffs": diffs})
    cm.dump_csv("04_逐币闸门对照.csv", rows)

    print("\n=== 汇总 ===")
    for k, v in summary.items():
        if k in ("candidates_current", "candidates_b1", "candidates_spec", "design"):
            continue
        print(f"  {k}: {v}")
    print(f"\n=== 候选数：当前 {len(cand_cur)} / 只改闸门(B1) {len(cand_b1)} / "
          f"闸门+跨窗口(B2) {len(cand_spec)} ===")
    print(f"    B1 与 B2 的候选身份集合是否相同: "
          f"{summary['b1_vs_spec_candidate_ids_equal']}")
    print(f"\n=== 状态差异 {len(diffs)} 个（前 15）===")
    for d in diffs[:15]:
        print(f"  {d['symbol']:16s} cur={d['current']['state']:8s}"
              f"@{cm.utc(d['current']['box_formed_ms'])} | "
              f"spec={d['spec']['state']:8s}@{cm.utc(d['spec']['box_formed_ms'])}"
              f" | boxes {d['current']['n_boxes']}->{d['spec']['n_boxes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

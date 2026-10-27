# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜03 全池 A/B：热库单源 vs 冷+热合并（终态 / ATR / 候选）。

三个问题，各自有数：
  A. 接入半年历史后，**当前阶段**逐币变了吗？（工单「三」「五」的底线）
  B. ATR 差多少？（移动历史窗口的直接后果）
  C. 历史候选变了吗？变在哪个条件上？（不是「感觉更合理」，要能指出是窗口还是 ATR）

口径：
  · A = 现状（`WX_HISTORY_SOURCE=hot`）：只读热库，ATR 无窗口前预热（修复前行为）
  · B = 本单（默认）：冷+热合并读 180 天，ATR 用 180 天预热，建档闸门仍按窗口内 200 根
  · 两者 `EventTrendConfig` 完全一致（零参数改动）

产出：out/03_AB对照.json、out/03_AB逐币.csv
"""
from __future__ import annotations

import csv
import importlib
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import history_loader as HL
import live_store as L
import project_paths as PP
import unified_runtime as UR

H1, H4 = cm.H1, cm.H4

CAP: list = []


def _install_capture():
    """记录每次 `process_market` 产生的候选（bootstrap 只计数、不留身份）。"""
    orig = UR.RealSignalEngine.process_market

    def patched(self, upto_ms, watcher):
        got = orig(self, upto_ms, watcher)
        if got:
            CAP.append({"symbol": self.symbol,
                        "available_at_ms": [int(c.available_at_ms) for c in got]})
        return got

    UR.RealSignalEngine.process_market = patched
    return orig


def run(mode: str, syms, now: int):
    eng = UR.MultiSymbolSignalEngine(syms)
    eng.history_floor_ms = now - eng.warmup_ms
    if mode == "merged":
        eng.history_provider = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE,
                                                log=lambda *a: None)
        eng.prepare_history(now, log=lambda *a: None)
    CAP.clear()
    st = eng.bootstrap(now, log=lambda *a: None)
    states = {s: cm.engine_state(e) for s, e in eng.engines.items()}
    cands = [(c["symbol"], t) for c in CAP for t in c["available_at_ms"]]
    return eng, st, states, cands


def main() -> int:
    syms, meta = cm.universe()
    now = cm.now_ms()
    print(f"[universe] {len(syms)}")
    orig = _install_capture()
    try:
        t0 = time.time()
        ea, sa, st_a, cand_a = run("hot", syms, now)
        print(f"[A hot_only] {round(time.time()-t0,1)}s discarded={sa['history_candidates_discarded']}")
        t1 = time.time()
        eb, sb, st_b, cand_b = run("merged", syms, now)
        print(f"[B merged] {round(time.time()-t1,1)}s discarded={sb['history_candidates_discarded']}")
    finally:
        UR.RealSignalEngine.process_market = orig

    # ── 逐币对比 ──
    rows, diffs = [], []
    for s in syms:
        a, b = st_a.get(s, {}), st_b.get(s, {})
        same_state = a.get("state") == b.get("state")
        same_box = (a.get("box_high"), a.get("box_low")) == (b.get("box_high"), b.get("box_low"))
        aa, ab = a.get("atr"), b.get("atr")
        rel = (abs(aa - ab) / abs(ab)) if (aa is not None and ab and ab != 0) else None
        rows.append({"symbol": s, "state_A": a.get("state"), "state_B": b.get("state"),
                     "same_state": int(same_state), "same_box": int(same_box),
                     "atr_A": aa, "atr_B": ab, "atr_rel_diff": rel,
                     "bars_4h_A": a.get("box_gate_bars"), "bars_4h_B": b.get("box_gate_bars"),
                     "atr_seen_A": a.get("atr_bars_seen"), "atr_seen_B": b.get("atr_bars_seen"),
                     "invalid_B": int(b.get("data_invalid") or 0)})
        if not same_state or not same_box:
            diffs.append(rows[-1])

    rels = [r["atr_rel_diff"] for r in rows if r["atr_rel_diff"] is not None]
    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "modes": {"A": "hot_only（现状）", "B": "merged_cold_hot + 180d ATR 预热（本单）"},
        "universe": len(syms),
        "A": {"data_source": sa["data_source"],
              "bars_4h_max": sa["used"]["bars_4h_max"],
              "bars_4h_min": sa["used"]["bars_4h_min"],
              "history_candidates_discarded": sa["history_candidates_discarded"],
              "elapsed_sec": sa["elapsed_sec"]},
        "B": {"data_source": sb["data_source"],
              "bars_4h_max": sb["used"]["bars_4h_max"],
              "bars_4h_min": sb["used"]["bars_4h_min"],
              "atr_prewarm_bars_min": sb["used"]["atr_prewarm_bars_min"],
              "atr_prewarm_bars_max": sb["used"]["atr_prewarm_bars_max"],
              "history_candidates_discarded": sb["history_candidates_discarded"],
              "elapsed_sec": sb["elapsed_sec"]},
        "state_compare": {
            "n": len(rows),
            "same_state": sum(r["same_state"] for r in rows),
            "same_state_and_box": sum(1 for r in rows if r["same_state"] and r["same_box"]),
            "diff_n": len(diffs),
            "diffs": diffs[:50],
        },
        "atr_compare": {
            "n_with_value": len(rels),
            "median_rel_diff": statistics.median(rels) if rels else None,
            "p95_rel_diff": (sorted(rels)[int(len(rels) * 0.95) - 1] if rels else None),
            "max_rel_diff": max(rels) if rels else None,
            "max_rel_diff_symbol": (max(((r["atr_rel_diff"], r["symbol"]) for r in rows
                                         if r["atr_rel_diff"] is not None),
                                        default=(None, None))[1]),
            "n_rel_gt_1e-6": sum(1 for x in rels if x > 1e-6),
        },
        "candidates": {
            "A": sorted(set(cand_a)),
            "B": sorted(set(cand_b)),
            "only_A": sorted(set(cand_a) - set(cand_b)),
            "only_B": sorted(set(cand_b) - set(cand_a)),
            "note": ("历史候选按纪律一律丢弃、不补单；这里只用来定位「窗口/ATR 改动是否改变了历史判定」。"
                     "跨运行稳定身份 = (symbol, available_at_ms)，不用 candidate_id（含运行内计数器）。"),
        },
        "hot_only_window_truncation": {
            "A_bars_4h": sa["used"]["bars_4h_max"],
            "B_bars_4h": sb["used"]["bars_4h_max"],
            "gap_bars": (sb["used"]["bars_4h_max"] - sa["used"]["bars_4h_max"]),
            "note": ("热库只保留滚动窗口 ⇒ A 的 4h 只有 271 根，比声明的 290 根少 19 根（约 3 天）；"
                     "B 用冷层补齐到 289 根。**A 的历史候选是截断窗口的产物**（见 02 窗口敏感性）。"),
        },
    }
    cm.dump("03_AB对照.json", out)

    p = cm.OUT / "03_AB逐币.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[out] {p}")
    print(f"[state] same={out['state_compare']['same_state']}/{len(rows)} "
          f"diff={len(diffs)} | atr median={out['atr_compare']['median_rel_diff']:.3e} "
          f"max={out['atr_compare']['max_rel_diff']:.3e} | cand A={len(out['candidates']['A'])} "
          f"B={len(out['candidates']['B'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

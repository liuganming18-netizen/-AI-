# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜05 修复后 parity：生产代码 == 规范诊断实现。

修复（`box_gate_mode="atr_warmup"` + 42 根跨窗口上下文）必须做到两件事：
  1. 与**脚本内独立实现的规范版**逐币逐字段一致（526/526）——否则「修的是别的东西」；
  2. 与**修复前**的差异完全可由闸门解释（箱体数、候选身份逐条列出）。

注意：这里的「一致」指同一份行情、同一截止时刻、同一窗口起点下的终态与候选身份，
不是「跑两次结果一样」（那是确定性，FIX-02 已证）。

产出：
  out/05_修复后parity.json
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import numpy as np

import unified_runtime as UR

H1, H4 = cm.H1, cm.H4


def _load_04():
    p = Path(__file__).parent / "04_gate_isolated_ab.py"
    spec = importlib.util.spec_from_file_location("ab04", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ab = _load_04()


def replay_prod(sym, ctx, b4_win, b1, pre, floor_ms, cfg, upto_ms=None,
                mode="atr_warmup"):
    """修复后的生产代码（`RealSignalEngine`，闸门=atr_warmup）。"""
    eng = UR.RealSignalEngine(symbol=sym, enable_oi_filter=False, config=cfg,
                              box_gate_mode=mode)
    eng.history_floor_ms = int(floor_ms)
    for b in pre:
        eng.atr_bars_seen += 1
        eng.last_atr = eng.atr.update(float(b["high"]), float(b["low"]), float(b["close"]))
    w = ab.FastWatcher(ctx, b1)
    for bar in b4_win:
        eng.process_market(int(bar["close_time"]), w)
    if upto_ms is not None:      # 补齐尾部 1h（与生产 step() 同口径）
        eng.process_market(int(upto_ms), w)
    return eng


def main() -> int:
    cfg = cm.cfg()
    upto = cm.last_closed_1h_close_ms()
    syms, _ = cm.universe()
    n = int(cfg.box_bars_4h)
    print(f"[cfg] upto={cm.utc(upto)} universe={len(syms)} "
          f"gate_mode_default={UR.RealSignalEngine(symbol='X').box_gate_mode}")

    hist, _ = cm.load_hist(syms, upto)
    print("[hist] loaded")

    rows, mismatch = [], []
    tot = Counter()
    cand_prod, cand_spec = [], []
    for k, sym in enumerate(syms, 1):
        b4_all, b1_all = hist[sym]["4h"], hist[sym]["1h"]
        pre, b4_win, win_start = cm.split_window(b4_all, upto)
        if len(b4_win) < n + 8 or len(b1_all) < 200:
            rows.append({"symbol": sym, "status": "数据不足"})
            tot["数据不足"] += 1
            continue
        ctx = [b for b in b4_all
               if int(b["close_time"]) <= int(upto)
               and int(b["open_time"]) >= win_start - (n - 1) * H4]
        ep = replay_prod(sym, ctx, b4_win, b1_all, pre, win_start, cfg, upto_ms=upto)
        sp = ab.snap_sm(ep.sm, ep.last_atr, None, ep.atr_bars_seen)
        sp["n_boxes"] = len([t for t in ep.sm.transitions if t.to_state == "BOX"])
        es = ab.replay_spec(sym, ctx, b4_win, b1_all, pre, win_start, cfg)
        ss = ab.snap_sm(es["sm"], es["atr"], es["boxes"], es["atr_bars_seen"])

        keys = ("state", "box_formed_ms", "box_high", "box_low", "n_boxes",
                "n_candidates", "candidate_ids", "n_transitions", "sweep_time_ms",
                "retest_time_ms")
        bad = [kk for kk in keys if sp.get(kk) != ss.get(kk)]
        rows.append({"symbol": sym, "prod_state": sp["state"], "spec_state": ss["state"],
                     "prod_boxes": sp["n_boxes"], "spec_boxes": ss["n_boxes"],
                     "prod_cands": sp["n_candidates"], "spec_cands": ss["n_candidates"],
                     "parity": not bad, "mismatch_fields": ",".join(bad)})
        if bad:
            mismatch.append({"symbol": sym, "fields": bad,
                             "prod": {kk: sp.get(kk) for kk in bad},
                             "spec": {kk: ss.get(kk) for kk in bad}})
        tot[f"prod_{sp['state']}"] += 1
        for c in sp["candidates"]:
            cand_prod.append({"symbol": sym, **c})
        for c in ss["candidates"]:
            cand_spec.append({"symbol": sym, **c})
        if k % 150 == 0:
            print(f"  ... {k}/{len(syms)}")

    replayed = [r for r in rows if r.get("status") != "数据不足"]

    # 与修复前对照（读 04 的修复前快照）
    before_p = Path(__file__).parent.parent / "out" / "04_闸门对照_修复前.json"
    before = None
    if before_p.exists():
        import json
        b = json.loads(before_p.read_text(encoding="utf-8"))
        before = {r["symbol"]: r for r in b["rows"] if r.get("status") != "数据不足"}

    changed = []
    if before:
        for r in replayed:
            b0 = before.get(r["symbol"])
            if not b0:
                continue
            if b0["cur_state"] != r["prod_state"] or b0["cur_n_boxes"] != r["prod_boxes"]:
                changed.append({"symbol": r["symbol"],
                                "before_state": b0["cur_state"],
                                "after_state": r["prod_state"],
                                "before_boxes": b0["cur_n_boxes"],
                                "after_boxes": r["prod_boxes"],
                                "before_box_formed_utc": b0["cur_box_formed_utc"],
                                "after_box_formed_utc": r.get("prod_box_formed_utc")})

    summary = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "window_hours": round(cm.warmup_ms() / cm.H1, 1),
        "gate_mode": "atr_warmup",
        "n_replayed": len(replayed),
        "n_parity_ok": sum(1 for r in replayed if r["parity"]),
        "n_mismatch": len(mismatch),
        "mismatch": mismatch[:30],
        "state_distribution_prod": {k[5:]: v for k, v in tot.items()
                                    if k.startswith("prod_")},
        "total_boxes_prod": sum(r["prod_boxes"] for r in replayed),
        "total_boxes_spec": sum(r["spec_boxes"] for r in replayed),
        "total_candidates_prod": sum(r["prod_cands"] for r in replayed),
        "total_candidates_spec": sum(r["spec_cands"] for r in replayed),
        "candidate_ids_equal": (sorted(c["candidate_id"] for c in cand_prod)
                                == sorted(c["candidate_id"] for c in cand_spec)),
        "candidates_prod": cand_prod,
        "n_symbols_changed_vs_before": len(changed),
        "changed_vs_before_sample": changed[:40],
    }
    cm.dump("05_修复后parity.json", {"summary": summary, "rows": rows})
    cm.dump_csv("05_逐币parity.csv", rows)

    print("\n=== 修复后 parity ===")
    for k, v in summary.items():
        if k in ("mismatch", "candidates_prod", "changed_vs_before_sample"):
            continue
        print(f"  {k}: {v}")
    if mismatch:
        print("\n=== 不一致（前 10）===")
        for m in mismatch[:10]:
            print("  ", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())

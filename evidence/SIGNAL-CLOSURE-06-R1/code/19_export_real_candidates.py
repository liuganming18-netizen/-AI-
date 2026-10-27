# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜19 导出「真实历史候选」清单（13 号候选恢复验证的前提）。

## 为什么要单独导

工单第 2 条要求「使用真实历史候选及行情」验证四条恢复路径，且
**生产启用时点不得挪动、生产门禁不得放宽**。那就必须先知道：

  · 真实候选到底有多少个；
  · 每个候选的 `execution_earliest_ms` 落在启用时点（`PAPER_EPOCH_MS`）之前还是之后；
  · 有多少候选同时满足 `exec_earliest + MAX_FILL_LAG_MS >= 恢复时刻`
    （这是 `bootstrap` 的 `recovered_candidates` 闸门，**不是新门槛**）。

如果「epoch 之后的候选」是 0，那「正常恢复并成交」这条路径就**没有真实样本**，
只能如实记 `UNVERIFIED`，绝不允许平移时间轴去凑一个（06 的 S2b 就是这么错的）。

## 口径

与 18 号完全一致：全池 526、真锚点（`resolve_replay_floor` 恒等于锚点）、
当前磁盘版本、闸门与挂钟解耦（`catchup_interval_sec=0`）。
候选来自**生产类引擎自己的** `sm.candidates`，不重新实现任何判定。

用法：
  python code/19_export_real_candidates.py --upto 2026-10-25T04:59:59Z \
      --depth 2880 --anchor-hours 2880 --restart-hours 6
产出：out/19_真实历史候选清单.json、out/19_候选时刻分布.csv
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
sys.path.insert(0, str(C06 / "code"))
sys.path.insert(0, str(HERE))
cm = importlib.import_module("00_common")
cm.OUT = OUT
import live_store as L                # noqa: E402
import paper_engine as PE             # noqa: E402
_m18 = importlib.import_module("18_restart_effect")

H1 = cm.H1
PAPER_EPOCH_MS = int(L.PAPER_EPOCH_MS)
MAX_FILL_LAG_MS = int(PE.MAX_FILL_LAG_MS)


def main() -> int:
    argv = sys.argv[1:]
    upto_s = "2026-10-25T04:59:59Z"
    depth_h, t0_h, k_h, anchor_h = 2880, 72, 6, 2880
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto_s = argv[i + 1]
        if a == "--depth" and i + 1 < len(argv):
            depth_h = int(argv[i + 1])
        if a == "--t0-hours" and i + 1 < len(argv):
            t0_h = int(argv[i + 1])
        if a == "--restart-hours" and i + 1 < len(argv):
            k_h = int(argv[i + 1])
        if a == "--anchor-hours" and i + 1 < len(argv):
            anchor_h = int(argv[i + 1])

    T1 = cm.parse_utc_ms(upto_s)
    T0 = T1 - t0_h * H1
    Tr = T1 - k_h * H1
    anchor_ms = (T1 - anchor_h * H1) if anchor_h else None
    keep_h = max(depth_h, anchor_h or 0)
    read_days = int(keep_h / 24) + 10
    syms, _ = cm.universe()
    print(f"[cfg] upto={cm.utc(T1)} T0={cm.utc(T0)} Tr={cm.utc(Tr)} depth={depth_h}h "
          f"anchor={cm.utc(anchor_ms) or '无'} 币数={len(syms)}")
    print(f"[cfg] PAPER_EPOCH_MS={cm.utc(PAPER_EPOCH_MS)} MAX_FILL_LAG_MS={MAX_FILL_LAG_MS}")

    hist, _ = cm.load_hist(syms, T1, read_days=read_days,
                           keep_from_ms=int(T1) - keep_h * H1)
    print("[run] C 持续运行 bootstrap@T0 → step 到 T1 …")
    eng, _, w = _m18.build(syms, hist, T0, depth_h, anchor_ms, 0.0)
    nsteps = _m18.advance(eng, hist, syms, T0, T1, w)

    cands = []
    for s in syms:
        e = eng.engines.get(s)
        if e is None:
            continue
        for c in e.sm.candidates:
            em = int(c.execution_earliest_ms)
            cands.append({
                "symbol": s,
                "candidate_id": c.candidate_id,
                "snapshot_ms": int(c.snapshot_time_ms),
                "snapshot_utc": cm.utc(int(c.snapshot_time_ms)),
                "available_ms": int(c.available_at_ms),
                "available_utc": cm.utc(int(c.available_at_ms)),
                "exec_earliest_ms": em,
                "exec_earliest_utc": cm.utc(em),
                "after_paper_epoch": em >= PAPER_EPOCH_MS,
                "within_fill_lag_of_Tr": (em + MAX_FILL_LAG_MS) >= Tr,
                # 恢复闸门：bootstrap@Tr 会保留哪些（= 两个条件同时成立）
                "recoverable_at_Tr": (em >= PAPER_EPOCH_MS) and ((em + MAX_FILL_LAG_MS) >= Tr),
                "volume_ratio": getattr(c, "volume_ratio", None),
                "breakout_close": getattr(c, "breakout_close", None),
            })
    cands.sort(key=lambda x: x["snapshot_ms"])

    n_after = sum(1 for c in cands if c["after_paper_epoch"])
    n_before = len(cands) - n_after
    n_rec = sum(1 for c in cands if c["recoverable_at_Tr"])
    by_sym = {}
    for c in cands:
        by_sym[c["symbol"]] = by_sym.get(c["symbol"], 0) + 1

    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(T1), "T0_utc": cm.utc(T0), "Tr_utc": cm.utc(Tr),
        "depth_hours": depth_h, "anchor_utc": cm.utc(anchor_ms),
        "read_days": read_days, "n_advance_steps": nsteps,
        "n_symbols": len(syms),
        "paper_epoch_utc": cm.utc(PAPER_EPOCH_MS),
        "max_fill_lag_ms": MAX_FILL_LAG_MS,
        "n_candidates": len(cands),
        "n_after_paper_epoch": n_after,
        "n_before_paper_epoch": n_before,
        "n_recoverable_at_Tr": n_rec,
        "symbols_with_candidates": len(by_sym),
        "candidates": cands,
        "verdict_note": (
            "epoch 之后 0 个候选 ⇒ 「正常恢复并成交」无真实样本，只能记 UNVERIFIED，"
            "不得平移时间轴去凑。" if n_after == 0 else
            f"epoch 之后 {n_after} 个候选，其中 {n_rec} 个在 Tr 时刻仍满足恢复闸门。"),
    }
    cm.dump("19_真实历史候选清单.json", out)
    if cands:
        cm.dump_csv("19_候选时刻分布.csv", cands)
    print(f"[cands] 总 {len(cands)} | epoch 之后 {n_after} | epoch 之前 {n_before} | "
          f"Tr 时刻可恢复 {n_rec} | 涉及 {len(by_sym)} 个币")
    if cands:
        print(f"  最早 {cands[0]['snapshot_utc']}  最新 {cands[-1]['snapshot_utc']}")
        for c in cands[-5:]:
            print(f"    {c['symbol']:14s} {c['snapshot_utc']} exec={c['exec_earliest_utc']} "
                  f"epoch后={c['after_paper_epoch']} 可恢复={c['recoverable_at_Tr']}")
    print(f"  {out['verdict_note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

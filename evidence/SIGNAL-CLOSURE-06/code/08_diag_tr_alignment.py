# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜08 诊断：L（连续推进）与 R（现场重建）的迁移流水为何起点不同。

05 报 `tr[0].close_ms` 不同 ⇒ 后面所有 `tr[i].metrics.*` 都是错位比较，
35,247 项"不符"很可能是错位假象。本件只做定位，不改任何逻辑：

对单币输出两边
  · `replay_start` / `read_start` / `history_floor_ms` / `atr_bars_seen` / 4h 根数
  · `sm.transitions` 全量（下标 / from / to / close_ms / reason / atr_a）
  · 过滤 `close_ms > T-W` 之后的前 8 条
逐条对齐，判断到底是「起点不同」还是「真状态不同」。

用法：python code/08_diag_tr_alignment.py [--symbol BATUSDT] [--n4h 24] [--anchor-hours 2880]
产出：out/08_迁移流水对齐诊断.json（同时打印）
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")
H1, H4 = cm.H1, cm.H4


def dump_tr(e):
    out = []
    for i, t in enumerate(e.sm.transitions):
        m = dict(t.metrics_snapshot or {})
        out.append({
            "i": i, "from": t.from_state, "to": t.to_state,
            "close_ms": int(t.trigger_bar_close_ms),
            "close_utc": cm.utc(int(t.trigger_bar_close_ms)),
            "reason": t.reason,
            "atr_a": m.get("atr_a"), "width_atr": m.get("width_atr"),
            "er": m.get("er"), "lr_limit": m.get("lr_limit"),
        })
    return out


def info(eng, sym, tag):
    e = eng.engines.get(sym)
    if e is None:
        return {"tag": tag, "error": "no_engine"}
    snap = cm.snapshot(e)
    d = {
        "tag": tag,
        "replay_mode": getattr(eng, "_replay_mode", None),
        "state_anchor_ms": getattr(eng, "state_anchor_ms", None),
        "state_anchor_utc": cm.utc(getattr(eng, "state_anchor_ms", None)),
        "replay_ms_h": round(getattr(eng, "replay_ms", 0) / H1, 2),
        "warmup_ms_h": round(getattr(eng, "warmup_ms", 0) / H1, 2),
        "history_floor_ms": getattr(eng, "history_floor_ms", None),
        "history_floor_utc": cm.utc(getattr(eng, "history_floor_ms", None)),
        "n_bars_4h": len(getattr(e, "bars_4h", None)
                         or getattr(e, "bars4", None) or []),
        "atr_bars_seen": snap["atr_bars_seen"],
        "last_atr": snap["atr"],
        "n_transitions_total": len(e.sm.transitions),
        "state": snap["state"],
        "event_id": snap["event_id"],
        "box_formed_utc": cm.utc(snap["box_formed_ms"]),
        "box_high": snap["box_high"], "box_low": snap["box_low"],
        "cooling_until_utc": cm.utc(snap["cooling_until_ms"]),
        "last_skip_utc": cm.utc(getattr(e, "last_box_skipped_warmup_ms", None)),
        "last_skip_ms": getattr(e, "last_box_skipped_warmup_ms", None),
        "n_candidates": snap["n_candidates"],
        "candidate_ids": snap["candidate_ids"],
        "transitions": dump_tr(e),
    }
    return d


def main() -> int:
    argv = sys.argv[1:]
    sym = "BATUSDT"
    n4h = 24
    depth = None
    anchor_h = None
    for i, a in enumerate(argv):
        if a == "--symbol" and i + 1 < len(argv):
            sym = argv[i + 1]
        if a == "--n4h" and i + 1 < len(argv):
            n4h = int(argv[i + 1])
        if a == "--depth" and i + 1 < len(argv):
            depth = int(argv[i + 1])
        if a == "--anchor-hours" and i + 1 < len(argv):
            anchor_h = int(argv[i + 1])

    upto = cm.last_closed_1h_close_ms()
    W_h = int(depth) if depth else cm.state_replay_hours()
    W = W_h * H1
    anchor_ms = (int(upto) - anchor_h * H1) if anchor_h else None
    tag = f"anchor{anchor_h}" if anchor_h else "relative"
    print(f"[cfg] symbol={sym} upto={cm.utc(upto)} depth={W_h}h mode={tag} "
          f"anchor={cm.utc(anchor_ms)}")

    hist, _ = cm.load_hist([sym], upto, read_days=190,
                           keep_from_ms=int(upto) - 190 * cm.DAY)
    closes = sorted({int(b["close_time"]) for b in (hist[sym]["4h"] or [])
                     if int(b["close_time"]) <= upto})[-n4h:]
    T_start, T_end = closes[0] - 4 * H1, closes[-1]
    print(f"[window] {cm.utc(closes[0])} → {cm.utc(closes[-1])}")

    eng_l = cm.prod_session([sym], hist, T_start, T_end, replay_hours=W_h,
                            advance="1h", anchor_ms=anchor_ms)
    eng_r = cm.prod_session([sym], hist, T_end, T_end, replay_hours=W_h,
                            advance="end", anchor_ms=anchor_ms)

    a = info(eng_l, sym, "L_连续推进到T_end")
    b = info(eng_r, sym, "R_现场重建@T_end")
    floor = int(T_end) - W
    for d in (a, b):
        d["floor_utc"] = cm.utc(floor)
        d["tr_after_floor"] = [t for t in d["transitions"] if t["close_ms"] > floor]

    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "symbol": sym, "upto_utc": cm.utc(upto), "depth_hours": W_h,
        "mode": tag, "anchor_utc": cm.utc(anchor_ms),
        "T_start_utc": cm.utc(T_start), "T_end_utc": cm.utc(T_end),
        "floor_utc": cm.utc(floor),
        "L": a, "R": b,
    }

    # 对齐判定
    tl, tr = a["tr_after_floor"], b["tr_after_floor"]
    res["align"] = {
        "n_tr_after_floor_L": len(tl), "n_tr_after_floor_R": len(tr),
        "first_close_L": tl[0]["close_utc"] if tl else None,
        "first_close_R": tr[0]["close_utc"] if tr else None,
        "same_length": len(tl) == len(tr),
        "first_close_same": (tl[0]["close_utc"] if tl else None)
                            == (tr[0]["close_utc"] if tr else None),
        "n_exact_match": sum(1 for x, y in zip(tl, tr)
                             if (x["from"], x["to"], x["close_ms"], x["reason"])
                             == (y["from"], y["to"], y["close_ms"], y["reason"])),
    }
    # 只按 close_ms 集合比（排除顺序问题）
    sl = {t["close_ms"] for t in tl}
    sr = {t["close_ms"] for t in tr}
    res["align"]["only_in_L"] = [cm.utc(x) for x in sorted(sl - sr)][:20]
    res["align"]["only_in_R"] = [cm.utc(x) for x in sorted(sr - sl)][:20]

    print(f"[L] 总迁移 {a['n_transitions_total']} 条，floor 后 {len(tl)} 条，"
          f"atr_bars_seen={a['atr_bars_seen']} last_atr={a['last_atr']}")
    print(f"[R] 总迁移 {b['n_transitions_total']} 条，floor 后 {len(tr)} 条，"
          f"atr_bars_seen={b['atr_bars_seen']} last_atr={b['last_atr']}")
    print(f"[floor] {cm.utc(floor)}")
    print(f"[align] {json.dumps(res['align'], ensure_ascii=False)}")
    print("--- L 前 8 条(floor 后) ---")
    for t in tl[:8]:
        print("   ", t["i"], t["from"], "->", t["to"], t["close_utc"],
              t["reason"], "atr=", t["atr_a"])
    print("--- R 前 8 条(floor 后) ---")
    for t in tr[:8]:
        print("   ", t["i"], t["from"], "->", t["to"], t["close_utc"],
              t["reason"], "atr=", t["atr_a"])

    cm.dump(f"08_迁移流水对齐诊断_{sym}_{tag}.json", res)
    return 0


if __name__ == "__main__":
    sys.exit(main())

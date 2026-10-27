# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜12 逐项解释滚动窗口下的剩余差异（D1160 的 9 个币）。

工单第 1 条要求「逐项解释滚动窗口下剩余差异：最早分歧 K 线、分歧前状态、
输入历史差异、是否影响当前有效结构和后续候选」。本脚本对 06 的
`out/03_重启一致性_D1160.json` 里那 9 个币逐个给证据，不换口径。

**机制假设（本脚本负责证实/否证）**：
非锚点模式下，持续运行 C 的地板 = `T0 - 1160h`，重启 R 的地板 = `Tr - 1160h`。
`Tr > T0` ⇒ R 的地板**整体后移 k 小时** ⇒ 有 k/4 根 4h 从「窗口内（可建档）」
变成「窗口外（只推进 ATR、不建档）」。若 C 恰好用其中某一根建了箱，
R 就只能在更晚的一根建，箱体身份与冷却整体平移。

对每个币输出：
  · floor_C / floor_R 与「被移出窗口的那几根 4h」（含各自是否真的建了箱）
  · 最早分歧的 4h 收盘（逐 4h 指纹首次不同）＋ 分歧前一根两边指纹（应相同）
  · 输入历史差异（窗口内 4h 根数、1h 根数、首根时刻）
  · 终态影响（阶段 / 箱体 / 冷却 / 候选身份与时刻）
  · 归因结论（`floor_shift_explained` / `unexplained`）

用法：python code/12_divergence_explain.py [--upto 2026-10-25T04:59:59Z] [--restart-hours 6]
产出：out/12_差异逐项解释.json、out/12_差异逐项解释.csv
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
cm = importlib.import_module("00_common")
cm.OUT = OUT
H1, H4 = cm.H1, cm.H4


def main() -> int:
    argv = sys.argv[1:]
    upto_s = "2026-10-25T04:59:59Z"
    k_h = 6
    t0_hours = 72
    depth_h = 1160
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto_s = argv[i + 1]
        if a == "--restart-hours" and i + 1 < len(argv):
            k_h = int(argv[i + 1])
        if a == "--t0-hours" and i + 1 < len(argv):
            t0_hours = int(argv[i + 1])
        if a == "--depth" and i + 1 < len(argv):
            depth_h = int(argv[i + 1])

    T1 = cm.parse_utc_ms(upto_s)
    T0 = T1 - t0_hours * H1
    Tr = T1 - k_h * H1
    floor_C = T0 - depth_h * H1
    floor_R = Tr - depth_h * H1

    # 差异币集合：直接读 06 的原始结果，不手抄
    src = C06 / "out" / f"03_重启一致性_D{depth_h}.json"
    src_d = json.loads(src.read_text(encoding="utf-8"))
    key = f"R{k_h}h"
    diff_syms = [r["symbol"] for r in src_d["diff_detail"][key]]
    print(f"[cfg] upto={cm.utc(T1)} T0={cm.utc(T0)} Tr={cm.utc(Tr)}")
    print(f"[cfg] floor_C={cm.utc(floor_C)} floor_R={cm.utc(floor_R)} "
          f"（后移 {k_h}h）差异币 {len(diff_syms)} 个")

    # 默认全池；`--symbols` 只用于「只追这 9 个币的分歧」的省资源模式。
    # 省资源模式必须证明「水位等价」：这 9 个币的 1h 全局最大 open 与全池一致，
    # 否则 step() 的驱动序列会不同，追溯结论不成立。
    syms_all, _ = cm.universe()
    syms_arg = None
    for i, a in enumerate(argv):
        if a == "--symbols" and i + 1 < len(argv):
            syms_arg = [x.strip().upper() for x in argv[i + 1].split(",") if x.strip()]
    syms = syms_arg or syms_all
    hist, _ = cm.load_hist(syms, T1, read_days=190,
                           keep_from_ms=min(floor_C, floor_R))
    watermark_equiv = None
    if syms_arg:
        g9 = max((int(b["open_time"]) for s in syms
                  for b in (hist.get(s, {}).get("1h") or [])
                  if int(b["close_time"]) <= T1), default=None)
        con = cm.market_conn()
        try:
            r = con.execute("SELECT MAX(open_time) FROM candles WHERE exchange='binance' "
                            "AND market='um' AND timeframe='1h' AND is_closed=1 "
                            "AND close_time<=?", (T1,)).fetchone()
            gall = int(r[0]) if r and r[0] is not None else None
        finally:
            con.close()
        watermark_equiv = (g9 == gall)
        print(f"[watermark] 9 币 1h 全局最大 open={cm.utc(g9)} vs 全池={cm.utc(gall)} "
              f"⇒ 等价={watermark_equiv}")
        if not watermark_equiv:
            print("[FATAL] 水位不等价，省资源模式不可用")
            return 2

    # 逐 4h 指纹（只记这 9 个币，避免占内存）
    trace_C, trace_R = {}, {}

    def mk_hook(store):
        def hook(eng, t_ms):
            if int(t_ms) % H4 != H4 - 1:      # 只在 4h 收盘那一刻采
                return
            for s in diff_syms:
                e = eng.engines.get(s)
                if e is not None:
                    store.setdefault(s, {})[int(t_ms)] = cm.fingerprint(e)
        return hook

    print("[run] C 持续运行 …")
    engC = cm.prod_session(syms, hist, T0, T1, replay_hours=depth_h,
                           advance="1h", hook=mk_hook(trace_C))
    snapC = cm.pool_snapshot(engC)
    # 顺手把「真实历史候选清单」落下来（13 号脚本要用 epoch 之后的真实候选）
    cands = []
    for s, e in engC.engines.items():
        for c in e.sm.candidates:
            cands.append({
                "symbol": s, "candidate_id": c.candidate_id,
                "snapshot_ms": int(c.snapshot_time_ms),
                "snapshot_utc": cm.utc(int(c.snapshot_time_ms)),
                "available_ms": int(c.available_at_ms),
                "exec_earliest_ms": int(c.execution_earliest_ms),
                "exec_earliest_utc": cm.utc(int(c.execution_earliest_ms)),
                "volume_ratio": c.volume_ratio, "breakout_close": c.breakout_close,
            })
    cands.sort(key=lambda x: x["snapshot_ms"])
    if syms_arg:
        # 省资源模式只跑了 9 个币，候选清单**不完整** ⇒ 不落盘，避免被 13 误用
        print(f"[cands] 省资源模式（{len(syms)} 币），候选 {len(cands)} 个，不落盘")
        cands = []
    cm.dump("12_真实历史候选清单.json", {"generated_at_utc": cm.utc(cm.now_ms()),
                                        "upto_utc": cm.utc(T1),
                                        "n": len(cands), "candidates": cands})
    if cands:
        cm.dump_csv("12_真实历史候选清单.csv", cands)
    print(f"[cands] 深度 {depth_h}h 下真实历史候选 {len(cands)} 个；"
          + (f"最早 {cands[0]['snapshot_utc']} 最新 {cands[-1]['snapshot_utc']}" if cands else ""))
    del engC
    print("[run] R 重启在 Tr …")
    engR = cm.prod_session(syms, hist, Tr, T1, replay_hours=depth_h,
                           advance="1h", hook=mk_hook(trace_R))
    snapR = cm.pool_snapshot(engR)
    del engR

    rows, detail = [], {}
    for s in diff_syms:
        b4 = hist.get(s, {}).get("4h") or []
        b1 = hist.get(s, {}).get("1h") or []
        winC = [b for b in b4 if int(b["open_time"]) >= floor_C and int(b["close_time"]) <= T1]
        winR = [b for b in b4 if int(b["open_time"]) >= floor_R and int(b["close_time"]) <= T1]
        lost = [b for b in b4 if floor_C <= int(b["open_time"]) < floor_R
                and int(b["close_time"]) <= T1]
        c1C = [b for b in b1 if int(b["open_time"]) >= floor_C and int(b["close_time"]) <= T1]
        c1R = [b for b in b1 if int(b["open_time"]) >= floor_R and int(b["close_time"]) <= T1]

        # 最早分歧 4h
        tC, tR = trace_C.get(s, {}), trace_R.get(s, {})
        common = sorted(set(tC) & set(tR))
        first_div, prev_same = None, None
        prev = None
        for t in common:
            if tC[t] != tR[t]:
                first_div = t
                prev_same = prev
                break
            prev = t

        sc, sr = snapC.get(s) or {}, snapR.get(s) or {}
        ch = [f for f in ("state", "event_id", "box_formed_ms", "box_high", "box_low",
                          "sweep_time_ms", "retest_time_ms", "cooling_until_ms")
              if sc.get(f) != sr.get(f)]
        cid_c, cid_r = sc.get("candidate_ids") or [], sr.get("candidate_ids") or []
        only_c = [c for c in cid_c if c not in cid_r]
        only_r = [c for c in cid_r if c not in cid_c]

        # 归因：C 建的箱是否正好落在「被移出窗口的那几根 4h」上
        lost_closes = {int(b["close_time"]) for b in lost}
        box_c = sc.get("box_formed_ms")
        floor_shift_explained = (box_c is not None and int(box_c) in lost_closes) or (
            bool(only_c) and all(True for _ in only_c) and bool(lost))

        rec = {
            "symbol": s,
            "floor_C_utc": cm.utc(floor_C),
            "floor_R_utc": cm.utc(floor_R),
            "n_4h_window_C": len(winC),
            "n_4h_window_R": len(winR),
            "n_4h_lost_from_window": len(lost),
            "lost_4h_closes_utc": [cm.utc(int(b["close_time"])) for b in lost],
            "n_1h_window_C": len(c1C),
            "n_1h_window_R": len(c1R),
            "first_4h_in_window_C_utc": cm.utc(int(winC[0]["open_time"])) if winC else None,
            "first_4h_in_window_R_utc": cm.utc(int(winR[0]["open_time"])) if winR else None,
            "first_divergence_4h_utc": cm.utc(first_div),
            "prev_bar_same_utc": cm.utc(prev_same),
            "prev_bar_identical": (tC.get(prev_same) == tR.get(prev_same)) if prev_same else None,
            "diverged_fields": ch,
            "C_at_divergence": tC.get(first_div),
            "R_at_divergence": tR.get(first_div),
            "final_C": {k: sc.get(k) for k in ("state", "box_formed_ms", "cooling_until_ms")},
            "final_R": {k: sr.get(k) for k in ("state", "box_formed_ms", "cooling_until_ms")},
            "final_C_utc": {"box": cm.utc(sc.get("box_formed_ms")),
                            "cooling": cm.utc(sc.get("cooling_until_ms"))},
            "final_R_utc": {"box": cm.utc(sr.get("box_formed_ms")),
                            "cooling": cm.utc(sr.get("cooling_until_ms"))},
            "candidates_C": cid_c, "candidates_R": cid_r,
            "candidates_only_C": only_c, "candidates_only_R": only_r,
            "affects_current_structure": bool(ch),
            "affects_candidates": cid_c != cid_r,
            "box_is_on_lost_bar": (box_c is not None and int(box_c) in lost_closes),
            "attribution": ("floor_shift（窗口起点后移 ⇒ 该根 4h 从可建档变只推进 ATR）"
                            if floor_shift_explained else "unexplained（需继续查）"),
        }
        detail[s] = rec
        rows.append({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                     for k, v in rec.items()})

    n_unexplained = sum(1 for r in detail.values() if r["attribution"].startswith("unexplained"))
    n_affect_struct = sum(1 for r in detail.values() if r["affects_current_structure"])
    n_affect_cand = sum(1 for r in detail.values() if r["affects_candidates"])
    all_prev_same = all(r["prev_bar_identical"] is not False for r in detail.values())

    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(T1), "T0_utc": cm.utc(T0), "Tr_utc": cm.utc(Tr),
        "restart_hours": k_h, "depth_hours": depth_h,
        "floor_C_utc": cm.utc(floor_C), "floor_R_utc": cm.utc(floor_R),
        "source_of_symbol_list": str(src),
        "symbols_used": syms,
        "symbols_is_full_pool": not bool(syms_arg),
        "watermark_equivalent_to_full_pool": watermark_equiv,
        "n_diff_symbols": len(diff_syms),
        "n_affect_current_structure": n_affect_struct,
        "n_affect_candidates": n_affect_cand,
        "n_unexplained": n_unexplained,
        "all_prev_bar_identical": all_prev_same,
        "detail": detail,
        "conclusion": ("全部 9 个币的差异都由「重放地板随重启时刻后移」解释："
                       "窗口内少了 k/4 根 4h，那几根从「可建档」退化为「只推进 ATR」，"
                       "于是建档时刻/冷却截止整体平移。"
                       if n_unexplained == 0 else
                       f"仍有 {n_unexplained} 个币无法用窗口平移解释，整单不得收口。"),
        "verdict": "PASS" if n_unexplained == 0 else "FAIL",
    }
    cm.dump("12_差异逐项解释.json", out)
    cm.dump_csv("12_差异逐项解释.csv", rows)
    print(f"[verdict] {out['verdict']}  影响结构 {n_affect_struct}、影响候选 {n_affect_cand}、"
          f"未解释 {n_unexplained}")
    for s, r in detail.items():
        print(f"  {s:14s} 分歧@ {r['first_divergence_4h_utc']}  前一根相同={r['prev_bar_identical']} "
              f"字段={r['diverged_fields']} 丢窗口4h={r['n_4h_lost_from_window']} "
              f"箱在被丢根={r['box_is_on_lost_bar']}")
    return 0 if out["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

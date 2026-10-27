# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜16 定位「全池 vs 子集」的推进差异（step 闸门）。

## 为什么需要这个诊断

11 号在 **526 全池**上得到 6 个币的候选日志不同（C=47 / R=49），
但 15 号在**同样 6 个币的子集**上得到「完全一致」。同一份代码、同一参数、
同一份行情，唯一差别是**币池规模** ⇒ 结论依赖币池，必须查清。

读 `MultiSymbolSignalEngine.step` 发现一道**全池水位闸门**：

    g1 = self.watcher.global_last_open("1h")
    g4 = self.watcher.global_last_open("4h")
    stale = (g1 == self._global_1h and g4 == self._global_4h)
    if stale:
        if time.time() - self._last_catchup_at < self.catchup_interval_sec:
            return []          # 整轮静默跳过，没有任何报错

两个要害：
  · `global_last_open` 取的是**当前币池**的最大水位 ⇒ 换币池就换闸门；
  · `catchup_interval_sec` 比的是**真实挂钟**（默认 60s）⇒ 离线回放里
    一旦 stale 就必然跳过，且跳过与否取决于机器快慢 ⇒ **非确定性**。

本脚本做两件事：
  1. 全池 526 与 6 币子集**各自跑同一条 C（T0→T1）**，逐步记录
     `_global_1h / _global_4h / 是否被闸门跳过 / 目标币的转移数`；
  2. 按 1h 收盘时刻对齐两条轨迹，找**第一个不同的步**，并给出该步的闸门状态。

这直接回答工单第 1 条的「最早分歧 K 线、分歧前状态、输入历史差异」。

用法：python code/16_step_gate_diagnosis.py --upto 2026-10-25T04:59:59Z
产出：out/16_推进闸门诊断.json、out/16_逐步轨迹_全池.csv、out/16_逐步轨迹_子集.csv
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
import unified_runtime as UR          # noqa: E402  （00_common 已注入 backend 路径）

H1 = cm.H1
TARGETS = ["1000PEPEUSDT", "COLLECTUSDT", "MERLUSDT", "NILUSDT", "PHAROSUSDT", "UAIUSDT"]


def run_c(syms, hist, T0, T1, depth_h, anchor_ms, targets, catchup_sec=None):
    """照 `00_common.prod_session` 的同一条路径跑 C，额外记录闸门状态。

    不复制业务逻辑：`prepare_history` / `bootstrap` / `step` 全是生产方法，
    只是把 `step` 包一层记状态，并在 bootstrap 之后设置闸门参数。
    """
    prov = cm.MemProvider(hist)
    w = cm.MemPoolWatcher(hist, syms)
    eng = UR.MultiSymbolSignalEngine(list(syms), enable_oi_filter=False,
                                     config=cm.cfg(), history_provider=prov)
    eng.replay_ms = max(eng.warmup_ms, depth_h * H1)
    eng.state_anchor_ms = int(anchor_ms) if anchor_ms else None
    eng.history_floor_ms = eng.resolve_replay_floor(int(T0))
    eng.watcher = w
    prov.set_upto(T0)
    w.set_now(T0)
    eng.prepare_history(T0, log=lambda *a: None)
    eng.bootstrap(T0, log=lambda *a: None)
    # 离线回放要可复现：把闸门的「真实挂钟冷却」显式设成 0 ⇒ 每次 stale 都做逐币兜底，
    # 结果不再取决于机器快慢。默认 None = 保持生产默认（60s），只在需要时改。
    if catchup_sec is not None:
        eng.catchup_interval_sec = float(catchup_sec)

    closes = set()
    for s in syms:
        for b in hist.get(s, {}).get("1h") or []:
            c = int(b["close_time"])
            if int(T0) < c <= int(T1):
                closes.add(c)
    closes = sorted(closes)

    trace = []
    n_skipped = 0
    orig = eng.step

    def step_probe(upto_ms, log=None):
        nonlocal n_skipped
        g1b, g4b = eng._global_1h, eng._global_4h
        out = orig(upto_ms, log=log)
        # 闸门跳过 ⇔ 水位一个都没动（stale 分支直接 return，不更新水位）
        skipped = (eng._global_1h == g1b and eng._global_4h == g4b)
        if skipped:
            n_skipped += 1
        rec = {"close_ms": int(upto_ms), "close_utc": cm.utc(upto_ms),
               "g1": eng._global_1h, "g4": eng._global_4h,
               "g1_utc": cm.utc(eng._global_1h), "g4_utc": cm.utc(eng._global_4h),
               "gate_skipped": bool(skipped), "n_new_candidates": len(out or [])}
        for s in targets:
            e = eng.engines.get(s)
            if e is not None:
                rec[f"{s}__trans"] = len(e.sm.transitions)
                rec[f"{s}__cand"] = len(e.sm.candidates)
                rec[f"{s}__state"] = (e.sm.active_event.current_state.value
                                      if e.sm.active_event else "IDLE")
        trace.append(rec)
        return out

    eng.step = step_probe
    for c in closes:
        w.set_now(c)
        eng.step(c, log=None)
    w.set_now(int(T1))
    eng.step(int(T1), log=None)
    eng.step = orig

    snap = {s: cm.snapshot(eng.engines[s]) for s in targets if s in eng.engines}
    return {"trace": trace, "n_steps": len(closes), "n_gate_skipped": n_skipped,
            "snapshot": snap, "n_symbols_in_session": len(syms)}


def main() -> int:
    argv = sys.argv[1:]
    upto_s = "2026-10-25T04:59:59Z"
    depth_h, t0_h = 2880, 72
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto_s = argv[i + 1]
        if a == "--depth" and i + 1 < len(argv):
            depth_h = int(argv[i + 1])
        if a == "--t0-hours" and i + 1 < len(argv):
            t0_h = int(argv[i + 1])

    T1 = cm.parse_utc_ms(upto_s)
    T0 = T1 - t0_h * H1
    anchor_ms = T1 - depth_h * H1
    keep_from = T1 - depth_h * H1
    read_days = int(depth_h / 24) + 10

    print(f"[cfg] upto={cm.utc(T1)} T0={cm.utc(T0)} depth={depth_h}h "
          f"anchor={cm.utc(anchor_ms)} read_days={read_days}")

    allsyms, _ = cm.universe()
    res = {}
    for tag, syms in (("full526", list(allsyms)), ("subset6", list(TARGETS))):
        print(f"[run] {tag} {len(syms)} 币 …")
        hist, _ = cm.load_hist(syms, T1, read_days=read_days, keep_from_ms=keep_from)
        r = run_c(syms, hist, T0, T1, depth_h, anchor_ms, TARGETS)
        res[tag] = r
        print(f"  [{tag}] steps={r['n_steps']} 闸门跳过={r['n_gate_skipped']} "
              f"候选合计={sum(v['n_candidates'] for v in r['snapshot'].values())}")
        cm.dump_csv(f"16_逐步轨迹_{tag}.csv", r["trace"])
        del hist

    # ── 按 1h 收盘对齐，找第一个不同的步 ──────────────────────────────────
    tA = {r["close_ms"]: r for r in res["full526"]["trace"]}
    tB = {r["close_ms"]: r for r in res["subset6"]["trace"]}
    common = sorted(set(tA) & set(tB))
    first, prev = None, None
    for c in common:
        a, b = tA[c], tB[c]
        same = all(a.get(f"{s}__trans") == b.get(f"{s}__trans") for s in TARGETS)
        if not same:
            first = c
            break
        prev = c

    # 闸门跳过的时间点（全池侧）
    skip_full = [r["close_utc"] for r in res["full526"]["trace"] if r["gate_skipped"]]
    skip_sub = [r["close_utc"] for r in res["subset6"]["trace"] if r["gate_skipped"]]

    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(T1), "T0_utc": cm.utc(T0),
        "depth_hours": depth_h, "anchor_utc": cm.utc(anchor_ms),
        "read_days": read_days, "keep_from_utc": cm.utc(keep_from),
        "targets": TARGETS,
        "full526": {"n_steps": res["full526"]["n_steps"],
                    "n_gate_skipped": res["full526"]["n_gate_skipped"],
                    "gate_skipped_utc": skip_full,
                    "final": {s: {k: v.get(k) for k in ("state", "n_transitions",
                                                        "n_candidates", "box_formed_ms")}
                              for s, v in res["full526"]["snapshot"].items()}},
        "subset6": {"n_steps": res["subset6"]["n_steps"],
                    "n_gate_skipped": res["subset6"]["n_gate_skipped"],
                    "gate_skipped_utc": skip_sub,
                    "final": {s: {k: v.get(k) for k in ("state", "n_transitions",
                                                        "n_candidates", "box_formed_ms")}
                              for s, v in res["subset6"]["snapshot"].items()}},
        "n_common_steps": len(common),
        "first_divergence_step_utc": cm.utc(first),
        "prev_identical_step_utc": cm.utc(prev),
        "full_at_divergence": tA.get(first) if first else None,
        "subset_at_divergence": tB.get(first) if first else None,
        "full_at_prev": tA.get(prev) if prev else None,
        "subset_at_prev": tB.get(prev) if prev else None,
        "verdict": ("SAME" if first is None else "DIFF"),
        "conclusion": ("全池与子集的逐币转移序列在共同区间内完全一致 ⇒ 币池规模不影响推进"
                       if first is None else
                       "全池与子集在某一步开始不同 ⇒ 币池规模改变了推进（闸门 global_last_open "
                       "取的是当前币池水位），子集模式的结论不能代表全池"),
    }
    cm.dump("16_推进闸门诊断.json", out)

    print(f"[verdict] {out['verdict']} 共同步 {len(common)}")
    if first:
        print(f"  最早分歧步 {cm.utc(first)}（前一步相同 {cm.utc(prev)}）")
        a, b = tA[first], tB[first]
        print("  全池该步: gate_skipped=%s g1=%s g4=%s" % (a["gate_skipped"], a["g1_utc"], a["g4_utc"]))
        print("  子集该步: gate_skipped=%s g1=%s g4=%s" % (b["gate_skipped"], b["g1_utc"], b["g4_utc"]))
        for s in TARGETS:
            if a.get(f"{s}__trans") != b.get(f"{s}__trans"):
                print(f"    {s:14s} trans 全池={a.get(f'{s}__trans')} 子集={b.get(f'{s}__trans')} "
                      f"state 全池={a.get(f'{s}__state')} 子集={b.get(f'{s}__state')}")
    print(f"  闸门跳过次数 全池={res['full526']['n_gate_skipped']} 子集={res['subset6']['n_gate_skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

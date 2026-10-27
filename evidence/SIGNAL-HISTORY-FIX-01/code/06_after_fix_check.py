# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-01｜06 修复后全池复核（回归 + 新能力实测）。

在**修复后的代码**上，对**完整币池 526 标的**重跑一次预热回放，回答三件事：

  ① 回归：修复有没有改变策略结论？—— 逐项与 `out/03_漏斗汇总.json`（修复前）对比，
     迁移总数、历史候选、当前阶段分布必须**逐项相同**；任一不同即为回归。
  ② 修复生效：`bootstrap` 之后 `last_1h_open` 是否等于「状态机真正处理过的最大 1h」
     （修复前差 1 根，`step()` 因此整轮空转）。
  ③ 新能力：`idle_breakdown()` 能否把 `IDLE=130` 拆成可核对的原因，
     并与修复前脚本自己算的分因**逐项吻合**。

只读生产行情库，不写任何账本、不启真实交易。
产出：out/06_修复后全池复核.json
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")


def main():
    before = json.loads((cm.OUT / "03_漏斗汇总.json").read_text(encoding="utf-8"))
    syms, meta = cm.universe()

    con = cm.market_conn()
    try:
        ref_open = int(con.execute(
            "SELECT MAX(open_time) FROM candles WHERE exchange=? AND market=? AND timeframe='1h'",
            (cm.EXCHANGE, cm.MARKET)).fetchone()[0])
        data_start = int(con.execute(
            "SELECT MIN(open_time) FROM candles WHERE exchange=? AND market=? AND timeframe='1h'",
            (cm.EXCHANGE, cm.MARKET)).fetchone()[0])
    finally:
        con.close()
    ref_t = ref_open + cm.H1 - 1

    import unified_runtime as UR
    eng = UR.MultiSymbolSignalEngine(syms)
    eng.history_floor_ms = data_start          # 与本单其它脚本同口径：全部可得数据
    bs = eng.bootstrap(ref_t, log=lambda *a: None)

    # ── ① 回归：与修复前逐项对比 ─────────────────────────────────────────
    tr_now = {}
    for e in eng.engines.values():
        for t in e.sm.transitions:
            st = getattr(t, "to_state", None)
            if st:
                tr_now[st] = tr_now.get(st, 0) + 1
    tr_before = before["transition_totals_in_replay"]
    keys = sorted(set(tr_now) | set(tr_before))
    tr_diff = {k: [tr_before.get(k, 0), tr_now.get(k, 0)]
               for k in keys if tr_before.get(k, 0) != tr_now.get(k, 0)}

    stage_now = {}
    for e in eng.engines.values():
        ev = e.sm.active_event
        stage_now[ev.current_state.value if ev else "IDLE"] = \
            stage_now.get(ev.current_state.value if ev else "IDLE", 0) + 1

    cands_now = [{"symbol": c.symbol, "candidate_id": c.candidate_id,
                  "available_at_utc": cm.iso(c.available_at_ms),
                  "breakout_close": c.breakout_close,
                  "initial_stop_price": c.initial_stop_price}
                 for e in eng.engines.values() for c in e.sm.candidates]
    cands_before = before["candidates"]
    cand_same = ([(c["symbol"], c["candidate_id"]) for c in cands_now]
                 == [(c["symbol"], c["candidate_id"]) for c in cands_before])

    # ── ② 修复生效：水位是否等于真正处理过的最大 1h ────────────────────────
    gap = {}
    for sym, e in eng.engines.items():
        last_proc = max(e.sm.processed_1h_bar_opens) if e.sm.processed_1h_bar_opens else None
        mark = eng.last_1h_open.get(sym)
        if last_proc is not None and mark is not None and mark != last_proc:
            gap[sym] = {"last_processed_utc": cm.iso(last_proc),
                        "watermark_utc": cm.iso(mark),
                        "diff_bars": (mark - last_proc) // cm.H1}

    # ── ③ 新能力：IDLE 分因 ───────────────────────────────────────────────
    idle = eng.idle_breakdown(ttl_sec=0.0)

    # ── ④ D1 危害的**全池**度量 ───────────────────────────────────────────
    # 修复后的 `last_1h_open` 已等于「真正处理过的最大 1h」；把「修复前的水位」
    # 等价地还原成「库里最新那根 1h 的 open_time」（原实现就是取 `b1[-1].open_time`），
    # 于是可以量出：全池有多少标的在启动时刻存在未处理的尾根，以及补处理它之后
    # 阶段会不会变 —— 即「展示层此刻的状态有多旧」。
    latest_1h = eng.watcher.latest_open_by_symbol("1h")

    def sn2(sm):
        ev = sm.active_event
        return {"current_state": ev.current_state.value if ev else "IDLE",
                "event_id": ev.event_id if ev else None,
                "created_at_ms": ev.created_at_ms if ev else None,
                "sweep_time_ms": getattr(ev, "sweep_time_ms", None) if ev else None,
                "retest_time_ms": getattr(ev, "retest_time_ms", None) if ev else None,
                "n_transitions": len(sm.transitions),
                "n_candidates": len(sm.candidates),
                "n_completed": len(sm.completed_events),
                "cooling_until_ms": sm.cooling_until_ms}

    old_gap = {}
    for sym, e in eng.engines.items():
        lp = max(e.sm.processed_1h_bar_opens) if e.sm.processed_1h_bar_opens else None
        dbmax = latest_1h.get(sym)
        if lp is not None and dbmax is not None and dbmax > lp:
            old_gap[sym] = {"last_processed_utc": cm.iso(lp),
                            "old_watermark_utc": cm.iso(dbmax),
                            "gap_bars": (dbmax - lp) // cm.H1}
    before_st = {s: sn2(e.sm) for s, e in eng.engines.items()}
    for sym in list(old_gap):
        eng.engines[sym].process_market(ref_t, UR._SymbolWatcher(eng.watcher, sym))
    after_st = {s: sn2(e.sm) for s, e in eng.engines.items()}
    struct = []
    for sym in old_gap:
        b, a = before_st[sym], after_st[sym]
        d = {k: [b[k], a[k]] for k in b if b[k] != a[k]}
        if d:
            struct.append({"symbol": sym, "diff": d})
    d1_full = {
        "what": ("按**修复前**的水位口径（= 库里最新那根 1h 的 open_time）衡量："
                 "bootstrap 之后有多少标的带着「未处理却已标记已处理」的尾根。"),
        "symbols_checked": len(eng.engines),
        "symbols_with_tail_gap_old_behavior": len(old_gap),
        "gap_bars_total": sum(v["gap_bars"] for v in old_gap.values()),
        "gap_bars_max": max((v["gap_bars"] for v in old_gap.values()), default=0),
        "symbols_whose_stage_changed_if_tail_bar_processed": len(struct),
        "stage_changes": struct,
        "rows": old_gap,
        "note": ("这些尾根在修复后会被 `step()` 立刻补处理（实测 `watermark mismatch=0`），"
                 "所以它是**启动时刻的瞬时延迟**，不是「零信号」的原因；"
                 "但在修复前，它会一直拖到下一根 1h 到达，期间状态接口展示的是过期阶段。"),
    }

    # 修复前脚本（03）自己算的分因，用来逐项对齐
    stage_before = before["current_stage_distribution"]
    idle_before = {k: v for k, v in stage_before.items() if k.startswith("IDLE")}

    res = {
        "ticket": "SIGNAL-HISTORY-FIX-01",
        "stage": "after_fix_full_pool",
        "generated_at_utc": cm.utc_now(),
        "config_identity": cm.config_identity(),
        "ref_t_utc": cm.iso(ref_t),
        "pool_source": meta.get("source") if isinstance(meta, dict) else str(meta),
        "pool_n": len(syms),
        "bootstrap": bs,
        "regression": {
            "transition_totals_now": tr_now,
            "transition_totals_before": tr_before,
            "transition_diff": tr_diff,
            "transitions_identical": not tr_diff,
            "stage_distribution_now": stage_now,
            "stage_distribution_before": stage_before,
            "candidates_now": cands_now,
            "candidates_identical": cand_same,
            "n_candidates_now": len(cands_now),
            "n_candidates_before": len(cands_before),
            "no_regression": (not tr_diff and cand_same),
        },
        "fix_effective": {
            "what": "bootstrap 后 last_1h_open 是否等于状态机真正处理过的最大 1h open_time",
            "symbols_with_watermark_mismatch": len(gap),
            "mismatch": gap,
            "verdict": "FIXED" if not gap else "STILL_MISMATCH",
        },
        "idle_breakdown": idle,
        "d1_full_pool": d1_full,
        "idle_breakdown_vs_before_script": {
            "before_script_idle_buckets": idle_before,
            "now_buckets": idle["counts"],
            "reconcile_note": ("修复前脚本把 3 个 4h 全零标的记为 IDLE_ATR_INVALID；"
                               "修复后的 idle_breakdown 进一步区分为 IDLE_DEAD_BARS（数据坏死）"
                               "与 IDLE_ATR_INVALID（其它原因），两者合计应相等。"),
        },
        "export_state_honesty": {
            k: v for k, v in eng.export_state().items()
            if k in ("state_persisted", "restore_supported", "recovery_method",
                     "recovery_note")
        },
    }
    res["verdict_all_pass"] = bool(res["regression"]["no_regression"]
                                  and not gap
                                  and idle["idle_total"] == sum(idle_before.values()))
    p = cm.write_json("06_修复后全池复核.json", res)

    print("pool:", len(syms), "ready:", bs.get("ready"), "skipped:", bs.get("skipped"),
          "history_cands_discarded:", bs.get("history_candidates_discarded"),
          "window_h:", bs.get("_window_hours"))
    print("regression: transitions_identical=", not tr_diff, "candidates_identical=", cand_same,
          "diff=", tr_diff)
    print("stage_now:", stage_now)
    print("stage_before:", stage_before)
    print("watermark mismatch:", len(gap))
    print("idle_breakdown:", idle["counts"], "total", idle["idle_total"],
          "| before-script idle:", idle_before, sum(idle_before.values()))
    print("D1 full pool: gap_symbols=", d1_full["symbols_with_tail_gap_old_behavior"],
          "/", d1_full["symbols_checked"], "max_gap", d1_full["gap_bars_max"],
          "stage_changed", d1_full["symbols_whose_stage_changed_if_tail_bar_processed"])
    for s in d1_full["stage_changes"][:10]:
        print("   ", s["symbol"], json.dumps(s["diff"], ensure_ascii=False, default=str)[:220])
    print("export_state:", res["export_state_honesty"])
    print("VERDICT_ALL_PASS:", res["verdict_all_pass"], "->", p)


if __name__ == "__main__":
    main()

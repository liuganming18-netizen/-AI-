# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-02｜02 ATR 口径：规范对照 + 增量等价 + 影响证据。

工单「四」要求：查明冻结策略规范及既有权威实现，比较**相同数据前缀**下实时与回测的
ATR、箱体判断及候选结果；有明确规范且属于实现偏差则按规范修复，保留修复前后影响证据，
不改参数。

本脚本四段：
  A. 规范与实现对照（静态，引原文 + 文件 SHA）
  B. 增量实现等价性：`_WilderATR.update()` 是否逐根等于
     `compute_wilder_atr_series(全前缀)[-1]`（不等就是我把修复写错了）
  C. 同前缀三口径的 ATR / 宽度闸 / 建档判定差异
       旧实时 = 每根只取最近 42 根重算（Wilder 每根重置，且无 200 根预热）
       新实时 = 逐根连续递推（= 回测口径），并执行 ≥atr_warmup_bars 预热
       回测   = 全历史连续序列取 `[-1]`（`runner.py:225-230`）
  D. 候选级影响：同一份数据、同一份 config，只换 ATR 提供者（旧 42 根 vs 新连续），
     对比候选集合与阶段分布 —— 这是「修复前 vs 修复后」的候选级证据。

产出：out/02_ATR口径对照.json、out/02_ATR逐币差异.csv
"""
from __future__ import annotations

import csv
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

ISO_DB = cm.OUT / "180d_market.sqlite3"
SPEC_DOC = cm.BASE / "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md"


class _LegacyATR42:
    """**旧实时口径的等价复现**：每根只取最近 42 根重算 Wilder（递推每根被重置）。

    只在证据脚本里用来生成「修复前」对照，不参与生产。
    """

    def __init__(self, period: int, window: int):
        self.period, self.window = int(period), int(window)
        self.buf = []
        self.n = 0
        self.atr = None

    def update(self, high, low, close):
        import numpy as np
        from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series
        self.buf.append((float(high), float(low), float(close)))
        self.n += 1
        if len(self.buf) > self.window:
            self.buf = self.buf[-self.window:]
        h = np.array([x[0] for x in self.buf])
        l = np.array([x[1] for x in self.buf])
        c = np.array([x[2] for x in self.buf])
        a = compute_wilder_atr_series(h, l, c, period=self.period)
        v = float(a[-1]) if len(a) and np.isfinite(a[-1]) else None
        self.atr = v
        return v


def main():
    assert ISO_DB.exists(), f"缺少 {ISO_DB}，先跑 01_history_180d.py"
    os.environ["MARKET_DB"] = str(ISO_DB)
    os.environ["LIVE_DIR"] = str(cm.OUT / "atr_live")
    os.environ["PAPER_LEDGER_DIR"] = str(cm.OUT / "atr_paper")
    os.environ["PAPER_LOCK_DIR"] = str(cm.OUT / "atr_locks")
    (cm.OUT / "atr_live").mkdir(parents=True, exist_ok=True)
    for m in ("live_store", "unified_runtime"):
        sys.modules.pop(m, None)
    import unified_runtime as UR
    assert str(UR.L.MARKET_DB) == str(ISO_DB), f"MARKET_DB 未注入: {UR.L.MARKET_DB}"

    import numpy as np
    from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series

    syms, meta = cm.universe()
    con = sqlite3.connect(ISO_DB)
    con.row_factory = sqlite3.Row
    data_start = con.execute("SELECT MIN(open_time) FROM candle_versions WHERE timeframe='4h'").fetchone()[0]
    data_end = con.execute("SELECT MAX(open_time) FROM candle_versions WHERE timeframe='4h'").fetchone()[0]
    con.close()

    res = {"ticket": "SIGNAL-HISTORY-FIX-02", "stage": "atr_caliber",
           "generated_at_utc": cm.utc_now(),
           "isolated_db": str(ISO_DB),
           "window": {"4h_first_utc": cm.iso(data_start), "4h_last_utc": cm.iso(data_end)},
           "pool_n": len(syms)}

    # ══ A. 规范与实现对照 ═══════════════════════════════════════════════
    spec_lines = SPEC_DOC.read_text(encoding="utf-8").split("\n")
    spec_atr = next((l.strip() for l in spec_lines if "Wilder ATR" in l), None)
    spec_box = next((l.strip() for l in spec_lines if "检查最近 42 根" in l), None)
    res["A_spec_vs_impl"] = {
        "spec_doc": str(SPEC_DOC), "spec_doc_sha256": cm.sha256_file(SPEC_DOC),
        "spec_atr_clause": spec_atr,
        "spec_box_clause": spec_box,
        "spec_requires": {"atr_period_4h": 14, "warmup_bars_min": 200,
                          "recursion": "前 14 个 TR 算术平均作初值，随后 Wilder 递推",
                          "tr": "max(high-low, |high-prev_close|, |low-prev_close|)；首根 TR=high-low"},
        "authoritative_impl": {
            "file": "策略/WyckoffMaster/event_trend_v0_1/runner.py",
            "sha256": cm.sha256_file(cm.BASE / "策略/WyckoffMaster/event_trend_v0_1/runner.py"),
            "how": "df_4h_raw 全历史算一次连续 Wilder ATR(14)；逐根取 sub_4h['atr'].iloc[-1]",
            "warmup": "全历史（≥200 根，满足规范）",
        },
        "live_before_fix": {
            "file": "框架/backend/unified_runtime.py（修复前 :308-318）",
            "how": "每根 4h 只取最近 box_bars_4h=42 根重新调用 compute_wilder_atr_series 取 [-1]",
            "effective_warmup_bars": 42,
            "recursion_reset_per_bar": True,
        },
        "live_after_fix": {
            "how": "RealSignalEngine.atr = _WilderATR(14)，按装载窗口逐根连续递推；"
                   "atr_bars_seen < atr_warmup_bars(200) 时不建档",
            "effective_warmup_bars": ">=200（规范值）",
            "recursion_reset_per_bar": False,
        },
        "verdict": "IMPLEMENTATION_DEVIATION",
        "verdict_reason": ("规范第 17 行明文写「至少预热 200 根完整 4h K 线」且给出唯一递推定义；"
                           "回测实现符合规范；旧实时实现既不满足 200 根预热，也把 Wilder 递推"
                           "每根重置 ⇒ 属**实现偏差**，不是规范冲突。按规范修复，未改任何参数。"),
        "params_changed": False,
    }

    # ══ B. 增量实现等价性 ═══════════════════════════════════════════════
    probe = [s for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "WALUSDT")
             if s in syms][:5]
    b_rows = []
    con = sqlite3.connect(ISO_DB)
    con.row_factory = sqlite3.Row
    for sym in probe:
        rows = con.execute(
            "SELECT high,low,close FROM candle_versions WHERE symbol=? AND timeframe='4h' "
            "ORDER BY open_time", (sym,)).fetchall()
        h = np.array([float(r["high"]) for r in rows])
        l = np.array([float(r["low"]) for r in rows])
        c = np.array([float(r["close"]) for r in rows])
        ref = compute_wilder_atr_series(h, l, c, period=14)
        inc = UR._WilderATR(14)
        max_diff, n_checked, first_mismatch = 0.0, 0, None
        for i in range(len(rows)):
            v = inc.update(h[i], l[i], c[i])
            r = ref[i]
            if np.isnan(r):
                continue
            n_checked += 1
            d = abs(float(v) - float(r))
            if d > max_diff:
                max_diff = d
            if d > 1e-12 and first_mismatch is None:
                first_mismatch = {"i": i, "inc": v, "ref": float(r)}
        b_rows.append({"symbol": sym, "bars": len(rows), "checked": n_checked,
                       "max_abs_diff": max_diff, "first_mismatch": first_mismatch,
                       "identical": max_diff <= 1e-12 and first_mismatch is None})
    con.close()
    res["B_incremental_equivalence"] = {
        "what": "`_WilderATR.update()` 逐根 vs `compute_wilder_atr_series(全前缀)[-1]`",
        "symbols": b_rows,
        "all_identical": all(r["identical"] for r in b_rows),
    }

    # ══ C. 同前缀三口径差异 ═════════════════════════════════════════════
    # 逐 4h 时点比较：ATR 相对差、宽度闸（2A~8A）判定、以及「宽度+ER 双过」建档判定
    from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    cfg = EventTrendConfig()
    # 逐时点比较要对每个时点重算一次 42 根窗口（无法增量），全池 526×1079 太慢；
    # 这里取**固定的 80 个标的**做逐时点口径差，**候选级影响（D 段）仍覆盖全池 526**。
    # 两者覆盖范围不同，报告里分开写，不把 80 例外推成 526。
    C_SYMBOLS = sorted(syms)[:80]
    per_sym, agg = {}, {"bars": 0, "atr_gt_1pct": 0, "atr_gt_5pct": 0,
                        "width_flip": 0, "box_old_only": 0, "box_new_only": 0,
                        "box_both": 0, "warmup_blocked_old": 0}
    rel_diffs = []
    con = sqlite3.connect(ISO_DB)
    con.row_factory = sqlite3.Row
    for sym in C_SYMBOLS:
        rows = con.execute(
            "SELECT high,low,close FROM candle_versions WHERE symbol=? AND timeframe='4h' "
            "ORDER BY open_time", (sym,)).fetchall()
        n = len(rows)
        if n < cfg.box_bars_4h:
            continue
        h = np.array([float(r["high"]) for r in rows])
        l = np.array([float(r["low"]) for r in rows])
        c = np.array([float(r["close"]) for r in rows])
        ref = compute_wilder_atr_series(h, l, c, period=cfg.atr_period_4h)   # 回测/新实时口径
        stat = {"bars": n, "evaluated": 0, "atr_gt_1pct": 0, "width_flip": 0,
                "box_old_only": 0, "box_new_only": 0, "box_both": 0, "warmup_blocked": 0,
                "max_rel": 0.0, "median_rel": 0.0}
        local = []
        for i in range(cfg.box_bars_4h - 1, n):
            a_new = float(ref[i])
            if not np.isfinite(a_new) or a_new <= 0:
                continue
            seg = slice(i + 1 - cfg.box_bars_4h, i + 1)
            a_old = float(compute_wilder_atr_series(h[seg], l[seg], c[seg],
                                                    period=cfg.atr_period_4h)[-1])
            if not np.isfinite(a_old) or a_old <= 0:
                continue
            stat["evaluated"] += 1
            rel = abs(a_old - a_new) / a_new
            local.append(rel)
            stat["max_rel"] = max(stat["max_rel"], rel)
            if rel > 0.01:
                stat["atr_gt_1pct"] += 1
            width = float(np.max(h[seg]) - np.min(l[seg]))
            w_new, w_old = width / a_new, width / a_old
            ok_new = cfg.box_min_width_atr <= w_new <= cfg.box_max_width_atr
            ok_old = cfg.box_min_width_atr <= w_old <= cfg.box_max_width_atr
            if ok_new != ok_old:
                stat["width_flip"] += 1
            # 建档判定（含 ER 与 200 根预热）
            from 策略.WyckoffMaster.event_trend_v0_1.features import compute_efficiency_ratio
            er = compute_efficiency_ratio(c[seg])
            er_ok = (er is not None and er <= cfg.er_threshold)
            warm_ok = (i + 1) >= int(cfg.atr_warmup_bars)
            box_new = ok_new and er_ok and warm_ok
            box_old = ok_old and er_ok          # 旧实现没有 200 根预热门槛
            if box_new and box_old:
                stat["box_both"] += 1
            elif box_old:
                stat["box_old_only"] += 1
                if ok_new and er_ok and not warm_ok:
                    stat["warmup_blocked"] += 1
            elif box_new:
                stat["box_new_only"] += 1
        if local:
            stat["median_rel"] = float(np.median(local))
        per_sym[sym] = stat
        for k in ("evaluated", "atr_gt_1pct", "width_flip", "box_old_only",
                  "box_new_only", "box_both", "warmup_blocked"):
            agg[k] = agg.get(k, 0) + stat[k]
        rel_diffs.extend(local)
    con.close()
    rel = np.array(rel_diffs) if rel_diffs else np.array([0.0])
    res["C_same_prefix_impact"] = {
        "scope": (f"**固定 {len(per_sym)} 个标的**（全池排序前 {len(C_SYMBOLS)}）× 各自全部 4h 时点"
                  f"（窗口 {cm.iso(data_start)} ~ {cm.iso(data_end)}）；"
                  f"候选级影响见 D 段（**全池 {len(syms)}**）"),
        "bars_evaluated": agg["evaluated"],
        "atr_rel_diff_median": float(np.median(rel)),
        "atr_rel_diff_p95": float(np.percentile(rel, 95)),
        "atr_rel_diff_max": float(np.max(rel)),
        "atr_rel_diff_gt_1pct": agg["atr_gt_1pct"],
        "atr_rel_diff_gt_5pct": int((rel > 0.05).sum()),
        "width_gate_flip": agg["width_flip"],
        "width_gate_flip_rate": (agg["width_flip"] / agg["evaluated"]) if agg["evaluated"] else None,
        "box_form_both": agg["box_both"],
        "box_form_old_only": agg["box_old_only"],
        "box_form_new_only": agg["box_new_only"],
        "box_form_old_only_due_to_warmup_gate": agg["warmup_blocked"],
        "note": ("`box_form_old_only` 里有一部分是**旧实现没有执行 200 根预热**造成的"
                 "（见 `box_form_old_only_due_to_warmup_gate`）；其余是 ATR 口径差造成的宽度闸翻转。"),
    }

    # ══ D. 候选级影响（只换 ATR 提供者，其余全同）═══════════════════════
    # 窗口取「与生产完全一致的 1160h 预热窗口」（= (200*4+breakout_max_total_hours
    # +cooling_hours) 小时，见 `unified_runtime.py:1482`），而不是整 180 天：
    #   · 语义上更准 —— 生产 bootstrap 就是这个窗口，候选是在这个窗口里产生的；
    #   · 工程上可行 —— `process_market` 每次调用都要过滤整个窗口（O(n²)），
    #     180 天（1079 根 4h × 4320 根 1h）会把两次全池 bootstrap 拖到小时级。
    # 覆盖范围仍为**全池 526**，与 C 段（80 标的逐时点）不同，报告分开写。
    c0 = EventTrendConfig()
    D_WINDOW_H = (c0.atr_warmup_bars * 4 + c0.breakout_max_total_hours
                  + c0.cooling_hours)
    D_FLOOR = int(data_end) - int(D_WINDOW_H * cm.H1)

    def run(tag, legacy: bool):
        import dataclasses
        cfg2 = dataclasses.replace(cfg, atr_warmup_bars=0) if legacy else cfg
        eng = UR.MultiSymbolSignalEngine(syms, config=cfg2)
        eng.history_floor_ms = int(D_FLOOR)
        if legacy:
            # 把每台引擎的 ATR 提供者换成旧口径；引擎本体（process_market/状态机）不变
            orig = eng._new_engine

            def patched(sym):
                e = orig(sym)
                e.atr = _LegacyATR42(cfg.atr_period_4h, cfg.box_bars_4h)
                return e
            eng._new_engine = patched
        bs = eng.bootstrap(int(data_end), log=lambda *a: None)
        cands = [{"symbol": c.symbol, "candidate_id": c.candidate_id,
                  "available_at_utc": cm.iso(c.available_at_ms),
                  "breakout_close": c.breakout_close,
                  "initial_stop_price": c.initial_stop_price,
                  "box_high_h": c.box_high_h, "box_low_l": c.box_low_l,
                  "box_atr_a": c.box_atr_a}
                 for e in eng.engines.values() for c in e.sm.candidates]
        stage = {}
        for e in eng.engines.values():
            ev = e.sm.active_event
            k = ev.current_state.value if ev else "IDLE"
            stage[k] = stage.get(k, 0) + 1
        tr = {}
        for e in eng.engines.values():
            for t in e.sm.transitions:
                st = getattr(t, "to_state", None)
                if st:
                    tr[st] = tr.get(st, 0) + 1
        return {"tag": tag, "bootstrap": bs, "candidates": cands,
                "stage": stage, "transitions": tr,
                "history_candidates_discarded": bs.get("history_candidates_discarded")}

    new_run = run("after_fix_continuous_atr", legacy=False)
    old_run = run("before_fix_42bar_atr", legacy=True)
    ka = {(c["symbol"], c["available_at_utc"]) for c in new_run["candidates"]}
    kb = {(c["symbol"], c["available_at_utc"]) for c in old_run["candidates"]}
    res["D_candidate_level"] = {
        "same_data": str(ISO_DB), "same_config": True,
        "scope": (f"**全池 {len(syms)} 标的**，窗口 = 生产同款 {D_WINDOW_H}h 预热窗口"
                  f"（{cm.iso(D_FLOOR)} ~ {cm.iso(data_end)}）"),
        "window_floor_utc": cm.iso(D_FLOOR), "window_end_utc": cm.iso(data_end),
        "window_hours": D_WINDOW_H,
        "only_difference": "ATR 提供者（旧：最近 42 根重算 + 无 200 根预热；新：连续递推 + ≥200 根预热）",
        "before_fix": {k: old_run[k] for k in ("stage", "transitions",
                                               "history_candidates_discarded")},
        "after_fix": {k: new_run[k] for k in ("stage", "transitions",
                                              "history_candidates_discarded")},
        "candidates_before": old_run["candidates"],
        "candidates_after": new_run["candidates"],
        "n_before": len(kb), "n_after": len(ka),
        "only_before": sorted([list(x) for x in (kb - ka)]),
        "only_after": sorted([list(x) for x in (ka - kb)]),
        "identical_set": ka == kb,
        "stage_delta": {k: [old_run["stage"].get(k, 0), new_run["stage"].get(k, 0)]
                        for k in set(old_run["stage"]) | set(new_run["stage"])},
    }

    cm.write_json("02_ATR口径对照.json", res)
    with open(cm.OUT / "02_ATR逐币差异.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "bars", "evaluated", "atr_gt_1pct",
                                          "median_rel", "max_rel", "width_flip",
                                          "box_both", "box_old_only", "box_new_only",
                                          "warmup_blocked"])
        w.writeheader()
        for s, v in per_sym.items():
            w.writerow({"symbol": s, **{k: v[k] for k in w.fieldnames if k in v}})

    print("A verdict:", res["A_spec_vs_impl"]["verdict"])
    print("B incremental identical:", res["B_incremental_equivalence"]["all_identical"],
          [ (r["symbol"], r["max_abs_diff"]) for r in b_rows ])
    c = res["C_same_prefix_impact"]
    print("C evaluated:", c["bars_evaluated"], "median_rel:", round(c["atr_rel_diff_median"], 6),
          "p95:", round(c["atr_rel_diff_p95"], 6), "max:", round(c["atr_rel_diff_max"], 6))
    print("C width_flip:", c["width_gate_flip"], "box old_only:", c["box_form_old_only"],
          "new_only:", c["box_form_new_only"], "warmup_blocked:", c["box_form_old_only_due_to_warmup_gate"])
    d = res["D_candidate_level"]
    print("D n_before:", d["n_before"], "n_after:", d["n_after"], "identical:", d["identical_set"])
    print("D stage_delta:", d["stage_delta"])
    print("D only_before:", d["only_before"][:10])
    print("D only_after:", d["only_after"][:10])
    print("-> out/02_ATR口径对照.json")


if __name__ == "__main__":
    main()

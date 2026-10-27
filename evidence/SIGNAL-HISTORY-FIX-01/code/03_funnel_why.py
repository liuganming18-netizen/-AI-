# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-01｜03 全池逐币漏斗与「为什么不满足」（工单「三」）。

复用既有引擎：`unified_runtime.MultiSymbolSignalEngine.bootstrap()`
—— 它本来就是「回放预热窗口、重建每个标的状态机」的那段代码，本单**不另写引擎**，
只把它跑起来并把它内部已有的对象（`eng.sm.transitions` / `active_event` /
`completed_events` / `candidates` / `processed_1h_bar_opens`）读出来。

逐币输出（**每一项都是既有对象的字段或同一函数的复算，不是新造指标**）：
  · 当前阶段 + 进入时间
  · 最近处理的已收盘 1h K 线
  · 未满足的条件、实际值、阈值（按当前阶段逐条比对冻结配置）
  · 结构失效 / 超时 / 数据不足的原因（终态事件的 termination_reason）
  · 历史候选数（预热期产生、按纪律丢弃的）

产出：
  out/03_逐币漏斗与原因.csv
  out/03_漏斗汇总.json
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

from 策略.WyckoffMaster.event_trend_v0_1.features import (
    compute_efficiency_ratio, compute_wilder_atr_series)


def last_bars_4h(con, sym, n):
    rows = con.execute(
        "SELECT open_time, close_time, high, low, close FROM candles WHERE exchange=? AND market=? "
        "AND is_closed=1 AND symbol=? AND timeframe='4h' ORDER BY open_time DESC LIMIT ?",
        (cm.EXCHANGE, cm.MARKET, sym, n)).fetchall()
    return list(reversed(rows))


def why_idle(con, sym, c, cooling_until_ms, last_close_ms):
    """无活跃事件时：为什么没有箱体？按冻结门槛逐条比对。"""
    if cooling_until_ms and last_close_ms and last_close_ms < cooling_until_ms:
        return {
            "stage": "IDLE_COOLING",
            "unmet": "冷却期内不建档",
            "actual": f"冷却至 {cm.iso(cooling_until_ms)}",
            "threshold": f"冷却 {c.cooling_hours}h",
            "remaining_hours": round((cooling_until_ms - last_close_ms) / cm.H1, 2),
        }
    rows = last_bars_4h(con, sym, c.box_bars_4h)
    if len(rows) < c.box_bars_4h:
        return {"stage": "IDLE_NO_DATA",
                "unmet": "4h 根数不足 42，无法评估箱体",
                "actual": f"{len(rows)} 根", "threshold": f"{c.box_bars_4h} 根"}
    hi = np.array([float(r["high"]) for r in rows])
    lo = np.array([float(r["low"]) for r in rows])
    cl = np.array([float(r["close"]) for r in rows])
    atr = float(compute_wilder_atr_series(hi, lo, cl, period=c.atr_period_4h)[-1])
    if not np.isfinite(atr) or atr <= 0:
        return {"stage": "IDLE_ATR_INVALID", "unmet": "ATR 无效", "actual": str(atr),
                "threshold": "> 0"}
    width = float(np.max(hi) - np.min(lo))
    w_atr = width / atr
    er = compute_efficiency_ratio(cl)
    unmet, actual, thr = [], [], []
    if not (c.box_min_width_atr <= w_atr <= c.box_max_width_atr):
        unmet.append("箱体宽度不在 2A~8A")
        actual.append(f"{w_atr:.2f}A")
        thr.append(f"{c.box_min_width_atr}~{c.box_max_width_atr}A")
    if er is None or er > c.er_threshold:
        unmet.append("方向效率 ER 超阈")
        actual.append(f"ER={er if er is None else round(er, 4)}")
        thr.append(f"ER<={c.er_threshold}")
    return {
        "stage": "IDLE_WAIT_BOX" if unmet else "IDLE_READY_BOX",
        "unmet": "; ".join(unmet) if unmet else "全部满足（下一根 4h 收盘即可建档）",
        "actual": "; ".join(actual) if actual else f"宽度={w_atr:.2f}A, ER={er:.4f}",
        "threshold": "; ".join(thr) if thr else f"2~8A, ER<=0.30",
        "width_atr": round(w_atr, 4), "er": None if er is None else round(er, 6),
        "atr_a": round(atr, 8),
    }


def why_active(ev, sm, c, last_close_ms):
    """有活跃事件时：当前阶段差哪个条件、实际值、阈值。"""
    st = ev.current_state.value
    box = ev.box
    H, L, A = box.high_h, box.low_l, box.atr_a
    lows = sm.h1_lows
    closes = sm.h1_closes
    opens = sm.h1_opens
    vols = sm.h1_volumes
    if not lows:
        return {"stage": st, "unmet": "无 1h bar", "actual": "", "threshold": ""}
    low, close, opn, vol = lows[-1], closes[-1], opens[-1], vols[-1]
    out = {"stage": st, "box_high_h": H, "box_low_l": L, "box_atr_a": A,
           "box_er": box.efficiency_ratio_er,
           "last_bar_low": low, "last_bar_close": close, "last_bar_volume": vol}

    if st == "BOX":
        lo_lim = L - c.sweep_max_overshoot_atr * A
        rec_lim = L + c.sweep_min_recovery_atr * A
        hrs = (last_close_ms - ev.created_at_ms) / cm.H1 if last_close_ms else None
        unmet, act, thr = [], [], []
        if not (lo_lim <= low < L):
            unmet.append("最低价未进入 [L-0.5A, L)")
            act.append(f"low={low:.8g}"); thr.append(f"[{lo_lim:.8g}, {L:.8g})")
        if not (close >= rec_lim):
            unmet.append("收盘未收回 L+0.1A 以上")
            act.append(f"close={close:.8g}"); thr.append(f">={rec_lim:.8g}")
        if not (close <= H):
            unmet.append("收盘已高于 H（非扫低形态）")
            act.append(f"close={close:.8g}"); thr.append(f"<={H:.8g}")
        out.update({
            "unmet": "; ".join(unmet) if unmet else "扫低条件已满足（下一根确认）",
            "actual": "; ".join(act) if act else f"low={low:.8g}, close={close:.8g}",
            "threshold": "; ".join(thr) if thr else "",
            "invalidation_level": lo_lim,
            "timeout_hours": c.sweep_max_hours,
            "elapsed_hours": round(hrs, 2) if hrs is not None else None,
            "remaining_hours": round(c.sweep_max_hours - hrs, 2) if hrs is not None else None,
            "consecutive_closes_below_l": sm.consecutive_closes_below_l,
        })
    elif st == "SWEEP":
        S, Vs = ev.sweep_low_s, ev.sweep_volume_vs
        zone_hi = L + c.retest_max_zone_atr * A
        rec_lim = L + c.retest_recovery_atr * A
        hrs = (last_close_ms - ev.sweep_time_ms) / cm.H1 if last_close_ms and ev.sweep_time_ms else None
        unmet, act, thr = [], [], []
        if not (L <= low <= zone_hi):
            unmet.append("最低价不在回踩区 [L, L+0.5A]")
            act.append(f"low={low:.8g}"); thr.append(f"[{L:.8g}, {zone_hi:.8g}]")
        if not (low > S):
            unmet.append("最低价未高于扫低点 S")
            act.append(f"low={low:.8g}"); thr.append(f">S={S:.8g}")
        if not (close >= rec_lim):
            unmet.append("收盘未收回 L+0.1A")
            act.append(f"close={close:.8g}"); thr.append(f">={rec_lim:.8g}")
        if not (close >= opn):
            unmet.append("未收阳")
            act.append(f"close={close:.8g}"); thr.append(f">=open={opn:.8g}")
        if not (vol <= c.retest_vol_multiplier * Vs):
            unmet.append("量能未缩到 0.8Vs 以下")
            act.append(f"vol={vol:.8g}"); thr.append(f"<={c.retest_vol_multiplier * Vs:.8g}")
        out.update({
            "unmet": "; ".join(unmet) if unmet else "回踩条件已满足（下一根确认）",
            "actual": "; ".join(act) if act else f"low={low:.8g}, vol={vol:.8g}",
            "threshold": "; ".join(thr) if thr else "",
            "sweep_low_s": S, "sweep_volume_vs": Vs,
            "invalidation_level": S - c.retest_fail_buffer_atr * A,
            "timeout_hours": c.retest_max_hours,
            "elapsed_hours": round(hrs, 2) if hrs is not None else None,
            "remaining_hours": round(c.retest_max_hours - hrs, 2) if hrs is not None else None,
        })
    elif st == "RETEST":
        Lr = ev.retest_low_lr
        lvl = H + c.breakout_buffer_atr * A
        hrs = (last_close_ms - ev.retest_time_ms) / cm.H1 if last_close_ms and ev.retest_time_ms else None
        prev_close = closes[-2] if len(closes) >= 2 else None
        v20 = (float(np.median(vols[-1 - c.breakout_vol_lookback:-1]))
               if len(vols) > c.breakout_vol_lookback else None)
        unmet, act, thr = [], [], []
        crossed = (prev_close is not None and prev_close <= lvl and close > lvl)
        if not crossed:
            unmet.append("尚未发生首次上穿 H+0.1A")
            act.append(f"prev_close={prev_close if prev_close is None else round(prev_close, 8)}, "
                       f"close={close:.8g}")
            thr.append(f"需 prev_close<={lvl:.8g} 且 close>{lvl:.8g}")
        else:
            if v20 is None or vol < c.breakout_vol_multiplier * v20:
                unmet.append("突破量能不足 1.5*V20")
                act.append(f"vol={vol:.8g}"); thr.append(
                    f">={c.breakout_vol_multiplier * (v20 or 0):.8g}")
            if not sm.enable_oi_filter:
                unmet.append("（OI 门槛未启用，B 组）")
        out.update({
            "unmet": "; ".join(unmet) if unmet else "突破条件已满足",
            "actual": "; ".join(act) if act else f"close={close:.8g}",
            "threshold": "; ".join(thr) if thr else "",
            "retest_low_lr": Lr, "breakout_level": lvl,
            "v20": v20, "v20_x1_5": (c.breakout_vol_multiplier * v20) if v20 else None,
            "invalidation_level": Lr - c.initial_stop_buffer_atr * A,
            "timeout_hours": c.breakout_max_hours_after_retest,
            "elapsed_hours": round(hrs, 2) if hrs is not None else None,
            "remaining_hours": (round(c.breakout_max_hours_after_retest - hrs, 2)
                                if hrs is not None else None),
            "consecutive_closes_below_l": sm.consecutive_closes_below_l,
        })
    return out


def main() -> None:
    c = cm.config()
    con = cm.market_conn()
    try:
        syms, meta = cm.universe()
        r = con.execute("SELECT MAX(open_time) FROM candle_heads WHERE exchange=? AND market=? "
                        "AND timeframe='1h'", (cm.EXCHANGE, cm.MARKET)).fetchone()
        ref_t = int(r[0]) + cm.H1 - 1
        floor = ref_t - cm.TARGET_DAYS * cm.DAY

        eng = cm.new_engine(syms, history_floor_ms=floor)
        st = eng.bootstrap(ref_t, log=lambda *a: None)

        rows, per_sym = [], {}
        agg_trans = {k: 0 for k in cm.ALL_STATES}
        n_cand = 0
        cand_list = []
        for sym in syms:
            e = eng.engines.get(sym)
            if e is None:
                rows.append({"symbol": sym, "ready": False,
                             "skip_reason": eng.skipped_reasons.get(sym)})
                continue
            sm = e.sm
            tc = {k: 0 for k in cm.ALL_STATES}
            for t in sm.transitions:
                stt = getattr(t, "to_state", None)
                if stt:
                    tc[stt] = tc.get(stt, 0) + 1
                    agg_trans[stt] = agg_trans.get(stt, 0) + 1
            n_cand += len(sm.candidates)
            for cd in sm.candidates:
                cand_list.append({
                    "symbol": sym, "candidate_id": cd.candidate_id,
                    "available_at_utc": cm.iso(cd.available_at_ms),
                    "direction": getattr(cd, "direction", None),
                    "breakout_close": cd.breakout_close,
                    "initial_stop_price": cd.initial_stop_price,
                    "box_high_h": cd.box_high_h, "box_low_l": cd.box_low_l,
                    "box_atr_a": cd.box_atr_a, "box_er": cd.box_er,
                })
            proc = sm.processed_1h_bar_opens
            last_proc = max(proc) if proc else None
            last_close_ms = (last_proc + cm.H1) if last_proc is not None else None
            ev = sm.active_event
            base = {
                "symbol": sym, "ready": True,
                "bars_1h_processed": len(proc),
                "last_processed_1h_open_utc": cm.iso(last_proc),
                "last_processed_1h_close_utc": cm.iso(last_close_ms),
                "engine_last_1h_open_utc": cm.iso(eng.last_1h_open.get(sym)),
                "current_state": (ev.current_state.value if ev else "IDLE"),
                "event_created_utc": cm.iso(ev.created_at_ms) if ev else None,
                "event_age_hours": (round((last_close_ms - ev.created_at_ms) / cm.H1, 2)
                                    if ev and last_close_ms else None),
                "n_transitions": len(sm.transitions),
                "n_candidates": len(sm.candidates),
            }
            for k in cm.ALL_STATES:
                base[f"tr_{k}"] = tc[k]
            # 最近一个已完结事件（终态与原因）——「结构失效/超时」的可查证据
            if sm.completed_events:
                le = sm.completed_events[-1]
                base["last_event_state"] = (le.current_state.value
                                            if hasattr(le.current_state, "value")
                                            else str(le.current_state))
                base["last_event_reason"] = le.termination_reason
                base["last_event_ended_utc"] = cm.iso(le.ended_at_ms)
            else:
                base["last_event_state"] = None
                base["last_event_reason"] = None
                base["last_event_ended_utc"] = None
            if ev is not None:
                base.update(why_active(ev, sm, c, last_close_ms))
            else:
                base.update(why_idle(con, sym, c, sm.cooling_until_ms, last_close_ms))
            rows.append(base)
            per_sym[sym] = base

        # ── 池级汇总 ──
        stage_cnt: dict[str, int] = {}
        for r_ in rows:
            if not r_.get("ready"):
                stage_cnt["SKIPPED"] = stage_cnt.get("SKIPPED", 0) + 1
                continue
            stage_cnt[r_["stage"]] = stage_cnt.get(r_["stage"], 0) + 1
        doc = {
            "ticket": "SIGNAL-HISTORY-FIX-01",
            "generated_at_utc": cm.utc_now(),
            "engine": "unified_runtime.MultiSymbolSignalEngine（既有，未改）",
            "config_identity": cm.config_identity(),
            "ref_t_utc": cm.iso(ref_t),
            "history_floor_requested_utc": cm.iso(floor),
            "history_floor_note": ("请求 180 天，实际受本地数据限制；"
                                   "bootstrap 统计里的 _window_hours 是实际回放窗口"),
            "bootstrap": {k: v for k, v in st.items() if k != "skipped_reasons"},
            "bootstrap_skipped": st.get("skipped_reasons") or {},
            "pool_source": meta.get("pool_source"),
            "pool_n": len(syms),
            "engines_ready": len(eng.engines),
            "transition_totals_in_replay": agg_trans,
            "history_candidates_total": n_cand,
            "current_stage_distribution": stage_cnt,
            "funnel": eng.funnel(),
            "candidates": cand_list,
            "per_symbol": per_sym,
        }
        cm.write_json("03_漏斗汇总.json", doc)

        import csv
        keys = ["symbol", "ready", "skip_reason", "stage", "current_state",
                "event_created_utc", "event_age_hours",
                "last_processed_1h_open_utc", "last_processed_1h_close_utc",
                "engine_last_1h_open_utc", "bars_1h_processed",
                "n_transitions", "n_candidates"]
        keys += [f"tr_{k}" for k in cm.ALL_STATES]
        keys += ["unmet", "actual", "threshold",
                 "box_high_h", "box_low_l", "box_atr_a", "box_er",
                 "sweep_low_s", "sweep_volume_vs", "retest_low_lr", "breakout_level",
                 "v20", "v20_x1_5", "last_bar_low", "last_bar_close", "last_bar_volume",
                 "invalidation_level", "timeout_hours", "elapsed_hours", "remaining_hours",
                 "consecutive_closes_below_l",
                 "last_event_state", "last_event_reason", "last_event_ended_utc",
                 "width_atr", "er", "atr_a"]
        with open(cm.OUT / "03_逐币漏斗与原因.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r_ in rows:
                w.writerow(r_)

        print("ref_t", cm.iso(ref_t), "floor_req", cm.iso(floor))
        print("bootstrap:", {k: v for k, v in st.items() if k != "skipped_reasons"})
        print("engines_ready", len(eng.engines), "/", len(syms))
        print("transition totals:", agg_trans)
        print("history candidates:", n_cand, [x["symbol"] for x in cand_list])
        print("stage distribution:", stage_cnt)
    finally:
        con.close()


if __name__ == "__main__":
    main()

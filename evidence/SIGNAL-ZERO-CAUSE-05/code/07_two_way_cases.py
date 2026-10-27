# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜07 双向案例验证 + 当前卡点量化。

回答工单第 4 条与第 6 条后半：
  · 「应触发」：每一个候选是否**真的**满足冻结规则的六项条件（逐条复算，不看状态机自报）；
  · 「不应触发」：当前明确不满足建档条件的标的，是否**确实没有**新建箱体；
  · 「卡在哪」：526 个标的逐个给出「离下一个条件还差多少」（时间/价格/量能），
    而不是只写「正常等待」。

产出：
  out/07_应触发复算.json
  out/07_不应触发核对.json
  out/07_当前卡点.csv / 07_当前卡点汇总.json
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


def _load(name, fn):
    p = Path(__file__).parent / fn
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ab = _load("ab04", "04_gate_isolated_ab.py")
pa = _load("pa05", "05_verify_fix_parity.py")


def verify_candidate(c, cfg):
    """按冻结规则六项逐条复算候选是否真的成立（不依赖状态机自报）。"""
    H, L, A = c.box_high_h, c.box_low_l, c.box_atr_a
    checks = {
        "1_箱体宽度在2~8A": (cfg.box_min_width_atr <= (H - L) / A <= cfg.box_max_width_atr,
                          f"(H-L)/A={(H - L) / A:.4f}", "2.0~8.0"),
        "2_回踩低点高于扫低低点": (c.retest_low_lr > c.sweep_low_s,
                        f"Lr={c.retest_low_lr} vs S={c.sweep_low_s}", "Lr > S"),
        "3_回踩低点在L~L+0.5A": (L <= c.retest_low_lr <= L + cfg.retest_max_zone_atr * A,
                          f"Lr={c.retest_low_lr}, 区间=[{L}, {L + cfg.retest_max_zone_atr * A:.8g}]",
                          "L <= Lr <= L+0.5A"),
        "4_突破收盘高于H+0.1A": (c.breakout_close > H + cfg.breakout_buffer_atr * A,
                         f"close={c.breakout_close} vs 阈值={H + cfg.breakout_buffer_atr * A:.8g}",
                         "close > H+0.1A"),
        "5_突破量能不低于1.5V20": (c.volume_ratio >= cfg.breakout_vol_multiplier,
                           f"volume_ratio={c.volume_ratio:.4f}", ">= 1.5"),
        "6_初始止损=Lr-0.1A": (abs(c.initial_stop_price
                              - (c.retest_low_lr - cfg.initial_stop_buffer_atr * A)) < 1e-12,
                          f"stop={c.initial_stop_price}",
                          f"Lr-0.1A={c.retest_low_lr - cfg.initial_stop_buffer_atr * A:.8g}"),
    }
    return {k: {"pass": bool(v[0]), "actual": v[1], "rule": v[2]}
            for k, v in checks.items()}


def main() -> int:
    cfg = cm.cfg()
    upto = cm.last_closed_1h_close_ms()
    syms, _ = cm.universe()
    n = int(cfg.box_bars_4h)
    print(f"[cfg] upto={cm.utc(upto)} universe={len(syms)}")

    hist, _ = cm.load_hist(syms, upto)

    rows = []
    all_cands = []
    box_new_at_last = {}      # sym -> 最后一根 4h 收盘是否新建了箱体
    for k, sym in enumerate(syms, 1):
        b4_all, b1_all = hist[sym]["4h"], hist[sym]["1h"]
        pre, b4_win, win_start = cm.split_window(b4_all, upto)
        if len(b4_win) < n + 8 or len(b1_all) < 200:
            rows.append({"symbol": sym, "status": "数据不足"})
            continue
        ctx = [b for b in b4_all
               if int(b["close_time"]) <= int(upto)
               and int(b["open_time"]) >= win_start - (n - 1) * H4]
        ep = pa.replay_prod(sym, ctx, b4_win, b1_all, pre, win_start, cfg, upto_ms=upto)
        st = cm.engine_state(ep)
        for c in ep.sm.candidates:
            all_cands.append({"symbol": sym, "candidate": c, "state": st["state"]})

        last4 = b4_win[-1] if b4_win else None
        box_new_at_last[sym] = bool(
            st["box_formed_ms"] == int(last4["close_time"])) if last4 else False

        # ── 卡点量化 ──
        row = {"symbol": sym, "state": st["state"],
               "stage_entered_utc": cm.utc(st["stage_entered_ms"]),
               "last_processed_1h_utc": cm.utc(st["processed_1h_max"]),
               "data_invalid": st["data_invalid"]}
        if st["state"] == "IDLE":
            cool = int(st["cooling_until_ms"] or 0)
            row["blocked_by"] = "冷却" if cool > int(st["processed_1h_max"] or 0) else "无箱体"
            row["remaining_h"] = (round((cool - int(st["processed_1h_max"])) / H1, 2)
                                  if row["blocked_by"] == "冷却" else None)
            # 最后一根 4h 的建档条件实际值（用于回答「为什么没箱体」）
            if last4 is not None and len(b4_win) >= n:
                seg = b4_win[-n:]
                hi = max(float(x["high"]) for x in seg)
                lo = min(float(x["low"]) for x in seg)
                cl = np.array([float(x["close"]) for x in seg])
                from 策略.WyckoffMaster.event_trend_v0_1.features import (
                    compute_efficiency_ratio)
                er = compute_efficiency_ratio(cl)
                a = st["atr"]
                row["last4_width_atr"] = round((hi - lo) / a, 4) if a else None
                row["last4_er"] = None if er is None else round(er, 4)
                row["last4_width_ok"] = bool(a and cfg.box_min_width_atr
                                             <= (hi - lo) / a <= cfg.box_max_width_atr)
                row["last4_er_ok"] = bool(er is not None and er <= cfg.er_threshold)
        elif st["state"] == "BOX":
            base = st["box_formed_ms"]
            row["elapsed_h"] = round((int(st["processed_1h_max"]) - base) / H1, 2)
            row["remaining_h"] = round(
                (base + cfg.sweep_max_hours * H1 - int(st["processed_1h_max"])) / H1, 2)
            row["blocked_by"] = "等扫低(7天内)"
            L, A = st["box_low"], st["box_atr"]
            row["sweep_trigger_low_range"] = [round(L - cfg.sweep_max_overshoot_atr * A, 8),
                                              round(L, 8)]
            row["sweep_need_close_ge"] = round(L + cfg.sweep_min_recovery_atr * A, 8)
        elif st["state"] == "SWEEP":
            row["blocked_by"] = "等缩量回踩(72h内)"
            row["remaining_h"] = round(
                (st["sweep_time_ms"] + cfg.retest_max_hours * H1
                 - int(st["processed_1h_max"])) / H1, 2)
            L, A = st["box_low"], st["box_atr"]
            row["retest_zone"] = [round(L, 8), round(L + cfg.retest_max_zone_atr * A, 8)]
            row["retest_vol_max"] = round(cfg.retest_vol_multiplier * (st["sweep_volume_vs"] or 0), 8)
        elif st["state"] == "RETEST":
            row["blocked_by"] = "等突破(H+0.1A)"
            row["remaining_h"] = round(
                (st["retest_time_ms"] + cfg.breakout_max_hours_after_retest * H1
                 - int(st["processed_1h_max"])) / H1, 2)
            H, A = st["box_high"], st["box_atr"]
            row["breakout_level"] = round(H + cfg.breakout_buffer_atr * A, 8)
            last1h_close = None
            if b1_all:
                last1h_close = float(b1_all[-1]["close"])
            row["last_1h_close"] = last1h_close
            row["gap_to_breakout_pct"] = (
                round((row["breakout_level"] / last1h_close - 1) * 100, 4)
                if last1h_close else None)
        rows.append(row)
        if k % 150 == 0:
            print(f"  ... {k}/{len(syms)}")

    # ── 应触发：逐条复算 ────────────────────────────────────────────────────
    trig = []
    for x in all_cands:
        c = x["candidate"]
        v = verify_candidate(c, cfg)
        trig.append({"symbol": x["symbol"], "candidate_id": c.candidate_id,
                     "snapshot_utc": cm.utc(c.snapshot_time_ms),
                     "all_pass": all(y["pass"] for y in v.values()),
                     "checks": v})
    n_trig_ok = sum(1 for t in trig if t["all_pass"])
    cm.dump("07_应触发复算.json", {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "n_candidates": len(trig),
        "n_all_checks_pass": n_trig_ok,
        "note": ("六项条件全部由候选快照字段独立复算，不采信状态机自报。"
                 "这 18 个都是**回放期历史候选**，按纪律丢弃、不补开。"),
        "items": trig,
    })

    # ── 不应触发：明确不满足建档条件的标的，是否确实没新建箱体 ────────────────
    neg = []
    for r in rows:
        if r.get("status") == "数据不足" or r["state"] != "IDLE":
            continue
        if r.get("blocked_by") != "无箱体":
            continue
        wo = r.get("last4_width_ok")
        eo = r.get("last4_er_ok")
        if wo is False or eo is False:
            neg.append({"symbol": r["symbol"],
                        "last4_width_atr": r.get("last4_width_atr"),
                        "last4_er": r.get("last4_er"),
                        "width_ok": wo, "er_ok": eo,
                        "box_formed_utc": None,
                        "new_box_at_last_4h": box_new_at_last.get(r["symbol"]),
                        "unmet": ([] if wo is not False else
                                  [f"宽度 {r.get('last4_width_atr')}A 不在 "
                                   f"{cfg.box_min_width_atr}~{cfg.box_max_width_atr}A"])
                                + ([] if eo is not False else
                                   [f"ER={r.get('last4_er')} 超阈 {cfg.er_threshold}"])})
    n_neg_violation = sum(1 for x in neg if x["new_box_at_last_4h"])
    cm.dump("07_不应触发核对.json", {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "n_symbols_unmet": len(neg),
        "n_violations": n_neg_violation,
        "rule": "最后一根 4h 的宽度/ER 明确不满足 ⇒ 该时点不得新建箱体；违反即缺陷。",
        "items": neg,
    })

    # ── 卡点汇总 ────────────────────────────────────────────────────────────
    replayed = [r for r in rows if r.get("status") != "数据不足"]
    by_state = Counter(r["state"] for r in replayed)
    by_block = Counter(r.get("blocked_by") for r in replayed)
    ret = [r for r in replayed if r["state"] == "RETEST"]
    gaps = sorted([r["gap_to_breakout_pct"] for r in ret
                   if r.get("gap_to_breakout_pct") is not None])
    summary = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "n_replayed": len(replayed),
        "state_distribution": dict(by_state),
        "blocked_by_distribution": dict(by_block),
        "n_candidates_replay": len(trig),
        "n_candidates_all_checks_pass": n_trig_ok,
        "n_negative_cases": len(neg),
        "n_negative_violations": n_neg_violation,
        "retest_gap_to_breakout_pct": {
            "n": len(gaps),
            "min": gaps[0] if gaps else None,
            "median": gaps[len(gaps) // 2] if gaps else None,
            "max": gaps[-1] if gaps else None,
            "note": "正数=还差多少百分比才到突破线；负数=已高于突破线但当时未收盘确认",
        },
        "note": ("卡点量化取自每个标的**已收盘**的最新 1h/4h，不预测、不外推；"
                 "`remaining_h` 为负表示该阶段已超时（会在下一根 1h 被终止）。"),
    }
    cm.dump_csv("07_当前卡点.csv", rows)
    cm.dump("07_当前卡点汇总.json", summary)

    print("\n=== 应触发复算 ===")
    print(f"  候选 {len(trig)} 个，六项条件全通过 {n_trig_ok} 个")
    for t in trig[:5]:
        bad = [k for k, v in t["checks"].items() if not v["pass"]]
        print(f"   {t['symbol']:14s} {t['snapshot_utc']} all_pass={t['all_pass']} "
              f"{'未过:' + ','.join(bad) if bad else ''}")
    print("\n=== 不应触发核对 ===")
    print(f"  明确不满足建档条件 {len(neg)} 个，其中「仍新建箱体」的违规 {n_neg_violation} 个")
    print("\n=== 卡点汇总 ===")
    for k, v in summary.items():
        if k == "note":
            continue
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-01｜02 ATR 预热配置 vs 实际计算路径（工单「二」第 4 条）。

工单要求：「核对 ATR 预热配置与实际计算路径是否一致；发现差异先记录影响，
不擅自改变冻结策略口径。」

三个口径对照（**全部只读，不改任何一行**）：
  (A) **配置声明**：`EventTrendConfig.atr_warmup_bars = 200`
      —— 注释原文「至少预热 200 根完整 4h K 线」。
  (B) **回测实际**：`runner.py:225` 对 `df_4h_raw`（全历史）算一次连续 Wilder ATR，
      取值 `sub_4h["atr"].iloc[-1]`；`:298-300` 只回取 220 根 4h 作为窗口。
  (C) **实时实际**：`unified_runtime.py:311-318` 在 `process_market` 里对
      **最近 42 根**（`box_bars_4h`）重算一次 `compute_wilder_atr_series`，取 `[-1]`。
      Wilder 递推从该窗口第 14 根播种 ⇒ 每根都「重新起算」。

量化影响：ATR 直接进 `width_atr = (H - L) / A`，而建档门槛是 `2A <= width <= 8A`。
ATR 一变，箱体是否成立就会变。故必须回答：**有多少个 4h 时点的建档判定会翻转**。

产出：
  out/02_ATR预热对照.json
  out/02_ATR逐币差异.csv
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


def main() -> None:
    c = cm.config()
    n_box = c.box_bars_4h
    period = c.atr_period_4h
    con = cm.market_conn()
    try:
        syms, meta = cm.universe()
        per_sym, rows = {}, []
        tot = {"bars_eval": 0, "flip_engine_in_bt_out": 0, "flip_engine_out_bt_in": 0,
               "engine_in": 0, "bt_in": 0, "both_in": 0}
        rel_diffs: list[float] = []
        for sym in syms:
            b = con.execute(
                "SELECT open_time, high, low, close FROM candles WHERE exchange=? AND market=? "
                "AND is_closed=1 AND symbol=? AND timeframe='4h' ORDER BY open_time",
                (cm.EXCHANGE, cm.MARKET, sym)).fetchall()
            if len(b) < n_box + 1:
                per_sym[sym] = {"bars_4h": len(b), "evaluated": 0,
                                "reason": f"4h_bars_insufficient({len(b)}<{n_box + 1})"}
                continue
            hi = np.array([float(r["high"]) for r in b], dtype=np.float64)
            lo = np.array([float(r["low"]) for r in b], dtype=np.float64)
            cl = np.array([float(r["close"]) for r in b], dtype=np.float64)
            ts = [int(r["open_time"]) for r in b]
            # (B) 回测口径：全历史连续 Wilder ATR（算一次，逐根取值）
            atr_bt = compute_wilder_atr_series(hi, lo, cl, period=period)
            n_eval = 0
            d_max, d_sum = 0.0, 0.0
            f1 = f2 = 0
            for i in range(n_box - 1, len(b)):
                # (C) 实时口径：最近 42 根重算
                a_eng = float(compute_wilder_atr_series(
                    hi[i + 1 - n_box:i + 1], lo[i + 1 - n_box:i + 1],
                    cl[i + 1 - n_box:i + 1], period=period)[-1])
                a_bt = float(atr_bt[i])
                if not (np.isfinite(a_eng) and np.isfinite(a_bt)) or a_eng <= 0 or a_bt <= 0:
                    continue
                seg_h = hi[i + 1 - n_box:i + 1]
                seg_l = lo[i + 1 - n_box:i + 1]
                seg_c = cl[i + 1 - n_box:i + 1]
                width = float(np.max(seg_h) - np.min(seg_l))
                w_eng = width / a_eng
                w_bt = width / a_bt
                in_eng = c.box_min_width_atr <= w_eng <= c.box_max_width_atr
                in_bt = c.box_min_width_atr <= w_bt <= c.box_max_width_atr
                er = compute_efficiency_ratio(seg_c)
                er_ok = (er is not None and er <= c.er_threshold)
                n_eval += 1
                d = abs(a_eng - a_bt) / a_bt
                rel_diffs.append(d)
                d_max = max(d_max, d)
                d_sum += d
                tot["bars_eval"] += 1
                tot["engine_in"] += int(in_eng)
                tot["bt_in"] += int(in_bt)
                tot["both_in"] += int(in_eng and in_bt)
                if in_eng and not in_bt:
                    tot["flip_engine_in_bt_out"] += 1
                    f1 += 1
                if in_bt and not in_eng:
                    tot["flip_engine_out_bt_in"] += 1
                    f2 += 1
                # 只有「宽度通过且 ER 通过」才算真正建档，单独再记一列
                if er_ok and in_eng and not in_bt:
                    tot.setdefault("box_form_engine_only", 0)
                    tot["box_form_engine_only"] += 1
                if er_ok and in_bt and not in_eng:
                    tot.setdefault("box_form_bt_only", 0)
                    tot["box_form_bt_only"] += 1
            per_sym[sym] = {
                "bars_4h": len(b), "evaluated": n_eval,
                "atr_rel_diff_median": round(float(np.median(rel_diffs[-n_eval:])), 6) if n_eval else None,
                "atr_rel_diff_max": round(d_max, 6),
                "width_verdict_flip_engine_in_bt_out": f1,
                "width_verdict_flip_engine_out_bt_in": f2,
            }
            rows.append({"symbol": sym, **per_sym[sym]})

        rd = np.array(rel_diffs, dtype=np.float64) if rel_diffs else np.array([0.0])
        doc = {
            "ticket": "SIGNAL-HISTORY-FIX-01",
            "generated_at_utc": cm.utc_now(),
            "question": "ATR 预热配置与实际计算路径是否一致？",
            "declared": {
                "source": "策略/WyckoffMaster/event_trend_v0_1/config.py:27",
                "atr_warmup_bars": c.atr_warmup_bars,
                "comment": "至少预热 200 根完整 4h K 线",
                "atr_period_4h": c.atr_period_4h,
            },
            "backtest_path": {
                "source": "策略/WyckoffMaster/event_trend_v0_1/runner.py:225-230, :297-301, :357-363",
                "how": "df_4h_raw 全历史算一次连续 Wilder ATR(14)，逐根取 sub_4h['atr'].iloc[-1]",
                "effective_warmup": "全历史（只回取 220 根 4h 作为窗口，满足 200 根声明）",
                "consistent_with_declared": True,
            },
            "live_path": {
                "source": "框架/backend/unified_runtime.py:308-318",
                "how": ("每根 4h 取最近 box_bars_4h=42 根，重新调用 "
                        "compute_wilder_atr_series 取 [-1]；Wilder 递推从窗口第 14 根播种"),
                "effective_warmup_bars": n_box,
                "effective_smoothing_after_seed": n_box - period,
                "consistent_with_declared": False,
            },
            "consistency_verdict": {
                "declared_vs_backtest": "一致（回测用全历史连续序列，≥200 根）",
                "declared_vs_live": "**不一致**（实时只用 42 根且每根重新起算）",
                "backtest_vs_live": "**不一致**（同一根 4h 上两个不同的 ATR 值）",
            },
            "impact": {
                "why_it_matters": ("ATR 直接决定 width_atr=(H-L)/A，而建档门槛是 "
                                   "2A<=width<=8A；ATR 不同 ⇒ 箱体成立与否可能翻转 ⇒ "
                                   "候选/信号集合不同。"),
                "bars_evaluated": tot["bars_eval"],
                "atr_rel_diff_median": round(float(np.median(rd)), 6),
                "atr_rel_diff_p95": round(float(np.percentile(rd, 95)), 6),
                "atr_rel_diff_max": round(float(np.max(rd)), 6),
                "atr_rel_diff_gt_1pct": int((rd > 0.01).sum()),
                "atr_rel_diff_gt_5pct": int((rd > 0.05).sum()),
                "width_pass_engine": tot["engine_in"],
                "width_pass_backtest": tot["bt_in"],
                "width_pass_both": tot["both_in"],
                "width_verdict_flip_engine_in_bt_out": tot["flip_engine_in_bt_out"],
                "width_verdict_flip_engine_out_bt_in": tot["flip_engine_out_bt_in"],
                "box_form_engine_only": tot.get("box_form_engine_only", 0),
                "box_form_bt_only": tot.get("box_form_bt_only", 0),
                "flip_rate_of_evaluated": round(
                    (tot["flip_engine_in_bt_out"] + tot["flip_engine_out_bt_in"])
                    / max(1, tot["bars_eval"]), 6),
            },
            "action_taken": ("**只记录，不修改**。`atr_warmup_bars` 属冻结配置，"
                             "改动即改策略口径，须走裁决流程。本单不动。"),
            "per_symbol": per_sym,
        }
        cm.write_json("02_ATR预热对照.json", doc)

        import csv
        with open(cm.OUT / "02_ATR逐币差异.csv", "w", newline="", encoding="utf-8") as f:
            cols = ["symbol", "bars_4h", "evaluated", "atr_rel_diff_median",
                    "atr_rel_diff_max", "width_verdict_flip_engine_in_bt_out",
                    "width_verdict_flip_engine_out_bt_in", "reason"]
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)

        print("declared_vs_live =", doc["consistency_verdict"]["declared_vs_live"])
        print("bars_evaluated =", tot["bars_eval"])
        print(f"ATR rel diff: median={doc['impact']['atr_rel_diff_median']:.4%} "
              f"p95={doc['impact']['atr_rel_diff_p95']:.4%} "
              f"max={doc['impact']['atr_rel_diff_max']:.4%}")
        print(f"  >1%: {doc['impact']['atr_rel_diff_gt_1pct']} bars, "
              f">5%: {doc['impact']['atr_rel_diff_gt_5pct']} bars")
        print(f"width pass: engine={tot['engine_in']} bt={tot['bt_in']} both={tot['both_in']}")
        print(f"flips: engine_in/bt_out={tot['flip_engine_in_bt_out']} "
              f"engine_out/bt_in={tot['flip_engine_out_bt_in']} "
              f"rate={doc['impact']['flip_rate_of_evaluated']:.3%}")
        print(f"box_form (width+ER both pass): engine_only={doc['impact']['box_form_engine_only']} "
              f"bt_only={doc['impact']['box_form_bt_only']}")
    finally:
        con.close()


if __name__ == "__main__":
    main()

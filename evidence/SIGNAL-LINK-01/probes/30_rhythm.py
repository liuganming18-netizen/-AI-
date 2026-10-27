#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-LINK-01 探针③：把「约 12 天一个信号」这个节奏拆成可算的分量（只读）

回答的问题：**为什么是这个节奏？**
只念「12 天/个」等于没解释。本探针把它拆成三层：

  1. **条件概率层**：箱体→扫低→回踩→候选 每一关的转化率相乘；
  2. **时间占用层**：每个箱体要 42 根 4h（7 天）窗口，一个标的在 47 天里到底
     有多少时间是在「某个箱体里」；失败后还有 24 小时冷却；
  3. **统计不确定性层**：只有 4 个事件，点估计本身很粗 —— 给出泊松精确 95% 区间。
     不写这一层就会把「12 天/个」当成精确值，那是过度自信。

输入只用 `out/funnel.json`（探针① 的产物），不重新回放、不碰任何库。
输出 `out/rhythm.json`。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from collections import Counter
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "out"


def poisson_cdf(lam: float, k: int) -> float:
    return sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k + 1))


def solve(f, lo: float = 1e-9, hi: float = 1e4, iters: int = 300) -> float:
    """二分求 f 的零点；f 单调。"""
    for _ in range(iters):
        mid = (lo + hi) / 2
        if f(mid):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def poisson_ci(k: int) -> tuple:
    """精确泊松 95% 区间（Garwood 式，用 CDF 二分，不依赖 scipy）。"""
    if k <= 0:
        return 0.0, 3.689          # 0 事件的单侧上界
    lam_lo = solve(lambda l: (1.0 - poisson_cdf(l, k - 1)) < 0.025)   # P(X>=k)=0.025
    lam_hi = solve(lambda l: poisson_cdf(l, k) > 0.025)              # P(X<=k)=0.025
    return lam_lo, lam_hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--funnel", default=str(OUT_DIR / "funnel.json"))
    ap.add_argument("--out", default=str(OUT_DIR / "rhythm.json"))
    args = ap.parse_args()

    d = json.load(open(args.funnel, encoding="utf-8"))
    ps = d["per_symbol"]
    days = float(d["window"]["days"])
    f = d["funnel"]["to_state_counts"]
    n_sym = len(ps)

    # ── 1. 条件概率层 ─────────────────────────────────────────────────────
    conv = {
        "box_to_sweep": f["SWEEP"] / f["BOX"],
        "sweep_to_retest": f["RETEST"] / f["SWEEP"],
        "retest_to_candidate": f["CANDIDATE"] / f["RETEST"],
        "box_to_candidate": f["CANDIDATE"] / f["BOX"],
    }

    # ── 2. 时间占用层 ─────────────────────────────────────────────────────
    box_per_sym = Counter(sum(1 for t in s["transitions"] if t["to"] == "BOX") for s in ps)
    tot_box = sum(k * v for k, v in box_per_sym.items())
    avg_box = tot_box / n_sym
    box_window_days = 42 * 4 / 24          # 42 根 4h = 7 天
    occupied_days = avg_box * box_window_days
    sym_reach = {
        "box": sum(1 for s in ps if any(t["to"] == "BOX" for t in s["transitions"])),
        "sweep": sum(1 for s in ps if any(t["to"] == "SWEEP" for t in s["transitions"])),
        "retest": sum(1 for s in ps if any(t["to"] == "RETEST" for t in s["transitions"])),
        "candidate": sum(1 for s in ps if s["candidates"]),
    }

    # 回踩之后死在哪 —— 这一段是最大的漏斗口
    reasons = d["funnel"]["termination_reasons"]
    retest_killers = {k: v for k, v in reasons.items() if "Lr" in k or "回踩后" in k}

    # ── 3. 统计不确定性层 ─────────────────────────────────────────────────
    k = f["CANDIDATE"]
    lam_lo, lam_hi = poisson_ci(k)
    ci = {
        "events": k, "exposure_days": days,
        "lambda_95ci": [round(lam_lo, 3), round(lam_hi, 3)],
        "days_per_signal_95ci": [round(days / lam_hi, 1), round(days / lam_lo, 1)],
        "days_per_signal_point": round(days / k, 1) if k else None,
    }

    out = {
        "probe": "SIGNAL-LINK-01/30_rhythm",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(args.funnel),
        "window_days": days,
        "symbols": n_sym,
        "counts": {kk: f[kk] for kk in ("BOX", "SWEEP", "RETEST", "CANDIDATE")},
        "conversion": {kk: round(v, 6) for kk, v in conv.items()},
        "time_occupancy": {
            "boxes_total": tot_box,
            "boxes_per_symbol_avg": round(avg_box, 2),
            "box_window_days": box_window_days,
            "box_occupied_days_per_symbol": round(occupied_days, 1),
            "window_days": days,
            "occupied_share_of_window": round(occupied_days / days, 3),
            "distribution_boxes_per_symbol": dict(sorted(box_per_sym.items())),
        },
        "symbols_reaching": {kk: {"n": v, "share": round(v / n_sym, 4)}
                             for kk, v in sym_reach.items()},
        "retest_stage_killers": retest_killers,
        "statistics": ci,
        "reading": {
            "why_rhythm": ("节奏 = 五道独立条件相乘（箱体宽度/效率比 → 假跌破且同根收回 → "
                           "缩量回踩 → 回踩后不破 Lr−0.1A → 首次上穿且量≥1.5×V20）"
                           "× 每道都要时间（箱体 7 天窗口、扫低 7 天内、回踩 72 小时内、"
                           "回踩后 7 天内、失败后 24 小时冷却）。"),
            "caveat": ("只有 4 个事件 ⇒ 点估计很粗。95% 区间跨 "
                       f"{ci['days_per_signal_95ci'][0]}～{ci['days_per_signal_95ci'][1]} 天/个，"
                       "不得把 12 天当成精确值，也不得据此推断策略优劣。"),
        },
    }
    p = Path(args.out)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"窗口 {days} 天 / {n_sym} 标的")
    print(f"转化率：箱体→扫低 {conv['box_to_sweep']:.1%} · "
          f"扫低→回踩 {conv['sweep_to_retest']:.1%} · "
          f"回踩→候选 {conv['retest_to_candidate']:.2%} · "
          f"箱体→候选 {conv['box_to_candidate']:.3%}")
    print(f"时间占用：平均 {avg_box:.1f} 个箱体/标的 × {box_window_days:.0f} 天 = "
          f"{occupied_days:.1f} 天（占窗口 {occupied_days/days:.0%}）")
    print(f"标的到达率：" + " · ".join(
        f"{k2} {v['n']}({v['share']:.1%})" for k2, v in out['symbols_reaching'].items()))
    print(f"回踩段杀手：{retest_killers}")
    print(f"节奏点估计 {ci['days_per_signal_point']} 天/个，95% 区间 "
          f"{ci['days_per_signal_95ci'][0]}～{ci['days_per_signal_95ci'][1]} 天/个")
    print(f"输出：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

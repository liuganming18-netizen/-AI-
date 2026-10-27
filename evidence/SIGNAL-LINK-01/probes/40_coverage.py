#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-LINK-01 探针④：覆盖广度到底有没有用（只读）

回答一个问题：**「全市场 500 多个币」为什么还是喂不饱这个低频策略？**

不靠推理，靠三件可复算的事：

  ① **速率恒等式**：把「12 天/个」拆成 标的数 × 每标的机会频率 × 单次中奖率，
     看每一项还能不能动；
  ② **覆盖反事实**：按成交额把 526 个标的排序，分别只取前 50 / 100 / 200 / 全部，
     用**各标的实际箱体数与实际候选数**算「如果只覆盖这些，47 天能有几个候选」——
     看覆盖广度是不是瓶颈；
  ③ **长尾贡献**：4 个候选落在成交额排名的第几百分位，长尾（低成交额）标的
     贡献了多少箱体、多少候选；以及有多少标的根本「没机会」（K 线太短/太死）。

纪律：
  · 行情库 `mode=ro` 只读，live 库完全不碰，不写任何库；
  · 只用 `out/funnel.json` 的既有字段 + 行情库 `candles.quote_volume`，
    不重新跑状态机（数字必须能与 10_funnel.py 对上）；
  · 所有输出可由 `out/coverage.json` 机械复算。

用法：
  python 40_coverage.py [--funnel .../out/funnel.json] [--out .../out/coverage.json]
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MARKET_DB = ROOT / "数据/市场数据/db/market_v2.sqlite3"
EXCHANGE = "binance"
MARKET = "um"


def _ro_con():
    con = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _iso_ms(s: str) -> int:
    """'2026-09-07T02:00:00Z' -> 毫秒时间戳（不依赖本地时区）。"""
    import calendar
    import datetime as dt
    d = dt.datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
    return calendar.timegm(d.timetuple()) * 1000


def quote_volume_by_symbol(con, start_ms: int, end_ms: int) -> dict:
    """窗口内 4h 成交额合计（美元）。用 4h 而非 15m：同一窗口、行数少 16 倍、够排序用。"""
    cur = con.execute(
        "SELECT symbol, SUM(quote_volume) AS qv, COUNT(*) AS n "
        "FROM candles WHERE exchange=? AND market=? AND timeframe='4h' AND is_closed=1 "
        "AND open_time>=? AND close_time<=? GROUP BY symbol",
        (EXCHANGE, MARKET, int(start_ms), int(end_ms)))
    return {r["symbol"]: {"quote_volume": float(r["qv"] or 0.0), "bars": int(r["n"])}
            for r in cur.fetchall()}


def percentile_rank(sorted_vals: list, v: float) -> float:
    """v 在升序列表里的百分位（0=最小，100=最大）。"""
    if not sorted_vals:
        return float("nan")
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] < v:
            lo = mid + 1
        else:
            hi = mid
    return 100.0 * lo / len(sorted_vals)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--funnel", default=str(ROOT / "evidence/SIGNAL-LINK-01/out/funnel.json"))
    ap.add_argument("--out", default=str(ROOT / "evidence/SIGNAL-LINK-01/out/coverage.json"))
    args = ap.parse_args()

    t0 = time.time()
    fn = json.load(io.open(args.funnel, encoding="utf-8"))
    win = fn["window"]
    start_ms = int(win["start_ms"]) if "start_ms" in win else _iso_ms(win["start_utc"])
    end_ms = int(win["end_ms"]) if "end_ms" in win else _iso_ms(win["end_utc"])
    days = float(win["days"])
    per_symbol = fn["per_symbol"]

    # ---- 每标的的机会数（= 进入 BOX 的次数）与候选数，全部来自 funnel.json ----
    rows = []
    for ps in per_symbol:
        sym = ps["symbol"]
        box_n = sum(1 for t in ps.get("transitions", []) if t.get("to") == "BOX")
        sweep_n = sum(1 for t in ps.get("transitions", []) if t.get("to") == "SWEEP")
        retest_n = sum(1 for t in ps.get("transitions", []) if t.get("to") == "RETEST")
        cand_n = len(ps.get("candidates", []))
        rows.append({"symbol": sym, "bars_4h": ps.get("bars_4h", 0),
                     "box": box_n, "sweep": sweep_n, "retest": retest_n,
                     "candidate": cand_n})

    if start_ms is None or end_ms is None:
        raise SystemExit("funnel.json 缺 window.start_ms/end_ms，无法取成交额")

    con = _ro_con()
    qv = quote_volume_by_symbol(con, start_ms, end_ms)
    con.close()

    for r in rows:
        info = qv.get(r["symbol"], {})
        r["quote_volume"] = info.get("quote_volume", 0.0)
        r["bars_4h_db"] = info.get("bars", 0)

    # ---- ① 速率恒等式 ----
    n_sym = len(rows)
    tot_box = sum(r["box"] for r in rows)
    tot_cand = sum(r["candidate"] for r in rows)
    opp_per_sym_day = (tot_box / n_sym) / days
    hit_rate = (tot_cand / tot_box) if tot_box else float("nan")
    rate_identity = {
        "symbols": n_sym,
        "window_days": round(days, 2),
        "boxes_total": tot_box,
        "candidates_total": tot_cand,
        "boxes_per_symbol": round(tot_box / n_sym, 3),
        "opportunities_per_symbol_per_day": round(opp_per_sym_day, 5),
        "market_opportunities_per_day": round(tot_box / days, 2),
        "hit_rate_box_to_candidate": round(hit_rate, 6),
        "signals_per_day": round(tot_cand / days, 5),
        "days_per_signal": round(days / tot_cand, 2) if tot_cand else None,
        "identity_check": round(round(tot_box / days, 2) * hit_rate, 5),
        "note": "signals_per_day = market_opportunities_per_day × hit_rate；"
                "三项里只有 hit_rate 与机会频率受规则控制，标的数已顶到 526",
    }

    # ---- ② 覆盖反事实：按成交额排序取前 N ----
    by_qv = sorted(rows, key=lambda r: r["quote_volume"], reverse=True)
    cf = []
    for n in (20, 50, 100, 200, 300, 526):
        n = min(n, len(by_qv))
        sub = by_qv[:n]
        b = sum(r["box"] for r in sub)
        c = sum(r["candidate"] for r in sub)
        share_qv = (sum(r["quote_volume"] for r in sub) /
                    sum(r["quote_volume"] for r in by_qv)) if by_qv else 0.0
        cf.append({
            "top_n": n,
            "quote_volume_share": round(share_qv, 4),
            "boxes": b,
            "candidates": c,
            "days_per_signal": round(days / c, 2) if c else None,
            "expected_candidates_in_47d": c,
        })

    # ---- ③ 长尾贡献与候选分布 ----
    qv_sorted = sorted(r["quote_volume"] for r in rows)
    cand_rows = [r for r in rows if r["candidate"] > 0]
    cand_detail = [{
        "symbol": r["symbol"],
        "quote_volume": round(r["quote_volume"], 2),
        "volume_percentile": round(percentile_rank(qv_sorted, r["quote_volume"]), 1),
        "boxes": r["box"],
        "candidates": r["candidate"],
    } for r in sorted(cand_rows, key=lambda x: -x["quote_volume"])]

    # 五等分（按成交额），看每档的箱体数 / 候选数 / 单次中奖率
    quint = []
    k = len(by_qv) // 5
    for i in range(5):
        sub = by_qv[i * k:(i + 1) * k] if i < 4 else by_qv[4 * k:]
        b = sum(r["box"] for r in sub)
        c = sum(r["candidate"] for r in sub)
        quint.append({
            "tier": f"Q{i + 1}" + ("(最低成交额)" if i == 4 else ("(最高成交额)" if i == 0 else "")),
            "symbols": len(sub),
            "quote_volume_share": round(sum(r["quote_volume"] for r in sub) /
                                        sum(r["quote_volume"] for r in by_qv), 4) if by_qv else 0,
            "boxes": b,
            "candidates": c,
            "hit_rate": round(c / b, 6) if b else None,
        })

    # 「没机会」的标的：整窗只有 0~1 个箱体
    dead = {
        "symbols_zero_box": sum(1 for r in rows if r["box"] == 0),
        "symbols_le1_box": sum(1 for r in rows if r["box"] <= 1),
        "symbols_lt120_bars_4h": sum(1 for r in rows if r["bars_4h"] < 120),
        "min_bars_4h": min(r["bars_4h"] for r in rows),
        "max_bars_4h": max(r["bars_4h"] for r in rows),
        "note": "箱体需要 42 根 4h + 200 根 ATR 预热；K 线太短的标的本窗口内根本进不了状态机",
    }

    # ---- 攒样本要多久（不改规则的前提下）----
    dps = (days / tot_cand) if tot_cand else None
    sample = [{"target_samples": t,
               "expected_days": round(t * dps, 1) if dps else None,
               "expected_years": round(t * dps / 365.0, 2) if dps else None}
              for t in (10, 30, 50, 100, 200)]

    out = {
        "probe": "SIGNAL-LINK-01/40_coverage",
        "generated_real_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_funnel": args.funnel,
        "market_db": str(MARKET_DB),
        "window": {"start_utc": win.get("start_utc"), "end_utc": win.get("end_utc"),
                   "days": round(days, 2)},
        "rate_identity": rate_identity,
        "coverage_counterfactual": cf,
        "candidate_detail": cand_detail,
        "volume_quintiles": quint,
        "no_opportunity_symbols": dead,
        "samples_needed": sample,
        "reading": {
            "q": "全市场 500 多个币不能覆盖低频策略？",
            "a": "覆盖广度已经用满了：526 个标的、3,278 次箱体机会，全部算过。"
                 "瓶颈不在「看得够不够多」，在「每次机会里只有 0.122% 能走到候选」。"
                 "加标的已无可加（526 已是全部合规合约），加时间不改规则就能线性攒样本，"
                 "改规则（放宽某道关卡）等于改冻结规则，须走裁决流程。",
        },
        "elapsed_s": round(time.time() - t0, 2),
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with io.open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ---- 打印 ----
    ri = rate_identity
    print(f"窗口 {ri['window_days']} 天 / {ri['symbols']} 标的")
    print(f"速率恒等式：全市场每天机会 {ri['market_opportunities_per_day']} 次 "
          f"× 单次中奖率 {ri['hit_rate_box_to_candidate']*100:.3f}% "
          f"= {ri['signals_per_day']:.4f} 个/天 ⇒ {ri['days_per_signal']} 天/个")
    print(f"  校验：{ri['market_opportunities_per_day']} × {ri['hit_rate_box_to_candidate']:.6f} "
          f"= {ri['identity_check']}")
    print("覆盖反事实（只取成交额前 N）：")
    for c in cf:
        print(f"  前 {c['top_n']:>3} 名（成交额占 {c['quote_volume_share']*100:5.1f}%）"
              f" 箱体 {c['boxes']:>4} → 候选 {c['candidates']} "
              f"{'（' + str(c['days_per_signal']) + ' 天/个）' if c['days_per_signal'] else '（47 天 0 个）'}")
    print("按成交额五等分：")
    for q in quint:
        hr = f"{q['hit_rate']*100:.3f}%" if q["hit_rate"] else "—"
        print(f"  {q['tier']:<18} 标的 {q['symbols']:>3} 成交额 {q['quote_volume_share']*100:5.1f}% "
              f"箱体 {q['boxes']:>4} 候选 {q['candidates']} 中奖率 {hr}")
    print("4 个候选的位置：")
    for c in cand_detail:
        print(f"  {c['symbol']:<12} 成交额排名百分位 {c['volume_percentile']:5.1f} "
              f"箱体 {c['boxes']} 候选 {c['candidates']}")
    print(f"没机会的标的：0 个箱体 {dead['symbols_zero_box']} · ≤1 个箱体 {dead['symbols_le1_box']} "
          f"· 4h K 线 <120 根 {dead['symbols_lt120_bars_4h']}")
    print("攒样本要多久（不改规则）：")
    for s in sample:
        print(f"  {s['target_samples']:>3} 个样本 ≈ {s['expected_days']} 天 ≈ {s['expected_years']} 年")
    print(f"输出：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

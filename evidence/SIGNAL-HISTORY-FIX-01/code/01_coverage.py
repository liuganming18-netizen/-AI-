# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-01｜01 历史数据覆盖审计（工单「二、历史读取与状态恢复」）。

回答：以「最近 180 天已收盘行情」为读取目标，本地到底有多少？
逐币 × 逐周期记录：请求范围 / 实际范围 / 根数 / 缺口 / 最后一根。
**数据不足一律如实标注，不伪造、不默默缩短、本单不下载任何数据。**

口径：
  · 参考时刻 `REF_T` = 全池最后一根**已闭合** 1h bar 的 close（数据相对，不用挂钟，
    这样结果可复现，且不会因为「现在几点」而漂移）。
  · 请求范围 = [REF_T - 180d, REF_T]。
  · 实际范围 = 该币该周期在库里的 [min(open_time), max(open_time)]。
  · 缺口 = 期望根数 - 实际根数；期望根数 = (max-min)/周期 + 1（闭区间）。
    注意：上市晚的币「前面缺」属正常，单列 `pre_listing_shortfall`，
    与「中间有洞」`inner_gaps` 分开，避免把上市时间当数据质量问题。

产出：
  out/01_覆盖审计.json
  out/01_逐币覆盖.csv
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

TFS = (("1h", cm.H1), ("4h", cm.H4), ("15m", cm.M15))


def gaps_for(con, sym: str, tf: str, step_ms: int, limit: int = 5) -> list[dict]:
    """列出该币该周期的内部缺口（只对确认有洞的币调用，避免全池扫）。"""
    rows = [r[0] for r in con.execute(
        "SELECT open_time FROM candle_heads WHERE exchange=? AND market=? AND symbol=? "
        "AND timeframe=? ORDER BY open_time", (cm.EXCHANGE, cm.MARKET, sym, tf))]
    out = []
    for a, b in zip(rows, rows[1:]):
        if b - a > step_ms:
            out.append({"after_utc": cm.iso(a), "before_utc": cm.iso(b),
                        "missing_bars": (b - a) // step_ms - 1})
            if len(out) >= limit:
                break
    return out


def main() -> None:
    con = cm.market_conn()
    try:
        syms, meta = cm.universe()
        # 参考时刻：全池最后一根已闭合 1h
        r = con.execute(
            "SELECT MAX(open_time) FROM candle_heads WHERE exchange=? AND market=? "
            "AND timeframe='1h'", (cm.EXCHANGE, cm.MARKET)).fetchone()
        ref_open = int(r[0])
        ref_t = ref_open + cm.H1 - 1          # 该 bar 的 close（最后一毫秒）
        req_start = ref_t - cm.TARGET_DAYS * cm.DAY

        per_symbol, rows = {}, []
        agg = {tf: {"bars": 0, "symbols_with_any": 0, "symbols_full_180d": 0,
                    "min_start_ms": None, "max_end_ms": None} for tf, _ in TFS}
        for sym in syms:
            rec = {"symbol": sym, "requested_start_utc": cm.iso(req_start),
                   "requested_end_utc": cm.iso(ref_t)}
            for tf, step in TFS:
                q = con.execute(
                    "SELECT MIN(open_time), MAX(open_time), COUNT(*) FROM candle_heads "
                    "WHERE exchange=? AND market=? AND symbol=? AND timeframe=?",
                    (cm.EXCHANGE, cm.MARKET, sym, tf)).fetchone()
                mn, mx, cnt = q[0], q[1], q[2]
                if not cnt:
                    rec[f"{tf}_bars"] = 0
                    rec[f"{tf}_first_utc"] = None
                    rec[f"{tf}_last_utc"] = None
                    rec[f"{tf}_days"] = 0.0
                    rec[f"{tf}_covers_180d"] = False
                    rec[f"{tf}_inner_gaps"] = None
                    continue
                expected = (mx - mn) // step + 1
                inner = int(expected - cnt)
                rec[f"{tf}_bars"] = int(cnt)
                rec[f"{tf}_first_utc"] = cm.iso(mn)
                rec[f"{tf}_last_utc"] = cm.iso(mx + step - 1)   # 该 bar 收盘
                rec[f"{tf}_days"] = round((mx + step - mn) / cm.DAY, 2)
                # 「覆盖 180 天」= 起点不晚于请求起点，且终点不早于参考时刻-1根
                rec[f"{tf}_covers_180d"] = bool(mn <= req_start)
                rec[f"{tf}_inner_gap_bars"] = inner
                rec[f"{tf}_inner_gaps"] = gaps_for(con, sym, tf, step) if inner > 0 else []
                rec[f"{tf}_pre_listing_shortfall_days"] = round(
                    max(0, mn - req_start) / cm.DAY, 2)
                a = agg[tf]
                a["bars"] += int(cnt)
                a["symbols_with_any"] += 1
                a["symbols_full_180d"] += 1 if mn <= req_start else 0
                a["min_start_ms"] = mn if a["min_start_ms"] is None else min(a["min_start_ms"], mn)
                a["max_end_ms"] = mx if a["max_end_ms"] is None else max(a["max_end_ms"], mx)
            per_symbol[sym] = rec
            rows.append(rec)

        # ── 池级结论 ──
        pool = {}
        for tf, _ in TFS:
            a = agg[tf]
            days_actual = (round((a["max_end_ms"] + cm.H1 - a["min_start_ms"]) / cm.DAY, 2)
                           if a["min_start_ms"] else 0)
            pool[tf] = {
                "symbols": len(syms),
                "symbols_with_data": a["symbols_with_any"],
                "symbols_covering_180d": a["symbols_full_180d"],
                "pool_first_utc": cm.iso(a["min_start_ms"]),
                "pool_last_utc": cm.iso(a["max_end_ms"]),
                "pool_span_days": days_actual,
                "total_bars": a["bars"],
                "shortfall_days_vs_180": round(cm.TARGET_DAYS - days_actual, 2),
            }
        doc = {
            "ticket": "SIGNAL-HISTORY-FIX-01",
            "generated_at_utc": cm.utc_now(),
            "read_only": True,
            "downloaded_anything": False,
            "pool_source": meta.get("pool_source"),
            "pool_n": len(syms),
            "ref_t_utc": cm.iso(ref_t),
            "ref_t_source": "全池最后一根已闭合 1h bar 的 close（数据相对，非挂钟）",
            "requested_window_days": cm.TARGET_DAYS,
            "requested_start_utc": cm.iso(req_start),
            "requested_end_utc": cm.iso(ref_t),
            "pool": pool,
            "verdict": {},
            "note": ("`covers_180d` 判据 = 该币该周期最早一根不晚于请求起点。"
                     "上市晚的币天然不满足，单列 pre_listing_shortfall_days，"
                     "与 inner_gaps（中间有洞）分开统计。"),
        }
        for tf, _ in TFS:
            p = pool[tf]
            doc["verdict"][tf] = {
                "covers_180d": p["symbols_covering_180d"] == len(syms),
                "symbols_covering_180d": p["symbols_covering_180d"],
                "actual_span_days": p["pool_span_days"],
                "shortfall_days": p["shortfall_days_vs_180"],
            }
        # 全池是否真的拿到 180 天：只要有一个周期不满足，就是「数据不足」
        doc["data_sufficient_for_180d"] = all(
            v["covers_180d"] for v in doc["verdict"].values())
        doc["data_sufficiency_statement"] = (
            "满足 180 天" if doc["data_sufficient_for_180d"] else
            "**不满足 180 天**：本地数据仅覆盖约 "
            f"{pool['1h']['pool_span_days']} 天（1h）/ {pool['4h']['pool_span_days']} 天（4h），"
            f"距 180 天缺 {pool['1h']['shortfall_days_vs_180']} 天。本单不下载，如实标注。")

        cm.write_json("01_覆盖审计.json", doc)

        import csv
        OUT = cm.OUT
        cols = ["symbol", "requested_start_utc", "requested_end_utc"]
        for tf, _ in TFS:
            cols += [f"{tf}_bars", f"{tf}_first_utc", f"{tf}_last_utc", f"{tf}_days",
                     f"{tf}_covers_180d", f"{tf}_inner_gap_bars",
                     f"{tf}_pre_listing_shortfall_days"]
        with open(OUT / "01_逐币覆盖.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)

        print("ref_t", cm.iso(ref_t), "requested_start", cm.iso(req_start))
        for tf, _ in TFS:
            print(f"  {tf}: span={pool[tf]['pool_span_days']}d "
                  f"covers180={pool[tf]['symbols_covering_180d']}/{len(syms)} "
                  f"bars={pool[tf]['total_bars']}")
        print("data_sufficient_for_180d =", doc["data_sufficient_for_180d"])
        print(doc["data_sufficiency_statement"])
        # 内部缺口汇总
        for tf, _ in TFS:
            n_gap_syms = sum(1 for r in rows if (r.get(f"{tf}_inner_gap_bars") or 0) > 0)
            n_gap_bars = sum((r.get(f"{tf}_inner_gap_bars") or 0) for r in rows)
            print(f"  {tf}: symbols_with_inner_gaps={n_gap_syms} inner_gap_bars_total={n_gap_bars}")
    finally:
        con.close()


if __name__ == "__main__":
    main()

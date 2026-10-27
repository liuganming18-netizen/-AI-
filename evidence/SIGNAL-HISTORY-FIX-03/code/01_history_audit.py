# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜01 半年历史接入审计（读取量 / 覆盖 / 连续性 / 来源）。

回答工单「一」「二」：
  · 冷层 + 热库按既定来源规则合并，读最近 180 天已收盘 1h/4h；
  · 去重（同 key 热层优先）、升序、逐币连续性校验；
  · 上市不足 180 天**单列**，不与「存储缺失」混为一谈；
  · 不下载、不写生产库。

产出：out/01_历史接入.json、out/01_逐币覆盖.csv
"""
from __future__ import annotations

import csv
import importlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import history_loader as HL
import live_store as L
import project_paths as PP

READ_DAYS = 180


def main() -> int:
    syms, meta = cm.universe()
    now = cm.now_ms()
    start = now - READ_DAYS * cm.DAY
    win_start = now - int((200 * 4 + 336 + 24) * cm.H1)      # 声明窗口 1160h
    print(f"[universe] {len(syms)} 标的，pool_source={meta.get('pool_source')}")
    print(f"[read] {cm.utc(start)} → {cm.utc(now)}（{READ_DAYS} 天）")

    prov = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=print)
    t0 = time.time()
    data, st = prov.load(syms, ("1h", "4h"), start, now, keep_from_ms=win_start)
    elapsed = round(time.time() - t0, 2)

    # 4h 需要全量（ATR 预热用 180 天）；单独再读一次只为拿到 4h 全量行
    d4, st4 = prov.load(syms, ("4h",), start, now, keep_from_ms=start)
    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "read_days": READ_DAYS,
        "read_window": {"start_utc": cm.utc(start), "end_utc": cm.utc(now)},
        "declared_replay_window_hours": 1160,
        "declared_replay_window_start_utc": cm.utc(win_start),
        "universe": {"n": len(syms), "pool_source": meta.get("pool_source")},
        "hot_db": str(L.MARKET_DB),
        "archive_root": str(PP.MARKET_ARCHIVE),
        "downloaded_anything": False,
        "wrote_production": False,
        "elapsed_sec": elapsed,
        "rows_materialized_total": st["rows_materialized_total"],
        "archive_state": st["archive_state"],
        "per_timeframe": {},
    }
    for tf, s in (("1h", st), ("4h", st4)):
        per = s["per_timeframe"][tf]
        out["per_timeframe"][tf] = {
            k: per[k] for k in (
                "cold_rows_scanned", "hot_rows_scanned", "overlap_rows",
                "overlap_value_mismatch", "rows_materialized", "dup_removed",
                "cold_max_open_utc", "symbols", "symbols_with_data",
                "symbols_full_window", "missing_bars_total", "bars_min", "bars_max",
                "listed_late_n", "source_split")
        }
        out["per_timeframe"][tf]["listed_late"] = per["listed_late"]

    # 逐币覆盖 CSV（两个周期合并到一张表）
    rows = []
    cov1 = {c["symbol"]: c for c in st["per_timeframe"]["1h"]["coverage"]}
    cov4 = {c["symbol"]: c for c in st4["per_timeframe"]["4h"]["coverage"]}
    for sym in syms:
        c1, c4 = cov1.get(sym, {}), cov4.get(sym, {})
        rows.append({
            "symbol": sym,
            "bars_1h_180d": c1.get("bars"),
            "first_1h_utc": c1.get("first_utc"),
            "missing_1h": c1.get("missing"),
            "bars_4h_180d": c4.get("bars"),
            "first_4h_utc": c4.get("first_utc"),
            "missing_4h": c4.get("missing"),
            "cold_1h": c1.get("cold"), "hot_1h": c1.get("hot"),
            "cold_4h": c4.get("cold"), "hot_4h": c4.get("hot"),
            "listed_late": "Y" if any(x["symbol"] == sym
                                      for x in st["per_timeframe"]["1h"]["listed_late"]) else "",
        })
    p = cm.OUT / "01_逐币覆盖.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[out] {p} ({len(rows)} 行)")

    # 声明窗口内的实际覆盖（这才是策略真正用到的区间）
    win_cov = {"symbols_with_1160h_1h": 0, "symbols_with_1160h_4h": 0,
               "min_bars_1h": 10 ** 9, "min_bars_4h": 10 ** 9}
    for sym in syms:
        n1 = len(data[sym]["1h"])
        n4 = len(d4[sym]["4h"])
        if n1 >= 1159:
            win_cov["symbols_with_1160h_1h"] += 1
        if n4 >= 289:
            win_cov["symbols_with_1160h_4h"] += 1
        win_cov["min_bars_1h"] = min(win_cov["min_bars_1h"], n1)
        win_cov["min_bars_4h"] = min(win_cov["min_bars_4h"], n4)
    out["declared_window_coverage"] = win_cov
    cm.dump("01_历史接入.json", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

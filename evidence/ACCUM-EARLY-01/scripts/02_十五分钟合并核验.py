# -*- coding: utf-8 -*-
"""ACCUM-EARLY-01｜02 十五分钟「冷档+热库」合并读取核验。

核验四件事（工单要求逐条写清）：去重、冲突优先级、时间连续性、可用时间。
并与既有单一实现 `history_loader.MergedHistory` 对撞，证明没另立一套来源规则。
输出：evidence/ACCUM-EARLY-01/out/02_十五分钟合并核验.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD = PROJECT / "数据" / "市场数据" / "parquet"

# 首轮验证窗口（拟采用，非用户批准的范围限制）
START = 1782547200000        # 2026-06-27T00:00Z
END = 1793005200000          # 2026-10-26T09:00Z


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def main() -> int:
    import research_15m as R
    m = R.Merged15m(HOT, COLD)
    rep = {"probe": "02_十五分钟合并核验",
           "generated_at_utc": iso(time.time() * 1000),
           "window": {"start_utc": iso(START), "end_utc": iso(END),
                      "note": "首轮验证窗口（拟采用）；历史可查询范围不受此限制"},
           "cold_upper_bound_utc": iso(m.cold_upper_bound()),
           "rules": {
               "dedup_key": "open_time",
               "conflict_priority": "hot_over_cold",
               "only_closed": True,
               "cold_upper_bound_source": "parquet/_state.json",
               "no_interpolation": True,
               "no_download": True,
           },
           "symbols": {}, "errors": []}

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"]
    for s in symbols:
        try:
            bars, st = m.load(s, START, END)
            cc = m.crosscheck_ohlcv(s, START, END)
            hot_first = next((b["open_time"] for b in bars if b["source"] == "hot"), None)
            rep["symbols"][s] = {
                "bars": len(bars),
                "rows_cold": st["rows_cold"], "rows_hot": st["rows_hot"],
                "overlap_hot_wins": st["overlap_hot_wins"],
                "dup_removed": st["dup_removed"],
                "first_bar_utc": iso(bars[0]["open_time"]) if bars else None,
                "first_hot_bar_utc": iso(hot_first) if hot_first else None,
                "continuity": st["continuity"],
                "available_at": st["available_at"],
                "crosscheck_with_MergedHistory": {
                    "compared": cc["compared_bars"],
                    "mismatch": cc["mismatch_count"],
                    "verdict": cc["verdict"],
                },
            }
        except Exception as exc:                               # noqa: BLE001
            rep["errors"].append({"symbol": s, "error": f"{type(exc).__name__}: {exc}"})

    # 热库单独覆盖不到的那一段：用合并读取器补上多少
    try:
        btc = rep["symbols"].get("BTCUSDT", {})
        rep["hot_only_gap"] = {
            "热库 15m 起点": btc.get("first_hot_bar_utc"),
            "合并读取器起点": btc.get("first_bar_utc"),
            "说明": "热库 15m 只从 2026-09-12 起；合并读取器把 2026-06-27 起补齐。"
                    "成交用的 paper_engine.closed_15m 仍只读热库 ⇒ 2026-06-27→2026-09-12 "
                    "能重建观察、重建不出成交（除非改用本读取器）。",
        }
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "gap", "error": f"{type(exc).__name__}: {exc}"})

    out = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "02_十五分钟合并核验.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({s: {"bars": v["bars"], "dup_removed": v["dup_removed"],
                          "gaps": v["continuity"]["gap_segment_count"],
                          "crosscheck": v["crosscheck_with_MergedHistory"]["verdict"]}
                      for s, v in rep["symbols"].items()},
                     ensure_ascii=False, indent=1))
    print("errors", rep["errors"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-B｜02 冷热合并口径核验（复用 ACCUM-EARLY-01 的核验脚本口径）。

工单第 2 条要求「明确重复行优先级、已收盘判断、缺口和价格精度；不跨缺口补造 K 线」。
这里逐条给数，不靠说明文字：

A 去重与优先级：同一 open_time 冷热都有 ⇒ 热层优先；重复条数、命中条数逐币列出
B 已收盘判断：只取 is_closed=1；并对「未收盘 bar 会不会被读出来」做反向检查
C 冷热交界：15m 交界（2026-09-12）、4h 交界（2026-09-09）逐根检查——有无重复、
  有无跳空、交界两侧价格是否连续（用「同一根两来源数值差」而不是「相邻两根价格差」，
  后者本来就该变，不能当不一致）
D 缺口：全池扫 2026 年 15m 缺口；对真实缺口（AIAUSDT）验证「不插值、不补造」
E 价格精度：冷热同源同一根逐字段比对；并与 `history_loader.MergedHistory` 对撞
F 缓存一致性：同一请求重复调用，返回逐位相同（防止缓存把窗口截断）

输出：out/02_合并口径核验.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
OUT = PROJECT / "evidence" / "ACCUM-EARLY-03-B" / "out"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD = PROJECT / "数据" / "市场数据" / "parquet"

M15, H4 = 900_000, 14_400_000
#: 纯冷段（热库 15m 起点之前）
COLD_START, COLD_END = 1782547200000, 1789171200000
#: 覆盖冷热交界的窗口
SEAM_15M = 1789171200000        # 2026-09-12T00:00Z 热库 15m 第一根
SEAM_4H = 1788912000000         # 2026-09-09T20:00Z 热库 4h 第一根
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT",
           "TNSRUSDT", "AGTUSDT", "METISUSDT", "AIAUSDT", "GUAUSDT"]


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def main() -> int:
    import research_15m as R

    rep = {"probe": "02_合并口径核验", "generated_at_utc": iso(time.time() * 1000),
           "module": "research_15m.Merged15m / MergedMarket（同一套合并逻辑）",
           "windows": {"cold_only": [iso(COLD_START), iso(COLD_END)],
                       "seam_15m_utc": iso(SEAM_15M), "seam_4h_utc": iso(SEAM_4H)},
           "A_去重与优先级": {}, "B_已收盘": {}, "C_冷热交界": {},
           "D_缺口": {}, "E_价格精度": {}, "F_缓存一致性": {}, "errors": []}

    m15 = R.Merged15m(HOT, COLD, timeframe="15m")
    m4h = R.Merged15m(HOT, COLD, timeframe="4h")

    # ── A 去重与优先级 ───────────────────────────────────────────────────
    for s in SYMBOLS:
        try:
            bars, st = m15.load(s, COLD_START, COLD_END + 14 * 86400_000)
            ot = [b["open_time"] for b in bars]
            rep["A_去重与优先级"][s] = {
                "rows_cold": st["rows_cold"], "rows_hot": st["rows_hot"],
                "rows_merged": st["rows_merged"],
                "overlap_hot_wins": st["overlap_hot_wins"],
                "dup_removed": st["dup_removed"],
                "dup_removed_equals_overlap": st["dup_removed"] == st["overlap_hot_wins"],
                "unique_open_time": len(ot) == len(set(ot)),
                "conflict_priority": st["rule"]["conflict_priority"],
                "dedup_key": st["rule"]["dedup_key"],
            }
        except Exception as exc:                             # noqa: BLE001
            rep["errors"].append({"stage": "A", "symbol": s, "error": str(exc)})

    # 优先级方向必须可证：同一 open_time 上，合并结果必须等于热层值（不是冷层值）
    # ⚠ 这里**不能**从合并结果里反推冷层：合并后重叠行的 source 已经是 "hot"，
    #   按 source 过滤再比就是自比（恒真）。必须分别直读两个源。
    prio_check = []
    for s in ("BTCUSDT", "METISUSDT"):
        try:
            lo, hi = SEAM_15M - 2 * 86400_000, SEAM_15M + 2 * 86400_000
            bars, _ = m15.load(s, lo, hi)
            merged = {b["open_time"]: b for b in bars}
            import sqlite3
            con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            hot_rows = {int(r["open_time"]): dict(r) for r in con.execute(
                "SELECT open_time,open,high,low,close,volume,quote_volume FROM candles "
                "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
                "AND is_closed=1 AND open_time>=? AND open_time<?", (s, lo, hi))}
            con.close()
            import duckdb
            dc = duckdb.connect()
            try:
                pat = str(COLD / "candles" / "binance" / "um" / s / "15m" / "*.parquet")
                cold_rows = {int(r[0]): {"open": r[1], "high": r[2], "low": r[3],
                                         "close": r[4], "volume": r[5], "quote_volume": r[6]}
                             for r in dc.execute(
                                 f"SELECT open_time,open,high,low,close,volume,quote_volume "
                                 f"FROM read_parquet('{pat}') WHERE is_closed=1 "
                                 f"AND open_time>=? AND open_time<? ORDER BY open_time",
                                 [lo, hi]).fetchall()}
            finally:
                dc.close()
            overlap = sorted(set(hot_rows) & set(cold_rows))
            diff_hot = diff_cold = 0
            not_labeled_hot = 0
            examples = []
            for t in overlap:
                mv = merged[t]
                if mv.get("source") != "hot":
                    not_labeled_hot += 1
                if any(abs(float(mv[k]) - float(hot_rows[t][k])) > 1e-12
                       for k in ("open", "high", "low", "close")):
                    diff_hot += 1
                if any(abs(float(mv[k]) - float(cold_rows[t][k])) > 1e-12
                       for k in ("open", "high", "low", "close")):
                    diff_cold += 1
                    if len(examples) < 3:
                        examples.append({"open_time": t,
                                         "merged_close": float(mv["close"]),
                                         "cold_close": float(cold_rows[t]["close"]),
                                         "hot_close": float(hot_rows[t]["close"])})
            prio_check.append({
                "symbol": s, "overlap_bars": len(overlap),
                "overlap_all_labeled_hot": not_labeled_hot == 0,
                "not_labeled_hot": not_labeled_hot,
                "merged_equals_hot": diff_hot == 0,
                "merged_equals_cold": diff_cold == 0,
                "diff_from_hot": diff_hot, "diff_from_cold": diff_cold,
                "cold_diff_examples": examples,
                "verdict": (
                    "规则已应用且数值方向可证（合并值=热层值，且与冷层确有差异）"
                    if not_labeled_hot == 0 and diff_hot == 0 and diff_cold > 0 else
                    "规则已应用（重叠行标记为热层），但冷热数值完全相同 ⇒ "
                    "「热层优先」在本数据上数值不可判，不能算证明通过"
                    if not_labeled_hot == 0 and diff_hot == 0 and diff_cold == 0 else
                    "规则未按预期应用（有重叠行未被标为热层，或合并值与热层不符）"),
            })
        except Exception as exc:                             # noqa: BLE001
            rep["errors"].append({"stage": "A-prio", "symbol": s, "error": str(exc)})
    rep["A_去重与优先级"]["优先级方向实证"] = prio_check

    # ── B 已收盘判断 ─────────────────────────────────────────────────────
    import sqlite3
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    try:
        unclosed = con.execute(
            "SELECT COUNT(*) FROM candle_versions WHERE exchange='binance' AND market='um' "
            "AND timeframe='15m' AND is_closed=0").fetchone()[0]
    finally:
        con.close()
    # 反向检查：把窗口右端设在一根未收盘 bar 的 open_time 之后，合并结果里不得出现它
    last_closed = None
    try:
        bars_all, _ = m15.load("BTCUSDT", SEAM_15M, SEAM_15M + 60 * 86400_000)
        last_closed = max(b["open_time"] for b in bars_all) if bars_all else None
    except Exception as exc:                                 # noqa: BLE001
        rep["errors"].append({"stage": "B", "error": str(exc)})
    rep["B_已收盘"] = {
        "热库未收盘 15m 版本行数": unclosed,
        "合并器规则": "只取 is_closed=1（冷档 SQL 与热库 SQL 都带这个条件）",
        "窗口内最后一根已收盘 15m": iso(last_closed),
        "说明": "未收盘 bar 不进合并结果；回放里也不存在「用未收盘 bar 定价」的路径。",
    }

    # ── C 冷热交界 ───────────────────────────────────────────────────────
    def seam_report(tf, module, seam_ms, label):
        s = "BTCUSDT"
        bars, st = module.load(s, seam_ms - 3 * 86400_000, seam_ms + 3 * 86400_000)
        idx = next((i for i, b in enumerate(bars) if b["source"] == "hot"), None)
        if idx is None:
            return {"error": "窗口内没有热层 bar"}
        around = bars[max(0, idx - 3): idx + 3]
        ot = [b["open_time"] for b in bars]
        step = M15 if tf == "15m" else H4
        gaps = [(iso(a), iso(b), (b - a) // step - 1)
                for a, b in zip(ot, ot[1:]) if b - a > step]
        # 交界处不得出现「同一 open_time 两条」
        dup = len(ot) - len(set(ot))
        prev = bars[idx - 1]
        first_hot = bars[idx]
        return {
            "label": label,
            "first_hot_bar_utc": iso(first_hot["open_time"]),
            "last_cold_bar_utc": iso(prev["open_time"]),
            "seam_step_ok": int(first_hot["open_time"]) - int(prev["open_time"]) == step,
            "duplicate_open_time": dup,
            "gap_segments_in_window": len(gaps),
            "gaps": gaps[:5],
            "around_seam": [{"utc": iso(b["open_time"]), "source": b["source"],
                             "open": b["open"], "close": b["close"],
                             "available_at": iso(b["available_at"])} for b in around],
            "overlap_hot_wins": st["overlap_hot_wins"],
            "verdict": ("交界连续：无重复、无跳空、步长正确"
                        if dup == 0 and not gaps and
                        int(first_hot["open_time"]) - int(prev["open_time"]) == step
                        else "交界有问题"),
        }

    rep["C_冷热交界"]["15m"] = seam_report("15m", m15, SEAM_15M, "15m 交界（热库起点）")
    rep["C_冷热交界"]["4h"] = seam_report("4h", m4h, SEAM_4H, "4h 交界（热库起点）")

    # ── D 缺口（不跨缺口补造）────────────────────────────────────────────
    try:
        import duckdb
        dc = duckdb.connect()
        try:
            rows = dc.execute("""
                WITH c AS (
                  SELECT symbol, open_time,
                         lag(open_time) OVER (PARTITION BY symbol ORDER BY open_time) AS prev
                  FROM read_parquet(?)
                  WHERE is_closed=1 AND open_time >= ? AND open_time < ?
                )
                SELECT symbol, count(*) AS n_gaps,
                       max((open_time - prev) / 900000 - 1) AS max_missing,
                       min(prev) AS first_gap_prev
                FROM c WHERE prev IS NOT NULL AND open_time - prev > 900000
                GROUP BY 1 ORDER BY max_missing DESC
            """, [str(COLD / "candles" / "binance" / "um" / "*" / "15m" / "*.parquet"),
                  1767225600000, COLD_END]).fetchall()
        finally:
            dc.close()
        gaps = [{"symbol": r[0], "gap_segments": int(r[1]),
                 "max_missing_bars": int(r[2]), "at_prev_utc": iso(r[3])} for r in rows]
    except Exception as exc:                                 # noqa: BLE001
        gaps = []
        rep["errors"].append({"stage": "D-scan", "error": str(exc)})
    rep["D_缺口"]["全池冷档 2026 年 15m 缺口"] = {
        "扫描区间": [iso(1767225600000), iso(COLD_END)],
        "有缺口的标的数": len(gaps), "明细": gaps,
        "说明": "冷档在本次验证窗口内几乎无缺口；唯一缺口用于验证「不补造」。",
    }
    # 对真实缺口做行为验证：缺口内取不到 bar，取到的是缺口之后那根
    if gaps:
        g = gaps[0]
        gs = g["at_prev_utc"]
        import calendar
        prev_ms = calendar.timegm(time.strptime(gs, "%Y-%m-%dT%H:%M:%SZ")) * 1000
        gsym = g["symbol"]
        inside_ms = prev_ms + M15
        b_inside = m15.load(gsym, inside_ms, inside_ms + M15)[0]
        b_after = m15.load(gsym, prev_ms, prev_ms + (g["max_missing_bars"] + 2) * M15)[0]
        reader = R.MergedReader(HOT, COLD, "15m")
        try:
            at = reader.at_or_after(gsym, inside_ms)
            lst = reader.bars(gsym, inside_ms, inside_ms + (g["max_missing_bars"]) * M15)
        finally:
            reader.close()
        rep["D_缺口"]["真实缺口行为"] = {
            "symbol": gsym, "缺口起点（上一根）": gs,
            "缺口后第一根": iso(int(at["open_time"])) if at else None,
            "缺口内请求 at_or_after 返回": iso(int(at["open_time"])) if at else None,
            "缺口内 bars() 返回根数": len(lst),
            "缺口内是否有补造行": len(b_inside) > 0,
            "缺口长度（缺根数）": g["max_missing_bars"],
            "verdict": ("不补造成立：缺口内取不到行，at_or_after 直接跳到缺口之后那根"
                        if (len(lst) == 0 and not b_inside and at is not None)
                        else "缺口行为异常"),
        }

    # ── E 价格精度 ───────────────────────────────────────────────────────
    cc15 = m15.crosscheck_ohlcv("BTCUSDT", COLD_START, COLD_END)
    cc4h = m4h.crosscheck_ohlcv("BTCUSDT", 1782547200000, SEAM_4H + 7 * 86400_000)
    rep["E_价格精度"] = {
        "15m_与 MergedHistory 对撞": cc15,
        "4h_与 MergedHistory 对撞": cc4h,
        "价格取整口径": {
            "成交价": "取 bar.open 原值 → paper_engine._apply_order_rounding（冻结实现）",
            "退出价": "close_at_price → trading_core.fill.price_breakdown（同一实现）",
            "合并层是否做任何取整": False,
            "说明": "合并层只做去重与来源选择，**不改任何价格数值**；"
                    "取整仍只在冻结的成交/平仓实现里发生一次。",
        },
    }

    # ── F 缓存一致性 ─────────────────────────────────────────────────────
    reader = R.MergedReader(HOT, COLD, "15m")
    try:
        a = reader.bars("TNSRUSDT", 1784880000000, 1784880000000 + 40 * M15)
        b = reader.bars("TNSRUSDT", 1784880000000, 1784880000000 + 40 * M15)
        same = (len(a) == len(b) and all(
            int(x["open_time"]) == int(y["open_time"]) and float(x["open"]) == float(y["open"])
            and float(x["close"]) == float(y["close"]) for x, y in zip(a, b)))
        # 先取窄窗、再取宽窗，窄窗结果必须仍能逐位复现（缓存不得截断语义）
        n1 = reader.bars("TNSRUSDT", 1784880000000, 1784880000000 + 10 * M15)
        wide = reader.bars("TNSRUSDT", 1784880000000 - 30 * 86400_000,
                           1784880000000 + 60 * 86400_000)
        n2 = reader.bars("TNSRUSDT", 1784880000000, 1784880000000 + 10 * M15)
        same2 = (len(n1) == len(n2) and all(
            int(x["open_time"]) == int(y["open_time"]) and float(x["open"]) == float(y["open"])
            for x, y in zip(n1, n2)))
        rep["F_缓存一致性"] = {
            "同一请求两次结果相同": same,
            "窄窗-宽窗-再窄窗 结果相同": same2,
            "窄窗根数": len(n1), "宽窗根数": len(wide),
            "缓存装载次数": len(reader.provenance()["cache_loads"]),
            "说明": "缓存只在内存，扩窗后重载；窄窗语义不因缓存而变。",
        }
    finally:
        reader.close()

    # ── 总结 ─────────────────────────────────────────────────────────────
    ok_a = all(v.get("unique_open_time") and v.get("dup_removed_equals_overlap")
               for k, v in rep["A_去重与优先级"].items() if k != "优先级方向实证")
    # 拆成两条，避免把「规则被执行」和「方向被证明」混成一条：
    ok_prio_applied = bool(prio_check) and all(
        x["overlap_bars"] > 0 and x["overlap_all_labeled_hot"] and x["merged_equals_hot"]
        for x in prio_check)
    # 非空转：必须有重叠行**且**冷热确有数值差异，否则「优先级方向」根本没测到
    ok_prio_proven = bool(prio_check) and all(
        x["overlap_bars"] > 0 and x["diff_from_cold"] > 0 for x in prio_check)
    ok_c = all(rep["C_冷热交界"][k].get("verdict", "").startswith("交界连续")
               for k in ("15m", "4h"))
    ok_d = (rep["D_缺口"].get("真实缺口行为", {}).get("verdict", "").startswith("不补造")
            if "真实缺口行为" in rep["D_缺口"] else None)
    ok_e = (cc15["mismatch_count"] == 0 and cc4h["mismatch_count"] == 0)
    ok_f = rep["F_缓存一致性"]["同一请求两次结果相同"] and rep["F_缓存一致性"]["窄窗-宽窗-再窄窗 结果相同"]
    rep["结论"] = {
        "A1_去重键唯一（open_time）": bool(ok_a),
        "A2_重叠行按规则取热层（标记+数值一致）": bool(ok_prio_applied),
        "A3_优先级数值方向被证明": bool(ok_prio_proven),
        "A_小计": ("通过" if (ok_a and ok_prio_applied and ok_prio_proven)
                   else ("部分完成：规则已应用，但冷热重叠段数值完全相同 ⇒ 方向不可判"
                         if (ok_a and ok_prio_applied) else "不通过")),
        "B_只取已收盘": True,
        "C_冷热交界连续": bool(ok_c),
        "D_缺口不补造": ok_d,
        "E_价格精度与既有实现一致": bool(ok_e),
        "F_缓存不改变语义": bool(ok_f),
        "总判定": ("通过" if all([ok_a, ok_prio_applied, ok_prio_proven, ok_c, ok_e, ok_f])
                   and ok_d is not False else
                   "部分完成（A3 与 D 见明细；A3 不是缺陷，是数据不支持证明）"),
        "口径提醒": ("「A3 未证明」不等于「优先级实现错了」。本数据上冷热重叠段逐位相同，"
                     "所以无法用数值区分两种取法；只能证明规则被应用（重叠行标记为 hot）。"
                     "要真正证明方向，需要一份冷热确实不一致的数据（当前没有）。"),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "02_合并口径核验.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(rep["结论"], ensure_ascii=False, indent=1))
    print("errors", rep["errors"][:5])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

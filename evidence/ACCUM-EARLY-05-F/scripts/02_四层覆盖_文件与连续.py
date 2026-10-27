# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-F｜02 四层覆盖：文件存在 / 窗口连续 / 当时可得 / 实际读取（只读）。

四层必须分开算，因为它们的结论完全不同：
  L1 文件存在 —— 目录里有 parquet、热库里有行；
  L2 窗口连续 —— 窗口内期望根数 vs 实际根数，缺口分「上市晚 / 中间断流 / 尾部提前结束」；
  L3 当时可得 —— `available_at` 是否有值（冷档恒空、热库部分有）；
  L4 决策实际读取 —— 由 03 号脚本做代码取证，本脚本只标「哪些周期被读」。

统计对象 = 05-C 固定的窗口（主）+ 04-A 已落盘的两段（对照）。
池 = 币安 USD-M 加密币 526（与 04-A 一致，不含 2 个指数）。
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
CANDLES = ROOT / "数据" / "市场数据" / "parquet" / "candles" / "binance" / "um"

STEP = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
EXCLUDED = {"ALLUSDT", "BTCDOMUSDT"}
DAY = 86_400_000


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def windows_from_upstream() -> dict:
    """窗口不手抄：05-C 与 04-A 的窗口直接从各自产物读。"""
    w = {}
    p05 = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out" / "01_输入快照与版本锁定.json"
    if p05.exists():
        d = json.loads(p05.read_text(encoding="utf-8"))["window"]
        w["05C"] = {
            "from_ms": int(d["start_ms"]), "to_ms": int(d["end_ms"]),
            "from_utc": d["start_utc"], "to_utc": d["end_utc"],
            "warmup_from_ms": int(d["warmup_start_ms"]),
            "warmup_from_utc": d["warmup_start_utc"],
            "source": "ACCUM-EARLY-05-C/out/01_输入快照与版本锁定.json",
        }
    for tag, key in (("research_final", "04A_research"), ("holdout_final", "04A_holdout")):
        p = ROOT / "evidence" / "ACCUM-EARLY-04-A" / "out" / f"01_共同观察范围_{tag}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        w[key] = {
            "from_ms": int(d["window"]["from_ms"]), "to_ms": int(d["window"]["to_ms"]),
            "from_utc": d["window"]["from"], "to_utc": d["window"]["to"],
            # 04-A 的实际读取范围（含预热），见其脚本 01 的 load_bars 调用
            "warmup_from_ms": int(d["window"]["from_ms"]) - 400 * DAY,
            "warmup_from_utc": iso(int(d["window"]["from_ms"]) - 400 * DAY),
            "source": f"ACCUM-EARLY-04-A/out/01_共同观察范围_{tag}.json",
        }
    return w


def pool() -> list:
    return sorted(p.name for p in CANDLES.iterdir()
                  if p.is_dir() and p.name not in EXCLUDED)


def cold_counts(syms: list, w0: int, w1: int, tfs=tuple(STEP)) -> dict:
    """一次扫全部 15m/1h/4h 分片，按 (symbol, timeframe) 聚合窗口内行数。"""
    paths = []
    for s in syms:
        for tf in tfs:
            paths += glob.glob(str(CANDLES / s / tf / "*.parquet"))
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    rows = con.execute(
        """
        SELECT regexp_extract(filename, 'um/([^/]+)/([^/]+)/', 1) AS sym,
               regexp_extract(filename, 'um/([^/]+)/([^/]+)/', 2) AS tf,
               COUNT(*) AS n,
               COUNT(DISTINCT open_time) AS nd,
               MIN(open_time) AS mn, MAX(open_time) AS mx,
               SUM(CASE WHEN available_at IS NULL THEN 1 ELSE 0 END) AS anull
        FROM read_parquet(?, filename=true)
        WHERE is_closed = 1 AND open_time >= ? AND open_time < ?
        GROUP BY 1, 2
        """, [paths, w0, w1]).fetchall()
    con.close()
    return {(r[0], r[1]): {"n": r[2], "nd": r[3], "min": r[4], "max": r[5],
                           "avail_null": r[6]} for r in rows}


def gap_profile(sym: str, tf: str, w0: int, w1: int) -> dict:
    """只对「根数不等于期望」的组做精确缺口画像（列读取，不扫全表）。"""
    files = sorted(glob.glob(str(CANDLES / sym / tf / "*.parquet")))
    if not files:
        return {"files": 0}
    con = duckdb.connect()
    rows = con.execute(
        "SELECT open_time FROM read_parquet(?) WHERE is_closed=1 "
        "AND open_time >= ? AND open_time < ? ORDER BY open_time",
        [files, w0, w1]).fetchall()
    con.close()
    step = STEP[tf]
    opens = [r[0] for r in rows]
    if not opens:
        return {"files": len(files), "bars": 0, "head_missing": (w1 - w0) // step,
                "tail_missing": 0, "gaps": 0, "gap_bars": 0, "first_utc": None,
                "last_utc": None}
    head_missing = (opens[0] - w0) // step          # 窗口起点到首根之间缺多少根
    tail_missing = (w1 - (opens[-1] + step)) // step
    gaps, gap_bars, worst = 0, 0, None
    for prev, cur in zip(opens, opens[1:]):
        d = (cur - prev) // step
        if d > 1:
            gaps += 1
            gap_bars += d - 1
            if worst is None or d - 1 > worst["missing_bars"]:
                worst = {"after_utc": iso(prev), "before_utc": iso(cur),
                         "missing_bars": d - 1}
    return {"files": len(files), "bars": len(opens), "head_missing": head_missing,
            "tail_missing": tail_missing, "gaps": gaps, "gap_bars": gap_bars,
            "worst_gap": worst, "first_utc": iso(opens[0]), "last_utc": iso(opens[-1])}


def hot_counts(syms: list, w0: int, w1: int) -> dict:
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    q = """
        SELECT symbol, timeframe, COUNT(*) n, MIN(open_time) mn, MAX(open_time) mx,
               SUM(CASE WHEN available_at IS NULL THEN 1 ELSE 0 END) anull
        FROM candles
        WHERE exchange='binance' AND market='um' AND is_closed=1
          AND open_time >= ? AND open_time < ? AND timeframe IN ('15m','1h','4h')
        GROUP BY 1,2
    """
    out = {}
    for r in con.execute(q, (w0, w1)):
        if r[0] in syms:
            out[(r[0], r[1])] = {"n": r[2], "min": r[3], "max": r[4], "avail_null": r[5]}
    con.close()
    return out


def expected_bars(w0: int, w1: int, step: int) -> int:
    """窗口内「应有几根 bar 的起点」。

    必须是向上取整：窗口终点不一定落在 bar 边界上（05-C 终点 09:00Z 对 4h 就不是边界，
    窗口内仍包含 08:00 那根）。用整除会把 727 根算成 726 根，
    结果所有标的都被判成「不连续」——这是自比式假失败，必须避免。
    """
    span = int(w1) - int(w0)
    return -((-span) // step)


def analyze_window(key: str, w: dict, syms: list) -> dict:
    w0, w1 = w["from_ms"], w["to_ms"]
    t0 = time.time()
    cold = cold_counts(syms, w0, w1)
    hot = hot_counts(syms, w0, w1)

    summary = {}
    anomalies = []
    for tf, step in STEP.items():
        exp = expected_bars(w0, w1, step)
        agg = {"expected_bars": exp, "symbols": len(syms),
               "cold": {"with_data": 0, "continuous": 0, "late_start": 0,
                        "mid_gap": 0, "tail_short": 0, "no_data": 0,
                        "missing_bars_total": 0, "available_at_filled": 0,
                        "available_at_null": 0, "dup_rows": 0},
               "hot": {"with_data": 0, "continuous": 0, "no_data": 0,
                       "available_at_filled": 0, "available_at_null": 0,
                       "missing_bars_total": 0}}
        for s in syms:
            c = cold.get((s, tf))
            if not c or c["n"] == 0:
                agg["cold"]["no_data"] += 1
                anomalies.append({"symbol": s, "tf": tf, "layer": "cold",
                                  "kind": "no_data_in_window", "expected": exp})
            else:
                agg["cold"]["with_data"] += 1
                agg["cold"]["available_at_filled"] += c["n"] - c["avail_null"]
                agg["cold"]["available_at_null"] += c["avail_null"]
                agg["cold"]["dup_rows"] += c["n"] - c["nd"]
                if c["n"] == exp and c["nd"] == exp:
                    agg["cold"]["continuous"] += 1
                else:
                    gp = gap_profile(s, tf, w0, w1)
                    miss = exp - c["nd"]
                    agg["cold"]["missing_bars_total"] += max(0, miss)
                    kinds = []
                    if gp.get("head_missing", 0) > 0:
                        agg["cold"]["late_start"] += 1
                        kinds.append("late_start")
                    if gp.get("gaps", 0) > 0:
                        agg["cold"]["mid_gap"] += 1
                        kinds.append("mid_gap")
                    if gp.get("tail_missing", 0) > 0:
                        agg["cold"]["tail_short"] += 1
                        kinds.append("tail_short")
                    anomalies.append({"symbol": s, "tf": tf, "layer": "cold",
                                      "kind": "+".join(kinds) or "count_mismatch",
                                      "expected": exp, "bars": c["nd"],
                                      "missing": miss, **{k: gp.get(k) for k in
                                                          ("head_missing", "tail_missing",
                                                           "gaps", "gap_bars", "worst_gap",
                                                           "first_utc", "last_utc")}})
            h = hot.get((s, tf))
            if not h or h["n"] == 0:
                agg["hot"]["no_data"] += 1
            else:
                agg["hot"]["with_data"] += 1
                agg["hot"]["available_at_filled"] += h["n"] - h["avail_null"]
                agg["hot"]["available_at_null"] += h["avail_null"]
                if h["n"] == exp:
                    agg["hot"]["continuous"] += 1
                else:
                    agg["hot"]["missing_bars_total"] += exp - h["n"]
        for layer in ("cold", "hot"):
            filled = agg[layer]["available_at_filled"]
            nulls = agg[layer]["available_at_null"]
            agg[layer]["available_at_total"] = filled + nulls
            agg[layer]["available_at_filled_pct"] = (
                round(100.0 * filled / (filled + nulls), 2) if (filled + nulls) else None)
        summary[tf] = agg

    return {"window": w, "by_timeframe": summary,
            "warmup_4h": warmup_profile(syms, w),
            "anomaly_count": len(anomalies),
            "anomalies": anomalies,
            "elapsed_sec": round(time.time() - t0, 1)}


def warmup_profile(syms: list, w: dict) -> dict:
    """预热段（warmup_from → 窗口起点）的 4h 覆盖。

    为什么单列：ATR(14) 与 42 根箱体是递推/滚动量，预热不足会让数值起点不同，
    与「窗口内有没有数据」是两件事。04-A 自己就设了「4h ≥ 260 根、1h ≥ 400 根」的闸门。
    """
    wu, w0 = w.get("warmup_from_ms"), w["from_ms"]
    if wu is None or wu >= w0:
        return {"applicable": False}
    exp = expected_bars(wu, w0, STEP["4h"])
    got = cold_counts(syms, wu, w0, tfs=("4h",))
    short, full, nodata, miss = 0, 0, 0, 0
    worst = []
    for s in syms:
        c = got.get((s, "4h"))
        n = c["n"] if c else 0
        if n == 0:
            nodata += 1
            short += 1
            miss += exp
            worst.append({"symbol": s, "bars": 0})
        elif n < exp:
            short += 1
            miss += exp - n
            worst.append({"symbol": s, "bars": n})
        else:
            full += 1
    worst.sort(key=lambda x: x["bars"])
    return {"applicable": True, "from_utc": iso(wu), "to_utc": iso(w0),
            "expected_bars": exp, "symbols_full": full, "symbols_short": short,
            "symbols_no_data": nodata, "missing_bars_total": miss,
            "worst_10": worst[:10],
            "note": "预热不足 ≠ 不可用；只说明该标的的 ATR/箱体递推起点比别人的样本短"} 


def object_05c(win: dict) -> dict:
    """05-C 真正的「统一观察母集」口径：48 个标的、1178 个观察事件。

    与 526 池的区别必须说清：05-C 的母集只覆盖「51 条原策略候选涉及的标的」，
    不是全池。全池统计在本文件 results.05C 里，作为「若扩到全池」的对照。
    """
    p = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out" / "04_统一观察母集.json"
    if not p.exists():
        return {"applicable": False}
    d = json.loads(p.read_text(encoding="utf-8"))
    syms = d["universe"]["symbols"]
    res = analyze_window("05C_universe", win, syms)
    res["universe"] = {
        "source": "ACCUM-EARLY-05-C/out/04_统一观察母集.json",
        "symbol_count": d["universe"]["symbol_count"],
        "symbols_source": d["universe"]["symbols_source"],
        "events_total": d["summary"]["events_total"],
        "events_with_candidate": d["summary"]["events_with_candidate"],
        "events_without_candidate": d["summary"]["events_without_candidate"],
        "engine_reader": d["engine"]["reader"],
        "oi_fed": d["engine"]["oi_fed"],
        "scope_warning": ("母集只覆盖 51 条原策略候选涉及的标的，"
                          "不是 526 全池；因此「只有新策略会进场」的机会只在这 48 个标的里找过"),
    }
    # 51 条候选的成交 bar 是否真的存在（事件级取证，不靠抽样推广）
    rows = (d.get("coverage_vs_51") or {}).get("rows") or []
    import calendar
    def to_ms(s):
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000
    want = []
    for r in rows:
        if r.get("entry_utc"):
            want.append((r["symbol"], to_ms(r["entry_utc"])))
    if want:
        paths = []
        for s in sorted({w[0] for w in want}):
            paths += glob.glob(str(CANDLES / s / "15m" / "*.parquet"))
        con = duckdb.connect()
        got = con.execute(
            "SELECT symbol, open_time FROM read_parquet(?) WHERE list_contains(?, open_time)",
            [paths, sorted({w[1] for w in want})]).fetchall()
        con.close()
        have = {(g[0], g[1]) for g in got}
        missing = [w for w in want if w not in have]
        res["candidate_entry_bars"] = {
            "candidates": len(want), "found": len(want) - len(missing),
            "missing": len(missing),
            "missing_examples": [{"symbol": w[0], "entry_utc": iso(w[1])}
                                 for w in missing[:5]],
            "scope": "全量 51 条候选，不是抽样",
        }
    return res


def main():
    syms = pool()
    wins = windows_from_upstream()
    facts = {
        "generated_at_utc": iso(int(time.time() * 1000)),
        "pool": {"n": len(syms), "excluded": sorted(EXCLUDED),
                 "source": "数据/市场数据/parquet/candles/binance/um 目录减 2 个指数"},
        "layers_definition": {
            "L1_file_exists": "冷档有 parquet 目录/文件；热库 candles 视图有行",
            "L2_window_continuous": "窗口内 open_time 期望根数 vs 实际唯一根数，缺口分三段（首/中/尾）",
            "L3_available_at": "available_at 非空 = 有抓取时刻证据；为空 ≠ 不可用，只等于没证明",
            "L4_actually_read": "由 03 号脚本做代码取证（哪个函数读哪个周期）",
        },
        "windows": wins,
        "results": {},
    }
    for key, w in wins.items():
        print(f"[{key}] {w['from_utc']} → {w['to_utc']} ...", flush=True)
        facts["results"][key] = analyze_window(key, w, syms)
        r = facts["results"][key]
        print("  ", json.dumps({tf: {k: v for k, v in r["by_timeframe"][tf].items()
                                     if k != "expected_bars"}
                                for tf in STEP}, ensure_ascii=False)[:400], flush=True)

    # 05-C 母集口径（主对象）
    if "05C" in wins:
        print("[05C_universe] 48 标的 ...", flush=True)
        facts["results"]["05C_universe"] = object_05c(wins["05C"])
        print("  ", json.dumps(facts["results"]["05C_universe"].get(
            "candidate_entry_bars"), ensure_ascii=False), flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "02_四层覆盖.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print("anomalies:", {k: v["anomaly_count"] for k, v in facts["results"].items()})


if __name__ == "__main__":
    main()

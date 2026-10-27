# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-01 / 02：接触证据台账（逐币 × 逐运行窗口）。

只读以下**索引类**文件，不读任何行情数值：
  A 回测日志/event_trend_v0_1/**/inputs.json         （运行目录索引：symbol + time_range_1h）
  B evidence/FULL_SWEEP_200x100/runs.jsonl           （200×100 扫描：symbol + start/end）
  C evidence/BACKTEST-LOSS-02/all_runs_manifest_v2.csv（批次清单：symbol + window）
  D evidence/BACKTEST-LOSS-03/*/*_summary.json       （分窗口汇总：symbol 列表 + 顶层 window）
  E evidence/BREAKEVEN-02/out/paired_fixed.csv       （母集 151 币，事件级）
  F 策略白名单/冒烟币清单                             （70 白名单、5 冒烟、36 因子扩展）

输出：
  out/02_接触运行索引.csv   一行一次运行（symbol, start, end, 来源批次, 来源文件）
  out/02_接触区间_逐币.csv   逐币合并后的接触区间（union）+ 来源 + 运行数 + 成交数
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import os
from collections import defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT_DIR = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-01/out")
RUNS_ROOT = os.path.join(ROOT, "回测日志")   # 全树扫描：event_trend_v0_1 + legacy_archive + batch_tests_archive + oi_resonance_v1
SWEEP = os.path.join(ROOT, "evidence/FULL_SWEEP_200x100")
BL02 = os.path.join(ROOT, "evidence/BACKTEST-LOSS-02/all_runs_manifest_v2.csv")
BL03 = os.path.join(ROOT, "evidence/BACKTEST-LOSS-03")
BREAKEVEN = os.path.join(ROOT, "evidence/BREAKEVEN-02/out/paired_fixed.csv")

FROZEN_WHITELIST = os.path.join(ROOT, "evidence/FT-ENTRY-03/user_data/config.json")


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def norm(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def add(rows, symbol, start, end, source, batch, path, candidates="", trades=""):
    start, end = norm(start), norm(end)
    if not symbol or not start or not end or start == "" or end == "":
        return
    if d(end) < d(start):
        return
    rows.append({
        "symbol": symbol, "start": start, "end": end,
        "source": source, "batch": batch, "path": path,
        "candidates": candidates, "trades": trades,
    })


def scan_run_dirs(rows):
    """A：运行目录索引。目录名/inputs.json 里的 symbol 与 1h 时间范围。"""
    n = 0
    for dirpath, _, files in os.walk(RUNS_ROOT):
        if "inputs.json" not in files:
            continue
        f = os.path.join(dirpath, "inputs.json")
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        tr = ((j.get("data_summary") or {}).get("time_range_1h") or {})
        sym = j.get("symbol") or ""
        st, en = tr.get("start_utc"), tr.get("end_utc")
        if not (sym and st and en):
            continue
        rel = os.path.relpath(f, ROOT)
        batch = rel.split(os.sep)[2] if len(rel.split(os.sep)) > 2 else "runs"
        add(rows, sym, st, en, "运行目录", batch, rel)
        n += 1
    return n


def scan_sweep(rows):
    """B：200×100 全扫描（不写 run 目录，只能从 runs.jsonl 取）。"""
    n = 0
    with open(os.path.join(SWEEP, "runs.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("status") not in ("ok", "no_data"):
                continue
            add(rows, r.get("symbol"), r.get("start"), r.get("end"),
                "全扫描", "FULL_SWEEP_200x100",
                "evidence/FULL_SWEEP_200x100/runs.jsonl",
                candidates=r.get("candidates", ""), trades=r.get("trades", ""))
            n += 1
    return n


def scan_bl02(rows):
    n = 0
    if not os.path.isfile(BL02):
        return 0
    with open(BL02, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            add(rows, r.get("symbol"), r.get("window_start"), r.get("window_end"),
                "批次清单", r.get("batch", ""),
                "evidence/BACKTEST-LOSS-02/all_runs_manifest_v2.csv")
            n += 1
    return n


def scan_bl03(rows):
    n = 0
    for f in sorted(glob.glob(os.path.join(BL03, "*", "*_summary.json"))):
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        w = j.get("window") or {}
        st, en = w.get("start"), w.get("end")
        batch = os.path.basename(os.path.dirname(f))
        for r in j.get("results") or []:
            add(rows, r.get("symbol"), st, en, "分窗口汇总", batch, os.path.relpath(f, ROOT))
            n += 1
    return n


def mother_set_symbols():
    syms = set()
    if os.path.isfile(BREAKEVEN):
        with open(BREAKEVEN, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r.get("symbol"):
                    syms.add(r["symbol"])
    return syms


def whitelist():
    syms = set()
    if os.path.isfile(FROZEN_WHITELIST):
        try:
            j = json.load(open(FROZEN_WHITELIST, encoding="utf-8"))
            for k in ("pair_whitelist", "exchange", "stake_currency"):
                pass
            # freqtrade 配置：exchange.pair_whitelist
            ex = j.get("exchange") or {}
            for s in ex.get("pair_whitelist") or j.get("pair_whitelist") or []:
                syms.add(s.split("/")[0].split(":")[0])
        except Exception:
            pass
    return syms


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    a = scan_run_dirs(rows)
    b = scan_sweep(rows)
    c = scan_bl02(rows)
    e = scan_bl03(rows)
    print("运行目录 %d 行；全扫描 %d 行；BL02 %d 行；BL03 %d 行" % (a, b, c, e))

    # 去重：同一 symbol/start/end 只留一条，来源合并
    merged = {}
    for r in rows:
        key = (r["symbol"], r["start"], r["end"])
        if key in merged:
            m = merged[key]
            if r["source"] not in m["source"].split("|"):
                m["source"] += "|" + r["source"]
            if r["batch"] not in m["batch"].split("|"):
                m["batch"] += "|" + r["batch"]
            if r["path"] not in m["path"].split("|"):
                m["path"] += "|" + r["path"]
            m["candidates"] = m["candidates"] or r["candidates"]
            m["trades"] = m["trades"] or r["trades"]
        else:
            merged[key] = dict(r)
    uniq = list(merged.values())
    print("去重后唯一运行窗口 %d 条，覆盖币 %d 个" % (len(uniq), len({r["symbol"] for r in uniq})))

    # 写运行索引
    p1 = os.path.join(OUT_DIR, "02_接触运行索引.csv")
    cols = ["symbol", "start", "end", "source", "batch", "candidates", "trades", "path"]
    with open(p1, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in sorted(uniq, key=lambda x: (x["symbol"], x["start"], x["end"])):
            w.writerow({k: r[k] for k in cols})

    # 逐币合并区间（union），并保留来源
    by_sym = defaultdict(list)
    for r in uniq:
        by_sym[r["symbol"]].append(r)

    out2 = []
    for sym, rs in sorted(by_sym.items()):
        rs.sort(key=lambda x: x["start"])
        cur = None
        for r in rs:
            s, e = d(r["start"]), d(r["end"])
            if cur is None:
                cur = {"start": s, "end": e, "n": 1, "src": set(r["source"].split("|")),
                       "batch": set(r["batch"].split("|")), "cand": 0, "trades": 0}
            elif s <= cur["end"] + dt.timedelta(days=1):
                cur["end"] = max(cur["end"], e)
                cur["n"] += 1
                cur["src"] |= set(r["source"].split("|"))
                cur["batch"] |= set(r["batch"].split("|"))
            else:
                out2.append((sym, cur))
                cur = {"start": s, "end": e, "n": 1, "src": set(r["source"].split("|")),
                       "batch": set(r["batch"].split("|")), "cand": 0, "trades": 0}
            for k, v in (("cand", r["candidates"]), ("trades", r["trades"])):
                try:
                    cur[k] += int(v)
                except Exception:
                    pass
        if cur:
            out2.append((sym, cur))

    # 研究母集/白名单的区间级接触（保守：整段计入，宁可多算接触）
    mothers = mother_set_symbols()
    wl = whitelist()
    research = []
    for sym in sorted(mothers):
        research.append({"symbol": sym, "start": "2021-12-23", "end": "2026-09-21",
                         "type": "保本/缩量/联合/因子扩展母集（事件级研究）"})
    for sym in sorted(wl):
        research.append({"symbol": sym, "start": "2021-12-01", "end": "2024-12-31",
                         "type": "A 路线 70 白名单 9 格研究（冻结研究期）"})
    p3 = os.path.join(OUT_DIR, "02_研究接触_区间.csv")
    with open(p3, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "start", "end", "type"])
        w.writeheader()
        w.writerows(research)

    p2 = os.path.join(OUT_DIR, "02_接触区间_逐币.csv")
    with open(p2, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "区间起", "区间止", "天数", "合并运行数", "来源类型", "来源批次", "区间内候选", "区间内成交"])
        for sym, c in out2:
            w.writerow([sym, c["start"].isoformat(), c["end"].isoformat(),
                        (c["end"] - c["start"]).days + 1, c["n"],
                        "|".join(sorted(c["src"])), "|".join(sorted(c["batch"]))[:300],
                        c["cand"], c["trades"]])
    print("写出 %s / %s / %s" % (p1, p2, p3))
    print("研究接触：母集 %d 币、白名单 %d 币" % (len(mothers), len(wl)))


if __name__ == "__main__":
    main()

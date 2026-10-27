# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-02 / 01：49 条「接触历史不清楚」区间的逐条证据采集。

只读，不跑策略、不算收益、不打开封存行情数值。

证据层级（本单核心口径）
  L4 区间内产出候选/成交   → 策略输出时间戳落在区间内（最强证据）
  L3 区间被运行窗口覆盖     → 运行索引里该币窗口与区间相交
  L2 数据被读入运行内存     → 该币任一运行都会把该币**全历史** parquet 读入后裁剪
                              （runner.py:215-240，先 concat 全部年份再按窗口切）
  L1 入库/覆盖检查记录      → 市场库 sources / coverage_cache / download_tasks + v31 manifest
  L0 无任何记录

数据来源（均为索引/元数据/策略输出，不含行情数值）：
  · evidence/FULL_SWEEP_200x100/{runs.jsonl,trades_rich.jsonl}
  · evidence/BREAKEVEN-02/out/paired_fixed.csv
  · 回测日志/**/inputs.json, trades.jsonl, candidates.jsonl
  · 数据/市场数据/db/market_v2.sqlite3（sources / coverage_cache / download_tasks，只读）
  · 数据/研究输入/WyckoffMaster_v31/逐币覆盖_v31.csv
  · 数据/市场数据/parquet/oi/...（只读 ts / source / fetched_at 三列，不读持仓量数值）

输出：out/01_证据采集_逐区间.csv、out/01_证据明细.json
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import os
import sqlite3
from collections import defaultdict

import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
T1 = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-01")
OUT = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-02/out")
MDB = os.path.join(ROOT, "数据/市场数据/db/market_v2.sqlite3")
V31 = os.path.join(ROOT, "数据/研究输入/WyckoffMaster_v31/逐币覆盖_v31.csv")
OI_DIR = os.path.join(ROOT, "数据/市场数据/parquet/oi/binance/um")
RUNS_ROOT = os.path.join(ROOT, "回测日志")

WARMUP_H = 220 * 4  # runner 实际回取的 4h 根数上限（220 根）


def ms(s):
    """'YYYY-MM-DD' / iso → epoch ms (UTC)"""
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).strip().rstrip("Z")
    if len(s) == 10:
        return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso(m):
    if m is None:
        return ""
    return dt.datetime.fromtimestamp(int(m) / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    os.makedirs(OUT, exist_ok=True)
    gaps = pd.read_csv(os.path.join(T1, "out/03_区间资格表.csv"))
    gaps = gaps[gaps["接触分层"] == "④"].copy()
    gaps["s"] = gaps["区间起"].map(ms)
    gaps["e"] = gaps["区间止"].map(lambda x: ms(x) + 86400000 - 1)  # 含末日
    print("待核查区间 %d 条" % len(gaps))

    # ── 运行索引（含窗口）──────────────────────────────
    runs = pd.read_csv(os.path.join(T1, "out/02_接触运行索引.csv"))
    runs["s"] = runs["start"].map(ms)
    runs["e"] = runs["end"].map(lambda x: ms(x) + 86400000 - 1)
    runby = defaultdict(list)
    for r in runs.itertuples():
        runby[r.symbol].append((r.s, r.e, r.source, r.batch))

    # ── 策略输出：FULL_SWEEP 逐笔 ─────────────────────
    sweep_tr = defaultdict(list)
    p = os.path.join(ROOT, "evidence/FULL_SWEEP_200x100/trades_rich.jsonl")
    for line in open(p, encoding="utf-8"):
        t = json.loads(line)
        sweep_tr[t["symbol"]].append((int(t["t_in"]), int(t["t_out"]), "全扫描逐笔"))

    # ── 策略输出：母集候选 ────────────────────────────
    mother = defaultdict(list)
    p = os.path.join(ROOT, "evidence/BREAKEVEN-02/out/paired_fixed.csv")
    for r in csv.DictReader(open(p, encoding="utf-8-sig")):
        mother[r["symbol"]].append((int(r["entry_time_ms"]), int(r["entry_time_ms"]), "母集候选"))

    # ── 策略输出：运行目录内 trades/candidates ─────────
    dirs_by_sym = defaultdict(list)
    for dirpath, _, files in os.walk(RUNS_ROOT):
        if "inputs.json" not in files:
            continue
        try:
            j = json.load(open(os.path.join(dirpath, "inputs.json"), encoding="utf-8"))
        except Exception:
            continue
        sym = j.get("symbol")
        if sym:
            dirs_by_sym[sym].append(dirpath)

    # ── L1：市场库记录 ────────────────────────────────
    con = sqlite3.connect("file:%s?mode=ro" % MDB, uri=True)
    cov_cache = {}
    for sym, tf, rows, exp, miss, upd in con.execute(
            "select symbol,timeframe,rows,expected,missing,updated_at from coverage_cache"):
        cov_cache.setdefault(sym, {})[tf] = (rows, exp, miss, upd)
    dl_task = {}
    for sym, tf, st, rows, first, last, gaps_, fin in con.execute(
            "select symbol,timeframe,status,rows,first_open,last_close,gaps,finished_at from download_tasks"):
        dl_task.setdefault(sym, {})[tf] = (st, rows, first, last, gaps_, fin)
    src = defaultdict(list)
    for sym, tf, fo, lc, imp, rowcnt in con.execute(
            "select symbol,timeframe,first_open,last_close,imported_at,row_count from sources"):
        src[sym].append((tf, fo, lc, imp, rowcnt))

    # ── v31 manifest 覆盖 ────────────────────────────
    v31 = defaultdict(dict)
    if os.path.isfile(V31):
        for r in csv.DictReader(open(V31, encoding="utf-8-sig")):
            sym = os.path.basename(r["path"]).replace(".parquet", "")
            v31[sym][r["path"]] = (r["first"], r["last"], r["rows"], r["sha256"])

    out_rows, details = [], {}
    for g in gaps.itertuples():
        sym, s, e = g.symbol, g.s, g.e
        rec = {"symbol": sym, "区间起": g.区间起, "区间止": g.区间止, "天数": int(g.天数)}
        ev = []

        # L3：窗口覆盖
        hit_win = [(a, b, src_, bat) for (a, b, src_, bat) in runby.get(sym, []) if a <= e and b >= s]
        rec["L3_窗口覆盖数"] = len(hit_win)
        if hit_win:
            ev.append({"层": "L3", "证据": "运行窗口覆盖", "明细": [{"起": iso(a), "止": iso(b), "来源": c} for a, b, c, _ in hit_win[:5]]})

        # L4：策略输出时间戳落在区间内
        outs = []
        for (a, b, tag) in sweep_tr.get(sym, []):
            if (s <= a <= e) or (s <= b <= e) or (a <= s and b >= e):
                outs.append({"来源": tag, "起": iso(a), "止": iso(b)})
        for (a, b, tag) in mother.get(sym, []):
            if s <= a <= e:
                outs.append({"来源": tag, "起": iso(a), "止": iso(b)})
        for d in dirs_by_sym.get(sym, []):
            for fn, keys in (("trades.jsonl", ("entry_time", "exit_time")),
                             ("candidates.jsonl", ("available_at_ms", "execution_earliest_ms"))):
                f = os.path.join(d, fn)
                if not os.path.isfile(f) or os.path.getsize(f) == 0:
                    continue
                try:
                    for line in open(f, encoding="utf-8"):
                        try:
                            j = json.loads(line)
                        except Exception:
                            continue
                        for k in keys:
                            v = j.get(k)
                            if isinstance(v, (int, float)) and s <= int(v) <= e:
                                outs.append({"来源": "运行目录/%s:%s" % (os.path.basename(d), fn),
                                             "字段": k, "时间": iso(int(v))})
                                break
                except Exception:
                    continue
        rec["L4_区间内策略输出数"] = len(outs)
        if outs:
            ev.append({"层": "L4", "证据": "策略输出落在区间内", "明细": outs[:5]})

        # L2：全历史读入（该币任一运行都会 concat 全部年份）
        rec["L2_该币运行数"] = len(runby.get(sym, []))
        if runby.get(sym):
            ev.append({"层": "L2", "证据": "该币 %d 个运行窗口均按「整文件读入后裁剪」加载（runner.py:215-240），区间数据被读入内存但未逐根处理" % len(runby[sym])})

        # 预热借用：后续窗口的 220 根 4h 回取是否伸进区间
        borrow = []
        for (a, b, src_, bat) in runby.get(sym, []):
            if a > e:
                ws = a - WARMUP_H * 3600 * 1000
                if ws <= e:
                    borrow.append({"后续窗口起": iso(a), "回取起点": iso(ws), "伸入区间天数": round((e - max(ws, s)) / 86400000, 1)})
        rec["ATR预热伸入区间"] = len(borrow)
        if borrow:
            ev.append({"层": "L2", "证据": "后续窗口的 4h 预热回取伸入区间（仅影响 ATR 递推，不含区间内逐根决策）", "明细": borrow[:3]})

        # L1：入库 / 覆盖 / 下载任务 / v31
        src_hit = [x for x in src.get(sym, []) if x[0] in ("4h", "1h", "15m") and x[1] <= e and x[2] >= s]
        imp = [x[3] for x in src_hit if x[3] not in (None, "None")]
        rec["入库_覆盖区间的源文件数"] = len(src_hit)
        rec["入库_最早"] = iso(min(imp)) if imp else ""
        rec["入库_最晚"] = iso(max(imp)) if imp else ""
        cc = cov_cache.get(sym, {})
        rec["覆盖检查_4h_missing"] = cc.get("4h", ("", "", "", ""))[2]
        rec["覆盖检查_1h_missing"] = cc.get("1h", ("", "", "", ""))[2]
        rec["覆盖检查_15m_missing"] = cc.get("15m", ("", "", "", ""))[2]
        rec["覆盖检查_更新时间"] = iso(cc.get("4h", ("", "", "", ""))[3]) if cc else ""
        dtask = dl_task.get(sym, {}).get("4h")
        rec["下载任务_状态"] = dtask[0] if dtask else ""
        rec["下载任务_完成时间"] = iso(dtask[5]) if dtask and dtask[5] not in (None, "None") else ""
        v31_hit = [k for k, v in v31.get(sym, {}).items()
                   if v[0] and v[1] and ms(v[0]) <= e and ms(v[1]) >= s]
        rec["v31清单_覆盖文件数"] = len(v31_hit)
        if src_hit or cc or v31_hit:
            ev.append({"层": "L1", "证据": "入库与完整性记录", "明细": {
                "源文件覆盖数": len(src_hit), "入库时间": [rec["入库_最早"], rec["入库_最晚"]],
                "覆盖检查_missing(4h/1h/15m)": [rec["覆盖检查_4h_missing"], rec["覆盖检查_1h_missing"], rec["覆盖检查_15m_missing"]],
                "v31清单文件数": len(v31_hit)}})

        # OI 时点证据（只读 ts/source/fetched_at）
        oi_files = sorted(glob.glob(os.path.join(OI_DIR, sym, "*.parquet")))
        oi_rows = 0
        oi_srcs = set()
        oi_fetched_null = 0
        oi_fetched_total = 0
        for f in oi_files:
            try:
                t = pd.read_parquet(f, columns=["ts", "source", "fetched_at"])
            except Exception:
                continue
            sub = t[(t["ts"] >= s) & (t["ts"] <= e)]
            oi_rows += len(sub)
            oi_srcs |= set(str(x) for x in sub["source"].dropna().unique())
            oi_fetched_total += len(sub)
            oi_fetched_null += int(sub["fetched_at"].isna().sum())
        exp_5m = int((e - s) / 300000) + 1
        rec["OI_5m_行数"] = oi_rows
        rec["OI_5m_应有行数"] = exp_5m
        rec["OI_5m_覆盖率"] = round(oi_rows / exp_5m, 4) if exp_5m else None
        rec["OI_source取值"] = "|".join(sorted(oi_srcs))[:80]
        rec["OI_fetched_at_空值率"] = round(oi_fetched_null / oi_fetched_total, 4) if oi_fetched_total else None
        ev.append({"层": "OI", "证据": "区间内 OI 5m 覆盖与发布时点", "明细": {
            "行数": oi_rows, "应有": exp_5m, "覆盖率": rec["OI_5m_覆盖率"],
            "source": rec["OI_source取值"], "fetched_at空值率": rec["OI_fetched_at_空值率"]}})

        # 数据条件（沿用 01 单结论，此处只复述）
        rec["数据条件_OI覆盖两端"] = g.OI覆盖
        rec["数据条件_真实mark"] = g.真实mark可用
        rec["数据条件_200根预热"] = g.满足200根预热
        rec["覆盖日历段"] = g.覆盖日历段

        out_rows.append(rec)
        details["%s|%s|%s" % (sym, g.区间起, g.区间止)] = ev

    p1 = os.path.join(OUT, "01_证据采集_逐区间.csv")
    cols = list(out_rows[0].keys())
    with open(p1, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)
    with open(os.path.join(OUT, "01_证据明细.json"), "w", encoding="utf-8") as fh:
        json.dump(details, fh, ensure_ascii=False, indent=1)
    print("写出 %s（%d 行）与证据明细" % (p1, len(out_rows)))
    print("L3 命中 %d 条；L4 命中 %d 条；ATR 预热伸入 %d 条" % (
        sum(1 for r in out_rows if r["L3_窗口覆盖数"]),
        sum(1 for r in out_rows if r["L4_区间内策略输出数"]),
        sum(1 for r in out_rows if r["ATR预热伸入区间"])))


if __name__ == "__main__":
    main()

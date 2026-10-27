#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OOS-READY-01 只读探针：数据覆盖与既有研究接触面。

只读：evidence 下既有产物、数据/市场数据/parquet 文件名、热库 market_v2.sqlite3（mode=ro）。
不写任何生产文件；全部输出到 evidence/OOS-READY-01/out/。
"""
import csv
import glob
import json
import os
import sqlite3
import datetime as dt

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/OOS-READY-01/out")
PQ = os.path.join(ROOT, "数据/市场数据/parquet")
DB = os.path.join(ROOT, "数据/市场数据/db/market_v2.sqlite3")


def iso(ms):
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def probe_kline_year_coverage():
    """K 线：按年文件名统计有多少标的有该年 4h/1h/15m 分片（文件名级，非行级）。"""
    rows = []
    for tf in ("4h", "1h", "15m"):
        per_year = {}
        for f in glob.glob(os.path.join(PQ, "candles/binance/um/*", tf, "*.parquet")):
            name = os.path.basename(f).replace(".parquet", "")
            if name.isdigit():
                sym = os.path.basename(os.path.dirname(os.path.dirname(f)))
                per_year.setdefault(name, set()).add(sym)
        for y in sorted(per_year):
            rows.append({"asset": "candles", "timeframe": tf, "year": y,
                         "symbols_with_file": len(per_year[y]),
                         "granularity": "文件名级"})
    return rows


def probe_oi_year_coverage():
    rows = []
    for f in glob.glob(os.path.join(PQ, "oi/binance/um/*", "*.parquet")):
        name = os.path.basename(f).replace(".parquet", "")
        if name.isdigit():
            rows.append((name, os.path.basename(os.path.dirname(f))))
    agg = {}
    for y, s in rows:
        agg.setdefault(y, set()).add(s)
    return [{"asset": "oi_5m", "timeframe": "5m", "year": y,
             "symbols_with_file": len(agg[y]), "granularity": "文件名级"}
            for y in sorted(agg)]


def probe_funding_mark():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("""select strftime('%Y', funding_time/1000, 'unixepoch') y,
                          count(*) tot,
                          sum(case when mark_price is not null and mark_price!='' then 1 else 0 end) mk
                   from funding_history group by y order by y""")
    rows = [{"year": y, "funding_events": t, "mark_nonnull": m,
             "mark_coverage_pct": round(m / t * 100, 2)} for y, t, m in cur.fetchall()]
    cur.execute("select min(funding_time), max(funding_time) from funding_history")
    lo, hi = cur.fetchone()
    cur.execute("select count(*), count(distinct symbol) from funding_history")
    n, nsym = cur.fetchone()
    con.close()
    return {"by_year": rows, "min": iso(lo), "max": iso(hi),
            "total_rows": n, "symbols": nsym}


def probe_oi_row_level():
    """行级：2020/2021 年 OI 文件的实际时间范围（抽样全部 2020 文件 + 2021 前 12 个）。"""
    try:
        import pyarrow.parquet as pq
        import pyarrow.compute as pc
    except Exception as e:
        return {"error": f"pyarrow 不可用: {e}"}
    out = []
    files = sorted(glob.glob(os.path.join(PQ, "oi/binance/um/*", "2020.parquet")))[:5]
    files += sorted(glob.glob(os.path.join(PQ, "oi/binance/um/*", "2021.parquet")))[:12]
    for f in files:
        t = pq.read_table(f, columns=["ts"])
        mn = pc.min(t["ts"]).as_py()
        mx = pc.max(t["ts"]).as_py()
        out.append({"symbol": os.path.basename(os.path.dirname(f)),
                    "year_file": os.path.basename(f),
                    "rows": t.num_rows, "min": iso(mn), "max": iso(mx)})
    return {"sampled": len(out), "detail": out}


def probe_sweep_window_union():
    plan = json.load(open(os.path.join(ROOT, "evidence/FULL_SWEEP_200x100/plan.json")))
    iv = sorted((dt.date.fromisoformat(g["start"]), dt.date.fromisoformat(g["end"])) for g in plan)
    merged = []
    for s, e in iv:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    starts = [s for s, _ in iv]
    gaps = [(starts[i + 1] - starts[i]).days for i in range(len(starts) - 1)]
    return {"groups": len(plan),
            "merged_intervals": [{"start": str(s), "end": str(e), "days": (e - s).days} for s, e in merged],
            "start_step_days_median": sorted(gaps)[len(gaps) // 2],
            "symbols_union": len({s for g in plan for s in g["symbols"]})}


def probe_sweep_funnel():
    agg = {}
    for line in open(os.path.join(ROOT, "evidence/FULL_SWEEP_200x100/runs.jsonl")):
        d = json.loads(line)
        y = d["start"][:4]
        a = agg.setdefault(y, dict(runs=0, cand=0, trades=0, net=0.0, bars1h=0, zero_cand=0, no_data=0))
        a["runs"] += 1
        if d["status"] != "ok":
            a["no_data"] += 1
            continue
        a["cand"] += d["candidates"]
        a["trades"] += d["trades"]
        a["net"] += d["net"]
        a["bars1h"] += d["bars1h"]
        if d["candidates"] == 0:
            a["zero_cand"] += 1
    return agg


def main():
    os.makedirs(OUT, exist_ok=True)
    log = []
    k = probe_kline_year_coverage()
    o = probe_oi_year_coverage()
    m = probe_funding_mark()
    r = probe_oi_row_level()
    u = probe_sweep_window_union()
    f = probe_sweep_funnel()

    with open(os.path.join(OUT, "00_数据覆盖探针.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["asset", "timeframe", "year", "symbols_with_file", "granularity"])
        w.writeheader()
        for row in k + o:
            w.writerow(row)

    json.dump({"funding_mark": m, "oi_row_level": r, "sweep_window_union": u,
               "sweep_funnel_by_start_year": f},
              open(os.path.join(OUT, "00_数据覆盖探针.json"), "w"), ensure_ascii=False, indent=2)

    print("== K线/ OI 文件名级覆盖 ==")
    for row in k + o:
        print(f"  {row['asset']:8s} {row['timeframe']:4s} {row['year']}  {row['symbols_with_file']:4d} 币")
    print("== 资金费 mark ==")
    print("  总行", m["total_rows"], "币", m["symbols"], "范围", m["min"], "~", m["max"])
    for row in m["by_year"]:
        print(f"  {row['year']} 事件{row['funding_events']:8d} mark{row['mark_nonnull']:8d} {row['mark_coverage_pct']:6.2f}%")
    print("== OI 行级抽样 ==")
    if "detail" in r:
        for d in r["detail"][:6]:
            print(f"  {d['symbol']:14s} {d['year_file']} rows={d['rows']} {d['min']} ~ {d['max']}")
        print("  ... 共", r["sampled"], "个抽样")
    print("== FULL_SWEEP 窗口并集 ==", u["merged_intervals"], "标的并集", u["symbols_union"])
    print("== FULL_SWEEP 漏斗（按窗口起点年）==")
    for y in sorted(f):
        a = f[y]
        print(f"  {y} 运行{a['runs']:5d} 候选{a['cand']:6d} 成交{a['trades']:5d} 净{a['net']:10.2f} 零候选{a['zero_cand']:5d} no_data{a['no_data']}")
    log.append("done")
    print("\n输出：", OUT)


if __name__ == "__main__":
    main()

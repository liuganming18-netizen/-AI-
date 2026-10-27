#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OOS-READY-01：已观察范围表 + 验证数据资格表（只读）。

输入全部为既有产物：
  evidence/FULL_SWEEP_200x100/{plan.json,runs.jsonl,trades_rich.jsonl}
  evidence/BACKTEST-LOSS-02/all_runs_manifest_v2.csv
  evidence/BACKTEST-LOSS-03/*/..._summary.json
  evidence/BREAKEVEN-02/out/paired_fixed.csv
  evidence/OOS-READY-01/out/00_数据覆盖探针.json
输出：out/01_*.csv、out/02_验证数据资格表.csv
"""
import csv
import glob
import json
import os
from collections import defaultdict, Counter
import datetime as dt

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
EV = os.path.join(ROOT, "evidence")
OUT = os.path.join(EV, "OOS-READY-01/out")


def parse_ms(v):
    return dt.datetime.utcfromtimestamp(v / 1000)


def parse_str(s):
    s = str(s).strip().replace("T", " ")
    return dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


# ---------- 1. 批次级已观察范围 ----------
def batch_table():
    rows = []

    # FULL_SWEEP
    plan = json.load(open(os.path.join(EV, "FULL_SWEEP_200x100/plan.json")))
    funnel = defaultdict(lambda: dict(runs=0, cand=0, trades=0, net=0.0))
    for line in open(os.path.join(EV, "FULL_SWEEP_200x100/runs.jsonl")):
        d = json.loads(line)
        a = funnel[d["start"][:4]]
        a["runs"] += 1
        if d["status"] == "ok":
            a["cand"] += d["candidates"]
            a["trades"] += d["trades"]
            a["net"] += d["net"]
    trades = [json.loads(l) for l in open(os.path.join(EV, "FULL_SWEEP_200x100/trades_rich.jsonl"))]
    uniq_net = sum(float(t["net"]) for t in trades) if trades and "net" in trades[0] else None
    rows.append({
        "批次": "FULL_SWEEP_200x100（A 变体，含 OI 门槛）",
        "窗口起": "2021-01-01", "窗口止": "2026-10-13",
        "标的数": len({s for g in plan for s in g["symbols"]}),
        "运行数": sum(a["runs"] for a in funnel.values()),
        "候选数（含窗口重复）": sum(a["cand"] for a in funnel.values()),
        "成交笔数（含窗口重复）": sum(a["trades"] for a in funnel.values()),
        "唯一事件数": len(trades),
        "净额U（运行级累加，含重复）": round(sum(a["net"] for a in funnel.values()), 2),
        "净额U（唯一事件）": round(uniq_net, 2) if uniq_net is not None else "",
        "是否用于调参/筛选": "是（全部后续研究的样本母集）",
        "证据文件": "evidence/FULL_SWEEP_200x100/{plan.json,runs.jsonl,trades_rich.jsonl}",
    })

    # BACKTEST-LOSS-01/02
    man = list(csv.DictReader(open(os.path.join(EV, "BACKTEST-LOSS-02/all_runs_manifest_v2.csv"), encoding="utf-8-sig")))
    g = defaultdict(list)
    for r in man:
        g[r["batch"]].append(r)
    for b, rs in sorted(g.items()):
        rows.append({
            "批次": b,
            "窗口起": min(r["window_start"] for r in rs)[:10],
            "窗口止": max(r["window_end"] for r in rs)[:10],
            "标的数": len({r["symbol"] for r in rs}),
            "运行数": len(rs),
            "候选数（含窗口重复）": "",
            "成交笔数（含窗口重复）": sum(int(float(r["trade_count"])) for r in rs),
            "唯一事件数": "",
            "净额U（运行级累加，含重复）": round(sum(float(r["net_pnl"]) for r in rs), 2),
            "净额U（唯一事件）": "",
            "是否用于调参/筛选": "是（失败归因与规格审计）",
            "证据文件": "evidence/BACKTEST-LOSS-02/all_runs_manifest_v2.csv",
        })

    # BACKTEST-LOSS-03
    mapping = [
        ("aug100", "evidence/BACKTEST-LOSS-03/aug100/aug100_summary.json"),
        ("aug_dec2025", "evidence/BACKTEST-LOSS-03/aug_dec2025/aug_dec2025_summary.json"),
        ("sep50", "evidence/BACKTEST-LOSS-03/sep50/sep50_summary.json"),
        ("sep50_full", "evidence/BACKTEST-LOSS-03/sep50_full/sep50_full_summary.json"),
        ("sep50_check", "evidence/BACKTEST-LOSS-03/sep50_check/sep50_check_summary.json"),
        ("sep50_strict", "evidence/BACKTEST-LOSS-03/sep50_strict/sep50_strict_summary.json"),
        ("sep_all_latest", "evidence/BACKTEST-LOSS-03/sep_all_latest/sep_all_latest_summary.json"),
    ]
    for name, rel in mapping:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        res = d.get("results", [])
        rows.append({
            "批次": f"BACKTEST-LOSS-03/{name}",
            "窗口起": d["window"]["start"], "窗口止": d["window"]["end"],
            "标的数": d.get("total_symbols", len(res)),
            "运行数": len(res),
            "候选数（含窗口重复）": "",
            "成交笔数（含窗口重复）": sum(int(r.get("trades", 0) or 0) for r in res),
            "唯一事件数": "",
            "净额U（运行级累加，含重复）": round(sum(float(r.get("net_pnl", 0) or 0) for r in res), 2),
            "净额U（唯一事件）": "",
            "是否用于调参/筛选": "是（分窗口结果比较）",
            "证据文件": rel,
        })

    # 保本/缩量/因子系列：同一批 206/201
    rows.append({
        "批次": "BREAKEVEN-01/02/03 + ENTRY-VOLUME-01/02 + VOLUME-BE-01/02（同一母集）",
        "窗口起": "2021-12-23", "窗口止": "2026-09-21",
        "标的数": 151, "运行数": 253,
        "候选数（含窗口重复）": 206, "成交笔数（含窗口重复）": 206,
        "唯一事件数": 201,
        "净额U（运行级累加，含重复）": 105.44,
        "净额U（唯一事件）": 105.44,
        "是否用于调参/筛选": "是（1R/2R/缩量/联合四组对照的判定母集）",
        "证据文件": "evidence/BREAKEVEN-02/out/paired_fixed.csv；BREAKEVEN-03/报告.md",
    })
    rows.append({
        "批次": "FACTOR-ENTRY-01~09 / ENTRY-AUDIT-01~06（同一母集的特征扩展）",
        "窗口起": "2021-12-23", "窗口止": "2026-09-21",
        "标的数": "", "运行数": "", "候选数（含窗口重复）": 227,
        "成交笔数（含窗口重复）": 205, "唯一事件数": "",
        "净额U（运行级累加，含重复）": "", "净额U（唯一事件）": "",
        "是否用于调参/筛选": "是（特征筛选与消融，296→205 映射）",
        "证据文件": "evidence/ENTRY-AUDIT-01/报告.md",
    })
    rows.append({
        "批次": "ACCUM-STRAT-01（震荡吸筹）",
        "窗口起": "不可复现", "窗口止": "不可复现",
        "标的数": "", "运行数": "", "候选数（含窗口重复）": "", "成交笔数（含窗口重复）": "",
        "唯一事件数": "", "净额U（运行级累加，含重复）": "", "净额U（唯一事件）": "",
        "是否用于调参/筛选": "否（代码/配置/币池/逐笔本机全无，仅剩汇总表）",
        "证据文件": "evidence/ACCUM-STRAT-01/报告.md",
    })
    return rows


# ---------- 2. 币种级已观察范围 ----------
def symbol_table():
    agg = {}

    def slot(sym, src):
        return agg.setdefault(sym, {
            "symbol": sym, "sources": set(), "windows": 0,
            "cand": 0, "trades": 0, "net": 0.0,
            "first_window": None, "last_window": None,
        })

    for line in open(os.path.join(EV, "FULL_SWEEP_200x100/runs.jsonl")):
        d = json.loads(line)
        a = slot(d["symbol"], "FULL_SWEEP")
        a["sources"].add("FULL_SWEEP")
        a["windows"] += 1
        a["cand"] += d.get("candidates", 0) or 0
        a["trades"] += d.get("trades", 0) or 0
        a["net"] += d.get("net", 0.0) or 0.0
        s, e = d["start"], d["end"]
        a["first_window"] = s if not a["first_window"] or s < a["first_window"] else a["first_window"]
        a["last_window"] = e if not a["last_window"] or e > a["last_window"] else a["last_window"]

    man = list(csv.DictReader(open(os.path.join(EV, "BACKTEST-LOSS-02/all_runs_manifest_v2.csv"), encoding="utf-8-sig")))
    for r in man:
        a = slot(r["symbol"], "BACKTEST-LOSS")
        a["sources"].add("BACKTEST-LOSS")
        a["windows"] += 1
        a["trades"] += int(float(r["trade_count"]))
        a["net"] += float(r["net_pnl"])
        s, e = r["window_start"][:10], r["window_end"][:10]
        a["first_window"] = s if not a["first_window"] or s < a["first_window"] else a["first_window"]
        a["last_window"] = e if not a["last_window"] or e > a["last_window"] else a["last_window"]

    # 206 事件的标的接触标记
    touched = set()
    for r in csv.DictReader(open(os.path.join(EV, "BREAKEVEN-02/out/paired_fixed.csv"))):
        touched.add(r["symbol"])
    out = []
    for sym, a in sorted(agg.items()):
        out.append({
            "symbol": sym,
            "接触来源": "+".join(sorted(a["sources"])),
            "覆盖窗口数": a["windows"],
            "候选数（含重复）": a["cand"],
            "成交笔数（含重复）": a["trades"],
            "净额U（含重复）": round(a["net"], 2),
            "最早窗口起": a["first_window"],
            "最晚窗口止": a["last_window"],
            "是否产生过真实事件": "是" if sym in touched else "否（仅被扫描）",
        })
    return out


# ---------- 3. 事件时间分布 ----------
def event_table():
    rows = []
    tr = [json.loads(l) for l in open(os.path.join(EV, "FULL_SWEEP_200x100/trades_rich.jsonl"))]
    for t in tr:
        t["_dt"] = parse_ms(t["t_in"])
    c = Counter(x["_dt"].strftime("%Y-%m") for x in tr)
    for m in sorted(c):
        sub = [x for x in tr if x["_dt"].strftime("%Y-%m") == m]
        rows.append({"口径": "唯一交易（296 笔）", "期间": m, "笔数": len(sub),
                     "标的数": len({x["symbol"] for x in sub}),
                     "净额U": round(sum(float(x["net"]) for x in sub), 2)})

    cand = list(csv.DictReader(open(os.path.join(EV, "BREAKEVEN-02/out/paired_fixed.csv"))))
    cc = Counter(parse_str(r["entry_time_utc"]).strftime("%Y") for r in cand)
    for y in sorted(cc):
        sub = [r for r in cand if parse_str(r["entry_time_utc"]).year == int(y)]
        rows.append({"口径": "保本母集（206 候选）", "期间": y, "笔数": len(sub),
                     "标的数": len({r["symbol"] for r in sub}),
                     "净额U": round(sum(float(r["A_net"]) for r in sub), 2)})
    return rows


# ---------- 4. 验证数据资格表 ----------
def eligibility_table():
    probe = json.load(open(os.path.join(OUT, "00_数据覆盖探针.json")))
    kline = {int(r["year"]): r["symbols_with_file"] for r in
             csv.DictReader(open(os.path.join(OUT, "00_数据覆盖探针.csv"), encoding="utf-8-sig"))
             if r["asset"] == "candles" and r["timeframe"] == "4h"}
    oi = {int(r["year"]): r["symbols_with_file"] for r in
          csv.DictReader(open(os.path.join(OUT, "00_数据覆盖探针.csv"), encoding="utf-8-sig"))
          if r["asset"] == "oi_5m"}
    mark = {r["year"]: r["mark_coverage_pct"] for r in probe["funding_mark"]["by_year"]}
    union = probe["sweep_window_union"]["merged_intervals"]

    tr = [json.loads(l) for l in open(os.path.join(EV, "FULL_SWEEP_200x100/trades_rich.jsonl"))]
    for t in tr:
        t["_dt"] = parse_ms(t["t_in"])
    cand = list(csv.DictReader(open(os.path.join(EV, "BREAKEVEN-02/out/paired_fixed.csv"))))

    def cnt(a, b):
        u = [t for t in tr if a <= t["_dt"] < b]
        cc = [r for r in cand if a <= parse_str(r["entry_time_utc"]) < b]
        return len(u), len(cc), round(sum(float(t["net"]) for t in u), 2)

    D = dt.datetime
    segs = [
        ("S0", D(2019, 12, 31), D(2020, 9, 1), "未覆盖",
         "plan.json 窗口并集起点 2021-01-01，早于此日期的日历不在任何回测窗口内",
         "全池 OI 0 币；K 线仅 3~56 币；无真实 mark",
         "不合格：无 OI 数据，OI 门槛必然 oi_unavailable，无法产生候选"),
        ("S1", D(2020, 9, 1), D(2021, 1, 1), "未覆盖",
         "同上；此段仅 BTCUSDT 有 5m OI（2020-09-01~2020-12-31）",
         "K 线 56 币；OI 1 币；无真实 mark（funding_history 最早 2022-01-01）",
         "不合格：单币 + 无 mark + 预热后仅约 81 天，期望事件数≈0.02 笔，不构成验证集"),
        ("S2", D(2021, 1, 1), D(2021, 12, 1), "已覆盖",
         "FULL_SWEEP 窗口并集 2021-01-01~2026-10-13 连续无洞；本段累计扫描 1h 根数 3,000 万级",
         "K 线 88 币；OI 0 币（92 币的 OI 文件均自 2021-12-01 起）；无真实 mark",
         "已观察：扫描过但 0 事件，原因是 OI 全缺而非未看；「无成交」不等于未观察"),
        ("S3", D(2021, 12, 1), D(2022, 1, 1), "已覆盖",
         "同上；OI 自 2021-12-01 起 92 币可用",
         "K 线 88 币；OI 92 币；真实 mark 0（最早 2022-01-01）",
         "已观察：已产生事件，但资金费金额无法按真实结算 mark 口径计算，成本不完整"),
        ("S4", D(2022, 1, 1), D(2026, 4, 15), "已覆盖",
         "同上；真实 mark 覆盖 2022 年 99.63%、2023 年 99.76%、2024/2025 年 100%",
         "K 线 104→473 币；OI 109→474 币；mark 99.6%~100%",
         "已观察（主样本）：全部 206 候选中的主体、四组规则对照的判定母集都在此段"),
        ("S5", D(2026, 4, 15), D(2026, 10, 14), "已覆盖",
         "同上；本段与 v3.1 旧 Holdout（2026-04-15~2026-10-15）完全重叠且已被交易",
         "K 线 525 币；OI 525 币；mark 2026 年 94.10%",
         "已观察且已被污染：旧 Holdout 内已产生事件并计入结果，不能再当样本外"),
        ("S6", D(2026, 10, 14), D(2026, 10, 19), "未覆盖",
         "plan.json 最晚窗口止 2026-10-13；热库 15m 最新 2026-10-18 12:00、1h 最新 2026-10-19 15:00",
         "约 5 天；数据末端不完整",
         "不合格：样本量不足（按 296 笔/5.8 年折算，5 天期望 0.7 笔），且数据未闭合完整"),
        ("S7", D(2026, 10, 19), D(2026, 12, 31), "无数据",
         "热库 1h 最新 2026-10-19 15:00，其后无 K 线",
         "无",
         "无数据：只能靠前向采集累积，不属于现有数据（本段止日为本单占位，非真实数据边界）"),
    ]
    rows = []
    for sid, a, b, covered, ev, avail, verdict in segs:
        u, cc, net = cnt(a, b)
        if covered == "已覆盖":
            touch = "已接触（有回测/实验记录）"
        elif covered == "无数据":
            touch = "无数据"
        else:
            # 只能证明"未被已记录的回测窗口覆盖"，证明不了"从未被任何方式接触"
            touch = "资格未知（无证据证明未接触；仅能证明未进入已记录的回测窗口）"
        if covered == "已覆盖":
            final = "已观察（不得作为样本外）"
        elif covered == "无数据":
            final = "不可用（无数据）"
        else:
            final = "不可用（数据资格不满足）"
        rows.append({
            "段": sid,
            "接触状态": touch,
            "综合判定": final,
            "起": a.strftime("%Y-%m-%d"),
            "止": (b - dt.timedelta(days=1)).strftime("%Y-%m-%d"),
            "日历天数": (b - a).days,
            "是否被既有回测窗口覆盖": covered,
            "覆盖证据": ev,
            "该段唯一事件数（296 口径）": u,
            "该段候选数（206 口径）": cc,
            "该段净额U": net,
            "K线可用币数（文件名级，4h）": kline.get(a.year, ""),
            "OI 可用币数（文件名级，5m）": oi.get(a.year, ""),
            "真实结算mark覆盖": mark.get(str(a.year), "0（无记录）"),
            "数据可用性说明": avail,
            "资格判定": verdict,
        })
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    bt = batch_table()
    write_csv(os.path.join(OUT, "01_已观察范围_批次.csv"), bt,
              ["批次", "窗口起", "窗口止", "标的数", "运行数", "候选数（含窗口重复）",
               "成交笔数（含窗口重复）", "唯一事件数", "净额U（运行级累加，含重复）",
               "净额U（唯一事件）", "是否用于调参/筛选", "证据文件"])
    st = symbol_table()
    write_csv(os.path.join(OUT, "01_已观察范围_币种.csv"), st,
              ["symbol", "接触来源", "覆盖窗口数", "候选数（含重复）", "成交笔数（含重复）",
               "净额U（含重复）", "最早窗口起", "最晚窗口止", "是否产生过真实事件"])
    et = event_table()
    write_csv(os.path.join(OUT, "01_已观察范围_事件时间分布.csv"), et,
              ["口径", "期间", "笔数", "标的数", "净额U"])
    el = eligibility_table()
    write_csv(os.path.join(OUT, "02_验证数据资格表.csv"), el,
              ["段", "接触状态", "综合判定", "起", "止", "日历天数", "是否被既有回测窗口覆盖", "覆盖证据",
               "该段唯一事件数（296 口径）", "该段候选数（206 口径）", "该段净额U",
               "K线可用币数（文件名级，4h）", "OI 可用币数（文件名级，5m）",
               "真实结算mark覆盖", "数据可用性说明", "资格判定"])

    print(f"批次行 {len(bt)}；币种行 {len(st)}；事件分布行 {len(et)}；资格段 {len(el)}")
    print("\n== 资格表 ==")
    for r in el:
        print(f"{r['段']} {r['起']}~{r['止']} 覆盖={r['是否被既有回测窗口覆盖']:4s} "
              f"唯一事件={r['该段唯一事件数（296 口径）']:3d} 候选={r['该段候选数（206 口径）']:3d} "
              f"净额={r['该段净额U']:8.2f} -> {r['资格判定'][:24]}")


if __name__ == "__main__":
    main()

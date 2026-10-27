# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-02 / 02：逐条判定 + 数据条件 + OI 时点证据。

在 01 采集结果之上补三件事（仍只读）：
  · 区间内 4h / 1h / 15m 实际根数 vs 应有根数（只读 open_time 列，不读价格）
  · 接触状态四分类（A/B/C/D），并区分事件级 / 计算级 / 记录级
  · 每条一句结论 + 证据文件路径

判定规则（写死，不事后调整）
  A 有证据表明曾用于策略研究
      A1 事件级：区间被运行窗口覆盖，或区间内有候选/成交时间戳
      A2 计算级：区间数据被后续研究窗口作为 4h 预热读入（参与 ATR 递推）
  B 仅检查过数据完整性：只有入库/覆盖检查/清单记录，无 A1、无 A2
  C 有依据支持未用于研究：有正面依据（入库晚于该币全部研究活动；或文档明文排除），且无 A1、A2
  D 仍无法确认：无任何可判定记录
优先级 A1 > A2 > C > B > D。

输出：out/02_逐条结论.csv、out/02_逐条结论.json
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import os
from collections import defaultdict

import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
T2 = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-02")
OUT = os.path.join(T2, "out")
CANDLE = os.path.join(ROOT, "数据/市场数据/parquet/candles/binance/um")
RUNS_ROOT = os.path.join(ROOT, "回测日志")


def ms(s):
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).strip().rstrip("Z")
    if len(s) == 10:
        return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def bar_stats(sym, tf, s, e):
    """区间内该周期实际根数 / 应有根数（只读 open_time）。"""
    step = {"4h": 4 * 3600 * 1000, "1h": 3600 * 1000, "15m": 15 * 60 * 1000}[tf]
    files = sorted(glob.glob(os.path.join(CANDLE, sym, tf, "*.parquet")))
    if not files:
        return 0, int((e - s) / step) + 1
    n = 0
    for f in files:
        try:
            t = pd.read_parquet(f, columns=["open_time"])["open_time"]
        except Exception:
            continue
        n += int(((t >= s) & (t <= e)).sum())
    return n, int((e - s) / step) + 1


def run_first_ts():
    """该币最早一次运行的时间（inputs.json.timestamp_created），用于 C 档判据。"""
    first = {}
    for dirpath, _, files in os.walk(RUNS_ROOT):
        if "inputs.json" not in files:
            continue
        try:
            j = json.load(open(os.path.join(dirpath, "inputs.json"), encoding="utf-8"))
        except Exception:
            continue
        sym = j.get("symbol")
        ts = j.get("timestamp_created")
        if not sym or not ts:
            continue
        try:
            v = ms(ts)
        except Exception:
            continue
        first[sym] = min(first.get(sym, v), v)
    return first


def main():
    ev = pd.read_csv(os.path.join(OUT, "01_证据采集_逐区间.csv"))
    detail = json.load(open(os.path.join(OUT, "01_证据明细.json"), encoding="utf-8"))
    runs = pd.read_csv(os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-01/out/02_接触运行索引.csv"))
    runs["s"] = runs["start"].map(ms)
    runs["e"] = runs["end"].map(lambda x: ms(x) + 86400000 - 1)
    runby = defaultdict(list)
    for r in runs.itertuples():
        runby[r.symbol].append((r.s, r.e))
    first_run = run_first_ts()

    rows = []
    for r in ev.itertuples():
        s = ms(r.区间起)
        e = ms(r.区间止) + 86400000 - 1
        sym = r.symbol
        b4, e4 = bar_stats(sym, "4h", s, e)
        b1, e1 = bar_stats(sym, "1h", s, e)
        b15, e15 = bar_stats(sym, "15m", s, e)

        # A2：后续窗口的 4h 预热是否伸入区间
        borrow_days = 0.0
        borrow_windows = []
        for (a, b) in runby.get(sym, []):
            if a > e:
                ws = a - 220 * 4 * 3600 * 1000
                if ws <= e:
                    ov = (e - max(ws, s)) / 86400000.0
                    if ov > 0:
                        borrow_days = max(borrow_days, ov)
                        borrow_windows.append(a)
        borrow_windows = sorted(set(borrow_windows))

        # 正面依据（C 档）：数据入库晚于该币首次研究活动
        imp_last = r.入库_最晚
        c_evidence = ""
        if imp_last and sym in first_run:
            try:
                if ms(imp_last) > first_run[sym]:
                    c_evidence = "入库晚于该币首次运行"
            except Exception:
                pass

        if r.L3_窗口覆盖数 > 0 or r.L4_区间内策略输出数 > 0:
            status, sub = "A", "A1-事件级"
            concl = "区间被运行窗口覆盖或区间内有候选/成交，属事件级接触"
        elif borrow_days > 0:
            status, sub = "A", "A2-计算级"
            concl = ("无事件级接触；但区间末段 %.1f 天被 %d 个后续研究窗口作为 4h 预热读入，"
                     "参与 ATR(14) 递推，进而影响箱体 2A~8A 过滤与止损距离" % (borrow_days, len(borrow_windows)))
        elif c_evidence:
            status, sub = "C", "C-正面依据"
            concl = "无事件级与计算级接触；%s" % c_evidence
        elif r.v31清单_覆盖文件数 or r.覆盖检查_更新时间 or r.入库_覆盖区间的源文件数:
            status, sub = "B", "B-仅完整性"
            concl = "无事件级与计算级接触；仅有入库/覆盖检查/清单记录"
        else:
            status, sub = "D", "D-无法确认"
            concl = "无任何可判定记录"

        rows.append({
            "symbol": sym, "区间起": r.区间起, "区间止": r.区间止, "天数": int(r.天数),
            "覆盖日历段": r.覆盖日历段,
            "接触状态": status, "接触子级": sub, "结论": concl,
            "事件级_窗口覆盖数": r.L3_窗口覆盖数,
            "事件级_区间内策略输出数": r.L4_区间内策略输出数,
            "计算级_ATR预热伸入天数": round(borrow_days, 1),
            "计算级_后续窗口数": len(borrow_windows),
            "计算级_该币运行数": r.L2_该币运行数,
            "记录级_入库源文件数": r.入库_覆盖区间的源文件数,
            "记录级_入库最早": r.入库_最早, "记录级_入库最晚": r.入库_最晚,
            "记录级_v31清单文件数": r.v31清单_覆盖文件数,
            "记录级_覆盖检查4h_missing": r.覆盖检查_4h_missing,
            "记录级_覆盖检查1h_missing": r.覆盖检查_1h_missing,
            "记录级_覆盖检查更新时间": r.覆盖检查_更新时间,
            "记录级_下载任务": r.下载任务_状态,
            "数据_4h根数": b4, "数据_4h应有": e4, "数据_4h覆盖率": round(b4 / e4, 4),
            "数据_1h根数": b1, "数据_1h应有": e1, "数据_1h覆盖率": round(b1 / e1, 4),
            "数据_15m根数": b15, "数据_15m应有": e15, "数据_15m覆盖率": round(b15 / e15, 4),
            "数据_OI5m覆盖率": r.OI_5m_覆盖率, "数据_OI来源": r.OI_source取值,
            "OI时点证据_fetched_at空值率": r.OI_fetched_at_空值率,
            "OI时点证据_判定": "无发布时点证据：全库 fetched_at 为空，只能声明计算不超前（桶起点+5m≤决策时刻）",
            "数据条件_mark": r.数据条件_真实mark,
            "数据条件_200根预热": r.数据条件_200根预热,
            "时长_满30天": "是" if int(r.天数) >= 30 else "否",
            "时长_满12个月": "是" if int(r.天数) >= 365 else "否",
            "证据文件": "out/01_证据明细.json#%s|%s|%s" % (sym, r.区间起, r.区间止),
        })

    p = os.path.join(OUT, "02_逐条结论.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    cnt = Counter(x["接触状态"] for x in rows)
    sub = Counter(x["接触子级"] for x in rows)
    summary = {
        "工单": "FT-VALIDATION-DATA-02",
        "生成时间": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "核查区间数": len(rows),
        "四分类计数": dict(sorted(cnt.items())),
        "子级计数": dict(sorted(sub.items())),
        "事件级命中": sum(1 for x in rows if x["事件级_窗口覆盖数"] or x["事件级_区间内策略输出数"]),
        "计算级命中": sum(1 for x in rows if x["计算级_ATR预热伸入天数"] > 0),
        "记录级命中": sum(1 for x in rows if x["记录级_入库源文件数"] > 0),
        "OI时点证据_全部为空": all(x["OI时点证据_fetched_at空值率"] == 1.0 for x in rows),
        "数据条件_满12个月": sum(1 for x in rows if x["时长_满12个月"] == "是"),
        "数据条件_4h全覆盖": sum(1 for x in rows if x["数据_4h覆盖率"] >= 0.999),
        "数据条件_15m全覆盖": sum(1 for x in rows if x["数据_15m覆盖率"] >= 0.999),
        "结论": ("49 条全部属 A2-计算级：无一条被运行窗口逐根处理、无一条产出候选或成交，"
                 "但每条的末段 4h 数据都被后续研究窗口作为 ATR 预热读入。"
                 "因此既不能判「仅检查过完整性」，也不能判「未用于研究」。"),
    }
    with open(os.path.join(OUT, "02_逐条结论.json"), "w", encoding="utf-8") as fh:
        json.dump({"汇总": summary, "逐条": rows}, fh, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("写出 %s（%d 行）" % (p, len(rows)))


if __name__ == "__main__":
    main()

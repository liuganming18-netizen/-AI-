# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-02 / 05：实证「4h 预热确实被加载」——用运行自报的根数核对。

不看任何价格：只读每个运行目录的 inputs.json（data_summary.bar_4h_count 与 time_range_1h）。
若 bar_4h_count ≈ 窗口内 4h 根数 + 220，则说明该运行把窗口之前的 4h（即区间末段）读进了内存；
结合 runner.py:225（ATR 在全历史上先算、后切片），这些根参与了 ATR(14) 递推。

输出：out/05_预热加载实证.csv
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import os

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
T2 = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-02")
OUT = os.path.join(T2, "out")
RUNS_ROOT = os.path.join(ROOT, "回测日志")


def ms(s):
    s = str(s).strip().rstrip("Z")
    if len(s) == 10:
        return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def main():
    concl = list(csv.DictReader(open(os.path.join(OUT, "02_逐条结论.csv"), encoding="utf-8-sig")))
    gaps = {r["symbol"]: (ms(r["区间起"]), ms(r["区间止"]) + 86400000 - 1) for r in concl}
    want = {r["symbol"] for r in concl if float(r["计算级_ATR预热伸入天数"]) > 0}

    # 收集所有运行
    runs = []
    for dirpath, _, files in os.walk(RUNS_ROOT):
        if "inputs.json" not in files:
            continue
        try:
            j = json.load(open(os.path.join(dirpath, "inputs.json"), encoding="utf-8"))
        except Exception:
            continue
        sym = j.get("symbol")
        ds = j.get("data_summary") or {}
        tr = ds.get("time_range_1h") or {}
        if sym not in want or not tr.get("start_utc") or not tr.get("end_utc"):
            continue
        runs.append({
            "symbol": sym, "dir": os.path.relpath(dirpath, ROOT),
            "start": tr["start_utc"], "end": tr["end_utc"],
            "bar4h": int(ds.get("bar_4h_count") or 0), "bar1h": int(ds.get("bar_1h_count") or 0),
            "s": ms(tr["start_utc"]), "e": ms(tr["end_utc"]),
        })

    rows = []
    for sym, (gs, ge) in gaps.items():
        borrow = [r for r in runs if r["symbol"] == sym and r["s"] > ge and r["s"] - 220 * 4 * 3600 * 1000 <= ge]
        if not borrow:
            # 该币的后续窗口只来自全扫描（不落运行目录）⇒ 无根数自报，只能给代码级证据
            rows.append({
                "symbol": sym,
                "区间起": dt.datetime.fromtimestamp(gs / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
                "区间止": dt.datetime.fromtimestamp(ge / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
                "后续窗口起": "（全扫描窗口，不落运行目录）", "后续窗口止": "",
                "窗口天数": "", "窗口内4h根数_估": "", "运行自报4h根数": "",
                "多出根数": "", "是否≈220": "代码级",
                "窗口内1h根数_估": "", "运行自报1h根数": "",
                "运行目录": "evidence/FULL_SWEEP_200x100/runs.jsonl（无 run 目录）",
            })
            continue
        borrow.sort(key=lambda x: x["s"])
        r = borrow[0]
        win_days = (r["e"] - r["s"]) / 86400000.0
        win_bars = int(win_days * 6) + 1                     # 4h 根数（窗口内）
        extra = r["bar4h"] - win_bars
        rows.append({
            "symbol": sym, "区间起": dt.datetime.fromtimestamp(gs / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
            "区间止": dt.datetime.fromtimestamp(ge / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
            "后续窗口起": r["start"], "后续窗口止": r["end"],
            "窗口天数": round(win_days, 1),
            "窗口内4h根数_估": win_bars,
            "运行自报4h根数": r["bar4h"],
            "多出根数": extra,
            "是否≈220": "是" if 190 <= extra <= 240 else "否",
            "窗口内1h根数_估": int((r["e"] - r["s"]) / 3600000) + 1,
            "运行自报1h根数": r["bar1h"],
            "运行目录": r["dir"],
        })

    p = os.path.join(OUT, "05_预热加载实证.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    ok = sum(1 for r in rows if r["是否≈220"] == "是")
    code = sum(1 for r in rows if r["是否≈220"] == "代码级")
    print("覆盖 %d/%d 条区间；%d 条有根数实证（多出 190~240 根≈220 根预热），%d 条仅代码级" % (
        len(rows), len(gaps), ok, code))
    for r in rows[:5]:
        print("  %-14s 窗口 %s~%s  4h 自报 %d / 窗口估 %d / 多 %d" % (
            r["symbol"], r["后续窗口起"][:10], r["后续窗口止"][:10],
            r["运行自报4h根数"], r["窗口内4h根数_估"], r["多出根数"]))


if __name__ == "__main__":
    main()

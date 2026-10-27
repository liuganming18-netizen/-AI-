# -*- coding: utf-8 -*-
"""
成交价落在当根 K 线区间内的硬校验（只读，纯标准库，不依赖数值计算库）。

规则（门禁）：
    成交价必须落在成交时刻所在 15m K 线的 [low, high] 区间内。
    允许一个极小的相对容差（默认 0.1%），用于吸收 K 线与成交时刻的
    四舍五入差异；超出容差即判该笔成交「价格不可能存在」。

用法：
    python3 verify_fill_within_bar.py            # 扫描全部运行，打印汇总
    python3 verify_fill_within_bar.py --csv 输出路径.csv
"""

import argparse
import csv
import glob
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS_DIR = os.path.join(PROJECT_ROOT, "回测日志", "event_trend_v0_1", "runs")
TOL = 0.001  # 相对容差 0.1%


def _bar_tuple(item):
    """兼容三种存法：数组、字典、以及带字段名的对象。"""
    if isinstance(item, (list, tuple)):
        return int(item[0]), float(item[1]), float(item[2]), float(item[3]), float(item[4])
    if isinstance(item, dict):
        ts = (item.get("ts") or item.get("t") or item.get("time")
              or item.get("timestamp") or item.get("open_time"))
        o = item.get("open", item.get("o"))
        h = item.get("high", item.get("h"))
        l = item.get("low", item.get("l"))
        c = item.get("close", item.get("c"))
        return int(ts), float(o), float(h), float(l), float(c)
    raise TypeError("无法识别的K线格式：%r" % (type(item),))


def load_candles(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    candles = data.get("candles") if isinstance(data, dict) else data
    if isinstance(candles, dict):
        candles = list(candles.values())
    out = {}
    for item in candles or []:
        ts, o, h, l, c = _bar_tuple(item)
        out[ts] = (o, h, l, c)
    return out


def check_price(px, bar, tol=TOL):
    """返回 (是否在区间内, 相对越界幅度)。"""
    o, h, l, c = bar
    if px < l * (1 - tol) or px > h * (1 + tol):
        span = h - l if h > l else (h or 1.0)
        dev = (px - h) / span if px > h else (px - l) / span
        return False, dev
    return True, 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--runs-dir", default=RUNS_DIR)
    args = ap.parse_args()

    rows = []
    n_runs = n_trades = 0
    for d in sorted(glob.glob(os.path.join(args.runs_dir, "run_*"))):
        tf = os.path.join(d, "trades.jsonl")
        cf = os.path.join(d, "chart.json")
        if not (os.path.exists(tf) and os.path.exists(cf)):
            continue
        trades = []
        with open(tf, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        if not trades:
            continue
        n_runs += 1
        candles = load_candles(cf)
        for t in trades:
            n_trades += 1
            sym = t.get("symbol")
            for leg, tkey, pkey in (("入场", "entry_time", "entry_price"),
                                    ("出场", "exit_time", "exit_price")):
                ts = t.get(tkey)
                px = t.get(pkey)
                if ts is None or px is None:
                    continue
                bar = candles.get(int(ts))
                if bar is None:
                    rows.append([os.path.basename(d), sym, leg, int(ts), px,
                                 None, None, None, "缺K线", ""])
                    continue
                ok, dev = check_price(float(px), bar)
                if not ok:
                    rows.append([os.path.basename(d), sym, leg, int(ts),
                                 float(px), bar[2], bar[1], round(dev, 4),
                                 "越界", t.get("net_pnl")])

    bad_trades = len({(r[0], r[1]) for r in rows})
    print("扫描含成交的运行：%d 个，成交笔数：%d 笔" % (n_runs, n_trades))
    print("判定为「价格不可能存在」的腿数：%d" % len(rows))
    print("涉及成交笔数：%d" % bad_trades)

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["运行目录", "标的", "腿", "时间戳", "成交价",
                        "当根最低", "当根最高", "越界幅度", "判定", "该笔净盈亏"])
            w.writerows(rows)
        print("明细已写入：%s" % args.csv)

    if not rows:
        return 0
    print("\n越界明细：")
    for r in rows[:40]:
        print("  %-52s %-14s %s 成交 %-10s 区间[%s, %s] %s" % (
            r[0].replace("run_WX_EVENT_TREND_V0_1_", ""), r[1], r[2],
            r[4], r[5], r[6], r[8]))
    return 1


if __name__ == "__main__":
    sys.exit(main())

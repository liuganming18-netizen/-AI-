# -*- coding: utf-8 -*-
"""
把回测运行目录里的逐笔成交，同步进前端使用的 all_trades.json。

背景：
    前端「逐笔明细」页读的是 `UI界面/web/public/all_trades.json`。
    旧批次脚本自带同步逻辑，新脚本没带，导致新跑的成交在页面上看不到。
    本工具补上这一步，并且只做「追加 + 去重」，不改动已有条目。

用法：
    python 工具_同步前端逐笔.py --runs-dir 回测日志/event_trend_v0_1/runs_2025aug100
    python 工具_同步前端逐笔.py --runs-dir A --runs-dir B --dry-run
"""

import argparse
import datetime
import glob
import json
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(PROJECT_ROOT, "UI界面", "web", "public", "all_trades.json")
INVALID_CSV = os.path.join(PROJECT_ROOT, "evidence", "BACKTEST-LOSS-03",
                           "price_impossible_fills.csv")


def load_invalid_keys():
    """已知「成交价不可能存在」的（运行目录, 标的）集合。"""
    keys = set()
    if not os.path.exists(INVALID_CSV):
        return keys
    import csv
    with open(INVALID_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            keys.add((row["运行目录"], row["标的"]))
    return keys


def collect(runs_dir):
    out = []
    for d in sorted(glob.glob(os.path.join(runs_dir, "run_*"))):
        tf = os.path.join(d, "trades.jsonl")
        if not (os.path.exists(tf) and os.path.getsize(tf) > 0):
            continue
        run_id = os.path.basename(d)
        with open(tf, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                t = json.loads(line)
                t["run_id"] = run_id
                t["entry_time_ms"] = t.get("entry_time") or t.get("entry_time_ms")
                t["exit_time_ms"] = t.get("exit_time") or t.get("exit_time_ms")
                # ⚠ 禁止用 order_id 顶替 trade_id：单笔详情只认账本 trd_…，
                #   冒充会让「点实验记录」请求 /trades/ord_… ⇒ 404（2026-10-18 事故）。
                t["order_id"] = t.get("order_id")
                t["trade_id"] = t.get("trade_id") or None
                out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", action="append", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    invalid = load_invalid_keys()

    if os.path.exists(TARGET):
        with open(TARGET, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"total": 0, "updated_at": None, "items": []}
    items = data.get("items", [])

    seen = {(i.get("run_id"), i.get("trade_id") or i.get("order_id")) for i in items}
    # 给既有条目补上价格有效性标记（已知越界的标 false，其余标 true）
    for i in items:
        if "price_valid" not in i:
            bad = (i.get("run_id"), i.get("symbol")) in invalid
            i["price_valid"] = not bad
            if bad:
                i["invalid_reason"] = "成交价越出当根K线区间"

    added = []
    for rd in args.runs_dir:
        for t in collect(rd):
            key = (t["run_id"], t["trade_id"])
            if key in seen:
                continue
            seen.add(key)
            bad = (t["run_id"], t["symbol"]) in invalid
            t["price_valid"] = not bad
            if bad:
                t["invalid_reason"] = "成交价越出当根K线区间"
            added.append(t)

    items.extend(added)
    items.sort(key=lambda x: x.get("entry_time_ms") or 0, reverse=True)

    print("原有 %d 条，新增 %d 条，合计 %d 条" % (len(items) - len(added), len(added), len(items)))
    if args.dry_run:
        for t in added:
            print("  + %-14s %+8.2f %s" % (t.get("symbol"), t.get("net_pnl", 0), t.get("run_id")))
        return

    data["total"] = len(items)
    data["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    data["items"] = items
    with open(TARGET, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("已写入 %s" % TARGET)


if __name__ == "__main__":
    main()

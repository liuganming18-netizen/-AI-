#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 核对 `candles_source` 是否可信（只读）

问题：`backtest_api._finalize_chart_meta` 里有一句

    if not chart.get("candles_source"):
        chart["candles_source"] = "读时按本 run 自己的标的与窗口取真实 K 线（…），未写回 run 目录"

这句是**默认填充**，不是事实判断。只要原 chart 没写这个字段，哪怕 K 线就是盘上那份，
也会被贴上「读时取、未写回」。所以不能拿这个字段统计「有多少 run 的 K 线是重建的」。

本脚本做法：对抽样的 E 类 run（盘上 candles 是对象且非空），比较
  · 盘上 `chart.json.candles` 根数与首末时间戳
  · 接口返回的 `chart.candles` 根数与首末时间戳
  · 接口 `candles_source` 的文案
三者一对照，就能判定：数据来自盘上，但标签说「读时取」。

并发 2（读侧多并发会压垮后端，见 02b 的事故记录）。输出 out/09_candles_source核对.json
"""
import json
import os
import random
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/09_candles_source核对.json")
SRC = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/02c_重开实测.json")
BASE = "http://127.0.0.1:8787/api/v1/backtests/{}/chart"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
random.seed(20261025)


def probe(item):
    rel, rid = item["run"], item["run"].split("/")[-1]
    dp = os.path.join(ROOT, rel)
    rec = {"run": rel}
    try:
        d = json.load(open(os.path.join(dp, "chart.json"), encoding="utf-8"))
        disk = d.get("candles") or []
        rec["disk_bars"] = len(disk)
        if disk and isinstance(disk[0], dict):
            rec["disk_first_t"] = disk[0].get("t")
            rec["disk_last_t"] = disk[-1].get("t")
        rec["disk_source"] = d.get("candles_source")
    except Exception as e:                                     # noqa: BLE001
        rec["disk_err"] = f"{type(e).__name__}"
        return rec
    try:
        with OPENER.open(BASE.format(quote(rid, safe="")), timeout=40) as r:
            body = r.read()
    except Exception as e:                                     # noqa: BLE001
        rec["api_err"] = f"{type(e).__name__}: {e}"[:80]
        return rec
    try:
        c = (json.loads(body).get("chart") or {})
    except Exception:                                          # noqa: BLE001
        rec["api_err"] = "json 解析失败"
        return rec
    bars = c.get("candles") or []
    rec["api_bars"] = len(bars)
    if bars:
        rec["api_first_t"] = bars[0].get("t")
        rec["api_last_t"] = bars[-1].get("t")
    rec["api_source"] = c.get("candles_source")
    rec["derived_from"] = bool(c.get("derived_from"))
    # 判定：盘上有 K 线且接口根数与首末时间完全一致 ⇒ 数据来自盘上
    same = (rec.get("disk_bars") == rec.get("api_bars")
            and rec.get("disk_first_t") == rec.get("api_first_t")
            and rec.get("disk_last_t") == rec.get("api_last_t")
            and rec.get("disk_bars", 0) > 0)
    rec["data_from_disk"] = bool(same)
    rec["label_says_read_live"] = bool("读时" in (rec.get("api_source") or ""))
    return rec


def main() -> None:
    src = json.load(open(SRC, encoding="utf-8"))
    pool = [x for x in src["明细"]
            if x["bucket"] == "200-有K线" and x["state"] == "E 对象K线"]
    take = random.sample(pool, min(40, len(pool)))
    print(f"E 类有K线 {len(pool)}，抽 {len(take)}")
    with ThreadPoolExecutor(max_workers=2) as p:
        res = list(p.map(probe, take))

    ok = [r for r in res if "api_err" not in r and "disk_err" not in r]
    cnt = Counter()
    for r in ok:
        if r["data_from_disk"] and r["label_says_read_live"]:
            cnt["数据来自盘上、标签却说读时取"] += 1
        elif r["data_from_disk"] and not r["label_says_read_live"]:
            cnt["数据来自盘上、标签未提读时"] += 1
        elif not r["data_from_disk"] and r["label_says_read_live"]:
            cnt["数据非盘上（确为重建）、标签说读时"] += 1
        else:
            cnt["其它（需人工看）"] += 1

    out = {
        "抽样数": len(take),
        "可用": len(ok),
        "判定": dict(cnt),
        "结论": ("`candles_source` 是默认填充文案，不能用来统计「重建率」；"
                 "判「K 线有没有留存」只能看盘上 chart.json 的 candles 是否为空。"),
        "明细": res,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "明细"}, ensure_ascii=False, indent=1))
    print("→", OUT)


if __name__ == "__main__":
    main()

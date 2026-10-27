#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 重开实测（分层全量 + 抽样，只读）

上一版对 4,289 个 run 全量请求，把 8787 接口压到 10 秒/请求，已停止。
本版按「结论需要」分层取数：

  · A 数组非空（375）—— 全量（已在 02 跑过，这里复核）
  · D 缺 chart（920）—— 全量（决定「空图」到底是有原因还是无原因）
  · 归档（449）—— 全量（404 很快，验证提示文案）
  · B 数组空（1,268）—— 抽 150
  · E 对象K线（1,317）—— 抽 150
  · D 缺来源/重建（1,102）—— 抽 150

每类给出「200 有K线 / 200 空图有原因 / 200 空图无原因 / 400 / 404」计数。
不写任何 run 目录。
"""
import json
import os
import random
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/02b_重开实测.json")
BASE = "http://127.0.0.1:8787/api/v1/backtests/{}/chart"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
random.seed(20261025)


def probe(item):
    rel, tier, state = item["run"], item["tier"], item["state"]
    rid = rel.split("/")[-1]
    from urllib.parse import quote
    rec = {"run": rel, "tier": tier, "state": state}
    try:
        with OPENER.open(BASE.format(quote(rid, safe="")), timeout=25) as r:
            body = r.read()
            rec["http"] = r.status
    except urllib.error.HTTPError as e:
        rec["http"] = e.code
        rec["err"] = e.read().decode("utf-8", "replace")[:120]
        return rec
    except Exception as e:                                     # noqa: BLE001
        rec["http"] = -1
        rec["err"] = f"{type(e).__name__}: {e}"[:120]
        return rec
    try:
        d = json.loads(body)
    except Exception:                                          # noqa: BLE001
        rec["http"] = -2
        return rec
    c = d.get("chart") or {}
    bars = c.get("candles") or []
    rec.update({
        "bars": len(bars),
        "exec_tf": c.get("exec_timeframe"),
        "display_tf": c.get("display_timeframe"),
        "blocked": (c.get("chart_blocked_reason") or "")[:100] or None,
        "top_missing": (d.get("missing_reason") or "")[:100] or None,
        "derived_from": bool(c.get("derived_from")),
    })
    return rec


def main() -> None:
    layer = json.load(open(os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/01_产物分层.json"),
                           encoding="utf-8"))
    # 用 01 的按批次状态重建逐 run 分类：直接复用 02 的分桶逻辑，但来源换成 01 的明细不可得，
    # 因此这里重扫目录（只判存在性，快）
    import re
    RUN_ID_RE = re.compile(
        r"^(bt_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}|run_WX_EVENT_TREND_V0_1_.*|run_FT_ENTRY_09_.*"
        r"|run_FT_STOP20_.*|run_FT_EXIT01_.*|run_FT_STOPGRID01_.*|run_FT_STOPGRID02_.*"
        r"|run_FT_LOW4H_.*)$")
    src = json.load(open(os.path.join(ROOT, "evidence/PROJECT-REVIEW-07/out/03_回测可追溯性.json"),
                        encoding="utf-8"))
    buckets = defaultdict(list)
    for r in src["记录"]:
        rel = r["run"]
        batch = "/".join(rel.split("/")[1:-1])
        tier = ("支持-默认根" if batch == "event_trend_v0_1/runs"
                else "支持-同级批次" if batch.startswith("event_trend_v0_1/runs")
                else "归档-不在扫描范围")
        ch = r.get("chart") or {}
        files = r.get("files") or {}
        if not files.get("chart.json"):
            st = "D 缺 chart"
        elif ch.get("candle_is_object") is False and (ch.get("candle_n") or 0) > 0:
            st = "A 数组非空"
        elif ch.get("candle_is_object") is False:
            st = "B 数组空"
        else:
            st = "E 对象K线"
        buckets[(tier, st)].append(rel)
    # 重建产物
    extra = json.load(open(os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/01b_未覆盖run目录.json"),
                           encoding="utf-8"))["记录"]
    for x in extra:
        rel = x["run"]
        batch = "/".join(rel.split("/")[1:-1])
        tier = ("支持-默认根" if batch == "event_trend_v0_1/runs"
                else "支持-同级批次" if batch.startswith("event_trend_v0_1/runs")
                else "归档-不在扫描范围")
        buckets[(tier, "D 缺来源(重建)")].append(rel)

    plan = []
    for key, runs in buckets.items():
        tier, st = key
        if st in ("A 数组非空", "D 缺 chart") or tier.startswith("归档"):
            take = runs
        else:
            take = random.sample(runs, min(150, len(runs)))
        plan += [{"run": x, "tier": tier, "state": st} for x in take]

    print("探测", len(plan), {f"{k[0]}/{k[1]}": len(v) for k, v in buckets.items()})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(probe, plan))

    def bucket(r):
        if r["http"] == 200:
            if (r.get("bars") or 0) > 0:
                return "200-有K线"
            return "200-空图-有原因" if (r.get("top_missing") or r.get("blocked")) else "200-空图-无原因"
        if r["http"] == 400:
            return "400-读侧异常"
        if r["http"] == 404:
            return "404-未知run_id"
        return f"{r['http']}-其它"

    agg = defaultdict(Counter)
    samples = {}
    for r in results:
        r["bucket"] = bucket(r)
        agg[f"{r['tier']} / {r['state']}"][r["bucket"]] += 1
        samples.setdefault(f"{r['tier']}|{r['state']}|{r['bucket']}", {
            "run": r["run"], "http": r["http"], "bars": r.get("bars"),
            "top_missing": r.get("top_missing"), "blocked": r.get("blocked"),
            "err": r.get("err")})

    out = {
        "探测数": len(results),
        "分层探测计划": {f"{k[0]}/{k[1]}": len(v) for k, v in buckets.items()},
        "按层与状态": {k: dict(v) for k, v in agg.items()},
        "总计": dict(Counter(r["bucket"] for r in results)),
        "提示样本": samples,
        "说明": "A 数组非空、D 缺 chart、归档 三类为全量；B/E/重建 各抽 150（随机种子 20261025）。",
        "明细": results,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for k, v in out["按层与状态"].items():
        print(f"{k:44s}", v)
    print("总计", out["总计"])
    print("→", OUT)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 重开实测补测（只读）

为什么有这一步：`02b_重开实测.py` 用 6 并发跑 2,230 个请求时，后端进程在
中途重启（pid 79140 → 2978），1,010 个请求返回 `Connection refused`、
6 个返回 `RemoteDisconnected`。这 1,016 条**不是 run 的问题**，是探测环境问题，
不能计入统计，必须作废重测。

本脚本：
  1. 只挑 02b 里 http == -1 的记录重测（其余保留原结论）；
  2. 并发降到 2，每个请求最多重试 3 次（退避 1s/2s/4s）；
  3. 每次重试前先打 /api/v1/health，健康才发业务请求；
  4. 合并 02b 的有效结果，输出 02c_重开实测.json（含「作废条数」与「补测后仍失败条数」）。

不写任何 run 目录。
"""
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUTDIR = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out")
SRC = os.path.join(OUTDIR, "02b_重开实测.json")
OUT = os.path.join(OUTDIR, "02c_重开实测.json")
BASE = "http://127.0.0.1:8787/api/v1/backtests/{}/chart"
HEALTH = "http://127.0.0.1:8787/api/v1/health"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health_ok() -> bool:
    try:
        with OPENER.open(HEALTH, timeout=5) as r:
            return r.status == 200
    except Exception:                                          # noqa: BLE001
        return False


def probe_once(rel, tier, state):
    rid = rel.split("/")[-1]
    rec = {"run": rel, "tier": tier, "state": state}
    try:
        with OPENER.open(BASE.format(quote(rid, safe="")), timeout=40) as r:
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
        "candles_source": (c.get("candles_source") or "")[:160] or None,
        "window_matches": c.get("candles_window_matches_run_data"),
        "symbol": c.get("symbol"),
    })
    return rec


def probe(item):
    for attempt in range(3):
        if not health_ok():
            time.sleep(3 * (attempt + 1))
            continue
        rec = probe_once(item["run"], item["tier"], item["state"])
        if rec["http"] != -1:
            rec["retries"] = attempt
            return rec
        time.sleep(2 ** attempt)
    rec = probe_once(item["run"], item["tier"], item["state"])
    rec["retries"] = "exhausted"
    return rec


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


def main() -> None:
    src = json.load(open(SRC, encoding="utf-8"))
    det = src["明细"]
    bad = [x for x in det if x["http"] == -1]
    good = [x for x in det if x["http"] != -1]
    print(f"02b 总 {len(det)}，作废 {len(bad)}，沿用 {len(good)}")

    if not health_ok():
        print("后端不健康，退出（不产生结论）")
        raise SystemExit(1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        fixed = list(pool.map(probe, [{"run": x["run"], "tier": x["tier"], "state": x["state"]}
                                      for x in bad]))

    still = [x for x in fixed if x["http"] == -1]
    results = good + fixed
    for r in results:
        r["bucket"] = bucket(r)

    agg = defaultdict(Counter)
    samples = {}
    for r in results:
        agg[f"{r['tier']} / {r['state']}"][r["bucket"]] += 1
        samples.setdefault(f"{r['tier']}|{r['state']}|{r['bucket']}", {
            "run": r["run"], "http": r["http"], "bars": r.get("bars"),
            "top_missing": r.get("top_missing"), "blocked": r.get("blocked"),
            "err": r.get("err"), "candles_source": r.get("candles_source"),
            "window_matches": r.get("window_matches"), "symbol": r.get("symbol")})

    # 只统计「200 有K线」里的来源构成：盘上原始 vs 读时重建
    src_mix = Counter()
    for r in results:
        if r.get("bucket") == "200-有K线":
            cs = r.get("candles_source") or ""
            if "读时" in cs or "重建" in cs:
                src_mix["读时从行情库重建"] += 1
            elif cs:
                src_mix["其它来源自述"] += 1
            else:
                src_mix["无来源自述"] += 1

    out = {
        "02b探测数": len(det),
        "作废条数": len(bad),
        "作废原因": "后端进程在 02b 运行中重启（pid 79140 → 2978），1,010 个 Connection refused + 6 个 RemoteDisconnected",
        "补测后仍失败": len(still),
        "有效总数": len(results),
        "按层与状态": {k: dict(v) for k, v in agg.items()},
        "总计": dict(Counter(r["bucket"] for r in results)),
        "有K线来源构成": dict(src_mix),
        "提示样本": samples,
        "说明": ("A 数组非空 375 全量、D 缺 chart 全量、归档全量；B/E/重建 各抽 150（种子 20261025）。"
                 "补测并发 2、每请求最多重试 3 次。"),
        "明细": results,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for k, v in out["按层与状态"].items():
        print(f"{k:44s}", v)
    print("总计", out["总计"])
    print("有K线来源构成", out["有K线来源构成"])
    print("补测后仍失败", len(still))
    print("→", OUT)


if __name__ == "__main__":
    main()

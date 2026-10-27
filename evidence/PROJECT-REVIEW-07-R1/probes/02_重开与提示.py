#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 重开与提示实测（只读，走本机 8787 接口）

回答三件事，全部以**实际 HTTP 响应**为准：
  1. 「应能重开却报错」到底报什么错、有没有可操作提示；
  2. 「归档 / 不索引」的 run 被打开时，接口说的是「已归档」还是「未知 run_id」；
  3. 空图时原因写在哪个字段、是否非空。

不写任何文件到 run 目录；只读接口 + 写本单 out/。
"""
import json
import os
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
LAYER = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/01_产物分层.json")
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/02_重开与提示.json")
BASE = "http://127.0.0.1:8787/api/v1/backtests/{}/chart"


def get(run_id: str) -> dict:
    url = BASE.format(run_id)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
            return {"http": r.status, "body": body}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode("utf-8", "replace")}
    except Exception as e:                                    # noqa: BLE001
        return {"http": -1, "body": f"{type(e).__name__}: {e}"}


def summarize(run_id: str, tier: str, state: str, res: dict) -> dict:
    body = res["body"]
    rec = {"run_id": run_id, "tier": tier, "state": state, "http": res["http"]}
    try:
        d = json.loads(body)
    except Exception:                                          # noqa: BLE001
        rec["raw"] = body[:200]
        return rec
    if "chart" in d:
        c = d["chart"] or {}
        bars = c.get("candles") or []
        ts = [b["t"] for b in bars[:80] if isinstance(b, dict)]
        step = None
        if len(ts) > 1:
            diffs = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
            step = diffs[len(diffs) // 2] // 1000
        rec.update({
            "bars": len(bars), "step_s": step,
            "exec_tf": c.get("exec_timeframe"), "display_tf": c.get("display_timeframe"),
            "blocked": c.get("chart_blocked_reason"),
            "top_missing": d.get("missing_reason"),
            "derived_from": c.get("derived_from"),
        })
    else:
        rec["err"] = (d.get("错误") or d.get("missing_reason") or body)[:220]
        rec["err_keys"] = list(d)
    return rec


def main() -> None:
    layer = json.load(open(LAYER, encoding="utf-8"))
    # 分层文件只存了聚合；这里按 run 重新分桶，用上一单 out/03 + 目录枚举补齐的结果
    src = json.load(open(os.path.join(ROOT, "evidence/PROJECT-REVIEW-07/out/03_回测可追溯性.json"),
                        encoding="utf-8"))
    by_state = defaultdict(list)
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
        by_state[(tier, st)].append(r["run"])

    probes = []
    # A 全量探（375 个，验证「应能重开却报错」是不是普遍成立）
    probes += [(x, "支持-默认根", "A 数组非空") for x in by_state[("支持-默认根", "A 数组非空")]]
    # B / D / E / 归档 分层抽样
    for key in list(by_state):
        if key[1] == "A 数组非空":
            continue
        probes += [(x, key[0], key[1]) for x in by_state[key][:6]]
    # 重建产物（上一单没覆盖的 runs_ft_replay_01）单独抽 6 个
    replay_dir = os.path.join(ROOT, "回测日志/event_trend_v0_1/runs_ft_replay_01")
    replay = [d for d in sorted(os.listdir(replay_dir)) if d.startswith("run_")][:6]
    probes += [(f"回测日志/event_trend_v0_1/runs_ft_replay_01/{d}", "支持-同级批次", "D 缺来源(重建)") for d in replay]

    print("探测数", len(probes))
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda t: summarize(t[0].split("/")[-1], t[1], t[2], get(t[0].split("/")[-1])), probes))

    agg = defaultdict(Counter)
    for r in results:
        agg[(r["tier"], r["state"])][f"HTTP {r['http']}"] += 1
    samples = {}
    for r in results:
        key = f"{r['tier']}|{r['state']}|HTTP{r['http']}"
        samples.setdefault(key, r)

    out = {
        "探测数": len(results),
        "按层与状态": {f"{k[0]} / {k[1]}": dict(v) for k, v in agg.items()},
        "提示文案样本": samples,
        "明细": results,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    for k, v in out["按层与状态"].items():
        print(k, v)
    print("--- 提示样本 ---")
    for k, v in samples.items():
        print(k, "|", v.get("err") or v.get("top_missing") or v.get("blocked") or f"bars={v.get('bars')}")
    print("→", OUT)


if __name__ == "__main__":
    main()

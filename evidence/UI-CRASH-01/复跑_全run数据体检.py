#!/usr/bin/env python3
"""对全部 run 做数据侧体检：找会引发前端崩溃的形态（trade_id 错位 / candles 非数组 / 接口 500）。"""
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "http://127.0.0.1:8787/api/v1/backtests"


def get(url):
    with urllib.request.urlopen(url, timeout=90) as r:
        return r.status, json.loads(r.read().decode())


def check(rid):
    rec = {"run": rid, "issues": []}
    try:
        st, d = get(f"{BASE}/{rid}/chart")
        if st != 200:
            rec["issues"].append(f"chart HTTP {st}")
        ch = d.get("chart") or {}
        cds = ch.get("candles")
        if cds is not None and cds and not isinstance(cds[0], (list, tuple)):
            rec["issues"].append(f"candles 首元素不是数组：{type(cds[0]).__name__}")
        for t in (ch.get("trades") or [])[:5]:
            tid = t.get("trade_id")
            if tid and str(tid).startswith("ord_"):
                rec["issues"].append(f"chart.trades.trade_id 是订单号：{tid}")
                break
            if tid is None:
                rec["issues"].append("chart.trades.trade_id 为 null")
                break
        st2, d2 = get(f"{BASE}/{rid}/trades")
        if st2 != 200:
            rec["issues"].append(f"trades HTTP {st2}")
        rows = d2.get("items") or []
        for r in rows[:5]:
            if not r.get("trade_id"):
                rec["issues"].append("逐笔 trade_id 为空")
                break
    except Exception as exc:
        rec["issues"].append(f"异常：{type(exc).__name__} {str(exc)[:120]}")
    return rec


st, d = get(f"{BASE}?limit=200")
runs = [it["run_id"] for it in (d.get("items") or [])]
print("run 总数:", len(runs))

with ThreadPoolExecutor(max_workers=6) as ex:
    results = list(ex.map(check, runs))

bad = [r for r in results if r["issues"]]
print("有问题 run 数:", len(bad))
for r in bad[:20]:
    print(" -", r["run"], r["issues"][:3])
json.dump(results, open("/tmp/run_health.json", "w"), ensure_ascii=False, indent=1)

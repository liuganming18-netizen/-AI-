#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本轮收口验收：接口耗时 / 体积 / 画布七段依赖，全部实测落盘。

判定纪律：
- HTTP 200 只说明「接口可达」，不说明业务正确；
- 本脚本只负责「同一个请求，优化前后各花多少毫秒、多大字节」这一件事；
- 七段是否判绿由页面自己的判定逻辑决定（见 10_canvas_state.mjs），
  这里**不替它下结论**。
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evidence/DB-PERF-01/out"
OUT.mkdir(parents=True, exist_ok=True)

BASE = "http://127.0.0.1:8787"

# 画布七段真正依赖的路径 + 本轮重点优化的两个接口
PATHS = [
    ("/api/v1/data/pool-scope", "1 交易所来源"),
    ("/api/v1/pool/status", "1/3/7 池分母"),
    ("/api/v1/data/import-plan", "2 实时采集"),
    ("/api/v1/data/status", "3/4 热库与水位"),
    ("/api/v1/data/cold-archive-status", "3 冷库（本轮新增路由）"),
    ("/api/v1/paper/status", "4/6 引擎与账本"),
    ("/api/v1/paper/accounts", "6 模拟账本"),
    ("/api/v1/signals?scope=current", "5 信号引擎"),
    ("/api/v1/backtests?limit=1", "7 工作台"),
    ("/api/v1/system-flow/paper-reconciliation", "7 逐笔对账（本轮新增路由）"),
    ("/api/v1/data/feeds", "数据中心 15 项数据源（本轮优化）"),
    ("/api/v1/data/coverage", "大响应体（对照）"),
    ("/api/v1/markets", "大响应体（对照）"),
]


def main() -> int:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    rows = []
    for path, why in PATHS:
        s = time.perf_counter()
        try:
            r = opener.open(BASE + path, timeout=180)
            body = r.read()
            ms = (time.perf_counter() - s) * 1000
            rows.append({"path": path, "why": why, "http": r.status,
                         "ms": round(ms, 1), "bytes": len(body)})
        except Exception as exc:                                   # noqa: BLE001
            ms = (time.perf_counter() - s) * 1000
            rows.append({"path": path, "why": why, "http": "ERR",
                         "ms": round(ms, 1), "bytes": 0, "error": str(exc)})

    result = {
        "captured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base": BASE,
        "note": ("每行是一次真实 HTTP 请求的耗时与响应体积。"
                 "首次访问已由服务启动预热覆盖，所以这里反映的是用户实际会遇到的情况。"),
        "rows": rows,
        "totals": {
            "requests": len(rows),
            "sum_ms": round(sum(r["ms"] for r in rows), 1),
            "max_ms": max(r["ms"] for r in rows),
            "sum_bytes": sum(r["bytes"] for r in rows),
        },
    }
    (OUT / "api_perf_after.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'HTTP':>5} {'ms':>8} {'bytes':>10}  path")
    for r in rows:
        print(f"{str(r['http']):>5} {r['ms']:>8.1f} {r['bytes']:>10}  {r['path']}")
    print(f"\n合计 {result['totals']['sum_ms']} ms · 最大 {result['totals']['max_ms']} ms · "
          f"总字节 {result['totals']['sum_bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

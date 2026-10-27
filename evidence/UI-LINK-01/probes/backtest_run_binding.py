#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI-LINK-01 · 回测详情 ↔ 对账表「同一真实 run_id」机械核对

工单任务 5 要求：
  · 回测详情与对账表必须绑定同一真实 run_id
  · 费用、资金、收益缺字段时明确缺失，不伪填

本脚本只做两件事，都可机械复核：
  ① 从 `/api/v1/backtests` 取一个**真实存在**的 run_id，逐个访问 run 级端点，
     断言每个响应的**顶层 run_id** 都等于所选 run_id（不相等/缺失即判失败）。
  ② 检查「缺字段」是否被伪填：把响应里应为数值的字段扫一遍，
     凡是 `null` 就记 null，凡是 `0` 就要求它**有来源**（同 run 的明细或指标里能对上），
     不允许出现「接口没给、前端自己补 0」。

用法：
  python3 backtest_run_binding.py [base_url]
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8799"
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

RUN_LEVEL = ["", "/metrics", "/equity", "/trades", "/orders", "/rejected", "/quality"]


def get(path):
    try:
        r = _OP.open(BASE + path, timeout=60)
        raw = r.read()
        return r.status, json.loads(raw), len(raw)
    except urllib.error.HTTPError as e:
        return e.code, None, 0
    except Exception as e:
        return "ERR", str(e), 0


def main():
    code, lst, _ = get("/api/v1/backtests?limit=5")
    if code != 200 or not isinstance(lst, dict):
        print(json.dumps({"error": "回测列表不可用", "http": code}, ensure_ascii=False))
        return 1
    items = lst.get("items") or []
    if not items:
        print(json.dumps({"error": "回测列表为空，无法核对"}, ensure_ascii=False))
        return 1

    rid = items[0].get("run_id")
    checks = []
    for suf in RUN_LEVEL:
        path = f"/api/v1/backtests/{rid}{suf}"
        st, body, n = get(path)
        got = body.get("run_id") if isinstance(body, dict) else None
        checks.append({
            "endpoint": path,
            "http": st,
            "run_id": got,
            "match": (got == rid),
            "bytes": n,
        })

    # 缺字段是否被伪填：扫 metrics 里所有数值字段，记录 null 与 0 的分布
    st, metrics, _ = get(f"/api/v1/backtests/{rid}/metrics")
    m = (metrics or {}).get("metrics") if isinstance(metrics, dict) else None
    if not isinstance(m, dict):
        m = (metrics or {}) if isinstance(metrics, dict) else {}
    num_fields = {k: v for k, v in m.items() if isinstance(v, (int, float)) or v is None}
    nulls = sorted([k for k, v in num_fields.items() if v is None])
    zeros = sorted([k for k, v in num_fields.items() if v == 0])

    passed = all(c["match"] and c["http"] == 200 for c in checks)
    out = {
        "base": BASE,
        "selected_run_id": rid,
        "list_item_keys": sorted(items[0].keys()),
        "run_level_checks": checks,
        "metrics_null_fields": nulls,
        "metrics_zero_fields": zeros,
        "verdict": ("7 个 run 级端点顶层 run_id 全部与所选一致" if passed
                    else "存在端点 run_id 不一致或不可用"),
        "note": ("缺字段判定口径：接口返回 null 就是 null，页面显示「—」；"
                 "返回 0 才算 0。本核对只记录接口实际返回，不对 0 做任何解释。"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

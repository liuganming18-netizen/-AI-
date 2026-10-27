#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-UI-02：量一下「接口返回 200 但字段集为空」的发生率。

背景：端到端复验里撞到过一次 —— ETHUSDT 的接口返回 **HTTP 200 + 空字段集**，
错误信息只在 `missing_reason` 里，客户端只看状态码会以为成功。
`metrics_api.handle_metrics` 的 except 分支就是这么写的（已补 stderr 日志 + `degraded` 标记）。

本脚本不改任何东西，只做重复请求统计：连续 N 次打同一批标的，记录空字段集次数与原因。
用法：
  python probe_api_degraded.py --rounds 30
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.abspath(os.path.join(HERE, ".."))
API = os.environ.get("API_BASE") or "http://127.0.0.1:8787"

CASES = [
    ("binance", "BTCUSDT", "1h"),
    ("binance", "ETHUSDT", "1h"),
    ("binance", "1000CATUSDT", "1h"),
    ("okx", "OKB-USDT-SWAP", "1h"),
]


def get(path):
    req = urllib.request.Request(API + path, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=60) as fh:
        return json.loads(fh.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=30, help="每个案例请求多少次")
    ap.add_argument("--gap", type=float, default=0.4, help="两次请求间隔秒")
    a = ap.parse_args()

    total = 0
    empty = 0
    events = []
    started = datetime.now(timezone.utc)
    for i in range(a.rounds):
        for ex, mid, period in CASES:
            total += 1
            try:
                d = get(f"/api/v1/markets/{ex}/{mid}/metrics?period={period}")
            except Exception as exc:                              # noqa: BLE001
                events.append({"at": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                               "case": f"{ex}:{mid}", "kind": "请求异常",
                               "reason": f"{type(exc).__name__}: {exc}"})
                continue
            if not (d.get("fields") or []):
                empty += 1
                events.append({"at": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                               "case": f"{ex}:{mid}", "kind": "200 但空字段集",
                               "reason": d.get("missing_reason")})
        time.sleep(a.gap)

    L = []
    L.append("DATA-UI-02：接口「200 但空字段集」发生率探测")
    L.append("")
    L.append(f"- 区间：{started.strftime('%Y-%m-%dT%H:%M:%SZ')} 起，"
             f"每案例 {a.rounds} 次，共 **{total}** 次请求")
    L.append(f"- 空字段集：**{empty}** 次（{empty / max(1, total) * 100:.2f}%）")
    L.append(f"- 请求异常：{sum(1 for e in events if e['kind'] == '请求异常')} 次")
    L.append("")
    if events:
        L.append("| 时刻 | 案例 | 类型 | 接口给出的原因 |")
        L.append("|---|---|---|---|")
        for e in events[:50]:
            L.append(f"| {e['at']} | `{e['case']}` | {e['kind']} | {(e['reason'] or '—')[:80]} |")
    else:
        L.append("本次探测未复现空字段集。")
    L.append("")
    out = os.path.join(EVID, "logs", "接口降级探测.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("\n".join(L))
    print("写出：", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

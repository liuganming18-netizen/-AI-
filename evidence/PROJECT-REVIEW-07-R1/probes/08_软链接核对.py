#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 软链接与物理 run 目录核对（只读）

为什么有这一步：`01_产物分层.py` 用 `os.path.isdir()` 枚举，**会跟随软链接**，
于是 `event_trend_v0_1/runs_ft_replay_01/` 下 1,065 个软链接被当成 1,065 个独立 run，
和它们在 `runs/` 下的目标各算一次 ⇒ 总数 5,091 里含 1,065 个重复入口。

本脚本用 `os.path.islink()` 把两类分开，并核对每个软链接的目标是否存在、是否指向 `runs/`。
输出 out/08_软链接核对.json
"""
import json
import os
import re
from collections import Counter

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
LOG = os.path.join(ROOT, "回测日志")
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/08_软链接核对.json")
RUN_ID_RE = re.compile(
    r"^(bt_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}|run_WX_EVENT_TREND_V0_1_.*|run_FT_ENTRY_09_.*"
    r"|run_FT_STOP20_.*|run_FT_EXIT01_.*|run_FT_STOPGRID01_.*|run_FT_STOPGRID02_.*"
    r"|run_FT_LOW4H_.*)$")


def main() -> None:
    links = []
    real = Counter()          # 层 -> 个数
    for dp, dns, fns in os.walk(LOG):
        for d in list(dns):
            p = os.path.join(dp, d)
            if os.path.islink(p):
                links.append(p)
        base = os.path.basename(dp)
        if not RUN_ID_RE.match(base) or os.path.islink(dp):
            continue
        rel = os.path.relpath(dp, LOG)
        batch = "/".join(rel.split("/")[:-1])
        tier = ("支持-默认根" if batch == "event_trend_v0_1/runs"
                else "支持-同级批次" if batch.startswith("event_trend_v0_1/runs")
                else "归档-不在扫描范围")
        real[tier] += 1

    targets = Counter()
    broken = 0
    outside_runs = 0
    link_by_batch = Counter()
    for p in links:
        t = os.path.realpath(p)
        if not os.path.exists(p):
            broken += 1
            continue
        targets[t] += 1
        link_by_batch[os.path.relpath(os.path.dirname(p), LOG)] += 1
        if "/event_trend_v0_1/runs/" not in t:
            outside_runs += 1

    out = {
        "物理run目录": sum(real.values()),
        "按层_物理": dict(real),
        "软链接目录": len(links),
        "软链接断链": broken,
        "软链接唯一目标数": len(targets),
        "软链接指向runs之外": outside_runs,
        "软链接按父目录": dict(link_by_batch),
        "逻辑路径合计": sum(real.values()) + len(links),
        "说明": ("01 报的 5,091 = 物理 4,026 + 软链接 1,065。上一单的 3,989 与物理数更接近，"
                 "但差在它只数「有 chart/inputs 且有 candidates/run.json」的子集。"),
        "样例": [{"link": os.path.relpath(p, LOG), "target": os.path.realpath(p)}
                 for p in links[:5]],
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "样例"}, ensure_ascii=False, indent=1))
    print("→", OUT)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""28_process_identity.py —— 第 3 条「核对实际进程加载版本」的进程身份快照

工单要求「核对实际进程加载版本、配置、历史源和恢复锚点」。
报告 5.1 节列了四个进程（接口 7171 / Worker 7174 / 运行时 7178 / 采集器 42278）及其启动时刻，
如果这份名单只写在报告里、没有产物，就是**手写数字**，无法机械复核。

本脚本把「进程身份」冻结成产物。纪律边界：
  · **进程身份只在当次运行有效**，跨时段只有文件身份可复用 ⇒ 产物里必须写清快照时刻；
  · 本快照晚于报告撰写时刻，作用是证明「这几个 pid 至今未变、启动时刻与报告一致」，
    **不是**「报告写作那一刻的快照」。两者不混。
  · 只读：`psutil.Process(pid)` 取 `create_time()` / `cmdline()`，不碰任何生产库。

用法：
    python code/28_process_identity.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import psutil

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence/SIGNAL-CLOSURE-06-R1/out"

# 报告 5.1 节声明的四个进程（角色 → pid）
EXPECT = [
    (7171, "接口服务", "adapter.py"),
    (7174, "回测 Worker", "backtest_worker.py"),
    (7178, "交易链运行时", "unified_runtime.py"),
    (42278, "实时采集器", "realtime_collector.py"),
]


def utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    now = utc(dt.datetime.now(dt.timezone.utc).timestamp())
    rows, ok = [], True
    for pid, role, script in EXPECT:
        rec = {"pid": pid, "role": role, "expected_script": script}
        try:
            p = psutil.Process(pid)
            argv = p.cmdline()
            rec.update({
                "alive": True,
                "started_utc": utc(p.create_time()),
                "cmdline_tail": " ".join(argv[1:])[:200],
                "script_matches": any(a.endswith(script) for a in argv),
            })
            if not rec["script_matches"]:
                ok = False
        except psutil.Error as e:
            rec.update({"alive": False, "error": type(e).__name__})
            ok = False
        rows.append(rec)

    payload = {
        "snapshot_utc": now,
        "script": "code/28_process_identity.py",
        "note": "进程身份只在当次运行有效；本快照用于证明 5.1 节四个 pid 至今未变、启动时刻一致，"
                "不是报告撰写那一刻的快照。",
        "self_pid": os.getpid(),
        "processes": rows,
        "all_alive_and_matching": ok,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(payload, open(OUT / "28_进程身份快照.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    for r in rows:
        print(f"{r['pid']:>6}  {r['role']:<12} alive={r['alive']}  "
              f"started={r.get('started_utc')}  match={r.get('script_matches')}")
    print(f"\n[snapshot] {now}  全部存活且脚本名匹配 = {ok}")
    print(f"[out] {OUT/'28_进程身份快照.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

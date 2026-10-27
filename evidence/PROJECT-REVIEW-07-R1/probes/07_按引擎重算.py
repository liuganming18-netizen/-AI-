#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 按引擎重算成交与 stop_trace（只读，全量）

为什么有这一步：`03_引擎与成交事件.py` 只统计了**有 `run.json` 且能读到 `engine`** 的目录，
合计 33,079 笔；而 `06_止损路径复核.py` 全量扫到 37,404 笔，差 4,325 笔没有归属。
不归属就没法回答「这 4,325 笔是谁的」。本脚本按目录扫全量，缺 `run.json` 的单列一类。

输出：out/07_按引擎重算.json
"""
import json
import os
import re
from collections import Counter, defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
LOG = os.path.join(ROOT, "回测日志")
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/07_按引擎重算.json")
RUN_ID_RE = re.compile(
    r"^(bt_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}|run_WX_EVENT_TREND_V0_1_.*|run_FT_ENTRY_09_.*"
    r"|run_FT_STOP20_.*|run_FT_EXIT01_.*|run_FT_STOPGRID01_.*|run_FT_STOPGRID02_.*"
    r"|run_FT_LOW4H_.*)$")


def to_ms(v):
    """entry_time 可能是 int 毫秒、毫秒字符串、ISO 字符串；解析不出来返回 None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            return int(s)
        try:
            from datetime import datetime, timezone
            return int(datetime.fromisoformat(s.replace("Z", "+00:00"))
                       .astimezone(timezone.utc).timestamp() * 1000)
        except Exception:                                      # noqa: BLE001
            return None
    return None


def engine_of(dp: str) -> str:
    p = os.path.join(dp, "run.json")
    if not os.path.exists(p):
        return "无 run.json（引擎未知）"
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return "run.json 读取失败"
    e = d.get("engine")
    if isinstance(e, dict):
        e = e.get("exec_core_version") or e.get("name")
    if not e:
        return "项目回测引擎（无 engine 字段）"
    e = str(e)
    if "freqtrade" in e.lower():
        return "Freqtrade 回测引擎"
    if e.startswith("oi_res"):
        return "OI 共振（已退役）"
    return "项目回测引擎"


def main() -> None:
    per = defaultdict(lambda: Counter())
    trace = defaultdict(lambda: Counter())
    dirs = 0
    for dp, dns, fns in os.walk(LOG):
        name = os.path.basename(dp)
        if not RUN_ID_RE.match(name):
            continue
        dirs += 1
        eng = engine_of(dp)
        p = os.path.join(dp, "trades.jsonl")
        if not os.path.exists(p):
            per[eng]["无 trades.jsonl"] += 1
            continue
        n = 0
        try:
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except Exception:                              # noqa: BLE001
                    per[eng]["坏行"] += 1
                    continue
                n += 1
                st = t.get("stop_trace")
                if st is None:
                    trace[eng]["无 stop_trace 字段"] += 1
                elif not isinstance(st, list) or len(st) == 0:
                    trace[eng]["空 stop_trace"] += 1
                else:
                    trace[eng]["有 stop_trace 字段"] += 1
        except Exception:                                      # noqa: BLE001
            per[eng]["读取失败"] += 1
        per[eng]["成交笔数"] += n

    total = sum(c["成交笔数"] for c in per.values())
    out = {
        "run目录数": dirs,
        "成交总数": total,
        "按引擎": {k: dict(v) for k, v in per.items()},
        "按引擎_stop_trace": {k: dict(v) for k, v in trace.items()},
        "说明": "按目录全量扫；缺 run.json 的单列一类，避免上一单把 4,325 笔漏在分母外。",
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("run 目录", dirs, "成交总数", total)
    for k, v in per.items():
        print(f"{k:34s}", dict(v))
    print("--- stop_trace")
    for k, v in trace.items():
        print(f"{k:34s}", dict(v))
    print("→", OUT)


if __name__ == "__main__":
    main()

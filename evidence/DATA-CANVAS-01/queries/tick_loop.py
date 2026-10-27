#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-CANVAS-01 隔离环境「活心跳」（只写 /tmp，随进程退出而停）。

静态夹具的问题：心跳写一次就不动了，2 分钟后「心跳新鲜」自然过期，
正常态会假故障（第一版就踩到：api_fail 态第 2 段误报「心跳 2 分钟前」）。

所以正常/缺数据/接口失败三态在截图期间跑这个循环：每 5 秒把
  · collector_state.heartbeat_at  推到 now-2s
  · kline_stream_state.last_msg_at 推到 now-1s
  · 各周期 last_closed_open_time / coverage_cache.last_close 推到"当前应闭合的那根"
（missing 态的 15m 保持 4 小时前不动，故障是它造成的，不能被心跳推进掩盖）

停止采集态**不跑**这个循环 —— 那一态要的就是心跳不动。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time

TF = {"5m": 300000, "15m": 900000, "30m": 1800000, "1h": 3600000,
      "4h": 14400000, "8h": 28800000, "12h": 43200000, "1d": 86400000}


def tick(base: str, state: str, stub_pid: int):
    now = int(time.time() * 1000)
    con = sqlite3.connect(os.path.join(base, "market", "market.db"))
    con.execute("UPDATE collector_state SET heartbeat_at=?, updated_at=?, pid=?, status='running' "
                "WHERE name='realtime'", (now - 2000, now, stub_pid))
    for tf, dur in TF.items():
        if state == "missing" and tf == "15m":
            continue
        con.execute("UPDATE coverage_cache SET last_close=? WHERE timeframe=?",
                    ((now // dur) * dur - 1, tf))
        con.execute("UPDATE kline_stream_state SET last_msg_at=?, updated_at=?, "
                    "last_closed_open_time=? WHERE timeframe=?",
                    (now - 1000, now, (now // dur) * dur - dur, tf))
    con.commit()
    con.close()

    p = os.path.join(base, "locks", "paper_runtime_3a_status.json")
    if os.path.exists(p):
        st = json.load(open(p, encoding="utf-8"))
        st["signal_engine"]["last_cycle_ms"] = now - 5000
        st["runtime_identity"]["pid"] = stub_pid
        for tf, key in (("15m", "latest_15m_close_time"), ("1h", "latest_1h_close_time"),
                        ("4h", "latest_4h_close_time")):
            if state == "missing" and tf == "15m":
                continue
            d = TF[tf]
            st["market_data"][key] = (now // d) * d - 1
        json.dump(st, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--stub-pid", type=int, required=True)
    ap.add_argument("--interval", type=float, default=5.0)
    a = ap.parse_args()
    print(f"[tick_loop] {a.state} every {a.interval}s", flush=True)
    while True:
        try:
            tick(a.base, a.state, a.stub_pid)
        except Exception as exc:  # noqa: BLE001
            print(f"[tick_loop] err {exc}", flush=True)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()

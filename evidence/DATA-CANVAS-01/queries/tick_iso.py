#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-CANVAS-01 隔离环境「时间推进」（只写 /tmp）。

为什么需要它：隔离库是静态夹具，心跳/闭合水位不会自己走。截图前把时间钉到"刚刚"，
否则 3 分钟后"心跳新鲜"就自然过期，正常态会假故障。各状态的偏移量如下：

  normal   : 全部钉到当前周期的最后闭合 bar（滞后 0）
  stopped  : 心跳/消息/闭合水位统一回拨 3 小时（采集停）
  missing  : 心跳与消息新鲜，但 15m 闭合水位回拨 4 小时（数据缺）
  api_fail : 同 normal（故障由代理注入，不在数据侧）
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time

TF = {"5m": 300000, "15m": 900000, "30m": 1800000, "1h": 3600000,
      "4h": 14400000, "8h": 28800000, "12h": 43200000, "1d": 86400000}
NOW = int(time.time() * 1000)


def exp_close(tf, shift=0):
    d = TF[tf]
    return (NOW // d) * d - 1 + shift


def exp_open(tf, shift=0):
    d = TF[tf]
    return (NOW // d) * d - d + shift


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--stub-pid", type=int, default=0)
    a = ap.parse_args()
    base = a.base
    db = os.path.join(base, "market", "market.db")
    con = sqlite3.connect(db)
    now = NOW
    info = {"state": a.state, "ticked_at_ms": now,
            "ticked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    hb = now - 3000
    msg = now - 2000
    shift_all = 0
    shift_15m = 0
    universe = 526

    if a.state == "stopped":
        hb = now - 3 * 3600 * 1000
        msg = hb
        shift_all = -3 * 3600 * 1000
    elif a.state == "missing":
        shift_15m = -4 * 3600 * 1000
        universe = 226

    con.execute("UPDATE collector_state SET heartbeat_at=?, updated_at=?, pid=?, status=? "
                "WHERE name='realtime'",
                (hb, now, a.stub_pid or os.getpid(),
                 "stopped" if a.state == "stopped" else "running"))

    for tf in TF:
        cl = exp_close(tf, shift_all + (shift_15m if tf == "15m" else 0))
        op = exp_open(tf, shift_all + (shift_15m if tf == "15m" else 0))
        con.execute("UPDATE coverage_cache SET last_close=? WHERE timeframe=?", (cl, tf))
        con.execute("UPDATE kline_stream_state SET last_msg_at=?, updated_at=?, "
                    "last_closed_open_time=?, status=?, detail=? "
                    "WHERE timeframe=?",
                    (msg, now, op,
                     "no_messages" if a.state == "stopped" else "ok",
                     "隔离实例：采集已停止 3 小时（夹具）" if a.state == "stopped"
                     else "隔离实例：WS 消息新鲜（夹具）",
                     tf))
    con.commit()
    con.close()

    # 运行时状态文件（夹具）：下游读取水位 = 热库最后闭合（正常/接口失败）；
    # stopped/missing 时下游读到的就是那个旧水位（模拟"引擎读到的是旧数据"）
    p = os.path.join(base, "locks", "paper_runtime_3a_status.json")
    st = json.load(open(p, encoding="utf-8"))
    st["runtime_identity"]["pid"] = a.stub_pid or os.getpid()
    st["runtime_identity"]["status"] = "running"
    st["runtime_identity"]["overall_health"] = "healthy"
    st["signal_engine"]["universe"] = universe
    st["signal_engine"]["universe_declared"] = 526
    st["signal_engine"]["last_cycle_ms"] = now - 8000
    for tf, key in (("15m", "latest_15m_close_time"), ("1h", "latest_1h_close_time"),
                    ("4h", "latest_4h_close_time")):
        st["market_data"][key] = exp_close(tf, shift_all + (shift_15m if tf == "15m" else 0))
    json.dump(st, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    info["status_file"] = p
    info["watermarks"] = {tf: exp_close(tf, shift_all + (shift_15m if tf == "15m" else 0))
                          for tf in ("15m", "1h", "4h")}
    info["heartbeat_at"] = hb
    info["last_msg_at"] = msg
    info["universe"] = universe
    print(json.dumps(info, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

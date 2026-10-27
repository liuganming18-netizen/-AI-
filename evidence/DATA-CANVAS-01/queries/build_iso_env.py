#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-CANVAS-01 隔离环境构建（只读生产、只写 /tmp）。

为什么要造隔离库：验收要求「分别制造正常 / 停止采集 / 缺数据 / 接口失败四种状态」。
不能去改生产库（那是别人的日常实例），所以：

  · 结构：从生产库**只读 ATTACH** 抄表结构（DDL 原样），保证跑的是同一套 SQL；
  · 数据：只抄画布判定需要的那十几张表（符号目录、覆盖缓存、采集心跳、WS 状态、下载任务…），
    行数不大（≈1.2 万行），足以让真实 adapter 代码走真实查询路径；
  · 时间：抄完后按场景**改写时间戳**制造四种状态，不碰任何生产文件。

纪律：
  · 生产库一律 `mode=ro` 打开；写操作只落 `--root` 指定的 /tmp 目录；
  · 不启动 unified_runtime、不写生产账本、不写生产 locks；
  · 隔离实例的「运行时状态文件」是**本工单构造的夹具**（pid 指向本工单自建的守护桩），
    只用于让画布走「引擎在跑」分支 —— 报告里必须如实写明这一点。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT = "/Users/boos/Desktop/威科夫因子共振趋势"
PROD_DB = os.path.join(PROJECT, "数据", "市场数据", "db", "market_v2.sqlite3")

# 抄哪些表（画布链路用到的全部读路径 + 让 adapter 不 500 的空壳表）
COPY = [
    "symbol_classification", "symbol_pool", "coverage_cache",
    "kline_stream_state", "collector_state", "download_tasks", "download_runs",
    "realtime_quotes", "symbol_supply", "api_scope_state", "api_calls",
    "sources", "signals", "candle_versions", "candle_heads", "candles",
    "oi_history", "funding_history", "realtime_klines",
]
# 行数大的表只抄少量（够让查询返回真实结构，不需要全量）
LIMIT = {"candle_versions": 0, "candle_heads": 0, "candles": 0,
         "oi_history": 0, "funding_history": 0, "signals": 0, "sources": 0,
         "realtime_klines": 0, "api_calls": 0}


def ms(txt):
    return int(datetime.strptime(txt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(m):
    if m is None:
        return None
    return datetime.fromtimestamp(m / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(root: str, tag: str):
    base = os.path.join(root, tag)
    if os.path.isdir(base):
        shutil.rmtree(base)
    for d in ("market", "archive", "live", "paper", "locks", "jobs", "logs", "pid"):
        os.makedirs(os.path.join(base, d), exist_ok=True)
    db = os.path.join(base, "market", "market.db")

    con = sqlite3.connect(db, uri=True)
    con.execute("ATTACH DATABASE ? AS p", (f"file:{PROD_DB}?mode=ro",))
    rows = con.execute("SELECT type,name,sql FROM p.sqlite_master "
                       "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()
    tables = [(n, s) for t, n, s in rows if t == "table"]
    indexes = [(n, s) for t, n, s in rows if t == "index"]
    for n, s in tables:
        try:
            con.execute(s)
        except sqlite3.Error as exc:
            print(f"  [warn] 建表 {n} 失败：{exc}")
    for n, s in indexes:
        try:
            con.execute(s)
        except sqlite3.Error:
            pass
    print(f"  表结构 {len(tables)} 张 / 索引 {len(indexes)} 个")

    for t in COPY:
        try:
            lim = LIMIT.get(t)
            if lim == 0:
                continue
            if lim:
                con.execute(f"INSERT INTO main.{t} SELECT * FROM p.{t} LIMIT {lim}")
            else:
                con.execute(f"INSERT INTO main.{t} SELECT * FROM p.{t}")
            n = con.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
            print(f"  抄入 {t}: {n} 行")
        except sqlite3.Error as exc:
            print(f"  [warn] 抄 {t} 失败：{exc}")
    con.commit()
    con.execute("DETACH DATABASE p")
    con.commit()
    # 与生产一致用 WAL：否则 adapter 的连接池持读锁时，心跳夹具写不进去
    con.execute("PRAGMA journal_mode=WAL")
    con.close()

    # live.db / jobs.db：空库，让 adapter 自己建 schema（不抄生产）
    for name in ("live/live.db", "jobs/jobs.sqlite3"):
        p = os.path.join(base, name)
        if not os.path.exists(p):
            sqlite3.connect(p).close()
    return base


def env_for(base: str, port: int) -> dict:
    env = dict(os.environ)
    env.update({
        "PORT": str(port),
        "MARKET_DB": os.path.join(base, "market", "market.db"),
        "MARKET_ARCHIVE": os.path.join(base, "archive"),
        "LIVE_DB": os.path.join(base, "live", "live.db"),
        "LIVE_DIR": os.path.join(base, "live"),
        "PAPER_LEDGER_DIR": os.path.join(base, "paper"),
        "PAPER_LOCK_DIR": os.path.join(base, "locks"),
        "JOBS_DB": os.path.join(base, "jobs", "jobs.sqlite3"),
        "SYS_PID_DIR": os.path.join(base, "pid"),
        "SYS_LOG_DIR": os.path.join(base, "logs"),
        "ISOLATED_MODE": "1",
        "DATA_HUB_PROJECT": PROJECT,
        "PYTHONPATH": f"{PROJECT}/框架:{PROJECT}/框架/backend:{PROJECT}",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
        "HTTP_PROXY": "", "HTTPS_PROXY": "", "http_proxy": "", "https_proxy": "",
    })
    env.pop("EFFECTIVE_VIEW", None)
    return env


# ────────────────────────── 四态改写 ──────────────────────────
def mutate(base: str, state: str, stub_pid: int, now_ms: int):
    db = os.path.join(base, "market", "market.db")
    con = sqlite3.connect(db)
    note = []

    if state == "normal":
        # 时间戳对齐到“刚刚”：抄来的行时间本来就新，只把心跳/消息钉到 now
        con.execute("UPDATE collector_state SET heartbeat_at=?, status='running', pid=?, detail=? "
                    "WHERE name='realtime'",
                    (now_ms - 3000, stub_pid, "隔离实例：心跳由夹具写入（每 15 秒一次）"))
        con.execute("UPDATE kline_stream_state SET last_msg_at=?, updated_at=?",
                    (now_ms - 2000, now_ms - 2000))
        note.append("心跳/WS 消息时间钉到 now-2s；collector pid 指向本工单守护桩")

    elif state == "stopped":
        # 采集停了 3 小时：心跳过期、消息停更、闭合水位停在 3 小时前
        old = now_ms - 3 * 3600 * 1000
        con.execute("UPDATE collector_state SET heartbeat_at=?, status='stopped', pid=?, detail=? "
                    "WHERE name='realtime'",
                    (old, stub_pid, "隔离实例：采集已停止（夹具）"))
        con.execute("UPDATE kline_stream_state SET last_msg_at=?, updated_at=?, status='no_messages', "
                    "detail='隔离实例：3 小时未收到 WS 消息（夹具）'",
                    (old, old))
        note.append("collector.heartbeat_at / kline_stream_state.last_msg_at 回拨 3 小时，status=no_messages")

    elif state == "missing":
        # 采集在跑，但 15m 数据缺一大块：删掉 300 个标的的 15m 覆盖 + 水位回拨 4 小时
        old = now_ms - 4 * 3600 * 1000
        con.execute("UPDATE collector_state SET heartbeat_at=?, status='running', pid=? "
                    "WHERE name='realtime'", (now_ms - 3000, stub_pid))
        con.execute("UPDATE kline_stream_state SET last_msg_at=?, updated_at=?",
                    (now_ms - 2000, now_ms - 2000))
        con.execute("DELETE FROM coverage_cache WHERE timeframe='15m' AND symbol IN "
                    "(SELECT symbol FROM coverage_cache WHERE timeframe='15m' "
                    " AND exchange='binance' ORDER BY symbol LIMIT 300)")
        # 剩下的 15m 水位回拨 4 小时 → 已闭合水位停更
        con.execute("UPDATE coverage_cache SET last_close=?, missing=missing+16 "
                    "WHERE timeframe='15m'", (old,))
        con.execute("UPDATE kline_stream_state SET last_closed_open_time=? "
                    "WHERE timeframe='15m' AND exchange='binance'", (old - 15 * 60 * 1000,))
        note.append("删 300 个标的的 15m 覆盖；剩余 15m last_close 回拨 4 小时、missing +16；WS 闭合水位同步回拨")

    elif state == "api_fail":
        # 数据本身正常；故障由代理注入（/api/v1/paper/status → 500）
        con.execute("UPDATE collector_state SET heartbeat_at=?, status='running', pid=? "
                    "WHERE name='realtime'", (now_ms - 3000, stub_pid))
        con.execute("UPDATE kline_stream_state SET last_msg_at=?, updated_at=?",
                    (now_ms - 2000, now_ms - 2000))
        note.append("数据侧保持正常；接口失败由取证代理对 /api/v1/paper/status 注入 500")

    con.commit()
    con.close()
    return note


def write_runtime_status(base: str, stub_pid: int, now_ms: int, healthy: bool = True):
    """构造 unified_runtime 状态文件（夹具）。

    说明：真实 worker 无法在隔离库里跑起来（没有 candle_heads 全量），
    这里只为了驱动画布的「引擎在跑」分支，**不代表引擎真的在跑**。
    """
    st = {
        "runtime_identity": {"arm": "3a", "symbol": "all(526)", "pid": stub_pid,
                             "status": "running" if healthy else "stopped",
                             "started_at": now_ms - 3600_000, "cycle_count": 128,
                             "overall_health": "healthy" if healthy else "stopped"},
        "market_data": {"status": "connected", "symbol": "all(526)",
                        "latest_15m_close_time": None, "latest_1h_close_time": None,
                        "latest_4h_close_time": None},
        "signal_engine": {"status": "active", "universe": 526, "universe_declared": 526,
                          "bootstrap": {"universe": 526, "ready": 526, "skipped": 0,
                                        "history_candidates_discarded": 4, "elapsed_sec": 17.0},
                          "state_counter": {"BOX": 363, "IDLE": 147, "SWEEP": 4, "RETEST": 12},
                          "active_box": True, "active_state": "BOX",
                          "signals_generated_total": 0, "last_cycle_ms": now_ms - 8000},
        "order_processing": {"status": "ready", "orders_routed_total": 0, "orders_rejected_total": 0,
                             "orders_waiting_gate": 0,
                             "new_entry_gate": {"allow_new_entries": True,
                                                "reason": "user_ui_resumed",
                                                "gate_file": os.path.join(base, "locks", "paper_new_entry_gate.json"),
                                                "gate_scope": "server_side_new_entry_only",
                                                "exits_fees_funding_health_unaffected": True,
                                                "set_by": "user_ui_control", "since_ms": now_ms - 7200_000},
                             "current_pending_in_db": 0},
        "execution_daemon": {"status": "running", "open_positions": 0, "pending_matched_total": 0,
                             "trailing_updates_total": 0, "exits_triggered_total": 0, "last_error": None},
    }
    # 下游读取水位：从隔离库 coverage_cache 取各周期 max(last_close)，与热库一致
    con = sqlite3.connect(os.path.join(base, "market", "market.db"))
    for tf, key in (("15m", "latest_15m_close_time"), ("1h", "latest_1h_close_time"),
                    ("4h", "latest_4h_close_time")):
        r = con.execute("SELECT MAX(last_close) FROM coverage_cache WHERE timeframe=?", (tf,)).fetchone()
        st["market_data"][key] = r[0]
    con.close()
    p = os.path.join(base, "locks", "paper_runtime_3a_status.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)
    gate = {"allow_new_entries": True, "reason": "user_ui_resumed",
            "set_by": "user_ui_control", "since_ms": now_ms - 7200_000,
            "gate_scope": "server_side_new_entry_only"}
    with open(os.path.join(base, "locks", "paper_new_entry_gate.json"), "w", encoding="utf-8") as fh:
        json.dump(gate, fh, ensure_ascii=False, indent=1)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/tmp/dc01")
    ap.add_argument("--state", default="normal")
    ap.add_argument("--stub-pid", type=int, default=0)
    args = ap.parse_args()
    now = int(time.time() * 1000)
    base = build(args.root, args.state)
    notes = mutate(base, args.state, args.stub_pid or os.getpid(), now)
    st = write_runtime_status(base, args.stub_pid or os.getpid(), now,
                             healthy=(args.state != "stopped"))
    print(json.dumps({"base": base, "state": args.state, "notes": notes,
                      "status_file": st, "built_at": iso(now)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

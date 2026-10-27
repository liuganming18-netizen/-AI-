#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-A · 04 健康采集未受损取证

工单要求「保护健康采集：不重复启动写入者、不重跑全池」并提供「健康采集未受损证据」。
本脚本给出**可复核的反证**：本单没有重启采集器、没有起第二个写入者、没有改代码。

四项判据（全部为客观值，不含自评）：
  P1 进程身份不变   采集器 pid / 启动时刻 早于本单开始时刻 ⇒ 全程未重启
  P2 写入者唯一     命令行以 realtime_collector.py 结尾的进程数必须 == 1
  P3 代码未改       关键文件 sha256 + mtime，且 mtime 早于本单开始时刻
  P4 采集仍在写     热库行数与最新 open_time 在观察窗口内持续推进（两次采样对比）

本单开始时刻由 `--ticket-start` 传入（默认取本文件所在目录 README 的首行时间戳，
取不到则用脚本首次运行时刻并显式标注）。
输出：out/health_intact.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.normpath(os.path.join(HERE, "..", "out"))
BACKEND = os.path.join(PROJ, "框架", "backend")
DB = os.path.join(PROJ, "数据", "市场数据", "db", "market_v2.sqlite3")

KEY_FILES = [
    "框架/backend/realtime_collector.py",
    "框架/backend/public_market_read.py",
    "框架/backend/public_snapshot.py",
    "框架/backend/metrics_feed.py",
    "工具_热转冷导出.py",
]


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def p1_p2_processes(ticket_start_epoch):
    import psutil                                            # noqa: PLC0415
    collectors = []
    for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            cl = p.info["cmdline"] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if any(a.endswith("realtime_collector.py") for a in cl):
            collectors.append({
                "pid": p.info["pid"],
                "created_utc": utc(p.info["create_time"]),
                "created_epoch": p.info["create_time"],
                "started_before_ticket": p.info["create_time"] < ticket_start_epoch,
                "cmdline": " ".join(cl)[:200],
            })
    return {
        "collector_count": len(collectors),
        "collectors": collectors,
        "unique_writer_ok": len(collectors) == 1,
        "no_restart_during_ticket": all(
            c["started_before_ticket"] for c in collectors) if collectors else False,
    }


def p3_code():
    res = {"files": [], "all_mtime_before_ticket": True}
    for rel in KEY_FILES:
        p = os.path.join(PROJ, rel)
        if not os.path.isfile(p):
            res["files"].append({"file": rel, "error": "不存在"})
            continue
        st = os.stat(p)
        res["files"].append({
            "file": rel,
            "sha256": sha256(p),
            "size": st.st_size,
            "mtime_utc": utc(st.st_mtime),
            "mtime_epoch": st.st_mtime,
        })
    return res


def p4_growth(gap_s=12.0):
    """热库两次采样：行数与最新 open_time 是否推进。

    间隔必须跨过一个真实写入周期：1h K 线按小时闭合、OI 轮次约 20 分钟一轮，
    所以短间隔（十几秒）必然全 0，**不能拿全 0 当「没在写」**。默认 420s。
    """
    def snap():
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            out = {}
            for tf in ("1h", "5m"):
                row = con.execute(
                    "SELECT COUNT(*), MAX(open_time) FROM candle_heads "
                    "WHERE exchange='binance' AND market='um' AND timeframe=?", (tf,)
                ).fetchone()
                out[f"candle_heads_{tf}"] = {
                    "rows": row[0],
                    "max_open_utc": utc(row[1] / 1000) if row[1] else None,
                }
            row = con.execute(
                "SELECT COUNT(*), MAX(close_time) FROM candles "
                "WHERE exchange='binance' AND market='um' AND timeframe='1h'").fetchone()
            out["candles_1h"] = {
                "rows": row[0],
                "max_close_utc": utc(row[1] / 1000) if row[1] else None,
            }
            row = con.execute(
                "SELECT COUNT(*), MAX(ts) FROM oi_history WHERE exchange='binance'").fetchone()
            out["oi_history"] = {
                "rows": row[0],
                "max_ts_utc": utc(row[1] / 1000) if row[1] else None,
            }
            row = con.execute(
                "SELECT COUNT(*), MAX(updated_at) FROM realtime_quotes").fetchone()
            out["realtime_quotes"] = {
                "rows": row[0],
                "max_updated_utc": utc(row[1] / 1000) if row[1] else None,
            }
            return out
        finally:
            con.close()

    a = snap()
    time.sleep(gap_s)
    b = snap()
    growth = {}
    for k in a:
        ra, rb = a[k]["rows"], b[k]["rows"]
        growth[k] = {"rows_before": ra, "rows_after": rb, "delta": (rb - ra) if None not in (ra, rb) else None}
        for kk in a[k]:
            if kk != "rows":
                growth[k][kk] = {"before": a[k][kk], "after": b[k][kk]}
    return {"sample_a": a, "sample_b": b, "growth": growth}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticket-start", default=None,
                    help="本单开始时刻（ISO8601 UTC），用于判定进程/文件是否早于本单")
    ap.add_argument("--gap", type=float, default=420.0,
                    help="热库两次采样间隔秒数（须跨过一个真实写入周期）")
    args = ap.parse_args()

    if args.ticket_start:
        ticket_start_epoch = datetime.strptime(
            args.ticket_start, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc).timestamp()
    else:
        # 兜底：用本目录最早产物的创建时刻（本单第一次落盘的时刻）
        cands = []
        for root in (OUT, HERE):
            for n in os.listdir(root) if os.path.isdir(root) else []:
                p = os.path.join(root, n)
                cands.append(os.path.getctime(p))
        ticket_start_epoch = min(cands) if cands else time.time()

    procs = p1_p2_processes(ticket_start_epoch)
    code = p3_code()
    for f in code["files"]:
        if "mtime_epoch" in f and f["mtime_epoch"] >= ticket_start_epoch:
            code["all_mtime_before_ticket"] = False

    res = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticket_start_utc": utc(ticket_start_epoch),
        "ticket_start_source": ("命令行传入" if args.ticket_start
                                else "本单最早产物创建时刻（兜底）"),
        "P1_P2_processes": procs,
        "P3_code_unchanged": code,
        "P4_db_growth": p4_growth(args.gap),
        "P4_gap_s": args.gap,
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "health_intact.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    print(json.dumps({
        "ticket_start_utc": res["ticket_start_utc"],
        "collector_count": procs["collector_count"],
        "collector_pid": [c["pid"] for c in procs["collectors"]],
        "collector_created": [c["created_utc"] for c in procs["collectors"]],
        "no_restart_during_ticket": procs["no_restart_during_ticket"],
        "code_all_mtime_before_ticket": code["all_mtime_before_ticket"],
        "growth": {k: v["delta"] for k, v in res["P4_db_growth"]["growth"].items()},
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

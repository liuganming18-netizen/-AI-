#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-A · 03 链路与写入者核对

工单要求「核对实际写入者、进程、调度、请求失败、数据库最新行和生产读取路径，
区分采集停更、读取错误、标的停止交易三种原因」。本脚本逐项取证，**不写任何结论性
形容词**，只产出可复核的原始值。

五段：
  ① 进程身份    采集器 / 后端 / 采集工作器的 pid、启动时刻、命令行、环境变量
  ② 调度        本机 launchd 任务清单（是否真有周期任务在跑）、exporter.sh 状态
  ③ 写入者审计  全仓搜「谁写 metrics parquet / 谁写 symbol_pool / 谁写 download_tasks」
  ④ 请求失败    realtime.log 里最近 N 轮的成功/失败/上游请求数，以及失败标的名单
  ⑤ 最新行      快照 _index.json、逐标快照、指标 parquet、symbol_pool 的最新时点
  ⑥ 读取路径    接口 → 函数 → 数据源 逐段实测（打印每段真实返回值的关键字段）

只读：不写库、不写快照、不改配置。
输出：out/chain_audit.json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.normpath(os.path.join(HERE, "..", "out"))
BACKEND = os.path.join(PROJ, "框架", "backend")
LOG = os.path.join(BACKEND, "logs", "realtime.log")
SNAP_DIR = os.path.join(BACKEND, "快照", "public_extras")
DB = os.path.join(PROJ, "数据", "市场数据", "db", "market_v2.sqlite3")
ARCHIVE = os.path.join(PROJ, "数据", "市场数据", "parquet")
API = "http://127.0.0.1:8787"
SCAN_ROOTS = ["框架", "工具", "脚本", "ops", "框架/ops", "工具_热转冷导出.py"]
SKIP_DIRS = {".git", "node_modules", "__pycache__", "_legacy_archive_v31",
             "evidence", "数据", "dist", "build"}


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(path, timeout=25.0):
    req = urllib.request.Request(API + path)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ── ① 进程身份 ──────────────────────────────────────────────────
def procs():
    import psutil                                            # noqa: PLC0415
    res = {"collector": [], "backend": [], "workers": [], "observation": []}
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cl = p.info["cmdline"] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        joined = " ".join(cl)
        rec = {
            "pid": p.info["pid"],
            "created_utc": datetime.fromtimestamp(
                p.info["create_time"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "uptime_hours": round((time.time() - p.info["create_time"]) / 3600, 2),
            "cmdline": joined[:220],
        }
        if any(a.endswith("realtime_collector.py") for a in cl):
            try:
                env = psutil.Process(p.info["pid"]).environ()
                rec["env_relevant"] = {
                    k: env.get(k) for k in
                    ("MARKET_DB", "MARKET_ARCHIVE", "LIVE_DB", "PAPER_LEDGER_DIR",
                     "PUBLIC_EXTRAS_INTERVAL_S", "PUBLIC_EXTRAS_HEAD_N",
                     "PUBLIC_EXTRAS_RATIO_EXTRA_N", "SMART_MONEY_HEAD_N")
                    if env.get(k) is not None
                }
            except Exception as exc:                          # noqa: BLE001
                rec["env_error"] = f"{type(exc).__name__}: {exc}"
            res["collector"].append(rec)
        elif any(a.endswith("adapter.py") for a in cl):
            res["backend"].append(rec)
        elif any(a.endswith(("worker.py", "unified_runtime.py")) for a in cl):
            res["workers"].append(rec)
        elif any("01_观察" in a for a in cl):
            res["observation"].append(rec)
    # 8787 实际监听者（ps 可能被拒，用 lsof 兜底）
    try:
        pid = subprocess.run(["lsof", "-nP", "-i:8787", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        res["listener_8787_pids"] = [x for x in pid.splitlines() if x]
    except Exception as exc:                                   # noqa: BLE001
        res["listener_8787_pids"] = f"ERR {type(exc).__name__}: {exc}"
    return res


# ── ② 调度 ─────────────────────────────────────────────────────
def scheduling():
    res = {"launch_agents": [], "ops_schedule": [], "plist_in_project": []}
    la = os.path.expanduser("~/Library/LaunchAgents")
    if os.path.isdir(la):
        res["launch_agents"] = sorted(os.listdir(la))
    sched = os.path.join(BACKEND, "..", "ops", "schedule")
    sched = os.path.normpath(sched)
    if os.path.isdir(sched):
        res["ops_schedule"] = sorted(os.listdir(sched))
        res["ops_schedule_dir"] = sched
    for dp, dn, fn in os.walk(PROJ):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".plist"):
                res["plist_in_project"].append(os.path.relpath(os.path.join(dp, f), PROJ))
    res["loaded_launchd_for_project"] = [
        x for x in res["launch_agents"]
        if any(k in x.lower() for k in ("crypto", "weikefu", "wx", "collector", "parquet"))
    ]
    return res


# ── ③ 写入者审计 ───────────────────────────────────────────────
def writer_audit():
    """搜「谁在写」。判据是可执行写入语句/调用，不是文档注释。"""
    targets = []
    for r in SCAN_ROOTS:
        p = os.path.join(PROJ, r)
        if os.path.isfile(p):
            targets.append(p)
            continue
        for dp, dn, fn in os.walk(p):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for f in fn:
                if f.endswith((".py", ".sh", ".plist", ".mjs")):
                    targets.append(os.path.join(dp, f))

    def scan(patterns, label, exclude_tests=True):
        hits = []
        for t in targets:
            rel = os.path.relpath(t, PROJ)
            if exclude_tests and ("/tests/" in rel or rel.startswith("tests/")):
                continue
            try:
                with open(t, encoding="utf-8", errors="ignore") as f:
                    src = f.read()
            except OSError:
                continue
            for pat in patterns:
                for m in re.finditer(pat, src):
                    line_no = src.count("\n", 0, m.start()) + 1
                    line = src.splitlines()[line_no - 1].strip() if line_no else ""
                    hits.append({"file": rel, "line": line_no,
                                 "pattern": pat, "text": line[:160]})
        return {"label": label, "hits": hits, "count": len(hits)}

    res = {
        "scanned_files": len(targets),
        "metrics_parquet_writer": scan(
            [r"metrics[/\\]binance[/\\]um", r"metrics_path\(", r"to_parquet\(",
             r"write_table\("],
            "指标 parquet 写入（metrics 目录 / parquet 写盘调用）"),
        "symbol_pool_writer": scan(
            [r"(?:INSERT|REPLACE|UPDATE)[^\n]{0,60}symbol_pool",
             r"executemany\([^\n]{0,80}symbol_pool"],
            "合约池快照写入（symbol_pool 写语句）"),
        "download_task_writer": scan(
            [r"(?:INSERT|REPLACE|UPDATE)[^\n]{0,60}download_tasks"],
            "归档下载任务写入（download_tasks 写语句）"),
        "exporter_metrics": scan(
            [r"def export_\w+"],
            "热转冷导出器导出的数据集（函数名清单）"),
    }
    # 导出器实际导出哪些数据集：直接列出函数名
    exp = os.path.join(PROJ, "工具_热转冷导出.py")
    if os.path.isfile(exp):
        with open(exp, encoding="utf-8") as f:
            src = f.read()
        res["exporter_functions"] = re.findall(r"^def (\w+)\(", src, re.M)
        res["exporter_mentions_metrics"] = src.count("metrics")
    # _export_state.json 的键
    st = os.path.join(ARCHIVE, "_export_state.json")
    if os.path.isfile(st):
        try:
            with open(st, encoding="utf-8") as f:
                res["export_state_keys"] = sorted(json.load(f).keys())
        except (OSError, ValueError) as exc:
            res["export_state_keys"] = f"ERR {type(exc).__name__}: {exc}"
    return res


# ── ④ 请求失败 ─────────────────────────────────────────────────
def request_stats(last_n_lines=2500):
    with open(LOG, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()[-last_n_lines:]
    rounds = []
    pat = re.compile(
        r"\[(?P<ts>[^\]]+)\] 公开扩展快照刷新：目标 (?P<targets>\d+)，成功 (?P<ok>\d+)，"
        r"跳过 (?P<skip>\d+)，失败 (?P<fail>\d+)；轮转窗口 (?P<rdone>\d+)/(?P<rtotal>\d+)")
    for ln in lines:
        m = pat.search(ln)
        if m:
            req = re.search(r"上游请求 (\d+)（缓存 (\d+)，本地节流 (\d+)）", ln)
            rounds.append({
                "log_ts": m.group("ts"),
                "targets": int(m.group("targets")),
                "ok": int(m.group("ok")),
                "skipped": int(m.group("skip")),
                "failed": int(m.group("fail")),
                "rotation_done": int(m.group("rdone")),
                "rotation_total": int(m.group("rtotal")),
                "upstream": int(req.group(1)) if req else None,
                "cached": int(req.group(2)) if req else None,
                "deferred": int(req.group(3)) if req else None,
                "elapsed_s": (lambda x: float(x.group(1)) if x else None)(
                    re.search(r"用时 ([\d.]+)s", ln)),
            })
    # 失败标的名单
    fail_names = []
    for ln in lines:
        if "公开扩展快照失败样例" in ln or "OI 币安失败" in ln:
            fail_names.append(ln.strip()[:220])
    # OI 完成行
    oi = []
    for ln in lines:
        m = re.search(r"OI 币安逐币完成：成功 (\d+)，失败 (\d+)", ln)
        if m:
            oi.append({"log_ts": re.search(r"\[([^\]]+)\]", ln).group(1),
                       "ok": int(m.group(1)), "failed": int(m.group(2))})
    return {
        "log_path": LOG,
        "lines_scanned": len(lines),
        "snapshot_rounds": rounds[-8:],
        "snapshot_round_count": len(rounds),
        "snapshot_failed_total": sum(r["failed"] for r in rounds),
        "snapshot_ok_total": sum(r["ok"] for r in rounds),
        "oi_rounds": oi[-4:],
        "failure_lines": fail_names[-6:],
    }


# ── ⑤ 最新行 ───────────────────────────────────────────────────
def latest_rows():
    res = {}
    # 快照索引
    idx = os.path.join(SNAP_DIR, "_index.json")
    if os.path.isfile(idx):
        with open(idx, encoding="utf-8") as f:
            d = json.load(f)
        res["snapshot_index"] = {
            "file": "_index.json",
            "mtime_utc": datetime.fromtimestamp(
                os.path.getmtime(idx), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "round_no": d.get("ratio_extra_round"),
            "ok": d.get("ok"), "failed": d.get("failed"),
            "head_n": d.get("head_n"), "interval_s": d.get("interval_s"),
            "elapsed_s": d.get("elapsed_s"),
            "ratio_extra_n": d.get("ratio_extra_n"),
            "ratio_extra_done": d.get("ratio_extra_done"),
            "ratio_extra_targets": d.get("ratio_extra_targets"),
        }
    # BTC 逐比率
    btc = os.path.join(SNAP_DIR, "binance__BTCUSDT.json")
    if os.path.isfile(btc):
        with open(btc, encoding="utf-8") as f:
            d = json.load(f)
        ls = d.get("long_short") or {}
        res["snapshot_btcusdt"] = {
            "mtime_utc": datetime.fromtimestamp(
                os.path.getmtime(btc), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "snapshot_refreshed_at": d.get("snapshot_refreshed_at"),
            "as_of": d.get("as_of"),
            "long_short": {
                k: {"value": (v or {}).get("value"),
                    "event_time": (v or {}).get("event_time"),
                    "fetched_at": (v or {}).get("fetched_at"),
                    "carried_over": (v or {}).get("carried_over"),
                    "carried_from": (v or {}).get("carried_from")}
                for k, v in ls.items() if isinstance(v, dict)
            },
            "open_interest": d.get("open_interest"),
        }
    # 指标 parquet 最新行（走既有只读读取器）
    sys.path.insert(0, BACKEND)
    try:
        import metrics_feed as mf                             # noqa: PLC0415
        now_ms = int(time.time() * 1000)
        per = {}
        for sym in ("BTCUSDT", "ETHUSDT"):
            tail = mf.read_metrics_tail(sym, now_ms)
            per[sym] = {
                "rows": len(tail),
                "last_ts_utc": (datetime.fromtimestamp(
                    max(int(r["ts"]) for r in tail) / 1000, timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ") if tail else None),
            }
        res["metrics_parquet"] = per
    except Exception as exc:                                   # noqa: BLE001
        res["metrics_parquet"] = {"error": f"{type(exc).__name__}: {exc}"}
    # symbol_pool 最新时点
    import sqlite3                                            # noqa: PLC0415
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT count(*), MIN(snapshot_utc), MAX(snapshot_utc), "
            "MAX(updated_at) FROM symbol_pool WHERE exchange='binance' AND market='um'"
        ).fetchone()
        res["symbol_pool"] = {
            "count": row[0],
            "min_snapshot_utc": datetime.fromtimestamp(
                row[1] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if row[1] else None,
            "max_snapshot_utc": datetime.fromtimestamp(
                row[2] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if row[2] else None,
            "max_updated_at": datetime.fromtimestamp(
                row[3] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if row[3] else None,
        }
        dl = con.execute(
            "SELECT id, kind, status, started_at, finished_at FROM download_runs "
            "ORDER BY id DESC LIMIT 3").fetchall()
        res["download_runs_tail"] = [
            {"id": r[0], "kind": r[1], "status": r[2],
             "started_utc": datetime.fromtimestamp(
                 r[3] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if r[3] else None,
             "finished_utc": datetime.fromtimestamp(
                 r[4] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if r[4] else None}
            for r in dl
        ]
        tf = con.execute(
            "SELECT DISTINCT timeframe FROM download_tasks ORDER BY 1").fetchall()
        res["download_task_timeframes"] = [r[0] for r in tf]
    finally:
        con.close()
    return res


# ── ⑥ 读取路径逐段实测 ─────────────────────────────────────────
def read_path():
    res = {}
    d = http_json("/api/v1/markets/binance/BTCUSDT/metrics?period=1h")
    res["api_metrics"] = {
        "url": "/api/v1/markets/binance/BTCUSDT/metrics?period=1h",
        "counts": d.get("counts"),
        "quality_status": (d.get("quality") or {}).get("status"),
        "snapshot_state": d.get("snapshot_state"),
        "time": d.get("time"),
        "sources": d.get("sources"),
        "evidence_history_bar": d.get("evidence", {}).get("history_bar"),
        "evidence_metrics_history": d.get("evidence", {}).get("metrics_history"),
    }
    # 逐字段来源
    res["api_metrics_fields"] = [
        {"key": f.get("key"), "value": f.get("value"), "event_time": f.get("event_time"),
         "source": f.get("source"), "layer": f.get("layer"),
         "missing_reason": f.get("missing_reason")}
        for f in (d.get("fields") or [])
    ]
    # 直接调读取器（绕过接口），确认接口与文件同源
    try:
        import public_snapshot as ps                          # noqa: PLC0415
        snap = ps.read("binance", "BTCUSDT")
        res["direct_snapshot_read"] = {
            "available": bool(snap),
            "freshness": ps.freshness(snap) if snap else None,
            "snapshot_refreshed_at": (snap or {}).get("snapshot_refreshed_at"),
        }
    except Exception as exc:                                   # noqa: BLE001
        res["direct_snapshot_read"] = {"error": f"{type(exc).__name__}: {exc}"}
    return res


def main():
    os.makedirs(OUT, exist_ok=True)
    out = {
        "generated_at": iso_now(),
        "project_root": PROJ,
        "processes": procs(),
        "scheduling": scheduling(),
        "writer_audit": writer_audit(),
        "request_stats": request_stats(),
        "latest_rows": latest_rows(),
        "read_path": read_path(),
    }
    with open(os.path.join(OUT, "chain_audit.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    wa = out["writer_audit"]
    print(json.dumps({
        "collector_pids": [c["pid"] for c in out["processes"]["collector"]],
        "collector_uptime_h": [c["uptime_hours"] for c in out["processes"]["collector"]],
        "listener_8787": out["processes"]["listener_8787_pids"],
        "metrics_writer_hits": wa["metrics_parquet_writer"]["count"],
        "symbol_pool_writer_hits": wa["symbol_pool_writer"]["count"],
        "download_task_writer_hits": wa["download_task_writer"]["count"],
        "exporter_functions": wa.get("exporter_functions"),
        "exporter_mentions_metrics": wa.get("exporter_mentions_metrics"),
        "export_state_keys": wa.get("export_state_keys"),
        "snapshot_rounds_seen": out["request_stats"]["snapshot_round_count"],
        "snapshot_failed_total": out["request_stats"]["snapshot_failed_total"],
        "snapshot_ok_total": out["request_stats"]["snapshot_ok_total"],
        "metrics_parquet_last": out["latest_rows"]["metrics_parquet"],
        "symbol_pool": out["latest_rows"]["symbol_pool"],
        "download_task_timeframes": out["latest_rows"]["download_task_timeframes"],
        "api_counts": out["read_path"]["api_metrics"]["counts"],
        "api_quality": out["read_path"]["api_metrics"]["quality_status"],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

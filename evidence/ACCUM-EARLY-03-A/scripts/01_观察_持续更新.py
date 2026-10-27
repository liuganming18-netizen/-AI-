#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-A · 01 持续更新观察

目的（对应工单「恢复后观察至少两个预期更新周期」）：
  按固定采样间隔，记录**原始行 / 读取器 / 观察证据**三层的时间推进，证明
  「不是 HTTP 200 就算恢复」，而是真的在长新数据。

只读：不写快照、不写库、不触发任何交易所请求（接口本身只读快照）。

采样四路证据：
  A 采集器写入侧  logs/realtime.log 里「公开扩展快照刷新」行的原始时间戳与统计
  B 原始落盘文件  快照/public_extras/_index.json 的 round_no / ratio_extra_targets
                  + 每个标的最新 snapshot_refreshed_at（文件 mtime 与内部字段各记一份）
  C 实际读取器     GET /api/v1/markets/binance/<SYM>/metrics 的 time.event_time /
                  snapshot_state.refreshed_at / 各比率 event_time
  D 陈旧状态       public_snapshot.freshness() 的 status / age_seconds

用法：
  python 01_观察_持续更新.py --minutes 22 --interval 30
输出：
  out/observe_timeline.jsonl   每次采样一行
  out/observe_summary.json     汇总结论（轮次推进数、是否满足≥2周期、逐项首末值）
"""
from __future__ import annotations

import argparse
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
OUT = os.path.join(HERE, "..", "out")
OUT = os.path.normpath(OUT)
BACKEND = os.path.join(PROJ, "框架", "backend")
SNAP_DIR = os.path.join(BACKEND, "快照", "public_extras")
LOG = os.path.join(BACKEND, "logs", "realtime.log")
API = "http://127.0.0.1:8787"
PROBE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "DOTUSDT")
RATIO_KEYS = (
    "top_position_long_short_ratio",
    "top_account_long_short_ratio",
    "global_account_long_short_ratio",
    "taker_buy_sell_ratio",
)

sys.path.insert(0, BACKEND)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(path: str, timeout: float = 20.0):
    req = urllib.request.Request(API + path)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tail_snapshot_log(n: int = 400):
    """取 realtime.log 尾部里最近的「公开扩展快照刷新」行（原样字符串）。"""
    try:
        with open(LOG, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        if "公开扩展快照刷新" in ln:
            out.append(ln.rstrip("\n"))
    return out[-3:]


def sample() -> dict:
    ts = iso_now()
    rec: dict = {"sampled_at": ts}

    # ── A 写入侧日志 ─────────────────────────────────────────────
    rec["log_lines"] = tail_snapshot_log()

    # ── B 原始落盘文件 ───────────────────────────────────────────
    idx_path = os.path.join(SNAP_DIR, "_index.json")
    try:
        with open(idx_path, encoding="utf-8") as f:
            idx = json.load(f)
        rec["index"] = {
            "round_no": idx.get("ratio_extra_round"),
            "ok": idx.get("ok"),
            "failed": idx.get("failed"),
            "targets": idx.get("head_n"),
            "ratio_extra_done": idx.get("ratio_extra_done"),
            "ratio_extra_targets": idx.get("ratio_extra_targets"),
            "elapsed_s": idx.get("elapsed_s"),
            "index_mtime": datetime.fromtimestamp(
                os.path.getmtime(idx_path), timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    except (OSError, ValueError) as exc:
        rec["index"] = {"error": f"{type(exc).__name__}: {exc}"}

    files = {
        "probe": {},
        "fresh_count": None,
        "stale_count": None,
    }
    try:
        names = [
            n for n in os.listdir(SNAP_DIR)
            if n.endswith(".json") and not n.startswith("_")
        ]
        now = time.time()
        fresh = stale = 0
        for n in names:
            p = os.path.join(SNAP_DIR, n)
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            age = now - os.path.getmtime(p)
            if age < 900:
                fresh += 1
            else:
                stale += 1
            sym = d.get("market_id")
            if sym in PROBE_SYMBOLS:
                ls = d.get("long_short") or {}
                files["probe"][sym] = {
                    "snapshot_refreshed_at": d.get("snapshot_refreshed_at"),
                    "file_mtime": datetime.fromtimestamp(
                        os.path.getmtime(p), timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "ratios": {
                        k: {
                            "event_time": (ls.get(k) or {}).get("event_time"),
                            "value": (ls.get(k) or {}).get("value"),
                            "carried_over": (ls.get(k) or {}).get("carried_over"),
                            "fetched_at": (ls.get(k) or {}).get("fetched_at"),
                        }
                        for k in RATIO_KEYS
                    },
                }
        files["fresh_count"] = fresh
        files["stale_count"] = stale
    except OSError as exc:
        files["error"] = f"{type(exc).__name__}: {exc}"
    rec["files"] = files

    # ── C 实际读取器（走接口，不直读文件）────────────────────────
    api = {}
    for sym in PROBE_SYMBOLS:
        try:
            d = http_json(f"/api/v1/markets/binance/{sym}/metrics?period=1h")
            api[sym] = {
                "counts": d.get("counts"),
                "quality_status": (d.get("quality") or {}).get("status"),
                "time_event_time": (d.get("time") or {}).get("event_time"),
                "time_fetched_at": (d.get("time") or {}).get("fetched_at"),
                "snapshot_refreshed_at": (d.get("snapshot_state") or {}).get("refreshed_at"),
                "snapshot_available": (d.get("snapshot_state") or {}).get("available"),
                "snapshot_status": (d.get("snapshot_state") or {}).get("status"),
                "snapshot_note": (d.get("snapshot_state") or {}).get("note"),
                "ratios": {
                    f.get("key"): {
                        "value": f.get("value"),
                        "event_time": f.get("event_time"),
                    }
                    for f in (d.get("fields") or [])
                    if f.get("key") in RATIO_KEYS
                },
            }
        except Exception as exc:                                    # noqa: BLE001
            api[sym] = {"error": f"{type(exc).__name__}: {exc}"}
    rec["api"] = api

    # ── D 陈旧状态（读取器同一实现的判定）────────────────────────
    try:
        import public_snapshot as ps                          # noqa: PLC0415
        fresh_state = {}
        for sym in PROBE_SYMBOLS:
            snap = ps.read("binance", sym)
            fresh_state[sym] = ps.freshness(snap) if snap else {"status": "absent"}
        rec["freshness"] = fresh_state
    except Exception as exc:                                        # noqa: BLE001
        rec["freshness"] = {"error": f"{type(exc).__name__}: {exc}"}

    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=22.0)
    ap.add_argument("--interval", type=float, default=30.0)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    tl_path = os.path.join(OUT, "observe_timeline.jsonl")
    t0 = time.time()
    deadline = t0 + args.minutes * 60.0
    n = 0
    with open(tl_path, "w", encoding="utf-8") as tl:
        while time.time() < deadline:
            rec = sample()
            tl.write(json.dumps(rec, ensure_ascii=False) + "\n")
            tl.flush()
            n += 1
            rnd = (rec.get("index") or {}).get("round_no")
            print(f"[{rec['sampled_at']}] #{n} round={rnd} "
                  f"fresh={(rec.get('files') or {}).get('fresh_count')}", flush=True)
            time.sleep(args.interval)

    # ── 汇总 ────────────────────────────────────────────────────
    recs = []
    with open(tl_path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                recs.append(json.loads(ln))

    rounds = [
        (r.get("index") or {}).get("round_no")
        for r in recs
        if (r.get("index") or {}).get("round_no") is not None
    ]
    round_seq = []
    for x in rounds:
        if not round_seq or round_seq[-1] != x:
            round_seq.append(x)

    def first_last(path):
        """按路径取值，返回首个/末个非空值。"""
        vals = []
        for r in recs:
            cur = r
            for k in path:
                cur = (cur or {}).get(k) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if cur is not None:
                vals.append(cur)
        if not vals:
            return {"first": None, "last": None, "changed": False, "samples": 0}
        return {"first": vals[0], "last": vals[-1],
                "changed": vals[0] != vals[-1], "samples": len(vals)}

    probes = {}
    for sym in PROBE_SYMBOLS:
        probes[sym] = {
            "snapshot_refreshed_at": first_last(["files", "probe", sym, "snapshot_refreshed_at"]),
            "api_time_event_time": first_last(["api", sym, "time_event_time"]),
            "api_time_fetched_at": first_last(["api", sym, "time_fetched_at"]),
            "api_snapshot_refreshed_at": first_last(["api", sym, "snapshot_refreshed_at"]),
            "api_counts_available": first_last(["api", sym, "counts", "available"]),
            "freshness_status": first_last(["freshness", sym, "status"]),
            "freshness_age_seconds": first_last(["freshness", sym, "age_seconds"]),
        }
        for k in RATIO_KEYS:
            probes[sym][f"ratio_event_time::{k}"] = first_last(
                ["files", "probe", sym, "ratios", k, "event_time"]
            )
            probes[sym][f"api_ratio_event_time::{k}"] = first_last(
                ["api", sym, "ratios", k, "event_time"]
            )

    distinct_rounds = len(round_seq)
    summary = {
        "generated_at": iso_now(),
        "window": {
            "started_at": recs[0]["sampled_at"] if recs else None,
            "ended_at": recs[-1]["sampled_at"] if recs else None,
            "samples": len(recs),
            "interval_s": args.interval,
            "minutes_requested": args.minutes,
        },
        "round_advance": {
            "round_sequence": round_seq,
            "distinct_rounds": distinct_rounds,
            "advanced_by": (round_seq[-1] - round_seq[0]) if len(round_seq) >= 2 else 0,
            "meets_two_cycles": distinct_rounds >= 3,
            "note": ("采到 ≥3 个不同轮次 ⇒ 至少跨越 2 个完整更新周期；"
                     "round_no 单调递增即证明写入者持续工作"),
        },
        "fresh_stale": {
            "fresh_first": first_last(["files", "fresh_count"])["first"],
            "fresh_last": first_last(["files", "fresh_count"])["last"],
            "stale_first": first_last(["files", "stale_count"])["first"],
            "stale_last": first_last(["files", "stale_count"])["last"],
        },
        "probes": probes,
        "log_line_samples": [
            {"sampled_at": r["sampled_at"], "lines": r.get("log_lines")}
            for r in recs[::max(1, len(recs) // 4)]
        ],
    }
    with open(os.path.join(OUT, "observe_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print(json.dumps({
        "distinct_rounds": distinct_rounds,
        "round_sequence": round_seq,
        "advanced_by": summary["round_advance"]["advanced_by"],
        "meets_two_cycles": summary["round_advance"]["meets_two_cycles"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

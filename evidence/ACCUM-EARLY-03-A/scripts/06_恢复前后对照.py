#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-A · 06 恢复前后对照（冷档导出链路）

本单唯一采取的动作：**重启停摆的热转冷导出器**（复用 `框架/backend/exporter.sh`，
断点 `_export_state.json` 原样保留，未改一行代码）。

本脚本把「恢复前 / 恢复后」的原始时间摆在一起，并**用三个独立视角交叉验证**：
  V1 接口视角   `GET /api/v1/markets/binance/BTCUSDT/metrics` 的
                `evidence.history_bar.open_time`（读的是冷档 parquet）
  V2 文件视角   冷档 `candles/binance/um/BTCUSDT/1h/live.parquet` 的修改时刻与大小
  V3 断点视角   `_export_state.json` 的 `last_run` 与 candles 水位
另附：指标归档（metrics）**恢复前后都不变**，用于证明「本单没有、也无法通过重启修好它」。

「恢复前」的值不手抄：V1 取自本单重启之前生成的 `out/chain_audit.json`；
V2/V3 取自磁盘上仍在的日志行与 2026.parquet 修改时刻。

输出：out/recovery_before_after.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
PROJ = os.path.dirname(os.path.dirname(BASE))
OUT = os.path.join(BASE, "out")
ARC = os.path.join(PROJ, "数据", "市场数据", "parquet")
STATE = os.path.join(ARC, "_export_state.json")
LOG = os.path.join(PROJ, "回测日志", "热转冷导出.log")
API = "http://127.0.0.1:8787"


def utc(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(path, timeout=25.0):
    req = urllib.request.Request(API + path)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    # ── V1 接口视角 ─────────────────────────────────────────────
    with open(os.path.join(OUT, "chain_audit.json"), encoding="utf-8") as f:
        pre = json.load(f)
    pre_hb = pre["read_path"]["api_metrics"]["evidence_history_bar"] or {}
    v1_before = pre_hb.get("open_time")
    v1_before_available = pre_hb.get("available")
    v1_before_reason = pre_hb.get("reason")
    v1_before_gen = pre["generated_at"]

    d = http_json("/api/v1/markets/binance/BTCUSDT/metrics?period=1h")
    hb = (d.get("evidence") or {}).get("history_bar") or {}
    v1_after = hb.get("open_time")
    v1_after_close = hb.get("close_time")
    v1_now = d.get("as_of")

    # 直接调读取器复测一次（绕过接口缓存），确认不是缓存假象
    sys.path.insert(0, os.path.join(PROJ, "框架", "backend"))
    direct = {}
    try:
        import metrics_api as ma                              # noqa: PLC0415
        r1 = ma.last_closed_bar("binance", "BTCUSDT", "1h")
        r2 = ma.last_closed_bar("binance", "BTCUSDT", "1h")
        direct = {"available": r1.get("available"), "open_time": r1.get("open_time"),
                  "second_call_same": r1.get("open_time") == r2.get("open_time")}
    except Exception as exc:                                   # noqa: BLE001
        direct = {"error": f"{type(exc).__name__}: {exc}"}

    # ── V2 文件视角 ─────────────────────────────────────────────
    live = os.path.join(ARC, "candles", "binance", "um", "BTCUSDT", "1h", "live.parquet")
    year = os.path.join(ARC, "candles", "binance", "um", "BTCUSDT", "1h", "2026.parquet")
    v2 = {}
    for label, p in (("live", live), ("year", year)):
        if os.path.isfile(p):
            st = os.stat(p)
            v2[label] = {"path": os.path.relpath(p, PROJ),
                         "mtime_utc": utc(st.st_mtime), "size": st.st_size}
        else:
            v2[label] = {"path": os.path.relpath(p, PROJ), "error": "不存在"}

    # ── V3 断点视角 ─────────────────────────────────────────────
    with open(STATE, encoding="utf-8") as f:
        st = json.load(f)
    lr = st.get("last_run") or {}
    v3_after = {
        "slow_candles_utc": utc(lr["slow_candles"]) if lr.get("slow_candles") else None,
        "oi_utc": utc(lr["oi"]) if lr.get("oi") else None,
        "finished_at_utc": utc(lr["finished_at"]) if lr.get("finished_at") else None,
        "duration_s": lr.get("duration_s"),
    }
    btc_key = "binance/um/BTCUSDT/1h"
    v3_after["candles_watermark"] = (st.get("candles") or {}).get(btc_key)

    # 恢复前的断点值：从磁盘日志里取「停摆前最后一轮」的收尾行（不手抄）。
    # 判据是**日期**而不是「最后一行」——重启后日志会继续追加，取最后一行会取错。
    pre_lines = []
    stopped_mid_round = None
    with open(LOG, encoding="utf-8", errors="ignore") as f:
        all_lines = [ln.rstrip("\n") for ln in f]
    for ln in all_lines:
        if ln.startswith("[2026-10-18") and "本轮结束" in ln:
            pre_lines.append(ln)
    # 停摆证据：10-18 当天最后一行的内容（若是半轮，则没有「本轮结束」）
    day_lines = [ln for ln in all_lines if ln.startswith("[2026-10-18")]
    if day_lines:
        stopped_mid_round = day_lines[-1]
    last_pre_round = pre_lines[-1] if pre_lines else None

    # ── 指标归档：恢复前后都不变 ────────────────────────────────
    metrics_after = (d.get("evidence") or {}).get("metrics_history") or {}
    metrics_before = (pre["read_path"]["api_metrics"]["evidence_metrics_history"] or {})

    # 恢复后首轮补数：从日志解析（不手抄数字）
    post_round = {}
    for ln in all_lines:
        if not ln.startswith("[2026-10-26"):
            continue
        for label, key in (("fast", "K线快车道"), ("slow", "K线慢车道"), ("oi", "持仓量")):
            if key in ln:
                post_round[label] = ln
    res_rounds = {}
    for label, ln in post_round.items():
        m = re.search(r"\{.*\}", ln)
        res_rounds[label] = {
            "log_line": ln,
            "stats": json.loads(m.group(0)) if m else None,
            "log_ts": ln[1:ln.find("]")] if ln.startswith("[") else None,
        }

    res = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": "重启停摆的热转冷导出器（exporter.sh start，断点原样保留）",
        "code_changed": False,
        "V1_api_history_bar": {
            "before": v1_before,
            "before_available": v1_before_available,
            "before_reason": v1_before_reason,
            "before_source": f"out/chain_audit.json @ {v1_before_gen}",
            "after": v1_after, "after_close_time": v1_after_close,
            "after_source": f"GET {API}/api/v1/markets/binance/BTCUSDT/metrics",
            "observed_at": v1_now,
            "advanced": bool(v1_after) and (v1_before is None or v1_after > v1_before),
            "direct_reader_retest": direct,
            "note": ("恢复前那一次探测落在归档读取不稳定的窗口里：落盘的 chain_audit.json "
                     "记录的是 available=false（归档内无已收盘 K 线），本报告只采用这条"
                     "可复现的记录，不引用未落盘的中间探测值"),
        },
        "V2_cold_parquet": v2,
        "V4_first_catchup_round": res_rounds,
        "V3_checkpoint": {
            "before_last_completed_round": last_pre_round,
            "before_last_line_of_that_day": stopped_mid_round,
            "after": v3_after,
            "note": ("before_last_line_of_that_day 若不是「本轮结束」，说明进程死在半轮，"
                     "属于被杀而不是正常退出"),
        },
        "metrics_archive_unaffected": {
            "before_last": metrics_before.get("last"),
            "before_rows": metrics_before.get("rows"),
            "after_last": metrics_after.get("last"),
            "after_rows": metrics_after.get("rows"),
            "unchanged": metrics_before.get("last") == metrics_after.get("last"),
            "note": ("指标归档没有任何写入者，重启导出器也不会变；"
                     "这条用来证明本单没有把它「顺手改成有值」"),
        },
    }
    with open(os.path.join(OUT, "recovery_before_after.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    print(json.dumps({
        "V1_before": v1_before, "V1_before_available": v1_before_available,
        "V1_before_reason": v1_before_reason,
        "V1_after": v1_after,
        "V1_advanced": res["V1_api_history_bar"]["advanced"],
        "V1_direct_retest": direct,
        "V2_live_mtime": v2.get("live", {}).get("mtime_utc"),
        "V3_after": v3_after,
        "V3_before_last_completed": last_pre_round,
        "V3_before_last_line_of_day": stopped_mid_round,
        "metrics_unchanged": res["metrics_archive_unaffected"]["unchanged"],
        "metrics_before": metrics_before.get("last"),
        "metrics_after": metrics_after.get("last"),
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

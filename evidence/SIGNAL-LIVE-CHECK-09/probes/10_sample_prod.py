#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-LIVE-CHECK-09 探针①：生产跨小时线采样（只读）

为什么要连续采样
----------------
要求 2 要的是「跨至少两根新增小时线，逐币记录」。单点快照只能看到「此刻」，
看不到「哪一根小时线推进了、谁在这一根上变了阶段、候选是不是在这一根冒出来的」。
所以按固定间隔连续抓，每份样本都带自己的采集时刻与当时的 1h 水位，
事后再按「水位从 X 推进到 Y」切段分析。

抓什么（全部来自既有接口，不新增端点、不改生产代码）
--------------------------------------------------
  · `/api/v1/paper/status` → `signal_engine` 整段（逐币阶段 / 水位一致性 /
    空转分因 / 漏斗 / 数据质量 / ATR 口径）+ `runtime_identity` + `market_data`
  · `/api/v1/signals?scope=current|all|history` → 接口层返回了什么
  · 生产 `live_signal` 表行数（只读）→ 入库层落了几行
  · 四本账本行数（只读）→ 有没有下单

停的条件：**水位推进满 2 根 1h** 或到 `--max-minutes` 上限，先到者停。
不伪造、不补数、不因为「没等到候选」就提前结束（那是把等待当结论）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
OUT = HERE.parents[1] / "out"
API = "http://127.0.0.1:8787"
PROD_LIVE_DB = ROOT / "框架/backend/live/live_signals.sqlite3"
LEDGERS = {a: ROOT / f"数据/paper/paper_3{a}.sqlite3" for a in ("a", "b", "c", "d")}

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def get(path):
    with _opener.open(API + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def ro(db: Path):
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def live_signal_snapshot():
    """生产实时库：行数 + 每行关键字段（只读）。"""
    try:
        con = ro(PROD_LIVE_DB)
        try:
            rows = [dict(zip(
                ("signal_uid", "symbol", "direction", "origin", "executable_state",
                 "confirmed_at"), r))
                for r in con.execute(
                    "SELECT signal_uid, symbol, direction, origin, executable_state, "
                    "confirmed_at FROM live_signal ORDER BY confirmed_at")]
            return {"n": len(rows), "rows": rows}
        finally:
            con.close()
    except Exception as exc:                                  # noqa: BLE001
        return {"n": -1, "error": str(exc), "rows": []}


def ledger_snapshot():
    out = {}
    for arm, p in LEDGERS.items():
        try:
            con = ro(p)
            try:
                d = {}
                for t in ("paper_order", "paper_position", "paper_pending", "paper_fill"):
                    try:
                        d[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    except sqlite3.OperationalError:
                        d[t] = None
                out[arm] = d
            finally:
                con.close()
        except Exception as exc:                              # noqa: BLE001
            out[arm] = {"error": str(exc)}
    return out


def sample_once(seq: int) -> dict:
    t_ms = int(time.time() * 1000)
    st = get("/api/v1/paper/status")
    item = st.get("item") or {}
    se = item.get("signal_engine") or {}
    md = item.get("market_data") or {}
    ri = item.get("runtime_identity") or {}

    cur = get("/api/v1/signals?scope=current&limit=500")
    allq = get("/api/v1/signals?scope=all&limit=500")
    hist = get("/api/v1/signals?scope=history&limit=500")

    return {
        "seq": seq,
        "sampled_at_ms": t_ms,
        "sampled_at_utc": iso(t_ms),
        "runtime": {
            "pid": ri.get("pid"), "status": ri.get("status"),
            "cycle_count": ri.get("cycle_count"),
            "started_at_utc": iso(ri.get("started_at")),
            "overall_health": ri.get("overall_health"),
        },
        "market_data": {
            "latest_1h_close_utc": iso(md.get("latest_1h_close_time")),
            "latest_4h_close_utc": iso(md.get("latest_4h_close_time")),
            "latest_15m_close_utc": iso(md.get("latest_15m_close_time")),
        },
        "watermark": {
            "watermark_1h": se.get("watermark_1h"),
            "watermark_1h_utc": iso(se.get("watermark_1h")),
            "watermark_4h": se.get("watermark_4h"),
            "watermark_4h_utc": iso(se.get("watermark_4h")),
            "check": se.get("watermark_check"),
        },
        "state_counter": se.get("state_counter"),
        "per_symbol_state": se.get("per_symbol_state"),
        "idle_breakdown": se.get("idle_breakdown"),
        "funnel_since_start": (se.get("funnel") or {}).get("since_start"),
        "funnel_last_candidate_utc": (se.get("funnel") or {}).get("last_candidate_utc"),
        "signals_generated_total": se.get("signals_generated_total"),
        "data_quality": se.get("data_quality"),
        "skipped_reasons": se.get("skipped_reasons"),
        "scan_errors_total": se.get("scan_errors_total"),
        "api": {
            "current_total": cur.get("total"),
            "current_ids": [i.get("signal_id") for i in (cur.get("items") or [])],
            "current_quality_status": (cur.get("quality") or {}).get("status"),
            "current_missing_reason": cur.get("missing_reason"),
            "current_note_len": len((cur.get("quality") or {}).get("note") or ""),
            "all_total": allq.get("total"),
            "history_total": hist.get("total"),
        },
        "live_db": live_signal_snapshot(),
        "ledgers": ledger_snapshot(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60, help="采样间隔秒")
    ap.add_argument("--max-minutes", type=int, default=95, help="最长采样分钟")
    ap.add_argument("--need-bars", type=int, default=2, help="水位推进多少根 1h 后停")
    ap.add_argument("--out", default=str(OUT / "10_prod_samples.ndjson"))
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    p = Path(args.out)
    f = open(p, "w", encoding="utf-8")

    first_wm = None
    n_written = 0
    deadline = time.time() + args.max_minutes * 60
    seq = 0
    try:
        while time.time() < deadline:
            seq += 1
            try:
                s = sample_once(seq)
            except Exception as exc:                          # noqa: BLE001
                print(f"[warn] 第 {seq} 次采样失败：{type(exc).__name__}: {exc}",
                      flush=True)
                time.sleep(args.interval)
                continue

            f.write(json.dumps(s, ensure_ascii=False) + "\n")
            f.flush()
            n_written += 1

            wm = s["watermark"]["watermark_1h"]
            if first_wm is None:
                first_wm = wm
                print(f"[start] 起始水位 1h={iso(wm)} "
                      f"latest_1h_close={s['market_data']['latest_1h_close_utc']}",
                      flush=True)

            advanced = (wm - first_wm) / 3_600_000 if (wm and first_wm) else 0
            print(f"[{s['sampled_at_utc']}] #{seq} 水位={iso(wm)} "
                  f"推进={advanced:.0f}根 "
                  f"stage={json.dumps(s['state_counter'], ensure_ascii=False)} "
                  f"候选(本进程)={s['funnel_since_start'].get('CANDIDATE') if s['funnel_since_start'] else None} "
                  f"库={s['live_db']['n']} API当前={s['api']['current_total']}",
                  flush=True)

            if advanced >= args.need_bars:
                print(f"[stop] 水位已推进 {advanced:.0f} 根（≥{args.need_bars}）", flush=True)
                break
            time.sleep(args.interval)
    finally:
        f.close()

    print(f"\n采样完成：{n_written} 份 → {p}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

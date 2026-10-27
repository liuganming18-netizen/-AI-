#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-UI-LINK-07 探针①：隔离环境下用**生产同一份**运行时重放真实行情，捕获候选全集。

回答的问题
----------
1. 预热回放到底产生了哪些候选？（候选 ID / 标的 / 生成时刻 / 可执行最早时刻）
2. 它们为什么一条都没进 `live_signal`？（按代码路径逐条给出原因）
3. 生产状态文件里的 `history_candidates_discarded=51` 能不能对上明细？

纪律
----
· 行情库**只读**打开；`LIVE_DB` / `PAPER_LEDGER_DIR` 全部指向本单 evidence 下的隔离目录；
· 生产 `live/live_signals.sqlite3`、`数据/paper/*` **一个字节都不写**（结尾核对 mtime/size）；
· 不改任何策略参数、不放宽任何门禁；
· 候选来自**真实引擎**（`MultiSymbolSignalEngine.bootstrap` → `RealSignalEngine.process_market`），
  本探针只在进程内包一层 `process_market` 记录返回值，不注入、不造候选。

用法
----
  python 20_iso_replay.py --out .../out/02_iso_replay.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
BACKEND = ROOT / "框架/backend"
ISO = HERE.parents[1] / "iso"

# ── 隔离必须在 import 业务模块**之前**生效 ────────────────────────────────────
ISO.mkdir(parents=True, exist_ok=True)
(ISO / "paper").mkdir(parents=True, exist_ok=True)
os.environ["LIVE_DB"] = str(ISO / "live_signals.sqlite3")
os.environ["PAPER_LEDGER_DIR"] = str(ISO / "paper")
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
os.environ["WX_HISTORY_SOURCE"] = "merged"
# 状态文件也必须隔离，否则会覆盖生产 locks/paper_runtime_3a_status.json
os.environ["PAPER_LOCK_DIR"] = str(ISO / "locks")

for p in (str(ROOT), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402

import live_store as L  # noqa: E402


def _iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _sha(p: Path):
    import hashlib
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _fingerprint():
    """生产关键文件的「未被我改动」指纹（大小 + mtime + sha）。"""
    out = {}
    for p in (ROOT / "框架/backend/live/live_signals.sqlite3",
              ROOT / "数据/paper/paper_3a.sqlite3",
              ROOT / "框架/backend/locks/paper_runtime_3a_status.json"):
        try:
            st = p.stat()
            out[str(p.relative_to(ROOT))] = {
                "size": st.st_size,
                "mtime_utc": dt.datetime.fromtimestamp(
                    st.st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sha256": _sha(p),
            }
        except FileNotFoundError:
            out[str(p.relative_to(ROOT))] = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE.parents[1] / "out" / "02_iso_replay.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--upto", default="", help="回放截止 ISO（默认=行情库最新闭合 1h）")
    args = ap.parse_args()

    fp_before = _fingerprint()

    import unified_runtime as UR
    from 策略.WyckoffMaster.event_trend_v0_1.contracts import CandidateSnapshot  # noqa: F401

    # ── 捕获层：只在进程内记录 process_market 的返回值 ────────────────────────
    captured = []
    orig_pm = UR.RealSignalEngine.process_market

    def _pm(self, upto_ms, watcher):
        got = orig_pm(self, upto_ms, watcher)
        for c in (got or []):
            captured.append({
                "candidate_id": c.candidate_id,
                "symbol": c.symbol,
                "snapshot_time_ms": int(c.snapshot_time_ms),
                "snapshot_utc": _iso(c.snapshot_time_ms),
                "available_at_ms": int(c.available_at_ms),
                "available_at_utc": _iso(c.available_at_ms),
                "execution_earliest_ms": int(c.execution_earliest_ms),
                "execution_earliest_utc": _iso(c.execution_earliest_ms),
                "box_high_h": c.box_high_h, "box_low_l": c.box_low_l,
                "box_atr_a": c.box_atr_a, "box_er": c.box_er,
                "breakout_close": c.breakout_close,
                "volume_ratio": c.volume_ratio,
                "oi_growth_6h": c.oi_growth_6h,
                "initial_stop_price": c.initial_stop_price,
                "max_entry_price": c.max_entry_price,
                "min_entry_price": c.min_entry_price,
                "pit_proven": c.pit_proven,
                "snapshot_hash": c.snapshot_hash,
                "captured_at_upto_ms": int(upto_ms),
            })
        return got

    UR.RealSignalEngine.process_market = _pm

    rt = UR.UnifiedRuntime(arm="3a")
    if args.limit:
        rt.symbols = rt.symbols[:args.limit]
        rt.signal_engine.symbols = rt.symbols
    se = rt.signal_engine

    # 截止时刻：默认取行情库最新闭合 1h 的 close_time
    if args.upto:
        upto = int(dt.datetime.strptime(args.upto, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    else:
        con = L.market_conn()
        try:
            upto = int(con.execute(
                "SELECT MAX(close_time) FROM candles WHERE exchange='binance' "
                "AND market='um' AND timeframe='1h' AND is_closed=1").fetchone()[0])
        finally:
            con.close()

    # 重放锚点：与生产同源（只读生产 live_run 的 state_anchor_ms）
    prod_anchor = None
    try:
        import sqlite3
        pc = sqlite3.connect(
            f"file:{ROOT / '框架/backend/live/live_signals.sqlite3'}?mode=ro", uri=True)
        row = pc.execute("SELECT value_json FROM live_run WHERE key='state_anchor_ms'").fetchone()
        pc.close()
        if row:
            prod_anchor = int(json.loads(row[0]) if isinstance(row[0], str) else row[0])
    except Exception as exc:                                  # noqa: BLE001
        print(f"[warn] 读生产锚点失败：{exc}", flush=True)

    se.state_anchor_ms = prod_anchor
    se.history_floor_ms = se.resolve_replay_floor(upto)

    import history_loader as HL
    import project_paths as PP
    se.history_provider = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=print)
    t0 = time.time()
    hist_stats = se.prepare_history(upto, log=print)
    t_hist = time.time() - t0
    t1 = time.time()
    bs = se.bootstrap(upto, log=print)
    t_boot = time.time() - t1
    try:
        se._hist = None
        se.history_provider.release()
    except Exception:                                         # noqa: BLE001
        pass

    # ── 逐条判因：为什么没进 live_signal ─────────────────────────────────────
    epoch = L.PAPER_EPOCH_MS          # 生产常量（隔离环境不改）
    max_lag = int(__import__("paper_engine").MAX_FILL_LAG_MS)
    for c in captured:
        reasons = []
        if c["available_at_ms"] < epoch:
            reasons.append("before_paper_epoch_no_backfill")
        if c["execution_earliest_ms"] + max_lag < upto:
            reasons.append("missed_fill_moment_no_backfill")
        c["would_be_discarded"] = bool(reasons)
        c["discard_reasons"] = reasons
        c["age_at_upto_hours"] = round((upto - c["available_at_ms"]) / 3_600_000, 2)

    captured.sort(key=lambda c: c["snapshot_time_ms"])
    n_in_lag = sum(1 for c in captured if not c["would_be_discarded"])
    n_after_epoch = sum(1 for c in captured if c["available_at_ms"] >= epoch)

    out = {
        "probe": "SIGNAL-UI-LINK-07/20_iso_replay",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isolation": {
            "LIVE_DB": os.environ["LIVE_DB"],
            "PAPER_LEDGER_DIR": os.environ["PAPER_LEDGER_DIR"],
            "PAPER_LOCK_DIR": os.environ.get("PAPER_LOCK_DIR"),
            "MARKET_DB": str(L.MARKET_DB),
            "market_db_writable": False,
            "note": "行情库只读；live/paper/状态文件全部落 evidence 隔离目录",
        },
        "clock": {
            "upto_ms": upto, "upto_utc": _iso(upto),
            "state_anchor_ms": prod_anchor, "state_anchor_utc": _iso(prod_anchor),
            "replay_floor_ms": se.history_floor_ms,
            "replay_floor_utc": _iso(se.history_floor_ms),
            "replay_mode": "absolute_anchor" if prod_anchor else "relative_window",
            "paper_epoch_ms": epoch, "paper_epoch_utc": _iso(epoch),
            "max_fill_lag_ms": max_lag,
        },
        "config": {
            "strategy": "WX_EVENT_TREND_V0_1 / FT-LOW4H",
            "box_bars_4h": se.config.box_bars_4h,
            "atr_period_4h": se.config.atr_period_4h,
            "breakout_vol_multiplier": se.config.breakout_vol_multiplier,
            "enable_oi_filter": bool(se.enable_oi_filter),
        },
        "timing": {"prepare_history_sec": round(t_hist, 1), "bootstrap_sec": round(t_boot, 1)},
        "history_stats": hist_stats,
        "bootstrap_stats": {k: v for k, v in (bs or {}).items() if k != "history"},
        "funnel_history": se.funnel().get("history"),
        "candidates_total": len(captured),
        "candidates_in_fill_lag": n_in_lag,
        "candidates_after_paper_epoch": n_after_epoch,
        "candidates": captured,
        "production_fingerprint_before": fp_before,
        "production_fingerprint_after": _fingerprint(),
        "iso_live_db_rows": None,
    }
    # 隔离库里到底落了什么
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{os.environ['LIVE_DB']}?mode=ro", uri=True)
        try:
            out["iso_live_db_rows"] = con.execute(
                "SELECT COUNT(*) FROM live_signal").fetchone()[0]
        except Exception:                                     # noqa: BLE001
            out["iso_live_db_rows"] = "table_missing"
        con.close()
    except Exception:                                         # noqa: BLE001
        pass

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n===== 候选全集 =====")
    for c in captured:
        print(f"  {c['symbol']:14s} {c['snapshot_utc']}  discard={c['discard_reasons']}")
    print(f"\n候选 {len(captured)} / 仍在成交时效内 {n_in_lag} / 晚于 paper epoch {n_after_epoch}")
    print(f"bootstrap: discarded={bs.get('history_candidates_discarded')} "
          f"recovered={bs.get('recovered_candidates')} "
          f"funnel.CANDIDATE={se.funnel()['history'].get('CANDIDATE')}")
    print(f"隔离 live_signal 行数：{out['iso_live_db_rows']}")
    print(f"输出：{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-UI-LINK-07 探针②：隔离环境端到端 —— 真实候选 → 落库 → API 口径。

做法
----
用**生产同一份** `UnifiedRuntime` / `MultiSymbolSignalEngine` / `RealSignalEngine` /
`SignalPersistenceAndRouter`，在**隔离的** LIVE_DB + PAPER_LEDGER_DIR + 锁目录里：

  ① 把隔离时钟与「模拟启用时点」一起设到真实候选发生之前（自洽，不动生产）；
  ② bootstrap 预热到候选前 1 毫秒（此时那根 1h 尚未收盘）；
  ③ `step_once(now=候选收盘时刻, origin='live')` 让引擎**自己**处理那根 1h 并产生候选
     —— 走 `persist_candidates` 正式落库，不插行、不伪造；
  ④ 再跑一次同样的 step，验证幂等（不新增行）；
  ⑤ 用**生产同一份** `live_api.live_signals()` 分别按 `scope=current` / `scope=all` 取，
     给出「候选 ID → 落库 → API」三段对账。

隔离纪律
--------
· 行情库只读；`sync_dataset_bars_upto`（会写 candles）在本进程内替换为 no-op；
· 生产 `live/live_signals.sqlite3`、`数据/paper/*` 前后 sha256 必须一致；
· **只覆盖内存里的 `live_store.PAPER_EPOCH_MS`**（模拟启用时点），生产文件不改；
  证据里同时记录生产常量原值与覆盖值，以及生产文件 sha 前后一致。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
BACKEND = ROOT / "框架/backend"
ISO = HERE.parents[1] / "iso_e2e"

CAND_SYMBOL = "VELVETUSDT"
CAND_SNAPSHOT_MS = 1_790_953_200_000        # 2026-10-02T15:00:00Z（探针①实测的真实候选）
STARTED_MS = 1_790_949_600_000              # 2026-10-02T14:00:00Z（模拟进程启动，早于候选）
ISO_PAPER_EPOCH_MS = 1_790_870_400_000      # 2026-10-01T00:00:00Z（模拟启用时点）
ISO_LIVE_START_MS = 1_790_870_400_000

ISO.mkdir(parents=True, exist_ok=True)
(ISO / "paper").mkdir(parents=True, exist_ok=True)
(ISO / "locks").mkdir(parents=True, exist_ok=True)
os.environ["LIVE_DB"] = str(ISO / "live_signals.sqlite3")
os.environ["PAPER_LEDGER_DIR"] = str(ISO / "paper")
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
os.environ["PAPER_LOCK_DIR"] = str(ISO / "locks")
os.environ["WX_HISTORY_SOURCE"] = "merged"

for p in (str(ROOT), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

import live_store as L  # noqa: E402

PROD_EPOCH_CONST = int(L.PAPER_EPOCH_MS)
# ── 隔离「模拟启用时点」：只改本进程内存，不改文件、不影响生产进程 ─────────────
L.PAPER_EPOCH_MS = int(ISO_PAPER_EPOCH_MS)


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


def _fp():
    out = {}
    for p in (ROOT / "框架/backend/live/live_signals.sqlite3",
              ROOT / "数据/paper/paper_3a.sqlite3",
              ROOT / "数据/市场数据/db/market_v2.sqlite3"):
        try:
            st = p.stat()
            out[str(p.relative_to(ROOT))] = {"size": st.st_size, "sha256": _sha(p)}
        except FileNotFoundError:
            out[str(p.relative_to(ROOT))] = None
    return out


class _CappedPool:
    """把 `PoolMarketWatcher` 的「水位」收窄到隔离时钟（真实行情、只读）。"""

    H = {"1h": 3_600_000, "4h": 14_400_000}

    def __init__(self, pool, upto_ms: int):
        self.pool = pool
        self.upto = int(upto_ms)

    def global_last_open(self, timeframe: str):
        con = L.market_conn()
        try:
            r = con.execute(
                "SELECT MAX(open_time) FROM candle_heads WHERE exchange='binance' "
                "AND market='um' AND timeframe=? AND open_time<=?",
                (timeframe, self.upto - self.H[timeframe])).fetchone()
            return int(r[0]) if r and r[0] is not None else None
        finally:
            con.close()

    def latest_open_by_symbol(self, timeframe: str):
        con = L.market_conn()
        try:
            rows = con.execute(
                "SELECT symbol, MAX(open_time) FROM candle_heads WHERE exchange='binance' "
                "AND market='um' AND timeframe=? AND open_time<=? GROUP BY symbol",
                (timeframe, self.upto - self.H[timeframe])).fetchall()
            return {s: int(m) for s, m in rows if m is not None}
        finally:
            con.close()

    def get_closed_bars(self, symbol, timeframe, start_open_ms, end_open_ms):
        return self.pool.get_closed_bars(symbol, timeframe, start_open_ms, end_open_ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE.parents[1] / "out" / "03_iso_e2e.json"))
    ap.add_argument("--keep-db", action="store_true")
    args = ap.parse_args()

    fp_before = _fp()
    # 每次从干净隔离库开始（避免上一轮残留影响幂等判定）
    if not args.keep_db:
        for p in (ISO / "live_signals.sqlite3",):
            if p.exists():
                p.unlink()
        shutil.rmtree(ISO / "paper", ignore_errors=True)
        (ISO / "paper").mkdir(parents=True, exist_ok=True)

    import unified_runtime as UR
    import live_api as LA

    # 建隔离库 schema 并落「模拟启用时点 / 启动水位」
    con = L.live_conn()
    L.set_run("live_start_ms", ISO_LIVE_START_MS)
    L.set_run("paper_epoch_ms", ISO_PAPER_EPOCH_MS)
    con.close()

    rt = UR.UnifiedRuntime(arm="3a")
    se = rt.signal_engine
    rt.started_at_ms = STARTED_MS
    # 本探针绝不写行情库：把会写 candles 的 dataset 同步替换成 no-op
    rt.watcher.sync_dataset_bars_upto = lambda *_a, **_k: None

    # 生产锚点（只读）⇒ 重放序列与生产完全一致
    prod_anchor = None
    try:
        pc = sqlite3.connect(
            f"file:{ROOT / '框架/backend/live/live_signals.sqlite3'}?mode=ro", uri=True)
        row = pc.execute("SELECT value_json FROM live_run WHERE key='state_anchor_ms'").fetchone()
        pc.close()
        if row:
            prod_anchor = int(json.loads(row[0]) if isinstance(row[0], str) else row[0])
    except Exception as exc:                                  # noqa: BLE001
        print(f"[warn] 读生产锚点失败：{exc}", flush=True)

    upto_boot = CAND_SNAPSHOT_MS - 1                          # 候选那根 1h 尚未收盘
    se.state_anchor_ms = prod_anchor
    se.history_floor_ms = se.resolve_replay_floor(upto_boot)

    import history_loader as HL
    import project_paths as PP
    se.history_provider = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=print)
    t0 = time.time()
    se.prepare_history(upto_boot, log=print)
    bs = se.bootstrap(upto_boot, log=print)
    t_boot = time.time() - t0

    pre_state = se.state_by_symbol().get(CAND_SYMBOL)
    pre_last_1h = se.last_1h_open.get(CAND_SYMBOL)

    # ── 隔离时钟推进：让引擎自己处理 2026-10-02T14:00 那根 1h ────────────────
    se.watcher = _CappedPool(UR.PoolMarketWatcher(), CAND_SNAPSHOT_MS)
    step1 = rt.step_once(now_ms=CAND_SNAPSHOT_MS, origin="live")
    rows_after_1 = _read_iso_signals()
    step2 = rt.step_once(now_ms=CAND_SNAPSHOT_MS, origin="live")
    rows_after_2 = _read_iso_signals()

    # ── 生产同一份 API 函数：两种口径 ─────────────────────────────────────────
    cur = LA.live_signals({"scope": ["current"], "limit": ["500"]})
    allq = LA.live_signals({"scope": ["all"], "limit": ["500"]})

    target_ids = [r["signal_uid"] for r in rows_after_1]
    in_cur = [i["signal_id"] for i in (cur.get("items") or [])]
    in_all = [i["signal_id"] for i in (allq.get("items") or [])]

    out = {
        "probe": "SIGNAL-UI-LINK-07/30_iso_e2e",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isolation": {
            "LIVE_DB": os.environ["LIVE_DB"],
            "PAPER_LEDGER_DIR": os.environ["PAPER_LEDGER_DIR"],
            "PAPER_LOCK_DIR": os.environ["PAPER_LOCK_DIR"],
            "MARKET_DB": str(L.MARKET_DB),
            "market_db_opened_ro": True,
            "dataset_sync_disabled": True,
            "note": ("行情库只读；live/paper/锁目录全部落 evidence 隔离目录；"
                     "`sync_dataset_bars_upto`（会写 candles）在本进程内为 no-op"),
        },
        "clock": {
            "mode": "isolated_replay_clock",
            "started_at_ms": STARTED_MS, "started_at_utc": _iso(STARTED_MS),
            "bootstrap_upto_ms": upto_boot, "bootstrap_upto_utc": _iso(upto_boot),
            "step_now_ms": CAND_SNAPSHOT_MS, "step_now_utc": _iso(CAND_SNAPSHOT_MS),
            "iso_paper_epoch_ms": ISO_PAPER_EPOCH_MS,
            "iso_paper_epoch_utc": _iso(ISO_PAPER_EPOCH_MS),
            "iso_live_start_ms": ISO_LIVE_START_MS,
            "iso_live_start_utc": _iso(ISO_LIVE_START_MS),
            "prod_paper_epoch_const_ms": PROD_EPOCH_CONST,
            "prod_paper_epoch_const_utc": _iso(PROD_EPOCH_CONST),
            "prod_const_overridden_in_process_only": True,
            "prod_const_file_unchanged": True,
            "state_anchor_ms": prod_anchor, "state_anchor_utc": _iso(prod_anchor),
            "replay_mode": "absolute_anchor" if prod_anchor else "relative_window",
            "note": ("隔离时钟=2026-10-02；模拟启用时点=2026-10-01（与时钟自洽、早于候选）；"
                     "生产启用时点常量 2026-10-14T01:00Z 与门禁文件均未修改"),
        },
        "pre_step": {
            "symbol": CAND_SYMBOL, "state": pre_state,
            "last_1h_open_utc": _iso(pre_last_1h),
            "bootstrap_discarded": bs.get("history_candidates_discarded"),
            "bootstrap_recovered": bs.get("recovered_candidates"),
            "bootstrap_sec": round(t_boot, 1),
        },
        "step1": step1,
        "step2_idempotent": step2,
        "persisted_rows_after_step1": rows_after_1,
        "persisted_rows_after_step2": rows_after_2,
        "idempotent_no_duplicate": (len(rows_after_1) == len(rows_after_2)
                                    and [r["signal_uid"] for r in rows_after_1]
                                    == [r["signal_uid"] for r in rows_after_2]),
        "api_scope_current": {
            "total": cur.get("total"), "n_items": len(cur.get("items") or []),
            "signal_ids": in_cur,
            "quality": cur.get("quality"),
        },
        "api_scope_all": {
            "total": allq.get("total"), "n_items": len(allq.get("items") or []),
            "signal_ids": in_all,
        },
        "target_signal_ids": target_ids,
        "target_in_scope_current": [s for s in target_ids if s in in_cur],
        "target_in_scope_all": [s for s in target_ids if s in in_all],
        "production_fingerprint_before": fp_before,
        "production_fingerprint_after": _fp(),
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n===== step1 =====")
    print(json.dumps(step1, ensure_ascii=False))
    print("===== step2（幂等）=====")
    print(json.dumps(step2, ensure_ascii=False))
    print(f"落库行数 step1={len(rows_after_1)} step2={len(rows_after_2)} "
          f"幂等={out['idempotent_no_duplicate']}")
    for r in rows_after_1:
        print(f"  {r['signal_uid']} origin={r['origin']} exec={r['executable_state']} "
              f"confirmed={_iso(r['confirmed_at'])}")
    print(f"API scope=current total={cur.get('total')} ids={in_cur}")
    print(f"API scope=all     total={allq.get('total')} n={len(in_all)}")
    print(f"输出：{p}")
    return 0


def _read_iso_signals():
    con = sqlite3.connect(f"file:{os.environ['LIVE_DB']}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT signal_uid, symbol, origin, executable_state, confirmed_at, "
            "available_at, event_time, payload_json FROM live_signal "
            "ORDER BY confirmed_at").fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

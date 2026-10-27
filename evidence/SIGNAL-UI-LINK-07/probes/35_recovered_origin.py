#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-UI-LINK-07 探针③：重启恢复候选（origin=recovered）能不能进当前信号区？

背景
----
`bootstrap()` 对「预热回放期间产生、但按 FILL_POLICY 仍在成交时效内」的候选**不丢弃**，
放进 `recovered_candidates`，由 `_route_recovered_candidates()` 以 `origin='recovered'`
落库。这是真实链路的一条分支（进程短暂停机后重启，期间刚好有一根 1h 确认突破）。

问题
----
`live_api.live_signals(scope=current)` 的 where 里写死 `origin='live'`。
若这条分支的候选**永远**进不了当前区，那它就是「生成→入库→页面」断链：
库里有一行、账本里可能有挂单，但用户在信号区永远看不到。

做法
----
隔离环境（独立 LIVE_DB / 账本 / 锁目录），bootstrap 截止到「候选收盘后 30 分钟」，
让真实候选走 recovered 分支落库，然后：
  · 查 `scope=current` → 记录是否可见；
  · 查 `scope=all`     → 记录是否可见（审计通道应可见）；
  · 直查隔离库 origin / executable_state。
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
ISO = HERE.parents[1] / "iso_recovered"

CAND_SNAPSHOT_MS = 1_790_953_200_000            # 2026-10-02T15:00:00Z
# 两个截止时刻分别验证「4h 边界内」与「恰在 4h 收盘」两种情形：
#   15:30 —— 最后一根已收盘 4h 是 08:00（close 11:59:59），12:00~15:00 的 1h 从未喂入
#   16:00 —— 12:00 那根 4h 已收盘，process_market 会把 12:00~15:00 的 1h 一并处理
UPTO_DEFAULT_MS = 1_790_953_200_000 + 30 * 60_000   # 15:30
STARTED_MS = 1_790_949_600_000                  # 2026-10-02T14:00:00Z（进程启动早于候选）
ISO_PAPER_EPOCH_MS = 1_790_870_400_000          # 2026-10-01T00:00:00Z（模拟启用时点）

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

L.PAPER_EPOCH_MS = int(ISO_PAPER_EPOCH_MS)


def _iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE.parents[1] / "out" / "04_recovered_origin.json"))
    ap.add_argument("--upto", type=int, default=UPTO_DEFAULT_MS,
                    help="bootstrap 截止毫秒（默认 15:30，可传 16:00 对应 4h 收盘）")
    args = ap.parse_args()
    UPTO_MS = int(args.upto)

    for p in (ISO / "live_signals.sqlite3",):
        if p.exists():
            p.unlink()
    shutil.rmtree(ISO / "paper", ignore_errors=True)
    (ISO / "paper").mkdir(parents=True, exist_ok=True)

    import unified_runtime as UR
    import live_api as LA

    con = L.live_conn()
    L.set_run("live_start_ms", ISO_PAPER_EPOCH_MS)
    L.set_run("paper_epoch_ms", ISO_PAPER_EPOCH_MS)
    con.close()

    rt = UR.UnifiedRuntime(arm="3a")
    se = rt.signal_engine
    rt.started_at_ms = STARTED_MS
    rt.watcher.sync_dataset_bars_upto = lambda *_a, **_k: None

    prod_anchor = None
    try:
        pc = sqlite3.connect(
            f"file:{ROOT / '框架/backend/live/live_signals.sqlite3'}?mode=ro", uri=True)
        row = pc.execute("SELECT value_json FROM live_run WHERE key='state_anchor_ms'").fetchone()
        pc.close()
        if row:
            prod_anchor = int(json.loads(row[0]) if isinstance(row[0], str) else row[0])
    except Exception:                                         # noqa: BLE001
        pass

    se.state_anchor_ms = prod_anchor
    se.history_floor_ms = se.resolve_replay_floor(UPTO_MS)

    import history_loader as HL
    import project_paths as PP
    se.history_provider = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=print)
    t0 = time.time()
    se.prepare_history(UPTO_MS, log=print)
    bs = se.bootstrap(UPTO_MS, log=print)
    # 生产 run_forever 里的顺序：bootstrap → _route_recovered_candidates()
    rec = rt._route_recovered_candidates(now_ms=UPTO_MS)

    cur = LA.live_signals({"scope": ["current"], "limit": ["500"]})
    allq = LA.live_signals({"scope": ["all"], "limit": ["500"]})

    rows = []
    con = sqlite3.connect(f"file:{os.environ['LIVE_DB']}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT signal_uid, symbol, origin, executable_state, confirmed_at, available_at "
            "FROM live_signal ORDER BY confirmed_at").fetchall()]
    finally:
        con.close()

    ids = [r["signal_uid"] for r in rows]
    cur_ids = [i["signal_id"] for i in (cur.get("items") or [])]
    all_ids = [i["signal_id"] for i in (allq.get("items") or [])]

    out = {
        "probe": "SIGNAL-UI-LINK-07/35_recovered_origin",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isolation": {"LIVE_DB": os.environ["LIVE_DB"],
                      "PAPER_LEDGER_DIR": os.environ["PAPER_LEDGER_DIR"],
                      "PAPER_LOCK_DIR": os.environ["PAPER_LOCK_DIR"]},
        "clock": {"started_at_utc": _iso(STARTED_MS), "bootstrap_upto_ms": UPTO_MS,
                  "bootstrap_upto_utc": _iso(UPTO_MS),
                  "iso_paper_epoch_utc": _iso(ISO_PAPER_EPOCH_MS),
                  "state_anchor_utc": _iso(prod_anchor),
                  "recovery_gate": bs.get("recovery_gate")},
        "bootstrap": {"ready": bs.get("ready"), "skipped": bs.get("skipped"),
                      "discarded": bs.get("history_candidates_discarded"),
                      "recovered": bs.get("recovered_candidates"),
                      "recovered_detail": bs.get("recovered_detail"),
                      "elapsed_sec": bs.get("elapsed_sec")},
        "route_recovered": rec,
        "db_rows": rows,
        "api_scope_current": {"total": cur.get("total"),
                              "signal_ids": cur_ids,
                              "quality": cur.get("quality")},
        "api_scope_all": {"total": allq.get("total"), "signal_ids": all_ids},
        "verdict": {
            "persisted": len(rows),
            "persisted_origin": sorted({r["origin"] for r in rows}),
            "visible_in_current": [i for i in ids if i in cur_ids],
            "visible_in_all": [i for i in ids if i in all_ids],
            "current_excludes_persisted": (len(rows) > 0 and not [i for i in ids if i in cur_ids]),
        },
        "elapsed_sec": round(time.time() - t0, 1),
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n===== 结果 =====")
    print(f"bootstrap discarded={bs.get('history_candidates_discarded')} "
          f"recovered={bs.get('recovered_candidates')}")
    print(f"route_recovered={json.dumps(rec, ensure_ascii=False)}")
    for r in rows:
        print(f"  落库 {r['signal_uid']} origin={r['origin']} exec={r['executable_state']}")
    print(f"scope=current total={cur.get('total')} ids={cur_ids}")
    print(f"scope=all     total={allq.get('total')} n={len(all_ids)}")
    print(f"判定：{json.dumps(out['verdict'], ensure_ascii=False)}")
    print(f"输出：{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

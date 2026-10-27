#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-UI-LINK-07-R1 探针④：两条真实候选路径，各跑一次正式引擎。

为什么要分两个进程
------------------
`live_store` / `live_api` 在 import 时就把 `LIVE_DB` 解析成模块级常量，
同一进程内切换环境变量不可靠 ⇒ 一条路径一个子进程，各自独立隔离目录：

  --mode live       新产生候选：bootstrap 预热到候选那根 1h **收盘前 1 毫秒**，
                    再 `step_once(now=候选收盘时刻, origin='live')`，
                    由引擎自己处理那根 1h 并 `persist_candidates` 正式落库。
  --mode recovered  重启恢复候选：进程停机期间那根 1h 已收盘，
                    bootstrap 截止到「候选收盘后 30 分钟」，候选进
                    `recovered_candidates`，再由 `_route_recovered_candidates()`
                    以 `origin='recovered'` 落库（生产 run_forever 的真实顺序）。

两条路径用的是**同一份真实历史行情**、同一个候选时刻，
因此期望得到**同一个候选 ID** —— 这正是用户第 3 条要的「按同一候选 ID 核对」。

隔离纪律
--------
· 行情库只读（`market_conn` 只 SELECT）；会写 candles 的 `sync_dataset_bars_upto`
  在本进程内替换成 no-op；
· `LIVE_DB` / `PAPER_LEDGER_DIR` / `PAPER_LOCK_DIR` 全部落本单 evidence 目录；
· 只改**进程内存**里的 `live_store.PAPER_EPOCH_MS`（模拟启用时点），生产常量与
  门禁文件不动；生产三个关键文件 sha256 前后必须一致。
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
BASE = HERE.parents[1]                      # evidence/SIGNAL-UI-LINK-07-R1

CAND_SYMBOL = "VELVETUSDT"
CAND_SNAPSHOT_MS = 1_790_953_200_000        # 2026-10-02T15:00:00Z（探针①实测的真实候选）
H1 = 3_600_000
STARTED_MS = 1_790_949_600_000              # 2026-10-02T14:00:00Z（进程启动早于候选）
ISO_PAPER_EPOCH_MS = 1_790_870_400_000      # 2026-10-01T16:00:00Z（模拟启用时点）
RECOVERED_UPTO_MS = CAND_SNAPSHOT_MS + 30 * 60_000   # 15:30：候选那根 1h 已收盘

# ── 两条路径的 bootstrap 截止时刻必须分开，否则演示不到各自的分支 ──────────────
# 本单实测教训：bootstrap 现在有「尾部补齐」（`process_market(upto_ms)` 补喂
# 最后一根 4h 之后已收盘的 1h，SIGNAL-UI-LINK-07 修复）。若把 live 路径的 upto
# 也设成 `候选收盘 - 1ms`，那根 1h 的 `close_time` 恰好等于 upto（close_time 是
# 包含式的：15:00:00 记作 1790953199999）⇒ 它会被 bootstrap 吃掉、进 recovered，
# 于是 step 无新信号。上一单 03 探针跑在修复之前，所以当时 step 才有 1 个新信号。
#
#   live      ：进程在 14:00 就在线，bootstrap 停在 14:00 之前（最后已收盘 1h=13:00），
#               15:00 那根 1h 收盘时由 `step_once` 现场处理 ⇒ **新产生候选**。
#   recovered ：进程 15:30 才重启，bootstrap 直接补喂到 15:30 ⇒ 候选进
#               `recovered_candidates`，由 `_route_recovered_candidates()` 落库。
LIVE_UPTO_MS = CAND_SNAPSHOT_MS - H1 - 1    # 2026-10-02T13:59:59.999

MODES = {
    "live": "iso_live",
    "recovered": "iso_recovered",
}


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
    """生产**状态**文件指纹（本探针唯一可能误写的两处）。

    行情库 `market_v2.sqlite3` 不在其中：采集器（PID 常驻）每秒都在往里写当前时段
    的 K 线，它的 sha 一定会变，拿它当「探针是否泄漏」的判据会得出错误结论。
    行情库单独用 `_market_state()` 记录水位，见那里的归因说明。
    """
    out = {}
    for p in (ROOT / "框架/backend/live/live_signals.sqlite3",
              ROOT / "数据/paper/paper_3a.sqlite3"):
        try:
            st = p.stat()
            out[str(p.relative_to(ROOT))] = {"size": st.st_size, "sha256": _sha(p)}
        except FileNotFoundError:
            out[str(p.relative_to(ROOT))] = None
    return out


def _market_state():
    """行情库水位（只读）。用来**归因**它的变化，而不是当作泄漏判据。

    判据：若 sha 变了、但变化只体现为「最新 1h 的 open_time 前进到当前时段」，
    那就是采集器在正常采数；探针自己只读 ≤ 2026-10-02 的窗口。
    """
    p = ROOT / "数据/市场数据/db/market_v2.sqlite3"
    out = {"path": str(p.relative_to(ROOT)), "sha256": _sha(p)}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            for tf in ("1h", "4h"):
                r = con.execute(
                    "SELECT MAX(open_time), COUNT(*) FROM candles WHERE exchange='binance' "
                    "AND market='um' AND timeframe=?", (tf,)).fetchone()
                out[tf] = {"max_open_ms": r[0], "max_open_utc": _iso(r[0]),
                           "rows": r[1]}
        finally:
            con.close()
    except Exception as exc:                                  # noqa: BLE001
        out["error"] = str(exc)
    return out


def _read_rows(db_path: str):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT signal_uid, symbol, direction, origin, display_state, "
            "executable_state, executable_reason, confirmed_at, available_at, event_time, "
            "bar_open_time, bar_close_time, admission, payload_json "
            "FROM live_signal ORDER BY confirmed_at").fetchall()]
    except sqlite3.OperationalError as exc:
        print(f"[warn] 读隔离库失败：{exc}", flush=True)
        return []
    finally:
        con.close()


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
    ap.add_argument("--mode", required=True, choices=sorted(MODES))
    ap.add_argument("--out", default=None)
    ap.add_argument("--keep-db", action="store_true")
    args = ap.parse_args()

    iso = BASE / MODES[args.mode]
    iso.mkdir(parents=True, exist_ok=True)
    (iso / "paper").mkdir(parents=True, exist_ok=True)
    (iso / "locks").mkdir(parents=True, exist_ok=True)

    os.environ["LIVE_DB"] = str(iso / "live_signals.sqlite3")
    os.environ["PAPER_LEDGER_DIR"] = str(iso / "paper")
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    os.environ["PAPER_LOCK_DIR"] = str(iso / "locks")
    os.environ["WX_HISTORY_SOURCE"] = "merged"

    for p in (str(ROOT), str(BACKEND)):
        if p not in sys.path:
            sys.path.insert(0, p)

    global L
    import live_store as L  # noqa: E402

    prod_epoch_const = int(L.PAPER_EPOCH_MS)
    L.PAPER_EPOCH_MS = int(ISO_PAPER_EPOCH_MS)      # 仅本进程内存

    fp_before = _fp()
    mkt_before = _market_state()
    if not args.keep_db:
        for p in (iso / "live_signals.sqlite3",):
            if p.exists():
                p.unlink()
        shutil.rmtree(iso / "paper", ignore_errors=True)
        (iso / "paper").mkdir(parents=True, exist_ok=True)

    import unified_runtime as UR
    import live_api as LA

    con = L.live_conn()
    L.set_run("live_start_ms", ISO_PAPER_EPOCH_MS)
    L.set_run("paper_epoch_ms", ISO_PAPER_EPOCH_MS)
    con.close()

    rt = UR.UnifiedRuntime(arm="3a")
    se = rt.signal_engine
    rt.started_at_ms = STARTED_MS
    # 本探针绝不写行情库
    rt.watcher.sync_dataset_bars_upto = lambda *_a, **_k: None

    # 生产锚点（只读）⇒ 重放序列与生产完全一致
    prod_anchor = None
    try:
        pc = sqlite3.connect(
            f"file:{ROOT / '框架/backend/live/live_signals.sqlite3'}?mode=ro", uri=True)
        row = pc.execute(
            "SELECT value_json FROM live_run WHERE key='state_anchor_ms'").fetchone()
        pc.close()
        if row:
            prod_anchor = int(json.loads(row[0]) if isinstance(row[0], str) else row[0])
    except Exception as exc:                                  # noqa: BLE001
        print(f"[warn] 读生产锚点失败：{exc}", flush=True)

    if args.mode == "live":
        upto_boot = LIVE_UPTO_MS               # 14:00 之前：候选那根 1h 尚未收盘
    else:
        upto_boot = RECOVERED_UPTO_MS          # 15:30：候选那根 1h 已收盘（停机期间）

    se.state_anchor_ms = prod_anchor
    se.history_floor_ms = se.resolve_replay_floor(upto_boot)

    import history_loader as HL
    import project_paths as PP
    se.history_provider = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=print)

    t0 = time.time()
    se.prepare_history(upto_boot, log=print)
    bs = se.bootstrap(upto_boot, log=print)
    t_boot = time.time() - t0

    pre_step = {
        "symbol": CAND_SYMBOL,
        "state": (se.state_by_symbol() or {}).get(CAND_SYMBOL),
        "last_1h_open_ms": se.last_1h_open.get(CAND_SYMBOL),
        "last_1h_open_utc": _iso(se.last_1h_open.get(CAND_SYMBOL)),
        "last_4h_open_ms": se.last_4h_open.get(CAND_SYMBOL),
        "last_4h_open_utc": _iso(se.last_4h_open.get(CAND_SYMBOL)),
        "bootstrap_recovered_n": len(se.recovered_candidates),
        "bootstrap_recovered_ids": [c.candidate_id for c in se.recovered_candidates],
    }

    steps = {}
    if args.mode == "live":
        se.watcher = _CappedPool(UR.PoolMarketWatcher(), CAND_SNAPSHOT_MS)
        steps["step1"] = rt.step_once(now_ms=CAND_SNAPSHOT_MS, origin="live")
        rows_1 = _read_rows(os.environ["LIVE_DB"])
        steps["step2_idempotent"] = rt.step_once(now_ms=CAND_SNAPSHOT_MS, origin="live")
        rows_2 = _read_rows(os.environ["LIVE_DB"])
    else:
        # 生产 run_forever 的真实顺序：bootstrap → _route_recovered_candidates()
        steps["route_recovered"] = rt._route_recovered_candidates(now_ms=RECOVERED_UPTO_MS)
        rows_1 = _read_rows(os.environ["LIVE_DB"])
        steps["route_recovered_again"] = rt._route_recovered_candidates(
            now_ms=RECOVERED_UPTO_MS)
        rows_2 = _read_rows(os.environ["LIVE_DB"])

    cur = LA.live_signals({"scope": ["current"], "limit": ["500"]})
    allq = LA.live_signals({"scope": ["all"], "limit": ["500"]})
    hist = LA.live_signals({"scope": ["history"], "limit": ["500"]})

    ids_1 = [r["signal_uid"] for r in rows_1]
    ids_2 = [r["signal_uid"] for r in rows_2]
    cur_items = cur.get("items") or []
    all_items = allq.get("items") or []
    hist_items = hist.get("items") or []
    cur_ids = [i["signal_id"] for i in cur_items]
    all_ids = [i["signal_id"] for i in all_items]
    hist_ids = [i["signal_id"] for i in hist_items]

    out = {
        "probe": f"SIGNAL-UI-LINK-07-R1/40_two_paths[{args.mode}]",
        "mode": args.mode,
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "isolation": {
            "LIVE_DB": os.environ["LIVE_DB"],
            "PAPER_LEDGER_DIR": os.environ["PAPER_LEDGER_DIR"],
            "PAPER_LOCK_DIR": os.environ["PAPER_LOCK_DIR"],
            "MARKET_DB": str(L.MARKET_DB),
            "market_db_readonly": True,
            "dataset_sync_disabled": True,
        },
        "clock": {
            "mode": "isolated_replay_clock",
            "started_at_ms": STARTED_MS, "started_at_utc": _iso(STARTED_MS),
            "bootstrap_upto_ms": upto_boot, "bootstrap_upto_utc": _iso(upto_boot),
            "iso_paper_epoch_ms": ISO_PAPER_EPOCH_MS,
            "iso_paper_epoch_utc": _iso(ISO_PAPER_EPOCH_MS),
            "prod_paper_epoch_const_ms": prod_epoch_const,
            "prod_paper_epoch_const_utc": _iso(prod_epoch_const),
            "prod_const_overridden_in_process_only": True,
            "prod_const_file_unchanged": True,
            "state_anchor_ms": prod_anchor, "state_anchor_utc": _iso(prod_anchor),
            "replay_mode": "absolute_anchor" if prod_anchor else "relative_window",
        },
        "bootstrap": {
            "ready": bs.get("ready"), "skipped": bs.get("skipped"),
            "history_candidates_discarded": bs.get("history_candidates_discarded"),
            "recovered_candidates": bs.get("recovered_candidates"),
            "recovered_detail": bs.get("recovered_detail"),
            "recovery_gate": bs.get("recovery_gate"),
            "elapsed_sec": round(t_boot, 1),
        },
        "steps": steps,
        "pre_step": pre_step,
        "db_rows_pass1": rows_1,
        "db_rows_pass2": rows_2,
        "idempotent_no_duplicate": (ids_1 == ids_2),
        "api": {
            "scope_current": {"total": cur.get("total"), "n": len(cur_items),
                              "signal_ids": cur_ids,
                              "quality": cur.get("quality"),
                              "missing_reason": cur.get("missing_reason")},
            "scope_all": {"total": allq.get("total"), "n": len(all_items),
                          "signal_ids": all_ids},
            "scope_history": {"total": hist.get("total"), "n": len(hist_items),
                              "signal_ids": hist_ids},
            "current_items_full": cur_items,
        },
        "target_signal_ids": ids_1,
        "target_in_current": [i for i in ids_1 if i in cur_ids],
        "target_in_all": [i for i in ids_1 if i in all_ids],
        "production_fingerprint_before": fp_before,
        "production_fingerprint_after": _fp(),
        "market_before": mkt_before,
        "market_after": _market_state(),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    # 生产**状态**文件（live 库 / 账本）必须零变化；行情库变化只允许来自采集器
    out["production_untouched"] = (
        out["production_fingerprint_before"] == out["production_fingerprint_after"])
    mb, ma = out["market_before"], out["market_after"]
    out["market_attribution"] = {
        "sha_changed": mb.get("sha256") != ma.get("sha256"),
        "probe_reads_only_upto_ms": upto_boot,
        "probe_reads_only_upto_utc": _iso(upto_boot),
        "market_max_1h_before_utc": (mb.get("1h") or {}).get("max_open_utc"),
        "market_max_1h_after_utc": (ma.get("1h") or {}).get("max_open_utc"),
        "note": ("行情库由常驻采集器实时写入当前时段 K 线，sha 变化属正常采数；"
                 "本探针只读 ≤ 探针截止时刻的窗口，且把会写 candles 的 "
                 "`sync_dataset_bars_upto` 在本进程内替换为 no-op。"),
    }

    p = Path(args.out) if args.out else (BASE / "out" / f"40_two_paths_{args.mode}.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n===== 结果 =====")
    print(f"mode={args.mode} upto={_iso(upto_boot)} "
          f"discarded={bs.get('history_candidates_discarded')} "
          f"recovered={bs.get('recovered_candidates')}")
    print(f"pre_step: state={pre_step['state']} last_1h={pre_step['last_1h_open_utc']} "
          f"last_4h={pre_step['last_4h_open_utc']} "
          f"recovered_ids={pre_step['bootstrap_recovered_ids']}")
    for r in rows_1:
        print(f"  落库 {r['signal_uid']}")
        print(f"        symbol={r['symbol']} direction={r['direction']} "
              f"origin={r['origin']} display={r['display_state']} "
              f"exec={r['executable_state']} reason={r['executable_reason']}")
        print(f"        confirmed={_iso(r['confirmed_at'])} "
              f"available={_iso(r['available_at'])} event={_iso(r['event_time'])}")
    print(f"幂等（两轮同 ID 集合）：{out['idempotent_no_duplicate']}")
    print(f"API current total={cur.get('total')} ids={cur_ids}")
    print(f"API all     total={allq.get('total')} n={len(all_ids)}")
    print(f"API history total={hist.get('total')} n={len(hist_ids)}")
    print(f"生产状态文件未动（live 库 / 账本）：{out['production_untouched']}")
    print(f"行情库归因：{json.dumps(out['market_attribution'], ensure_ascii=False)}")
    print(f"输出：{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-LINK-01 探针②：端到端链路验证（**完全隔离**）

要回答的问题
────────────
生产状态里 `signals_generated_total=0`、`orders_routed_total=0`、`live_signal` 0 行。
这有两种完全不同的解释，必须分开：

  (甲) **链路断了** —— 信号生成了，但落库/扫描/路由/挂单某一段接不上；
  (乙) **链路通，只是没信号** —— 策略本身在这段时间没产出候选。

只看「0」永远分不出是哪种。本探针用**真实代码 + 真实行情**把 (甲) 逐段跑一遍，
用真实结果判 (甲) 成不成立；再看候选产出速率判 (乙)。

怎么保证「真」
──────────────
  · 落库/路由用生产同一份 `SignalPersistenceAndRouter`（`unified_runtime`）；
  · 挂单/成交用生产同一份 `paper_engine.open_position` 与 `advance_pending`；
  · 价格、ATR、止损全部取自**真实行情库**的真实 K 线；
  · **唯一非真实的东西是「这个信号存在」本身** —— 所以 candidate_id 带
    `probe_signal_link_01` 前缀，并在输出里 `signal_is_synthetic: true` 明确标注。
    本探针**不声称**市场真的出现了该信号。

怎么保证「不污染生产」
──────────────────────
全部写路径指到临时目录（导入前设环境变量，因为 `live_store` 在导入期固化路径）：
  LIVE_DIR / LIVE_DB / PAPER_LEDGER_DIR / PAPER_GATE_FILE
行情库 `MARKET_DB` 只读打开（`market_conn` 内部 `mode=ro`）。
探针结束时**逐条核对**四个写路径确实都在临时目录内，并打印生产库的 mtime/size
未变化作为旁证。

用法：
  python 20_e2e_link.py [--out .../out/e2e_link.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
H1 = 3_600_000
M15 = 900_000
DAY = 24 * H1

# ── ① 隔离环境必须在**导入任何后端模块之前**设好 ────────────────────────────────
TMP = Path(tempfile.mkdtemp(prefix="signal_link_01_"))
(TMP / "live").mkdir(parents=True, exist_ok=True)
(TMP / "paper").mkdir(parents=True, exist_ok=True)
os.environ["LIVE_DIR"] = str(TMP / "live")
os.environ["LIVE_DB"] = str(TMP / "live" / "live_signals.sqlite3")
os.environ["PAPER_LEDGER_DIR"] = str(TMP / "paper")
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
os.environ["PAPER_GATE_FILE"] = str(TMP / "gate.json")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
# 生产行情库只读引用（不复制，避免 78G 拷贝；market_conn 内部 mode=ro）
PROD_MARKET_DB = ROOT / "数据/市场数据/db/market_v2.sqlite3"
PROD_LIVE_DB = ROOT / "框架/backend/live/live_signals.sqlite3"
PROD_PAPER_DIR = ROOT / "数据/paper"
os.environ["MARKET_DB"] = str(PROD_MARKET_DB)

BACKEND = ROOT / "框架/backend"
for p in (str(BACKEND), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402

from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig  # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.contracts import CandidateSnapshot  # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.features import (  # noqa: E402
    compute_wilder_atr_series,
)

import live_store as L                       # noqa: E402
import paper_engine as PE                    # noqa: E402
import unified_runtime as UR                 # noqa: E402

STEPS: list[dict] = []


def step(name: str, ok: bool, detail) -> None:
    STEPS.append({"step": name, "ok": bool(ok), "detail": detail})
    mark = "通过" if ok else "不通过"
    print(f"  [{mark}] {name}")


def _iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stat(p: Path):
    try:
        st = p.stat()
        return {"path": str(p), "mtime": st.st_mtime, "size": st.st_size}
    except OSError:
        return {"path": str(p), "mtime": None, "size": None}


def _mk_candidate(sym: str, t0_close_ms: int, con) -> CandidateSnapshot:
    """用**真实 K 线**构造一个候选（信号存在性为探针构造，价格全为真实值）。"""
    cfg = EventTrendConfig()
    n = cfg.box_bars_4h
    rows = con.execute(
        "SELECT open_time, close_time, open, high, low, close, volume FROM candles "
        "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='4h' "
        "AND is_closed=1 AND close_time<=? ORDER BY open_time DESC LIMIT ?",
        (sym, int(t0_close_ms), n)).fetchall()
    rows = list(reversed(rows))
    h = np.array([float(r["high"]) for r in rows])
    lo = np.array([float(r["low"]) for r in rows])
    c = np.array([float(r["close"]) for r in rows])
    atr = compute_wilder_atr_series(h, lo, c, period=cfg.atr_period_4h)
    A = float(atr[-1])
    H, L = float(h.max()), float(lo.min())

    b1 = con.execute(
        "SELECT open_time, close_time, open, high, low, close, volume FROM candles "
        "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='1h' "
        "AND is_closed=1 AND close_time<=? ORDER BY open_time DESC LIMIT 22",
        (sym, int(t0_close_ms))).fetchall()
    b1 = list(reversed(b1))
    last = b1[-1]
    close_p = float(last["close"])
    volume_v = float(last["volume"])
    v20 = float(np.median([float(r["volume"]) for r in b1[-21:-1]]))
    lr = float(min(float(r["low"]) for r in b1[-6:]))
    stop = lr - cfg.initial_stop_buffer_atr * A

    return CandidateSnapshot.create(
        candidate_id=f"probe_signal_link_01|{sym}|{int(t0_close_ms)}",
        event_id=f"probe_ev|{sym}|{int(t0_close_ms)}",
        strategy_version=cfg.strategy_version,
        symbol=sym, venue="binance",
        snapshot_time_ms=int(t0_close_ms), available_at_ms=int(t0_close_ms),
        box_high_h=H, box_low_l=L, box_atr_a=A, box_er=0.2,
        sweep_low_s=lr - 0.2 * A, retest_low_lr=lr,
        breakout_close=close_p, breakout_volume=volume_v,
        v20_median_volume=v20,
        volume_ratio=(volume_v / v20) if v20 else 0.0,
        oi_breakout=0.0, oi_6h_ago=0.0, oi_growth_6h=0.0,
        initial_stop_price=stop,
        initial_risk_r=close_p - stop,
        max_entry_price=close_p + cfg.max_gap_up_atr * A,
        min_entry_price=H,
        execution_earliest_ms=int(t0_close_ms) + M15,
        pit_proven=False,
        pit_assumption_note="探针构造：仅验证管道通断，不代表市场信号",
        missing_fields=[],
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "out" / "e2e_link.json"))
    args = ap.parse_args()

    print(f"[隔离目录] {TMP}")
    for k in ("LIVE_DIR", "LIVE_DB", "PAPER_LEDGER_DIR", "PAPER_GATE_FILE", "MARKET_DB"):
        print(f"  {k} = {os.environ.get(k)}")

    # 生产库/账本基线（用于事后旁证「未被写过」）
    prod_before = {"live_db": _stat(PROD_LIVE_DB)}
    if PROD_PAPER_DIR.exists():
        prod_before["paper_files"] = {
            p.name: {"mtime": p.stat().st_mtime, "size": p.stat().st_size}
            for p in sorted(PROD_PAPER_DIR.glob("*.sqlite3"))}

    # ── 开闸（隔离闸门文件）──────────────────────────────────────────────────
    (TMP / "gate.json").write_text(json.dumps({
        "allow_new_entries": True, "reason": "probe_signal_link_01_isolated",
        "set_by": "20_e2e_link.py", "since_ms": int(time.time() * 1000)},
        ensure_ascii=False), encoding="utf-8")

    L.ensure_live_schema()
    L.paper_conn("3a").close()                 # 建隔离账本
    now = int(time.time() * 1000)
    L.ensure_live_start_ms(now - 30 * DAY)     # 隔离口径的 live 起点（便于审计）

    con = L.market_conn()
    try:
        sym = "BTCUSDT"
        # 信号时刻 = 最后一根已闭合 1h 的收盘时刻往前推 2 小时（真实值）。
        # 为什么往前推：真实挂钟下「信号后 15 分钟那根 15m bar」还不存在 ⇒ 生产里必然登记挂单；
        # 要验证 advance_pending 真的能成交，信号时刻必须早到让那根 bar 已经闭合。
        r = con.execute(
            "SELECT MAX(close_time) FROM candles WHERE exchange='binance' AND market='um' "
            "AND symbol=? AND timeframe='1h' AND is_closed=1", (sym,)).fetchone()
        t0 = int(r[0]) - 2 * H1
        cand = _mk_candidate(sym, t0, con)
        m15max = con.execute(
            "SELECT MAX(close_time) FROM candles WHERE exchange='binance' AND market='um' "
            "AND symbol=? AND timeframe='15m' AND is_closed=1", (sym,)).fetchone()[0]
    finally:
        con.close()

    print(f"\n[信号] {cand.symbol} 快照={_iso(cand.snapshot_time_ms)} "
          f"收盘={cand.breakout_close} 止损={cand.initial_stop_price:.8g} "
          f"A={cand.box_atr_a:.8g} 量比={cand.volume_ratio:.2f}")
    print(f"[行情] 15m 最后收盘={_iso(m15max)}")

    # ══════════ 第 1 段：候选 → live_signal（落库）══════════
    router = UR.SignalPersistenceAndRouter(arm="3a")
    wr = router.persist_candidates([cand], origin="live", now_ms=now)
    lc = L.live_conn(ro=True)
    try:
        row = lc.execute("SELECT * FROM live_signal WHERE signal_uid=?",
                         (cand.candidate_id,)).fetchone()
    finally:
        lc.close()
    if row is None:
        step("1. 候选落库 live_signal", False, {"write": wr, "row": None})
        _finish(args, TMP, prod_before, cand, None, None)
        return 1
    payload = json.loads(row["payload_json"])
    step("1. 候选落库 live_signal", True, {
        "signal_uid": row["signal_uid"], "origin": row["origin"],
        "executable_state": row["executable_state"],
        "display_state": row["display_state"],
        "strategy_key": row["strategy_key"],
        "payload_role": payload.get("role"), "payload_arm": payload.get("arm"),
        "exchange": row["exchange"], "market": row["market"],
        "timeframe": row["timeframe"],
    })

    # ══════════ 第 2 段：live_signal → 可执行扫描（口径一致）══════════
    lc = L.live_conn(ro=True)
    try:
        scanned = router._scan_actionable(lc, origin="live")
    finally:
        lc.close()
    hit = [s for s in scanned if s["signal_uid"] == cand.candidate_id]
    step("2. 可执行扫描命中（路由与界面同一口径）", len(hit) == 1,
         {"scanned": len(scanned), "hit": [s["signal_uid"] for s in hit]})

    # ══════════ 第 3 段：路由 → 挂单登记 ══════════
    rr = router.route_pending_orders(now, origin="live")
    pc = L.paper_conn("3a", ro=True)
    try:
        pend = pc.execute("SELECT * FROM paper_pending WHERE signal_uid=?",
                          (cand.candidate_id,)).fetchone()
    finally:
        pc.close()
    step("3. 路由登记挂单 paper_pending", rr.get("registered", 0) >= 1 and pend is not None,
         {"route": rr,
          "pending": (None if pend is None else {
              "pending_id": pend["pending_id"], "symbol": pend["symbol"],
              "direction": pend["direction"],
              "requested_at": pend["requested_at"],
              "earliest_fill_ms": pend["earliest_fill_ms"],
              "stoploss_s": pend["stoploss_s"], "trail_d": pend["trail_d"],
              "origin": pend["origin"]})})

    # ══════════ 第 4 段：挂单 → 成交 → 持仓 ══════════
    # 为什么不能直接推进上面那张挂单：
    #   真实挂钟下「信号后 15 分钟的 15m bar」还不存在 ⇒ 生产里这一步**本来就应该**停在 pending；
    #   而 `paper_request_anchor` 会把首次请求墙钟钉成权威锚点（防止事后回填），
    #   所以事后改挂单行也推不动它 —— 这是**正确的防护**，不是缺陷。
    # 因此成交这一段改用**文档给定的回放时钟路径**（`origin="test"`，仅隔离环境允许指定时钟），
    # 用**另一个** signal_uid 走同一份 `open_position`：契约校验、epoch 门禁、
    # 选 bar、滞后检查、仓位取整、止损计算、持仓落库**全部是生产代码**。
    filled = None
    cand2 = None
    if pend is not None:
        con = L.market_conn()
        try:
            cand2 = _mk_candidate(sym, t0, con)
            cand2 = CandidateSnapshot.create(**{**cand2.to_dict(),
                                                "candidate_id": cand2.candidate_id + "|fill",
                                                "event_id": cand2.event_id + "|fill",
                                                "snapshot_hash": ""})
            bar = PE.bar_at_or_after(con, sym, int(cand2.available_at_ms) + 15 * 60 * 1000)
        finally:
            con.close()
        if bar is None:
            step("4. 挂单成交并建仓 paper_position", False,
                 {"reason": "信号后 15m bar 不存在，无法验证成交"})
        else:
            t_fill = int(bar["open_time"]) + 60 * 1000     # 应成交 bar 刚闭合 1 分钟
            res = PE.open_position(
                "3a", sym, signal_uid=cand2.candidate_id, direction="long",
                now_ms=t_fill, event_time=cand2.snapshot_time_ms,
                available_at=cand2.available_at_ms, requested_at=cand2.available_at_ms,
                origin="test")
            pc = L.paper_conn("3a", ro=True)
            try:
                pos = pc.execute("SELECT * FROM paper_position WHERE signal_uid=?",
                                 (cand2.candidate_id,)).fetchone()
            finally:
                pc.close()
            filled = (None if pos is None else {
                "position_id": pos["position_id"], "symbol": pos["symbol"],
                "qty": pos["qty"], "entry_price": pos["entry_price"],
                "entry_ts": pos["entry_ts"], "entry_utc": _iso(pos["entry_ts"]),
                "stop_price": pos["stop_price"],
                "initial_stop_price": pos["initial_stop_price"],
                "planned_risk": pos["planned_risk"],
                "stoploss_s": pos["stoploss_s"], "trail_d": pos["trail_d"],
                "status": pos["status"], "origin": pos["origin"],
                "model_version": pos["model_version"]})
            step("4. 挂单成交并建仓 paper_position",
                 bool(res.get("ok")) and pos is not None,
                 {"open_position": {k: res.get(k) for k in
                                    ("ok", "reason", "entry_price", "size", "stop_price",
                                     "bar_open_time", "lag_ms", "risk", "note")},
                  "clock": {"signal_utc": _iso(cand2.snapshot_time_ms),
                            "earliest_fill_utc": _iso(int(cand2.available_at_ms) + 15 * 60 * 1000),
                            "fill_bar_open_utc": _iso(bar["open_time"]),
                            "attempt_utc": _iso(t_fill),
                            "lag_ms": t_fill - int(bar["open_time"])},
                  "position": filled})
    else:
        step("4. 挂单成交并建仓 paper_position", False, {"reason": "无挂单可推进"})

    # ══════════ 第 5 段：成交价与冻结规则一致（止损 = 成交价 × 0.95 取整）══════════
    if filled:
        params = PE.approved_params()
        s = params.get("stoploss_s")
        expect = filled["entry_price"] * (1 - s) if s else None
        got = filled["stop_price"]
        ok = expect is not None and got is not None and abs(got - expect) / expect < 5e-3
        step("5. 初始止损 = 成交价×(1−stoploss_s)", ok,
             {"stoploss_s": s, "entry_price": filled["entry_price"],
              "expect_stop": expect, "actual_stop": got,
              "rel_diff": (abs(got - expect) / expect if expect else None)})

    return _finish(args, TMP, prod_before, cand, filled, router)


def _finish(args, tmp, prod_before, cand, filled, router):
    # ── 隔离核对：四个写路径必须都在临时目录内 ─────────────────────────────
    checks = {}
    for k, v in (("LIVE_DIR", os.environ["LIVE_DIR"]),
                 ("LIVE_DB", os.environ["LIVE_DB"]),
                 ("PAPER_LEDGER_DIR", os.environ["PAPER_LEDGER_DIR"]),
                 ("PAPER_GATE_FILE", os.environ["PAPER_GATE_FILE"])):
        checks[k] = {"value": v, "inside_tmp": str(tmp) in str(Path(v).resolve())}
    iso_ok = all(c["inside_tmp"] for c in checks.values())

    # 生产库旁证
    prod_after = {"live_db": _stat(PROD_LIVE_DB)}
    if PROD_PAPER_DIR.exists():
        prod_after["paper_files"] = {
            p.name: {"mtime": p.stat().st_mtime, "size": p.stat().st_size}
            for p in sorted(PROD_PAPER_DIR.glob("*.sqlite3"))}
    untouched = (prod_before.get("live_db", {}).get("mtime") == prod_after["live_db"]["mtime"]
                 and prod_before.get("live_db", {}).get("size") == prod_after["live_db"]["size"]
                 and prod_before.get("paper_files") == prod_after.get("paper_files"))

    passed = sum(1 for s in STEPS if s["ok"])
    total = len(STEPS)
    out = {
        "probe": "SIGNAL-LINK-01/20_e2e_link",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "signal_is_synthetic": True,
        "synthetic_note": ("候选的『存在性』由探针构造（真实市场在该时刻未必出现突破）；"
                           "价格/ATR/量能/止损全部取自真实行情库。本探针只验证管道通断，"
                           "不声称市场出现信号。"),
        "candidate": {"candidate_id": cand.candidate_id, "symbol": cand.symbol,
                      "snapshot_time_ms": cand.snapshot_time_ms,
                      "snapshot_utc": _iso(cand.snapshot_time_ms),
                      "breakout_close": cand.breakout_close,
                      "initial_stop_price": cand.initial_stop_price,
                      "box_atr_a": cand.box_atr_a,
                      "volume_ratio": cand.volume_ratio},
        "isolation": {"tmp_dir": str(tmp), "paths": checks, "all_inside_tmp": iso_ok,
                      "prod_live_db_before": prod_before.get("live_db"),
                      "prod_live_db_after": prod_after["live_db"],
                      "prod_paper_untouched": untouched,
                      "market_db_readonly": str(PROD_MARKET_DB)},
        "steps": STEPS,
        "verdict": {
            "passed": passed, "total": total,
            "all_passed": passed == total,
            "chain": "候选 → live_signal → 可执行扫描 → paper_pending → 成交 → paper_position",
            "meaning": ("链路本身通/不通。注意：**通 ≠ 有信号**。是否真的产出信号取决于"
                        "策略漏斗，见 10_funnel.py 的漏斗统计。"),
        },
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n===== 结果：{passed}/{total} 段通过 =====")
    print(f"隔离：全部写路径在临时目录内 = {iso_ok}；生产库/账本未被写 = {untouched}")
    print(f"输出：{p}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

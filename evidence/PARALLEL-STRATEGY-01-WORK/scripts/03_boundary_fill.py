#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""03 · 边界对齐的精确撮合延迟：证明「撮合发生在执行点后几秒」，不是 15 分钟后。

为什么要单独做这一条：02 的 P1 虽然成交了，但 `lag_ms` 里含着「脚本准备时间」
（bar 已开盘数分钟才跑到用例），不能代表生产条件。生产 `step_once` 每 1~2 秒一轮，
执行点一到就会处理。本脚本把时序钉死。

**必须走两段式（这条踩过坑，见 §历史）**：
  生产真实链路是「信号时刻 `open_position` → 登记挂单 → 执行点 `advance_pending` → 成交」。
  如果只在执行点直接调一次 `open_position` 并把 `event_time` 设成 15 分钟前，
  `paper_engine.py:1259-1262` 会因为 `origin='live'` 且无 `pending_id` 而
  `is_replay=False` ⇒ 首次请求墙钟取**真实挂钟**（= 现在）⇒ 触发
  `earliest_fill_before_first_request_clock` 拒单。那不是缺陷，正是「旧信号不得续期」的保护。
  所以本脚本严格照生产两段式跑。

时序：
  S  = 上一个 15m 边界（= 信号确认 bar 收盘 = `event_time`）
  T0 = S + 15m（= 执行点；信号确认后下一个合法 15m 执行点）
  立刻（S 之后任意时刻）：`open_position(event_time=S)` ⇒ 应登记挂单 `pending_future_fill`
  T0 + 1.5s ：读实时 bar（应已是 T0 那根、is_closed=0）→ `advance_pending(T=T0+1.5s)` 撮合
  T0 + 20s  ：再读同一根，证明 **open 在 bar 内恒定**（不是某一瞬间的报价）
  T0 + 15m + 90s：读 `candles` 里该 bar 已闭合的 open，证明与成交价**逐位相同**

全部在隔离账本上；行情库 `mode=ro`；`origin` 取**生产原值 `live`**，
且 `WX_REALITY_FILL_MODE` 已从环境 pop 掉 ⇒ 验证的是**不带任何开关的生产默认路径**。
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

UTC = dt.timezone.utc
PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "PARALLEL-STRATEGY-01-WORK"
ISO = EVID / "isolated"
OUT = EVID / "out"
SCRIPTS = EVID / "scripts"
BACKEND = PROJ / "框架" / "backend"
MARKET_DB = PROJ / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
PROD_LEDGER = PROJ / "数据" / "paper"
PROD_LIVE = PROJ / "框架" / "backend" / "live" / "live_signals.sqlite3"
M15 = 900_000
# 为什么不用 BTCUSDT：隔离工作区是**生产账本的副本**，里面 #4 是 `test_probe_succ`
# 的 BTCUSDT 未平仓持仓（工单要求原样保留），会先触发「同币已有持仓 ⇒ 不加仓」，
# 挂单根本登记不上。ETHUSDT 在副本里没有持仓，且 P1 已实测其实时 15m 行情可用。
SYMBOL = (sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT")


def iso(ms):
    return None if ms is None else dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def ro(p):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=8)
    con.row_factory = sqlite3.Row
    return con


def rt_bar(symbol=SYMBOL):
    con = ro(MARKET_DB)
    r = con.execute(
        "SELECT open_time,close_time,open,high,low,close,is_closed,event_time,received_at,"
        "source,transport FROM realtime_klines WHERE exchange='binance' AND market='um' "
        "AND symbol=? AND timeframe='15m'", (symbol,)).fetchone()
    con.close()
    return dict(r) if r else None


def closed_bar(symbol, open_time):
    con = ro(MARKET_DB)
    r = con.execute(
        "SELECT open,high,low,close,close_time,available_at FROM candle_versions "
        "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
        "AND open_time=? AND is_closed=1", (symbol, open_time)).fetchone()
    con.close()
    return dict(r) if r else None


def build_ws(tag):
    root = ISO / tag
    if root.exists():
        shutil.rmtree(root)
    (root / "ledger").mkdir(parents=True)
    (root / "locks").mkdir(parents=True)
    for arm in ("3a", "3b", "3c", "3d"):
        src = PROD_LEDGER / f"paper_{arm}.sqlite3"
        if src.exists():
            shutil.copy2(src, root / "ledger" / f"paper_{arm}.sqlite3")
    live = root / "live_signals.sqlite3"
    if PROD_LIVE.exists():
        shutil.copy2(PROD_LIVE, live)
    gate = root / "locks" / "paper_new_entry_gate.json"
    gate.write_text(json.dumps({"allow_new_entries": True,
                                "reason": "iso_boundary_03",
                                "set_by": "parallel_strategy_01_work",
                                "since_ms": 1_792_748_844_240,
                                "scope": "server_side_new_entry_only"},
                               ensure_ascii=False), encoding="utf-8")
    return {"root": root, "ledger": root / "ledger", "live": live,
            "gate": gate, "locks": root / "locks"}


def run_case(ws, cfg):
    import os
    env = dict(os.environ)
    env.update({"LIVE_DB": str(ws["live"]),
                "PAPER_LEDGER_DIR": str(ws["ledger"]),
                "PAPER_LOCK_DIR": str(ws["locks"]),
                "PAPER_GATE_FILE": str(ws["gate"]),
                "PAPER_LEDGER_ALLOW_INIT": "1",
                "MARKET_DB": str(MARKET_DB),
                "PYTHONPATH": str(BACKEND),
                "ISO_CASE": json.dumps(cfg, ensure_ascii=False),
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "no_proxy": "127.0.0.1,localhost,::1"})
    env.pop("WX_REALITY_FILL_MODE", None)
    p = subprocess.run([sys.executable, str(SCRIPTS / "_iso_case.py")],
                       capture_output=True, text=True, env=env, timeout=180)
    for ln in (p.stdout or "").splitlines():
        if ln.startswith("ISO_RESULT "):
            return json.loads(ln[len("ISO_RESULT "):])
    return {"error": "no result", "stdout": (p.stdout or "")[-1500:],
            "stderr": (p.stderr or "")[-2500:]}


def pending_rows(ws):
    con = ro(ws["ledger"] / "paper_3a.sqlite3")
    rows = [dict(r) for r in con.execute(
        "SELECT pending_id,symbol,status,reason,requested_at,earliest_fill_ms,origin,"
        "first_request_ms,event_time,available_at FROM paper_pending ORDER BY pending_id")]
    con.close()
    return rows


def dump(rec, path=None):
    """每步都落盘：上一次跑因为只有结束时才写文件，进程卡住时**完全看不到卡在哪**。"""
    p = path or (OUT / "03_boundary_fill.partial.json")
    OUT.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return p


def main() -> int:
    now = int(time.time() * 1000)
    # S = 刚刚过去的那个 15m 边界（信号确认 bar 的收盘时刻）；T0 = 下一个边界（执行点）
    sig_t = (now // M15) * M15
    t0 = sig_t + M15
    rec = {"generated_at_utc": iso(now), "symbol": SYMBOL,
           "signal_confirmed_at": sig_t, "signal_confirmed_at_utc": iso(sig_t),
           "planned_exec_at": t0, "planned_exec_at_utc": iso(t0),
           "delay_minutes": 15, "steps": {}}

    # ── 步骤 0：信号时刻登记挂单（生产第一段）────────────────────────────────
    ws = build_ws("P3_boundary")
    t_reg = int(time.time() * 1000)
    cfg0 = {"name": "B0_register", "action": "open", "symbol": SYMBOL,
            "uid": f"iso_boundary_{sig_t}",
            "event_time": sig_t, "available_at": sig_t, "requested_at": sig_t,
            "attempt_ms": t_reg, "origin": "live"}
    r0 = run_case(ws, cfg0)
    res0 = r0.get("result") or {}
    rec["steps"]["0_register_pending"] = {
        "call_at_utc": iso(t_reg), "call_minus_signal_ms": t_reg - sig_t,
        "result": res0, "resolved": r0.get("resolved")}
    print(f"[0] 登记挂单 @{iso(t_reg)} → {res0.get('reason')} pending_id={res0.get('pending_id')}",
          flush=True)
    rec["steps"]["0_register_pending"]["pending_rows_after"] = pending_rows(ws)
    dump(rec)

    # ── 步骤 1：等执行点，边界后立刻轮询实时 bar，记录「第一次看到执行点那根」的时刻 ──
    # 为什么轮询而不是只读一次：01:45 那一跑实测，边界后 1.5 秒实时表里**没有该标的的行**
    # （20 秒后才有）。只读一次会把「数据还没到」误记成「bar 不对」。
    # 这里如实记录**首次可见延迟**，它本身就是一条重要事实。
    wait = t0 + 300 - int(time.time() * 1000)      # 边界后 0.3 秒开始找
    if wait > 0:
        time.sleep(wait / 1000.0)
    b1 = None
    first_seen = None
    polls = []
    for _ in range(200):                            # 最多 20 秒
        t_read = int(time.time() * 1000)
        b = rt_bar()
        polls.append({"at_utc": iso(t_read), "minus_exec_ms": t_read - t0,
                      "present": b is not None,
                      "open_time_utc": iso(b["open_time"]) if b else None,
                      "is_closed": b["is_closed"] if b else None})
        if b is not None and int(b["open_time"]) == t0:
            b1 = b
            first_seen = t_read - t0
            break
        time.sleep(0.1)
    rec["steps"]["1_read_at_boundary"] = {
        "read_at_utc": iso(t_read), "read_minus_exec_ms": t_read - t0,
        "first_seen_minus_exec_ms": first_seen,
        "polls_n": len(polls), "polls": polls[-40:],
        "bar": b1, "bar_open_utc": iso(b1["open_time"]) if b1 else None,
        "bar_is_expected": bool(b1 and int(b1["open_time"]) == t0),
        "bar_is_closed": b1["is_closed"] if b1 else None}
    print(f"[1] 执行点首见 bar @+{first_seen} ms（轮询 {len(polls)} 次）"
          f" open_time={rec['steps']['1_read_at_boundary']['bar_open_utc']} "
          f"is_closed={rec['steps']['1_read_at_boundary']['bar_is_closed']}", flush=True)
    dump(rec)

    # ── 步骤 2：执行点推进挂单撮合（生产第二段）──────────────────────────────
    # **必须重试**：01:45 那一跑实测发现，bar 边界后 1.5 秒 `realtime_klines` 里
    # 该标的**瞬时没有行**（20 秒后才有）。此时引擎走闭 bar 路径、找不到已闭合 bar
    # ⇒ 正确返回 `pending_future_fill`（**不拒单、不回填**）。
    # 生产 `step_once` 每 1~2 秒一轮，下一轮自然会补上；本脚本只调一次就永远等不到，
    # 所以这里照生产节奏重试，并逐次记录「第几次尝试才拿到实时行」。
    attempts = []
    res = {}
    adv = {}
    t_call = None
    for i in range(60):
        t_call = int(time.time() * 1000)
        cfg1 = {"name": f"B1_advance_{i}", "action": "advance_pending",
                "symbol": SYMBOL, "attempt_ms": t_call, "origin": "live"}
        r1 = run_case(ws, cfg1)
        adv = r1.get("result") or {}
        filled = (adv.get("filled") or [])
        rej = (adv.get("rejected") or [])
        attempts.append({"n": i + 1, "at_utc": iso(t_call),
                         "minus_exec_ms": t_call - t0,
                         "filled": bool(filled),
                         "rejected": rej[:2] if rej else [],
                         "still_pending": adv.get("still_pending"),
                         "error": r1.get("error")})
        if filled:
            res = filled[0]
            break
        if rej:
            # 真被拒（不是「数据还没到」）⇒ 立刻停，不要用重试掩盖
            break
        time.sleep(1.0)
    rec["steps"]["2_advance_fill"] = {
        "call_at_utc": iso(t_call), "call_minus_exec_ms": (t_call - t0) if t_call else None,
        "attempts_n": len(attempts), "attempts": attempts,
        "advance": adv, "resolved": r1.get("resolved"),
        "pending_rows_after": pending_rows(ws)}
    print(f"[2] 推进撮合 @{iso(t_call)} 第 {len(attempts)} 次尝试 ok={res.get('ok')} "
          f"reason={res.get('reason')} reality_fill={res.get('reality_fill')} "
          f"lag_ms={res.get('fill_lag_ms')}", flush=True)
    dump(rec)

    # ── 步骤 3：20 秒后再读同一根，证明 open 在 bar 内恒定 ─────────────────────
    time.sleep(20)
    t_read2 = int(time.time() * 1000)
    b2 = rt_bar()
    rec["steps"]["3_open_invariance"] = {
        "read_at_utc": iso(t_read2),
        "bar_open_time_utc": iso(b2["open_time"]) if b2 else None,
        "open": b2["open"] if b2 else None,
        "same_bar": bool(b2 and int(b2["open_time"]) == t0),
        "open_unchanged": bool(b1 and b2 and str(b1["open"]) == str(b2["open"])),
        "close_now": b2["close"] if b2 else None,
        "high_now": b2["high"] if b2 else None,
        "low_now": b2["low"] if b2 else None}
    print(f"[3] 20s 后复读 open={rec['steps']['3_open_invariance']['open']} "
          f"同根={rec['steps']['3_open_invariance']['same_bar']} "
          f"未变={rec['steps']['3_open_invariance']['open_unchanged']}", flush=True)
    dump(rec)

    # ── 步骤 4：等 bar 闭合并落库，比对 candles 的 open ────────────────────────
    wait2 = t0 + M15 + 90_000 - int(time.time() * 1000)
    if wait2 > 0:
        time.sleep(wait2 / 1000.0)
    t_after = int(time.time() * 1000)
    cb = closed_bar(SYMBOL, t0)
    filled_px = res.get("entry_price")
    raw_px = res.get("raw_price")
    rec["steps"]["4_candles_after_close"] = {
        "read_at_utc": iso(t_after),
        "candles_bar": cb,
        "available_minus_close_ms": (int(cb["available_at"]) - int(cb["close_time"])
                                     if cb and cb.get("available_at") else None),
        "realtime_open_at_fill": (b1["open"] if b1 else None),
        "candles_open": cb["open"] if cb else None,
        "raw_price_used": raw_px,
        "fill_price_after_slippage": filled_px,
        "raw_equals_candles_open": bool(cb and raw_px is not None
                                        and str(raw_px) == str(cb["open"])),
        "raw_equals_realtime_open": bool(b1 and raw_px is not None
                                         and str(raw_px) == str(b1["open"]))}
    print(f"[4] candles open={rec['steps']['4_candles_after_close']['candles_open']} "
          f"成交原始价={raw_px} 逐位相同="
          f"{rec['steps']['4_candles_after_close']['raw_equals_candles_open']}", flush=True)

    # ── 判定 ──────────────────────────────────────────────────────────────────
    checks = {
        "step0_registered_pending": res0.get("reason") == "pending_future_fill",
        "effective_mode_is_realtime_only":
            (r1.get("resolved") or {}).get("effective_fill_mode") == "realtime_only",
        "bar_first_seen_within_60s":
            first_seen is not None and first_seen <= 60_000,
        "bar_at_exec_point_was_forming":
            rec["steps"]["1_read_at_boundary"]["bar_is_closed"] == 0,
        "bar_at_exec_point_is_expected_one":
            rec["steps"]["1_read_at_boundary"]["bar_is_expected"] is True,
        "fill_happened_within_60s_of_exec_point":
            bool(res.get("ok")) and int(res.get("fill_lag_ms") or 10 ** 9) <= 60_000,
        "reality_fill_flag_is_1": res.get("reality_fill") is True,
        "fill_source_is_realtime": res.get("fill_source") == "realtime_15m_open",
        "open_constant_within_bar": rec["steps"]["3_open_invariance"]["open_unchanged"] is True,
        "raw_price_equals_realtime_open":
            rec["steps"]["4_candles_after_close"]["raw_equals_realtime_open"],
        "raw_price_equals_candles_open":
            rec["steps"]["4_candles_after_close"]["raw_equals_candles_open"],
        "entry_ts_equals_exec_point": int(res.get("bar_open_time") or 0) == t0,
        "matched_at_after_exec_point":
            int(res.get("timeline", {}).get("matched_at") or 0) >= t0,
    }
    rec["checks"] = checks
    rec["all_pass"] = all(checks.values())
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "03_boundary_fill.json"
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"written {p}")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print(f"  信号 {iso(sig_t)} → 执行点 {iso(t0)} → 成交 {iso(res.get('timeline', {}).get('matched_at'))} "
          f"/ lag {res.get('fill_lag_ms')} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())

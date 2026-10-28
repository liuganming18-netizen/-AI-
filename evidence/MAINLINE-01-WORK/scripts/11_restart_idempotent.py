#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11 · 重启幂等 / 有效期 / 停机不追补（工单第 2 条的其余正反例）。

四条要证明的事：
  R1 **重启后不丢账**：复制生产账本，跑一轮 `advance_pending`（模拟重启后的第一轮），
     持仓 / 订单 / 挂单 / 已处理 四张表的行数与内容逐项不变。
  R2 **重启后不重复下单**：用已存在的 `signal_uid`（`test_probe_succ`）再开一次
     ⇒ 必须 `duplicate_signal_uid`，且不新增持仓。
  R3 **超过既有有效期不补成交**：`legacy` 模式 + 信号时刻早于 1 小时容忍窗
     ⇒ `missed_fill_moment_no_backfill`（不补、不顺延）。
  R4 **停机期间的执行点不追补**：`process_start_ms` 晚于应成交 bar 收盘
     ⇒ `fill_point_closed_before_process_start`。

全部在隔离账本副本上；行情库只读；不写生产账本。
输出 `out/11_restart_idempotent.json`。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

UTC = dt.timezone.utc
PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "MAINLINE-01-WORK"
ISO = EVID / "isolated"
OUT = EVID / "out"
SCRIPTS = EVID / "scripts"
BACKEND = PROJ / "框架" / "backend"
MARKET_DB = PROJ / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
PROD_LEDGER = PROJ / "数据" / "paper"
PROD_LIVE = PROJ / "框架" / "backend" / "live" / "live_signals.sqlite3"
M15 = 900_000
H1 = 3_600_000


def iso(ms):
    return None if ms is None else dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def now_ms():
    return int(dt.datetime.now(UTC).timestamp() * 1000)


def ro(p: Path):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def build_ws(tag: str) -> dict:
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
    gate.write_text(json.dumps({
        "allow_new_entries": True, "reason": "iso_mainline_01",
        "set_by": "mainline_01_work", "since_ms": 1_792_748_844_240,
        "scope": "server_side_new_entry_only"}, ensure_ascii=False), encoding="utf-8")
    return {"root": root, "ledger": root / "ledger", "live": live,
            "gate": gate, "locks": root / "locks"}


def run_case(ws: dict, cfg: dict, mode: str | None = None) -> dict:
    env = dict(os.environ)
    env.update({
        "LIVE_DB": str(ws["live"]),
        "PAPER_LEDGER_DIR": str(ws["ledger"]),
        "PAPER_LOCK_DIR": str(ws["locks"]),
        "PAPER_GATE_FILE": str(ws["gate"]),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "MARKET_DB": str(MARKET_DB),
        "PYTHONPATH": str(BACKEND),
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    })
    env.pop("WX_REALITY_FILL_MODE", None)
    if mode:
        env["WX_REALITY_FILL_MODE"] = mode
    env["ISO_CASE"] = json.dumps(cfg, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCRIPTS / "_iso_case.py")],
                       capture_output=True, text=True, env=env, timeout=240)
    line = None
    for ln in (p.stdout or "").splitlines():
        if ln.startswith("ISO_RESULT "):
            line = ln[len("ISO_RESULT "):]
    if line is None:
        return {"ok": False, "subprocess_rc": p.returncode,
                "stdout": (p.stdout or "")[-1500:], "stderr": (p.stderr or "")[-2500:]}
    try:
        return json.loads(line)
    except Exception as exc:                                   # noqa: BLE001
        return {"parse_error": str(exc), "raw": line[:1500]}


def state(ws: dict) -> dict:
    con = ro(ws["ledger"] / "paper_3a.sqlite3")
    d = {"counts": {}, "positions": [], "pending": [], "processed": []}
    for t in ("paper_position", "paper_order", "paper_fill", "paper_pending",
              "paper_processed", "paper_blocked"):
        try:
            d["counts"][t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            d["counts"][t] = None
    cols = {r[1] for r in con.execute("PRAGMA table_info(paper_position)")}
    def c(n):
        return n if n in cols else f"NULL AS {n}"
    d["positions"] = [dict(r) for r in con.execute(
        "SELECT position_id,symbol,status,entry_ts,entry_price,voided,signal_uid,stop_price,"
        f"{c('reality_fill')},{c('read_at_ms')},{c('matched_at_ms')} FROM paper_position "
        "ORDER BY position_id")]
    d["pending"] = [dict(r) for r in con.execute(
        "SELECT pending_id,symbol,status,reason,signal_uid FROM paper_pending ORDER BY pending_id")]
    d["processed"] = [dict(r) for r in con.execute(
        "SELECT signal_uid,arm,action FROM paper_processed ORDER BY signal_uid")]
    con.close()
    return d


def main() -> int:
    cases = []

    def add(name, expect, actual, ok, extra=None):
        cases.append({"case": name, "expect": expect, "actual": actual,
                      "pass": bool(ok), "extra": extra})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: expect={expect} actual={actual}", flush=True)

    t = now_ms()

    # ── R1 重启后不丢账 ────────────────────────────────────────────────────────
    ws = build_ws("r1_restart")
    before = state(ws)
    res = run_case(ws, {"name": "R1", "action": "advance_pending",
                        "attempt_ms": t, "process_start_ms": t})
    after = state(ws)
    same = (before["counts"] == after["counts"]
            and before["positions"] == after["positions"]
            and before["pending"] == after["pending"]
            and before["processed"] == after["processed"])
    add("R1_restart_keeps_everything", "四表行数与内容逐项不变",
        "same" if same else "DIFF", same,
        {"counts_before": before["counts"], "counts_after": after["counts"],
         "advance_result": (res.get("result") or {})})

    # ── R2 重启后不重复下单 ────────────────────────────────────────────────────
    ws2 = build_ws("r2_duplicate")
    st0 = state(ws2)
    res2 = run_case(ws2, {
        "name": "R2", "action": "open", "symbol": "BTCUSDT",
        "uid": "test_probe_succ", "event_time": t - 30 * 60_000,
        "available_at": t - 30 * 60_000, "origin": "test",
        "atr": 500.0, "stop_price": None, "stoploss_s": 0.05, "trail_d": 0.05,
        "config_fingerprint": "82dec362c182", "model_version": "FT-LOW4H-EXP-V2.2",
        "attempt_ms": t,
    }, mode="realtime_only")
    st1 = state(ws2)
    r2 = (res2.get("result") or {})
    add("R2_duplicate_signal_uid", "duplicate_signal_uid",
        r2.get("reason"), r2.get("reason") == "duplicate_signal_uid",
        {"positions_before": st0["counts"]["paper_position"],
         "positions_after": st1["counts"]["paper_position"]})
    add("R2_no_new_position", "持仓数不变",
        st1["counts"]["paper_position"],
        st1["counts"]["paper_position"] == st0["counts"]["paper_position"])

    # ── R3 超过有效期不补成交 ──────────────────────────────────────────────────
    # legacy 模式：只读闭 bar。信号时刻取 3 小时前 ⇒ earliest_fill 距今远超 1 小时容忍窗。
    ws3 = build_ws("r3_expired")
    ev = t - 3 * H1
    res3 = run_case(ws3, {
        "name": "R3", "action": "open", "symbol": "ETHUSDT",
        "uid": f"mainline01_r3_{t}", "event_time": ev, "available_at": ev,
        "origin": "test", "atr": 500.0, "stop_price": None,
        "stoploss_s": 0.05, "trail_d": 0.05,
        "config_fingerprint": "82dec362c182", "model_version": "FT-LOW4H-EXP-V2.2",
        "attempt_ms": t,
    }, mode="legacy")
    r3 = (res3.get("result") or {})
    add("R3_expired_no_backfill", "missed_fill_moment_no_backfill",
        r3.get("reason"), r3.get("reason") == "missed_fill_moment_no_backfill",
        {"event_time": iso(ev), "lag_hours": round((t - ev) / H1, 2),
         "lag_ms": r3.get("lag_ms")})

    # ── R4 停机期间执行点不追补 ────────────────────────────────────────────────
    # legacy + process_start_ms = now：应成交 bar **已收盘**（收盘早于本进程启动）
    # ⇒ 拒单，不用旧价回填。
    # 时序必须算准（这里踩过一次）：event_time 取 60 分钟前 ⇒ 执行点 = 45 分钟前
    # ⇒ 对齐后那根 bar 在 30 分钟前收盘（早于 process_start），而滞后 41 分钟仍在
    # 1 小时容忍窗内 ⇒ 只会命中「停机不追补」，不会先命中「错过时刻」。
    ws4 = build_ws("r4_process_start")
    ev4 = t - 60 * 60_000
    res4 = run_case(ws4, {
        "name": "R4", "action": "open", "symbol": "ETHUSDT",
        "uid": f"mainline01_r4_{t}", "event_time": ev4, "available_at": ev4,
        "origin": "test", "atr": 500.0, "stop_price": None,
        "stoploss_s": 0.05, "trail_d": 0.05,
        "config_fingerprint": "82dec362c182", "model_version": "FT-LOW4H-EXP-V2.2",
        "attempt_ms": t, "process_start_ms": t,
    }, mode="legacy")
    r4 = (res4.get("result") or {})
    add("R4_process_start_no_backfill", "fill_point_closed_before_process_start",
        r4.get("reason"), r4.get("reason") == "fill_point_closed_before_process_start",
        {"event_time": iso(ev4), "bar_close_time": iso(r4.get("bar_close_time")),
         "process_start_ms": t,
         "bar_close_before_start": (r4.get("bar_close_time") is not None
                                    and int(r4["bar_close_time"]) < t)})

    rec = {"generated_utc": dt.datetime.now(UTC).isoformat(),
           "cases": cases,
           "verdict": "PASS" if all(c["pass"] for c in cases) else "FAIL",
           "pass_n": sum(1 for c in cases if c["pass"]), "total_n": len(cases)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "11_restart_idempotent.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"verdict": rec["verdict"], "pass_n": rec["pass_n"],
                      "total_n": rec["total_n"]}, ensure_ascii=False))
    return 0 if rec["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

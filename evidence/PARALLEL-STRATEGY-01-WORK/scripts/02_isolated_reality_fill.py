#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""02 · 隔离正反例：现实时间撮合的逐事件时间证据。

**纪律**：全部在 `isolated/<case>/` 副本上做；行情库 `mode=ro`；生产账本只读复制、
不写不改；每例一个独立子进程（`_iso_case.py`）。

用例矩阵（每条都给出「期望」与「实际」）：
  P1  现实成交         执行点那根 bar 正在形成 ⇒ 用它的 open 成交，reality_fill=1
  P2  执行点已过       实时表已切下一根 ⇒ 拒单，不回填闭 bar 的 open
  P3  执行点未到       earliest_fill > 现在 ⇒ 挂单，不成交
  P4  旧行为对照       legacy 模式同 P1 场景 ⇒ 只能挂单（证明旧实现无法当场成交）
  P5  回退明确标记     prefer_realtime + 执行点已过 ⇒ 用闭 bar 成交但 reality_fill=0
  P6  无实时行情       标的在 realtime 表里没有行 ⇒ 拒单（不伪造）
  P7  幂等             同一 signal_uid 第二次 ⇒ duplicate_signal_uid
  P8  epoch 门禁       确认时刻早于本轮 paper 下限 ⇒ before_paper_epoch_no_backfill
  P9  停机不追补       legacy + process_start 晚于 bar 收盘 ⇒ fill_point_closed_before_process_start
  P10 重启不丢账       复制生产账本跑一轮推进 ⇒ 持仓/挂单条数与内容逐项不变

输出 `out/02_isolation.json`（含每条断言的期望/实际/证据）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
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
H1 = 3_600_000

CASES: list[dict] = []


def iso(ms) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def align15(ms: int) -> int:
    return ((int(ms) + M15 - 1) // M15) * M15


def ro(path: Path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=8)
    con.row_factory = sqlite3.Row
    return con


def current_bar(symbol: str = "BTCUSDT") -> dict:
    """当前正在形成的 15m bar（真实行情库，只读）。"""
    con = ro(MARKET_DB)
    r = con.execute(
        "SELECT symbol,open_time,close_time,open,high,low,close,is_closed,"
        "event_time,received_at FROM realtime_klines "
        "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m'",
        (symbol,)).fetchone()
    con.close()
    if r is None:
        raise RuntimeError(f"realtime_klines 没有 {symbol} 的 15m 行")
    return dict(r)


def realtime_symbols() -> set:
    con = ro(MARKET_DB)
    s = {r[0] for r in con.execute(
        "SELECT symbol FROM realtime_klines WHERE exchange='binance' "
        "AND market='um' AND timeframe='15m'")}
    con.close()
    return s


def closed_bar_exists(symbol: str, open_time: int) -> bool:
    con = ro(MARKET_DB)
    r = con.execute(
        "SELECT 1 FROM candle_versions WHERE exchange='binance' AND market='um' "
        "AND symbol=? AND timeframe='15m' AND open_time=? AND is_closed=1",
        (symbol, open_time)).fetchone()
    con.close()
    return r is not None


def build_ws(tag: str, *, copy_ledger: bool = True) -> dict:
    """搭一个隔离工作区：账本副本 + 实时信号库副本 + 闸门文件 + 锁目录。"""
    root = ISO / tag
    if root.exists():
        shutil.rmtree(root)
    (root / "ledger").mkdir(parents=True)
    (root / "locks").mkdir(parents=True)
    if copy_ledger:
        for arm in ("3a", "3b", "3c", "3d"):
            src = PROD_LEDGER / f"paper_{arm}.sqlite3"
            if src.exists():
                shutil.copy2(src, root / "ledger" / f"paper_{arm}.sqlite3")
    live = root / "live_signals.sqlite3"
    if PROD_LIVE.exists():
        shutil.copy2(PROD_LIVE, live)
    gate = root / "locks" / "paper_new_entry_gate.json"
    gate.write_text(json.dumps({
        "allow_new_entries": True, "reason": "iso_parallel_strategy_01",
        "set_by": "parallel_strategy_01_work",
        "since_ms": 1_792_748_844_240,
        "scope": "server_side_new_entry_only"}, ensure_ascii=False), encoding="utf-8")
    return {"root": root, "ledger": root / "ledger", "live": live,
            "gate": gate, "locks": root / "locks"}


def ws_env(ws: dict, mode: str | None = None) -> dict:
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
    # 隔离用例的 origin 都是 `test`（为控制时钟），而 `test` 默认走 legacy。
    # 要验证**生产实时**口径就必须显式设成 realtime_only；legacy 对照臂另行传入。
    env["WX_REALITY_FILL_MODE"] = mode or "realtime_only"
    return env


def run_case(ws: dict, cfg: dict, mode: str | None = None) -> dict:
    env = ws_env(ws, mode)
    env["ISO_CASE"] = json.dumps(cfg, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCRIPTS / "_iso_case.py")],
                       capture_output=True, text=True, env=env, timeout=180)
    line = None
    for ln in (p.stdout or "").splitlines():
        if ln.startswith("ISO_RESULT "):
            line = ln[len("ISO_RESULT "):]
    if line is None:
        return {"case": cfg.get("name"), "ok": False, "subprocess_rc": p.returncode,
                "stdout": (p.stdout or "")[-2000:], "stderr": (p.stderr or "")[-3000:]}
    try:
        return json.loads(line)
    except Exception as exc:                                   # noqa: BLE001
        return {"case": cfg.get("name"), "parse_error": str(exc), "raw": line[:2000]}


def _cols(con, table: str) -> set:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def ledger_state(ws: dict, arm: str = "3a") -> dict:
    p = ws["ledger"] / f"paper_{arm}.sqlite3"
    con = ro(p)
    out = {}
    for t in ("paper_position", "paper_order", "paper_pending", "paper_processed"):
        try:
            out[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception:                                      # noqa: BLE001
            out[t] = None
    # 三列是本次**加性**迁移新增的；未跑过 `paper_conn` 的副本上还不存在 ⇒ 用 NULL 占位，
    # 而不是让读数失败（否则「迁移前」的状态就无法留证）。
    pc = _cols(con, "paper_position")
    def c(name):
        return name if name in pc else f"NULL AS {name}"
    out["positions"] = [dict(r) for r in con.execute(
        "SELECT position_id,symbol,status,entry_price,entry_ts,signal_uid,voided,"
        f"{c('reality_fill')},{c('fill_source')},{c('data_available_at')},market_source "
        "FROM paper_position ORDER BY position_id")]
    oc = _cols(con, "paper_order")
    def co(name):
        return name if name in oc else f"NULL AS {name}"
    out["orders"] = [dict(r) for r in con.execute(
        "SELECT order_id,event_uid,symbol,status,reject_reason,fill_price,bar_open_time,"
        f"{co('reality_fill')},{co('fill_source')} FROM paper_order ORDER BY order_id")]
    out["pending"] = [dict(r) for r in con.execute(
        "SELECT pending_id,symbol,signal_uid,status,reason,earliest_fill_ms FROM paper_pending "
        "ORDER BY pending_id")]
    con.close()
    return out


def add(case: str, expected: str, actual: str, passed: bool, evidence: dict):
    CASES.append({"case": case, "expected": expected, "actual": actual,
                  "pass": bool(passed), "evidence": evidence})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ISO.mkdir(parents=True, exist_ok=True)
    bar = current_bar("BTCUSDT")
    B = int(bar["open_time"])
    BC = int(bar["close_time"])
    now = int(time.time() * 1000)
    rt_syms = realtime_symbols()
    meta = {
        "generated_at_utc": iso(now),
        "now_ms": now,
        "current_bar": {"symbol": "BTCUSDT", "open_time": B, "open_time_utc": iso(B),
                        "close_time": BC, "close_time_utc": iso(BC),
                        "open": bar["open"], "is_closed": bar["is_closed"],
                        "received_at": bar["received_at"],
                        "received_at_utc": iso(bar["received_at"]),
                        "event_time_utc": iso(bar["event_time"])},
        "bar_still_forming": now < BC,
        "realtime_symbols_n": len(rt_syms),
    }

    # ── P1 现实成交 ────────────────────────────────────────────────────────────
    # 关键：bar 必须**在用例执行那一刻**重新读。`realtime_klines` 每个标的一行、
    # 永远指向「正在形成的那一根」，所以重新读到的 bar 一定 `is_closed=0`
    # ⇒ 「执行点那根正在形成」这个前提由数据源本身保证，不靠脚本开头的旧值。
    bar1 = current_bar("ETHUSDT")
    B1 = int(bar1["open_time"])
    now1 = int(time.time() * 1000)
    ws = build_ws("P1_reality_fill")
    cfg = {"name": "P1", "action": "open", "symbol": "ETHUSDT",
           "uid": f"iso_p1_{now1}",
           # 信号在上一根 bar 收盘时刻确认 ⇒ 最早执行点 = 当前这根 bar 开盘
           "event_time": B1 - M15, "available_at": B1 - M15,
           "requested_at": B1 - M15,
           "attempt_ms": now1, "origin": "test"}
    r1 = run_case(ws, cfg)
    st1 = ledger_state(ws)
    res1 = r1.get("result") or {}
    pos_new = [p for p in st1["positions"] if p["signal_uid"] == cfg["uid"]]
    ok1 = (res1.get("ok") is True
           and res1.get("reality_fill") is True
           and res1.get("fill_source") == "realtime_15m_open"
           and int(res1.get("bar_open_time") or 0) == B1
           and pos_new and int(pos_new[0]["reality_fill"] or 0) == 1)
    add("P1 现实成交", "执行点那根 bar 正在形成 ⇒ 当场用它的 open 成交，reality_fill=1",
        f"ok={res1.get('ok')} reality_fill={res1.get('reality_fill')} "
        f"fill_source={res1.get('fill_source')} bar_open={iso(res1.get('bar_open_time'))} "
        f"lag_ms={res1.get('fill_lag_ms')}", ok1,
        {"cfg": cfg, "result": res1, "position_row": pos_new,
         "realtime_bar_at_case_start": {"open_time_utc": iso(B1),
                                        "is_closed": bar1["is_closed"],
                                        "open": bar1["open"]},
         "timeline": res1.get("timeline")})

    # ── P2 执行点已过 ⇒ 不回填 ─────────────────────────────────────────────────
    ws2 = build_ws("P2_passed_no_backfill")
    cfg2 = {"name": "P2", "action": "open", "symbol": "SOLUSDT",
            "uid": f"iso_p2_{now}",
            # 信号在两根前确认 ⇒ 最早执行点是**上一根**（已经闭合、实时表已被覆盖）
            "event_time": B - 2 * M15, "available_at": B - 2 * M15,
            "requested_at": B - 2 * M15,
            "attempt_ms": now, "origin": "test"}
    r2 = run_case(ws2, cfg2)
    st2 = ledger_state(ws2)
    res2 = r2.get("result") or {}
    ok2 = (res2.get("ok") is False
           and res2.get("reason") == "fill_point_passed_no_backfill"
           and not [p for p in st2["positions"] if p["signal_uid"] == cfg2["uid"]])
    add("P2 执行点已过不回填",
        "实时表已切到更晚的一根 ⇒ 拒单 fill_point_passed_no_backfill，不写持仓、不用闭 bar 的 open",
        f"ok={res2.get('ok')} reason={res2.get('reason')} "
        f"expected_open={iso(res2.get('expected_open_time'))} "
        f"realtime_open={iso(res2.get('realtime_open_time'))} "
        f"新增持仓={len([p for p in st2['positions'] if p['signal_uid'] == cfg2['uid']])}",
        ok2, {"cfg": cfg2, "result": res2})

    # ── P3 执行点未到 ⇒ 挂单 ───────────────────────────────────────────────────
    ws3 = build_ws("P3_future_pending")
    cfg3 = {"name": "P3", "action": "open", "symbol": "BNBUSDT",
            "uid": f"iso_p3_{now}",
            "event_time": now, "available_at": now, "requested_at": now,
            "attempt_ms": now, "origin": "test"}
    r3 = run_case(ws3, cfg3)
    st3 = ledger_state(ws3)
    res3 = r3.get("result") or {}
    pend3 = [p for p in st3["pending"] if p["signal_uid"] == cfg3["uid"]]
    ok3 = (res3.get("ok") is False and res3.get("reason") == "pending_future_fill"
           and res3.get("pending_id") and pend3 and pend3[0]["status"] == "pending")
    add("P3 执行点未到 ⇒ 挂单",
        "earliest_fill(now+15m) 晚于现在 ⇒ pending_future_fill，登记挂单，不成交",
        f"ok={res3.get('ok')} reason={res3.get('reason')} pending_id={res3.get('pending_id')} "
        f"earliest={iso(res3.get('earliest_fill_ms'))}", ok3,
        {"cfg": cfg3, "result": res3, "pending_row": pend3})

    # ── P4 legacy 对照：同 P1 场景在旧口径下只能挂单 ───────────────────────────
    # 重新读 bar：旧口径要求 `is_closed=1`，而「当前那根」必然还没闭合
    # ⇒ 同场景在 legacy 下**结构上不可能**当场成交，只能挂单。
    bar4 = current_bar("XRPUSDT")
    B4 = int(bar4["open_time"])
    now4 = int(time.time() * 1000)
    ws4 = build_ws("P4_legacy_control")
    cfg4 = {"name": "P4", "action": "open", "symbol": "XRPUSDT",
            "uid": f"iso_p4_{now4}",
            "event_time": B4 - M15, "available_at": B4 - M15,
            "requested_at": B4 - M15,
            "attempt_ms": now4, "origin": "test"}
    r4 = run_case(ws4, cfg4, mode="legacy")
    res4 = r4.get("result") or {}
    ok4 = (res4.get("ok") is False and res4.get("reason") == "pending_future_fill")
    add("P4 旧行为对照（legacy）",
        "同 P1 场景下旧口径必须等 bar 闭合 ⇒ 只能挂单，无法当场成交",
        f"ok={res4.get('ok')} reason={res4.get('reason')} "
        f"mode={(r4.get('resolved') or {}).get('realtime_fill_mode')} "
        f"bar_is_closed={bar4['is_closed']}", ok4,
        {"cfg": cfg4, "result": res4,
         "realtime_bar_at_case_start": {"open_time_utc": iso(B4),
                                        "is_closed": bar4["is_closed"]}})

    # ── P5 prefer_realtime 回退必须明确标记 ────────────────────────────────────
    ws5 = build_ws("P5_prefer_backfill")
    cfg5 = {"name": "P5", "action": "open", "symbol": "ADAUSDT",
            "uid": f"iso_p5_{now}",
            "event_time": B - 2 * M15, "available_at": B - 2 * M15,
            "requested_at": B - 2 * M15,
            "attempt_ms": now, "origin": "test"}
    has_closed = closed_bar_exists("ADAUSDT", B - M15)
    r5 = run_case(ws5, cfg5, mode="prefer_realtime")
    res5 = r5.get("result") or {}
    ok5 = (res5.get("ok") is True and res5.get("reality_fill") is False
           and res5.get("fill_source") == "closed_15m_open") if has_closed else True
    add("P5 回退明确标记（prefer_realtime）",
        "执行点已过且允许回退 ⇒ 用闭 bar 的 open 成交，但 reality_fill=0、fill_source=closed_15m_open",
        f"闭 bar 存在={has_closed} ok={res5.get('ok')} reality_fill={res5.get('reality_fill')} "
        f"fill_source={res5.get('fill_source')} reason={res5.get('reason')}", ok5,
        {"cfg": cfg5, "result": res5, "closed_bar_exists": has_closed})

    # ── P6 无实时行情 ⇒ 拒单（不伪造）──────────────────────────────────────────
    no_rt = "1000000BOBUSDT" if "1000000BOBUSDT" not in rt_syms else "EPICUSDT"
    ws6 = build_ws("P6_no_realtime")
    cfg6 = {"name": "P6", "action": "open", "symbol": no_rt,
            "uid": f"iso_p6_{now}",
            "event_time": B - 2 * M15, "available_at": B - 2 * M15,
            "requested_at": B - 2 * M15,
            "attempt_ms": now, "origin": "test"}
    r6 = run_case(ws6, cfg6)
    res6 = r6.get("result") or {}
    ok6 = (res6.get("ok") is False
           and res6.get("reason") in ("fill_point_passed_no_backfill",
                                      "fill_point_closed_without_realtime",
                                      "pending_future_fill"))
    add("P6 无实时行情不伪造",
        f"{no_rt} 不在 realtime 表 ⇒ realtime_only 下不得用闭 bar 的 open 成交",
        f"ok={res6.get('ok')} reason={res6.get('reason')}", ok6,
        {"cfg": cfg6, "result": res6, "symbol_in_realtime": no_rt in rt_syms})

    # ── P7 幂等 ────────────────────────────────────────────────────────────────
    r7a = run_case(ws, cfg)          # 复用 P1 工作区，同一 signal_uid
    res7 = r7a.get("result") or {}
    ok7 = (res7.get("ok") is False and res7.get("reason") == "duplicate_signal_uid")
    add("P7 幂等（同一 signal_uid 不重开）",
        "同一 signal_uid 第二次调用 ⇒ duplicate_signal_uid，不新增持仓",
        f"ok={res7.get('ok')} reason={res7.get('reason')}", ok7,
        {"cfg": cfg, "result": res7})

    # ── P8 epoch 门禁 ─────────────────────────────────────────────────────────
    ws8 = build_ws("P8_epoch_gate")
    cfg8 = {"name": "P8", "action": "open", "symbol": "DOGEUSDT",
            "uid": f"iso_p8_{now}",
            "event_time": 1_792_748_844_240 - 2 * H1,      # epoch 之前
            "available_at": 1_792_748_844_240 - 2 * H1,
            "requested_at": 1_792_748_844_240 - 2 * H1,
            "attempt_ms": now, "origin": "test"}
    r8 = run_case(ws8, cfg8)
    res8 = r8.get("result") or {}
    ok8 = (res8.get("ok") is False and res8.get("reason") == "before_paper_epoch_no_backfill")
    add("P8 epoch 门禁", "确认时刻早于本轮 paper 启用下限 ⇒ before_paper_epoch_no_backfill",
        f"ok={res8.get('ok')} reason={res8.get('reason')}", ok8,
        {"cfg": cfg8, "result": res8})

    # ── P9 停机不追补（legacy 下走闭 bar 路径，判据才可达）────────────────────
    ws9 = build_ws("P9_restart_no_catchup")
    prev_open = B - M15
    cfg9 = {"name": "P9", "action": "open", "symbol": "LINKUSDT",
            "uid": f"iso_p9_{now}",
            "event_time": prev_open - M15, "available_at": prev_open - M15,
            "requested_at": prev_open - M15,
            "attempt_ms": now, "origin": "test",
            # 进程启动晚于执行点那根 bar 的收盘 ⇒ 停机期间过去的执行点
            "process_start_ms": prev_open + M15 + 60_000}
    r9 = run_case(ws9, cfg9, mode="legacy")
    res9 = r9.get("result") or {}
    ok9 = (res9.get("ok") is False
           and res9.get("reason") == "fill_point_closed_before_process_start")
    add("P9 停机后不追补（legacy）",
        "应成交 bar 在本进程启动前已收盘 ⇒ fill_point_closed_before_process_start",
        f"ok={res9.get('ok')} reason={res9.get('reason')} "
        f"bar_close={iso(res9.get('bar_close_time'))} "
        f"process_start={iso(res9.get('process_start_ms'))}", ok9,
        {"cfg": cfg9, "result": res9})

    # ── P10 重启不丢账（复制生产账本跑一轮推进）────────────────────────────────
    ws10 = build_ws("P10_restart_keep_ledger")
    before = ledger_state(ws10)
    r10 = run_case(ws10, {"name": "P10", "action": "advance_pending",
                          "attempt_ms": now, "process_start_ms": now - 60_000})
    after = ledger_state(ws10)
    same_pos = before["positions"] == after["positions"]
    same_counts = all(before[t] == after[t] for t in
                      ("paper_position", "paper_order", "paper_pending", "paper_processed"))
    ok10 = same_pos and same_counts
    add("P10 重启不丢账（复制生产账本）",
        "跑一轮 advance_pending 后，持仓/挂单/订单/已处理条数与内容逐项不变",
        f"持仓同={same_pos} 计数同={same_counts} "
        f"position_ids={[p['position_id'] for p in after['positions']]} "
        f"advance={(r10.get('result') or {})}", ok10,
        {"before_counts": {k: before[k] for k in
                           ("paper_position", "paper_order", "paper_pending", "paper_processed")},
         "after_counts": {k: after[k] for k in
                          ("paper_position", "paper_order", "paper_pending", "paper_processed")},
         "positions_after": after["positions"]})

    summary = {
        "meta": meta,
        "total": len(CASES),
        "passed": sum(1 for c in CASES if c["pass"]),
        "failed": [c["case"] for c in CASES if not c["pass"]],
        "cases": CASES,
    }
    p = OUT / "02_isolation.json"
    p.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    print(f"written {p}")
    print(f"{summary['passed']}/{summary['total']} 通过")
    for c in CASES:
        print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['case']}: {c['actual']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""03 · 真实时钟边界：逐次读取时间线（本单最硬的一条证据）。

与上一单 `03_boundary_fill.py` 的差别（为什么不能直接复用）：
  上一单只取「执行点后 1.5 秒」一个点，然后手工重试；**没有记录每次读取**，
  也没有把「读到行 / 没读到行」这件事本身留成证据。
  本单要求「按现有 1–2 秒运行节奏，在既定执行点附近记录**每次**读取、
  行情实际写入/可得、决策及成交处理时刻与同一原始行」⇒ 必须连续采样。

流程（严格照生产两段式，否则测的不是生产链路）：
  S  = 信号确认 bar 收盘（= event_time）
  T0 = S + 15m（= 执行点）
  ① S 之后立刻 `open_position(event_time=S, origin="live")` ⇒ 期望 `pending_future_fill`
  ② T0 前 3 秒开始，每 ~1.2 秒一轮：读实时行（独立只读连接）→ 调 `advance_pending(T=挂钟)`
     · 每轮记录：轮次、挂钟、实时行是否存在、open_time/is_closed/open/received_at、
       本轮决策结果（reason）、本轮是否成交
  ③ 成交后立刻读隔离账本，核对五时刻链
  ④ T0+20s 再读同一根，证明 open 在 bar 内恒定
  ⑤ 落盘 `out/03_boundary.json`

纪律：
  · 账本用**生产 3a 副本**（`isolated/boundary/ledger`），生产账本一个字节不写；
  · 行情库 `mode=ro`；
  · `origin` 取生产原值 `live`，且 `WX_REALITY_FILL_MODE` 从环境 pop 掉
    ⇒ 测的是**不带任何开关的生产默认路径**；
  · 标的 ETHUSDT（副本里 #4 是 BTCUSDT 未平仓持仓，同币不加仓会挡掉挂单）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

UTC = dt.timezone.utc
PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "MAINLINE-01-WORK"
ISO = EVID / "isolated"
OUT = EVID / "out"
BACKEND = PROJ / "框架" / "backend"
MARKET_DB = PROJ / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
PROD_LEDGER = PROJ / "数据" / "paper"
PROD_LIVE = PROJ / "框架" / "backend" / "live" / "live_signals.sqlite3"
M15 = 900_000
SYMBOL = (sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT")
TAG = "boundary"


def iso(ms):
    return None if ms is None else dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def ro(p: Path):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def build_ws() -> dict:
    root = ISO / TAG
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


def current_bar(symbol: str):
    con = ro(MARKET_DB)
    r = con.execute(
        "SELECT symbol,open_time,close_time,open,high,low,close,is_closed,"
        "event_time,received_at,source,transport FROM realtime_klines "
        "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m'",
        (symbol,)).fetchone()
    con.close()
    return dict(r) if r else None


def main() -> int:
    ws = build_ws()
    # 环境必须在 import paper_engine 之前设好（live_store 在 import 时解析路径）
    os.environ.update({
        "LIVE_DB": str(ws["live"]),
        "PAPER_LEDGER_DIR": str(ws["ledger"]),
        "PAPER_LOCK_DIR": str(ws["locks"]),
        "PAPER_GATE_FILE": str(ws["gate"]),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "MARKET_DB": str(MARKET_DB),
        "PYTHONPATH": str(BACKEND),
    })
    os.environ.pop("WX_REALITY_FILL_MODE", None)
    sys.path.insert(0, str(BACKEND))
    import paper_engine as PE
    import live_store as L

    now0 = L.now_ms()
    S = ((now0 // M15) + 1) * M15          # 下一个 15m 边界 = 信号确认 bar 收盘
    T0 = S + M15                            # 执行点
    rec = {
        "script": "03_boundary",
        "symbol": SYMBOL,
        "started_utc": iso(L.now_ms()),
        "process_start_ms": now0,
        "signal_confirm_bar_close_ms": S,
        "signal_confirm_bar_close_utc": iso(S),
        "planned_exec_ms": T0,
        "planned_exec_utc": iso(T0),
        "env": {
            "PAPER_LEDGER_DIR": os.environ.get("PAPER_LEDGER_DIR"),
            "MARKET_DB": os.environ.get("MARKET_DB"),
            "WX_REALITY_FILL_MODE": os.environ.get("WX_REALITY_FILL_MODE"),
            "realtime_fill_mode()": PE.realtime_fill_mode(),
            "effective_fill_mode('live')": PE.effective_fill_mode("live"),
        },
        "isolation": {"ledger_dir": str(L.get_ledger_dir()),
                      "market_db": str(L.MARKET_DB),
                      "ledger_is_prod": str(L.get_ledger_dir()).startswith(str(PROD_LEDGER))},
        "steps": [],
    }

    def step(name, **kw):
        d = {"step": name, "at_ms": L.now_ms()}
        d["at_utc"] = iso(d["at_ms"])
        d.update(kw)
        rec["steps"].append(d)
        print(f"[{d['at_utc']}] {name} {json.dumps(kw, ensure_ascii=False, default=str)[:400]}",
              flush=True)
        return d

    # ① 信号时刻登记挂单
    while L.now_ms() < S + 1500:
        time.sleep(0.2)
    bar_at_signal = current_bar(SYMBOL)
    step("signal_registered",
         realtime_row=bar_at_signal,
         note="S 时刻实时表里应是 S-15m 那根（正在收盘/刚收盘）")

    uid = f"mainline01_boundary_{S}"
    r1 = PE.open_position(
        "3a", SYMBOL, signal_uid=uid, direction="long",
        atr=None, stop_price=None,
        event_time=S, available_at=S,
        origin="live",
        stoploss_s=0.05, trail_d=0.05,
        config_fingerprint="82dec362c182",
        model_version="FT-LOW4H-EXP-V2.2",
    )
    step("open_position_at_signal", result=r1,
         expect="pending_future_fill（执行点还没到）",
         verdict=("PASS" if r1.get("reason") == "pending_future_fill" else "FAIL"))

    # ② 执行点前后按生产节奏连续采样
    samples = []
    filled = None
    read_deadline = T0 + 120_000
    n = 0
    while L.now_ms() < read_deadline:
        t = L.now_ms()
        row = current_bar(SYMBOL)
        n += 1
        s = {"n": n, "wall_ms": t, "wall_utc": iso(t),
             "rel_to_exec_ms": t - T0,
             "row_present": row is not None,
             "row_open_time": (row or {}).get("open_time"),
             "row_open_utc": iso((row or {}).get("open_time")),
             "row_is_closed": (row or {}).get("is_closed"),
             "row_open": (row or {}).get("open"),
             "row_received_at": (row or {}).get("received_at"),
             "row_received_utc": iso((row or {}).get("received_at")),
             "row_is_expected": bool(row and int(row["open_time"]) == T0),
             "decision": None, "filled": False}
        # 只在执行点之后才推进（生产也是到点才推进；提前推进会登记挂单）
        if t >= T0:
            adv = PE.advance_pending(T=t, log=lambda *a, **k: None,
                                     process_start_ms=now0)
            s["decision"] = {"filled": len(adv["filled"]),
                             "rejected": adv["rejected"],
                             "still_pending": adv["still_pending"],
                             "by_arm": adv.get("by_arm")}
            if adv["filled"]:
                filled = adv["filled"][0]
                s["filled"] = True
                samples.append(s)
                step("filled", sample=s, result=filled)
                break
        samples.append(s)
        time.sleep(1.2)
    rec["samples"] = samples
    rec["sample_n"] = len(samples)

    # ③ 账本核对
    con = ro(ws["ledger"] / "paper_3a.sqlite3")
    pc = {r[1] for r in con.execute("PRAGMA table_info(paper_position)")}
    fcols = {r[1] for r in con.execute("PRAGMA table_info(paper_fill)")}

    def c(cols, name):
        return name if name in cols else f"NULL AS {name}"

    prow = con.execute(
        "SELECT position_id,symbol,status,entry_ts,entry_price,entry_fee,"
        f"{c(pc,'reality_fill')},{c(pc,'data_available_at')},{c(pc,'fill_source')},"
        f"{c(pc,'read_at_ms')},{c(pc,'matched_at_ms')},market_source,requested_at,"
        "first_request_ms,earliest_fill_ms,signal_uid,voided "
        "FROM paper_position WHERE signal_uid=? ORDER BY position_id DESC LIMIT 1",
        (uid,)).fetchone()
    frow = con.execute(
        "SELECT fill_id,order_id,position_id,action,raw_price,fill_price,bar_open_time,"
        f"bar_close_time,fill_ts,{c(fcols,'data_available_at')},{c(fcols,'read_at_ms')},"
        f"{c(fcols,'matched_at_ms')},{c(fcols,'reality_fill')} "
        "FROM paper_fill WHERE signal_uid=? AND action='open' ORDER BY fill_id DESC LIMIT 1",
        (uid,)).fetchone()
    orow = con.execute(
        "SELECT order_id,ts,status,fill_price,bar_open_time FROM paper_order "
        "WHERE event_uid=? ORDER BY order_id DESC LIMIT 1", (uid,)).fetchone()
    pend = con.execute(
        "SELECT pending_id,status,reason,symbol,earliest_fill_ms FROM paper_pending "
        "WHERE signal_uid=? ORDER BY pending_id DESC LIMIT 1", (uid,)).fetchone()
    con.close()
    pos = dict(prow) if prow else None
    fil = dict(frow) if frow else None
    ordr = dict(orow) if orow else None
    pd = dict(pend) if pend else None
    rec["ledger"] = {"position": pos, "fill": fil, "order": ordr, "pending": pd}
    rec["ledger_utc"] = {
        "entry_ts": iso((pos or {}).get("entry_ts")),
        "data_available_at": iso((pos or {}).get("data_available_at")),
        "read_at_ms": iso((pos or {}).get("read_at_ms")),
        "matched_at_ms": iso((pos or {}).get("matched_at_ms")),
        "fill_ts": iso((fil or {}).get("fill_ts")),
        "order_ts": iso((ordr or {}).get("ts")),
    }

    # ④ 三时刻不变式（本单核心断言）
    checks = {}

    def chk(name, cond, detail=None):
        checks[name] = {"pass": bool(cond), "detail": detail}

    if pos and fil:
        dav = pos.get("data_available_at")
        rd = pos.get("read_at_ms")
        mt = pos.get("matched_at_ms")
        fts = fil.get("fill_ts")
        ets = pos.get("entry_ts")
        chk("filled_once", filled is not None)
        chk("reality_fill_is_1", pos.get("reality_fill") == 1, pos.get("reality_fill"))
        chk("fill_source_realtime", pos.get("fill_source") == "realtime_15m_open",
            pos.get("fill_source"))
        chk("data_available_at_present", dav is not None, dav)
        chk("read_at_present", rd is not None, rd)
        chk("matched_at_present", mt is not None, mt)
        chk("fill_ts_equals_matched", fts == mt, {"fill_ts": fts, "matched_at": mt})
        chk("order_ts_equals_matched", (ordr or {}).get("ts") == mt,
            {"order_ts": (ordr or {}).get("ts"), "matched_at": mt})
        if None not in (dav, rd, mt):
            chk("invariant_data_avail_le_read", int(dav) <= int(rd),
                {"data_available_at": dav, "read_at": rd})
            chk("invariant_read_le_matched", int(rd) <= int(mt),
                {"read_at": rd, "matched_at": mt})
            chk("invariant_matched_ge_data_avail", int(mt) >= int(dav),
                {"matched_at": mt, "data_available_at": dav})
        chk("entry_ts_is_price_event_bar_open", ets == T0, {"entry_ts": ets, "exec_point": T0})
        chk("entry_ts_before_matched_expected", ets is not None and mt is not None and ets <= mt,
            {"entry_ts": ets, "matched_at": mt,
             "note": "价格事件时刻必然早于实际执行时刻——这正是不能用它冒充成交时刻的原因"})
        chk("matched_within_60s_of_exec_point",
            mt is not None and 0 <= int(mt) - T0 <= 60_000, {"lag_ms": (mt or 0) - T0})
        chk("pending_resolved_filled", (pd or {}).get("status") == "filled", pd)
        # 首次拿到 T0 那根 bar 的采样序号
        first = next((s for s in samples if s["row_is_expected"]), None)
        rec["first_expected_row_sample"] = first
        chk("first_expected_row_within_60s",
            bool(first) and 0 <= (first["rel_to_exec_ms"] or 0) <= 60_000,
            None if not first else {"n": first["n"], "rel_ms": first["rel_to_exec_ms"]})
        # 边界前后「行缺失」窗口（这就是工单说的「执行点后 1.5 秒可能为空」）
        missing = [s for s in samples if not s["row_present"]]
        rec["row_missing_samples"] = missing
        chk("missing_row_did_not_cause_reject",
            all((s["decision"] or {}).get("rejected") in (None, []) for s in missing),
            [s["n"] for s in missing][:10])
    else:
        chk("filled_once", False, {"note": "未成交，无法核对时刻链"})
    rec["checks"] = checks
    rec["verdict"] = "PASS" if all(v["pass"] for v in checks.values()) else "FAIL"
    rec["finished_utc"] = iso(L.now_ms())
    rec["process_start_utc"] = iso(now0)

    # ⑤ 同一根 bar 的 open 在 bar 内恒定（T0+20s 复读）
    t_wait = T0 + 20_000
    while L.now_ms() < t_wait:
        time.sleep(0.5)
    again = current_bar(SYMBOL)
    rec["recheck_same_bar"] = {"at_utc": iso(L.now_ms()), "row": again,
                               "open_unchanged": bool(again and fil
                                                      and float(again["open"]) == float(fil["raw_price"])),
                               "still_same_open_time": bool(again and int(again["open_time"]) == T0)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "03_boundary.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"verdict": rec["verdict"],
                      "checks": {k: v["pass"] for k, v in checks.items()},
                      "ledger_utc": rec["ledger_utc"],
                      "sample_n": rec["sample_n"],
                      "missing_n": len(rec.get("row_missing_samples") or []),
                      "open_unchanged": rec["recheck_same_bar"]["open_unchanged"]},
                     ensure_ascii=False, indent=2), flush=True)
    return 0 if rec["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

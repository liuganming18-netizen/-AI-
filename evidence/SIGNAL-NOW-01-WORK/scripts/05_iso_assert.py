# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-WORK｜步骤5：隔离验证（生产零写入）。

覆盖工单「隔离验证」四组：
  G1 历史结构 + 当前扫描仍满足规则 ⇒ 正常响应；只有历史确认、当前条件失效 ⇒ 不补单
  G2 确认于 12:00 最早执行点 12:15；处理已错过的 bar 不得回填历史 entry 时间/价格
  G3 停机后旧执行点过期不追补；正常重启幂等、不重单、不丢既有挂单/持仓；
     `approved=false` 拒绝、未批准仓位倍数不影响基准风险
  G4 事件时间/确认/真实发现/订单登记/计划执行/实际撮合各字段语义与证据链可逐笔复算；
     无证据 `available_at` 不伪填

纪律：全部在 `iso/` 副本上做；生产库只读；行情库 `mode=ro`；每例独立子进程。
"""
from __future__ import annotations

import calendar
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-WORK"
ISO = ROOT / "iso"
BASE = ROOT / "baseline"
LOGS = ROOT / "logs"
OUT = ROOT / "out"
BACKEND = PROJ / "框架/backend"
MARKET_DB = PROJ / "数据/市场数据/db/market_v2.sqlite3"

FLOOR = 1_792_748_844_240        # 2026-10-23T09:47:24Z
H1 = 3_600_000
M15 = 900_000
# 选 2026-10-23T10:00:00Z 作为「12:00 确认」的替身（水位之后、行情库有完整 15m）
T_CONFIRM = 1_792_749_600_000    # 2026-10-23T10:00:00Z（水位后、行情库有完整 15m）
T_BAR = T_CONFIRM + M15          # 10-23T10:15:00Z（应成交 bar 开盘）
T_BAR_CLOSE = T_BAR + M15 - 1    # 10-23T10:29:59.999Z
T_NORMAL = T_BAR_CLOSE + 5_000   # 10-23T10:30:04.999Z（bar 收盘后 5 秒，正常处理）
T_LATE = T_BAR_CLOSE + 2 * H1    # 超过 MAX_FILL_LAG_MS ⇒ 必须拒
T_RESTART = T_BAR_CLOSE + 30 * 60_000  # 停机 30 分钟后重启（bar 已在停机期间收盘）

RESULTS: list[dict] = []


def iso(ms) -> str | None:
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(ms) / 1000)) + \
        f".{int(ms) % 1000:03d}Z"


def new_ws(tag: str, with_ledger: bool = False) -> dict:
    root = ISO / tag
    if root.exists():
        shutil.rmtree(root)
    (root / "ledger").mkdir(parents=True)
    (root / "locks").mkdir(parents=True)
    live = root / "live_signals.sqlite3"
    shutil.copy2(BASE / "live_signals_snapshot.sqlite3", live)
    if with_ledger:
        for arm in ("3a", "3b", "3c", "3d"):
            shutil.copy2(BASE / f"paper_{arm}_snapshot.sqlite3",
                         root / "ledger" / f"paper_{arm}.sqlite3")
    gate = root / "locks" / "paper_new_entry_gate.json"
    gate.write_text(json.dumps({
        "allow_new_entries": True, "reason": "iso_test_now01",
        "set_by": "signal_now_01", "since_ms": FLOOR,
        "scope": "server_side_new_entry_only"}, ensure_ascii=False), encoding="utf-8")
    return {"root": root, "live": live, "ledger": root / "ledger",
            "gate": gate, "locks": root / "locks"}


def ws_env(ws: dict, arm: str = "3a") -> dict:
    return {
        "LIVE_DB": str(ws["live"]),
        "PAPER_LEDGER_DIR": str(ws["ledger"]),
        "PAPER_LOCK_DIR": str(ws["locks"]),
        "PAPER_GATE_FILE": str(ws["gate"]),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "MARKET_DB": str(MARKET_DB),
        "TEST_ARM": arm,
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    }


def run_child(code: str, env_extra: dict, tag: str) -> dict:
    env = dict(os.environ)
    env.update(env_extra)
    env["BACKEND"] = str(BACKEND)
    env["PYTHONPATH"] = f"{PROJ / '框架'}:{BACKEND}:{PROJ}"
    p = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, timeout=900)
    (LOGS / f"{tag}.log").write_text(
        (p.stdout or "") + "\n---STDERR---\n" + (p.stderr or ""), encoding="utf-8")
    for line in (p.stdout or "").splitlines():
        if line.startswith("__JSON__"):
            return json.loads(line[len("__JSON__"):])
    return {"error": "child_no_json", "rc": p.returncode,
            "stderr_tail": (p.stderr or "")[-2000:]}


def check(group: str, name: str, ok: bool, detail) -> None:
    RESULTS.append({"group": group, "name": name, "pass": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {group} {name}")
    if not ok:
        print(f"         detail={json.dumps(detail, ensure_ascii=False, default=str)[:600]}")


# ══════════════════════════════════════════════════════════════════════════════
# 通用子进程：直接调 open_position（即时路径）
# ══════════════════════════════════════════════════════════════════════════════
OPEN_CHILD = r'''
import json, os, sys
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L, paper_engine as PE

kw = dict(signal_uid=os.environ["UID"], direction="long", atr=800.0,
          event_time=int(os.environ["EV"]),
          available_at=(int(os.environ["AVAIL"]) if os.environ.get("AVAIL") else None),
          now_ms=int(os.environ["NOW"]), requested_at=int(os.environ["REQ"]),
          origin=os.environ.get("ORIGIN", "replay"),
          epoch_ms=int(os.environ["FLOOR"]),
          stop_mode="uniform", entry_mode="three_buy_points")
if os.environ.get("PROC_START"):
    kw["process_start_ms"] = int(os.environ["PROC_START"])
if os.environ.get("PM"):
    kw["signal_payload"] = {"position_multiplier": float(os.environ["PM"]),
                            "position_multiplier_approved": os.environ.get("PM_OK") == "1"}
out = {"case": os.environ.get("CASE")}
try:
    out["res"] = PE.open_position("3a", "BTCUSDT", **kw)
except Exception as exc:
    out["exc"] = f"{type(exc).__name__}: {exc}"
con = L.paper_conn("3a")
try:
    out["pending"] = [dict(r) for r in con.execute(
        "SELECT pending_id,signal_uid,status,event_time,available_at,requested_at,"
        "first_request_ms,earliest_fill_ms,epoch_ms FROM paper_pending")]
    out["positions"] = [dict(r) for r in con.execute(
        "SELECT position_id,signal_uid,status,qty,entry_price,stop_price,entry_ts,"
        "planned_risk,initial_stop_price,available_at,event_time,earliest_fill_ms,"
        "first_request_ms,epoch_ms,market_source FROM paper_position")]
    out["fills"] = [dict(r) for r in con.execute(
        "SELECT fill_id,signal_uid,qty,raw_price,fill_price,notional,fee,slippage_cost,"
        "bar_open_time,bar_close_time,fill_ts,available_at,event_time,initial_stop_price,"
        "planned_risk FROM paper_fill")]
    out["orders"] = [dict(r) for r in con.execute(
        "SELECT order_id,event_uid,status,reject_reason,bar_open_time,ts FROM paper_order")]
finally:
    con.close()
print("__JSON__" + json.dumps(out, ensure_ascii=False, default=str))
'''

# 挂单登记 → 推进（用真实 advance_pending）
ADV_CHILD = r'''
import json, os, sys
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L, paper_engine as PE

uid = os.environ["UID"]
# 1) 先在「bar 尚不存在」的时刻登记挂单
reg = PE.open_position("3a", "BTCUSDT", signal_uid=uid, direction="long", atr=800.0,
                       event_time=int(os.environ["EV"]),
                       available_at=(int(os.environ["AVAIL"]) if os.environ.get("AVAIL") else None),
                       now_ms=int(os.environ["REG_NOW"]), requested_at=int(os.environ["REQ"]),
                       origin="replay", epoch_ms=int(os.environ["FLOOR"]),
                       stop_mode="uniform", entry_mode="three_buy_points")
out = {"case": os.environ.get("CASE"), "reg": reg}
# 2) 推进（可多次，验证幂等）
outs = []
n = int(os.environ.get("ADV_N", "1"))
for i in range(n):
    kw = {"T": int(os.environ["ADV_T"]), "log": lambda *a: None}
    if os.environ.get("PROC_START"):
        kw["process_start_ms"] = int(os.environ["PROC_START"])
    try:
        outs.append(PE.advance_pending(**kw))
    except Exception as exc:
        outs.append({"error": f"{type(exc).__name__}: {exc}"})
out["adv"] = outs
con = L.paper_conn("3a")
try:
    out["pending"] = [dict(r) for r in con.execute(
        "SELECT pending_id,signal_uid,status,reason,resolved_at FROM paper_pending")]
    out["positions"] = [dict(r) for r in con.execute(
        "SELECT position_id,signal_uid,status,qty,entry_price,stop_price,entry_ts,"
        "planned_risk,available_at FROM paper_position")]
    out["fills"] = [dict(r) for r in con.execute(
        "SELECT fill_id,signal_uid,fill_ts,bar_open_time,bar_close_time,raw_price,"
        "fill_price,available_at FROM paper_fill")]
finally:
    con.close()
print("__JSON__" + json.dumps(out, ensure_ascii=False, default=str))
'''

# 重启幂等（用生产账本快照副本）
RESTART_CHILD = r'''
import json, os, sys
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L, paper_engine as PE

con = L.paper_conn("3a")
try:
    before = {
        "pending": [dict(r) for r in con.execute(
            "SELECT pending_id,signal_uid,status,fill_position_id FROM paper_pending")],
        "positions": [dict(r) for r in con.execute(
            "SELECT position_id,signal_uid,status,qty,stop_price FROM paper_position")],
        "n_order": con.execute("SELECT COUNT(*) FROM paper_order").fetchone()[0],
        "n_fill": con.execute("SELECT COUNT(*) FROM paper_fill").fetchone()[0],
    }
finally:
    con.close()
outs = []
for i in range(2):
    outs.append(PE.advance_pending(T=int(os.environ["ADV_T"]), log=lambda *a: None,
                                   process_start_ms=int(os.environ["PROC_START"])))
con = L.paper_conn("3a")
try:
    after = {
        "pending": [dict(r) for r in con.execute(
            "SELECT pending_id,signal_uid,status,fill_position_id FROM paper_pending")],
        "positions": [dict(r) for r in con.execute(
            "SELECT position_id,signal_uid,status,qty,stop_price FROM paper_position")],
        "n_order": con.execute("SELECT COUNT(*) FROM paper_order").fetchone()[0],
        "n_fill": con.execute("SELECT COUNT(*) FROM paper_fill").fetchone()[0],
    }
finally:
    con.close()
print("__JSON__" + json.dumps({"before": before, "after": after, "adv": outs},
                              ensure_ascii=False, default=str))
'''

# 路由：approved=false 不进路由
ROUTE_CHILD = r'''
import json, os, sys, sqlite3
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L, unified_runtime as UR

# 在隔离副本里插一条 approved=false 的“当前”信号（origin=live，确认于水位后）
H1 = 3600000
uid = "iso_now01_unapproved"
con = sqlite3.connect(os.environ["LIVE_DB"])
try:
    payload = {"role": "enterable_arm_entry", "arm": "3a", "approved": False,
               "price": 81780.0, "breakout_price": 81800.0, "stop_loss": 77691.0,
               "struct_low": 77000.0, "stop_mode": "uniform",
               "entry_mode": "three_buy_points",
               "box_start_ms": int(os.environ["EV"]) - 48 * H1,
               "box_low_l": 77000.0, "box_high_h": 81800.0, "box_atr_a": 800.0,
               "raw_candidate": {"entry_ref_price": 81780.0, "resistance": 81800.0,
                                 "initial_stop_price": 77691.0, "breakout_close": 81780.0}}
    con.execute(
        "INSERT OR REPLACE INTO live_signal(signal_uid,strategy_key,exchange,market,timeframe,"
        "symbol,confirmed_at,available_at,origin,source,executable_state,executable_reason,"
        "payload_json,first_seen_at,updated_at,display_state,event_time,bar_open_time) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid, "FT-LOW4H-EXP-V2.2", "binance", "um", "1h", "BTCUSDT",
         int(os.environ["EV"]), int(os.environ["EV"]), "live", "iso_now01_construct",
         "enterable", "iso_constructed",
         json.dumps(payload, ensure_ascii=False), int(os.environ["EV"]),
         int(os.environ["EV"]), "displayable", int(os.environ["EV"]),
         int(os.environ["EV"]) - H1))
    con.commit()
finally:
    con.close()

router = UR.SignalPersistenceAndRouter(arm="3a")
r = router.route_pending_orders(int(os.environ["NOW"]), origin="live")
con = L.paper_conn("3a")
try:
    pend = [dict(x) for x in con.execute("SELECT pending_id,signal_uid,status FROM paper_pending")]
    pos = [dict(x) for x in con.execute("SELECT position_id,signal_uid,status FROM paper_position")]
finally:
    con.close()
print("__JSON__" + json.dumps({"route": r, "pending": pend, "positions": pos},
                              ensure_ascii=False, default=str))
'''



# 直接插入一条 pending 行再推进（库里历史 bar 一律 is_closed=1，
# 「挂单已登记、应成交 bar 已收盘」只能用这种方式构造）
PEND_CHILD = r'''
import json, os, sys
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L, paper_engine as PE

con = L.paper_conn("3a")
try:
    con.execute(
        "INSERT INTO paper_pending(account_id,arm,symbol,direction,signal_uid,origin,atr,"
        "stop_price,event_time,available_at,requested_at,earliest_fill_ms,status,reason,"
        "created_at,market_source,first_request_ms,epoch_ms,stoploss_s,trail_d,model_version,"
        "config_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending',NULL,?,?,?,?,?,?,?,?)",
        ("paper43_3a", "3a", "BTCUSDT", "long", os.environ["UID"], "signal", 800.0, None,
         int(os.environ["EV"]), None, int(os.environ["REQ"]), int(os.environ["EF"]),
         int(os.environ["REQ"]), "binance.um.candles_15m.is_closed.open",
         int(os.environ["REQ"]), int(os.environ["FLOOR"]), 0.05, 0.05,
         "FT-LOW4H-EXP-V2.2", "82dec362c182"))
    con.commit()
finally:
    con.close()

outs = []
for i in range(int(os.environ.get("ADV_N", "1"))):
    kw = {"T": int(os.environ["ADV_T"]), "log": lambda *a: None}
    if os.environ.get("PROC_START"):
        kw["process_start_ms"] = int(os.environ["PROC_START"])
    try:
        outs.append(PE.advance_pending(**kw))
    except Exception as exc:
        outs.append({"error": f"{type(exc).__name__}: {exc}"})
con = L.paper_conn("3a")
try:
    out = {"case": os.environ.get("CASE"), "adv": outs,
           "pending": [dict(r) for r in con.execute(
               "SELECT pending_id,signal_uid,status,reason,resolved_at FROM paper_pending")],
           "positions": [dict(r) for r in con.execute(
               "SELECT position_id,signal_uid,status,qty,entry_price,stop_price,entry_ts,"
               "planned_risk,available_at FROM paper_position")],
           "fills": [dict(r) for r in con.execute(
               "SELECT fill_id,signal_uid,fill_ts,bar_open_time,bar_close_time,raw_price,"
               "fill_price,available_at FROM paper_fill")]}
finally:
    con.close()
print("__JSON__" + json.dumps(out, ensure_ascii=False, default=str))
'''

def main() -> int:
    print("== G1 历史结构 + 当前确认 ==")
    # G1a 正例：确认在水位后、正常处理 ⇒ 成交
    ws = new_ws("g1a")
    r = run_child(OPEN_CHILD, {**ws_env(ws), "UID": "iso_now01_g1a", "EV": str(T_CONFIRM),
                               "NOW": str(T_NORMAL), "REQ": str(T_CONFIRM),
                               "FLOOR": str(FLOOR), "CASE": "g1a"}, "g1a")
    res = (r.get("res") or {})
    check("G1", "正例_确认于水位后+正常处理⇒成交", res.get("ok") is True,
          {"reason": res.get("reason"), "fill": res.get("fill_price"), "exc": r.get("exc")})
    # G1b 反例：只有历史确认（10-23T09:47:25Z），10-27 才请求 ⇒ 必须拒（旧信号不得续期）
    ws = new_ws("g1b")
    r = run_child(OPEN_CHILD, {**ws_env(ws), "UID": "iso_now01_g1b",
                               "EV": "1792748845240", "NOW": "1793118614728",
                               "REQ": "1793116007361", "FLOOR": str(FLOOR), "CASE": "g1b"},
                  "g1b")
    res = (r.get("res") or {})
    check("G1", "反例_4天前确认+今日请求⇒拒(不续期)",
          res.get("ok") is False and res.get("reason") == "missed_fill_moment_no_backfill",
          {"reason": res.get("reason"), "res": {k: res.get(k) for k in
                                                ("bar_open_time", "lag_ms", "note")}})
    # G1c 反例：确认早于水位 ⇒ before_paper_epoch_no_backfill
    ws = new_ws("g1c")
    r = run_child(OPEN_CHILD, {**ws_env(ws), "UID": "iso_now01_g1c",
                               "EV": str(FLOOR - 2 * H1), "NOW": str(T_NORMAL),
                               "REQ": str(FLOOR - 2 * H1), "FLOOR": str(FLOOR), "CASE": "g1c"},
                  "g1c")
    res = (r.get("res") or {})
    check("G1", "反例_确认早于水位⇒拒", res.get("reason") == "before_paper_epoch_no_backfill",
          {"reason": res.get("reason")})

    print("== G2 12:00 确认最早 12:15 / 不回填 ==")
    # G2a earliest_fill 精确 = 确认 + 15 分钟
    ws = new_ws("g2a")
    r = run_child(OPEN_CHILD, {**ws_env(ws), "UID": "iso_now01_g2a", "EV": str(T_CONFIRM),
                               "NOW": str(T_NORMAL), "REQ": str(T_CONFIRM),
                               "FLOOR": str(FLOOR), "CASE": "g2a"}, "g2a")
    fills = r.get("fills") or []
    pos = r.get("positions") or []
    ef = fills[0].get("bar_open_time") if fills else None
    check("G2", "确认+15min⇒应成交bar开盘", ef == T_BAR,
          {"bar_open_time": iso(ef), "expect": iso(T_BAR)})
    # G2b 应成交 bar 尚不存在（未来）⇒ 只登记挂单，不成交
    # 注：库里历史 bar 一律 is_closed=1，所以「bar 未收盘」只能用**真实当前时刻**构造
    now_real = int(time.time() * 1000)
    ws = new_ws("g2b")
    r = run_child(OPEN_CHILD, {**ws_env(ws), "UID": "iso_now01_g2b", "EV": str(now_real),
                               "NOW": str(now_real), "REQ": str(now_real),
                               "FLOOR": str(FLOOR), "CASE": "g2b"}, "g2b")
    res = (r.get("res") or {})
    check("G2", "应成交bar尚不存在⇒pending_future_fill(不提前成交)",
          res.get("reason") == "pending_future_fill" and not (r.get("fills") or []),
          {"reason": res.get("reason"), "fills": len(r.get("fills") or []),
           "earliest_fill_ms": res.get("earliest_fill_ms")})
    # G2c 正常处理：entry_ts = bar 开盘，fill_ts = 处理时刻，bar 已收盘
    ws = new_ws("g2c")
    r = run_child(OPEN_CHILD, {**ws_env(ws), "UID": "iso_now01_g2c", "EV": str(T_CONFIRM),
                               "NOW": str(T_NORMAL), "REQ": str(T_CONFIRM),
                               "FLOOR": str(FLOOR), "CASE": "g2c"}, "g2c")
    fills = r.get("fills") or []
    pos = r.get("positions") or []
    f = fills[0] if fills else {}
    p0 = pos[0] if pos else {}
    ok = (f.get("bar_close_time") == T_BAR_CLOSE and f.get("fill_ts") == T_NORMAL
          and p0.get("entry_ts") == T_BAR)
    check("G2", "闭bar后处理: entry_ts=bar开盘 / fill_ts=处理时刻 / 两者可分辨", ok,
          {"bar_open": iso(f.get("bar_open_time")), "bar_close": iso(f.get("bar_close_time")),
           "fill_ts": iso(f.get("fill_ts")), "entry_ts": iso(p0.get("entry_ts")),
           "lag_after_close_ms": (f.get("fill_ts") - f.get("bar_close_time"))
           if f.get("fill_ts") and f.get("bar_close_time") else None})
    # G2d 超过 MAX_FILL_LAG ⇒ 拒，不回填
    ws = new_ws("g2d")
    r = run_child(OPEN_CHILD, {**ws_env(ws), "UID": "iso_now01_g2d", "EV": str(T_CONFIRM),
                               "NOW": str(T_LATE), "REQ": str(T_CONFIRM),
                               "FLOOR": str(FLOOR), "CASE": "g2d"}, "g2d")
    res = (r.get("res") or {})
    check("G2", "迟到>1h⇒拒(不回填旧bar)",
          res.get("reason") == "missed_fill_moment_no_backfill"
          and not (r.get("positions") or []),
          {"reason": res.get("reason"), "lag_ms": res.get("lag_ms")})
    # G2e 可用时刻晚于事件时刻时，最早成交取可用时刻
    ws = new_ws("g2e")
    r = run_child(OPEN_CHILD, {**ws_env(ws), "UID": "iso_now01_g2e", "EV": str(T_CONFIRM),
                               "AVAIL": str(T_CONFIRM + 10 * 60_000),
                               "NOW": str(T_NORMAL), "REQ": str(T_CONFIRM),
                               "FLOOR": str(FLOOR), "CASE": "g2e"}, "g2e")
    fills = r.get("fills") or []
    ef = fills[0].get("bar_open_time") if fills else None
    check("G2", "available_at 晚 10min⇒应成交bar顺延到 10:30 那根", ef == T_BAR + M15,
          {"bar_open_time": iso(ef), "expect": iso(T_BAR + M15)})

    print("== G3 停机不追补 / 重启幂等 ==")
    # G3a 停机 30 分钟后重启 ⇒ 拒 fill_point_closed_before_process_start
    ws = new_ws("g3a")
    r = run_child(PEND_CHILD, {**ws_env(ws), "UID": "iso_now01_g3a", "EV": str(T_CONFIRM),
                               "REQ": str(T_CONFIRM), "EF": str(T_BAR),
                               "ADV_T": str(T_RESTART), "ADV_N": "1",
                               "PROC_START": str(T_RESTART), "FLOOR": str(FLOOR),
                               "CASE": "g3a"}, "g3a")
    pend = r.get("pending") or []
    adv = (r.get("adv") or [{}])[0]
    check("G3", "停机期间收盘的执行点⇒拒(不追补)",
          bool(pend) and pend[0].get("status") == "rejected"
          and pend[0].get("reason") == "fill_point_closed_before_process_start"
          and not (r.get("positions") or []),
          {"pending": pend, "adv": adv})
    # G3b 同一挂单，进程在 bar 收盘前就在跑 ⇒ 正常成交
    ws = new_ws("g3b")
    r = run_child(PEND_CHILD, {**ws_env(ws), "UID": "iso_now01_g3b", "EV": str(T_CONFIRM),
                               "REQ": str(T_CONFIRM), "EF": str(T_BAR),
                               "ADV_T": str(T_NORMAL), "ADV_N": "1",
                               "PROC_START": str(T_CONFIRM), "FLOOR": str(FLOOR),
                               "CASE": "g3b"}, "g3b")
    pend = r.get("pending") or []
    fills = r.get("fills") or []
    check("G3", "进程在bar收盘前已运行⇒正常成交",
          bool(pend) and pend[0].get("status") == "filled" and bool(r.get("positions"))
          and fills and fills[0].get("bar_close_time") == T_BAR_CLOSE,
          {"pending": pend, "positions": len(r.get("positions") or []),
           "fill_ts": iso(fills[0].get("fill_ts")) if fills else None,
           "bar_close": iso(fills[0].get("bar_close_time")) if fills else None})
    # G3c 幂等：连续推进两次，只成交一次
    ws = new_ws("g3c")
    r = run_child(PEND_CHILD, {**ws_env(ws), "UID": "iso_now01_g3c", "EV": str(T_CONFIRM),
                               "REQ": str(T_CONFIRM), "EF": str(T_BAR),
                               "ADV_T": str(T_NORMAL), "ADV_N": "2",
                               "PROC_START": str(T_CONFIRM), "FLOOR": str(FLOOR),
                               "CASE": "g3c"}, "g3c")
    advs = r.get("adv") or []
    n_fill = sum(len(a.get("filled") or []) for a in advs)
    check("G3", "连续推进两次⇒只成交一次(幂等)",
          n_fill == 1 and len(r.get("positions") or []) == 1,
          {"filled_total": n_fill, "positions": len(r.get("positions") or [])})
    # G3d 生产账本快照：既有挂单/持仓不丢、不重单
    ws = new_ws("g3d", with_ledger=True)
    r = run_child(RESTART_CHILD, {**ws_env(ws), "ADV_T": str(T_NORMAL),
                                  "PROC_START": "1793117541000",  # 2026-10-27T16:12:21Z
                                  }, "g3d")
    b, a = r.get("before") or {}, r.get("after") or {}
    advs = r.get("adv") or []
    probe_b = [p for p in (b.get("pending") or []) if p.get("signal_uid") == "test_probe_succ"]
    probe_a = [p for p in (a.get("pending") or []) if p.get("signal_uid") == "test_probe_succ"]
    pos4_b = [p for p in (b.get("positions") or []) if p.get("position_id") == 4]
    pos4_a = [p for p in (a.get("positions") or []) if p.get("position_id") == 4]
    check("G3", "生产快照: 既有挂单/持仓保留且不重单",
          b == a and bool(probe_b) and probe_a == probe_b and pos4_a == pos4_b
          and all(not (x.get("filled") or []) for x in advs),
          {"probe_before": probe_b, "probe_after": probe_a,
           "pos4_before": pos4_b, "pos4_after": pos4_a,
           "order_fill_counts": (b.get("n_order"), b.get("n_fill"),
                                 a.get("n_order"), a.get("n_fill"))})
    # G3e approved=false 不进路由
    ws = new_ws("g3e")
    r = run_child(ROUTE_CHILD, {**ws_env(ws), "EV": str(T_CONFIRM),
                                "NOW": str(T_CONFIRM + 5 * 60_000)}, "g3e")
    rt = r.get("route") or {}
    check("G3", "approved=false⇒不进路由(registered=0)",
          int(rt.get("registered") or 0) == 0 and not (r.get("pending") or []),
          {"route": {k: rt.get(k) for k in ("registered", "rejected", "processed",
                                            "waiting_gate", "errors")},
           "pending": r.get("pending")})
    # G3f 未批准倍数不改基准风险
    base_risk, pm_risk = None, None
    for tag, pm, ok_flag in (("g3f_base", None, None), ("g3f_pm", "0.5", "0")):
        ws = new_ws(tag)
        env = {**ws_env(ws), "UID": f"iso_now01_{tag}", "EV": str(T_CONFIRM),
               "NOW": str(T_NORMAL), "REQ": str(T_CONFIRM), "FLOOR": str(FLOOR),
               "CASE": tag}
        if pm:
            env.update({"PM": pm, "PM_OK": ok_flag})
        r = run_child(OPEN_CHILD, env, tag)
        pos = r.get("positions") or []
        risk = pos[0].get("planned_risk") if pos else None
        if tag == "g3f_base":
            base_risk = risk
        else:
            pm_risk = risk
    check("G3", "未批准0.5x⇒下单量等于基准(风险不变)",
          base_risk is not None and pm_risk is not None and abs(base_risk - pm_risk) < 1e-9,
          {"base_planned_risk": base_risk, "unapproved_pm_planned_risk": pm_risk})

    print("== G4 字段语义与证据链可复算 ==")
    ws = new_ws("g4")
    r = run_child(OPEN_CHILD, {**ws_env(ws), "UID": "iso_now01_g4", "EV": str(T_CONFIRM),
                               "NOW": str(T_NORMAL), "REQ": str(T_CONFIRM),
                               "FLOOR": str(FLOOR), "CASE": "g4"}, "g4")
    f = (r.get("fills") or [{}])[0]
    p0 = (r.get("positions") or [{}])[0]
    # 4a 价格链：fill = ceil(raw*(1+5bps), tick)；stop = floor(fill*0.95)
    raw, fill, stop = f.get("raw_price"), f.get("fill_price"), p0.get("stop_price")
    qty, notional, fee = f.get("qty"), f.get("notional"), f.get("fee")
    risk = p0.get("planned_risk")
    rec = {
        "fill_gt_raw_不利方向": (fill > raw) if (fill and raw) else None,
        "fill_within_5bps_plus_tick": (0 < fill - raw <= raw * 0.0005 + 0.1 + 1e-9)
        if (fill and raw) else None,
        "stop_eq_floor_fill_095": (0 <= fill * 0.95 - stop < 0.1 + 1e-9) if (stop and fill) else None,
        "notional_eq_qty_fill": (abs(notional - qty * fill) < 1e-6)
        if (notional and qty and fill) else None,
        "fee_eq_notional_5bps": (abs(fee - notional * 0.0005) < 1e-9)
        if (fee and notional) else None,
        "risk_eq_qty_times_stopdist": (abs(risk - qty * (fill - stop)) < 1e-6)
        if (risk and qty and fill and stop) else None,
    }
    check("G4", "成交价/止损/名义/手续费/计划风险可逐项复算",
          all(v is True for v in rec.values()), rec)
    # 4b available_at 无证据 ⇒ 不伪填（本用例 available_at 未提供）
    check("G4", "无 available_at 证据⇒成交/持仓仍为 NULL(不伪填)",
          f.get("available_at") is None and p0.get("available_at") is None,
          {"fill_available_at": f.get("available_at"),
           "position_available_at": p0.get("available_at")})
    # 4c 证据链四时刻齐全且有序：event_time < earliest_fill <= bar_open < bar_close < fill_ts
    order_ok = (f.get("event_time") is not None and f.get("bar_open_time") is not None
                and f.get("bar_close_time") is not None and f.get("fill_ts") is not None
                and f["event_time"] < f["bar_open_time"]
                and f["bar_close_time"] < f["fill_ts"]
                and p0.get("entry_ts") == f["bar_open_time"])
    check("G4", "四时刻语义有序: event<bar_open=entry_ts<bar_close<fill_ts", order_ok,
          {"event_time": iso(f.get("event_time")),
           "bar_open_time": iso(f.get("bar_open_time")),
           "entry_ts": iso(p0.get("entry_ts")),
           "bar_close_time": iso(f.get("bar_close_time")),
           "fill_ts": iso(f.get("fill_ts"))})
    # 4d 订单登记时刻 = 处理时刻（挂钟），不等于经济入场时刻
    orders = r.get("orders") or []
    ord_ok = bool(orders) and orders[0].get("ts") == T_NORMAL
    check("G4", "订单登记时刻=处理挂钟(≠entry_ts)", ord_ok,
          {"order_ts": iso(orders[0].get("ts")) if orders else None,
           "entry_ts": iso(p0.get("entry_ts"))})

    total = len(RESULTS)
    passed = sum(1 for x in RESULTS if x["pass"])
    rep = {"ticket": "SIGNAL-NOW-01-WORK", "step": "5 隔离验证",
           "generated_at_utc": iso(int(time.time() * 1000)),
           "env": {"FLOOR": FLOOR, "T_CONFIRM": T_CONFIRM, "T_BAR": T_BAR,
                   "T_BAR_CLOSE": T_BAR_CLOSE, "T_NORMAL": T_NORMAL, "T_LATE": T_LATE,
                   "T_RESTART": T_RESTART},
           "total": total, "passed": passed, "failed": total - passed,
           "results": RESULTS}
    (OUT / "05_隔离断言.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n合计 {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

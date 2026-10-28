# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-02-WORK｜隔离断言（重载前预检，生产零写入）。

工单第 5 节要求逐项验证的 8 类，本脚本覆盖 02 相对 01 **新增**的部分：

  A 组（#3）确认时刻恰在边界前 / 等于边界 / 边界后 —— 闭区间口径
  B 组（#2）结构形成于水位前、确认于水位后 ⇒ 必须能正常响应（正例 + 反例）
  C 组（#5）重启沿用经核定水位：三水位不被改写；路由重复调用不重复、不漏
  D 组（#6）既有挂单 / 持仓在「重启」（重开连接 + 推进）前后保持并继续管理
  E 组（#8）未获批仓位倍数不得改变下单量（行为实测，非代码断言）
  F 组（#7）approved=false 研究信号不得进入路由（在 B 组反例基础上单列复核）

纪律：
- 全部在 `iso/` 下的**副本**上做；生产库只读；行情库以只读方式打开。
- 不写 `live_run`、不写生产账本、不动进程。
- 每个用例独立子进程（避免模块级缓存串扰），原始 stdout/stderr 落 `logs/`。
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
ROOT = PROJ / "evidence" / "SIGNAL-EFFECTIVE-02-WORK"
ISO = ROOT / "iso"
BASE = ROOT / "baseline"
LOGS = ROOT / "logs"
OUT = ROOT / "out"
BACKEND = PROJ / "框架/backend"
MARKET_DB = PROJ / "数据/市场数据/db/market_v2.sqlite3"

PAPER_EPOCH = 1_791_939_600_000          # 2026-10-14T01:00:00Z
FLOOR = 1_792_748_844_240                # 2026-10-23T09:47:24Z
H1 = 3_600_000
M15 = 900_000


def iso(ms) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def ms(s: str) -> int:
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def new_ws(tag: str) -> dict:
    root = ISO / tag
    if root.exists():
        shutil.rmtree(root)
    (root / "ledger").mkdir(parents=True)
    (root / "locks").mkdir(parents=True)
    live = root / "live_signals.sqlite3"
    shutil.copy2(BASE / "live_signals_snapshot.sqlite3", live)
    gate = root / "locks" / "paper_new_entry_gate.json"
    gate.write_text(json.dumps({
        "allow_new_entries": True, "reason": "iso_test_02",
        "set_by": "signal_effective_02", "since_ms": FLOOR,
        "scope": "server_side_new_entry_only"}, ensure_ascii=False), encoding="utf-8")
    return {"root": root, "live": live, "ledger": root / "ledger", "gate": gate,
            "locks": root / "locks"}


def ws_env(ws: dict, arm: str) -> dict:
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
                       capture_output=True, text=True, timeout=600)
    (LOGS / f"{tag}.log").write_text(
        (p.stdout or "") + "\n---STDERR---\n" + (p.stderr or ""), encoding="utf-8")
    for line in (p.stdout or "").splitlines():
        if line.startswith("__JSON__"):
            return json.loads(line[len("__JSON__"):])
    return {"error": "child_no_json", "rc": p.returncode,
            "stderr_tail": (p.stderr or "")[-1500:]}


# ══════════════════════════════════════════════════════════════════════════════
# A 组：#3 边界闭区间（open_position 直接调用，显式 epoch_ms=FLOOR）
# ══════════════════════════════════════════════════════════════════════════════
OPEN_CHILD = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L, paper_engine as PE

case = os.environ["CASE"]
ev = int(os.environ["EV_MS"])
now = int(time.time() * 1000)
kw = dict(
    signal_uid="iso02_" + case, direction="long",
    event_time=ev, available_at=ev, origin="live",
    epoch_ms=int(os.environ["FLOOR"]),
)
if os.environ.get("PM"):
    kw["signal_payload"] = {"position_multiplier": float(os.environ["PM"]),
                            "position_multiplier_approved": os.environ.get("PM_OK") == "1"}
if os.environ.get("ATR"):
    kw["atr"] = float(os.environ["ATR"])
out = {"case": case, "event_ms": ev, "event_utc": time.strftime(
    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ev / 1000)), "real_wall_ms": now}
try:
    out["res"] = PE.open_position("3a", "BTCUSDT", **kw)
except Exception as exc:
    out["exc"] = f"{type(exc).__name__}: {exc}"
print("__JSON__" + json.dumps(out, ensure_ascii=False, default=str))
'''

# ══════════════════════════════════════════════════════════════════════════════
# B / C / D / F 组：路由 + 挂单推进
# ══════════════════════════════════════════════════════════════════════════════
ROUTE_CHILD = r'''
import json, os, sys, time, sqlite3
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L, paper_engine as PE, unified_runtime as UR

arm = os.environ["TEST_ARM"]
now = int(time.time() * 1000)
router = UR.SignalPersistenceAndRouter(arm=arm)

def run_levels():
    con = L.live_conn(ro=True)
    try:
        return {r[0]: int(r[1]) for r in con.execute("SELECT key, value_json FROM live_run")}
    finally:
        con.close()

before_levels = run_levels()
r1 = router.route_pending_orders(now)
mid_levels = run_levels()
r2 = router.route_pending_orders(now)          # 幂等：第二次不得重复登记
after_levels = run_levels()

con = L.paper_conn(arm)
try:
    pend = [dict(x) for x in con.execute(
        "SELECT pending_id,symbol,signal_uid,status,event_time,available_at,epoch_ms "
        "FROM paper_pending")]
    pos = [dict(x) for x in con.execute(
        "SELECT position_id,symbol,signal_uid,status,qty,entry_price,stop_price,"
        "planned_risk,voided FROM paper_position")]
    order_n = con.execute("SELECT COUNT(*) FROM paper_order").fetchone()[0]
    fill_n = con.execute("SELECT COUNT(*) FROM paper_fill").fetchone()[0]
finally:
    con.close()

adv = None
if os.environ.get("ADVANCE") == "1":
    try:
        adv = PE.advance_pending(T=now, log=lambda *a: None)
    except Exception as exc:
        adv = {"error": f"{type(exc).__name__}: {exc}"}
    con = L.paper_conn(arm)
    try:
        pos_after = [dict(x) for x in con.execute(
            "SELECT position_id,symbol,signal_uid,status,qty,entry_price,stop_price,"
            "planned_risk,voided FROM paper_position")]
        pend_after = [dict(x) for x in con.execute(
            "SELECT pending_id,signal_uid,status FROM paper_pending")]
    finally:
        con.close()
else:
    pos_after, pend_after = pos, pend

print("__JSON__" + json.dumps({
    "arm": arm, "now_ms": now,
    "levels_before": before_levels, "levels_mid": mid_levels, "levels_after": after_levels,
    "first": r1, "second": r2,
    "pending": pend, "positions": pos,
    "pending_after": pend_after, "positions_after": pos_after,
    "order_n": order_n, "fill_n": fill_n, "advance": adv,
}, ensure_ascii=False, default=str))
'''

# ══════════════════════════════════════════════════════════════════════════════
# E 组：#8 仓位倍数行为实测（让信号在历史时刻真实成交）
# ══════════════════════════════════════════════════════════════════════════════
PM_CHILD = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L, paper_engine as PE

case = os.environ["CASE"]
ev = int(os.environ["EV_MS"])            # 2026-10-24T00:00:00Z（水位后，行情库有数据）
now = ev + 30 * 60 * 1000                # 00:30，成交 bar=00:15，lag 15min < 1h
pm = float(os.environ["PM"])
approved = os.environ.get("PM_OK") == "1"
payload = {"position_multiplier": pm}
if approved:
    payload["position_multiplier_approved"] = True
out = {"case": case, "pm": pm, "approved": approved, "now_ms": now}
try:
    out["res"] = PE.open_position(
        "3a", "BTCUSDT", signal_uid="iso02_pm_" + case, direction="long",
        atr=800.0, event_time=ev, available_at=ev,
        now_ms=now, requested_at=ev, origin="replay",
        epoch_ms=int(os.environ["FLOOR"]),
        stop_mode="uniform", entry_mode="three_buy_points",
        signal_payload=payload)
except Exception as exc:
    out["exc"] = f"{type(exc).__name__}: {exc}"
con = L.paper_conn("3a")
try:
    out["positions"] = [dict(r) for r in con.execute(
        "SELECT position_id,symbol,qty,entry_price,stop_price,planned_risk,status "
        "FROM paper_position")]
    out["pending"] = [dict(r) for r in con.execute(
        "SELECT pending_id,signal_uid,status,stop_price FROM paper_pending")]
finally:
    con.close()
print("__JSON__" + json.dumps(out, ensure_ascii=False, default=str))
'''


def insert_signal(dbp: Path, *, uid: str, confirmed_at: int, available_at: int | None,
                  arm: str = "3a", approved=None, pm=None, box_start_ms=None,
                  executable_state="enterable", origin="live", role="enterable_arm_entry"):
    """在隔离副本里插一条 live_signal（生产库不碰）。"""
    con = sqlite3.connect(str(dbp))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(live_signal)")}
        payload = {
            "role": role, "arm": arm,
            "price": 81000.0, "breakout_price": 81100.0,
            "stop_loss": 76950.0, "struct_low": 76000.0,
            "stop_mode": "uniform", "entry_mode": "three_buy_points",
            "box_start_ms": box_start_ms if box_start_ms is not None else confirmed_at - 48 * H1,
            "box_low_l": 76000.0, "box_high_h": 81100.0, "box_atr_a": 800.0,
            "raw_candidate": {"entry_ref_price": 81000.0, "resistance": 81100.0,
                              "initial_stop_price": 76950.0, "breakout_close": 81000.0},
        }
        if approved is not None:
            payload["approved"] = approved
        if pm is not None:
            payload["position_multiplier"] = pm
        row = {
            "signal_uid": uid, "strategy_key": "FT-LOW4H-EXP-V2.2",
            "exchange": "binance", "market": "um", "timeframe": "1h",
            "symbol": "BTCUSDT", "confirmed_at": confirmed_at,
            "available_at": available_at, "origin": origin,
            "source": "iso02_construct", "executable_state": executable_state,
            "executable_reason": "iso_constructed",
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "first_seen_at": confirmed_at,
            "updated_at": confirmed_at,
            "display_state": "displayable",
            "event_time": confirmed_at,
            "bar_open_time": confirmed_at - H1,
            "bar_close_time": confirmed_at + 1,
        }
        use = {k: v for k, v in row.items() if k in cols}
        keys = ",".join(use)
        ph = ",".join("?" * len(use))
        con.execute(f"INSERT OR REPLACE INTO live_signal({keys}) VALUES({ph})",
                    tuple(use.values()))
        con.commit()
    finally:
        con.close()


def latest_closed_15m_close(now_ms: int) -> int:
    """最近一根**已闭合** 15m 的 close_time - 1（= 库里 confirmed_at 的存法）。"""
    con = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True, timeout=20)
    try:
        r = con.execute(
            "SELECT open_time, close_time FROM candles WHERE exchange='binance' "
            "AND market='um' AND symbol='BTCUSDT' AND timeframe='15m' AND is_closed=1 "
            "AND close_time < ? ORDER BY open_time DESC LIMIT 1", (now_ms,)).fetchone()
    finally:
        con.close()
    return int(r[1]) if r else None


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    ISO.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    R = {"generated_at_utc": iso(int(time.time() * 1000)),
         "paper_epoch_ms": PAPER_EPOCH, "effective_floor_ms": FLOOR,
         "floor_utc": iso(FLOOR), "production_writes": 0, "cases": {}}

    # ── A 组：#3 边界闭区间 ─────────────────────────────────────────────────
    wsA = new_ws("A_boundary")
    envA = ws_env(wsA, "3a")
    envA["FLOOR"] = str(FLOOR)
    cases_a = [
        ("A1_边界前2ms", FLOOR - 2, "expect_reject"),
        ("A2_闭区间右端_边界前1ms", FLOOR - 1, "expect_pass_epoch_gate"),
        ("A3_恰在边界", FLOOR, "expect_pass_epoch_gate"),
        ("A4_边界后1h", FLOOR + H1, "expect_pass_epoch_gate"),
    ]
    R["cases"]["A_边界闭区间"] = {}
    for name, ev, expect in cases_a:
        r = run_child(OPEN_CHILD, {**envA, "CASE": name, "EV_MS": str(ev)},
                      f"A_{name}")
        res = r.get("res") or {}
        reason = res.get("reason")
        got_reject = (reason == "before_paper_epoch_no_backfill")
        ok = (got_reject if expect == "expect_reject" else not got_reject)
        R["cases"]["A_边界闭区间"][name] = {
            "event_utc": iso(ev), "expected": expect, "reason": reason,
            "ok_flag": res.get("ok"), "pass_epoch_gate": not got_reject,
            "assert_pass": ok, "exc": r.get("exc"),
            "detail": (res.get("detail") or "")[:200],
        }
        print(f"[{name}] event={iso(ev)} reason={reason} 断言={'通过' if ok else '失败'}")

    # ── B 组：#2 结构早于水位、确认晚于水位 ─────────────────────────────────
    wsB = new_ws("B_struct_before_confirm_after")
    now_b = int(time.time() * 1000)
    last_close = latest_closed_15m_close(now_b)
    if last_close is None:
        R["cases"]["B_结构早于水位"] = {"error": "行情库无最近闭合 15m"}
    else:
        box_old = FLOOR - 72 * H1          # 结构形成于 10-20，早于水位
        # 正例：结构早于水位，确认在最近一根已闭合 15m（水位之后）
        insert_signal(wsB["live"], uid="iso02_B1_struct_early_confirm_late",
                      confirmed_at=last_close, available_at=last_close,
                      box_start_ms=box_old)
        # 反例：同样结构，但确认时刻在水位前 1h
        insert_signal(wsB["live"], uid="iso02_B2_confirm_before_floor",
                      confirmed_at=FLOOR - H1, available_at=FLOOR - H1,
                      box_start_ms=box_old)
        rB = run_child(ROUTE_CHILD, {**ws_env(wsB, "3a"), "ADVANCE": "0"}, "B_route")
        pend_b = rB.get("pending") or []
        b1 = [p for p in pend_b if p.get("signal_uid") == "iso02_B1_struct_early_confirm_late"]
        b2 = [p for p in pend_b if p.get("signal_uid") == "iso02_B2_confirm_before_floor"]
        R["cases"]["B_结构早于水位"] = {
            "构造": {
                "box_start_utc": iso(box_old),
                "B1_confirmed_utc": iso(last_close),
                "B2_confirmed_utc": iso(FLOOR - H1),
                "水位_utc": iso(FLOOR),
            },
            "route_first": rB.get("first"), "route_second": rB.get("second"),
            "B1_pending": b1, "B2_pending": b2,
            "断言_B1能正常响应": len(b1) == 1,
            "断言_B2被拒且不登记": len(b2) == 0,
            "error": rB.get("error"), "stderr_tail": rB.get("stderr_tail"),
        }
        print(f"[B] B1(结构早/确认晚) 挂单={len(b1)}  B2(确认在水位前) 挂单={len(b2)}"
              f"  route={rB.get('first')}")

        # ── F 组：#7 approved=false 不得进入路由 ─────────────────────────────
        wsF = new_ws("F_unapproved")
        insert_signal(wsF["live"], uid="iso02_F1_approved_false",
                      confirmed_at=last_close, available_at=last_close, approved=False)
        insert_signal(wsF["live"], uid="iso02_F2_approved_true",
                      confirmed_at=last_close, available_at=last_close, approved=True)
        insert_signal(wsF["live"], uid="iso02_F3_approved_missing",
                      confirmed_at=last_close, available_at=last_close, approved=None)
        rF = run_child(ROUTE_CHILD, {**ws_env(wsF, "3a"), "ADVANCE": "0"}, "F_route")
        pend_f = {p.get("signal_uid"): p for p in (rF.get("pending") or [])}
        R["cases"]["F_未批准不进路由"] = {
            "route_first": rF.get("first"),
            "pending_uids": sorted(pend_f.keys()),
            "断言_F1_approved_false_无挂单": "iso02_F1_approved_false" not in pend_f,
            "断言_F2_approved_true_有挂单": "iso02_F2_approved_true" in pend_f,
            "断言_F3_缺字段_默认放行": "iso02_F3_approved_missing" in pend_f,
            "error": rF.get("error"),
        }
        print(f"[F] 挂单 uid={sorted(pend_f.keys())}")

    # ── C 组：#5 重启沿用 + 幂等 ────────────────────────────────────────────
    wsC = new_ws("C_restart_idempotent")
    rC = run_child(ROUTE_CHILD, {**ws_env(wsC, "3a"), "ADVANCE": "0"}, "C_route")
    lv_b, lv_m, lv_a = (rC.get("levels_before") or {}), (rC.get("levels_mid") or {}), \
                       (rC.get("levels_after") or {})
    R["cases"]["C_重启沿用与幂等"] = {
        "levels_before": lv_b, "levels_mid": lv_m, "levels_after": lv_a,
        "断言_三水位未变": (lv_b == lv_m == lv_a),
        "断言_水位等于核定值": (lv_b.get("live_start_ms") == FLOOR),
        "first": rC.get("first"), "second": rC.get("second"),
        "断言_第二次不重复登记": (rC.get("second") or {}).get("registered") == 0,
        "pending_n": len(rC.get("pending") or []),
        "error": rC.get("error"), "stderr_tail": rC.get("stderr_tail"),
    }
    print(f"[C] 水位未变={lv_b == lv_m == lv_a}  live_start={lv_b.get('live_start_ms')} "
          f"first={rC.get('first')} second={rC.get('second')}")

    # ── D 组：#6 既有挂单 / 持仓保持 ────────────────────────────────────────
    wsD = new_ws("D_preexisting_kept")
    # 先用一次空跑让 paper_conn 自动建表
    run_child("import os,sys;sys.path.insert(0,os.environ['BACKEND']);"
              "import live_store as L;L.paper_conn('3a').close();print('__JSON__{}')",
              ws_env(wsD, "3a"), "D_init_schema")
    tD = int(time.time() * 1000)
    con = sqlite3.connect(str(wsD["ledger"] / "paper_3a.sqlite3"))
    try:
        con.execute(
            "INSERT OR REPLACE INTO paper_pending(pending_id,account_id,arm,symbol,"
            "direction,signal_uid,origin,atr,stop_price,event_time,available_at,"
            "requested_at,earliest_fill_ms,status,created_at,first_request_ms,epoch_ms,"
            "voided,stoploss_s,trail_d,model_version,config_fingerprint,initial_stop_price) "
            "VALUES(90001,'paper43_3a','3a','BTCUSDT','long','iso02_D_preexisting',"
            "'live',800.0,76950.0,?,?,?,?,'pending',?,?,?,0,0.05,0.05,"
            "'FT-LOW4H-EXP-V2.2','iso02',76950.0)",
            (FLOOR + H1, FLOOR + H1, FLOOR + H1, FLOOR + 2 * H1, tD, FLOOR + H1, FLOOR))
        con.execute(
            "INSERT OR REPLACE INTO paper_position(position_id,account_id,symbol,qty,"
            "entry_price,entry_ts,status,signal_uid,arm,stop_price,atr_at_entry,"
            "model_version,event_time,available_at,requested_at,earliest_fill_ms,"
            "origin,first_request_ms,epoch_ms,voided,stoploss_s,trail_d,"
            "config_fingerprint,initial_stop_price,planned_risk) "
            "VALUES(90002,'paper43_3a','BTCUSDT',0.02,81000.0,?,'open',"
            "'iso02_D_position','3a',76950.0,800.0,'FT-LOW4H-EXP-V2.2',?,?,?,?,"
            "'live',?,?,0,0.05,0.05,'iso02',76950.0,81.0)",
            (FLOOR + H1, FLOOR + H1, FLOOR + H1, FLOOR + H1, FLOOR + 2 * H1, FLOOR + H1, FLOOR))
        con.commit()
        n_pend_before = con.execute("SELECT COUNT(*) FROM paper_pending").fetchone()[0]
        n_pos_before = con.execute("SELECT COUNT(*) FROM paper_position").fetchone()[0]
    finally:
        con.close()
    rD = run_child(ROUTE_CHILD, {**ws_env(wsD, "3a"), "ADVANCE": "1"}, "D_route_advance")
    pend_after = rD.get("pending_after") or []
    pos_after = rD.get("positions_after") or []
    kept_pend = [p for p in pend_after if p.get("signal_uid") == "iso02_D_preexisting"]
    kept_pos = [p for p in pos_after if p.get("signal_uid") == "iso02_D_position"]
    stop_ok = bool(kept_pos) and float(kept_pos[0].get("stop_price") or 0) >= 76950.0
    R["cases"]["D_既有挂单持仓保持"] = {
        "before": {"pending_n": n_pend_before, "position_n": n_pos_before},
        "after": {"pending_n": len(pend_after), "position_n": len(pos_after)},
        "kept_pending": kept_pend, "kept_position": kept_pos,
        "advance": rD.get("advance"),
        "断言_挂单未被删": len(kept_pend) == 1,
        "断言_持仓仍在且open": bool(kept_pos) and kept_pos[0].get("status") == "open",
        "断言_止损不下移": stop_ok,
        "error": rD.get("error"), "stderr_tail": rD.get("stderr_tail"),
    }
    print(f"[D] 挂单 kept={len(kept_pend)} 持仓 kept={len(kept_pos)} 止损不下移={stop_ok}")

    # ── E 组：#8 仓位倍数不得改变下单量（每个用例独立工作区，避免同标的加仓被拒）──
    evE = ms("2026-10-24T00:00:00Z")
    e1 = run_child(PM_CHILD, {**ws_env(new_ws("E1_pm_unapproved"), "3a"),
                              "FLOOR": str(FLOOR), "EV_MS": str(evE),
                              "CASE": "E1_pm0.5_unapproved", "PM": "0.5", "PM_OK": "0"},
                   "E1_pm_unapproved")
    e2 = run_child(PM_CHILD, {**ws_env(new_ws("E2_pm_approved"), "3a"),
                              "FLOOR": str(FLOOR), "EV_MS": str(evE),
                              "CASE": "E2_pm0.5_approved", "PM": "0.5", "PM_OK": "1"},
                   "E2_pm_approved")
    e3 = run_child(PM_CHILD, {**ws_env(new_ws("E3_pm_baseline"), "3a"),
                              "FLOOR": str(FLOOR), "EV_MS": str(evE),
                              "CASE": "E3_pm1.0_baseline", "PM": "1.0", "PM_OK": "0"},
                   "E3_pm_baseline")

    def risk_of(r):
        ps = r.get("positions") or []
        if not ps:
            return None
        return float(ps[0].get("planned_risk") or 0)

    r1v, r2v, r3v = risk_of(e1), risk_of(e2), risk_of(e3)
    R["cases"]["E_仓位倍数"] = {
        "E1_pm0.5_未批准": {"reason": (e1.get("res") or {}).get("reason"),
                        "planned_risk": r1v, "positions": e1.get("positions")},
        "E2_pm0.5_已批准": {"reason": (e2.get("res") or {}).get("reason"),
                        "planned_risk": r2v, "positions": e2.get("positions")},
        "E3_pm1.0_基准": {"reason": (e3.get("res") or {}).get("reason"),
                       "planned_risk": r3v, "positions": e3.get("positions")},
        "断言_未批准不改变下单量": (r1v is not None and r3v is not None
                          and abs(r1v - r3v) < 1e-9),
        "断言_已批准确实减半": (r2v is not None and r3v is not None
                        and abs(r2v - r3v * 0.5) < 1e-6),
        "exc": [e1.get("exc"), e2.get("exc"), e3.get("exc")],
    }
    print(f"[E] risk: 未批准0.5x={r1v} 已批准0.5x={r2v} 基准1.0x={r3v}")

    # ── 汇总 ────────────────────────────────────────────────────────────────
    checks = []
    for k, v in (R["cases"].get("A_边界闭区间") or {}).items():
        checks.append((f"A/{k}", v.get("assert_pass")))
    for k, v in (R["cases"].get("B_结构早于水位") or {}).items():
        if k.startswith("断言_"):
            checks.append((f"B/{k}", v))
    for k, v in (R["cases"].get("F_未批准不进路由") or {}).items():
        if k.startswith("断言_"):
            checks.append((f"F/{k}", v))
    for k, v in (R["cases"].get("C_重启沿用与幂等") or {}).items():
        if k.startswith("断言_"):
            checks.append((f"C/{k}", v))
    for k, v in (R["cases"].get("D_既有挂单持仓保持") or {}).items():
        if k.startswith("断言_"):
            checks.append((f"D/{k}", v))
    for k, v in (R["cases"].get("E_仓位倍数") or {}).items():
        if k.startswith("断言_"):
            checks.append((f"E/{k}", v))
    R["summary"] = {
        "total": len(checks),
        "passed": sum(1 for _, ok in checks if ok),
        "failed": [n for n, ok in checks if not ok],
        "items": [{"assert": n, "pass": bool(ok)} for n, ok in checks],
    }
    p = OUT / "07_隔离断言.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n汇总：{R['summary']['passed']}/{R['summary']['total']} 通过")
    if R["summary"]["failed"]:
        print("失败项：" + ", ".join(R["summary"]["failed"]))
    print(f"产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

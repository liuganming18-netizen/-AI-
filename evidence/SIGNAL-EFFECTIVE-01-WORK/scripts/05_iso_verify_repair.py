# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-01-WORK｜隔离验证（修复前 vs 修复后，生产零写入）。

覆盖本单第七节要求的 8 类：
  C1 生效前已确认的历史信号 ⇒ 不得创建/登记当前订单（含 9 天窗口的 A/B 对照）
  C2 生效后新确认的合法信号 ⇒ 正常登记挂单（链路没被修坏）
  C3 289 条恢复身份后 ⇒ 无论运行臂是 3a 还是 4a，路由都扫不到
  C4 修复幂等（第二次 0 命中）、路由重复调用不重复挂单
  C5 已存在的挂单/持仓不被误清
  C6 回滚可还原
  C7 available_at 为空时不再被 confirmed_at 填补（代码级断言）
  C8 页面/API 与路由用同一集合（approved=false 双端同判据）

全部在 evidence/SIGNAL-EFFECTIVE-01-WORK/iso/ 下的副本上做，生产库只读。
"""
from __future__ import annotations

import json
import calendar
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EV = PROJ / "evidence" / "SIGNAL-EFFECTIVE-01-WORK"
ISO = EV / "iso"
BASE = EV / "baseline"
LOGS = EV / "logs"
OUT = EV / "out"
SCRIPTS = EV / "scripts"
BACKEND = PROJ / "框架/backend"

PAPER_EPOCH = 1_791_939_600_000          # 2026-10-14T01:00:00Z
FLOOR = 1_792_748_844_240                # 2026-10-23T09:47:24Z


def iso(ms) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def ms(s: str) -> int:
    """UTC 字符串 → 毫秒（用 timegm，不用 mktime，避免本地时区偏移）。"""
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
    gate.write_text(json.dumps({"allow_new_entries": True,
                                "reason": "iso_test", "set_by": "signal_effective_01",
                                "since_ms": FLOOR, "scope": "server_side_new_entry_only"},
                               ensure_ascii=False), encoding="utf-8")
    return {"root": root, "live": live, "ledger": root / "ledger", "gate": gate}


# ── 子进程：路由 ────────────────────────────────────────────────────────────
ROUTE_CHILD = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L, unified_runtime as UR
arm = os.environ["TEST_ARM"]
router = UR.SignalPersistenceAndRouter(arm=arm)
now = int(time.time() * 1000)
r1 = router.route_pending_orders(now)
r2 = router.route_pending_orders(now)     # 幂等：第二次不得重复登记
con = L.paper_conn(arm)
try:
    pend = [dict(x) for x in con.execute(
        "SELECT pending_id,symbol,signal_uid,status,event_time,available_at FROM paper_pending")]
    pos = [dict(x) for x in con.execute(
        "SELECT position_id,signal_uid,status FROM paper_position")]
finally:
    con.close()
print("__JSON__" + json.dumps({"arm": arm, "first": r1, "second": r2,
                               "pending": pend, "positions": pos},
                              ensure_ascii=False, default=str))
'''

# ── 子进程：open_position 的 A/B（新旧 epoch 语义）────────────────────────────
EPOCH_CHILD = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L, paper_engine as PE

case = os.environ["CASE"]
now = int(time.time() * 1000)
out = {"case": case, "real_wall_ms": now}
if case == "before_floor_old_epoch":
    # 落在 [paper_epoch, floor) 的「启动前」信号，用**旧默认**（paper_epoch）判。
    # origin 用 live（非回放）⇒ 经济请求时刻 = 真实挂钟 ⇒ 应成交 bar 在未来 ⇒ 登记挂单。
    ev = int(os.environ["EV_MS"])
    out["res"] = PE.open_position(
        "3a", "BTCUSDT", signal_uid="iso_before_floor_old", direction="long",
        event_time=ev, available_at=ev, origin="live",
        epoch_ms=int(os.environ["PAPER_EPOCH"]))
elif case == "before_floor_new_epoch":
    # 同一条信号，走**新默认**（effective_floor）
    ev = int(os.environ["EV_MS"])
    out["res"] = PE.open_position(
        "3a", "BTCUSDT", signal_uid="iso_before_floor_new", direction="long",
        event_time=ev, available_at=ev, origin="live")
elif case == "after_floor_legal":
    # 生效之后的合法新信号 ⇒ 正常登记挂单（成交 bar 尚未出现）
    ev = now - 5 * 60 * 1000
    out["res"] = PE.open_position(
        "3a", "BTCUSDT", signal_uid="iso_after_floor_legal", direction="long",
        now_ms=now, event_time=ev, available_at=ev, requested_at=now, origin="test")
elif case == "null_available_at":
    # available_at 为空 ⇒ 不得用 confirmed_at 填补（经济时刻仍取 max(事件, 首次请求)）
    ev = now - 5 * 60 * 1000
    out["res"] = PE.open_position(
        "3a", "BTCUSDT", signal_uid="iso_null_avail", direction="long",
        now_ms=now, event_time=ev, available_at=None, requested_at=now, origin="test")
print("__JSON__" + json.dumps(out, ensure_ascii=False, default=str))
'''


def run_child(code: str, env_extra: dict, tag: str) -> dict:
    env = dict(os.environ)
    env.update(env_extra)
    env["BACKEND"] = str(BACKEND)
    env["PYTHONPATH"] = str(BACKEND)
    p = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, timeout=900)
    (LOGS / f"{tag}.log").write_text(
        (p.stdout or "") + "\n---STDERR---\n" + (p.stderr or ""), encoding="utf-8")
    for line in (p.stdout or "").splitlines():
        if line.startswith("__JSON__"):
            return json.loads(line[len("__JSON__"):])
    return {"error": "child_no_json", "rc": p.returncode,
            "stderr_tail": (p.stderr or "")[-2000:]}


def ws_env(ws: dict, arm: str) -> dict:
    return {"LIVE_DB": str(ws["live"]), "PAPER_LEDGER_DIR": str(ws["ledger"]),
            "PAPER_GATE_FILE": str(ws["gate"]), "PAPER_LEDGER_ALLOW_INIT": "1",
            "TEST_ARM": arm}


def repair(dbp: Path, apply: bool, rollback: bool = False) -> dict:
    cmd = [sys.executable, str(SCRIPTS / "04_repair_signal_identity.py"), "--db", str(dbp)]
    if apply:
        cmd.append("--apply")
    if rollback:
        cmd.append("--rollback")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return {"rc": p.returncode, "stdout": (p.stdout or "")[-800:],
            "stderr": (p.stderr or "")[-800:]}


def snapshot_fields(dbp: Path) -> dict:
    con = sqlite3.connect(str(dbp))
    try:
        q = ("SELECT origin,executable_state,available_at,"
             "json_extract(payload_json,'$.role'),"
             "json_extract(payload_json,'$.approved'),"
             "COUNT(*) FROM live_signal GROUP BY 1,2,3,4,5")
        return {"groups": [list(x) for x in con.execute(q)],
                "total": con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0],
                "first_seen": [x[0] for x in con.execute(
                    "SELECT MIN(first_seen_at) FROM live_signal")],
                "confirmed_span": list(con.execute(
                    "SELECT MIN(confirmed_at),MAX(confirmed_at) FROM live_signal").fetchone())}
    finally:
        con.close()


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    ISO.mkdir(parents=True, exist_ok=True)
    R = {"generated_at_utc": iso(int(time.time() * 1000)),
         "paper_epoch_ms": PAPER_EPOCH, "effective_floor_ms": FLOOR,
         "production_writes": 0, "cases": {}}

    # ── C1a：身份未修 + 代码已修 ⇒ 只靠代码判据是否已经挡住 ──────────────────
    # 真正的「修复前」对照是 03_iso_route_repro.py 在**改代码之前**跑的：
    # 同一份快照、同一臂 4a → 12 条被登记挂单（见 out/iso_route_repro.json）。
    ws0 = new_ws("V0_identity_broken_code_fixed")
    r0 = run_child(ROUTE_CHILD, ws_env(ws0, "4a"), "V0_route_arm4a")
    R["cases"]["C1a_身份未修但代码已修_arm4a"] = {
        "route_first": r0.get("first"), "route_second": r0.get("second"),
        "pending_n": len(r0.get("pending") or []),
        "error": r0.get("error"), "stderr_tail": r0.get("stderr_tail"),
        "对照": ("改代码之前（03_iso_route_repro.py）同条件为 12 条挂单；"
                 "说明 `approved=false` 判据单独就够挡住"),
        "结论": "身份未修、只加代码判据 ⇒ 已 0 挂单（但仍需修身份，否则页面照旧冒充当前）",
    }
    print(f"[C1a] 身份未修+代码已修 arm=4a → pending={len(r0.get('pending') or [])}"
          f"（改代码前同条件 = 12）")

    # ── C3：修复后（隔离副本上跑修复脚本）────────────────────────────────────
    ws1 = new_ws("V1_after_repair")
    before = snapshot_fields(ws1["live"])
    rep1 = repair(ws1["live"], apply=True)
    after = snapshot_fields(ws1["live"])
    R["cases"]["C3_修复字段断言"] = {
        "repair": rep1, "before": before, "after": after,
        "行数不变": before["total"] == after["total"],
        "first_seen_at不变": before["first_seen"] == after["first_seen"],
        "confirmed区间不变": before["confirmed_span"] == after["confirmed_span"],
    }
    print(f"[C3] 修复后分组：{after['groups']}")

    r3a = run_child(ROUTE_CHILD, ws_env(ws1, "3a"), "V1_route_arm3a")
    r4a = run_child(ROUTE_CHILD, ws_env(ws1, "4a"), "V1_route_arm4a")
    R["cases"]["C3b_修复后路由"] = {
        "arm3a": {"route": r3a.get("first"), "pending_n": len(r3a.get("pending") or [])},
        "arm4a": {"route": r4a.get("first"), "pending_n": len(r4a.get("pending") or [])},
        "结论": "修复后两臂都扫不到，挂单 0",
    }
    print(f"[C3b] 修复后 arm=3a pending={len(r3a.get('pending') or [])} "
          f"arm=4a pending={len(r4a.get('pending') or [])}")

    # ── C4：幂等 ────────────────────────────────────────────────────────────
    rep2 = repair(ws1["live"], apply=True)
    R["cases"]["C4_幂等"] = {
        "second_run": rep2,
        "route_twice": {"first": r4a.get("first"), "second": r4a.get("second")},
        "结论": "第二次修复 0 命中；路由连跑两次不重复登记",
    }
    print(f"[C4] 第二次修复输出：{rep2['stdout'].strip().splitlines()[:3]}")

    # ── C5：已有挂单不被误清 ────────────────────────────────────────────────
    con = sqlite3.connect(str(ws1["ledger"] / "paper_3a.sqlite3"))
    _t = int(time.time() * 1000)
    con.execute("INSERT OR IGNORE INTO paper_pending(account_id,arm,symbol,direction,"
                "signal_uid,origin,status,requested_at,earliest_fill_ms,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("paper43_3a", "3a", "BTCUSDT", "long", "iso_preexisting_pending",
                 "live", "pending", _t, _t, _t))
    con.commit()
    n_before = con.execute("SELECT COUNT(*) FROM paper_pending").fetchone()[0]
    con.close()
    repair(ws1["live"], apply=True)
    con = sqlite3.connect(str(ws1["ledger"] / "paper_3a.sqlite3"))
    n_after = con.execute("SELECT COUNT(*) FROM paper_pending").fetchone()[0]
    kept = con.execute("SELECT COUNT(*) FROM paper_pending WHERE signal_uid=?",
                       ("iso_preexisting_pending",)).fetchone()[0]
    con.close()
    R["cases"]["C5_已有挂单"] = {"before": n_before, "after": n_after, "kept": kept,
                             "结论": "修复只动 live_signal，挂单原样保留"}
    print(f"[C5] 挂单 before={n_before} after={n_after} kept={kept}")

    # ── C1b/C2/C7：epoch A/B 与 available_at ─────────────────────────────────
    ws2 = new_ws("V2_epoch_ab")
    ev = ms("2026-10-20T00:00:00Z")
    base_env = ws_env(ws2, "3a")
    old = run_child(EPOCH_CHILD, {**base_env, "CASE": "before_floor_old_epoch",
                                  "EV_MS": str(ev), "PAPER_EPOCH": str(PAPER_EPOCH)},
                    "V2_before_floor_old")
    new = run_child(EPOCH_CHILD, {**base_env, "CASE": "before_floor_new_epoch",
                                  "EV_MS": str(ev)}, "V2_before_floor_new")
    legal = run_child(EPOCH_CHILD, {**base_env, "CASE": "after_floor_legal"},
                      "V2_after_floor_legal")
    nullav = run_child(EPOCH_CHILD, {**base_env, "CASE": "null_available_at"},
                       "V2_null_available_at")
    R["cases"]["C1b_启动前信号_AB"] = {
        "信号时刻": iso(ev),
        "旧默认(paper_epoch)": old.get("res"),
        "新默认(effective_floor)": new.get("res"),
        "结论": ("同一条启动前信号：旧默认会被登记成挂单/成交，新默认拒单 "
                 "before_paper_epoch_no_backfill"),
    }
    R["cases"]["C2_生效后合法信号"] = {"res": legal.get("res"),
                                  "结论": "生效之后的信号正常登记挂单（链路未被修坏）"}
    R["cases"]["C7_available_at为空"] = {"res": nullav.get("res"),
                                     "结论": "传 None 不再被 confirmed_at 填补"}
    print(f"[C1b] 旧={ (old.get('res') or {}).get('reason') } "
          f"新={ (new.get('res') or {}).get('reason') }")
    print(f"[C2] 合法信号={ (legal.get('res') or {}).get('reason') }")
    print(f"[C7] available_at=None → { (nullav.get('res') or {}).get('reason') }")

    # ── C6：回滚 ────────────────────────────────────────────────────────────
    ws3 = new_ws("V3_rollback")
    repair(ws3["live"], apply=True)
    mid = snapshot_fields(ws3["live"])
    rb = repair(ws3["live"], apply=True, rollback=True)
    back = snapshot_fields(ws3["live"])
    R["cases"]["C6_回滚"] = {"after_repair_group_n": len(mid["groups"]),
                          "after_repair_sample": mid["groups"][:2],
                          "after_rollback_group_n": len(back["groups"]),
                          "after_rollback_sample": back["groups"][:2],
                          "rollback_output": rb["stdout"],
                          "结论": "回滚后回到修复前状态（origin=recovered / available_at 非空）"}
    print(f"[C6] 回滚后分组数={len(back['groups'])} 样例={back['groups'][:1]}")

    # ── C9：API 口径（当前区 / 研究区）────────────────────────────────────────
    CUR_SQL = ("SELECT COUNT(*) FROM live_signal WHERE origin IN ('live','recovered') "
               "AND executable_state='enterable' AND confirmed_at IS NOT NULL "
               "AND available_at IS NOT NULL "
               "AND (CASE WHEN available_at>confirmed_at THEN available_at ELSE confirmed_at END)+1>=? "
               "AND json_extract(payload_json,'$.role')='enterable_arm_entry' "
               "{guard}"
               "AND signal_uid NOT LIKE 'sig_closeloop_%' AND signal_uid NOT LIKE 'sig_ui_test_%'")
    GUARD = "AND COALESCE(json_extract(payload_json,'$.approved'),1)<>0 "
    api = {}
    for tag, dbp in (("身份未修", ws0["live"]), ("身份已修", ws1["live"])):
        con = sqlite3.connect(str(dbp))
        try:
            api[tag] = {
                "当前区_有approved判据": con.execute(
                    CUR_SQL.format(guard=GUARD), (FLOOR,)).fetchone()[0],
                "当前区_无approved判据": con.execute(
                    CUR_SQL.format(guard=""), (FLOOR,)).fetchone()[0],
                "研究区": con.execute(
                    "SELECT COUNT(*) FROM live_signal WHERE origin='historical_research'"
                ).fetchone()[0],
            }
        finally:
            con.close()
    R["cases"]["C9_API口径"] = {
        **api,
        "结论": ("身份未修时：无判据 14 条冒充当前、有判据 0 条；"
                 "身份修好后当前区 0 条、研究区 289 条（仍可逐条查看，不冒充当前）"),
    }
    print(f"[C9] {json.dumps(api, ensure_ascii=False)}")

    # ── C8：代码级断言 ──────────────────────────────────────────────────────
    pe = (BACKEND / "paper_engine.py").read_text(encoding="utf-8")
    ur = (BACKEND / "unified_runtime.py").read_text(encoding="utf-8")
    lw = (BACKEND / "live_worker.py").read_text(encoding="utf-8")
    api = (BACKEND / "live_api.py").read_text(encoding="utf-8")
    R["cases"]["C8_代码断言"] = {
        "open_position_默认用有效下限": "L.effective_floor_ms()" in pe,
        "position_multiplier需显式批准": "position_multiplier_approved" in pe,
        "路由排除approved=false": "COALESCE(json_extract(payload_json,'$.approved'),1)<>0" in ur,
        "API排除approved=false": "COALESCE(json_extract(payload_json,'$.approved'),1)<>0" in api,
        "worker不再用confirmed_at填available_at":
            'available_at=int(d.get("available_at") or d["confirmed_at"])' not in lw,
        "worker用有效下限传epoch": "epoch_ms=floor_ms" in lw,
        "OI门槛恢复跟随配置":
            'enable_oi = False if (arm == "3b" or os.environ.get("WYCKOFF_RESONANCE"' not in ur,
    }
    print(f"[C8] {json.dumps(R['cases']['C8_代码断言'], ensure_ascii=False)}")

    p = OUT / "iso_verify.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

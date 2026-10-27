# -*- coding: utf-8 -*-
"""60b｜闸门纵深防御 worker（由 60_闸门正反例.py 以子进程调用，只在副本上跑）。

问题：研究信号「不可执行」到底靠哪一层？单层失效会不会漏？
做法：在**副本**上逐层把防护条件改坏，看路由扫描何时才真的扫得到。

五层（`unified_runtime.py:2169 _scan_actionable`）：
  L1 `executable_state='enterable'`
  L2 `payload.role='enterable_arm_entry'`
  L3 `payload.arm=<本臂>`（生产臂 3a）
  L4 `confirmed_at / available_at 都非空`
  L5 调用方传的 `origin` 过滤（生产自然路径传 `live`）

另外做**正向对照**：合成一条 `live + enterable` 行必须被扫到（证明扫描没坏）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT / "框架" / "backend"
for p in (str(BACKEND), str(PROJECT / "框架"), str(PROJECT),
          str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import probe_market  # noqa: E402

probe_market.install()          # 必须在 import 业务模块之前

import live_store as LS  # noqa: E402
import unified_runtime as UR  # noqa: E402

R: dict = {}
RESEARCH = "origin='historical_research'"


def emit():
    print("@@RESULT@@" + json.dumps(R, ensure_ascii=False, default=str))


def main() -> int:
    db = Path(os.environ["LIVE_DB"]).resolve()
    prod = (BACKEND / "live" / "live_signals.sqlite3").resolve()
    R["自报"] = {"LIVE_DB": str(db), "is_production": db == prod,
               "PAPER_LEDGER_DIR": str(Path(LS.LEDGER_DIR).resolve()),
               "pid": os.getpid()}
    if db == prod:
        R["refused"] = "拒绝在生产线库上做「改坏条件」实验"
        emit()
        return 3

    router = UR.SignalPersistenceAndRouter(arm="3a")
    con = LS.live_conn(ro=False)
    try:
        n_research = con.execute(f"SELECT COUNT(*) FROM live_signal WHERE {RESEARCH}").fetchone()[0]
        R["研究行数"] = n_research

        def scans(tag):
            return {
                tag: {
                    "scan(None)": len(router._scan_actionable(con, None)),
                    "scan(live)": len(router._scan_actionable(con, "live")),
                    "scan(historical_research)": len(router._scan_actionable(con, "historical_research")),
                }
            }

        R.update(scans("A_基线（原样）"))

        # ── 正向对照：合成一条 live+enterable ────────────────────────────
        now = int(time.time() * 1000)
        ctrl = f"cand_r1_gate60_{now}"
        con.execute(
            "INSERT INTO live_signal(signal_uid,source,origin,exchange,market,symbol,timeframe,"
            "strategy_key,strategy_version,structural_kind,direction,event_time,confirmed_at,"
            "available_at,bar_open_time,bar_close_time,display_state,executable_state,"
            "payload_json,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ctrl, "r1_gate60_control", "live", UR.EXCHANGE, UR.MARKET, "GATE60USDT",
             UR.TIMEFRAME, UR.STRATEGY_KEY, "v0.1.0", "breakout", "long", now, now, now,
             now - 3_600_000, now, "displayable", "enterable",
             json.dumps({"role": UR.ARM_ENTRY_ROLE, "arm": "3a"}, ensure_ascii=False), now, now))
        con.commit()
        R["B_正向对照"] = {
            "合成 live+enterable 被扫到(scan(None))": len(router._scan_actionable(con, None)),
            "合成 live+enterable 被扫到(scan(live))": len(router._scan_actionable(con, "live")),
        }
        con.execute("DELETE FROM live_signal WHERE signal_uid=?", (ctrl,))
        con.commit()
        R["B_正向对照"]["删除后(scan(None))"] = len(router._scan_actionable(con, None))

        # ── 逐层改坏（在副本上；副本用完即弃，不还原）────────────────────
        con.execute(f"UPDATE live_signal SET executable_state='enterable' WHERE {RESEARCH}")
        con.commit()
        R.update(scans("C1_只改坏L1(executable_state=enterable)"))

        con.execute(f"UPDATE live_signal SET payload_json=json_set(payload_json,'$.role',?) "
                    f"WHERE {RESEARCH}", (UR.ARM_ENTRY_ROLE,))
        con.commit()
        R.update(scans("C2_再改坏L2(role=enterable_arm_entry)"))

        con.execute(f"UPDATE live_signal SET payload_json=json_set(payload_json,'$.arm','3a') "
                    f"WHERE {RESEARCH}")
        con.commit()
        R.update(scans("C3_再改坏L3(arm=3a)"))

        con.execute(f"UPDATE live_signal SET available_at=confirmed_at WHERE {RESEARCH}")
        con.commit()
        R.update(scans("C4_再改坏L4(available_at=confirmed_at)"))

        R["说明"] = ("L1–L4 全改坏后 scan(None) 才非 0 ⇒ 四层各自独立有效；"
                     "但生产自然路径传 origin='live'，L5 仍会把研究行挡在外面。")

        # ── L5：五层全坏时，origin 过滤是否还挡得住 ─────────────────────
        con.execute("DELETE FROM live_signal WHERE origin='historical_research'")
        con.commit()
        uid = f"accum05_4a_GATE60USDT_{now}"
        con.execute(
            "INSERT INTO live_signal(signal_uid,source,origin,exchange,market,symbol,timeframe,"
            "strategy_key,strategy_version,structural_kind,direction,event_time,confirmed_at,"
            "available_at,bar_open_time,bar_close_time,display_state,executable_state,"
            "payload_json,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, "antigravity_08_backtest", "historical_research", UR.EXCHANGE, UR.MARKET,
             "GATE60USDT", UR.TIMEFRAME, "ACCUM-RESEARCH-v0::4a", "accum-early-05::v1::4a",
             "accum_early_entry", "long", now - 60_000, now - 60_000, now - 60_000,
             now - 3_660_000, now - 60_000, "displayable", "enterable",
             json.dumps({"role": UR.ARM_ENTRY_ROLE, "arm": "3a", "approved": False,
                         "run_id": "gate60_probe"}, ensure_ascii=False), now, now))
        con.commit()
        R["D_五层全坏的单条研究行"] = {
            "scan(None)": len(router._scan_actionable(con, None)),
            "scan(live)": len(router._scan_actionable(con, "live")),
            "scan(historical_research)": len(router._scan_actionable(con, "historical_research")),
        }
        R["D_路由_origin=live"] = router.route_pending_orders(now, origin="live")
        R["D_路由_origin=historical_research"] = router.route_pending_orders(
            now, origin="historical_research")
        # 副本账本里看看有没有真登记（有就说明「五层全坏 = 真能开仓」，如实记录）
        try:
            con_p = LS.paper_conn("3a")
            try:
                R["D_副本账本"] = {
                    "paper_pending": con_p.execute(
                        "SELECT COUNT(*) FROM paper_pending WHERE signal_uid=?", (uid,)).fetchone()[0],
                    "paper_position": con_p.execute(
                        "SELECT COUNT(*) FROM paper_position WHERE signal_uid=?", (uid,)).fetchone()[0],
                    "paper_processed": con_p.execute(
                        "SELECT COUNT(*) FROM paper_processed WHERE signal_uid=?", (uid,)).fetchone()[0],
                }
            finally:
                con_p.close()
        except Exception as exc:  # noqa: BLE001
            R["D_副本账本"] = {"error": f"{type(exc).__name__}: {exc}"}

        # ── 本副本到此已被改坏（故意），不还原：60 每次运行都会重新复制一份
        R["E_副本状态"] = {"说明": "副本已被刻意改坏（步骤 C/D），本副本丢弃不复用",
                        "研究行数": con.execute(
                            f"SELECT COUNT(*) FROM live_signal WHERE {RESEARCH}").fetchone()[0],
                        "原始行数（改坏前）": n_research}
    finally:
        con.close()

    R["行情库写探针"] = probe_market.report()
    emit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

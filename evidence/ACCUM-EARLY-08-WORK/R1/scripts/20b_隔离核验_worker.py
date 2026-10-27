# -*- coding: utf-8 -*-
"""20b｜隔离核验 worker（由 20_隔离演练.py 以子进程调用，不单独运行）。

在**隔离库**上回答四件事：
  ① 落库字段是否逐项符合契约（身份/来源/四时刻/可执行态/缺口可见）
  ② 现有 API 处理函数能否按「研究范围」读到、能否被当前交易区挡住
  ③ 批准闸门：路由扫描是否**根本扫不到**研究信号（含最坏情况 origin=None）
  ④ 正向对照：同一条链路对 `origin=live + enterable` 的合成行**能**扫到（证明不是查询坏了）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

PROJECT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT / "框架" / "backend"
for p in (str(BACKEND), str(PROJECT / "框架"), str(PROJECT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import live_store as LS  # noqa: E402

R: dict = {}


def emit():
    print("@@RESULT@@" + json.dumps(R, ensure_ascii=False, default=str))


def main() -> int:
    db = Path(os.environ["LIVE_DB"]).resolve()
    R["隔离自报"] = {
        "LIVE_DB": str(db),
        "is_production": db == (BACKEND / "live" / "live_signals.sqlite3").resolve(),
        "live_store.LIVE_DB": str(Path(LS.LIVE_DB).resolve()),
        "live_store.LEDGER_DIR": str(Path(LS.LEDGER_DIR).resolve()),
        "pid": os.getpid(),
    }

    con = sqlite3.connect(str(db), timeout=20)
    con.row_factory = sqlite3.Row

    # ── ① 字段契约核对 ────────────────────────────────────────────────────
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM live_signal WHERE origin='historical_research' ORDER BY signal_uid")]
    R["行数"] = {"research": len(rows),
               "total": con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]}
    sample = rows[0] if rows else None
    if sample:
        pl = json.loads(sample["payload_json"])
        R["单条字段"] = {
            "signal_uid": sample["signal_uid"],
            "source": sample["source"],
            "origin": sample["origin"],
            "strategy_key": sample["strategy_key"],
            "strategy_version": sample["strategy_version"],
            "config_hash": sample["config_hash"],
            "data_version": sample["data_version"],
            "executable_state": sample["executable_state"],
            "executable_reason": sample["executable_reason"],
            "display_state": sample["display_state"],
            "available_at": sample["available_at"],
            "event_time": sample["event_time"],
            "confirmed_at": sample["confirmed_at"],
            "bar_open_time": sample["bar_open_time"],
            "bar_close_time": sample["bar_close_time"],
            "payload_keys_n": len(pl),
            "payload_role": pl.get("role"),
            "payload_run_id": pl.get("run_id"),
            "payload_approved": pl.get("approved"),
            "payload_available_at_status": (pl.get("timestamps") or {}).get("available_at_status"),
            "payload_config_sha256": pl.get("config_sha256"),
            "payload_engine_view_sha256": pl.get("engine_view_sha256"),
            "payload_code_hashes_n": len(pl.get("code_hashes") or {}),
            "payload_gaps_n": len(pl.get("gaps") or []),
        }

    # 逐项统计（不靠样本）
    agg = con.execute(
        "SELECT COUNT(*) n,"
        " SUM(CASE WHEN origin='historical_research' THEN 1 ELSE 0 END) origin_ok,"
        " SUM(CASE WHEN executable_state='historical_not_executable' THEN 1 ELSE 0 END) exec_ok,"
        " SUM(CASE WHEN available_at IS NULL THEN 1 ELSE 0 END) avail_null,"
        " SUM(CASE WHEN event_time=confirmed_at AND confirmed_at=bar_close_time THEN 1 ELSE 0 END) three_same,"
        " SUM(CASE WHEN bar_open_time IS NOT NULL AND bar_open_time<>event_time THEN 1 ELSE 0 END) bar_open_distinct,"
        " SUM(CASE WHEN bar_open_time < bar_close_time"
        "      AND bar_close_time - bar_open_time = 3599999 THEN 1 ELSE 0 END) bar_order_ok,"
        " SUM(CASE WHEN json_extract(payload_json,'$.timestamps.entry_bar_open_ms') > bar_close_time"
        "      THEN 1 ELSE 0 END) entry_after_close,"
        " SUM(CASE WHEN data_version IS NULL THEN 1 ELSE 0 END) dv_null,"
        " SUM(CASE WHEN config_hash IS NOT NULL THEN 1 ELSE 0 END) cfg_ok,"
        " SUM(CASE WHEN json_extract(payload_json,'$.role')='research_only' THEN 1 ELSE 0 END) role_ok,"
        " SUM(CASE WHEN json_extract(payload_json,'$.approved')=0 THEN 1 ELSE 0 END) approved_false,"
        " SUM(CASE WHEN json_extract(payload_json,'$.run_id')='antigravity_08_arm_b' THEN 1 ELSE 0 END) run_ok,"
        " SUM(CASE WHEN json_extract(payload_json,'$.timestamps.available_at_status')='unproven' THEN 1 ELSE 0 END) unproven_ok"
        " FROM live_signal WHERE origin='historical_research'").fetchone()
    R["逐项统计"] = dict(agg)
    R["按方案"] = {r["strategy_key"]: r["n"] for r in con.execute(
        "SELECT strategy_key, COUNT(*) n FROM live_signal WHERE origin='historical_research' "
        "GROUP BY strategy_key ORDER BY strategy_key")}

    # 首现时刻（幂等判据）
    R["first_seen"] = {r["signal_uid"]: r["first_seen_at"] for r in con.execute(
        "SELECT signal_uid, first_seen_at FROM live_signal ORDER BY signal_uid LIMIT 3")}
    R["first_seen_sha"] = hashlib_sha(json.dumps(R["first_seen"], sort_keys=True))
    con.close()

    # ── ② 现有 API 处理函数 ───────────────────────────────────────────────
    import adapter

    def call(params):
        try:
            return adapter._v1_signals_merged(params)
        except Exception as exc:  # noqa: BLE001
            return {"_error": f"{type(exc).__name__}: {exc}"}

    all_env = call({"scope": ["all"], "origin": ["historical_research"], "limit": ["1000"]})
    cur_env = call({"scope": ["current"], "limit": ["500"]})
    all_any = call({"scope": ["all"], "limit": ["1000"]})
    items = all_env.get("items") or []
    R["API"] = {
        "研究范围(scope=all&origin=historical_research)": {
            "total": all_env.get("total"), "items_n": len(items),
            "quality_status": (all_env.get("quality") or {}).get("status"),
        },
        "审计全量(scope=all)": {"total": all_any.get("total"),
                            "items_n": len(all_any.get("items") or [])},
        "当前交易区(scope=current)": {
            "total": cur_env.get("total"), "items_n": len(cur_env.get("items") or []),
            "quality_status": (cur_env.get("quality") or {}).get("status"),
            "excluded_breakdown": (cur_env.get("quality") or {}).get("excluded_breakdown"),
        },
        "单条字段": items[0] if items else None,
        "研究信号是否混入当前区":
            any(i.get("origin") == "historical_research" for i in (cur_env.get("items") or [])),
    }

    # ── ③ 批准闸门：路由扫描 ──────────────────────────────────────────────
    import unified_runtime as UR
    router = UR.SignalPersistenceAndRouter(arm="3a")
    con2 = LS.live_conn(ro=False)
    try:
        by_origin = router._scan_actionable(con2, "historical_research")
        no_filter = router._scan_actionable(con2, None)
        by_live = router._scan_actionable(con2, "live")
        R["路由扫描"] = {
            "origin=historical_research": len(by_origin),
            "origin=None（最坏情况，无来源过滤）": len(no_filter),
            "origin=live": len(by_live),
            "说明": "三组都必须为 0；研究行的 executable_state='historical_not_executable' 使任何来源过滤都扫不到它",
        }

        # 正向对照：插一条 origin=live + enterable 的合成行，必须被扫到，然后删除
        now = int(time.time() * 1000)
        ctrl_uid = f"cand_r1_gate_control_{now}"
        con2.execute(
            "INSERT INTO live_signal(signal_uid,source,origin,exchange,market,symbol,timeframe,"
            "strategy_key,strategy_version,structural_kind,direction,event_time,confirmed_at,"
            "available_at,bar_open_time,bar_close_time,display_state,executable_state,"
            "payload_json,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ctrl_uid, "r1_gate_control", "live", UR.EXCHANGE, UR.MARKET, "GATECTRLUSDT",
             UR.TIMEFRAME, UR.STRATEGY_KEY, "v0.1.0", "breakout", "long", now, now, now,
             now - 3_600_000, now, "displayable", "enterable",
             json.dumps({"role": UR.ARM_ENTRY_ROLE, "arm": "3a"}, ensure_ascii=False), now, now))
        con2.commit()
        hit_ctrl = router._scan_actionable(con2, None)
        hit_ctrl_live = router._scan_actionable(con2, "live")
        con2.execute("DELETE FROM live_signal WHERE signal_uid=?", (ctrl_uid,))
        con2.commit()
        after_del = router._scan_actionable(con2, None)
        R["正向对照"] = {
            "合成 enterable+live 行被扫到（无过滤）": len(hit_ctrl),
            "合成 enterable+live 行被扫到（origin=live）": len(hit_ctrl_live),
            "删除后回到 0": len(after_del),
            "说明": "证明扫描函数本身是好的；研究信号扫不到不是因为查询坏了",
        }

        # 路由入口：真调一次（应 0 处理、0 挂单、不写账本）
        rr = router.route_pending_orders(int(time.time() * 1000), origin="historical_research")
        R["route_pending_orders(origin=historical_research)"] = rr
    finally:
        con2.close()

    emit()
    return 0


def hashlib_sha(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:16]


if __name__ == "__main__":
    raise SystemExit(main())

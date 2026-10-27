# -*- coding: utf-8 -*-
"""03b｜隔离链路 worker（由 03_隔离与空链路.py 以子进程方式调用，**不单独运行**）。

为什么单独一个进程：生产 API 进程（pid 91940）正持有生产库；在同一进程里改环境变量
指向隔离库会污染不了生产，但为了让「隔离」成为**进程级事实**而不是函数级约定，
本 worker 用一份干净的环境变量启动，跑完即退。

做的事（全部写入只落在 ISOLATED 目录）：
  1. 自报隔离路径（LIVE_DB / PAPER_LEDGER_DIR 指向哪里）；
  2. 用生产库**只读**连接 + SQL 级写探针，确认生产库 0 条写语句；
  3. 造两个**明确标注的合成候选**（不是自然信号）：
       · uid_research：origin="research"（研究/未批标识）
       · uid_live    ：origin="live"（仅作「当前区过滤」的正向对照）
  4. 各写两次 ⇒ 验证幂等（同键只一行、first_seen_at 不变、updated_at 变）；
  5. 调**现有 API 处理函数** `adapter._v1_signals_merged`：
       scope=all      → 两条都应在（审计通道）
       scope=current  → 只应有 origin=live 那条（研究标识进不了交易信号区）
  6. 把结果以 JSON 打到 stdout 最后一行，父进程解析。
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

RESULT: dict = {}


def emit() -> None:
    print("@@RESULT@@" + json.dumps(RESULT, ensure_ascii=False, default=str))


def main() -> int:
    RESULT["生成时刻"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    RESULT["进程"] = {"pid": os.getpid(), "argv0": sys.executable}

    # ── 1. 隔离自报（先证明「我现在写的是哪本库」）────────────────────────
    iso_live = Path(os.environ["LIVE_DB"]).resolve()
    iso_ledger = Path(os.environ["PAPER_LEDGER_DIR"]).resolve()
    prod_live = BACKEND / "live" / "live_signals.sqlite3"
    RESULT["隔离自报"] = {
        "LIVE_DB": str(iso_live),
        "LIVE_DB_is_production": iso_live == prod_live.resolve(),
        "live_store.LIVE_DB": str(Path(LS.LIVE_DB).resolve()),
        "live_store.LEDGER_DIR": str(Path(LS.LEDGER_DIR).resolve()),
        "PAPER_LEDGER_DIR_is_production": Path(LS.LEDGER_DIR).resolve()
        == (PROJECT / "数据" / "paper").resolve(),
        "MARKET_DB": os.environ.get("MARKET_DB"),
        "PAPER_LOCK_DIR": os.environ.get("PAPER_LOCK_DIR"),
        "note": "隔离以「进程环境变量」成立：LIVE_DB / PAPER_LEDGER_DIR 均指向 evidence 下的副本",
    }

    # ── 2. 生产库只读 + SQL 级写探针 ──────────────────────────────────────
    prod = sqlite3.connect(f"file:{prod_live}?mode=ro", uri=True, timeout=10)
    prod.execute("PRAGMA query_only=ON")
    prod_stmts: list[str] = []
    prod.set_trace_callback(lambda s: prod_stmts.append(s))
    n_prod_rows = prod.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
    RESULT["生产库只读探测"] = {
        "path": str(prod_live), "rows_before": n_prod_rows,
        "statements": len(prod_stmts),
        "write_like": [s for s in prod_stmts
                       if s.strip().split(None, 1)[0].upper() in
                       ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP",
                        "ALTER", "VACUUM", "REINDEX", "ATTACH", "DETACH")],
    }
    prod.close()

    # ── 3. 造两个合成候选（明确标注，不是自然信号）──────────────────────
    from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    from 策略.WyckoffMaster.event_trend_v0_1.contracts import CandidateSnapshot
    import unified_runtime as UR

    cfg = EventTrendConfig()
    now_ms = int(time.time() * 1000)
    close_ms = now_ms - (now_ms % UR.H1)          # 对齐到 1h 收盘
    tag = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    def make(uid: str) -> CandidateSnapshot:
        return CandidateSnapshot.create(
            candidate_id=uid,
            event_id=f"ev08work_{tag}",
            strategy_version=cfg.strategy_version,
            symbol="SELFTESTUSDT",     # 故意用不存在的标的：绝不可被当成自然信号
            venue="binance",
            snapshot_time_ms=close_ms,
            available_at_ms=close_ms,
            box_high_h=1.00, box_low_l=0.90, box_atr_a=0.01, box_er=0.12,
            sweep_low_s=0.895, retest_low_lr=0.905, breakout_close=1.02,
            breakout_volume=12345.0, v20_median_volume=5000.0, volume_ratio=2.469,
            oi_breakout=1_000_000.0, oi_6h_ago=960_000.0, oi_growth_6h=0.041667,
            initial_stop_price=0.969, initial_risk_r=0.051,
            max_entry_price=1.025, min_entry_price=1.00,
            execution_earliest_ms=close_ms + 15 * 60 * 1000,
            pit_proven=False,
            pit_assumption_note="ACCUM-EARLY-08-WORK 隔离自测：非自然信号，仅验证链路",
            missing_fields=[],
        )

    uid_research = f"cand_ev08work_research_{tag}"
    uid_live = f"cand_ev08work_livecontrol_{tag}"

    router = UR.SignalPersistenceAndRouter(arm="3a")
    RESULT["持久化"] = {"uid_research": uid_research, "uid_live": uid_live,
                     "config_strategy_version": cfg.strategy_version,
                     "config_rule": cfg.rule, "config_model_version": cfg.model_version}

    w1 = router.persist_candidates([make(uid_research)], origin="research", now_ms=now_ms)
    time.sleep(0.05)
    w2 = router.persist_candidates([make(uid_research)], origin="research",
                                   now_ms=now_ms + 1000)
    w3 = router.persist_candidates([make(uid_live)], origin="live", now_ms=now_ms)
    RESULT["持久化"]["writes"] = {"first_research": w1, "second_research_same_uid": w2,
                                "live_control": w3}

    # ── 4. 幂等核对（直接查隔离库）───────────────────────────────────────
    con = LS.live_conn(ro=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT signal_uid, source, origin, symbol, timeframe, strategy_key, "
        "strategy_version, structural_kind, direction, event_time, confirmed_at, "
        "available_at, bar_open_time, bar_close_time, display_state, executable_state, "
        "first_seen_at, updated_at, payload_json FROM live_signal "
        "WHERE signal_uid IN (?,?) ORDER BY signal_uid", (uid_research, uid_live))]
    total_iso = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
    con.close()

    by_uid = {r["signal_uid"]: r for r in rows}
    r0 = by_uid.get(uid_research) or {}
    payload = json.loads(r0.get("payload_json") or "{}")
    RESULT["幂等核对"] = {
        "隔离库总行数": total_iso,
        "research_行数": 1 if uid_research in by_uid else 0,
        "同一uid只一行": (uid_research in by_uid),
        "两次写入统计": {"first_new": w1.get("new"), "first_updated": w1.get("updated"),
                     "second_new": w2.get("new"), "second_updated": w2.get("updated")},
        "first_seen_at_未被改写": (r0.get("first_seen_at") == now_ms),
        "updated_at_已推进": (r0.get("updated_at") == now_ms + 1000),
        "research_origin": r0.get("origin"),
        "research_source": r0.get("source"),
        "research_strategy_key": r0.get("strategy_key"),
        "research_strategy_version": r0.get("strategy_version"),
        "research_executable_state": r0.get("executable_state"),
        "research_available_at_eq_confirmed_at": r0.get("available_at") == r0.get("confirmed_at"),
        "payload_role": payload.get("role"),
        "payload_arm": payload.get("arm"),
        "payload_keys_n": len(payload),
        "payload_has_rule_or_model_version":
            ("rule" in payload) or ("model_version" in payload),
        "payload_snapshot_hash": payload.get("snapshot_hash"),
        "live_control_origin": (by_uid.get(uid_live) or {}).get("origin"),
    }

    # ── 5. 现有 API 处理函数（route 表绑定的就是它）──────────────────────
    import adapter

    def call(scope: str, limit: str = "500") -> dict:
        try:
            return adapter._v1_signals_merged({"scope": [scope], "limit": [limit]})
        except Exception as exc:  # noqa: BLE001
            return {"_error": f"{type(exc).__name__}: {exc}"}

    all_env = call("all")
    cur_env = call("current")
    all_items = all_env.get("items") or []
    cur_items = cur_env.get("items") or []
    all_uids = [i.get("signal_uid") or i.get("signal_id") or i.get("id") for i in all_items]
    cur_uids = [i.get("signal_uid") or i.get("signal_id") or i.get("id") for i in cur_items]

    def pick(items, uid):
        for i in items:
            if (i.get("signal_uid") or i.get("signal_id") or i.get("id")) == uid:
                return i
        return None

    item_all = pick(all_items, uid_research)
    item_cur_live = pick(cur_items, uid_live)
    RESULT["API层"] = {
        "处理函数": "adapter._v1_signals_merged（= api/routes.py 里 Route('/api/v1/signals') 绑定的同一个函数）",
        "scope_all": {"total": all_env.get("total"), "items_n": len(all_items),
                      "quality_status": (all_env.get("quality") or {}).get("status"),
                      "strategy_version": all_env.get("strategy_version"),
                      "rule_version": all_env.get("rule_version"),
                      "param_hash": all_env.get("param_hash"),
                      "research_uid_present": uid_research in all_uids,
                      "live_uid_present": uid_live in all_uids},
        "scope_current": {"total": cur_env.get("total"), "items_n": len(cur_items),
                          "quality_status": (cur_env.get("quality") or {}).get("status"),
                          "excluded_n": (cur_env.get("quality") or {}).get("excluded_n"),
                          "excluded_breakdown": (cur_env.get("quality") or {}).get("excluded_breakdown"),
                          "research_uid_present": uid_research in cur_uids,
                          "live_uid_present": uid_live in cur_uids},
        "research_item_fields": item_all,
        "live_control_item_fields": item_cur_live,
    }

    # ── 6. 生产库再查一次（行数未变 + 写探针）───────────────────────────
    prod2 = sqlite3.connect(f"file:{prod_live}?mode=ro", uri=True, timeout=10)
    n_after = prod2.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
    prod2.close()
    RESULT["生产库只读探测"]["rows_after"] = n_after
    RESULT["生产库只读探测"]["rows_unchanged"] = n_after == n_prod_rows

    emit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

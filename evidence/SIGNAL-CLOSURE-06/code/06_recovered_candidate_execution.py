# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜06 重启恢复后的候选如何进入执行（工单第 5 条）。

要回答的四件事：
  ① **过期历史候选** —— 预热回放里那些早已过成交时效的候选，是否真的不落库、不下单；
  ② **停机期间候选** —— 停机期间产生、但**仍在成交时效内**的候选，重启后是否被恢复并正常下单
     （这正是本单修掉的那条「重启方式不同 ⇒ 少一笔信号」的路径）；
  ③ **过期的不补** —— 同一个候选，把重启时刻推后到超过 `MAX_FILL_LAG_MS`，是否一律丢弃；
  ④ **重复处理不重复发信号 / 不重复下单** —— 同一个候选连着重启两次，是否只有一条信号、
     一张单、一笔持仓。

隔离（硬要求，缺一不可）：
  · `MARKET_DB` → 本单自建的独立行情库（只含 3 个标的的 1h/4h/15m，从生产库**只读**导出）；
  · `MARKET_ARCHIVE` → 空目录（否则冷层 parquet 会把生产历史带进来）；
  · `PAPER_LEDGER_DIR` + `PAPER_LEDGER_ALLOW_INIT=1` → 独立纸面账本；
  · `LIVE_DB` → 独立 live 库；`PAPER_GATE_FILE` → 独立闸门文件（显式放行，仅为测成交）；
  · `EVENT_TREND_BATCH_ROOT` / `RUNS_DIR` → 独立目录（否则 `/batches` 会回落读生产）。
以上全部在 **import 任何 backend 模块之前** 写进 env（本项目硬约束）。

跑的是**生产的** `UnifiedRuntime` / `MultiSymbolSignalEngine` / `SignalPersistenceAndRouter`，
只把「历史来源」和「行情源」换成内存件 —— 等价于「把库倒回到那一刻」。

用法：python code/06_recovered_candidate_execution.py
产出：out/06_恢复候选执行.json、out/06_场景逐条.csv
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

# ── 0. 隔离环境（必须在 import backend 之前）────────────────────────────────
HERE = Path(__file__).resolve()
EVID = HERE.parents[1]
ISO = EVID / "out" / "00_iso_06"
PROJ = HERE.parents[3]
PROD_MARKET = PROJ / "数据" / "市场数据" / "db" / "market_v2.sqlite3"

for _p in (ISO / "market", ISO / "paper", ISO / "live", ISO / "batches",
           ISO / "runs", ISO / "locks", ISO / "archive_empty"):
    _p.mkdir(parents=True, exist_ok=True)
(ISO / "gate.json").write_text(
    json.dumps({"allow_new_entries": True, "reason": "SIGNAL-CLOSURE-06 隔离验证",
                "set_by": "evidence_script", "since_ms": 0}, ensure_ascii=False),
    encoding="utf-8")

# 账本 / live / 闸门 / batches 先隔离 —— 这些必须在 `live_store` 首次 import 前就位
os.environ["PAPER_LEDGER_DIR"] = str(ISO / "paper")
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
os.environ["LIVE_DB"] = str(ISO / "live" / "live.sqlite3")
os.environ["PAPER_GATE_FILE"] = str(ISO / "gate.json")
os.environ["EVENT_TREND_BATCH_ROOT"] = str(ISO / "batches")
os.environ["RUNS_DIR"] = str(ISO / "runs")
os.environ["no_proxy"] = "*"
os.environ["WX_HISTORY_SOURCE"] = "hot"
# **MARKET_DB / MARKET_ARCHIVE 先不动**：要先用生产库（冷层 parquet + 热库合并）
# 把合并历史读出来，再据此建隔离行情库，最后才把 MARKET_DB 切到隔离库。
# 踩过的坑：只从热库 `candle_versions` 导出 ⇒ 4h 只剩 45 天（更早的在冷层 parquet），
# bootstrap 直接因 4h 根数不足跳过全部标的，候选一个都出不来。

sys.path.insert(0, str(HERE.parent))
import importlib                                            # noqa: E402
import time                                                 # noqa: E402

cm = importlib.import_module("00_common")
H1, M15 = cm.H1, 900_000
SYMS = ["AIGENSYNUSDT", "IRYSUSDT", "YGGUSDT"]
READ_BACK_DAYS = 200


def build_iso_market(syms, hist, upto_ms, tf15_days=70) -> Path:
    """建独立行情库：schema 从生产库拷贝，1h/4h 用**合并历史**，15m 从生产热库直读。

    为什么 1h/4h 不用热库：热库 `candle_versions` 对这几个标的只保留约 45 天，
    更早在冷层 parquet 里。用合并历史才能让重放深度 2880h 真的可达。
    """
    dst_p = ISO / "market" / "market.sqlite3"
    if dst_p.exists():
        dst_p.unlink()
    src = sqlite3.connect(f"file:{PROD_MARKET}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(dst_p)
    try:
        for o in src.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND type IN ('table','view','index','trigger')").fetchall():
            try:
                dst.execute(o["sql"])
            except sqlite3.Error:
                pass
        dst.commit()

        def put(rows, sym, tf):
            n = 0
            for r in rows:
                dst.execute(
                    "INSERT OR REPLACE INTO candle_versions(exchange,market,symbol,timeframe,"
                    "open_time,close_time,open,high,low,close,volume,quote_volume,trade_count,"
                    "taker_buy_volume,taker_buy_quote_volume,source_id,available_at,is_closed) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("binance", "um", sym, tf, int(r["open_time"]),
                     int(r["close_time"]), float(r["open"]), float(r["high"]),
                     float(r["low"]), float(r["close"]), float(r["volume"]),
                     float(r.get("quote_volume") or 0.0), int(r.get("trade_count") or 0),
                     float(r.get("taker_buy_volume") or 0.0),
                     float(r.get("taker_buy_quote_volume") or 0.0),
                     int(r.get("source_id") or 0), r.get("available_at"),
                     int(r.get("is_closed") or 1)))
                dst.execute(
                    "INSERT OR REPLACE INTO candle_heads(exchange,market,symbol,timeframe,"
                    "open_time,source_id) VALUES(?,?,?,?,?,?)",
                    ("binance", "um", sym, tf,
                     int(r["open_time"]), int(r.get("source_id") or 0)))
                n += 1
            return n

        n = 0
        for sym in syms:
            for tf in ("4h", "1h"):
                n += put(hist[sym][tf], sym, tf)
        # 15m 成交价来源：热库近 70 天（候选时刻都在这个范围内）
        for sym in syms:
            rows = src.execute(
                "SELECT symbol,timeframe,open_time,close_time,open,high,low,close,volume,"
                "quote_volume,trade_count,taker_buy_volume,taker_buy_quote_volume,"
                "source_id,available_at,is_closed FROM candle_versions "
                "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
                "AND is_closed=1 AND close_time<=? AND open_time>=? ORDER BY open_time",
                (sym, int(upto_ms), int(upto_ms) - tf15_days * 86_400_000)).fetchall()
            n += put([dict(r) for r in rows], sym, "15m")
        dst.commit()
        n_head = dst.execute("SELECT COUNT(*) FROM candle_heads").fetchone()[0]
        n_view = dst.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        assert n_view == n_head and n_view > 0, (
            f"candles 视图 {n_view} 行 != candle_heads {n_head} 条（写入 versions {n} 条）")
        per = {f"{s}/{tf}": dst.execute(
            "SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe=?", (s, tf)).fetchone()[0]
            for s in syms for tf in ("4h", "1h", "15m")}
        print(f"[iso] 行情库 {n} 条版本 / 视图 {n_view} 行 {per}")
    finally:
        dst.close()
        src.close()
    return dst_p


def ledger_counts():
    """隔离账本里与本单相关的行数（信号 / 挂单 / 持仓 / 已处理 / 拒单）。"""
    import live_store as L
    out = {}
    con_l = L.live_conn(ro=True)
    try:
        out["live_signal"] = con_l.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        out["live_signal_recovered"] = con_l.execute(
            "SELECT COUNT(*) FROM live_signal WHERE origin='recovered'").fetchone()[0]
        out["signal_uids"] = [r[0] for r in con_l.execute(
            "SELECT signal_uid FROM live_signal ORDER BY signal_uid").fetchall()]
    finally:
        con_l.close()
    con_p = L.paper_conn("3a", ro=True)
    try:
        for t in ("paper_order", "paper_pending", "paper_position",
                  "paper_processed", "paper_blocked", "paper_realized"):
            try:
                out[t] = con_p.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = None
        out["order_status"] = [dict(r) for r in con_p.execute(
            "SELECT order_id, event_uid, symbol, side, qty, price, status, reject_reason, "
            "fill_price, ts FROM paper_order").fetchall()]
        out["position_rows"] = [dict(r) for r in con_p.execute(
            "SELECT position_id, signal_uid, symbol, status, entry_price, entry_ts, "
            "stop_price, qty FROM paper_position").fetchall()]
        out["pending_rows"] = [dict(r) for r in con_p.execute(
            "SELECT pending_id, signal_uid, symbol, status, earliest_fill_ms "
            "FROM paper_pending").fetchall()]
    finally:
        con_p.close()
    return out


def main() -> int:
    # ── 第一步：用**生产库**读合并历史（冷层 parquet + 热库），据此建隔离行情库 ──
    upto_now = cm.last_closed_1h_close_ms()
    hist_all, _ = cm.load_hist(SYMS, upto_now, read_days=READ_BACK_DAYS,
                               keep_from_ms=upto_now - 300 * 24 * H1)
    print(f"[read] 生产合并历史 upto={cm.utc(upto_now)} "
          + ", ".join(f"{s}:4h{len(hist_all[s]['4h'])}/1h{len(hist_all[s]['1h'])}"
                      for s in SYMS))
    build_iso_market(SYMS, hist_all, upto_now)
    # ── 第二步：切到隔离行情库（`market_conn()` 每次调用都按 env 解析 ⇒ 立即生效）──
    os.environ["MARKET_DB"] = str(ISO / "market" / "market.sqlite3")
    os.environ["MARKET_ARCHIVE"] = str(ISO / "archive_empty")

    import unified_runtime as UR
    import live_store as L

    # 先把隔离账本建起来（PAPER_LEDGER_ALLOW_INIT=1 ⇒ 允许受控建档）
    L.ensure_live_schema()
    for arm in ("3a",):
        con = L.paper_conn(arm)
        con.close()
    print(f"[iso] live={os.environ['LIVE_DB']}")
    print(f"[iso] paper={os.environ['PAPER_LEDGER_DIR']}")
    print(f"[iso] market={os.environ['MARKET_DB']}")

    # ── 先找出「已知应触发案例」：AIGENSYNUSDT 最近那个候选 ────────────────
    probe = cm.prod_session(SYMS, hist_all, upto_now, upto_now,
                            replay_hours=2880, advance="end")
    cases = []
    for s in SYMS:
        e = probe.engines.get(s)
        if not e:
            continue
        for c in e.sm.candidates:
            cases.append({"symbol": s, "candidate_id": c.candidate_id,
                          "snapshot_ms": int(c.snapshot_time_ms),
                          "available_ms": int(c.available_at_ms),
                          "exec_earliest_ms": int(c.execution_earliest_ms),
                          "volume_ratio": c.volume_ratio,
                          "breakout_close": c.breakout_close})
    cases.sort(key=lambda x: x["snapshot_ms"])
    print(f"[cases] 深度 2880h 下的已知候选 {len(cases)} 个；"
          f"最早 {cm.utc(cases[0]['snapshot_ms'])} 最新 {cm.utc(cases[-1]['snapshot_ms'])}")
    case = cases[-1]                                  # 最新那个作为主案例
    Tc = case["snapshot_ms"]
    print(f"[case] 主案例 {case['symbol']} snapshot={cm.utc(Tc)} "
          f"exec_earliest={cm.utc(case['exec_earliest_ms'])}")

    results = {}
    rows = []

    def run_scenario(name: str, upto_ms: int, sim_now_ms: int, note: str,
                     advance_to_ms: int | None = None, advance_start_ms: int | None = None):
        """一次「重启 + 路由」：真跑生产 UnifiedRuntime 的 bootstrap → _route_recovered_candidates。"""
        rt = UR.UnifiedRuntime(arm="3a", symbols=SYMS, poll_interval_sec=1.0,
                               lock_dir=ISO / "locks")
        rt.signal_engine.history_provider = cm.MemProvider(hist_all)
        rt.signal_engine.watcher = cm.MemPoolWatcher(hist_all, SYMS)
        rt.signal_engine.replay_ms = 2880 * H1
        rt.signal_engine.history_floor_ms = int(upto_ms) - rt.signal_engine.replay_ms
        rt.signal_engine.history_provider.set_upto(int(upto_ms))
        rt.signal_engine.watcher.set_now(int(upto_ms))
        rt.signal_engine.prepare_history(int(upto_ms), log=lambda *a: None)
        rt.signal_engine.bootstrap(int(upto_ms), log=lambda *a: None)
        bs = dict(rt.signal_engine.bootstrap_stats)
        rec = rt._route_recovered_candidates(now_ms=int(sim_now_ms))
        # 模拟推进（仅用于「恢复后新候选」场景）
        step_res = None
        if advance_to_ms is not None:
            rt.started_at_ms = int(advance_start_ms or upto_ms)
            cur = int(upto_ms)
            while cur < int(advance_to_ms):
                cur += H1
                rt.signal_engine.watcher.set_now(cur)
                step_res = rt.step_once(now_ms=cur)
        snap = {
            "scenario": name, "note": note,
            "upto_utc": cm.utc(upto_ms), "sim_now_utc": cm.utc(sim_now_ms),
            "bootstrap_recovered": bs.get("recovered_candidates"),
            "bootstrap_discarded": bs.get("history_candidates_discarded"),
            "recovered_detail": bs.get("recovered_detail"),
            "route": rec, "step": step_res,
        }
        results[name] = snap
        lc = ledger_counts()
        snap["ledger"] = {k: v for k, v in lc.items() if k != "signal_uids"}
        snap["ledger_signal_uids"] = lc["signal_uids"]
        rows.append({"scenario": name, "note": note,
                     "upto_utc": snap["upto_utc"], "sim_now_utc": snap["sim_now_utc"],
                     "bootstrap_recovered": snap["bootstrap_recovered"],
                     "bootstrap_discarded": snap["bootstrap_discarded"],
                     "route_registered": rec.get("registered"),
                     "route_rejected": rec.get("rejected"),
                     "live_signal": lc["live_signal"],
                     "live_signal_recovered": lc["live_signal_recovered"],
                     "paper_order": lc["paper_order"],
                     "paper_pending": lc["paper_pending"],
                     "paper_position": lc["paper_position"],
                     "paper_processed": lc["paper_processed"]})
        print(f"  [{name}] recovered={snap['bootstrap_recovered']} "
              f"discarded={snap['bootstrap_discarded']} "
              f"registered={rec.get('registered')} rejected={rec.get('rejected')} "
              f"live_signal={lc['live_signal']} order={lc['paper_order']} "
              f"pending={lc['paper_pending']} pos={lc['paper_position']}")
        return rt

    # ── S1 过期历史候选：截止 = 现在，所有候选都早已过时效 ────────────────
    run_scenario("S1_过期历史候选_截止现在", upto_now, upto_now,
                 "预热回放里的候选全部早于 成交时效 ⇒ 应 recovered=0，且账本 0 行")

    # ── S2 停机期间候选（仍在时效内）：重启于候选后 20 分钟 ────────────────
    run_scenario("S2_停机候选仍在时效内", Tc + 20 * 60_000, Tc + 20 * 60_000,
                 "候选 exec_earliest=+15m，重启在 +20m ⇒ 应 recovered>=1 并落库；"
                 "成交 bar 尚未收盘 ⇒ 应登记为挂单")

    # ── S3 同一候选重复重启（幂等）────────────────────────────────────────
    run_scenario("S3_同一候选重复重启", Tc + 20 * 60_000, Tc + 20 * 60_000,
                 "与 S2 完全相同的重启时刻再跑一次 ⇒ 信号/挂单/持仓行数不得增加")

    # ── S4 过期不再补：把重启时刻推后到超过 1h 成交时效 ────────────────────
    late = case["exec_earliest_ms"] + 2 * 3_600_000
    run_scenario("S4_超过时效不补", late, late,
                 "重启时刻已超过 exec_earliest+MAX_FILL_LAG ⇒ 应 recovered=0，账本不新增")

    # ── S5 恢复后新候选：从候选时刻继续推进 6 小时 ─────────────────────────
    rt5 = run_scenario("S5_恢复后新候选", Tc + 20 * 60_000, Tc + 20 * 60_000,
                       "重启后逐 1h 推进 6 小时，新候选应走 origin=live 正常落库",
                       advance_to_ms=Tc + 20 * 60_000 + 6 * H1,
                       advance_start_ms=Tc + 20 * 60_000)
    _ = rt5

    # ── S2b 成交链路：把 epoch 参数前移（**仅测试**），验证「闸门不挡路时」能成交 ──
    # 为什么需要它：本单 526 个真实候选**全部早于 paper epoch**（2026-10-14T01:00Z），
    # 所以 S2 里恢复出来的候选被既有 epoch 闸门正常拒掉（`before_paper_epoch_no_backfill`）
    # —— 那是「不补下过期单」的正确行为，但也意味着**成交链路本身没被验证到**。
    # 这里只把 `open_position(epoch_ms=...)` 这个**既有参数**前移到候选之前，
    # 用来证明「恢复的候选在成交时效内确实能按 FILL_POLICY 挂单并成交」。
    # 生产闸门一个字没改：`route_pending_orders` 仍然不传 epoch_ms。
    # 用**另一个**候选做成交链路验证：同一个 signal_uid 已在 S2 落过一张被 epoch 拒掉的单，
    # `open_position` 会按幂等返回 `duplicate_signal_uid`（这本身也是要证明的性质）。
    case2 = cases[-2] if len(cases) >= 2 else case
    Tc2 = case2["snapshot_ms"]
    s2b = {"symbol": case2["symbol"], "candidate_id": case2["candidate_id"],
           "snapshot_utc": cm.utc(Tc2),
           "epoch_used_utc": cm.utc(Tc2 - 24 * H1), "steps": []}
    import paper_engine as PE
    r1 = PE.open_position(
        "3a", case2["symbol"], signal_uid=case2["candidate_id"], direction="long",
        now_ms=Tc2 + 50 * 60_000, event_time=case2["snapshot_ms"],
        available_at=case2["available_ms"], requested_at=Tc2 + 20 * 60_000,
        # `origin="replay"` 是 `open_position` **既有**的「允许指定时钟」开关
        # （见其 docstring：只有独立回放环境才允许指定 now_ms/requested_at，
        #  日常路径一律用真实挂钟，禁止调用方回填）。
        origin="replay", epoch_ms=Tc2 - 24 * H1)
    s2b["steps"].append({"label": "恢复候选按 FILL_POLICY 成交（请求锚=候选+20m，尝试=+50m）",
                         "now_utc": cm.utc(Tc2 + 50 * 60_000),
                         "ok": r1.get("ok"), "reason": r1.get("reason"),
                         "status": r1.get("status"),
                         "earliest_fill_utc": cm.utc(r1.get("earliest_fill_ms"))})
    print(f"  [S2b] 第一次 ok={r1.get('ok')} reason={r1.get('reason')}")
    # 生产的挂单推进：真实进程时钟到点后由 `advance_pending` 撮合
    adv = PE.advance_pending(T=Tc2 + 60 * 60_000, log=lambda *a: None)
    s2b["advance_pending"] = adv.get("by_arm", {}).get("3a")
    print(f"  [S2b] 挂单推进 {adv.get('by_arm', {}).get('3a')}")
    lc2b = ledger_counts()
    s2b["ledger"] = {k: v for k, v in lc2b.items() if k != "signal_uids"}
    s2b["filled"] = bool(r1.get("ok")) or bool((s2b.get("advance_pending") or {}).get("filled"))
    s2b["n_positions"] = lc2b.get("paper_position") or 0
    results["S2b_成交链路"] = s2b

    # ── 判定 ──────────────────────────────────────────────────────────────
    s1 = results["S1_过期历史候选_截止现在"]
    s2 = results["S2_停机候选仍在时效内"]
    s3 = results["S3_同一候选重复重启"]
    s4 = results["S4_超过时效不补"]
    s5 = results["S5_恢复后新候选"]
    s5_errs = (s5.get("step") or {}).get("errors") or []
    checks = {
        "S1_过期候选零恢复": s1["bootstrap_recovered"] == 0,
        "S1_账本零写入": (s1["ledger"]["live_signal"] == 0
                        and (s1["ledger"]["paper_order"] or 0) == 0),
        "S2_恢复出候选": (s2["bootstrap_recovered"] or 0) >= 1,
        "S2_候选含主案例": any(d.get("candidate_id") == case["candidate_id"]
                             for d in (s2["recovered_detail"] or [])),
        "S2_信号落库": s2["ledger"]["live_signal_recovered"] >= 1,
        "S2_订单已登记": (s2["ledger"]["paper_order"] or 0) >= 1,
        "S3_信号不重复": s3["ledger"]["live_signal"] == s2["ledger"]["live_signal"],
        "S3_订单不重复": (s3["ledger"]["paper_order"] == s2["ledger"]["paper_order"]
                       and s3["ledger"]["paper_pending"] == s2["ledger"]["paper_pending"]
                       and s3["ledger"]["paper_position"] == s2["ledger"]["paper_position"]),
        "S4_超时效零恢复": s4["bootstrap_recovered"] == 0,
        "S4_账本不新增": (s4["ledger"]["live_signal"] == s3["ledger"]["live_signal"]
                       and s4["ledger"]["paper_order"] == s3["ledger"]["paper_order"]),
        "S5_推进无异常": not s5_errs,
        "S2b_成交链路打通": bool(s2b.get("filled")),
        "S2_被epoch闸门拦下": any(
            (o.get("reject_reason") == "before_paper_epoch_no_backfill")
            for o in (s2["ledger"].get("order_status") or [])),
    }
    ok = all(checks.values())
    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "isolation": {k: os.environ.get(k) for k in
                      ("MARKET_DB", "MARKET_ARCHIVE", "PAPER_LEDGER_DIR", "LIVE_DB",
                       "PAPER_GATE_FILE", "EVENT_TREND_BATCH_ROOT", "RUNS_DIR")},
        "symbols": SYMS,
        "state_replay_hours": 2880,
        "known_candidates": cases,
        "main_case": case,
        "scenarios": results,
        "checks": checks,
        "verdict": "PASS" if ok else "FAIL",
        "judgement": ("判据：S1/S4 必须 0 恢复、账本 0 新增（过期不补）；"
                      "S2 必须恢复出主案例并落库登记订单；"
                      "S3 必须与 S2 行数完全相同（不重复发信号/下单）；"
                      "S5 推进无异常。任一条不成立即 FAIL。"),
        "recovery_gate": ("bootstrap 只恢复 `execution_earliest_ms + MAX_FILL_LAG_MS(3600s) >= upto` "
                          "的候选；能否真成交仍由 paper_engine.FILL_POLICY 决定。"),
    }
    cm.dump("06_恢复候选执行.json", out)
    cm.dump_csv("06_场景逐条.csv", rows)
    print(f"[verdict] {out['verdict']}")
    for k, v in checks.items():
        print(f"    {'OK ' if v else 'BAD'} {k}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

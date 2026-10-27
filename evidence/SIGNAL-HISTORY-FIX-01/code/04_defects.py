# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-01｜04 缺陷机械复现（修复前后各跑一次 = 前后证据）。

用法：
    python 04_defects.py --tag before     # 修复前
    python 04_defects.py --tag after      # 修复后

被测对象（全部是**既有实现**，本脚本只驱动与观测，不重算策略条件）：
    `框架/backend/unified_runtime.py`  MultiSymbolSignalEngine / RealSignalEngine
    `策略/WyckoffMaster/event_trend_v0_1/state_machine.py`  EventStateMachine

逐项：
  D1 预热水位记错：`bootstrap` 把「最后一根未处理的 1h」当成已处理
     ⇒ `step()` 的 `has_new` 判 False ⇒ 该根推迟到下一根到达才处理；期间停止则永不处理。
     度量：tail_gap_bars（每标的未处理但被标记已处理的 1h 根数）、
           以及「单独补处理这根 1h 后状态是否改变」（= 展示层此刻的状态有多旧）。
  D2 状态恢复链断在哪一层：逐层实测 export/restore 的完整性与往返等价性。
  D3 bootstrap 硬门槛是否在**当前生产窗口**静默剔除标的（0 个也要如实写 0）。
  D4 坏死行情（4h 全零）导致 ATR=0 静默不建档，展示层看不出是数据坏死。
  D5 已核查并**排除**的怀疑项：4h 建档与 1h 处理的先后顺序，回测与实时是否一致。
  D6 已核查并**排除**的怀疑项：`available_at` 与「下一根 15m 开盘」是否差 15 分钟。

产出：out/04_缺陷复现_<tag>.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

ISO = cm.OUT / "00_isolated_market"
ISO_DB = ISO / "defects_market.sqlite3"


# ══════════════════════════════════════════════════════════════════════
# 0. 独立行情库（只读导出，绝不碰生产库）
# ══════════════════════════════════════════════════════════════════════
def build_isolated(symbols, floor_ms, ceil_ms, force=False):
    ISO.mkdir(parents=True, exist_ok=True)
    if ISO_DB.exists() and not force:
        con = sqlite3.connect(ISO_DB)
        n = con.execute("SELECT COUNT(*) FROM candle_heads").fetchone()[0]
        con.close()
        if n > 0:
            print(f"[iso] reuse {ISO_DB.name} ({n} heads)")
            return ISO_DB
    if ISO_DB.exists():
        ISO_DB.unlink()
    src = cm.market_conn()
    dst = sqlite3.connect(ISO_DB)
    try:
        for name in ("candle_versions", "candle_heads"):
            dst.execute(src.execute("SELECT sql FROM sqlite_master WHERE name=?",
                                    (name,)).fetchone()[0])
        dst.execute(src.execute("SELECT sql FROM sqlite_master WHERE name='candles'").fetchone()[0])
        dst.execute("CREATE INDEX ix_cv ON candle_versions(exchange,market,symbol,timeframe,open_time)")
        dst.execute("CREATE INDEX ix_cv_ct ON candle_versions(exchange,market,symbol,timeframe,close_time)")
        n = 0
        for sym in symbols:
            for tf in ("1h", "4h"):
                rows = src.execute(
                    "SELECT exchange,market,symbol,timeframe,open_time,close_time,open,high,low,"
                    "close,volume,quote_volume,trade_count,taker_buy_volume,taker_buy_quote_volume,"
                    "source_id,available_at,is_closed FROM candle_versions WHERE exchange=? AND market=? "
                    "AND symbol=? AND timeframe=? AND is_closed=1 AND open_time>=? AND close_time<=? "
                    "ORDER BY open_time",
                    (cm.EXCHANGE, cm.MARKET, sym, tf, int(floor_ms), int(ceil_ms))).fetchall()
                for r in rows:
                    dst.execute(
                        "INSERT OR REPLACE INTO candle_versions(exchange,market,symbol,timeframe,"
                        "open_time,close_time,open,high,low,close,volume,quote_volume,trade_count,"
                        "taker_buy_volume,taker_buy_quote_volume,source_id,available_at,is_closed) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(r))
                    dst.execute(
                        "INSERT OR REPLACE INTO candle_heads(exchange,market,symbol,timeframe,"
                        "open_time,source_id) VALUES(?,?,?,?,?,?)",
                        (r["exchange"], r["market"], r["symbol"], r["timeframe"],
                         r["open_time"], r["source_id"]))
                    n += 1
        dst.commit()
        n_head = dst.execute("SELECT COUNT(*) FROM candle_heads").fetchone()[0]
        chk = dst.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        assert chk == n_head and chk > 0, f"candles 视图 {chk} != heads {n_head}"
        print(f"[iso] {len(symbols)} symbols, {n} bars -> {ISO_DB.name}")
    finally:
        dst.close()
        src.close()
    return ISO_DB


def sn(sm):
    """状态机可比较快照（只取报告要引用的字段）。"""
    ev = sm.active_event
    return {
        "current_state": ev.current_state.value if ev else "IDLE",
        "event_id": ev.event_id if ev else None,
        "created_at_ms": ev.created_at_ms if ev else None,
        "sweep_time_ms": getattr(ev, "sweep_time_ms", None) if ev else None,
        "retest_time_ms": getattr(ev, "retest_time_ms", None) if ev else None,
        "n_transitions": len(sm.transitions),
        "n_candidates": len(sm.candidates),
        "n_completed": len(sm.completed_events),
        "cooling_until_ms": sm.cooling_until_ms,
        "last_processed_1h": (max(sm.processed_1h_bar_opens)
                              if sm.processed_1h_bar_opens else None),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="before")
    ap.add_argument("--rebuild-iso", action="store_true")
    args = ap.parse_args()

    # ── 标的集合：三路对比保留的 23 个 ∪ 4 个已知历史候选 ∪ 3 个坏死数据标的 ──
    link = json.loads((cm.OUT / "05_三路衔接对比.json").read_text(encoding="utf-8"))
    base = list(link["symbols_picked"])
    known_cands = [c["symbol"] for c in
                   json.loads((cm.OUT / "03_漏斗汇总.json").read_text(encoding="utf-8"))["candidates"]]
    dead = ["1000000BOBUSDT", "PROMPTUSDT", "PUMPBTCUSDT"]
    syms = []
    for s in base + known_cands + dead:
        if s not in syms:
            syms.append(s)

    con = cm.market_conn()
    try:
        T1 = int(con.execute("SELECT MAX(close_time) FROM candles WHERE exchange=? AND market=? "
                             "AND timeframe='1h'", (cm.EXCHANGE, cm.MARKET)).fetchone()[0])
        data_start = int(con.execute(
            "SELECT MIN(open_time) FROM candles WHERE exchange=? AND market=? AND timeframe='1h'",
            (cm.EXCHANGE, cm.MARKET)).fetchone()[0])
    finally:
        con.close()

    build_isolated(syms, floor_ms=data_start, ceil_ms=T1 + cm.H1, force=args.rebuild_iso)

    # ── 注入隔离环境（必须在 import unified_runtime 之前）──────────────────
    os.environ["MARKET_DB"] = str(ISO_DB)
    os.environ["LIVE_DIR"] = str(ISO / "live")
    os.environ["PAPER_LEDGER_DIR"] = str(ISO / "paper")
    os.environ["PAPER_LOCK_DIR"] = str(ISO / "locks")
    (ISO / "live").mkdir(parents=True, exist_ok=True)
    for m in ("live_store", "unified_runtime"):
        sys.modules.pop(m, None)
    import unified_runtime as UR
    assert str(UR.L.MARKET_DB) == str(ISO_DB), f"MARKET_DB 未注入: {UR.L.MARKET_DB}"
    from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine

    res = {
        "ticket": "SIGNAL-HISTORY-FIX-01",
        "stage": f"defects_{args.tag}",
        "generated_at_utc": cm.utc_now(),
        "config_identity": cm.config_identity(),
        "isolated_market_db": str(ISO_DB),
        "symbols": syms,
        "T1_utc": cm.iso(T1),
        "data_start_utc": cm.iso(data_start),
        "defects": {},
        "cleared": {},
    }

    def fresh_engine():
        e = UR.MultiSymbolSignalEngine(syms)
        e.history_floor_ms = data_start
        return e

    # ══════════════════════════════════════════════════════════════════
    # D1 预热水位记错（最后一根未处理的 1h 被当成已处理）
    # ══════════════════════════════════════════════════════════════════
    eng = fresh_engine()
    eng.bootstrap(T1, log=lambda *a: None)
    assert eng.engines, f"bootstrap 0 就绪: {eng.skipped_reasons}"
    latest_1h = eng.watcher.latest_open_by_symbol("1h")
    latest_4h = eng.watcher.latest_open_by_symbol("4h")

    d1_rows = []
    for sym, e in eng.engines.items():
        sm = e.sm
        last_proc = max(sm.processed_1h_bar_opens) if sm.processed_1h_bar_opens else None
        eng_mark = eng.last_1h_open.get(sym)
        gap = []
        if last_proc is not None and eng_mark is not None and eng_mark > last_proc:
            t = last_proc + cm.H1
            while t <= eng_mark:
                gap.append(t)
                t += cm.H1
        # step() 此刻会怎么判（原样照抄 :593-596 的判据）
        n1, n4 = latest_1h.get(sym), latest_4h.get(sym)
        has_new = ((n1 is not None and n1 > eng.last_1h_open.get(sym, 0))
                   or (n4 is not None and n4 > eng.last_4h_open.get(sym, 0)))
        d1_rows.append({"symbol": sym, "last_processed_1h_utc": cm.iso(last_proc),
                        "engine_marked_1h_utc": cm.iso(eng_mark),
                        "db_latest_1h_utc": cm.iso(n1),
                        "tail_gap_bars": len(gap),
                        "tail_gap_utc": [cm.iso(x) for x in gap],
                        "step_has_new_now": bool(has_new)})

    # 单独补处理那根被跳过的 1h（绕过 has_new 闸门），看状态是否改变
    before = {s: sn(e.sm) for s, e in eng.engines.items()}
    for sym in list(eng.engines):
        eng.engines[sym].process_market(T1, UR._SymbolWatcher(eng.watcher, sym))
    after = {s: sn(e.sm) for s, e in eng.engines.items()}
    changed = []
    for sym in eng.engines:
        b, a = before[sym], after[sym]
        diff = {k: [b[k], a[k]] for k in b if b[k] != a[k]}
        if diff:
            changed.append({"symbol": sym, "diff": diff,
                            "diff_fields": sorted(diff.keys())})
    # 「只差 last_processed_1h」是必然的（那根就是刚补处理的），不算结构性变化；
    # 真正的危害是**阶段/事件本身**变了 ⇒ 展示层此刻显示的是过期阶段。
    struct = [c for c in changed if set(c["diff_fields"]) - {"last_processed_1h"}]

    n_gap = sum(1 for r in d1_rows if r["tail_gap_bars"] > 0)
    res["defects"]["D1_bootstrap_watermark"] = {
        "what": ("bootstrap 用 `last_1h_open[sym]=b1[-1].open_time` 记账，但回放只按 4h 收盘时刻"
                 "驱动 process_market ⇒ 最后一根 4h 收盘之后才开盘的 1h 从未被喂进状态机，"
                 "却被标成已处理 ⇒ step() 的 has_new 判 False ⇒ 该根推迟到下一根 1h 到达才处理。"),
        "where": "框架/backend/unified_runtime.py:525（记账）、:593-596（has_new 判据）",
        "symbols_checked": len(d1_rows),
        "symbols_with_tail_gap": n_gap,
        "tail_gap_bars_total": sum(r["tail_gap_bars"] for r in d1_rows),
        "tail_gap_bars_max": max((r["tail_gap_bars"] for r in d1_rows), default=0),
        "step_has_new_true_now": sum(1 for r in d1_rows if r["step_has_new_now"]),
        "state_changed_if_gap_bar_processed": len(changed),
        "state_changed_structurally": len(struct),
        "structural_changes": [{"symbol": c["symbol"], "diff": c["diff"]} for c in struct],
        "state_changes": changed,
        "rows": d1_rows,
        "verdict": ("DEFECT_PRESENT" if n_gap else "FIXED"),
        "harm": ("被推迟的那根 1h 里的状态迁移与候选，在下一根到达前不体现；"
                 "期间进程停止/重启则该根永不处理（重启会全量重放，故仅当不再重启时成立）。"),
    }

    # ══════════════════════════════════════════════════════════════════
    # D2 状态恢复链逐层实测
    # ══════════════════════════════════════════════════════════════════
    layers = {}
    # L1 EventStateMachine 往返
    sm0 = list(eng.engines.values())[0].sm
    st1 = sm0.export_state()
    sm1 = EventStateMachine.restore_state(json.loads(json.dumps(st1, default=str)))
    st2 = sm1.export_state()
    layers["L1_EventStateMachine"] = {
        "export_keys": sorted(st1.keys()),
        "roundtrip_identical": json.dumps(st1, sort_keys=True, default=str)
                               == json.dumps(st2, sort_keys=True, default=str),
        "instance_attrs": sorted(k for k in sm0.__dict__ if not k.startswith("__")),
        "attrs_not_in_export": sorted(k for k in sm0.__dict__
                                      if not k.startswith("__") and k not in st1),
    }
    # L2 RealSignalEngine 往返
    re0 = list(eng.engines.values())[0]
    rs = re0.export_state()
    re1 = UR.RealSignalEngine(symbol=re0.symbol, enable_oi_filter=re0.enable_oi_filter,
                              config=re0.config)
    re1.restore_state(rs)
    lost = [k for k in re0.__dict__ if k != "sm" and getattr(re0, k) != getattr(re1, k, "<missing>")]
    layers["L2_RealSignalEngine"] = {
        "export_keys": sorted(rs.keys()),
        "attrs": sorted(k for k in re0.__dict__ if not k.startswith("__")),
        "attrs_not_restored": lost,
        "roundtrip_identical": not lost,
    }
    # L3 MultiSymbolSignalEngine
    layers["L3_MultiSymbolSignalEngine"] = {
        "has_restore_state": hasattr(UR.MultiSymbolSignalEngine, "restore_state"),
        "export_keys": sorted(eng.export_state().keys()),
        "per_symbol_state_in_export": False if "engines" not in eng.export_state() else True,
        "note": ("export_state 只导出 symbols/ready/bootstrap，**不含任何逐标的状态机状态**；"
                 "且类上没有 restore_state ⇒ 多标层无法冷热重启。"),
    }
    # L4 UnifiedRuntime 启动恢复
    import inspect
    src = inspect.getsource(UR.UnifiedRuntime._recover_state_on_startup)
    layers["L4_UnifiedRuntime_startup"] = {
        "recover_fn_mentions_signal_engine": ("signal_engine" in src),
        "recover_fn_lines": [ln.strip() for ln in src.split("\n") if ln.strip()][:14],
        "note": "启动只数纸面账本行数，不碰信号引擎状态；信号状态一律靠 bootstrap 全量重放。",
    }
    # L2b 恢复后继续推进是否等价（用内建机制，而非重放）
    def _ms(iso_s):
        return int(cm.dt.datetime.strptime(iso_s, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=cm.UTC).timestamp() * 1000)

    T0 = _ms(link["windows"]["T0_utc"])

    def run_to(t_end, restore_at=None):
        """两条路径唯一差异 = 中途有没有换引擎（用内建 export/restore）。

        推进方式：**逐小时直接调 `process_market`**（而不是 `step`），因为本脚本的
        独立库里 bar 是一次性全量可见的，`step` 的 `has_new` 判据依赖「库里的最新 bar」，
        在这里不会随时间推进 ⇒ 会把「路径差异」和「水位差异」混在一起。`process_market`
        内部按 `close_time <= upto_ms` 取数，逐小时调用即等价于真实逐根追加。
        """
        e = fresh_engine()
        e.bootstrap(T0, log=lambda *a: None)

        def drive(t):
            for s, e2 in e.engines.items():
                e2.process_market(t, UR._SymbolWatcher(e.watcher, s))

        t = T0
        while t <= t_end:
            if restore_at is not None and t == restore_at:
                snap = {s: json.loads(json.dumps(e2.export_state(), default=str))
                        for s, e2 in e.engines.items()}
                e = UR.MultiSymbolSignalEngine(syms)
                e.history_floor_ms = data_start
                for s, st in snap.items():
                    e2 = UR.RealSignalEngine(symbol=s, enable_oi_filter=False)
                    e2.restore_state(st)
                    # restore_state **不恢复** history_floor_ms（见 L2）⇒ 必须手工补，
                    # 否则下一次 process_market 会从 0 开始扫全表。
                    e2.history_floor_ms = data_start
                    e.engines[s] = e2
            drive(t)
            t += cm.H1
        return e

    T_end = T1
    Tm = _ms(link["windows"]["Tm_restart_utc"])
    ea = run_to(T_end)
    eb = run_to(T_end, restore_at=Tm)
    st_a = {s: sn(e.sm) for s, e in ea.engines.items()}
    st_b = {s: sn(e.sm) for s, e in eb.engines.items()}
    diffs = [{"symbol": s, "diff": {k: [st_a[s][k], st_b[s][k]]
                                    for k in st_a[s] if st_a[s][k] != st_b[s][k]}}
             for s in st_a if st_a[s] != st_b.get(s)]
    layers["L2b_continue_after_restore"] = {
        "restore_at_utc": cm.iso(Tm), "t_end_utc": cm.iso(T_end),
        "symbols": len(st_a), "n_diff": len(diffs), "identical": not diffs,
        "diffs": diffs[:20],
        "note": ("用 `RealSignalEngine.export_state/restore_state` 内建机制在中途换引擎，"
                 "与不换引擎跑到同一截止时刻对比。"),
    }
    res["defects"]["D2_state_recovery_chain"] = {
        "what": "「历史状态恢复」这条链逐层实测：哪一层能导出/恢复，哪一层是桩。",
        "layers": layers,
        "verdict": ("BROKEN_AT_MULTI_SYMBOL_LAYER"
                    if not layers["L3_MultiSymbolSignalEngine"]["has_restore_state"]
                    else "OK"),
        "harm": ("进程重启后没有任何状态被恢复，只能靠 bootstrap 全量重放；"
                 "副作用：`transition_counts`（since_start）与 `candidates_since_start` 归零 ⇒ "
                 "「信号 0」在每次重启后都从 0 重新计数，无法跨重启累积。"),
    }

    # ══════════════════════════════════════════════════════════════════
    # D3 bootstrap 硬门槛是否静默剔除标的（本窗口）
    # ══════════════════════════════════════════════════════════════════
    res["defects"]["D3_bootstrap_hard_gate"] = {
        "what": "bootstrap 硬门槛 4h>=box_bars_4h+8(=50) 且 1h>=200，达不到即整币跳过。",
        "isolated_universe": len(syms),
        "ready": eng.bootstrap_stats.get("ready"),
        "skipped": eng.bootstrap_stats.get("skipped"),
        "skipped_reasons": eng.bootstrap_stats.get("skipped_reasons"),
        "verdict": ("NOT_TRIGGERED_THIS_WINDOW" if not eng.bootstrap_stats.get("skipped")
                    else "TRIGGERED"),
        "latent_risk": ("新上市标的在预热窗口内达不到 200 根 1h 时会被静默跳过，"
                        "且 skipped_reasons 只进 bootstrap_stats，不进 state_counter / 前端 ⇒ "
                        "展示为「无信号」而不是「未就绪」。"),
    }

    # ══════════════════════════════════════════════════════════════════
    # D4 坏死行情 → ATR=0 → 静默不建档
    # ══════════════════════════════════════════════════════════════════
    import numpy as np
    from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series
    d4 = []
    c2 = sqlite3.connect(ISO_DB)
    c2.row_factory = sqlite3.Row
    for sym in syms:
        rows = c2.execute(
            "SELECT open_time,high,low,close,volume FROM candle_versions WHERE symbol=? AND "
            "timeframe='4h' ORDER BY open_time DESC LIMIT 42", (sym,)).fetchall()
        rows = list(reversed(rows))
        if len(rows) < 42:
            d4.append({"symbol": sym, "bars_4h": len(rows), "note": "不足 42 根"})
            continue
        h = np.array([float(r["high"]) for r in rows])
        l = np.array([float(r["low"]) for r in rows])
        c = np.array([float(r["close"]) for r in rows])
        atr = compute_wilder_atr_series(h, l, c, period=14)
        v = float(atr[-1]) if len(atr) else 0.0
        zero_bars = int(sum(1 for r in rows if float(r["volume"]) == 0.0
                            and float(r["high"]) == float(r["low"])))
        d4.append({"symbol": sym, "bars_4h": len(rows), "atr_last42": v,
                   "atr_ok": bool(np.isfinite(v) and v > 0),
                   "all_zero_bars_in_last42": zero_bars,
                   "last_4h_open_utc": cm.iso(rows[-1]["open_time"])})
    c2.close()
    bad = [r for r in d4 if not r.get("atr_ok")]
    res["defects"]["D4_dead_bars_atr_zero"] = {
        "what": ("4h 全零（volume=0、high==low==close）⇒ compute_wilder_atr_series 返回 0.0 ⇒ "
                 "`if np.isfinite(value) and value > 0` 不成立 ⇒ check_and_form_box 静默不执行 ⇒ "
                 "当前阶段显示为 IDLE_ATR_INVALID，看不出是数据坏死。"),
        "where": "框架/backend/unified_runtime.py:315-318",
        "symbols_checked": len(d4),
        "symbols_atr_zero": len(bad),
        "atr_zero_symbols": [r["symbol"] for r in bad],
        "detail": d4,
        "verdict": "DEFECT_PRESENT" if bad else "NOT_TRIGGERED",
    }

    # ══════════════════════════════════════════════════════════════════
    # D5（已排除）4h 建档 vs 1h 处理 的先后顺序
    # ══════════════════════════════════════════════════════════════════
    # 实时：process_market 把事件按 (close_time, kind) 排序，kind=0 是 4h ⇒ 同一收盘时刻 4h 先。
    # 回测：runner.py 先 `if t_close % 4h == 0: check_and_form_box(...)`，再 process_1h_bar。
    # ⇒ 两者都是「先建档、再处理同一时刻的 1h」。
    res["cleared"]["D5_4h_vs_1h_ordering"] = {
        "realtime": "process_market: events sorted by (close_time, kind)，kind=0(4h) 先于 kind=1(1h)",
        "backtest": "runner.py:409/432 先 check_and_form_box(t_close)，再 process_1h_bar(t_open=t_close-1h)",
        "same_order": True,
        "conclusion": "顺序一致，不是缺陷；回测与实时的差异只来自 ATR 计算口径（见 02）。",
    }

    # ══════════════════════════════════════════════════════════════════
    # D6（已排除）available_at 与「下一根 15m 开盘」
    # ══════════════════════════════════════════════════════════════════
    from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    cands = [c for e in eng.engines.values() for c in e.sm.candidates]
    sample = []
    for c in cands[:3]:
        sample.append({"symbol": c.symbol,
                       "snapshot_time_utc": cm.iso(c.snapshot_time_ms),
                       "available_at_utc": cm.iso(c.available_at_ms),
                       "execution_earliest_utc": cm.iso(c.execution_earliest_ms),
                       "delta_available_to_exec_ms": int(c.execution_earliest_ms) - int(c.available_at_ms)})
    res["cleared"]["D6_available_at_vs_execution"] = {
        "candidate_samples": sample,
        "paper_engine_rule": ("paper_engine.py:103-107  order_request_ms=max(event_time, available_at)；"
                              "earliest_fill_ms=order_request_ms+15m；"
                              "成交 bar=第一根 open_time>=earliest_fill_ms 的已闭合 15m bar"),
        "backtest_rule": "contracts.py execution_earliest_ms = snapshot_time_ms + 15m（state_machine.py:616）",
        "conclusion": ("两者都等于「1h 收盘 + 15m」，口径一致，不是缺陷。"
                       "`available_at` 只是决策可得时刻（=1h 收盘），不承担执行时点语义。"),
    }

    # ── 落盘 ──────────────────────────────────────────────────────────
    p = cm.write_json(f"04_缺陷复现_{args.tag}.json", res)
    print("\n==== SUMMARY", args.tag, "====")
    _d1 = res["defects"]["D1_bootstrap_watermark"]
    print("D1 tail_gap:", _d1["symbols_with_tail_gap"], "/", _d1["symbols_checked"],
          "max_gap", _d1["tail_gap_bars_max"],
          "has_new_true", _d1["step_has_new_true_now"],
          "struct_changed", _d1["state_changed_structurally"],
          "verdict", _d1["verdict"])
    print("D2 L3 has_restore:", layers["L3_MultiSymbolSignalEngine"]["has_restore_state"],
          "L2 lost:", layers["L2_RealSignalEngine"]["attrs_not_restored"],
          "L2b identical:", layers["L2b_continue_after_restore"]["identical"],
          "n_diff", layers["L2b_continue_after_restore"]["n_diff"])
    print("D3 ready/skipped:", eng.bootstrap_stats.get("ready"), eng.bootstrap_stats.get("skipped"),
          res["defects"]["D3_bootstrap_hard_gate"]["verdict"])
    print("D4 atr_zero:", res["defects"]["D4_dead_bars_atr_zero"]["atr_zero_symbols"])
    print("->", p)


if __name__ == "__main__":
    main()

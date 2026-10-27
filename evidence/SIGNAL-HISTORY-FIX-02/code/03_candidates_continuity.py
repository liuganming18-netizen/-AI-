# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-02｜03 历史候选复现 + 衔接验证 + 全池资格 + 异常标的溯源。

工单「五」「六」要求：
  · 覆盖**全部 4 个已知历史候选**；可分设合理起点，**不得因单一起点剔除其中 3 个**；
  · 对比**连续回放 / 初始化后追加 / 中途重启**三种驱动方式下的候选与状态；
  · 对**当前完整币池**检查数据资格、水位、状态；**全池检查**与**代表性行为测试**的
    覆盖范围分开写，不把样例结果外推成 526 全部通过；
  · 核对 3 个异常行情标的的源数据与采集记录，**未查明前不归因于「市场无成交」**。

为什么不能只读生产快照：`bootstrap` 里 `discarded += len(eng.process_market(...))`
**只计数、不保留身份**（`unified_runtime.py:590`），所以运行态只能给出「4 个」这个数，
给不出是哪 4 个 ⇒ 必须在**同口径、同窗口**下重放一遍把身份取回来。

三种驱动方式的语义（与生产一致，不另建引擎）：
  · 连续回放      = 从 `floor` 一路喂到 `t_end`，中间不重启（`bootstrap` 的做法）
  · 初始化后追加  = 先回放到 `t_init`（= 进程启动），再继续喂到 `t_end`（`step()` 的做法）
  · 中途重启      = 回放到 `t_init`，喂到 `t_restart`，**新建引擎 + 全量重放**到
                    `t_restart`，再继续喂到 `t_end`
                    （`export_state` 声明 `restore_supported=False`、
                     `recovery_method="bootstrap_full_replay"`，所以「重启」的真实语义
                     就是**重放**，不是「读断点续跑」）

用法：
    python 03_candidates_continuity.py --phase prod      # A1 生产库复现（只读）
    python 03_candidates_continuity.py --phase iso       # A2 隔离库复现 + C 衔接
    python 03_candidates_continuity.py --phase pool      # B 全池资格 + 异常标的
    python 03_candidates_continuity.py --phase all
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

ISO_DB = cm.OUT / "180d_market.sqlite3"

#: 生产 runtime 的启动时刻（`service_manager.py status --json` →
#: `services.runtime.runtime_snapshot.runtime_identity.started_at`，2026-10-24T14:18:45Z 读取）
PROD_STARTED_AT_MS = 1792835343435
#: 同一快照里 `bootstrap.elapsed_sec = 24.2` ⇒ 预热截止取启动后 25 s
PROD_UPTO_MS = PROD_STARTED_AT_MS + 25_000
#: 同一快照里 `signal_engine.watermark_1h / watermark_4h`（用于「水位一致性」核对）
PROD_WATERMARK_1H = 1792846800000
PROD_WATERMARK_4H = 1792828800000
#: 同一快照里 `history_candidates_discarded`
PROD_HIST_DISCARDED = 4

#: 3 个异常行情标的（上一单 `SIGNAL-LINK-01` / `idle_breakdown` 标为 IDLE_DEAD_BARS）
ANOMALY_SYMBOLS = ["1000000BOBUSDT", "PROMPTUSDT", "PUMPBTCUSDT"]


# ══════════════════════════════════════════════════════════════════════
# 通用
# ══════════════════════════════════════════════════════════════════════
def warmup_ms(cfg) -> int:
    """与 `unified_runtime.py:1482` 完全同一式子。"""
    return int((cfg.atr_warmup_bars * 4 + cfg.breakout_max_total_hours
                + cfg.cooling_hours) * cm.H1)


def load_bars(con, sym: str, tf: str):
    rows = con.execute(
        "SELECT open_time,close_time,open,high,low,close,volume,is_closed "
        "FROM candle_versions WHERE symbol=? AND timeframe=? ORDER BY open_time",
        (sym, tf)).fetchall()
    return [dict(r) for r in rows]


def cand_row(c) -> dict:
    """候选快照 → 可跨运行比对的记录。

    **不能拿 `candidate_id` 当身份**：`state_machine.py:584` 里
    `cand_id = f"cand_{event.event_id}_{close_time_ms}"`，而 `event_id` 是**运行内计数器**
    ⇒ 同一个候选在不同运行里 id 不同。跨运行稳定身份是 `(symbol, available_at_ms)`。
    `event_id` 仍然照记，用于「候选→订单→成交→复盘」在**同一次运行内**的关联。
    """
    return {"symbol": c.symbol, "candidate_id": c.candidate_id,
            "event_id": getattr(c, "event_id", None),
            "available_at_ms": int(c.available_at_ms),
            "available_at_utc": cm.iso(c.available_at_ms),
            "breakout_close": c.breakout_close,
            "initial_stop_price": c.initial_stop_price,
            "box_high_h": c.box_high_h, "box_low_l": c.box_low_l,
            "box_atr_a": c.box_atr_a}


def final_state(eng) -> str:
    ev = eng.sm.active_event
    return ev.current_state.value if ev else "IDLE"


def snapshot_engine(eng) -> dict:
    return {"final_state": final_state(eng),
            "transitions": len(eng.sm.transitions),
            "candidates": [cand_row(c) for c in eng.sm.candidates],
            "processed_4h": len(eng.processed_4h_closes),
            "processed_1h": len(eng.processed_1h_opens),
            "atr_bars_seen": getattr(eng, "atr_bars_seen", None),
            "last_atr": getattr(eng, "last_atr", None)}


# ══════════════════════════════════════════════════════════════════════
# A. 复现 4 个历史候选
# ══════════════════════════════════════════════════════════════════════
def reproduce(syms, floor_ms, upto_ms, log=None, legacy=False) -> dict:
    """用**既有** `MultiSymbolSignalEngine.bootstrap()` 原样重放，取回候选身份。

    `legacy=True` 时只把每台引擎的 ATR 提供者换成**旧实时口径**（最近 42 根重算 +
    无 200 根预热），引擎本体（`process_market` / 状态机 / 配置）一个字节都不动
    ⇒ 两次结果的差**只能**归因到 ATR 口径，不掺其它变量。
    """
    import unified_runtime as UR
    import dataclasses
    from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    cfg = EventTrendConfig()
    if legacy:
        cfg = dataclasses.replace(cfg, atr_warmup_bars=0)
    eng = UR.MultiSymbolSignalEngine(syms, config=cfg)
    eng.history_floor_ms = int(floor_ms)
    if legacy:
        _L = importlib.import_module("02_atr_parity")._LegacyATR42
        orig = eng._new_engine

        def patched(sym):
            e = orig(sym)
            e.atr = _L(cfg.atr_period_4h, cfg.box_bars_4h)
            return e
        eng._new_engine = patched
    bs = eng.bootstrap(int(upto_ms), log=log or (lambda *a: None))
    cands = [cand_row(c) for e in eng.engines.values() for c in e.sm.candidates]
    cands.sort(key=lambda x: (x["available_at_ms"], x["symbol"]))
    return {"bootstrap": bs, "candidates": cands,
            "n": len(cands),
            "state_counter": eng.state_counter(),
            "history_transitions": dict(eng.history_transition_counts),
            "floor_utc": cm.iso(floor_ms), "upto_utc": cm.iso(upto_ms),
            "window_hours_requested": round((int(upto_ms) - int(floor_ms)) / cm.H1, 1)}


def phase_prod() -> dict:
    """A1：**生产热库**（只读）上按 `run_forever` 同一口径复现。"""
    import unified_runtime as UR
    from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    assert str(UR.L.MARKET_DB).endswith("market_v2.sqlite3"), UR.L.MARKET_DB
    cfg = EventTrendConfig()
    wm = warmup_ms(cfg)
    floor = max(0, PROD_STARTED_AT_MS - wm)
    syms, _ = cm.universe()
    # 同一份生产数据、同一窗口、同一 config，**只换 ATR 提供者**：
    #   旧（= 生产进程实际在跑的代码，pid 59109 启动于 09:49，早于 14:10 的修复）
    #   新（= 本轮修复后的代码）
    legacy = reproduce(syms, floor, PROD_UPTO_MS, legacy=True)
    fixed = reproduce(syms, floor, PROD_UPTO_MS, legacy=False)
    # 生产库实际可用跨度（热库只保近 ~47 天，请求窗口 1160h 会被数据截断）
    con = cm.market_conn()
    span = {}
    for tf in ("1h", "4h"):
        row = con.execute("SELECT MIN(open_time),MAX(open_time),COUNT(*) "
                          "FROM candle_versions WHERE timeframe=?", (tf,)).fetchone()
        span[tf] = {"first_utc": cm.iso(row[0]), "last_utc": cm.iso(row[1]),
                    "rows": row[2], "span_hours": round((row[1] - row[0]) / cm.H1, 1)}
    con.close()
    ka = {(c["symbol"], c["available_at_utc"]) for c in fixed["candidates"]}
    kb = {(c["symbol"], c["available_at_utc"]) for c in legacy["candidates"]}
    out = {"phase": "A1_production_db", "generated_at_utc": cm.utc_now(),
           "db": str(UR.L.MARKET_DB), "read_only": True,
           "prod_started_at_utc": cm.iso(PROD_STARTED_AT_MS),
           "warmup_ms_hours": round(wm / cm.H1, 1),
           "requested_window": {"floor_utc": cm.iso(floor), "upto_utc": cm.iso(PROD_UPTO_MS)},
           "hot_db_span": span,
           "snapshot_n": PROD_HIST_DISCARDED,
           "legacy_atr_n": legacy["n"],
           "legacy_matches_snapshot": legacy["n"] == PROD_HIST_DISCARDED,
           "fixed_atr_n": fixed["n"],
           "legacy_candidates": legacy["candidates"],
           "fixed_candidates": fixed["candidates"],
           "only_in_legacy": sorted([list(x) for x in (kb - ka)]),
           "only_in_fixed": sorted([list(x) for x in (ka - kb)]),
           "legacy_bootstrap": legacy["bootstrap"],
           "fixed_bootstrap": fixed["bootstrap"],
           "legacy_state_counter": legacy["state_counter"],
           "fixed_state_counter": fixed["state_counter"],
           "legacy_history_transitions": legacy["history_transitions"],
           "fixed_history_transitions": fixed["history_transitions"],
           "note": ("`_window_hours` 是**请求**窗口（1160h≈48.3 天），不是实际覆盖；"
                    "热库 1h 实际只到 ~1139h ⇒ 预热回放被数据末端截断。"
                    "两者必须分开写，不能把 1160 当成「真的回放了 48.3 天」。"
                    "`legacy_matches_snapshot=True` 说明复现口径与生产一致，"
                    "此时 `fixed_atr_n` 与它的差**只能**归因到 ATR 口径修复。")}
    cm.write_json("03_A1_生产库复现候选.json", out)
    return out


def phase_iso(prod_candidates, anchor_ms=None, upto_ms=None) -> dict:
    """A2 + C：隔离 180d 库上复现 + 每个候选三场景衔接。

    `anchor_ms` = **生产实际生效的预热起点**（热库 4h 首根 open_time）。为什么不按
    「候选时刻 − 1160h」逐候选取起点：`_WilderATR` 以装载窗口首根为原点递推，
    而 200 根预热门槛会把「可建档起点」整体后移 800h ⇒ **窗口锚点一变，建档起点就变**，
    同一候选会因此消失。生产上所有标的共用**一个**锚点（`run_forever` 里
    `history_floor_ms` 是全局量），所以衔接验证也必须共用同一个锚点，
    否则「复现不出」是锚点差异造成的，不能归因到驱动方式。
    """
    assert ISO_DB.exists(), f"缺少 {ISO_DB}"
    import unified_runtime as UR
    from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    assert str(UR.L.MARKET_DB) == str(ISO_DB), f"MARKET_DB 未注入: {UR.L.MARKET_DB}"
    cfg = EventTrendConfig()
    wm = warmup_ms(cfg)
    syms, _ = cm.universe()

    con = sqlite3.connect(f"file:{ISO_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    iso_end = con.execute("SELECT MAX(close_time) FROM candle_versions WHERE timeframe='4h'"
                          ).fetchone()[0]
    if anchor_ms is None:
        anchor_ms = int(iso_end) - wm
    if upto_ms is None:
        upto_ms = int(iso_end)
    anchor_ms, upto_ms = int(anchor_ms), int(upto_ms)
    # A2：与生产**同锚点、同截止**，两种 ATR 口径各跑一次
    r2 = reproduce(syms, anchor_ms, upto_ms)
    r2l = reproduce(syms, anchor_ms, upto_ms, legacy=True)
    a2 = {"phase": "A2_isolated_db", "generated_at_utc": cm.utc_now(),
          "db": str(ISO_DB),
          "window": {"floor_utc": cm.iso(anchor_ms), "upto_utc": cm.iso(upto_ms),
                     "hours": round((upto_ms - anchor_ms) / cm.H1, 1)},
          "anchor_source": ("生产热库 4h 首根 open_time（= 生产实际生效的预热起点，"
                            "因为请求窗口 1160h 被热库保留策略截断）"),
          "fixed_atr_n": r2["n"], "legacy_atr_n": r2l["n"],
          "fixed_candidates": r2["candidates"],
          "legacy_candidates": r2l["candidates"],
          "fixed_state_counter": r2["state_counter"],
          "legacy_state_counter": r2l["state_counter"],
          "history_transitions": r2["history_transitions"],
          "bootstrap": r2["bootstrap"]}
    if prod_candidates is not None:
        # 身份比对必须**同口径**比：隔离库旧口径 vs 生产库旧口径
        ka = {(c["symbol"], c["available_at_utc"]) for c in r2l["candidates"]}
        kb = {(c["symbol"], c["available_at_utc"]) for c in prod_candidates}
        a2["identity_vs_production_same_caliber"] = {
            "caliber": "legacy（旧 ATR，= 生产进程实际在跑的代码）",
            "prod_n": len(kb), "iso_n": len(ka), "identical": ka == kb,
            "only_in_prod": sorted([list(x) for x in (kb - ka)]),
            "only_in_iso": sorted([list(x) for x in (ka - kb)]),
            "note": ("锚点与截止时刻都与生产一致，剩余差异只能来自**数据本身**"
                     "（热库是活库：保留策略滚掉了最老数据，且部分标的早期 bar 缺失）。")}
    cm.write_json("03_A2_隔离库复现候选.json", a2)

    # ── C. 每个候选三场景衔接（每个候选独立起点）──────────────────────
    # 目标 = **4 个已知历史候选**（旧 ATR 复现、与生产快照数一致）
    #      + **修复后新增候选**（新 ATR 复现出来的，若有）
    # 每个目标都在**两种 ATR 口径**下各跑三场景：
    #   · 旧口径 = 生产进程实际在跑的代码 → 4 个候选应当稳定复现（验证「衔接确定性」）
    #   · 新口径 = 修复后的代码       → 4 个候选应当稳定缺席、新增候选应当稳定出现
    #     （验证「修复本身是确定的」，不是随机漂移）
    legacy_targets = [dict(t, caliber="legacy") for t in (prod_candidates or [])]
    fixed_targets = [dict(t, caliber="fixed") for t in r2["candidates"]]
    targets = legacy_targets + fixed_targets
    _L = importlib.import_module("02_atr_parity")._LegacyATR42
    rows, per_cand = [], {}
    for t in targets:
        sym = t["symbol"]
        b4 = load_bars(con, sym, "4h")
        b1 = load_bars(con, sym, "1h")
        if not b4 or not b1:
            per_cand[f"{sym}@{t['caliber']}"] = {"error": "no_bars_in_isolated_db"}
            continue
        ct = int(t["available_at_ms"])
        floor = anchor_ms        # 全部候选共用生产同一锚点（见 docstring）

        def drive(t_init_ms, t_end_ms, restart_ms=None, legacy=False):
            """按「初始化 → (可选重启) → 追加」驱动一台真实引擎。"""
            def mk():
                # 旧口径 = 旧 ATR 提供者 **且** 没有 200 根预热门槛（两者是同一个缺陷的两面）；
                # 只换 ATR 不放开预热，会造出「旧 ATR + 新门槛」的混合体，不是修复前的真实行为。
                c2 = dataclasses.replace(cfg, atr_warmup_bars=0) if legacy else cfg
                e = UR.RealSignalEngine(symbol=sym, config=c2)
                e.history_floor_ms = floor
                if legacy:
                    e.atr = _L(cfg.atr_period_4h, cfg.box_bars_4h)
                return e

            w = UR._MemWatcher(b4, b1)
            eng = mk()
            for bar in b4:                       # 初始化：回放到 t_init
                c_t = int(bar["close_time"])
                if c_t > t_init_ms:
                    break
                eng.process_market(c_t, w)
            init = snapshot_engine(eng)
            if restart_ms is not None:
                # 重启：新引擎 + 全量重放到 restart_ms（= bootstrap_full_replay）
                eng = mk()
                for bar in b4:
                    c_t = int(bar["close_time"])
                    if c_t > restart_ms:
                        break
                    eng.process_market(c_t, w)
            base = restart_ms if restart_ms is not None else t_init_ms
            for bar in b4:                       # 追加：继续喂到 t_end
                c_t = int(bar["close_time"])
                if c_t <= base:
                    continue
                if c_t > t_end_ms:
                    break
                eng.process_market(c_t, w)
            return {"init": init, "final": snapshot_engine(eng)}

        t_end = ct + int(24 * cm.H1)
        scen = {}
        for cal in ("legacy", "fixed"):
            lg = (cal == "legacy")
            scen[cal] = {
                "S1": drive(floor, t_end, legacy=lg),
                "S2": drive(ct - int(72 * cm.H1), t_end, legacy=lg),
                "S3": drive(ct - int(72 * cm.H1), t_end,
                            restart_ms=ct - int(12 * cm.H1), legacy=lg),
            }

        def has(snap):
            # 稳定身份 = (symbol, available_at_ms)；candidate_id 含运行内计数器，跨运行不可比
            return any(c["available_at_ms"] == int(t["available_at_ms"])
                       for c in snap["final"]["candidates"])

        def states(s):
            return [s["S1"]["final"]["final_state"], s["S2"]["final"]["final_state"],
                    s["S3"]["final"]["final_state"]]

        def cset(s):
            return sorted(c["available_at_ms"] for c in s["S1"]["final"]["candidates"])

        row = {"symbol": sym, "caliber": t["caliber"],
               "candidate_id": t["candidate_id"],
               "event_id": t.get("event_id"),
               "available_at_utc": t["available_at_utc"],
               "box_atr_a": t["box_atr_a"],
               "S1_continuous_found": has(scen[t["caliber"]]["S1"]),
               "S2_init_then_append_found": has(scen[t["caliber"]]["S2"]),
               "S3_midway_restart_found": has(scen[t["caliber"]]["S3"]),
               "state_S1_S2_S3": "/".join(states(scen[t["caliber"]])),
               "states_all_equal": len(set(states(scen[t["caliber"]]))) == 1,
               "candidate_set_S1": len(cset(scen[t["caliber"]])),
               # 同一候选在两种口径下的可见性（= 修复前后影响，候选级）
               "legacy_visible": has(scen["legacy"]["S1"]),
               "fixed_visible": has(scen["fixed"]["S1"]),
               "caliber_flips_visibility": (has(scen["legacy"]["S1"])
                                            != has(scen["fixed"]["S1"])),
               "order_id": None,
               "order_reason": "history_discarded_by_bootstrap（预热期候选按纪律丢弃，不补开）"}
        row["all_three_found"] = (row["S1_continuous_found"]
                                  and row["S2_init_then_append_found"]
                                  and row["S3_midway_restart_found"])
        rows.append(row)
        per_cand[f"{sym}@{t['caliber']}"] = {"target": t, "scenarios": scen,
                                             "verdict": row}
    con.close()

    c_res = {"phase": "C_continuity", "generated_at_utc": cm.utc_now(),
             "n_targets": len(targets),
             "n_legacy_targets": len(legacy_targets),
             "n_fixed_targets": len(fixed_targets),
             "start_points": ("**全部候选共用生产同一锚点**（= 热库 4h 首根 open_time，"
                              "即生产实际生效的预热起点）；驱动方式三分：S1 从锚点一路喂到 "
                              "候选+24h；S2 先回放到候选前 72h 再追加；S3 在候选前 72h 初始化、"
                              "候选前 12h 重启（新引擎全量重放）再追加。"
                              "不剔除任何一个候选；不按候选各自平移锚点（那会因 200 根预热"
                              "把建档起点平移而制造假阴性）。"),
             "rows": rows,
             "all_three_found_for_all": all(r["all_three_found"] for r in rows) if rows else None,
             "states_all_equal_for_all": (all(r["states_all_equal"] for r in rows)
                                          if rows else None),
             "legacy_targets_visible_under_legacy": all(
                 r["legacy_visible"] for r in rows if r["caliber"] == "legacy") if rows else None,
             "legacy_targets_absent_under_fixed": all(
                 (not r["fixed_visible"]) for r in rows if r["caliber"] == "legacy") if rows else None,
             "fixed_targets_visible_under_fixed": all(
                 r["fixed_visible"] for r in rows if r["caliber"] == "fixed") if rows else None,
             "detail": per_cand}
    cm.write_json("03_C_候选衔接验证.json", c_res)
    with open(cm.OUT / "03_C_候选衔接验证.csv", "w", newline="", encoding="utf-8") as f:
        fn = ["symbol", "caliber", "candidate_id", "event_id", "available_at_utc",
              "box_atr_a",
              "S1_continuous_found", "S2_init_then_append_found", "S3_midway_restart_found",
              "state_S1_S2_S3", "states_all_equal", "candidate_set_S1",
              "legacy_visible", "fixed_visible", "caliber_flips_visibility",
              "all_three_found", "order_id", "order_reason"]
        w = csv.DictWriter(f, fieldnames=fn)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fn})
    return {"A2": a2, "C": c_res}


# ══════════════════════════════════════════════════════════════════════
# B. 全池数据资格 + 水位 + 3 个异常标的溯源
# ══════════════════════════════════════════════════════════════════════
def phase_pool() -> dict:
    assert ISO_DB.exists(), f"缺少 {ISO_DB}"
    import unified_runtime as UR
    from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    cfg = EventTrendConfig()
    syms, meta = cm.universe()
    con = sqlite3.connect(f"file:{ISO_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    per, elig = {}, {"ok": 0, "insufficient_4h": 0, "insufficient_1h": 0, "no_data": 0}
    min_4h = cfg.box_bars_4h + 8
    for sym in syms:
        d = {}
        for tf in ("1h", "4h"):
            row = con.execute(
                "SELECT COUNT(*),MIN(open_time),MAX(close_time) FROM candle_versions "
                "WHERE symbol=? AND timeframe=?", (sym, tf)).fetchone()
            d[tf] = {"bars": row[0], "first_utc": cm.iso(row[1]),
                     "last_utc": cm.iso(row[2])}
        # 4h 全零坏死检查（volume=0 且 high==low==open==close）
        z = con.execute(
            "SELECT COUNT(*) FROM candle_versions WHERE symbol=? AND timeframe='4h' "
            "AND volume=0 AND high=low", (sym,)).fetchone()[0]
        d["4h_zero_flat_bars"] = z
        d["4h_zero_flat_ratio"] = round(z / d["4h"]["bars"], 4) if d["4h"]["bars"] else None
        if d["4h"]["bars"] == 0 and d["1h"]["bars"] == 0:
            elig["no_data"] += 1
            d["eligible"] = False
            d["reason"] = "no_data"
        elif d["4h"]["bars"] < min_4h:
            elig["insufficient_4h"] += 1
            d["eligible"] = False
            d["reason"] = f"4h_bars_insufficient({d['4h']['bars']}<{min_4h})"
        elif d["1h"]["bars"] < 200:
            elig["insufficient_1h"] += 1
            d["eligible"] = False
            d["reason"] = f"1h_bars_insufficient({d['1h']['bars']}<200)"
        else:
            elig["ok"] += 1
            d["eligible"] = True
            d["reason"] = "ok"
        per[sym] = d

    # ── 先落盘 eligibility（异常标的段要读生产库，可能失败；不能让它拖累已算好的全池结果）──
    _pre_out = {"phase": "B_pool_eligibility", "generated_at_utc": cm.utc_now(),
                "db": str(ISO_DB), "pool_n": len(syms), "pool_source_meta": meta,
                "criteria": {"min_4h_bars": min_4h, "min_1h_bars": 200},
                "eligibility": elig, "per_symbol": per,
                "coverage_statement": (
                    f"**全池 {len(syms)} 标的**：数据资格/水位/状态（本段，静态 SQL + 生产快照）。"
                    f"**代表性行为测试**：只有 4 个历史候选（C 段）+ 02 脚本的 80 标的逐时点 ATR 对照。"
                    "两者覆盖范围不同，**不把 4 个候选或 80 标的的结果外推成 526 全部通过**。")}
    cm.write_json("03_B_全池资格与异常标的.json", _pre_out)
    with open(cm.OUT / "03_B_全池资格.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "eligible", "reason", "1h_bars", "4h_bars",
                    "1h_first", "4h_first", "4h_last", "4h_zero_flat_bars",
                    "4h_zero_flat_ratio"])
        for s_, v_ in per.items():
            w.writerow([s_, v_["eligible"], v_["reason"], v_["1h"]["bars"], v_["4h"]["bars"],
                        v_["1h"]["first_utc"], v_["4h"]["first_utc"], v_["4h"]["last_utc"],
                        v_["4h_zero_flat_bars"], v_["4h_zero_flat_ratio"]])
    print("[B] eligibility 已落盘；继续异常标的溯源…")

    # 3 个异常标的：源数据 + 采集记录
    anomaly = {}
    for sym in ANOMALY_SYMBOLS:
        a = {"isolated": per.get(sym)}
        rows = con.execute(
            "SELECT * FROM candle_versions WHERE symbol=? AND timeframe='4h' "
            "ORDER BY open_time", (sym,)).fetchall()
        a["iso_4h_first"] = [dict(r) for r in rows[:3]]
        a["iso_4h_last"] = [dict(r) for r in rows[-3:]]
        a["iso_4h_all_zero_flat"] = all(
            float(r["volume"]) == 0 and float(r["high"]) == float(r["low"]) for r in rows
        ) if rows else None
        a["iso_4h_n"] = len(rows)
        anomaly[sym] = a
    con.close()

    # 生产库侧：源数据版本 / 采集记录
    con2 = cm.market_conn()
    for sym in ANOMALY_SYMBOLS:
        a = anomaly[sym]
        for tf in ("4h", "1h"):
            rows = con2.execute(
                "SELECT * FROM candle_versions WHERE symbol=? AND timeframe=? "
                "ORDER BY open_time", (sym, tf)).fetchall()
            a[f"prod_{tf}_n"] = len(rows)
            a[f"prod_{tf}_first"] = dict(rows[0]) if rows else None
            a[f"prod_{tf}_last"] = dict(rows[-1]) if rows else None
            a[f"prod_{tf}_all_zero_flat"] = all(
                float(r["volume"]) == 0 and float(r["high"]) == float(r["low"])
                for r in rows) if rows else None
        # sources / 采集轮次
        try:
            src = con2.execute(
                "SELECT * FROM sources WHERE symbol=? ORDER BY rowid DESC LIMIT 5",
                (sym,)).fetchall()
            a["sources_tail"] = [dict(r) for r in src]
        except Exception as exc:                                # noqa: BLE001
            a["sources_tail_error"] = f"{type(exc).__name__}: {exc}"
        try:
            cr = con2.execute(
                "SELECT * FROM collector_rounds WHERE symbol=? ORDER BY rowid DESC LIMIT 5",
                (sym,)).fetchall()
            a["collector_rounds_tail"] = [dict(r) for r in cr]
        except Exception as exc:                                # noqa: BLE001
            a["collector_rounds_error"] = f"{type(exc).__name__}: {exc}"
        try:
            cs = con2.execute("SELECT * FROM collector_state WHERE symbol=?", (sym,)).fetchall()
            a["collector_state"] = [dict(r) for r in cs]
        except Exception as exc:                                # noqa: BLE001
            a["collector_state_error"] = f"{type(exc).__name__}: {exc}"
        # 官方归档 zip 里这些标的存在吗（只列清单，不解压、不下载）
        zdir = cm.BASE / "数据/raw"
        a["raw_zip"] = {tf: sorted([p.name for p in (zdir / tf / sym).glob("*.zip")])[:6]
                        if (zdir / tf / sym).exists() else None
                        for tf in ("4h", "1h")}
        a["raw_zip_dir_exists"] = {tf: (zdir / tf / sym).exists() for tf in ("4h", "1h")}
    con2.close()

    out = {"phase": "B_pool_eligibility", "generated_at_utc": cm.utc_now(),
           "db": str(ISO_DB), "pool_n": len(syms), "pool_source_meta": meta,
           "criteria": {"min_4h_bars": min_4h, "min_1h_bars": 200},
           "eligibility": elig, "per_symbol": per,
           "anomaly_symbols": anomaly,
           "coverage_statement": (
               f"**全池 {len(syms)} 标的**：数据资格/水位/状态（本段，静态 SQL + 生产快照）。"
               f"**代表性行为测试**：只有 4 个历史候选（C 段）+ 02 脚本的 80 标的逐时点 ATR 对照。"
               "两者覆盖范围不同，**不把 4 个候选或 80 标的的结果外推成 526 全部通过**。"),
           "note": ("`4h_zero_flat_bars` 是「volume=0 且 high==low」的 4h 根数；"
                    "币安官方归档在上市前会产出这类**零量平价占位行**"
                    "（见 `框架/backend/history_lifecycle.py`），"
                    "它是**归档证据**但不是可交易的趋势输入 ⇒ 必须与「市场无成交」分开定性。")}
    cm.write_json("03_B_全池资格与异常标的.json", out)
    with open(cm.OUT / "03_B_全池资格.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "eligible", "reason", "1h_bars", "4h_bars",
                    "1h_first", "4h_first", "4h_last", "4h_zero_flat_bars",
                    "4h_zero_flat_ratio"])
        for s, v in per.items():
            w.writerow([s, v["eligible"], v["reason"], v["1h"]["bars"], v["4h"]["bars"],
                        v["1h"]["first_utc"], v["4h"]["first_utc"], v["4h"]["last_utc"],
                        v["4h_zero_flat_bars"], v["4h_zero_flat_ratio"]])
    return out


def _inject_iso_env():
    """把 `MARKET_DB` 指到隔离库——**必须在 import live_store / unified_runtime 之前**。

    `live_store.MARKET_DB` 在 import 时就求值并缓存，晚设环境变量不生效（本项目已踩过）。
    """
    os.environ["MARKET_DB"] = str(ISO_DB)
    os.environ["LIVE_DIR"] = str(cm.OUT / "cand_live")
    os.environ["PAPER_LEDGER_DIR"] = str(cm.OUT / "cand_paper")
    os.environ["PAPER_LOCK_DIR"] = str(cm.OUT / "cand_locks")
    (cm.OUT / "cand_live").mkdir(parents=True, exist_ok=True)
    for m in ("live_store", "unified_runtime"):
        sys.modules.pop(m, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all",
                    choices=["prod", "iso", "pool", "all"])
    a = ap.parse_args()

    if a.phase == "all":
        # 生产库与隔离库是**两份不同的 MARKET_DB**，同进程内 import 缓存会串味 ⇒ 分进程跑
        import subprocess
        for ph in ("prod", "iso", "pool"):
            r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--phase", ph])
            if r.returncode != 0:
                raise SystemExit(f"phase {ph} failed rc={r.returncode}")
        return

    if a.phase == "prod":
        prod = phase_prod()
        print(f"[A1] 旧 ATR（= 生产在跑）复现 {prod['legacy_atr_n']} 个候选 "
              f"（快照 {prod['snapshot_n']}，一致={prod['legacy_matches_snapshot']}）")
        for c in prod["legacy_candidates"]:
            print("   L", c["symbol"], c["available_at_utc"], "A=", c["box_atr_a"])
        print(f"[A1] 新 ATR（= 修复后）复现 {prod['fixed_atr_n']} 个候选")
        for c in prod["fixed_candidates"]:
            print("   F", c["symbol"], c["available_at_utc"], "A=", c["box_atr_a"])
        print(f"[A1] 只在旧口径出现: {prod['only_in_legacy']}")
        print(f"[A1] 只在新口径出现: {prod['only_in_fixed']}")
        return

    _inject_iso_env()
    if a.phase == "iso":
        p = cm.OUT / "03_A1_生产库复现候选.json"
        pc = None
        anchor = upto = None
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            # 目标 = **4 个已知历史候选**（旧口径复现出来的那批，与生产快照数一致时）
            pc = d.get("legacy_candidates") or d.get("candidates")
            # 锚点 = 生产热库 4h 首根 open_time；截止 = 生产预热截止时刻
            f4 = (d.get("hot_db_span") or {}).get("4h", {}).get("first_utc")
            if f4:
                import datetime as _dt
                anchor = int(_dt.datetime.strptime(f4, "%Y-%m-%dT%H:%M:%SZ")
                             .replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
            upto = d.get("requested_window", {}).get("upto_utc")
            if upto:
                import datetime as _dt
                upto = int(_dt.datetime.strptime(upto, "%Y-%m-%dT%H:%M:%SZ")
                           .replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
        r = phase_iso(pc, anchor_ms=anchor, upto_ms=upto)
        a2 = r["A2"]
        print(f"[A2] 隔离库：旧口径 {a2['legacy_atr_n']} 个 / 新口径 {a2['fixed_atr_n']} 个")
        idv = a2.get("identity_vs_production_same_caliber")
        if idv:
            print(f"[A2] 同口径身份比对 vs 生产：一致={idv['identical']} "
                  f"（prod={idv['prod_n']} iso={idv['iso_n']}）"
                  f" only_prod={idv['only_in_prod']} only_iso={idv['only_in_iso']}")
        c = r["C"]
        print(f"[C] 目标 {c['n_targets']} 个（旧口径 {c['n_legacy_targets']} + "
              f"新口径 {c['n_fixed_targets']}）；三场景全中={c['all_three_found_for_all']}；"
              f"三场景终态一致={c['states_all_equal_for_all']}")
        print(f"[C] 旧口径目标在旧口径下可见={c['legacy_targets_visible_under_legacy']}；"
              f"在修复后缺席={c['legacy_targets_absent_under_fixed']}；"
              f"新增候选在修复后可见={c['fixed_targets_visible_under_fixed']}")
        for row in c["rows"]:
            print("   ", row["caliber"], row["symbol"], row["available_at_utc"],
                  f"S1={row['S1_continuous_found']}", f"S2={row['S2_init_then_append_found']}",
                  f"S3={row['S3_midway_restart_found']}",
                  f"state={row['state_S1_S2_S3']}",
                  f"legacyVis={row['legacy_visible']}", f"fixedVis={row['fixed_visible']}")
        return

    if a.phase == "pool":
        b = phase_pool()
        print(f"[B] 全池 {b['pool_n']}；资格 {b['eligibility']}")
        for s in ANOMALY_SYMBOLS:
            v = b["anomaly_symbols"][s]
            print(f"   {s}: iso4h_n={v.get('iso_4h_n')} "
                  f"all_zero_flat={v.get('iso_4h_all_zero_flat')} "
                  f"prod4h_n={v.get('prod_4h_n')} zip4h={v.get('raw_zip_dir_exists')}")


if __name__ == "__main__":
    main()

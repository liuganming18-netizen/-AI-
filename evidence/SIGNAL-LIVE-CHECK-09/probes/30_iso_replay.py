#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-LIVE-CHECK-09 探针③：同段新增行情的隔离回放 vs 生产（要求 3）

为什么「生产 0 条」不能单独当结论
--------------------------------
0 可能是三件完全不同的事：
  (甲) 规则确实没触发（正常）
  (乙) 触发了但被某道闸门吃掉（缺陷）
  (丙) 引擎根本没处理到那几根 bar（缺陷）
单看「0」分不开。唯一能分开的办法：**拿同一段行情、同一份代码、同一套参数
在隔离环境里独立跑一遍**。如果回放也是 0、且**逐币阶段与丢弃原因逐条相同**，
才说明生产的 0 是 (甲)。

隔离到什么程度
--------------
  · 生产 epoch **不覆盖**（`paper_epoch_ms=1791939600000`，与生产同一个值）；
  · 生产 `live_start_ms` / `state_anchor_ms` 原样搬进隔离库（同一个起点、同一个锚点）；
  · `LIVE_DB` / `PAPER_LEDGER_DIR` / `PAPER_LOCK_DIR` 全落本单 evidence 目录；
  · 行情库只读，会写 candles 的 `sync_dataset_bars_upto` 在本进程内为 no-op；
  · 生产 `live_signals.sqlite3` + 四本账本 sha256 前后必须一致。

比什么（逐项，不按条数）
------------------------
  1. 逐币阶段：526 个标的 `per_symbol_state` 必须**逐币相同**；
  2. 阶段分布：`state_counter` 逐项相同；
  3. 候选：回放候选 ID 集合 vs 生产 `live_signal` 行（生产当前 0 行 ⇒ 回放也必须 0）；
  4. 丢弃原因：`discarded_detail` 的 ID 集合与原因**逐条相同**（51 条）；
  5. 漏斗：`funnel.history` 逐项相同；
  6. 水位：回放处理到的 1h/4h 与生产 `watermark_*` 相同。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
BACKEND = ROOT / "框架/backend"
BASE = HERE.parents[1]
ISO = BASE / "iso_replay"
API = "http://127.0.0.1:8787"
H1, H4 = 3_600_000, 14_400_000

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def sha(p: Path):
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def get(path):
    with _opener.open(API + path, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def prod_snapshot():
    """生产当前状态：逐币阶段 / 分布 / 水位 / 漏斗 / 候选 / 丢弃明细。"""
    st = get("/api/v1/paper/status")
    item = st.get("item") or {}
    se = item.get("signal_engine") or {}
    cur = get("/api/v1/signals?scope=current&limit=500")
    con = sqlite3.connect(
        f"file:{ROOT / '框架/backend/live/live_signals.sqlite3'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        db_rows = [dict(r) for r in con.execute(
            "SELECT signal_uid, symbol, direction, origin, executable_state, confirmed_at "
            "FROM live_signal ORDER BY confirmed_at")]
    finally:
        con.close()
    return {
        "captured_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_identity": item.get("runtime_identity"),
        "watermark_1h": se.get("watermark_1h"),
        "watermark_4h": se.get("watermark_4h"),
        "watermark_check": se.get("watermark_check"),
        "state_counter": se.get("state_counter"),
        "per_symbol_state": se.get("per_symbol_state") or {},
        "funnel_history": (se.get("funnel") or {}).get("history"),
        "funnel_since_start": (se.get("funnel") or {}).get("since_start"),
        "signals_generated_total": se.get("signals_generated_total"),
        "bootstrap": {
            "history_candidates_discarded":
                (se.get("bootstrap") or {}).get("history_candidates_discarded"),
            "recovered_candidates":
                (se.get("bootstrap") or {}).get("recovered_candidates"),
            "recovered_detail": (se.get("bootstrap") or {}).get("recovered_detail"),
            "discarded_detail": (se.get("bootstrap") or {}).get("discarded_detail"),
        },
        "api_current_total": cur.get("total"),
        "api_current_ids": [i.get("signal_id") for i in (cur.get("items") or [])],
        "live_db_rows": db_rows,
        "paper_epoch_ms": (cur.get("quality") or {}).get("paper_epoch_ms"),
        "live_start_ms": (cur.get("quality") or {}).get("live_start_ms"),
        "effective_floor_ms": (cur.get("quality") or {}).get("effective_floor_ms"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upto", type=int, default=None,
                    help="回放截止毫秒；默认按生产 watermark_1h 那根 1h 的收盘时刻")
    ap.add_argument("--out", default=str(BASE / "out" / "30_iso_replay.json"))
    args = ap.parse_args()

    prod = prod_snapshot()
    upto = args.upto or (int(prod["watermark_1h"]) + H1 - 1)

    ISO.mkdir(parents=True, exist_ok=True)
    (ISO / "paper").mkdir(parents=True, exist_ok=True)
    (ISO / "locks").mkdir(parents=True, exist_ok=True)
    for p in (ISO / "live_signals.sqlite3",):
        if p.exists():
            p.unlink()
    shutil.rmtree(ISO / "paper", ignore_errors=True)
    (ISO / "paper").mkdir(parents=True, exist_ok=True)

    os.environ["LIVE_DB"] = str(ISO / "live_signals.sqlite3")
    os.environ["PAPER_LEDGER_DIR"] = str(ISO / "paper")
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    os.environ["PAPER_LOCK_DIR"] = str(ISO / "locks")
    os.environ["WX_HISTORY_SOURCE"] = "merged"

    for p in (str(ROOT), str(BACKEND)):
        if p not in sys.path:
            sys.path.insert(0, p)

    import live_store as L  # noqa: E402

    # 生产 epoch **不覆盖**：隔离库直接写生产值，get_paper_epoch_ms 的 max 语义天然一致
    prod_epoch_const = int(L.PAPER_EPOCH_MS)

    fp_before = {str(p.relative_to(ROOT)): sha(p) for p in (
        ROOT / "框架/backend/live/live_signals.sqlite3",
        *[ROOT / f"数据/paper/paper_3{a}.sqlite3" for a in ("a", "b", "c", "d")])}

    import unified_runtime as UR
    import live_api as LA

    con = L.live_conn()
    L.set_run("live_start_ms", int(prod["live_start_ms"]))
    L.set_run("paper_epoch_ms", int(prod["paper_epoch_ms"]))
    L.set_run("state_anchor_ms", 1782542715686)
    con.close()

    rt = UR.UnifiedRuntime(arm="3a")
    se = rt.signal_engine
    # 与生产同一个锚点（本单要求：同段行情、同起点，不是换一个起点再比）
    se.state_anchor_ms = 1782542715686
    se.history_floor_ms = se.resolve_replay_floor(upto)
    rt.started_at_ms = int(upto) - H1        # 模拟「上一根 1h 就已在线」
    rt.watcher.sync_dataset_bars_upto = lambda *_a, **_k: None

    import history_loader as HL
    import project_paths as PP
    se.history_provider = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=print)

    t0 = time.time()
    se.prepare_history(upto, log=print)
    bs = se.bootstrap(upto, log=print)
    t_boot = time.time() - t0

    # 生产 run_forever 的顺序：bootstrap → _route_recovered_candidates()
    rec = rt._route_recovered_candidates(now_ms=upto)

    cur = LA.live_signals({"scope": ["current"], "limit": ["500"]})
    allq = LA.live_signals({"scope": ["all"], "limit": ["500"]})

    con = sqlite3.connect(f"file:{os.environ['LIVE_DB']}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        iso_rows = [dict(r) for r in con.execute(
            "SELECT signal_uid, symbol, direction, origin, executable_state, confirmed_at "
            "FROM live_signal ORDER BY confirmed_at")]
    except sqlite3.OperationalError:
        iso_rows = []
    finally:
        con.close()

    iso_state = se.state_by_symbol()
    iso_counter = se.state_counter()
    iso_funnel = se.funnel()
    iso_disc = (bs.get("discarded_detail") or [])

    # ── 逐币条件实际值（要求 2 的「条件实际值和阈值」）───────────────────────
    # 为什么要在这里导：生产接口的 `idle_breakdown` 只给**每类一个样例**
    # （内部其实算了逐币 detail，但返回时收窄成 sample），非 IDLE 的币根本没有
    # 条件值可读。本单不改生产接口（改契约要重启、会打断跨小时线观测），
    # 改为在**回放**里导出逐币条件值 —— 前提是回放与生产逐币状态 0 差异（本脚本会核）。
    # 纪律：这份数据必须标注为「回放复现」，不能当成「生产直读」。
    per_symbol_cond = {}
    _cfg = se.config

    def _box_of(ev):
        """`EventDossier.box` 是 `contracts.BoxVersion`（冻结结构）。

        真实字段名（**不是** `box_high_h`/`box_atr_a`，那是 `CandidateSnapshot` 的叫法）：
        `high_h` / `low_l` / `atr_a` / `efficiency_ratio_er` / `box_width_atr` /
        `formation_time_ms` / `bars_count` / `version_id`。
        第一版照候选快照的名字取，导致 atr/er/width 全 null —— 已按真名改正。
        """
        b = getattr(ev, "box", None)
        if b is None:
            return None
        hi = getattr(b, "high_h", None)
        lo = getattr(b, "low_l", None)
        atr = getattr(b, "atr_a", None)
        er = getattr(b, "efficiency_ratio_er", None)
        w = getattr(b, "box_width_atr", None)
        if w is None and atr and hi is not None and lo is not None:
            w = (float(hi) - float(lo)) / float(atr)
        return {
            "version_id": getattr(b, "version_id", None),
            "box_high_h": hi,
            "box_low_l": lo,
            "box_atr_a": round(float(atr), 8) if atr is not None else None,
            "box_er": round(float(er), 6) if er is not None else None,
            "width_atr": round(float(w), 3) if w is not None else None,
            "formation_time_utc": iso(getattr(b, "formation_time_ms", None)),
            "bars_count": getattr(b, "bars_count", None),
        }

    def _next_gate(state, ev, atr):
        """把「为什么没进下一环」写成**可核对的价位/比值**，而不是一句"未触发"。

        只用冻结阈值与档案里的实际值算，不读行情、不改判据；
        算出来的价位是「要跨过去的那条线」，不是预测。
        """
        if ev is None or state == "IDLE":
            return {
                "结论": "无事件在跟踪（IDLE），要等箱体建档",
                "门槛": {
                    "需连续 4h 根数": _cfg.box_bars_4h,
                    "ATR 预热根数": _cfg.atr_warmup_bars,
                    "ER 上限": _cfg.er_threshold,
                    "箱宽区间(ATR)": [_cfg.box_min_width_atr, _cfg.box_max_width_atr],
                },
            }
        b = getattr(ev, "box", None)
        hi = getattr(b, "high_h", None) if b else None
        lo = getattr(b, "low_l", None) if b else None
        a = atr if atr else (getattr(b, "atr_a", None) if b else None)
        if state == "BOX":
            return {
                "结论": "箱体已建档，等 1h 向下扫损（还没跌破下沿）",
                "门槛": {
                    "1h 最低价 需 <": lo,
                    "扫损深度上限": (round(lo - _cfg.sweep_max_overshoot_atr * a, 8)
                                     if lo is not None and a else None),
                    "收盘需回到 >=": (round(lo + _cfg.sweep_min_recovery_atr * a, 8)
                                     if lo is not None and a else None),
                    "时限(h)": _cfg.sweep_max_hours,
                },
                "实际": {"箱体上沿 H": hi, "箱体下沿 L": lo, "ATR": a},
            }
        if state == "SWEEP":
            return {
                "结论": "已扫损，等 72h 内回踩（还没回踩到位）",
                "门槛": {
                    "回踩低点区间": [(round(lo + _cfg.retest_min_zone_atr * a, 8)
                                     if lo is not None and a else None),
                                    (round(lo + _cfg.retest_max_zone_atr * a, 8)
                                     if lo is not None and a else None)],
                    # 成交量是「个」量级，取整到个位；用 round(…, 2) 在 2.8e8 这种
                    # 量级上根本无效（浮点 ulp ≈ 6e-8，输出会带 …60000002 的尾巴）
                    "回踩量 需 <=": (int(round(_cfg.retest_vol_multiplier
                                              * float(getattr(ev, "sweep_volume_vs", 0) or 0)))
                                    or None),
                    "收盘需回到 >=": (round(lo + _cfg.retest_recovery_atr * a, 8)
                                     if lo is not None and a else None),
                    "时限(h)": _cfg.retest_max_hours,
                },
                "实际": {"扫损低点 S": getattr(ev, "sweep_low_s", None),
                         "扫损量 Vs": getattr(ev, "sweep_volume_vs", None),
                         "扫损时刻": iso(getattr(ev, "sweep_time_ms", None))},
            }
        if state == "RETEST":
            return {
                "结论": "已回踩，等突破确认（收盘/量能/OI 三关）",
                "门槛": {
                    "1h 收盘 需 >": (round(hi + _cfg.breakout_buffer_atr * a, 8)
                                    if hi is not None and a else None),
                    "成交量 需 >=": f"{_cfg.breakout_vol_multiplier} × 前 "
                                    f"{_cfg.breakout_vol_lookback} 根 1h 中位量",
                    "OI 6h 增长 需 >=": _cfg.oi_min_growth_pct,
                    "时限(h)": _cfg.breakout_max_hours_after_retest,
                },
                "实际": {"箱体上沿 H": hi, "回踩低点 Lr": getattr(ev, "retest_low_lr", None),
                         "回踩时刻": iso(getattr(ev, "retest_time_ms", None))},
            }
        return {"结论": f"状态 {state} 无下一环门槛定义", "门槛": None}

    for sym, eng in (se.engines or {}).items():
        sm = getattr(eng, "sm", None)
        p1 = list(getattr(eng, "processed_1h_opens", set()) or [])
        p4 = list(getattr(eng, "processed_4h_opens", set()) or [])
        ev = getattr(sm, "active_event", None) if sm else None
        _atr = (float(eng.last_atr) if getattr(eng, "last_atr", None) is not None else None)
        # 刚结束的事件：`active_event` 会被清空，`termination_reason` 只留在
        # `sm.completed_events` 里。不取它，BOX→IDLE 这类迁移就只剩「无事件」，
        # 答不出「具体原因」。只取最后一条（本次新增线上刚终止的那个）。
        _ce = list(getattr(sm, "completed_events", []) or []) if sm else []
        _last_end = None
        if _ce:
            _e = _ce[-1]
            _last_end = {
                "event_id": getattr(_e, "event_id", None),
                "ended_at_utc": iso(getattr(_e, "ended_at_ms", None)),
                "termination_reason": getattr(_e, "termination_reason", None),
                "final_state": (getattr(getattr(_e, "current_state", None), "value", None)
                                or str(getattr(_e, "current_state", None))),
            }
        per_symbol_cond[sym] = {
            "state": iso_state.get(sym),
            "last_atr": (round(_atr, 8) if _atr is not None else None),
            "atr_bars_seen": getattr(eng, "atr_bars_seen", None),
            "processed_1h_max_utc": iso(max(p1) if p1 else None),
            "processed_4h_max_utc": iso(max(p4) if p4 else None),
            "cooling_until_utc": iso(getattr(sm, "cooling_until_ms", None)) if sm else None,
            "next_gate": _next_gate(iso_state.get(sym), ev, _atr),
            "last_terminated_event": _last_end,
            "active_event": ({
                "event_id": getattr(ev, "event_id", None),
                "current_state": (getattr(getattr(ev, "current_state", None), "value", None)
                                  or str(getattr(ev, "current_state", None))),
                "direction": getattr(ev, "hypothesis_direction", None),
                "box": _box_of(ev),
                "sweep_low_s": getattr(ev, "sweep_low_s", None),
                "sweep_volume_vs": getattr(ev, "sweep_volume_vs", None),
                "sweep_time_utc": iso(getattr(ev, "sweep_time_ms", None)),
                "retest_low_lr": getattr(ev, "retest_low_lr", None),
                "retest_time_utc": iso(getattr(ev, "retest_time_ms", None)),
                "breakout_close_price": getattr(ev, "breakout_close_price", None),
                "breakout_volume": getattr(ev, "breakout_volume", None),
                "v20_median_volume": getattr(ev, "v20_median_volume", None),
                "breakout_time_utc": iso(getattr(ev, "breakout_time_ms", None)),
                "oi_growth_6h": getattr(ev, "oi_growth_6h", None),
                "termination_reason": getattr(ev, "termination_reason", None),
                "candidate_id": getattr(ev, "candidate_id", None),
            } if ev is not None else None),
            "data_quality_invalid": bool(
                (getattr(eng, "data_quality", {}) or {}).get("invalid")),
        }

    # 阈值（冻结参数，只读 config；本单不调参、不改门禁）
    # 分组按状态机实际关卡来，方便「逐币条件实际值」直接对号入座。
    thresholds = {
        "box_建档": {
            "box_bars_4h": getattr(_cfg, "box_bars_4h", None),
            "atr_period_4h": getattr(_cfg, "atr_period_4h", None),
            "atr_warmup_bars": getattr(_cfg, "atr_warmup_bars", None),
            "er_threshold": getattr(_cfg, "er_threshold", None),
            "box_min_width_atr": getattr(_cfg, "box_min_width_atr", None),
            "box_max_width_atr": getattr(_cfg, "box_max_width_atr", None),
        },
        "sweep_扫损": {
            "sweep_max_hours": getattr(_cfg, "sweep_max_hours", None),
            "sweep_max_overshoot_atr": getattr(_cfg, "sweep_max_overshoot_atr", None),
            "sweep_min_recovery_atr": getattr(_cfg, "sweep_min_recovery_atr", None),
        },
        "retest_回踩": {
            "retest_max_hours": getattr(_cfg, "retest_max_hours", None),
            "retest_min_zone_atr": getattr(_cfg, "retest_min_zone_atr", None),
            "retest_max_zone_atr": getattr(_cfg, "retest_max_zone_atr", None),
            "retest_recovery_atr": getattr(_cfg, "retest_recovery_atr", None),
            "retest_vol_multiplier": getattr(_cfg, "retest_vol_multiplier", None),
            "retest_fail_buffer_atr": getattr(_cfg, "retest_fail_buffer_atr", None),
        },
        "breakout_突破": {
            "breakout_max_hours_after_retest":
                getattr(_cfg, "breakout_max_hours_after_retest", None),
            "breakout_max_total_hours": getattr(_cfg, "breakout_max_total_hours", None),
            "breakout_buffer_atr": getattr(_cfg, "breakout_buffer_atr", None),
            "breakout_vol_lookback": getattr(_cfg, "breakout_vol_lookback", None),
            "breakout_vol_multiplier": getattr(_cfg, "breakout_vol_multiplier", None),
        },
        "oi_持仓": {
            "oi_lookback_hours": getattr(_cfg, "oi_lookback_hours", None),
            "oi_min_growth_pct": getattr(_cfg, "oi_min_growth_pct", None),
            "oi_max_time_distance_sec": getattr(_cfg, "oi_max_time_distance_sec", None),
        },
        "冷却": {"cooling_hours": getattr(_cfg, "cooling_hours", None)},
    }

    # ── 逐币对撞 ─────────────────────────────────────────────────────────────
    p_state = prod["per_symbol_state"]
    diff = []
    for sym in sorted(set(p_state) | set(iso_state)):
        a, b = p_state.get(sym), iso_state.get(sym)
        if a != b:
            diff.append({"symbol": sym, "production": a, "replay": b})

    def key_disc(lst):
        return sorted((d.get("candidate_id"), tuple(sorted(d.get("discard_reasons") or [])))
                      for d in lst)

    p_disc = key_disc(prod["bootstrap"]["discarded_detail"] or [])
    i_disc = key_disc(iso_disc)

    # ── 漏斗口径：production = history（bootstrap 期，重启后冻住）+ since_start（增量）
    #    回放 = 一次性 bootstrap 到同一水位 ⇒ 全部落在 history，since_start 恒 0。
    #    所以可比量是 `prod.history + prod.since_start` ↔ `replay.history`。
    #    本单第一次跑（32_iso_replay_fields_fix）只比了 history，误报 False；
    #    差值恰好等于 since_start（BOX +26 / SWEEP +7 / RETEST +8 / INVALIDATED +3 / EXPIRED +2），
    #    反过来正好证明「生产增量确实在记账」。此坑记在 DELIVERY 里，别再踩。
    _ph = prod["funnel_history"] or {}
    _ps = prod["funnel_since_start"] or {}
    _rh = iso_funnel.get("history") or {}
    _rs = iso_funnel.get("since_start") or {}
    prod_total_funnel = {k: int(_ph.get(k, 0)) + int(_ps.get(k, 0))
                         for k in set(_ph) | set(_ps)}
    replay_total_funnel = {k: int(_rh.get(k, 0)) + int(_rs.get(k, 0))
                           for k in set(_rh) | set(_rs)}
    funnel_total_equal = prod_total_funnel == replay_total_funnel
    funnel_delta = {k: {"prod": prod_total_funnel.get(k), "replay": replay_total_funnel.get(k)}
                    for k in sorted(set(prod_total_funnel) | set(replay_total_funnel))
                    if prod_total_funnel.get(k) != replay_total_funnel.get(k)}

    out = {
        "probe": "SIGNAL-LIVE-CHECK-09/30_iso_replay",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "isolation": {
            "LIVE_DB": os.environ["LIVE_DB"],
            "PAPER_LEDGER_DIR": os.environ["PAPER_LEDGER_DIR"],
            "PAPER_LOCK_DIR": os.environ["PAPER_LOCK_DIR"],
            "MARKET_DB": str(L.MARKET_DB),
            "market_db_readonly": True,
            "dataset_sync_disabled": True,
            "paper_epoch_overridden": False,
            "prod_paper_epoch_const": prod_epoch_const,
            "note": ("生产 epoch 未覆盖（隔离库直接写生产值）；生产锚点/实时起点原样搬入；"
                     "只改本进程内存与隔离目录"),
        },
        "clock": {
            "upto_ms": upto, "upto_utc": iso(upto),
            "prod_watermark_1h_utc": iso(prod["watermark_1h"]),
            "prod_watermark_4h_utc": iso(prod["watermark_4h"]),
            "replay_watermark_1h": se._global_1h,
            "replay_watermark_1h_utc": iso(se._global_1h),
            "replay_watermark_4h": se._global_4h,
            "replay_watermark_4h_utc": iso(se._global_4h),
            "state_anchor_ms": se.state_anchor_ms,
            "state_anchor_utc": iso(se.state_anchor_ms),
            "replay_mode": "absolute_anchor",
        },
        "production": {
            "captured_utc": prod["captured_utc"],
            "state_counter": prod["state_counter"],
            "watermark_1h_utc": iso(prod["watermark_1h"]),
            "watermark_4h_utc": iso(prod["watermark_4h"]),
            "funnel_history": prod["funnel_history"],
            "funnel_since_start": prod["funnel_since_start"],
            "funnel_total": prod_total_funnel,
            "funnel_note": ("`history` 是**预热回放期**（本次进程 bootstrap 那一刻为止）的迁移计数，"
                            "进程重启后被冻住、不随新增小时线增长；`since_start` 是本次进程启动后"
                            "逐根处理新增 K 线累加的迁移计数。要与「一次性回放到同一水位的回放」比，"
                            "必须比 `history + since_start`（即 funnel_total），"
                            "只比 `history` 会恰好差出一根新增线的迁移量。"),
            "signals_generated_total": prod["signals_generated_total"],
            "bootstrap_discarded": prod["bootstrap"]["history_candidates_discarded"],
            "bootstrap_recovered": prod["bootstrap"]["recovered_candidates"],
            "api_current_total": prod["api_current_total"],
            "live_db_rows": len(prod["live_db_rows"]),
        },
        "replay": {
            "state_counter": iso_counter,
            "n_engines": len(iso_state),
            "watermark_1h_utc": iso(se._global_1h),
            "watermark_4h_utc": iso(se._global_4h),
            "funnel_history": iso_funnel.get("history"),
            "funnel_since_start": iso_funnel.get("since_start"),
            "funnel_total": replay_total_funnel,
            "bootstrap": {
                "ready": bs.get("ready"), "skipped": bs.get("skipped"),
                "discarded": bs.get("history_candidates_discarded"),
                "recovered": bs.get("recovered_candidates"),
                "recovered_detail": bs.get("recovered_detail"),
                "elapsed_sec": round(t_boot, 1),
            },
            "route_recovered": rec,
            "db_rows": iso_rows,
            "api_current_total": cur.get("total"),
            "api_current_ids": [i.get("signal_id") for i in (cur.get("items") or [])],
            "api_all_total": allq.get("total"),
        },
        "compare": {
            "per_symbol_diff_n": len(diff),
            "per_symbol_diff": diff[:40],
            "state_counter_equal": prod["state_counter"] == iso_counter,
            "candidate_ids_equal": (
                sorted(r["signal_uid"] for r in prod["live_db_rows"])
                == sorted(r["signal_uid"] for r in iso_rows)),
            "discarded_ids_equal": p_disc == i_disc,
            "discarded_n_prod": len(p_disc),
            "discarded_n_replay": len(i_disc),
            "funnel_history_equal": prod["funnel_history"] == iso_funnel.get("history"),
            "funnel_total_equal": funnel_total_equal,
            "funnel_delta": funnel_delta,
            "watermark_equal": (
                iso(prod["watermark_1h"]) == iso(se._global_1h)
                and iso(prod["watermark_4h"]) == iso(se._global_4h)),
        },
        "production_fingerprint_before": fp_before,
        "production_fingerprint_after": {
            str(p.relative_to(ROOT)): sha(p) for p in (
                ROOT / "框架/backend/live/live_signals.sqlite3",
                *[ROOT / f"数据/paper/paper_3{a}.sqlite3" for a in ("a", "b", "c", "d")])},
        "elapsed_sec": round(time.time() - t0, 1),
    }
    out["production_untouched"] = (
        out["production_fingerprint_before"] == out["production_fingerprint_after"])

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # 逐币条件值单独落一个文件（526 币 × 10 字段，塞进主 JSON 会盖掉对照结论）
    cond_p = p.parent / (p.stem + "_per_symbol_conditions.json")
    cond_p.write_text(json.dumps({
        "probe": "SIGNAL-LIVE-CHECK-09/30_iso_replay:per_symbol_conditions",
        "source": "isolated_replay（回放复现，**不是**生产直读）",
        "upto_utc": iso(upto),
        "n": len(per_symbol_cond),
        "note": ("生产接口只对 IDLE 且 active_event 为空的币给出条件值（且只返回每类一个样例）；"
                 "非 IDLE 的币没有直读入口。本文件由隔离回放导出，"
                 "**只有在本脚本 compare.per_symbol_diff_n == 0 时才能代表生产同一时刻的状态**。"),
        "thresholds": thresholds,
        "per_symbol": per_symbol_cond,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n===== 对照结果 =====")
    print(f"截止 {iso(upto)}｜生产水位 1h={iso(prod['watermark_1h'])} "
          f"4h={iso(prod['watermark_4h'])}")
    print(f"回放水位 1h={iso(se._global_1h)} 4h={iso(se._global_4h)}")
    print(f"阶段分布 生产={prod['state_counter']} 回放={iso_counter} "
          f"相同={out['compare']['state_counter_equal']}")
    print(f"逐币阶段差异 {len(diff)} 个")
    for d in diff[:10]:
        print(f"   {d['symbol']}: 生产={d['production']} 回放={d['replay']}")
    print(f"候选：生产 {len(prod['live_db_rows'])} 行 / 回放 {len(iso_rows)} 行 "
          f"相同={out['compare']['candidate_ids_equal']}")
    print(f"丢弃明细：生产 {len(p_disc)} / 回放 {len(i_disc)} "
          f"逐条相同={out['compare']['discarded_ids_equal']}")
    print(f"漏斗合计相同={out['compare']['funnel_total_equal']} "
          f"（生产 history {sum((prod['funnel_history'] or {}).values())} + "
          f"since_start {sum((prod['funnel_since_start'] or {}).values())}）"
          f"｜水位相同={out['compare']['watermark_equal']}")
    if funnel_delta:
        print(f"   漏斗差异项：{funnel_delta}")
    print(f"生产文件未动={out['production_untouched']}")
    print(f"输出：{p}")
    print(f"逐币条件值：{cond_p}（{len(per_symbol_cond)} 币）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

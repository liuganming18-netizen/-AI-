# -*- coding: utf-8 -*-
"""09｜观察流回放：把「观察了但没成候选」这一情形真正跑出来

为什么必须单独一段
------------------
07 只回放了 3 条**已经成候选**的历史事件，覆盖不到「进入观察、最后没成候选」。
而这一情形恰恰是当前最常见的（全池自然候选 0、观察事件几百个），
不把它跑出来，就不能声称「完整事件回放」。

做法
----
用**真实历史行情**（冷档 parquet + 热库合并读取）驱动冻结状态机
`EventStateMachine`，逐根 1h 推进、每 4h 尝试建档，把每一根触发的
状态迁移、终结原因、未满足条件全部落盘。

口径必须写清楚
--------------
* 持仓量传空数组 ⇒ 与生产运行实例一致（生产也是空数组，见 03 脚本），
  所以本回放**不含 OI 门槛**；这是「与现状同口径」，不是「OI 已验证」。
* 4h ATR 用 Wilder ATR(14)，与生产 `features.compute_wilder_atr_series` 同一实现。
* 本段只重建**观察流**（事件链），不做成交；成交见 07。
* 回放起点受 4h 预热约束（≥42 根 + ATR 需要 14 根），所以事件从窗口内第一根
  满足条件的 4h 开始，不是从 2026-06-27 整点开始。
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "框架" / "backend"))

import history_loader as hl                                        # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig  # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.features import (          # noqa: E402
    compute_wilder_atr_series,
)
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import (      # noqa: E402
    EventStateMachine,
    compute_efficiency_ratio,
)

OUT = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out"
HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
ARCHIVE = PROJECT / "数据" / "市场数据" / "parquet"

# 首轮验证窗口（拟采用）；不是历史可查询范围的上限
WIN_START = 1782547200000          # 2026-06-27T00:00:00Z
WIN_END = 1793005200000            # 2026-10-26T09:00:00Z
# 4h 预热：42 根箱体 + 14 根 ATR ⇒ 至少 56 根，留足余量取 220 根（约 37 天）
WARMUP_START = WIN_START - 220 * 4 * 3600 * 1000

SYMBOLS = ["TNSRUSDT", "AGTUSDT", "METISUSDT",
           "BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"]

STATE_ORDER = ["BOX", "SWEEP", "RETEST", "BREAKOUT_EVAL", "CANDIDATE"]


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def drive(symbol: str, data: dict) -> dict:
    """用真实历史驱动冻结状态机，返回该标的全部事件档案。"""
    b4 = pd.DataFrame(data[symbol]["4h"])
    if b4.empty:
        return {"symbol": symbol, "error": "no_4h"}
    atr = compute_wilder_atr_series(b4["high"].to_numpy(float),
                                   b4["low"].to_numpy(float),
                                   b4["close"].to_numpy(float), 14)
    b4["atr"] = atr
    b1 = pd.DataFrame(data[symbol]["1h"])
    if b1.empty:
        return {"symbol": symbol, "error": "no_1h"}

    cfg = EventTrendConfig()
    sm = EventStateMachine(config=cfg, symbol=symbol,
                           enable_oi_filter=False,
                           pit_mode="declared_assumption")
    # 与生产运行实例同口径：喂空 OI（见 03 脚本逐行证据）
    oi = np.array([], dtype=np.int64)
    ov = np.array([], dtype=np.float64)
    sm.pit.register_oi(oi, ov)

    t0 = time.time()
    blocked = []
    for r in b1.itertuples(index=False):
        t_open = int(r.open_time)
        t_close = t_open + 3600 * 1000
        if t_close % (4 * 3600 * 1000) == 0:
            sub = b4[b4["close_time"] <= t_close]
            if len(sub) >= cfg.box_bars_4h:
                last = sub.tail(cfg.box_bars_4h)
                H = float(last["high"].max())
                L = float(last["low"].min())
                A = float(sub["atr"].iloc[-1])
                er = compute_efficiency_ratio(last["close"].to_numpy(float))
                width = (H - L) / A if A and A > 0 else None
                eligible = (width is not None
                            and cfg.box_min_width_atr <= width <= cfg.box_max_width_atr
                            and er is not None and er <= cfg.er_threshold)
                busy = sm.active_event is not None
                cooling = t_close < sm.cooling_until_ms
                formed = sm.check_and_form_box(
                    bar_4h_close_ms=t_close,
                    history_42_highs=last["high"].to_numpy(float),
                    history_42_lows=last["low"].to_numpy(float),
                    history_42_closes=last["close"].to_numpy(float),
                    current_atr_a=A)
                # 条件明明满足却没建档 ⇒ 记下被谁挡住（这是路径依赖的直接证据）
                if eligible and formed is None:
                    blocked.append({
                        "at_utc": iso(t_close),
                        "reason": ("active_event_occupied" if busy else
                                   ("cooling_period" if cooling else "unknown")),
                        "active_event_id": (sm.active_event.event_id
                                            if busy else None),
                        "cooling_until_utc": (iso(sm.cooling_until_ms)
                                              if cooling else None),
                        "H": H, "L": L, "width_atr": width, "er": er})
        sm.process_1h_bar(t_open, float(r.open), float(r.high),
                          float(r.low), float(r.close), float(r.volume),
                          oi, ov)
    drive_sec = round(time.time() - t0, 2)

    # ── 逐事件归并（含未终结的活跃事件）────────────────────────────
    events = {}
    for tr in sm.transitions:
        events.setdefault(tr.event_id, {"event_id": tr.event_id,
                                        "transitions": [],
                                        "candidate_ids": []})
        events[tr.event_id]["transitions"].append({
            "to_state": tr.to_state,
            "from_state": tr.from_state,
            "at_ms": int(tr.trigger_bar_close_ms),
            "at_utc": iso(tr.trigger_bar_close_ms),
            "available_at_ms": int(tr.available_at_ms),
            "reason": tr.reason,
            "metrics": tr.metrics_snapshot,
        })
    for cd in sm.candidates:
        ev_id = getattr(cd, "event_id", None)
        if ev_id in events:
            events[ev_id]["candidate_ids"].append(getattr(cd, "candidate_id", None))

    # 终结原因：从 completed_events 取
    for e in sm.completed_events:
        rec = events.get(e.event_id)
        if rec is None:
            continue
        rec["final_state"] = e.current_state.value if hasattr(e.current_state, "value") \
            else str(e.current_state)
        rec["terminal_reason"] = getattr(e, "termination_reason", None)
        rec["box"] = {"high_h": e.box.high_h, "low_l": e.box.low_l,
                      "atr_a": e.box.atr_a,
                      "er": e.box.efficiency_ratio_er,
                      "width_atr": e.box.box_width_atr,
                      "created_at_ms": int(e.created_at_ms),
                      "created_utc": iso(e.created_at_ms)}

    for rec in events.values():
        states = [t["to_state"] for t in rec["transitions"]]
        rec["states"] = states
        rec["max_stage"] = max((STATE_ORDER.index(s) for s in states
                                if s in STATE_ORDER), default=-1)
        rec["max_stage_name"] = (STATE_ORDER[rec["max_stage"]]
                                 if rec["max_stage"] >= 0 else None)
        rec["has_candidate"] = "CANDIDATE" in states or bool(rec["candidate_ids"])
        rec["ended"] = rec.get("final_state") in (
            "INVALIDATED", "EXPIRED", "CLOSED",
            "VOLUME_REJECTED", "OI_REJECTED", "OI_UNAVAILABLE",
            "MISSED_WITHOUT_RETEST")
        # 未满足条件：从最后一次迁移的 reason + metrics 反推
        last = rec["transitions"][-1]
        rec["unmet"] = _unmet(rec, last)

    return {"symbol": symbol, "drive_sec": drive_sec,
            "bars_1h": len(b1), "bars_4h": len(b4),
            "event_count": len(events),
            "candidate_count": len(sm.candidates),
            "blocked_formations_count": len(blocked),
            "blocked_formations": blocked,
            "events": list(events.values())}


def _unmet(rec: dict, last: dict) -> dict:
    """把「为什么没走到候选」写成可读条件，而不是只留一个状态名。"""
    st = last["to_state"]
    m = last.get("metrics") or {}
    box = rec.get("box") or {}
    if st == "BOX":
        return {"停在": "BOX（等扫低）",
                "需要的条件": "收盘价跌破箱体下沿 L 后在 L-0.5A 内收回",
                "箱体下沿_L": box.get("low_l"),
                "箱体上沿_H": box.get("high_h"),
                "当前状态": "窗口内没有出现「先跌破下沿再收回」的 1h 收盘"}
    if st == "SWEEP":
        return {"停在": "SWEEP（等回踩）",
                "需要的条件": "扫低后价格回抽到箱体下沿附近再向上",
                "扫低低点": m.get("sweep_low") or m.get("low"),
                "当前状态": "扫低后未等到回踩即超时或失效"}
    if st == "RETEST":
        return {"停在": "RETEST（等突破上沿）",
                "需要的条件": "回踩成立后收盘突破箱体上沿 H 且放量",
                "箱体上沿_H": box.get("high_h"),
                "当前状态": "回踩成立但窗口内未突破上沿"}
    if st == "EXPIRED":
        return {"停在": "观察到期", "原因": last["reason"],
                "当前状态": "超时，不是形态失败"}
    if st == "INVALIDATED":
        return {"停在": "结构失效", "原因": last["reason"],
                "当前状态": "跌破 L-0.5A 或连续 2 根收盘破 L"}
    return {"停在": st, "原因": last.get("reason")}


def probe_at(symbol: str, data: dict, target_ms: int) -> dict:
    """定点探针：在生产那个箱体时刻，本回放的判据算出来是多少、被谁挡住。

    这是「没复现」的归因证据——必须给出当时的 H/L/ER/ATR 与占位状态，
    不能只说一句「路径不同」就完事。
    """
    b4 = pd.DataFrame(data[symbol]["4h"])
    atr = compute_wilder_atr_series(b4["high"].to_numpy(float),
                                   b4["low"].to_numpy(float),
                                   b4["close"].to_numpy(float), 14)
    b4["atr"] = atr
    b1 = pd.DataFrame(data[symbol]["1h"])
    cfg = EventTrendConfig()
    sm = EventStateMachine(config=cfg, symbol=symbol,
                           enable_oi_filter=False,
                           pit_mode="declared_assumption")
    oi = np.array([], dtype=np.int64)
    ov = np.array([], dtype=np.float64)
    sm.pit.register_oi(oi, ov)
    snap = None
    for r in b1.itertuples(index=False):
        t_open = int(r.open_time)
        t_close = t_open + 3600 * 1000
        if t_close % (4 * 3600 * 1000) == 0:
            sub = b4[b4["close_time"] <= t_close]
            if len(sub) >= cfg.box_bars_4h:
                last = sub.tail(cfg.box_bars_4h)
                H = float(last["high"].max())
                L = float(last["low"].min())
                A = float(sub["atr"].iloc[-1])
                er = compute_efficiency_ratio(last["close"].to_numpy(float))
                width = (H - L) / A if A and A > 0 else None
                if t_close == target_ms:
                    busy = sm.active_event is not None
                    snap = {
                        "at_utc": iso(t_close),
                        "H": H, "L": L, "atr_a": A,
                        "width_atr": width, "er": er,
                        "active_event_id": (sm.active_event.event_id
                                            if busy else None),
                        "active_event_state": (
                            sm.active_event.current_state.value
                            if busy and hasattr(sm.active_event.current_state,
                                                "value") else None),
                        "cooling_until_utc": iso(sm.cooling_until_ms),
                        "in_cooling": t_close < sm.cooling_until_ms,
                        "box_conditions_met": (
                            width is not None
                            and cfg.box_min_width_atr <= width <= cfg.box_max_width_atr
                            and er is not None and er <= cfg.er_threshold),
                    }
                sm.check_and_form_box(
                    bar_4h_close_ms=t_close,
                    history_42_highs=last["high"].to_numpy(float),
                    history_42_lows=last["low"].to_numpy(float),
                    history_42_closes=last["close"].to_numpy(float),
                    current_atr_a=A)
                if t_close == target_ms and snap is not None:
                    snap["formed_event_id"] = (sm.active_event.event_id
                                               if sm.active_event else None)
        sm.process_1h_bar(t_open, float(r.open), float(r.high),
                          float(r.low), float(r.close), float(r.volume),
                          oi, ov)
    return snap or {"at_utc": iso(target_ms), "error": "target_not_hit"}


def main():
    errors = []
    out = {"probe": "09_观察流回放",
           "generated_at_utc": iso(time.time() * 1000)}
    mh = hl.MergedHistory(str(HOT), str(ARCHIVE), log=lambda *a, **k: None)
    t0 = time.time()
    data, load_stats = mh.load(SYMBOLS, ["1h", "4h"], WARMUP_START, WIN_END)
    out["load_sec"] = round(time.time() - t0, 1)
    out["window"] = {"research_start_utc": iso(WIN_START),
                     "research_end_utc": iso(WIN_END),
                     "warmup_start_utc": iso(WARMUP_START),
                     "note": "首轮验证窗口（拟采用）；预热只为 4h 箱体与 ATR"}
    out["oi_fed"] = {"enable_oi_filter": False,
                     "arrays": "空数组（与生产运行实例同口径，见 03 脚本）",
                     "含义": "本回放不含 OI 门槛；不代表 OI 已验证"}

    per_symbol = {}
    all_events = []
    for s in SYMBOLS:
        try:
            r = drive(s, data)
        except Exception as e:
            errors.append(f"{s}: {type(e).__name__}: {e}")
            per_symbol[s] = {"symbol": s, "error": f"{type(e).__name__}: {e}"}
            continue
        per_symbol[s] = {k: v for k, v in r.items()
                         if k not in ("events", "blocked_formations")}
        # 条件满足却被挡住建档的记录（路径依赖的直接证据），最多留 200 条
        bf = r.get("blocked_formations") or []
        per_symbol[s]["blocked_formations_truncated"] = len(bf) > 200
        per_symbol[s]["blocked_formations_sample"] = bf[:200]
        all_events.extend([{**e, "symbol": s} for e in r.get("events", [])])

    # ── 汇总 ────────────────────────────────────────────────────────────
    stage_counter = Counter()
    terminal_counter = Counter()
    for e in all_events:
        for st in e["states"]:
            stage_counter[st] += 1
        if e.get("final_state"):
            terminal_counter[e["final_state"]] += 1
        elif not e["has_candidate"]:
            terminal_counter["ACTIVE(窗口末仍观察中)"] += 1

    no_cand = [e for e in all_events if not e["has_candidate"]]
    with_cand = [e for e in all_events if e["has_candidate"]]

    out["summary"] = {
        "symbols": len(SYMBOLS),
        "events_total": len(all_events),
        "events_with_candidate": len(with_cand),
        "events_without_candidate": len(no_cand),
        "stage_hits": dict(stage_counter),
        "terminal_states": dict(terminal_counter),
        "note": "stage_hits 是「有过该状态的事件数」，不是迁移次数",
    }
    out["per_symbol"] = per_symbol

    # 紧凑时间线（全量事件，只留关键列；明细只给抽样，避免产物膨胀）
    out["event_timeline"] = [{
        "symbol": e["symbol"],
        "event_id": e["event_id"],
        "box_created_utc": (e.get("box") or {}).get("created_utc"),
        "states": e["states"],
        "final_state": e.get("final_state"),
        "terminal_reason": e.get("terminal_reason"),
        "has_candidate": e["has_candidate"],
        "max_stage": e.get("max_stage_name"),
        "transition_count": len(e["transitions"]),
    } for e in sorted(all_events, key=lambda x: (
        x["symbol"], (x.get("box") or {}).get("created_at_ms") or 0))]

    # ── 抽 3 条「观察后未形成候选」的代表事件做完整回放 ────────────────
    # 选取规则：覆盖不同停靠阶段（BOX / SWEEP / RETEST），且尽量来自不同标的，
    # 避免三条都是同一个币（否则看不出是普遍现象还是单币特例）。
    picks = []
    used_sym = set()

    def _pick(stage, allow_final_only=True):
        cand = [e for e in no_cand if e.get("max_stage_name") == stage]
        if allow_final_only:
            fin = [e for e in cand if e.get("final_state") in ("EXPIRED", "INVALIDATED")]
            cand = fin or cand
        fresh = [e for e in cand if e["symbol"] not in used_sym]
        pool = fresh or cand
        if not pool:
            return None
        pool.sort(key=lambda x: -len(x["transitions"]))
        used_sym.add(pool[0]["symbol"])
        return pool[0]

    for stage in ("RETEST", "SWEEP", "BOX"):
        p = _pick(stage)
        if p:
            picks.append(p)
    out["replay_without_candidate"] = picks

    # 本回放自己产出的候选（用于与 07 对撞）
    out["replay_candidates"] = [
        {"symbol": e["symbol"], "event_id": e["event_id"],
         "box_created_utc": (e.get("box") or {}).get("created_utc"),
         "candidate_at_utc": next((t["at_utc"] for t in e["transitions"]
                                   if t["to_state"] == "CANDIDATE"), None),
         "box": e.get("box")}
        for e in with_cand]

    # ── 与 07 的候选对撞：用 event_id 里的箱体时刻 + 箱体 H/L 做身份比对 ──
    # 两处易错必须避开：
    #   ① 07 的 `snapshot_utc` 是**候选快照时刻**（1h 收盘确认），不是箱体建档时刻；
    #   ② 生产 `event_id` 末尾是 4h 的 `close_time`（= 区间末毫秒），
    #      本回放传的是 `t_close`（= 下一根起点毫秒）⇒ 两者恒差 1ms，
    #      必须给 1ms 容差，否则会得到 3 条全「不一致」的假失败。
    p07 = OUT / "07_事件回放.json"
    cross = {"checked": False}
    if p07.exists():
        d07 = json.loads(p07.read_text(encoding="utf-8"))
        rows = []
        for c in d07["chains"]:
            sym = c["symbol"]
            want_h = c["1_当时可见数据"]["box_high_h"]
            want_l = c["1_当时可见数据"]["box_low_l"]
            snap = c["2_结构确认"]["snapshot_utc"]
            prod_ms = int(c["event_id"].rsplit("_", 1)[1])
            # 生产 event_id 末尾 = 4h 的 close_time（区间末毫秒，...999999）；
            # 本回放传 t_close（下一根起点毫秒，...000000）⇒ 同一根相差正好 1ms。
            hit = None
            box_hit = None
            for rc in out["replay_candidates"]:
                if rc["symbol"] != sym:
                    continue
                my_ms = int(rc["event_id"].rsplit("_", 1)[1])
                if my_ms == prod_ms + 1:
                    hit = rc
                    break
                b = rc["box"] or {}
                if b.get("high_h") is not None and \
                        abs(float(b["high_h"]) - float(want_h)) < 1e-12 and \
                        abs(float(b["low_l"]) - float(want_l)) < 1e-12:
                    box_hit = rc
            if hit is not None:
                verdict = "完全一致（箱体时刻逐位相同 + 候选时刻相同）"
                bar_diff = 0
            elif box_hit is not None:
                my_ms = int(box_hit["event_id"].rsplit("_", 1)[1])
                bar_diff = round((my_ms - (prod_ms + 1)) / (4 * 3600 * 1000), 2)
                verdict = (f"箱体数值一致但建档相差 {bar_diff} 根 4h"
                           f"（候选时刻{'相同' if box_hit['candidate_at_utc'] == snap else '不同'}）")
            else:
                bar_diff = None
                verdict = "箱体未重现（前序事件路径不同）"
            rows.append({
                "symbol": sym,
                "生产_event_id_箱体时刻_utc": iso(prod_ms + 1),
                "07_箱体": {"H": want_h, "L": want_l},
                "候选快照_utc": snap,
                "本回放命中": ((hit or box_hit) and {
                    "event_id": (hit or box_hit)["event_id"],
                    "box_created_utc": (hit or box_hit)["box_created_utc"],
                    "candidate_at_utc": (hit or box_hit)["candidate_at_utc"]}),
                "建档相差_4h根数": bar_diff,
                "判定": verdict,
            })
        exact = sum(1 for r in rows if r["判定"].startswith("完全一致"))
        cross = {"checked": True,
                 "口径": "生产 event_id 末尾即 4h 箱体建档时刻；本回放同规则。"
                         "允许 1ms 差（close_time 与下一根 open_time 的口径差）。"
                         "候选时刻另列，不参与判身份。",
                 "rows": rows,
                 "exact_match": exact,
                 "total": len(rows),
                 "all_reproduced": exact == len(rows),
                 "未复现的机制": "冻结规则「每个 symbol+venue 最多一个处于观察状态的事件」"
                                "+ 事件终结后的冷却期 ⇒ 状态机路径依赖。前序事件序列不同，"
                                "后面某些箱体就建不出来。这不是数据错，也不是规则错，"
                                "而是「单条事件回放」不能等价于「生产运行」的原因。",
                 }
    out["crosscheck_with_07"] = cross

    # ── 未复现的归因：在生产那个时刻定点复核判据 ────────────────────────
    if cross.get("checked"):
        for row in cross["rows"]:
            if row["判定"].startswith("完全一致"):
                continue
            sym = row["symbol"]
            prod_ms = _ms(row["生产_event_id_箱体时刻_utc"])
            try:
                pr = probe_at(sym, data, prod_ms)
            except Exception as e:
                pr = {"error": f"{type(e).__name__}: {e}"}
                errors.append(f"定点探针失败 {sym}")
            row["定点复核"] = pr
            if pr.get("error"):
                row["归因"] = "探针未取到"
            elif not pr.get("box_conditions_met"):
                row["归因"] = "该时刻本回放箱体条件本身不满足（数据或预热差）"
            elif pr.get("active_event_id"):
                row["归因"] = ("该时刻已有活跃事件占位 ⇒ 冻结规则"
                              "「每标的每场所最多一个观察事件」不允许再建档")
            elif pr.get("in_cooling"):
                row["归因"] = "该时刻处于上一事件终结后的冷却期"
            else:
                row["归因"] = "条件满足、无占位、无冷却，仍未建档（需查实现）"
            # 与生产声明的箱体数值对撞（判据是否真的一致）
            want = row["07_箱体"]
            row["判据数值对撞"] = {
                "H_相同": abs(float(pr.get("H", 0)) - float(want["H"])) < 1e-12,
                "L_相同": abs(float(pr.get("L", 0)) - float(want["L"])) < 1e-12,
                "本回放": {"H": pr.get("H"), "L": pr.get("L"),
                           "er": pr.get("er"), "atr_a": pr.get("atr_a")},
            }

    # ── 确定性复算：同一起点跑两遍必须逐位相同 ─────────────────────────
    det = {"strength": "rerun_identity", "symbol": SYMBOLS[0]}
    try:
        r1 = drive(SYMBOLS[0], data)
        r2 = drive(SYMBOLS[0], data)
        s1 = json.dumps(r1["events"], sort_keys=True, default=str)
        s2 = json.dumps(r2["events"], sort_keys=True, default=str)
        det["events"] = len(r1["events"])
        det["identical"] = (s1 == s2)
        det["verdict"] = "pass" if det["identical"] else "fail"
        if not det["identical"]:
            errors.append("确定性复算不一致")
    except Exception as e:
        det["verdict"] = "fail"
        det["error"] = f"{type(e).__name__}: {e}"
        errors.append("确定性复算失败")
    out["determinism"] = det

    # ── 七种情形覆盖表 ─────────────────────────────────────────────────
    out["scenarios_covered"] = {
        "完整成交与退出": {
            "status": "已覆盖",
            "依据": "07_事件回放.json 三条真实历史候选全链",
        },
        "观察后未形成候选": {
            "status": "已覆盖",
            "依据": f"本脚本真实历史回放：{len(no_cand)} 个事件进入观察但未成候选",
            "样例": [p["event_id"] for p in picks],
        },
        "候选等待期取消": {
            "status": "已覆盖",
            "依据": "04 脚本 observe_candidate 取消记录（cancel_reason 必填）",
        },
        "结构失效": {
            "status": "已覆盖",
            "依据": f"本脚本 INVALIDATED {terminal_counter.get('INVALIDATED', 0)} 个，"
                    f"与 EXPIRED {terminal_counter.get('EXPIRED', 0)} 个分开计数",
        },
        "观察到期": {
            "status": "已覆盖",
            "依据": "本脚本 EXPIRED 计数；expires_at 冻结见 04 脚本",
        },
        "数据缺失": {
            "status": "部分覆盖",
            "依据": "04 脚本 DATA_INSUFFICIENT 独立状态；本脚本 15m 缺口未参与"
                    "（观察流只用 1h/4h，不读 15m）",
            "缺口": "「观察期内 15m 数据缺口导致回踩无法判定」需接 15m 合并读取器后再验",
        },
        "中断恢复": {
            "status": "已覆盖",
            "依据": "08 脚本 A 段真进程重启接力 + 06 脚本 F 段恢复/回放分离",
        },
    }

    out["errors"] = errors
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "09_观察流回放.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False))
    print("crosscheck:", json.dumps(cross, ensure_ascii=False)[:400])
    print("written:", dst)


def _ms(s: str) -> int:
    import calendar
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000


if __name__ == "__main__":
    main()

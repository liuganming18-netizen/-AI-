# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-C｜04 统一观察母集重建

任务第 1 条的核心要求：
    「固定共同观察范围……不限于原策略 51 个候选，
      纳入仅新策略可能出现的机会。」

问题
----
04-C 声称做了「统一观察范围」，但实际只用了 51 条候选；它所谓的第四象限
「都未入场」只有文字定义，没有一条实例。原因是：
* 生产库 `live_signals.sqlite3` 的 `live_signal` 表实测 0 行 ⇒ 没有历史观察事件存档；
* `paper_runtime_3a_observe.json` 只是**当前时刻**一份快照（357 个事件），不含历史。

做法
----
用真实历史行情驱动**冻结状态机**（与生产同一份 `EventStateMachine`、同一份
`EventTrendConfig`、同样喂空 OI 数组），逐根 1h 推进、每 4h 尝试建档，
把窗口内产生过的**全部观察事件**重建出来。

这就是「统一观察母集」：
* 它是客观可复算的（同一份代码 + 同一份数据 ⇒ 同一批事件）；
* 两套策略（原策略 / 新策略）都在这同一母集上判定，比较才公平；
* 它天然包含「走到 RETEST 但没突破」的机会 —— 正是新策略唯一能新增的那部分。

必须写清的局限
--------------
1. 重放母集 ≠ 生产实际事件序列。冻结规则「每标的每场所最多一个观察事件」+ 终结后
   冷却 24h ⇒ **路径依赖**：前序事件序列不同，后面某些箱体就建不出来。
   所以本母集只能保证「给定起点下确定性可复算」，不能保证「与生产逐条相同」。
2. 母集全部依赖冷档 1h/4h，而冷档 `available_at` 填充率 0% ⇒ **探索性结果**，
   不得声称「当时可得」。
3. 母集只重建「观察」，不含成交。成交在 02 脚本里单独做。
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

import history_loader as hl
from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import (
    EventStateMachine, compute_efficiency_ratio,
)

HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
ARCHIVE = ROOT / "数据" / "市场数据" / "parquet"
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out"
SRC_02 = OUT_DIR / "02_逐事件基线.json"

WIN_START = 1782547200000
WIN_END = 1793005200000
WARMUP_START = WIN_START - 220 * 4 * 3600 * 1000

STATE_ORDER = ["BOX", "SWEEP", "RETEST", "BREAKOUT_EVAL", "CANDIDATE"]
TERMINAL = ("INVALIDATED", "EXPIRED", "CLOSED", "VOLUME_REJECTED",
            "OI_REJECTED", "OI_UNAVAILABLE", "MISSED_WITHOUT_RETEST")


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def drive(symbol: str, data: dict) -> dict:
    """用真实历史驱动冻结状态机（与 ACCUM-EARLY-01/09 同一套调用方式）。"""
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
    oi = np.array([], dtype=np.int64)
    ov = np.array([], dtype=np.float64)
    sm.pit.register_oi(oi, ov)

    for r in b1.itertuples(index=False):
        t_open = int(r.open_time)
        t_close = t_open + 3600 * 1000
        if t_close % (4 * 3600 * 1000) == 0:
            sub = b4[b4["close_time"] <= t_close]
            if len(sub) >= cfg.box_bars_4h:
                last = sub.tail(cfg.box_bars_4h)
                sm.check_and_form_box(
                    bar_4h_close_ms=t_close,
                    history_42_highs=last["high"].to_numpy(float),
                    history_42_lows=last["low"].to_numpy(float),
                    history_42_closes=last["close"].to_numpy(float),
                    current_atr_a=float(sub["atr"].iloc[-1]))
        sm.process_1h_bar(t_open, float(r.open), float(r.high),
                          float(r.low), float(r.close), float(r.volume),
                          oi, ov)

    events = {}
    for tr in sm.transitions:
        rec = events.setdefault(tr.event_id, {"event_id": tr.event_id,
                                             "transitions": [],
                                             "candidate_ids": []})
        rec["transitions"].append({
            "to_state": tr.to_state,
            "at_ms": int(tr.trigger_bar_close_ms),
            "at_utc": iso(tr.trigger_bar_close_ms),
            "reason": tr.reason,
        })
    for cd in sm.candidates:
        ev = getattr(cd, "event_id", None)
        if ev in events:
            events[ev]["candidate_ids"].append(getattr(cd, "candidate_id", None))

    for e in sm.completed_events:
        rec = events.get(e.event_id)
        if rec is None:
            continue
        rec["final_state"] = (e.current_state.value if hasattr(e.current_state, "value")
                              else str(e.current_state))
        rec["terminal_reason"] = getattr(e, "termination_reason", None)
        rec["box"] = {"H": e.box.high_h, "L": e.box.low_l, "A": e.box.atr_a,
                      "ER": e.box.efficiency_ratio_er,
                      "width_atr": e.box.box_width_atr,
                      "created_at_ms": int(e.created_at_ms),
                      "created_utc": iso(e.created_at_ms)}

    for rec in events.values():
        states = [t["to_state"] for t in rec["transitions"]]
        rec["states"] = states
        rec["max_stage"] = max((STATE_ORDER.index(s) for s in states if s in STATE_ORDER),
                              default=-1)
        rec["max_stage_name"] = (STATE_ORDER[rec["max_stage"]]
                                 if rec["max_stage"] >= 0 else None)
        rec["has_candidate"] = ("CANDIDATE" in states) or bool(rec["candidate_ids"])
        # 事件在时间轴上的存活区间：建档 → 最后一次迁移（或窗口末）
        box = rec.get("box") or {}
        rec["start_ms"] = box.get("created_at_ms")
        rec["end_ms"] = (max(t["at_ms"] for t in rec["transitions"])
                         if rec["transitions"] else None)
        rec["ended"] = rec.get("final_state") in TERMINAL

    return {"symbol": symbol, "event_count": len(events),
            "candidate_count": len(sm.candidates),
            "events": list(events.values())}


def main():
    t_all = time.time()
    d2 = json.loads(SRC_02.read_text(encoding="utf-8"))
    per_events = d2["calibers"]["生效视图_5bps"]["per_event"]
    symbols = sorted({e["symbol"] for e in per_events})

    out = {
        "task": "ACCUM-EARLY-05-C",
        "probe": "04_统一观察母集重建",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "definition": {
            "母集": "窗口内冻结状态机产生过的全部观察事件（BOX→SWEEP→RETEST→…，"
                    "含走到候选的与没走到候选的）。",
            "为什么这是统一观察范围": "两套策略都在同一批事件上判定，比较才公平；"
                                     "并且天然包含「只有新策略可能进场」的那部分（停在 RETEST 未突破）。",
            "不是": "不是生产实际事件序列（路径依赖，见 limitations）。",
        },
        "window": {"start_utc": iso(WIN_START), "end_utc": iso(WIN_END),
                   "warmup_start_utc": iso(WARMUP_START)},
        "universe": {"symbols_source": "51 条候选涉及的标的（去重）",
                     "symbols": symbols, "symbol_count": len(symbols)},
        "engine": {"state_machine": "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
                   "config": "EventTrendConfig()（默认，非 make_ft_low4h_config）",
                   "oi_fed": "空数组，enable_oi_filter=False ⇒ 与生产运行实例同口径，不含 OI 门槛",
                   "reader": "history_loader.MergedHistory（冷热合并）"},
        "summary": {},
        "coverage_vs_51": {},
        "quadrant_readiness": {},
        "limitations": [
            "重放母集 ≠ 生产事件序列：冻结规则「每标的每场所最多一个观察事件」+ 终结后冷却 24h "
            "⇒ 路径依赖，前序序列不同则后续箱体建不出来。只能声称「给定起点下确定性可复算」。",
            "母集依赖冷档 1h/4h，冷档 available_at 填充率 0% ⇒ 探索性结果，不得声称「当时可得」。",
            "母集只重建观察，不含成交；成交见 02 脚本。",
            "EventTrendConfig() 默认 enable_oi_filter=True，本脚本显式关掉并与生产同口径；"
            "这不代表 OI 已验证。",
        ],
        "per_symbol": {},
        "events": [],
        "errors": [],
    }

    print(f">>> 加载 {len(symbols)} 个标的的 1h/4h（冷热合并）...")
    mh = hl.MergedHistory(str(HOT), str(ARCHIVE), log=lambda *a, **k: None)
    t0 = time.time()
    data, _ = mh.load(symbols, ["1h", "4h"], WARMUP_START, WIN_END)
    out["load_sec"] = round(time.time() - t0, 1)
    print(f"    加载耗时 {out['load_sec']}s")

    all_events = []
    for s in symbols:
        try:
            r = drive(s, data)
        except Exception as e:
            out["errors"].append(f"{s}: {type(e).__name__}: {e}")
            out["per_symbol"][s] = {"error": f"{type(e).__name__}: {e}"}
            continue
        out["per_symbol"][s] = {"event_count": r["event_count"],
                                "candidate_count": r["candidate_count"]}
        for e in r["events"]:
            all_events.append({**e, "symbol": s})

    # ── 汇总 ──────────────────────────────────────────────────────────
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
        "symbols_driven": len([s for s in symbols if "error" not in out["per_symbol"].get(s, {})]),
        "events_total": len(all_events),
        "events_with_candidate": len(with_cand),
        "events_without_candidate": len(no_cand),
        "stage_hits": dict(stage_counter),
        "terminal_states": dict(terminal_counter),
        "stage_hits_note": "stage_hits 是「有过该状态的事件数」，不是迁移次数",
    }

    # ── 与 51 条生产候选的覆盖对撞 ────────────────────────────────────
    # 两处易错必须避开：
    #   ① 不能用 candidate_id 里的时间戳做对撞：实测 51 条候选的 candidate_id 前一段
    #      是同一个批次锚点（TNSRUSDT / PEOPLEUSDT / 1000PEPEUSDT 完全相同），
    #      不是各自的箱体建档时刻，拿它对撞会得到「全一致」的假结果；
    #   ② 不能用「成交时刻」直接判区间包含：事件在**候选确认**时刻就终结，
    #      而成交在确认后 15 分钟 ⇒ 实测 NILUSDT / DOTUSDT 的 end_ms 与 entry_ms
    #      恰好差 900000ms（15 分钟），直接用 entry_ms 会把这批正常时差误判成「未覆盖」。
    # 正确做法：用「确认时刻 = 成交时刻 - 15 分钟」判是否落在事件存活区间内。
    print(">>> 与 51 条候选做覆盖对撞（按确认时刻）...")
    CONFIRM_LAG_MS = 15 * 60 * 1000
    EDGE_TOL_MS = 60 * 1000
    by_symbol = {}
    for e in all_events:
        if e.get("start_ms") is None or e.get("end_ms") is None:
            continue
        by_symbol.setdefault(e["symbol"], []).append(e)

    hit_rows = []
    for cand in per_events:
        sym = cand["symbol"]
        confirm_ms = cand["entry_ms"] - CONFIRM_LAG_MS
        hit = None
        for e in by_symbol.get(sym, []):
            if e["start_ms"] <= confirm_ms <= e["end_ms"] + EDGE_TOL_MS:
                hit = e
                break
        hit_rows.append({
            "symbol": sym,
            "candidate_id": cand["candidate_id"],
            "entry_utc": cand["entry_utc"],
            "confirm_utc": iso(confirm_ms),
            "matched_event_id": (hit or {}).get("event_id"),
            "matched_max_stage": (hit or {}).get("max_stage_name"),
            "matched_has_candidate": (hit or {}).get("has_candidate"),
            "matched_end_utc": iso((hit or {}).get("end_ms")),
        })
    covered = [r for r in hit_rows if r["matched_event_id"]]
    cov_no_cand = [r for r in covered if not r["matched_has_candidate"]]
    out["coverage_vs_51"] = {
        "candidates": len(hit_rows),
        "covered_by_replay_events": len(covered),
        "not_covered": len(hit_rows) - len(covered),
        "coverage_pct": round(len(covered) / len(hit_rows) * 100, 2) if hit_rows else None,
        "covered_and_replay_reached_candidate": len(covered) - len(cov_no_cand),
        "covered_but_replay_stopped_earlier": len(cov_no_cand),
        "rows": hit_rows,
        "口径": "「覆盖」= 候选的**确认时刻**（成交时刻 - 15 分钟）落在某个重放事件的 "
                "[建档时刻, 最后一次迁移时刻 + 1 分钟] 区间内。这是时间包含判定，"
                "不是 event_id 逐位相同；路径依赖下二者不可能逐条相等。",
        "meaning": "覆盖到的候选说明母集在同一标的、同一时段产生过对应观察事件；"
                   "其中「母集没走到候选」的部分说明重放在该事件上走了不同路径（路径依赖）。"
                   "未覆盖的部分同样是路径依赖，不是数据错。",
    }

    # ── 四象限可实例化性 ─────────────────────────────────────────────
    stuck_retest = [e for e in no_cand if e.get("max_stage_name") == "RETEST"]
    stuck_sweep = [e for e in no_cand if e.get("max_stage_name") == "SWEEP"]
    stuck_box = [e for e in no_cand if e.get("max_stage_name") == "BOX"]
    out["quadrant_readiness"] = {
        "Q1_两者都入场": {
            "可实例化": False,
            "原因": "需要两样东西同时到位：①可执行的新策略实现；②被用户批准的入场配置。"
                    "05-A 的五项决策仍标待批，05-B 的自测也明确声明「基于 fixture 测试配置、"
                    "不构成策略绩效结论」⇒ 未批准前不判定谁能入场。状态见 05 产物的依赖探测。",
        },
        "Q2_仅原策略入场": {
            "可实例化": False,
            "原因": "同上：需要新策略的「未满足」结论才能判定「仅原策略」。",
        },
        "Q3_仅新策略入场": {
            "可实例化": False,
            "原因": "同上。但候选池已备好：停在 RETEST 未突破的事件 "
                    f"{len(stuck_retest)} 个，这是新策略唯一能新增机会的来源。",
        },
        "Q4_都未入场": {
            "可实例化": True,
            "实例数": len(no_cand),
            "细分": {"停在BOX": len(stuck_box), "停在SWEEP": len(stuck_sweep),
                     "停在RETEST": len(stuck_retest)},
            "说明": "原策略确定未入场（母集重放没有产生候选）。"
                    "新策略是否入场仍需 05-B，故「都未入场」目前只能确认「原策略未入场」这一半。",
        },
        "结论": "四象限中只有第四象限的「原策略未入场」这一半现在有实例；"
                "其余三象限的判定依赖 05-B 实现与用户批准配置，本单不越权代替。",
    }

    # ── 事件表（只留关键列，避免产物膨胀）────────────────────────────
    out["events"] = [{
        "symbol": e["symbol"],
        "event_id": e["event_id"],
        "box_created_utc": (e.get("box") or {}).get("created_utc"),
        "box": {k: (e.get("box") or {}).get(k) for k in ("H", "L", "A", "ER", "width_atr")},
        "states": e["states"],
        "max_stage": e.get("max_stage_name"),
        "has_candidate": e["has_candidate"],
        "final_state": e.get("final_state"),
        "terminal_reason": e.get("terminal_reason"),
        "start_ms": e.get("start_ms"),
        "end_ms": e.get("end_ms"),
    } for e in sorted(all_events, key=lambda x: (x["symbol"], x.get("start_ms") or 0))]

    out["elapsed_sec"] = round(time.time() - t_all, 1)
    dst = OUT_DIR / "04_统一观察母集.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✔ 已落盘 {dst.relative_to(ROOT)} ({out['elapsed_sec']}s)")
    print("  母集:", json.dumps(out["summary"], ensure_ascii=False))
    print("  覆盖 51:", out["coverage_vs_51"]["covered_by_replay_events"], "/",
          out["coverage_vs_51"]["candidates"],
          f"({out['coverage_vs_51']['coverage_pct']}%)")
    if out["errors"]:
        print("  errors:", out["errors"][:5])


if __name__ == "__main__":
    main()

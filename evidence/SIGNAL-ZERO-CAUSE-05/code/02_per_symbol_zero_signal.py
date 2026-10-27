# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜02 完整币池零信号逐币归因。

在**统一的已收盘时点**（`upto_ms` = 库里最新一根已收盘 1h 的 close_time）逐币回答：
  · 数据是否有效、ATR 是否就绪；
  · 当前阶段、阶段进入时间、阶段内已用时长；
  · 未通过条件、实际值、阈值（建档四道关：上下文 42 根 / 宽度 2~8A / ER<=0.30 / ATR 有效）；
  · 结构失效 / 超时 / 成交量拒绝 / OI 拒绝等淘汰原因（取状态机真实迁移流水，不猜）；
  · 最新处理 K 线时间；
  · 是否被「建档预热门槛」额外拦截（当前实现特有：窗口内 <200 根 4h 不建档）。

方法：逐根驱动 `RealSignalEngine.process_market`（与 bootstrap 同一喂法，FIX-03 已证
「逐根喂 == 一次喂完」526/526 等价），每根之后用**外部影子计算**复算该根 42 根窗口的
宽度/ER，从而把「被闸门拦掉」与「条件本身不满足」区分开 —— 状态机内部看不到闸门前的
那次 return，必须从外部补这一层，否则会把「被拦」误记成「条件不满足」。

产出：
  out/02_逐币归因.csv        526 行（每币一行，可回溯）
  out/02_逐币归因.json       同上 + 逐币明细（最近一次被拦记录、迁移流水）
  out/02_汇总.json           全池漏斗计数 + 归因分类
"""
from __future__ import annotations

import bisect
import importlib
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import numpy as np

import unified_runtime as UR
from 策略.WyckoffMaster.event_trend_v0_1.features import compute_efficiency_ratio


class FastWatcher:
    """与 `_MemWatcher` 同语义，但用 bisect 切片（全池 526×290 根必需）。"""

    def __init__(self, b4, b1):
        self.b4 = b4
        self.b1 = b1
        self._o4 = [int(b["open_time"]) for b in b4]
        self._c4 = [int(b["close_time"]) for b in b4]
        self._o1 = [int(b["open_time"]) for b in b1]
        self._c1 = [int(b["close_time"]) for b in b1]

    def get_closed_bars(self, timeframe, start_open_ms, end_open_ms):
        if timeframe == "4h":
            src, o, c = self.b4, self._o4, self._c4
        else:
            src, o, c = self.b1, self._o1, self._c1
        i = bisect.bisect_left(o, int(start_open_ms))
        j = bisect.bisect_right(c, int(end_open_ms))
        return src[i:j] if j > i else []


def shadow_box_metrics(seg, atr):
    """复算 42 根窗口的宽度与 ER（与 state_machine.check_and_form_box 同式）。"""
    h = np.array([float(x["high"]) for x in seg], dtype=np.float64)
    l = np.array([float(x["low"]) for x in seg], dtype=np.float64)
    c = np.array([float(x["close"]) for x in seg], dtype=np.float64)
    H, L = float(np.max(h)), float(np.min(l))
    width = (H - L) / atr if atr else None
    er = compute_efficiency_ratio(c)
    return {"high_h": H, "low_l": L, "atr_a": atr, "width_atr": width, "er": er}


def classify_reject(m, cfg):
    """按 state_machine 的四道关给出「未通过条件 + 实际值 + 阈值」。"""
    if m["width_atr"] is None:
        return ("ATR 无效", None, None)
    w, er = m["width_atr"], m["er"]
    if not (cfg.box_min_width_atr <= w <= cfg.box_max_width_atr):
        if w < cfg.box_min_width_atr:
            return ("宽度不足(太窄)", round(w, 4), f">= {cfg.box_min_width_atr}")
        return ("宽度超限(太宽)", round(w, 4), f"<= {cfg.box_max_width_atr}")
    if er is None:
        return ("ER 无法计算", None, None)
    if er > cfg.er_threshold:
        return ("ER 过高(有方向性)", round(er, 4), f"<= {cfg.er_threshold}")
    return ("全部满足", {"width_atr": round(w, 4), "er": round(er, 4)}, None)


def replay_symbol(sym, b4_ctx, b4_win, b1, pre, floor_ms, cfg, upto_ms=None):
    """逐根回放一只标的；返回引擎、逐根建档尝试统计、被闸门额外拦截明细。

    `b4_ctx` = 窗口前 (box_bars_4h-1) 根 + 窗口内（只供「最近 42 根」计算）；
    `b4_win` = 窗口内（只有这些 4h 收盘会产生事件）。
    """
    eng = UR.RealSignalEngine(symbol=sym, enable_oi_filter=False, config=cfg)
    eng.history_floor_ms = int(floor_ms)
    # 窗口前预热（与 bootstrap 完全一致：只推进 ATR，不建档）
    for b in pre:
        eng.atr_bars_seen += 1
        eng.last_atr = eng.atr.update(float(b["high"]), float(b["low"]), float(b["close"]))
    w = FastWatcher(b4_ctx, b1)
    n = int(cfg.box_bars_4h)
    gate_n = int(cfg.atr_warmup_bars)
    win_opens = {int(b["open_time"]) for b in b4_win}

    attempts = Counter()
    last_attempt = None
    gate_blocked = []          # 「条件全满足但被预热门槛拦掉」的记录
    formed_ms = []
    last_skip_ms = None

    for idx, bar in enumerate(b4_ctx):
        if int(bar["open_time"]) not in win_opens:
            continue                     # 窗口前上下文：不产生事件
        cl = int(bar["close_time"])
        # 闸门前的三个「本来也不该建档」的前置状态，必须在调用**之前**取样，
        # 否则 process_market 内部推进过状态后会把「被拦」误算成「本可建档」。
        pre_has_event = eng.sm.active_event is not None
        pre_cooling = cl < int(eng.sm.cooling_until_ms or 0)
        pre_usable = not UR.RealSignalEngine._is_zero_flat(bar)

        eng.process_market(cl, w)
        skipped_warmup = (eng.last_box_skipped_warmup_ms == cl)
        if skipped_warmup:
            last_skip_ms = cl

        # 该根是否「本来可以建档」——外部影子复算（上下文够 42 根才谈得上）
        if idx + 1 < n:
            attempts["上下文不足(<42根)"] += 1
            continue
        seg = b4_ctx[idx + 1 - n: idx + 1]
        m = shadow_box_metrics(seg, eng.last_atr)
        verdict, actual, threshold = classify_reject(m, cfg)
        rec = {"ms": cl, "utc": cm.utc(cl), "verdict": verdict,
               "actual": actual, "threshold": threshold,
               "box_gate_bars": eng.box_gate_bars, "atr_bars_seen": eng.atr_bars_seen,
               "width_atr": m["width_atr"], "er": m["er"], "atr": m["atr_a"],
               "pre_has_event": pre_has_event, "pre_cooling": pre_cooling,
               "pre_usable": pre_usable,
               "active_state": (eng.sm.active_event.current_state.value
                                if eng.sm.active_event else "IDLE")}
        last_attempt = rec

        if verdict == "全部满足":
            if pre_has_event:
                # 已有活跃事件 ⇒ 即使闸门开着也不会建档（规范行为，不算额外拦截）
                attempts["已有活跃事件(条件满足不建档)"] += 1
            elif pre_cooling:
                attempts["冷却期内(条件满足不建档)"] += 1
            elif not pre_usable:
                attempts["4h占位行(零成交平价，不建档)"] += 1
            elif skipped_warmup:
                # **唯一**的额外拦截：闸门未开 + 本可建档 + 无其它正当理由
                attempts["条件全满足·被预热门槛拦掉"] += 1
                gate_blocked.append(rec)
            elif eng.sm.active_event is not None and eng.sm.active_event.box.formation_time_ms == cl:
                attempts["建档成功"] += 1
                formed_ms.append(cl)
            else:
                attempts["条件满足但未建档(待查)"] += 1
        else:
            attempts[verdict] += 1

    # ── 补齐尾部 1h（与生产 step() 同口径）──────────────────────────────────
    # 只按 4h 收盘驱动时，最后一根 4h 收盘之后才开盘的 1h 从未被喂进状态机
    # （SIGNAL-HISTORY-FIX-01 的 D1 缺陷同类）。生产里 `step()` 每 2 秒推进到 now，
    # 会把这些 1h 补上；回放必须做同一件事，否则「统一已收盘时点」不成立。
    if upto_ms is not None:
        eng.process_market(int(upto_ms), w)

    return eng, attempts, last_attempt, gate_blocked, formed_ms, last_skip_ms, gate_n

def main() -> int:
    cfg = cm.cfg()
    # 支持 --upto <ms>：用于「与生产 bootstrap 同一截止时刻」的对照回放
    upto = None
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto = int(argv[i + 1])
    tag = ""
    if upto is not None:
        tag = f"_upto{upto}"
    else:
        upto = cm.last_closed_1h_close_ms()
    syms, meta = cm.universe()
    print(f"[cfg] upto={cm.utc(upto)} universe={len(syms)} window={cm.warmup_ms()/cm.H1}h")

    hist, hstats = cm.load_hist(syms, upto)
    print(f"[hist] 4h rows={hstats['4h'].get('rows_materialized')} "
          f"1h rows={hstats['1h'].get('rows_materialized')}")

    rows, details = [], {}
    totals = Counter()
    gate_blocked_symbols = 0
    gate_blocked_total = 0

    for k, sym in enumerate(syms, 1):
        b4_all = hist[sym]["4h"]
        b1 = hist[sym]["1h"]
        pre, b4, win_start = cm.split_window(b4_all, upto)
        # 42 根上下文：窗口前 (box_bars_4h-1) 根 + 窗口内（只供 42 根计算，不产生事件）
        _n = int(cfg.box_bars_4h)
        ctx = [b for b in b4_all
               if int(b["close_time"]) <= int(upto)
               and int(b["open_time"]) >= win_start - (_n - 1) * cm.H4]
        row = {"symbol": sym, "window_start_utc": cm.utc(win_start),
               "bars_4h_window": len(b4), "bars_1h_window": len(b1),
               "bars_4h_prewarm": len(pre)}
        if len(b4) < int(cfg.box_bars_4h) + 8 or len(b1) < 200:
            row.update({"status": "数据不足", "reason":
                        f"4h={len(b4)}(<{cfg.box_bars_4h + 8}) 或 1h={len(b1)}(<200)"})
            rows.append(row)
            totals["数据不足"] += 1
            continue
        eng, attempts, last, blocked, formed, last_skip, gate_n = replay_symbol(
            sym, ctx, b4, b1, pre, win_start, cfg, upto_ms=upto)
        st = cm.engine_state(eng)
        dq = st["data_quality"]

        if blocked:
            gate_blocked_symbols += 1
            gate_blocked_total += len(blocked)

        row.update({
            "status": "已回放",
            "data_invalid": st["data_invalid"],
            "data_invalid_reason": dq.get("reason"),
            "atr_ready": (st["atr"] is not None and st["atr"] > 0),
            "atr": None if st["atr"] is None else round(st["atr"], 8),
            "atr_bars_seen": st["atr_bars_seen"],
            "box_gate_bars": st["box_gate_bars"],
            "gate_satisfied": st["box_gate_bars"] >= gate_n,
            "gate_blocked_n": len(blocked),
            "gate_blocked_last_utc": (blocked[-1]["utc"] if blocked else None),
            "state": st["state"],
            "stage_entered_utc": cm.utc(st["stage_entered_ms"]),
            "stage_elapsed_h": st["stage_elapsed_h"],
            "box_formed_utc": cm.utc(st["box_formed_ms"]),
            "box_width_atr": (None if st["box_width_atr"] is None
                              else round(st["box_width_atr"], 4)),
            "box_er": None if st["box_er"] is None else round(st["box_er"], 4),
            "n_box_formed": len(formed),
            "n_candidates": st["n_candidates"],
            "n_completed": st["n_completed"],
            "last_processed_1h_utc": cm.utc(st["processed_1h_max"]),
            "last_processed_4h_utc": cm.utc(st["processed_4h_max"]),
            "last_4h_attempt_utc": (last["utc"] if last else None),
            "last_4h_verdict": (last["verdict"] if last else None),
            "last_4h_actual": (str(last["actual"]) if last else None),
            "last_4h_threshold": (last["threshold"] if last else None),
            "last_warmup_skip_utc": cm.utc(last_skip),
            "attempts": dict(attempts),
        })
        # 淘汰原因：取最近 3 条终态迁移
        term = [t for t in cm.transitions_of(eng)
                if t["to_state"] in ("INVALIDATED", "EXPIRED", "VOLUME_REJECTED",
                                     "OI_REJECTED", "OI_UNAVAILABLE",
                                     "MISSED_WITHOUT_RETEST")]
        row["last_terminal_reason"] = (term[-1]["reason"] if term else None)
        row["last_terminal_utc"] = (cm.utc(term[-1]["trigger_bar_close_ms"])
                                   if term else None)
        rows.append(row)
        details[sym] = {
            "state": st,
            "attempts": dict(attempts),
            "gate_blocked": blocked[-20:],
            "gate_blocked_n": len(blocked),
            "box_formed_utc": [cm.utc(x) for x in formed],
            "transitions": cm.transitions_of(eng)[-40:],
            "last_4h_attempt": last,
        }
        for kk, vv in attempts.items():
            totals[kk] += vv
        if k % 100 == 0:
            print(f"  ... {k}/{len(syms)}")

    # ── 汇总 ────────────────────────────────────────────────────────────────
    replayed = [r for r in rows if r.get("status") == "已回放"]
    state_cnt = Counter(r["state"] for r in replayed)
    verdict_cnt = Counter(r["last_4h_verdict"] for r in replayed)
    summary = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "universe_declared": len(syms),
        "pool_meta": meta if isinstance(meta, dict) else str(meta),
        "window_hours": round(cm.warmup_ms() / cm.H1, 1),
        "window_start_utc": cm.utc(upto - cm.warmup_ms()),
        "n_replayed": len(replayed),
        "n_data_insufficient": totals.get("数据不足", 0),
        "n_data_invalid": sum(1 for r in replayed if r["data_invalid"]),
        "n_atr_ready": sum(1 for r in replayed if r["atr_ready"]),
        "n_gate_satisfied": sum(1 for r in replayed if r["gate_satisfied"]),
        "n_gate_blocked_symbols": gate_blocked_symbols,
        "n_gate_blocked_attempts": gate_blocked_total,
        "state_distribution": dict(state_cnt),
        "last_4h_verdict_distribution": dict(verdict_cnt),
        "box_attempt_totals": dict(totals),
        "n_symbols_with_box_formed": sum(1 for r in replayed if r["n_box_formed"] > 0),
        "n_symbols_with_candidate": sum(1 for r in replayed if r["n_candidates"] > 0),
        "total_boxes_formed": sum(r["n_box_formed"] for r in replayed),
        "total_candidates": sum(r["n_candidates"] for r in replayed),
        "note": ("attempts 统计的是「每个 4h 收盘是否本来可以建档」；"
                 "『条件全满足·被预热门槛拦掉』= 当前实现特有的一次额外拦截，"
                 "规范口径下这些根会正常建档。"),
    }
    cm.dump_csv(f"02_逐币归因{tag}.csv", rows)
    cm.dump(f"02_逐币归因{tag}.json", {"summary": summary, "rows": rows, "details": details})
    cm.dump(f"02_汇总{tag}.json", summary)

    print("\n=== 汇总 ===")
    for k, v in summary.items():
        if k not in ("pool_meta", "note"):
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

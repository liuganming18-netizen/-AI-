# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜03 漏信号专项：三种启动方式的状态与候选对照。

同一份行情、相同截止时刻 `upto`、同一套冻结参数，只改「引擎怎么启动」：

  S1 一次启动、不重启        bootstrap@upto          窗口 [upto-1160h, upto]
  S2 历史初始化后接新行情    bootstrap@upto-60h      窗口 [upto-1220h, upto-60h] + 推进 60h
  S3 中途重启后重建          bootstrap@upto-24h      窗口 [upto-1184h, upto-24h] + 推进 24h
  S4 两次重启                bootstrap@upto-60h → 推进到 upto-24h → 再 bootstrap@upto-24h → 推进

S1 vs S2 复现 FIX-03 记录的「窗口移动 60h ⇒ 24/526 状态差异」，S3 检验差异是否随移动量变小，
S4 检验「重启累积漂移」。逐币比较**候选身份**（candidate_id）与关键数值，不只比总数。

产出：
  out/03_三场景对照.json    场景汇总 + 逐币差异
  out/03_逐币三场景.csv     526 行 × 各场景终态
"""
from __future__ import annotations

import bisect
import importlib
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import unified_runtime as UR

H1, H4 = cm.H1, cm.H4


class FastWatcher:
    def __init__(self, b4, b1):
        self.b4, self.b1 = b4, b1
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


def run_session(sym, b4_all, b1_all, init_ms, end_ms, cfg, win_start_override=None):
    """一次「启动 + 推进」：bootstrap 到 init_ms，再增量推进到 end_ms。

    `win_start_override` 用于**严格同长度对照**：重启时通常窗口是
    `[重启时刻-1160h, 重启时刻]`，比「不重启」多读一段历史。强制指定窗口起点后，
    两者覆盖区间完全相同，差异就只能来自「bootstrap 时刻」本身（锚点效应）。
    """
    eng = UR.RealSignalEngine(symbol=sym, enable_oi_filter=False, config=cfg)
    win_start = (int(win_start_override) if win_start_override is not None
                 else int(init_ms) - cm.warmup_ms())
    eng.history_floor_ms = win_start
    pre = [b for b in b4_all
           if int(b["open_time"]) < win_start and int(b["close_time"]) <= int(init_ms)]
    win = [b for b in b4_all
           if int(b["open_time"]) >= win_start and int(b["close_time"]) <= int(init_ms)]
    # 窗口前预热（与 bootstrap 完全同口径）
    for b in pre:
        eng.atr_bars_seen += 1
        eng.last_atr = eng.atr.update(float(b["high"]), float(b["low"]), float(b["close"]))
    w = FastWatcher(b4_all, b1_all)
    for bar in win:
        eng.process_market(int(bar["close_time"]), w)
    # ── 增量推进（这是「接新行情」，不是重新 bootstrap）──
    if int(end_ms) > int(init_ms):
        nxt = [b for b in b4_all
               if int(init_ms) < int(b["close_time"]) <= int(end_ms)]
        for bar in nxt:
            eng.process_market(int(bar["close_time"]), w)
    # 补齐尾部 1h（与生产 step() 同口径：最后一根 4h 收盘之后的 1h 也要处理）
    eng.process_market(int(end_ms), w)
    return eng


def snap(eng):
    s = cm.engine_state(eng)
    return {
        "state": s["state"],
        "box_formed_ms": s["box_formed_ms"],
        "box_high": s["box_high"], "box_low": s["box_low"], "box_atr": s["box_atr"],
        "sweep_time_ms": s["sweep_time_ms"], "sweep_low": s["sweep_low"],
        "retest_time_ms": s["retest_time_ms"], "retest_low": s["retest_low"],
        "cooling_until_ms": s["cooling_until_ms"],
        "atr": s["atr"],
        "box_gate_bars": s["box_gate_bars"],
        "n_transitions": s["n_transitions"],
        "n_completed": s["n_completed"],
        "n_candidates": s["n_candidates"],
        "candidate_ids": [c.candidate_id for c in eng.sm.candidates],
        "completed_event_ids": [e.event_id for e in eng.sm.completed_events],
        "last_terminal_reason": ([t.reason for t in eng.sm.transitions
                                  if t.to_state in ("INVALIDATED", "EXPIRED",
                                                    "VOLUME_REJECTED", "OI_REJECTED",
                                                    "OI_UNAVAILABLE",
                                                    "MISSED_WITHOUT_RETEST")] or [None])[-1],
    }


def diff_key(a, b):
    """状态差异的判定键：阶段 + 箱体身份 + 箱体形成时刻。"""
    return (a["state"], a["box_formed_ms"], a["box_high"], a["box_low"])


def main() -> int:
    cfg = cm.cfg()
    upto = cm.last_closed_1h_close_ms()
    syms, _ = cm.universe()
    print(f"[cfg] upto={cm.utc(upto)} universe={len(syms)}")

    # 读 190 天，保证 S2/S3 的 180 天预热也够
    hist, _ = cm.load_hist(syms, upto, read_days=190)
    print("[hist] loaded")

    scen = {
        "S1_一次启动不重启": (upto, upto),
        "S2_初始化后接60h新行情": (upto - 60 * H1, upto),
        "S3_中途重启重建_24h": (upto - 24 * H1, upto),
    }
    res = {k: {} for k in scen}
    for k, (init_ms, end_ms) in scen.items():
        print(f"[run] {k}: init={cm.utc(init_ms)} end={cm.utc(end_ms)}")
        for i, sym in enumerate(syms, 1):
            b4 = hist[sym]["4h"]
            b1 = hist[sym]["1h"]
            eng = run_session(sym, b4, b1, init_ms, end_ms, cfg)
            res[k][sym] = snap(eng)
            if i % 150 == 0:
                print(f"    ... {i}/{len(syms)}")

    # S4：两次重启（60h 处 bootstrap → 推进到 24h 处 → 再 bootstrap → 推进到 upto）
    print("[run] S4_两次重启")
    res["S4_两次重启"] = {}
    t1, t2 = upto - 60 * H1, upto - 24 * H1
    for sym in syms:
        b4, b1 = hist[sym]["4h"], hist[sym]["1h"]
        e1 = run_session(sym, b4, b1, t1, t2, cfg)
        # 第二次重启：状态丢弃，按 t2 重新 bootstrap（真实重启就是这样）
        e2 = run_session(sym, b4, b1, t2, upto, cfg)
        res["S4_两次重启"][sym] = snap(e2)
        _ = e1  # 第一次会话的终态不参与对比（重启即丢弃），保留是为显式说明这一点

    # S5：严格同长度对照 —— 60h 处重建，但窗口起点强制 = upto-1160h
    # 这样覆盖区间与 S1 完全一致，差异只能来自「bootstrap 时刻」（锚点效应），
    # 而不是「重启让你多读了一段历史」。这是「修好了没有」的判据。
    print("[run] S5_同长度_60h处重建")
    res["S5_同长度_60h处重建"] = {}
    for sym in syms:
        b4, b1 = hist[sym]["4h"], hist[sym]["1h"]
        e = run_session(sym, b4, b1, t1, upto, cfg,
                        win_start_override=upto - cm.warmup_ms())
        res["S5_同长度_60h处重建"][sym] = snap(e)

    # ── 逐币差异 ────────────────────────────────────────────────────────────
    base = res["S1_一次启动不重启"]
    cmp_list = ["S2_初始化后接60h新行情", "S3_中途重启重建_24h", "S4_两次重启",
                "S5_同长度_60h处重建"]
    diffs = {k: [] for k in cmp_list}
    rows = []
    for sym in syms:
        row = {"symbol": sym}
        for k in ["S1_一次启动不重启"] + cmp_list:
            s = res[k][sym]
            row[f"{k}_state"] = s["state"]
            row[f"{k}_box_formed_utc"] = cm.utc(s["box_formed_ms"])
            row[f"{k}_box_gate_bars"] = s["box_gate_bars"]
            row[f"{k}_n_candidates"] = s["n_candidates"]
            row[f"{k}_n_transitions"] = s["n_transitions"]
        for k in cmp_list:
            a, b = base[sym], res[k][sym]
            same = (a["state"] == b["state"] and a["box_formed_ms"] == b["box_formed_ms"]
                    and a["box_high"] == b["box_high"] and a["box_low"] == b["box_low"])
            row[f"{k}_same"] = same
            if not same:
                diffs[k].append({
                    "symbol": sym,
                    "S1": {kk: a[kk] for kk in ("state", "box_formed_ms", "box_high",
                                                "box_low", "sweep_time_ms", "retest_time_ms",
                                                "n_transitions", "n_candidates",
                                                "candidate_ids", "last_terminal_reason")},
                    "other": {kk: b[kk] for kk in ("state", "box_formed_ms", "box_high",
                                                   "box_low", "sweep_time_ms", "retest_time_ms",
                                                   "n_transitions", "n_candidates",
                                                   "candidate_ids", "last_terminal_reason")},
                    "S1_utc": cm.utc(a["box_formed_ms"]),
                    "other_utc": cm.utc(b["box_formed_ms"]),
                })
        rows.append(row)

    summary = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "window_hours": round(cm.warmup_ms() / cm.H1, 1),
        "scenarios": {k: {"init_utc": cm.utc(v[0]) if isinstance(v, tuple) else None,
                          "end_utc": cm.utc(v[1]) if isinstance(v, tuple) else None}
                      for k, v in scen.items()},
        "S5_note": ("S5 = 60h 处重建，但窗口起点强制 = upto-1160h ⇒ 覆盖区间与 S1 完全相同；"
                    "S5≡S1 说明锚点效应已消除，剩下的 S2 差异只是「多读 60h 历史」的增量。"),
        "state_distribution": {k: dict(Counter(res[k][s]["state"] for s in syms))
                               for k in res},
        "total_candidates": {k: sum(res[k][s]["n_candidates"] for s in syms) for k in res},
        "total_boxes_formed": {
            k: sum(1 for s in syms if res[k][s]["box_formed_ms"] is not None) for k in res},
        "n_diff_vs_S1": {k: len(v) for k, v in diffs.items()},
        "diff_detail": diffs,
        "judgement_rule": (
            "状态差异只有在「改变了当前有效结构 / 候选时刻 / 后续触发」时才算漏信号；"
            "仅仅 box_gate_bars 计数不同不算。逐币逐字段列出，不按总数下结论。"),
    }
    cm.dump("03_三场景对照.json", {"summary": summary, "rows": rows,
                                  "snapshots": res})
    cm.dump_csv("03_逐币三场景.csv", rows)

    print("\n=== 汇总 ===")
    for k, v in summary.items():
        if k in ("diff_detail", "judgement_rule"):
            continue
        print(f"  {k}: {json_dumps(v)}")
    for k in cmp_list:
        print(f"\n--- S1 vs {k}: {len(diffs[k])} 个状态差异 ---")
        for d in diffs[k][:12]:
            print(f"  {d['symbol']:16s} S1={d['S1']['state']:8s}@{d['S1_utc']} "
                  f"| {k}={d['other']['state']:8s}@{d['other_utc']}")
    return 0


def json_dumps(o):
    import json
    return json.dumps(o, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜01 逐项查清剩余差异（最早分歧那根 4h）。

工单第 1 条：针对 ZERO-CAUSE-05 中 S2 的 6 个币、S3/S4 的 1 个币，以及 17/20 的候选差异，
记录**最早分歧 K 线、分歧前状态、触发条件及后续影响**。不能只用「读取历史不同」结束归因。

方法（两层定位，不混在一起）：

  ① **数值分歧**：两个会话在同一根 4h 收盘上，`last_atr` 是否已经不同（>1e-12）。
     这是机制层 —— 窗口起点移动 ⇒ ATR 递推起点移动 ⇒ ATR 值漂移。
  ② **决策分歧**：同一根 4h 收盘上，阶段 / 事件身份 / 箱体身份 / 冷却截止 / 迁移数 / 候选数
     是否已经不同。这是后果层 —— 真正让「当前有效状态」不一样的那一根。

对每一处决策分歧，输出：
  · 分歧前一根的状态（两边应相同，作为基线）
  · 分歧当根两边各自的 42 根箱体度量（H / L / ATR / 宽度 / ER）与闸门判定
  · 该根两边的阻塞原因（已有活跃事件 / 冷却 / 上下文不足 / 正常建档）
  · 后续影响（终态、箱体数、候选数、候选身份）

用法：python code/01_earliest_divergence.py [--upto <ms>] [--symbols A,B,C]
产出：out/01_最早分歧.json、out/01_最早分歧.csv
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

H1 = cm.H1

#: ZERO-CAUSE-05 记录的分歧币（S2 的 6 个 + S3/S4 的 1 个）
S2_SYMS = ["BATUSDT", "BTWUSDT", "CTRUSDT", "EDUUSDT", "GRVTUSDT", "TAKEUSDT"]
S3_SYMS = ["CTRUSDT"]

DECISION_FIELDS = ("state", "event_id", "box_formed_ms", "cooling_until_ms",
                   "n_transitions", "n_candidates", "last_skip_ms")


def block_reason(fp, close_ms, has_prev_bar):
    """按优先级给这根 4h 的「为什么没建档」一个可读原因。"""
    if fp["state"] != "IDLE":
        return f"已有活跃事件({fp['state']})"
    cu = fp["cooling_until_ms"]
    if cu and int(close_ms) < int(cu):
        return f"冷却中(至 {cm.utc(cu)})"
    if not has_prev_bar:
        return "上下文不足(<42根)"
    if fp["last_skip_ms"] == close_ms:
        return "被预热门槛拦掉"
    return "无箱体(条件未满足)"


def metrics_at(b4_all, close_ms, atr, n):
    """该根 4h 的 42 根箱体度量（用各自引擎的 ATR）。"""
    idx = None
    for i, b in enumerate(b4_all):
        if int(b["close_time"]) == int(close_ms):
            idx = i
            break
    if idx is None or idx + 1 < n:
        return {"error": "定位不到该根或上下文不足"}
    seg = b4_all[idx + 1 - n: idx + 1]
    m = cm.box_metrics(seg, atr)
    m["width_ok"] = bool(m["width_atr"] is not None and 2.0 <= m["width_atr"] <= 8.0)
    m["er_ok"] = bool(m["er"] is not None and m["er"] <= 0.30)
    m["seg_first_utc"] = cm.utc(int(seg[0]["close_time"]))
    m["seg_last_utc"] = cm.utc(int(seg[-1]["close_time"]))
    return m


def compare(trace_a, trace_b, b4_all, n, label_a, label_b):
    """对比两条轨迹，返回数值分歧与决策分歧的最早位置。"""
    keys_a = {k for k in trace_a if isinstance(k, int)}
    keys_b = {k for k in trace_b if isinstance(k, int)}
    common = sorted(keys_a & keys_b)
    num_first, dec_first, dec_all = None, None, []
    prev_key = None
    for k in common:
        fa, fb = trace_a[k], trace_b[k]
        # ① 数值层
        if num_first is None:
            da = abs(float(fa["atr"] or 0) - float(fb["atr"] or 0))
            if da > 1e-12:
                num_first = {"close_ms": k, "close_utc": cm.utc(k),
                             "atr_a": fa["atr"], "atr_b": fb["atr"],
                             "abs_diff": da,
                             "rel_diff_pct": (da / float(fa["atr"]) * 100
                                              if fa["atr"] else None)}
        # ② 决策层
        diff_fields = [f for f in DECISION_FIELDS if fa.get(f) != fb.get(f)]
        if diff_fields:
            rec = {
                "close_ms": k, "close_utc": cm.utc(k),
                "diff_fields": diff_fields,
                "before": ({"close_utc": cm.utc(prev_key),
                            "a": {f: trace_a[prev_key][f] for f in DECISION_FIELDS},
                            "b": {f: trace_b[prev_key][f] for f in DECISION_FIELDS}}
                           if prev_key is not None else None),
                "a": {f: fa[f] for f in DECISION_FIELDS},
                "b": {f: fb[f] for f in DECISION_FIELDS},
                "a_block": block_reason(fa, k, prev_key is not None),
                "b_block": block_reason(fb, k, prev_key is not None),
                "a_metrics": metrics_at(b4_all, k, fa["atr"], n),
                "b_metrics": metrics_at(b4_all, k, fb["atr"], n),
            }
            dec_all.append(rec)
            if dec_first is None:
                dec_first = rec
        prev_key = k
    return {
        "n_common_bars": len(common),
        "common_from_utc": cm.utc(common[0]) if common else None,
        "common_to_utc": cm.utc(common[-1]) if common else None,
        "first_numeric_divergence": num_first,
        "first_decision_divergence": dec_first,
        "n_decision_divergence_bars": len(dec_all),
        "decision_divergences": dec_all[:12],
        "labels": [label_a, label_b],
    }


def main() -> int:
    cfg = cm.cfg()
    n = int(cfg.box_bars_4h)
    argv = sys.argv[1:]
    upto = None
    syms_arg = None
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto = int(argv[i + 1])
        if a == "--symbols" and i + 1 < len(argv):
            syms_arg = argv[i + 1].split(",")
    if upto is None:
        upto = cm.last_closed_1h_close_ms()
    syms = syms_arg or sorted(set(S2_SYMS) | set(S3_SYMS))
    print(f"[cfg] upto={cm.utc(upto)} symbols={len(syms)}")

    hist, _ = cm.load_hist(syms, upto, read_days=190)

    out, rows = {}, []
    for sym in syms:
        b4, b1 = hist[sym]["4h"], hist[sym]["1h"]
        # S1：截止时刻全新启动（窗口 [upto-1160h, upto]）
        t1 = {}
        e1 = cm.run_session(sym, b4, b1, upto, upto, cfg, trace=t1)
        # S2：早 60h 启动（窗口 [upto-1220h, upto-60h]）再推进
        t2 = {}
        e2 = cm.run_session(sym, b4, b1, upto - 60 * H1, upto, cfg, trace=t2)
        # S3：早 24h 启动再推进
        t3 = {}
        e3 = cm.run_session(sym, b4, b1, upto - 24 * H1, upto, cfg, trace=t3)

        s1, s2, s3 = cm.snapshot(e1), cm.snapshot(e2), cm.snapshot(e3)
        rec = {
            "symbol": sym,
            "s1_vs_s2": compare(t1, t2, b4, n, "S1_全新启动@upto", "S2_早60h启动+推进"),
            "s1_vs_s3": compare(t1, t3, b4, n, "S1_全新启动@upto", "S3_早24h启动+推进"),
            "final": {"S1": s1, "S2": s2, "S3": s3},
            "final_same": {
                "S1_vs_S2": cm.diff_key(s1) == cm.diff_key(s2),
                "S1_vs_S3": cm.diff_key(s1) == cm.diff_key(s3),
            },
        }
        out[sym] = rec
        for tag, cmp_ in (("S2", rec["s1_vs_s2"]), ("S3", rec["s1_vs_s3"])):
            d = cmp_["first_decision_divergence"] or {}
            nd = cmp_["first_numeric_divergence"] or {}
            rows.append({
                "symbol": sym, "compare": tag,
                "final_same": rec["final_same"][f"S1_vs_{tag}"],
                "common_bars": cmp_["n_common_bars"],
                "first_numeric_utc": nd.get("close_utc"),
                "numeric_rel_pct": (round(nd["rel_diff_pct"], 8)
                                    if nd.get("rel_diff_pct") is not None else None),
                "first_decision_utc": d.get("close_utc"),
                "decision_fields": ",".join(d.get("diff_fields") or []),
                "state_before_a": (d.get("before") or {}).get("a", {}).get("state"),
                "state_a": (d.get("a") or {}).get("state"),
                "state_b": (d.get("b") or {}).get("state"),
                "a_block": d.get("a_block"),
                "b_block": d.get("b_block"),
                "a_width": (d.get("a_metrics") or {}).get("width_atr"),
                "b_width": (d.get("b_metrics") or {}).get("width_atr"),
                "a_er": (d.get("a_metrics") or {}).get("er"),
                "b_er": (d.get("b_metrics") or {}).get("er"),
                "n_decision_bars": cmp_["n_decision_divergence_bars"],
            })
        print(f"  {sym:14s} S2同={rec['final_same']['S1_vs_S2']} "
              f"最早决策分歧={((rec['s1_vs_s2']['first_decision_divergence'] or {}).get('close_utc'))} "
              f"| S3同={rec['final_same']['S1_vs_S3']} "
              f"最早决策分歧={((rec['s1_vs_s3']['first_decision_divergence'] or {}).get('close_utc'))}")

    # ── 候选差异：17 vs 20 到底是哪 3 个、出在哪 ────────────────────────────
    cand = {}
    for sym in syms:
        f = out[sym]["final"]
        cand[sym] = {"S1": f["S1"]["candidate_ids"], "S2": f["S2"]["candidate_ids"],
                     "S3": f["S3"]["candidate_ids"]}
    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "window_hours": round(cm.warmup_ms() / H1, 1),
        "symbols": syms,
        "method": ("① 数值分歧 = 同一根 4h 收盘 last_atr 差 >1e-12（机制层）；"
                   "② 决策分歧 = 同一根 4h 收盘 阶段/事件/箱体/冷却/迁移数/候选数 不同（后果层）。"
                   "两层分开定位，不把 ATR 漂移直接当成「状态不一样」。"),
        "per_symbol": out,
        "candidates": cand,
    }
    cm.dump("01_最早分歧.json", res)
    cm.dump_csv("01_最早分歧.csv", rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())

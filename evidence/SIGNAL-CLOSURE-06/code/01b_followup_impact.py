# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜01b 分歧的「后续影响」逐币归纳（只读 01 产物，可机械复算）。

工单第 1 条要求记录「最早分歧 K 线、分歧前状态、触发条件及**后续影响**」。
`01_earliest_divergence.py` 已给出前三项；本件只补第四项，不重跑任何引擎：

  ① 该分歧是否**传播到终态**（阶段 / 箱体身份 / 事件身份）；
  ② 该分歧是否**传播到候选层**（候选 id 集合是否不同）—— 这是「漏信号」的直接判据；
  ③ 传播的量级（迁移数差、完成事件数差），区分「只是中间过程不同」与「结论不同」。

纪律：候选 id 集合相同就写「不影响信号」，不拿迁移数差异冒充信号差异。

用法：python code/01b_followup_impact.py
产出：out/01_后续影响.csv、out/01_后续影响.json
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

#: 终态里真正决定「有没有信号」的字段
SIGNAL_KEYS = ("state", "box_formed_ms", "sweep_time_ms", "retest_time_ms")
STATE_KEYS = SIGNAL_KEYS + ("event_id", "cooling_until_ms", "box_high", "box_low")


def one(sym: str, base: dict, tag: str) -> dict:
    """归纳一个 (币, 对照) 的后续影响。"""
    cmp_ = base["s1_vs_s2" if tag == "S2" else "s1_vs_s3"]
    fd = cmp_["first_decision_divergence"]
    f = base["final"]
    s1, sb = f["S1"], f[tag]

    c1 = set(s1.get("candidate_ids") or [])
    cb = set(sb.get("candidate_ids") or [])
    only_s1 = sorted(c1 - cb)
    only_b = sorted(cb - c1)

    # 终态字段级差异（排除累计量）
    final_diff = [k for k in STATE_KEYS if s1.get(k) != sb.get(k)]

    if not fd:
        verdict = "无决策分歧"
    elif only_s1 or only_b:
        verdict = "传播到候选层（候选集合不同）"
    elif final_diff:
        verdict = "传播到终态但未传播到候选"
    else:
        verdict = "仅中间过程不同，终态与候选均相同"

    return {
        "symbol": sym, "compare": tag,
        # ── 最早分歧 ────────────────────────────────────────────────
        "first_decision_utc": fd.get("close_utc") if fd else None,
        "divergence_fields": ",".join(fd.get("diff_fields") or []) if fd else "",
        "n_divergence_bars": cmp_["n_decision_divergence_bars"],
        "numeric_divergence_utc": (cmp_["first_numeric_divergence"] or {}).get("close_utc"),
        # ── 分歧前状态（基线）──────────────────────────────────────
        "state_before_a": ((fd or {}).get("before") or {}).get("a", {}).get("state"),
        "state_before_b": ((fd or {}).get("before") or {}).get("b", {}).get("state"),
        # ── 分歧当根：两边状态与阻塞原因 ───────────────────────────
        "state_a": (fd or {}).get("a", {}).get("state"),
        "state_b": (fd or {}).get("b", {}).get("state"),
        "event_a": (fd or {}).get("a", {}).get("event_id"),
        "event_b": (fd or {}).get("b", {}).get("event_id"),
        "block_a": (fd or {}).get("a_block"),
        "block_b": (fd or {}).get("b_block"),
        # ── 触发条件（当根 42 根箱体度量 + 两道闸门）───────────────
        "a_width_atr": ((fd or {}).get("a_metrics") or {}).get("width_atr"),
        "b_width_atr": ((fd or {}).get("b_metrics") or {}).get("width_atr"),
        "a_er": ((fd or {}).get("a_metrics") or {}).get("er"),
        "b_er": ((fd or {}).get("b_metrics") or {}).get("er"),
        "a_width_ok": ((fd or {}).get("a_metrics") or {}).get("width_ok"),
        "b_width_ok": ((fd or {}).get("b_metrics") or {}).get("width_ok"),
        "a_er_ok": ((fd or {}).get("a_metrics") or {}).get("er_ok"),
        "b_er_ok": ((fd or {}).get("b_metrics") or {}).get("er_ok"),
        # ── 后续影响：终态 ─────────────────────────────────────────
        "final_state_s1": s1.get("state"), "final_state_b": sb.get("state"),
        "final_diff_fields": ",".join(final_diff),
        "n_transitions_s1": s1.get("n_transitions"),
        "n_transitions_b": sb.get("n_transitions"),
        "n_transitions_delta": (sb.get("n_transitions") or 0) - (s1.get("n_transitions") or 0),
        "n_completed_s1": s1.get("n_completed"), "n_completed_b": sb.get("n_completed"),
        # ── 后续影响：候选层（漏信号的直接判据）────────────────────
        "n_candidates_s1": s1.get("n_candidates"), "n_candidates_b": sb.get("n_candidates"),
        "only_in_s1": ",".join(only_s1), "only_in_b": ",".join(only_b),
        "candidate_sets_equal": c1 == cb,
        "verdict": verdict,
    }


def main() -> int:
    src = cm.OUT / "01_最早分歧.json"
    if not src.exists():
        print(f"[err] 缺 {src}，先跑 01_earliest_divergence.py")
        return 2
    import json
    d = json.loads(src.read_text(encoding="utf-8"))

    rows = []
    for sym, base in d["per_symbol"].items():
        for tag in ("S2", "S3"):
            rows.append(one(sym, base, tag))

    n_signal = sum(1 for r in rows if not r["candidate_sets_equal"])
    n_terminal = sum(1 for r in rows
                     if r["candidate_sets_equal"] and r["final_diff_fields"])
    n_mid = sum(1 for r in rows
                if r["candidate_sets_equal"] and not r["final_diff_fields"])

    print(f"[src] {src.name}  upto={d['upto_utc']}  币 {len(d['per_symbol'])}  对照 {len(rows)} 条")
    print(f"[归纳] 传播到候选层 {n_signal} 条 / 传播到终态 {n_terminal} 条 / "
          f"仅中间过程不同 {n_mid} 条")
    for r in rows:
        print(f"  {r['symbol']:12s} {r['compare']}  分歧@{r['first_decision_utc']} "
              f"[{r['divergence_fields']}]  "
              f"终态 {r['final_state_s1']}→{r['final_state_b']}  "
              f"候选 {r['n_candidates_s1']}→{r['n_candidates_b']}  {r['verdict']}")

    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "source": str(src.relative_to(cm.ROOT)) if hasattr(cm, "ROOT") else src.name,
        "upto_utc": d["upto_utc"],
        "n_symbols": len(d["per_symbol"]),
        "n_comparisons": len(rows),
        "n_propagated_to_candidates": n_signal,
        "n_propagated_to_terminal_only": n_terminal,
        "n_transient_only": n_mid,
        "judgement": ("候选 id 集合相同 ⇒ 该分歧不改变「有没有信号」，只是中间过程不同；"
                      "候选集合不同 ⇒ 才是真的漏信号。迁移数/完成事件数差异单独列出，"
                      "不用它冒充信号差异。"),
        "rows": rows,
    }
    cm.dump("01_后续影响.json", res)
    cm.dump_csv("01_后续影响.csv", rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())

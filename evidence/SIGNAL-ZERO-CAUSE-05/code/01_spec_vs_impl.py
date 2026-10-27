# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜01 规范原文 ↔ 实现行为 对照（机械提取，不手抄）。

回答工单第 1 条：
  · ATR 已用窗口前历史预热后，为什么建档仍要求从回放窗口起点重新计满 200 根 4h？
  · 原规范「ATR 至少预热 200 根」是否明确要求上述第二次等待？
  · 规范原文 / 代码位置 / 行为证据三者对应。

做法：把规范 md、实时实现 py、回测实现 py 的**相关行原文连同行号**一起抓出来，
再按冻结参数算「窗口内 200 根」把多少有效事件窗口吃掉了。不做任何人工改写。
产出：out/01_规范与实现对照.json
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

SPEC = cm.BASE / "evidence" / "WX_EVENT_TREND_V0_1_策略与回测冻结规则.md"
LIVE = cm.BACKEND / "unified_runtime.py"
BT = cm.BASE / "策略" / "WyckoffMaster" / "event_trend_v0_1" / "runner.py"


def lines_of(p: Path):
    return p.read_text(encoding="utf-8").split("\n")


def pick(lines, start, end):
    """取 1-based 闭区间行原文。"""
    return [{"line": i, "text": lines[i - 1].rstrip()} for i in range(start, end + 1)
            if 0 < i <= len(lines)]


def find_lines(lines, pat, limit=12):
    rx = re.compile(pat)
    return [{"line": i, "text": l.rstrip()} for i, l in enumerate(lines, 1)
            if rx.search(l)][:limit]


def main() -> int:
    c = cm.cfg()
    sl, ll, bl = lines_of(SPEC), lines_of(LIVE), lines_of(BT)

    # ── 1. 规范原文（三条相关条款，原文照录）────────────────────────────────
    spec_items = {
        "ATR 预热条款": pick(sl, cm.SPEC_RULE_LINE, cm.SPEC_RULE_LINE),
        "建档条款": pick(sl, cm.SPEC_BOX_LINE, cm.SPEC_BOX_LINE),
        "分段回测预热条款": pick(sl, cm.SPEC_SPLIT_LINE, cm.SPEC_SPLIT_LINE),
    }

    # ── 2. 实时实现：建档闸门的三个关键行 ────────────────────────────────────
    live_gate = find_lines(ll, r"box_gate_bars")
    live_atr = find_lines(ll, r"atr_bars_seen")
    live_call = find_lines(ll, r"check_and_form_box|sm\.check_and_form_box")

    # ── 3. 回测实现：有没有同样的闸门 ────────────────────────────────────────
    bt_gate = find_lines(bl, r"box_gate_bars|atr_warmup_bars")
    bt_warmup = find_lines(bl, r"warmup_start_idx|idx_start_4h")
    bt_call = find_lines(bl, r"check_and_form_box|len\(sub_4h\)")

    # ── 4. 量化：窗口内 200 根把有效事件窗口吃掉多少 ──────────────────────────
    win_h = cm.warmup_ms() / cm.H1
    bars_4h_in_win = int(win_h // 4)                       # 289
    gate_bars = int(c.atr_warmup_bars)                     # 200
    need_ctx = int(c.box_bars_4h)                          # 42
    # 当前实现：建档从「窗口第 200 根」开始（且需 42 根上下文，前 42 根本来就不能建档）
    effective_live_bars = bars_4h_in_win - max(gate_bars, need_ctx)
    # 规范口径：ATR 已由窗口外预热满足 ⇒ 只要 42 根上下文即可建档
    effective_spec_bars = bars_4h_in_win - need_ctx
    # 事件链最长耗时：箱体 42 根(168h) 内必须扫低 168h + 回踩 72h + 突破 168h
    chain_max_h = (c.box_bars_4h * 4) + c.sweep_max_hours + c.retest_max_hours \
        + c.breakout_max_hours_after_retest

    quant = {
        "replay_window_hours": round(win_h, 1),
        "bars_4h_in_window": bars_4h_in_win,
        "gate_bars_window_inner": gate_bars,
        "box_context_bars": need_ctx,
        "current_impl_effective_event_bars": effective_live_bars,
        "current_impl_effective_event_hours": round(effective_live_bars * 4, 1),
        "current_impl_effective_event_days": round(effective_live_bars * 4 / 24, 2),
        "spec_impl_effective_event_bars": effective_spec_bars,
        "spec_impl_effective_event_hours": round(effective_spec_bars * 4, 1),
        "spec_impl_effective_event_days": round(effective_spec_bars * 4 / 24, 2),
        "lost_event_bars": effective_spec_bars - effective_live_bars,
        "lost_event_days": round((effective_spec_bars - effective_live_bars) * 4 / 24, 2),
        "worst_case_event_chain_hours": chain_max_h,
        "worst_case_event_chain_days": round(chain_max_h / 24, 2),
        "current_impl_can_fit_worst_case_chain": (effective_live_bars * 4) >= chain_max_h,
        "spec_impl_can_fit_worst_case_chain": (effective_spec_bars * 4) >= chain_max_h,
    }

    # ── 5. 判定（三条对照，逐条给证据行号）──────────────────────────────────
    verdict = [
        {
            "id": "V1",
            "claim": "规范第 17 行只约束 ATR 预热 200 根，未要求「建档前窗口内再累积 200 根」",
            "evidence": [f"规范第 {cm.SPEC_RULE_LINE} 行原文（见 spec_items.ATR 预热条款）",
                         f"规范第 {cm.SPEC_BOX_LINE} 行建档条件原文（见 spec_items.建档条款）"],
            "holds": True,
        },
        {
            "id": "V2",
            "claim": "实时实现把 atr_warmup_bars 复用成「建档闸门」，且计数锚在回放窗口起点",
            "evidence": [f"unified_runtime.py:{r['line']}" for r in live_gate],
            "holds": bool(live_gate),
        },
        {
            "id": "V3",
            "claim": "回测实现没有任何「窗口内满 200 根才建档」的闸门：每个 4h 收盘只要够 42 根就建档",
            "evidence": [f"runner.py:{r['line']}" for r in bt_call + bt_warmup],
            "holds": (not bt_gate) and bool(bt_call),
        },
    ]

    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "identity": {
            "spec_md": str(SPEC.relative_to(cm.BASE)),
            "spec_sha256": cm.sha256_file(SPEC),
            "spec_bytes": SPEC.stat().st_size,
            "live_py": str(LIVE.relative_to(cm.BASE)),
            "live_sha256": cm.sha256_file(LIVE),
            "bt_py": str(BT.relative_to(cm.BASE)),
            "bt_sha256": cm.sha256_file(BT),
        },
        "frozen_params_in_effect": {
            "atr_warmup_bars": c.atr_warmup_bars,
            "box_bars_4h": c.box_bars_4h,
            "breakout_max_total_hours": c.breakout_max_total_hours,
            "cooling_hours": c.cooling_hours,
            "sweep_max_hours": c.sweep_max_hours,
            "retest_max_hours": c.retest_max_hours,
            "breakout_max_hours_after_retest": c.breakout_max_hours_after_retest,
        },
        "spec_items": spec_items,
        "live_impl": {"gate_lines": live_gate, "atr_lines": live_atr,
                      "box_call_lines": live_call},
        "bt_impl": {"gate_lines": bt_gate, "warmup_lines": bt_warmup,
                    "box_call_lines": bt_call},
        "quantification": quant,
        "verdict": verdict,
    }
    cm.dump("01_规范与实现对照.json", out)

    print("\n=== 规范原文 ===")
    for k, v in spec_items.items():
        for r in v:
            print(f"  [{k}] L{r['line']}: {r['text'][:150]}")
    print("\n=== 实时实现（建档闸门）===")
    for r in live_gate:
        print(f"  L{r['line']}: {r['text'].strip()[:150]}")
    print("\n=== 回测实现 ===")
    print(f"  runner.py 里 box_gate_bars/atr_warmup_bars 命中：{len(bt_gate)} 行")
    for r in bt_warmup + bt_call:
        print(f"  L{r['line']}: {r['text'].strip()[:150]}")
    print("\n=== 量化 ===")
    for k, v in quant.items():
        print(f"  {k}: {v}")
    print("\n=== 判定 ===")
    for v in verdict:
        print(f"  {v['id']} holds={v['holds']} :: {v['claim']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

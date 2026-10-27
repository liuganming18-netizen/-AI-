# -*- coding: utf-8 -*-
"""全池形态漏斗诊断：逐步统计「建档 / 扫低 / 回踩 / 突破 / 失效 / 超时」各多少。

为什么要它：运行时报「候选 0 个」有两种可能——
  ① 引擎坏了；② 策略筛子太窄，当前确实没满足条件的标的。
只看「0」分不清，必须把状态机自己的 `transitions`（每次迁移都带 reason）
按 `to_state` 聚合，才能定位卡在哪一步、以什么理由被淘汰。

只读：不写库、不落信号、不改任何状态文件。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault(
    "MARKET_DB", str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"))

import unified_runtime as U  # noqa: E402

OUT = ROOT / "evidence" / "SYSTEM-RUNTIME-01-FIX" / "funnel_pool.json"


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    syms = U._default_universe()
    if limit:
        syms = syms[:limit]
    eng = U.MultiSymbolSignalEngine(syms)
    # 保留历史候选（bootstrap 默认丢弃，这里要统计，故手动回放）
    ready = 0
    to_state = Counter()
    reasons = Counter()
    sweep_overshoot = []
    box_widths = []
    for i, sym in enumerate(syms, 1):
        b4 = eng.watcher.get_closed_bars(sym, "4h", 0, 2 ** 62)
        b1 = eng.watcher.get_closed_bars(sym, "1h", 0, 2 ** 62)
        if len(b4) < eng.config.box_bars_4h + 8 or len(b1) < 200:
            continue
        e = U.RealSignalEngine(symbol=sym)
        w = U._MemWatcher(b4, b1)
        for bar in b4:
            e.process_market(int(bar["close_time"]), w)
        ready += 1
        for t in e.sm.transitions:
            to_state[t.to_state] += 1
            if t.to_state in ("INVALIDATED", "EXPIRED"):
                reasons[(t.to_state, (t.reason or "")[:28])] += 1
            if t.to_state == "BOX":
                m = t.metrics_snapshot or {}
                box_widths.append(m.get("width_atr"))
        if i % 100 == 0:
            print(f"[{i}/{len(syms)}] ready={ready} 迁移={sum(to_state.values())}", flush=True)

    # 逐标的最终状态
    final = Counter()
    for e in eng.engines.values() if False else []:
        pass
    rep = {
        "universe": len(syms), "ready": ready,
        "transitions_by_state": dict(to_state),
        "terminate_reasons": {f"{k[0]} | {k[1]}": v for k, v in reasons.most_common(20)},
        "box_width_atr": {
            "n": len(box_widths),
            "min": min(box_widths) if box_widths else None,
            "median": sorted(box_widths)[len(box_widths) // 2] if box_widths else None,
            "max": max(box_widths) if box_widths else None,
        },
    }
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

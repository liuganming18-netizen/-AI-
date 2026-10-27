# -*- coding: utf-8 -*-
"""回踩事件的最终去向诊断：22 次回踩为什么一次突破都没有。

口径：按 `event_id` 把状态机的迁移分组，找出每个到达 RETEST 的事件，
再统计它之后**是否出现突破首次穿越**、若出现又死在哪个闸门（量能 / OI）、
若没出现，则记录「回踩后最高收盘离突破线还差几个 ATR」。

只读，不写库。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault(
    "MARKET_DB", str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"))

import unified_runtime as U  # noqa: E402

OUT = ROOT / "evidence" / "SYSTEM-RUNTIME-01-FIX" / "retest_fate.json"


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    syms = U._default_universe()[:limit]
    eng = U.MultiSymbolSignalEngine(syms)

    fate = Counter()
    gaps = []            # 回踩后最高收盘距突破线（以 A 为单位，负=没到）
    vol_ratios = []      # 若发生过穿越，量能倍数
    oi_statuses = Counter()
    per_event = []

    for sym in syms:
        b4 = eng.watcher.get_closed_bars(sym, "4h", 0, 2 ** 62)
        b1 = eng.watcher.get_closed_bars(sym, "1h", 0, 2 ** 62)
        if len(b4) < eng.config.box_bars_4h + 8 or len(b1) < 200:
            continue
        e = U.RealSignalEngine(symbol=sym)
        w = U._MemWatcher(b4, b1)
        for bar in b4:
            e.process_market(int(bar["close_time"]), w)

        by_ev = defaultdict(list)
        for t in e.sm.transitions:
            by_ev[t.event_id].append(t)
        for ev_id, trs in by_ev.items():
            states = [t.to_state for t in trs]
            if "RETEST" not in states:
                continue
            idx = states.index("RETEST")
            after = states[idx + 1:]
            terminal = [s for s in after
                        if s in ("BREAKOUT", "VOLUME_REJECTED", "OI_UNAVAILABLE",
                                 "OI_REJECTED", "EXPIRED", "INVALIDATED",
                                 "MISSED_WITHOUT_RETEST")]
            key = terminal[0] if terminal else "STILL_RETEST(未终结)"
            fate[key] += 1
            # 量能与 OI 明细
            for t in trs[idx + 1:]:
                if t.to_state == "VOLUME_REJECTED":
                    m = t.metrics_snapshot or {}
                    if m.get("ratio") is not None:
                        vol_ratios.append(round(float(m["ratio"]), 2))
                if t.to_state in ("OI_UNAVAILABLE", "OI_REJECTED"):
                    m = t.metrics_snapshot or {}
                    oi_statuses[str(m.get("oi_status"))] += 1
            # 回踩后最高收盘距突破线
            retest_tr = trs[idx]
            m = retest_tr.metrics_snapshot or {}
            if after:
                per_event.append({"symbol": sym, "event": ev_id,
                                  "after": after[:4], "retest": m})

    rep = {
        "symbols": len(syms),
        "retest_events": sum(fate.values()),
        "fate": dict(fate),
        "volume_reject_ratios": sorted(vol_ratios)[:20],
        "oi_status_on_reject": dict(oi_statuses),
        "samples": per_event[:10],
    }
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())

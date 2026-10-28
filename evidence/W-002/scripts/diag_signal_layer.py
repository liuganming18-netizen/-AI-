#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[W-002 信号层] 诊断：真实行情下 RETEST→CANDIDATE 为什么走不通。

只读真实行情库；观察库写到临时目录，绝不碰生产研究库。

用法：
    /usr/bin/python3 evidence/W-002/scripts/diag_signal_layer.py HYPEUSDT
    /usr/bin/python3 evidence/W-002/scripts/diag_signal_layer.py HYPEUSDT --probe
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
for _p in (str(BACKEND), str(PROJECT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import accum_early_entry as A          # noqa: E402

CONFIG = PROJECT / "evidence" / "ACCUM-EARLY-06-W2" / "01" / "config" / "fixture_rules_06w2.json"
H = 3_600_000
DAY = 86_400_000


def ms(s: str) -> int:
    """UTC 字符串 → 毫秒（必须用 timegm，mktime 会按本地时区偏移）。"""
    import calendar
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def iso(v) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(v) / 1000)) if v else "—"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    probe = "--probe" in sys.argv
    symbols = args or ["HYPEUSDT"]
    start = ms("2026-06-27T08:00:00Z")
    end = ms("2026-10-26T09:00:00Z")

    cfg = A.load_config(str(CONFIG))
    print(f"配置 ready={cfg['ready']} open_allowed={cfg['open_allowed']} kind={cfg['kind']}")
    print(f"窗口 {iso(start)} → {iso(end)}  ({(end - start) / DAY:.1f} 天)")

    tmp = Path(tempfile.mkdtemp(prefix="w002_sig_"))
    try:
        store = A.open_store(str(tmp / "observe.sqlite3"))
        feed = A.DataFeed()
        traces = {}
        if probe:
            # 给 4b/4c 的 RETEST 判定加探针，记录每根 bar 的条件真值
            orig_hold = A.SchemeObserver._hold_actions
            orig_pull = A.SchemeObserver._pullback_actions

            def hold_probe(self, bar, atr):
                acts = orig_hold(self, bar, atr)
                traces.setdefault((self.symbol, self.scheme), []).append(
                    {"t": iso(bar["close_time"]), "st": "RETEST/4b",
                     "cond": self._last_conditions, "acts": len(acts)})
                return acts

            def pull_probe(self, bars_1h, i, atr, bar):
                acts = orig_pull(self, bars_1h, i, atr, bar)
                traces.setdefault((self.symbol, self.scheme), []).append(
                    {"t": iso(bar["close_time"]), "st": "RETEST/4c",
                     "cond": self._last_conditions, "acts": len(acts)})
                return acts

            A.SchemeObserver._hold_actions = hold_probe
            A.SchemeObserver._pullback_actions = pull_probe

        for sym in symbols:
            t0 = time.time()
            r = A.scan_symbol(sym, cfg, feed, store, start_ms=start, end_ms=end,
                              max_boxes=40, warmup_days=60, open_candidates=False)
            print(f"\n=== {sym}  耗时 {time.time() - t0:.1f}s")
            print(f"4h={r['bars']['4h']} 1h={r['bars']['1h']} "
                  f"箱体成立={r['boxes_formed']} 箱体使用={r['boxes_used']} "
                  f"动作={r['actions']} 候选={len(r['candidates'])}")
            print("事件轨迹：")
            for e in r["events"]:
                print(f"  {e.get('scheme')} {e.get('state'):<12} {e.get('at_ms') and iso(e['at_ms'])}"
                      f"  {str(e.get('reason'))[:70]}")
            for c in r["candidates"]:
                print(f"  [候选] {c['scheme']} {iso(c['confirm_close_ms'])} "
                      f"status={c['status']} {c['status_reason'][:60]}")
            if probe:
                for (sym2, sch), rows in traces.items():
                    if sym2 != sym:
                        continue
                    print(f"  --- {sch} RETEST 逐根（{len(rows)} 根）---")
                    for row in rows[:40]:
                        cs = [(x.get("field"), x.get("value"), x.get("status"))
                              for x in (row.get("cond") or [])]
                        print(f"    {row['t']} {row['st']} acts={row['acts']} {cs}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

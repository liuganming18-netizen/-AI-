# -*- coding: utf-8 -*-
"""SIGNAL-OBSERVE-FIX-12｜定点取证：观察事件**退出原因**。

`62_replay_lifecycle.py` 里 `0GUSDT` 在回放中途离开观察集合，但读 `sm.active_event`
得到 `None` —— 说明事件结束时引擎把 `active_event` 置空，终止态不在那里。
这不等于「退出没有原因」，只是读错了位置。

本探针只做一件事：把同一个窗口、同一个标的再跑一遍，在**它离开观察集合的那一步**
同时读三处：`transition_counts`（状态迁移累计）、`funnel()["since_start"]`（终止态计数）、
以及 `history` 最后一条迁移 —— 用它们确定退出到底属于哪一类。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "框架" / "backend"
OUT = Path(__file__).resolve().parents[1] / "out"
sys.path.insert(0, str(BACKEND))

H1 = 3600 * 1000
DAYS = 5
SYM = "0GUSDT"

TMP = Path(tempfile.mkdtemp(prefix="observe_exit_"))
(TMP / "locks").mkdir(parents=True, exist_ok=True)
(TMP / "paper").mkdir(parents=True, exist_ok=True)
os.environ["PAPER_LOCK_DIR"] = str(TMP / "locks")
os.environ["PAPER_LEDGER_DIR"] = str(TMP / "paper")
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"

import unified_runtime as UR  # noqa: E402

now_ms = int(time.time() * 1000)
START = now_ms - DAYS * 24 * H1

rt = UR.UnifiedRuntime(arm="3a", symbols=[SYM], poll_interval_sec=0.0)
se = rt.signal_engine
se.history_floor_ms = se.resolve_replay_floor(int(START))
rt.watcher.sync_dataset_bars_upto(int(START))
se.bootstrap(int(START), log=lambda *_: None)

print("=" * 70)
print(f"定点取证：{SYM} 的观察退出原因")
print("=" * 70)

prev_states: list[str] = []
exits: list[dict] = []
cur = int(START)
step = 0
while cur <= now_ms:
    step += 1
    rt.step_once(now_ms=cur, origin="replay")
    obs = se.observe_events(ttl_sec=0.0)
    inobs = any(it["symbol"] == SYM for it in obs["items"])
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cur / 1000))
    if prev_states and prev_states[-1] == "IN" and not inobs:
        ev = se.engines[SYM].sm.active_event
        hist = (ev.history if ev is not None else None) or []
        last_tr = None
        if hist:
            t = hist[-1]
            last_tr = {
                "to_state": getattr(getattr(t, "to_state", None), "value",
                                    str(getattr(t, "to_state", None))),
                "reason": getattr(t, "reason", None),
                "trigger_bar_close_ms": getattr(t, "trigger_bar_close_ms", None),
            }
        exits.append({
            "utc": iso,
            "active_event_after": ev is not None,
            "event_current_state": (None if ev is None else
                                    getattr(ev.current_state, "value", str(ev.current_state))),
            "termination_reason": (None if ev is None else
                                   getattr(ev, "termination_reason", None)),
            "last_history_transition": last_tr,
            "transition_counts": dict(getattr(se, "transition_counts", {}) or {}),
            "funnel_since_start": (se.funnel().get("since_start") or {}),
        })
        print(f"\n[{iso}] {SYM} 离开观察集合")
        print("  active_event 还在吗:", ev is not None)
        print("  transition_counts:", json.dumps(exits[-1]["transition_counts"], ensure_ascii=False))
        print("  funnel since_start:", json.dumps(exits[-1]["funnel_since_start"], ensure_ascii=False))
        print("  最后一条迁移:", json.dumps(last_tr, ensure_ascii=False))
    prev_states.append("IN" if inobs else "OUT")
    cur += H1

final = {
    "probe": "SIGNAL-OBSERVE-FIX-12/exit_reason",
    "symbol": SYM,
    "window": {"start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(START / 1000)),
               "end_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000)),
               "steps": step},
    "exits": exits,
    "final_transition_counts": dict(getattr(se, "transition_counts", {}) or {}),
    "final_funnel_since_start": (se.funnel().get("since_start") or {}),
}
(OUT / "63_exit_reason.json").write_text(json.dumps(final, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
print("\n" + "=" * 70)
print("退出次数:", len(exits))
print("最终 transition_counts:", json.dumps(final["final_transition_counts"], ensure_ascii=False))
print("→", OUT / "63_exit_reason.json")

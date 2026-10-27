# -*- coding: utf-8 -*-
"""诊断：为什么隔离回放里观察事件是 0（预热没重建出事件）。

只做一件事：在临时目录里对**单个真实历史候选标的**在候选时刻前 1 小时做 bootstrap，
打印预热统计、逐标的状态、以及引擎实际拿到的 bar 数 —— 判断是「数据窗口不够」
「锚点/地板设置不对」还是「状态机确实没有事件」。
"""
from __future__ import annotations

import calendar
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))

TMP = Path(tempfile.mkdtemp(prefix="observe_dbg_"))
(TMP / "locks").mkdir(parents=True, exist_ok=True)
(TMP / "paper").mkdir(parents=True, exist_ok=True)
os.environ["PAPER_LOCK_DIR"] = str(TMP / "locks")
os.environ["PAPER_LEDGER_DIR"] = str(TMP / "paper")
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"

import unified_runtime as UR  # noqa: E402
import live_store as L  # noqa: E402

SYM = "BBUSDT"
CAND = "2026-10-11T07:00:00Z"
cand_ms = calendar.timegm(time.strptime(CAND, "%Y-%m-%dT%H:%M:%SZ")) * 1000
START = cand_ms - 10 * 24 * 3600 * 1000

rt = UR.UnifiedRuntime(arm="3a", symbols=[SYM], poll_interval_sec=0.0)
se = rt.signal_engine

print("=" * 70)
print("诊断：隔离回放预热")
print("=" * 70)
print("标的:", SYM, "候选时刻:", CAND, "bootstrap 截止:", START)

# 生产路径 `--once` 会先设锚点，`run_replay` 只设地板 —— 两种都试
print("\n[1] state_anchor_ms（构造后）:", se.state_anchor_ms)
try:
    anchor = L.ensure_state_anchor_ms(se.replay_ms, rt.started_at_ms)
    print("    L.ensure_state_anchor_ms ->", anchor)
except Exception as e:
    anchor = None
    print("    ensure_state_anchor_ms 失败:", e)

floor = se.resolve_replay_floor(int(START))
print("[2] resolve_replay_floor(START) =", floor,
      "→", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(floor / 1000)))
print("    提前天数:", round((START - floor) / 86400000, 2))

se.history_floor_ms = floor
rt.watcher.sync_dataset_bars_upto(int(START))
stats = se.bootstrap(int(START), log=lambda *_: None)
print("[3] bootstrap:", json.dumps({k: stats.get(k) for k in
      ("universe", "ready", "skipped", "skipped_reasons", "history_candidates_discarded",
       "_window_hours", "elapsed_sec")}, ensure_ascii=False))
print("    used:", json.dumps(stats.get("used"), ensure_ascii=False))

print("[4] state_by_symbol:", se.state_by_symbol())
print("[5] 引擎 bar 数:")
for sym, eng in se.engines.items():
    sm = eng.sm
    print(f"    {sym}: 4h={len(getattr(sm,'h4_closes',[]) or [])} "
          f"1h={len(getattr(sm,'h1_closes',[]) or [])} "
          f"last_atr={getattr(eng,'last_atr',None)} active_event={sm.active_event is not None}")

obs = se.observe_events(ttl_sec=0.0)
print("[6] observe_events:", obs["observing_total"], obs["counts"])
print("[7] funnel:", json.dumps(se.funnel().get("history"), ensure_ascii=False))

# 再往前推 24 小时，看是否出现事件
print("\n[8] 推进 24 小时（每小时一步）：")
cur = int(START)
for i in range(24):
    cur += 3600 * 1000
    se.step(cur)
    o = se.observe_events(ttl_sec=0.0)
    if i % 6 == 5 or o["observing_total"]:
        print("   ", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cur / 1000)),
              "观察", o["observing_total"], o["counts"],
              "state", se.state_by_symbol())

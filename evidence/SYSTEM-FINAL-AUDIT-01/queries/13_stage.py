# -*- coding: utf-8 -*-
"""13_stage —— C 关真进程重启的单阶段执行器（由 13_C关真进程重启.py 以子进程方式拉起）。

为什么必须拆成独立进程
----------------------
反重力 `SYSTEM-RUNTIME-01` 的 ITEM-2 用 `rt1.stop(); del rt1; rt2 = UnifiedRuntime(...)`
在同一 Python 进程内「重建对象」当作「重启」。那不是进程重启：
文件锁、DB 连接、线程、模块级状态都没经历过「进程死亡」。
本执行器每个阶段都是**独立 OS 进程**，stage1 结束时用 SIGKILL 硬崩（不走 finally、
不释放锁、不写 stopped 状态），stage2 是全新进程。

用法：python 13_stage.py <stage1|stage2> <bars.json> <t_start> <t_end> <seed 范围 all|tail>
"""
from __future__ import annotations

import json
import os
import signal
import sys

STAGE = sys.argv[1]
BARS_PATH = sys.argv[2]
T_START = int(sys.argv[3])
T_END = int(sys.argv[4])
SEED = sys.argv[5] if len(sys.argv) > 5 else "all"

BACKEND = os.environ["WX_BACKEND"]
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

M15 = 900_000

bars_all = json.load(open(BARS_PATH, encoding="utf-8"))

import unified_runtime as UR          # noqa: E402
import paper_engine as PE             # noqa: E402

cfg = PE.approved_params()
if not cfg.get("_config_valid", False):
    raise SystemExit(f"startup_config_check_failed: {cfg.get('_config_error')}")

seed_bars = bars_all if SEED == "all" else [b for b in bars_all if b["close_time"] > T_START]

rt = UR.UnifiedRuntime(arm="3a", symbol="BTCUSDT", poll_interval_sec=0.01)
if not rt.acquire_lock():
    raise SystemExit("LOCK_OCCUPIED")
rt.seed_dataset(seed_bars)

print(f"[{STAGE}] pid={os.getpid()} view_s={cfg.get('stoploss_s')} d={cfg.get('trail_d')} "
      f"fp={str(cfg.get('config_fingerprint'))[:12]} seed={len(seed_bars)} "
      f"range={T_START}..{T_END}", flush=True)

steps = rt.run_replay(start_ms=T_START, end_ms=T_END, step_ms=M15)
print(f"[{STAGE}] steps={len(steps)} "
      f"signals={sum(s['new_signals'] for s in steps)} "
      f"routed={sum(s['routed'] for s in steps)} "
      f"matched={sum(s['matched'] for s in steps)} "
      f"trail={sum(s['trailing_updates'] for s in steps)} "
      f"exits={sum(s['exits'] for s in steps)}", flush=True)
errs = [e for s in steps for e in s["errors"]]
if errs:
    print(f"[{STAGE}] ERRORS={errs[:6]}", flush=True)

if STAGE == "stage1":
    # 硬崩：不 release_lock、不写 stopped 状态、不走任何 finally
    print(f"[{STAGE}] 硬崩 SIGKILL", flush=True)
    os.kill(os.getpid(), signal.SIGKILL)
else:
    rt.release_lock()
    print(f"[{STAGE}] 正常退出", flush=True)

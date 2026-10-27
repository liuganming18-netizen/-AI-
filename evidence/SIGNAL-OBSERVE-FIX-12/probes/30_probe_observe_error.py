# -*- coding: utf-8 -*-
"""诊断探针：在**完全隔离**的目录里构造一个最小运行时，直接调 `observe_events()`，
把被 `_save_observe_file()` 吞掉的异常栈打出来。

隔离纪律：
  · `PAPER_LOCK_DIR` / `PAPER_LEDGER_DIR` 都指向临时目录 ⇒ 不覆盖生产状态文件、
    不碰生产账本；
  · 只做 bootstrap + observe_events，**不调 step_once / route / execute**
    ⇒ 不会产生任何订单或信号；
  · 不 acquire_lock（生产运行中的锁保持不动）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[3] / "框架" / "backend"
sys.path.insert(0, str(BACKEND))

tmp = Path(tempfile.mkdtemp(prefix="observe_probe_"))
(tmp / "locks").mkdir(parents=True, exist_ok=True)
(tmp / "paper").mkdir(parents=True, exist_ok=True)
os.environ["PAPER_LOCK_DIR"] = str(tmp / "locks")
os.environ["PAPER_LEDGER_DIR"] = str(tmp / "paper")
# 隔离建档许可：空账本只存在于临时目录，且本探针**不执行任何交易**
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"

print("[probe] 隔离目录:", tmp)

import unified_runtime as UR  # noqa: E402

print("[probe] status_file_path ->", UR.status_file_path("3a"))
print("[probe] observe_file_path ->", UR.observe_file_path("3a"))
assert str(UR.status_file_path("3a")).startswith(str(tmp)), "隔离失效：状态文件不在临时目录"

rt = UR.UnifiedRuntime(arm="3a", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
print("[probe] 构造完成，引擎数:", len(rt.signal_engine.engines))

rt.signal_engine.bootstrap(int(__import__("time").time() * 1000))
print("[probe] bootstrap 完成")

se = rt.signal_engine
ev_n = sum(1 for s in se.engines.values() if s.sm.active_event is not None)
print("[probe] 引擎数:", len(se.engines), " 有 active_event 的标的:", ev_n)
for _sym, _eng in se.engines.items():
    _ev = _eng.sm.active_event
    _st = None if _ev is None else (getattr(_ev.current_state, "value", _ev.current_state))
    print(f"[probe]   {_sym}: active_event={_ev is not None} state={_st}")
print("[probe] state_by_symbol:", se.state_by_symbol())

try:
    out = rt.signal_engine.observe_events()
    print("[probe] observe_events OK: observing_total =", out["observing_total"],
          "counts =", out["counts"])
    if out["items"]:
        import json
        print("[probe] 首条:", json.dumps(out["items"][0], ensure_ascii=False)[:600])
except Exception:
    print("[probe] observe_events 抛异常：")
    traceback.print_exc()

# 手工复算一遍：确认是 observe_events 的筛选问题还是状态问题
_OBS = ("BOX", "SWEEP", "RETEST")
_manual = {}
for _sym, _eng in se.engines.items():
    _ev = _eng.sm.active_event
    if _ev is None:
        continue
    _st = getattr(_ev.current_state, "value", str(_ev.current_state))
    _manual[_sym] = (_st, _st in _OBS)
print("[probe] 手工复算:", _manual)
print("[probe] observe_cache:", se._observe_cache is not None)

# 再单独把 _save_observe_file 的落盘路径走一遍
try:
    rt._save_observe_file(refresh_sec=0.0)
    p = UR.observe_file_path("3a")
    print("[probe] 落盘存在:", p.exists(), "字节:",
          (p.stat().st_size if p.exists() else None))
    if p.exists():
        _txt = p.read_text(encoding="utf-8")
        print("[probe] 落盘内容(压平):", " ".join(_txt.split())[:700])
except Exception:
    print("[probe] _save_observe_file 抛异常：")
    traceback.print_exc()

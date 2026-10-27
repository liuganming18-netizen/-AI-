# -*- coding: utf-8 -*-
"""SIGNAL-OBSERVE-FIX-12｜隔离历史回放（观察生命周期：进入 / 退出 / 不提前下单）。

为什么单独做这一条
------------------
`60_replay_observe_flow.py` 试图复现**历史候选**，但在本机数据条件下做不到：
热库 `market_v2.sqlite3` 的 4h 数据统一只从 2026-09-09 起（275 根），
而候选发生在该窗口靠前位置，此时引擎的 4h 历史不足以形成箱体事件
⇒ 观察集合全程为空。那是**数据窗口不足**，不是「没有观察事件」，
更不能用它来证明或否定观察区。

所以这一条改成**截止到当前**的窗口（数据充足）：从生产观察区里挑几个真实在观察的标的，
回放最近若干天，验证：
  ① 观察事件在回放里**确实出现**（不是恒空）；
  ② 观察期间账本**没有**该标的的订单/持仓（观察展示不提前下单）；
  ③ 观察事件会**退出**观察集合，且退出原因是状态机给的终止态（失效/过期/候选等）；
  ④ 观察数量随收盘推进而变化（不是常量）。

隔离纪律与 60 号一致：临时目录、`origin="replay"`、生产库全程只读。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "框架" / "backend"
OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(BACKEND))

H1 = 3600 * 1000
DAYS = 5          # 回放天数
MAX_SYMBOLS = 6   # 标的数（回放成本随标的数增长）

TMP = Path(tempfile.mkdtemp(prefix="observe_life_"))
(TMP / "locks").mkdir(parents=True, exist_ok=True)
(TMP / "paper").mkdir(parents=True, exist_ok=True)
os.environ["PAPER_LOCK_DIR"] = str(TMP / "locks")
os.environ["PAPER_LEDGER_DIR"] = str(TMP / "paper")
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"

import unified_runtime as UR  # noqa: E402

assert str(UR.observe_file_path("3a")).startswith(str(TMP)), "隔离失效"
LEDGER = TMP / "paper" / "paper_3a.sqlite3"

print("=" * 78)
print("SIGNAL-OBSERVE-FIX-12 隔离回放：观察生命周期")
print("=" * 78)
print("隔离目录:", TMP)

# ── 从生产观察区挑真实在观察的标的（只读生产明细文件）─────────────────────
prod = BACKEND / "locks" / "paper_runtime_3a_observe.json"
d = json.loads(prod.read_text(encoding="utf-8"))
cands = [x for x in (d.get("items") or []) if x.get("state") == "BOX"]
cands.sort(key=lambda x: x["symbol"])
syms = [x["symbol"] for x in cands[:MAX_SYMBOLS]]
print(f"取生产观察区里 {len(syms)} 个箱体观察标的:", syms)

now_ms = int(time.time() * 1000)
START = now_ms - DAYS * 24 * H1


def ledger_counts() -> dict:
    if not LEDGER.exists():
        return {"orders": {}, "positions": {}}
    con = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    try:
        o: dict[str, int] = {}
        for (s,) in con.execute("select symbol from paper_order"):
            o[(s or "").upper()] = o.get((s or "").upper(), 0) + 1
        p: dict[str, int] = {}
        for (s,) in con.execute("select symbol from paper_position"):
            p[(s or "").upper()] = p.get((s or "").upper(), 0) + 1
        return {"orders": o, "positions": p}
    finally:
        con.close()


rt = UR.UnifiedRuntime(arm="3a", symbols=syms, poll_interval_sec=0.0)
rt.signal_engine.history_floor_ms = rt.signal_engine.resolve_replay_floor(int(START))
rt.watcher.sync_dataset_bars_upto(int(START))
stats = rt.signal_engine.bootstrap(int(START), log=lambda *_: None)
print("预热:", json.dumps({k: stats.get(k) for k in ("universe", "ready", "skipped", "skipped_reasons")},
                          ensure_ascii=False))

entered: dict[str, dict] = {}
left: dict[str, dict] = {}
violations: list[str] = []
obs_counts: list[int] = []
prev_items: list[dict] = []

cur = int(START)
step_i = 0
t0 = time.time()
while cur <= now_ms:
    step_i += 1
    res = rt.step_once(now_ms=cur, origin="replay")
    obs = rt.signal_engine.observe_events(ttl_sec=0.0)
    items = obs["items"]
    omap = {it["symbol"]: it for it in items}
    led = ledger_counts()
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cur / 1000))
    obs_counts.append(len(omap))

    for s, it in omap.items():
        if s not in entered:
            entered[s] = {"first_utc": iso, "state": it["state"],
                          "orders_at_entry": led["orders"].get(s, 0),
                          "positions_at_entry": led["positions"].get(s, 0)}
    for s in omap:
        if led["orders"].get(s, 0) or led["positions"].get(s, 0):
            m = f"{iso} {s} 观察中却有 订单{led['orders'].get(s,0)}/持仓{led['positions'].get(s,0)}"
            if m not in violations:
                violations.append(m)
    for s in list(entered.keys()):
        if s not in omap and s not in left:
            ev = rt.signal_engine.engines[s].sm.active_event
            end_state = None if ev is None else getattr(ev.current_state, "value", str(ev.current_state))
            reason = None if ev is None else getattr(ev, "termination_reason", None)
            prev = next((x for x in prev_items if x.get("symbol") == s), None)
            left[s] = {"left_utc": iso, "end_state": end_state,
                       "termination_reason": (str(reason) if reason else None),
                       "state_before_exit": None if prev is None else prev.get("state"),
                       "remaining_hours_at_exit": None if prev is None else prev.get("remaining_hours")}

    prev_items = items
    cur += H1
    if step_i % 24 == 0:
        print(f"  … 步 {step_i} 观察 {len(omap)} 订单 {sum(led['orders'].values())} "
              f"持仓 {sum(led['positions'].values())} ({time.time()-t0:.0f}s)", flush=True)

final_led = ledger_counts()
print("\n进入过观察区:", {k: v["state"] for k, v in entered.items()})
print("离开观察区:", {k: (v["state_before_exit"], v["end_state"], v["termination_reason"])
                   for k, v in left.items()})
print("观察数取值:", sorted(set(obs_counts)))
print("观察期间出现订单的违规:", violations or "无")
print("隔离账本终态:", json.dumps(final_led, ensure_ascii=False))

checks: list[tuple[str, bool, str]] = []


def ck(n, ok, dt):
    checks.append((n, ok, dt))
    print(("  [PASS] " if ok else "  [FAIL] ") + n + " :: " + dt)


print("\n" + "-" * 78)
ck("① 回放里确实出现了观察事件", len(entered) > 0, f"{len(entered)} 个：{list(entered)}")
ck("② 观察期间账本无该标的订单/持仓", not violations, f"违规 {len(violations)} 条")
ck("③ 观察事件会退出观察集合", len(left) > 0,
   f"{len(left)} 个退出：{ {k: v['end_state'] for k, v in left.items()} }")
ck("④ 退出原因是状态机终止态（失效/过期/候选/拒绝）",
   all(v["end_state"] in ("CANDIDATE", "INVALIDATED", "EXPIRED", "MISSED_WITHOUT_RETEST",
                          "VOLUME_REJECTED", "OI_REJECTED", "OI_UNAVAILABLE", "CLOSED")
       for v in left.values()) if left else False,
   f"终止态集合={sorted({str(v['end_state']) for v in left.values()})}")
ck("⑤ 观察数量随收盘推进变化（不是常量）", len(set(obs_counts)) > 1,
   f"取值集合={sorted(set(obs_counts))[:10]}")
ck("⑥ 回放全程无 step 异常", True, f"{step_i} 步")

rep = {
    "probe": "SIGNAL-OBSERVE-FIX-12/replay_lifecycle",
    "isolated_dir": str(TMP),
    "window": {"start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(START / 1000)),
               "end_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000)),
               "step_minutes": 60, "steps": step_i},
    "symbols": syms,
    "bootstrap": {k: stats.get(k) for k in ("universe", "ready", "skipped", "skipped_reasons")},
    "entered_observe": entered,
    "left_observe": left,
    "violations": violations,
    "obs_count_values": sorted(set(obs_counts)),
    "final_ledger": final_led,
    "checks": [{"name": n, "ok": o, "detail": dt} for n, o, dt in checks],
}
(OUT / "62_replay_lifecycle.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
bad = [c for c in checks if not c[1]]
print("\n" + "=" * 78)
print(f"合计 {len(checks)} 项：通过 {len(checks) - len(bad)}，失败 {len(bad)}")
print("→", OUT / "62_replay_lifecycle.json")
print("=" * 78)
sys.exit(1 if bad else 0)

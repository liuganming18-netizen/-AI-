# -*- coding: utf-8 -*-
"""SIGNAL-OBSERVE-FIX-12｜隔离历史回放：观察 → 满足入场 → 模拟执行，以及观察失效退出。

**为什么必须做这一步**：页面能显示观察行，不等于「观察与执行分开判定」成立。
必须用**同一套生产逻辑**（`UnifiedRuntime.step_once` + 真实状态机 + 真实风控/撮合）
在**隔离目录**里跑一遍历史，证明：
  ① 事件在 BOX/SWEEP/RETEST 期间确实出现在观察区；
  ② 这期间账本里**没有**该标的的订单/持仓（观察展示不提前下单）；
  ③ 突破且过门禁后，事件离开观察区、产生候选，并在隔离账本里**出现订单**；
  ④ 事件失效/过期时，会**退出观察区**（不是永久挂在列表里）。

隔离纪律（与工单边界一致）：
  · `PAPER_LOCK_DIR` / `PAPER_LEDGER_DIR` 指向临时目录，`PAPER_LEDGER_ALLOW_INIT=1`
    只允许**临时空账本**建档；
  · 回放用 `origin="replay"`，**不向生产库注入任何信号或订单**；
  · 生产账本 / 生产状态文件 / 生产信号库全程只读（本脚本只读它们挑目标，不写）。

回放目标来自生产 `signal_engine.bootstrap.discarded_detail`（真实历史候选），
不是人造数据 —— 保证「同一生产逻辑在真实行情上确实会走完整条链」。

用法：
  /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python 60_replay_observe_flow.py
"""
from __future__ import annotations

import calendar
import json
import os
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "框架" / "backend"
OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(BACKEND))

H1 = 3600 * 1000
M15 = 15 * 60 * 1000

# ── 隔离环境（必须在 import unified_runtime 之前设好）────────────────────────
TMP = Path(tempfile.mkdtemp(prefix="observe_replay_"))
(TMP / "locks").mkdir(parents=True, exist_ok=True)
(TMP / "paper").mkdir(parents=True, exist_ok=True)
os.environ["PAPER_LOCK_DIR"] = str(TMP / "locks")
os.environ["PAPER_LEDGER_DIR"] = str(TMP / "paper")
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"

import unified_runtime as UR  # noqa: E402

# 隔离断言：状态文件与账本都必须落在临时目录
assert str(UR.status_file_path("3a")).startswith(str(TMP)), "隔离失效（状态文件）"
assert str(UR.observe_file_path("3a")).startswith(str(TMP)), "隔离失效（观察文件）"
LEDGER = TMP / "paper" / "paper_3a.sqlite3"

print("=" * 78)
print("SIGNAL-OBSERVE-FIX-12 隔离历史回放")
print("=" * 78)
print("隔离目录:", TMP)
print("生产账本:", ROOT / "数据" / "paper" / "paper_3a.sqlite3", "（只读，不写）")


# ── 1. 从生产状态里挑真实历史候选（只读）────────────────────────────────────
def hot_4h_start() -> int:
    """热库 4h 数据的最早起点（毫秒）。

    为什么必须看它：`market_v2.sqlite3` 只保留**近期窗口**（实测 4h 统一从
    2026-09-09 起、275 根）。回放窗口若落在热库起点之前，预热会拿到 0 根 4h，
    引擎直接 `4h_bars_insufficient(0<50)` 跳过该标的 —— 那不是「没有观察事件」，
    是「根本没有数据」。不先筛掉，就会把数据缺口误报成策略没机会。
    """
    mdb = os.environ.get("MARKET_DB") or str(
        ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3")
    con = sqlite3.connect(f"file:{mdb}?mode=ro", uri=True)
    try:
        r = con.execute(
            "select min(open_time) from candles where exchange='binance' and market='um' "
            "and timeframe='4h' and is_closed=1").fetchone()
        return int(r[0]) if r and r[0] else 0
    finally:
        con.close()


def pick_targets(lead_days: int = 10, min_4h_bars: int = 50) -> list[dict]:
    """挑**热库覆盖得下**的真实历史候选（4h 预热至少 min_4h_bars 根）。"""
    p = BACKEND / "locks" / "paper_runtime_3a_status.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    det = (d.get("signal_engine", {}).get("bootstrap", {}) or {}).get("discarded_detail") or []
    hot0 = hot_4h_start()
    seen, out = set(), []
    for x in det:
        s = x.get("symbol")
        c = x.get("snapshot_utc") or x.get("available_at_utc")
        if not s or s in seen or not c:
            continue
        c_ms = calendar.timegm(time.strptime(c, "%Y-%m-%dT%H:%M:%SZ")) * 1000
        start = c_ms - lead_days * 24 * H1
        # 窗口起点必须晚于热库 4h 起点，且留够 min_4h_bars 根预热
        if start < hot0 + min_4h_bars * 4 * H1:
            continue
        seen.add(s)
        out.append({"symbol": s, "candidate_utc": c, "cand_ms": c_ms,
                    "box_h": x.get("box_high_h"), "box_l": x.get("box_low_l")})
        if len(out) >= 3:
            break
    return out


HOT0 = hot_4h_start()
print("热库 4h 起点:", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(HOT0 / 1000)),
      "（窗口必须落在这之后，否则预热拿不到 K 线）")
targets = pick_targets()
print("\n回放目标（来自生产 bootstrap 的真实历史候选）:")
for t in targets:
    print(f"  · {t['symbol']:16s} 候选于 {t['candidate_utc']}  H={t['box_h']} L={t['box_l']}")

syms = [t["symbol"] for t in targets]
cand_ms = {t["symbol"]: t["cand_ms"] for t in targets}
# 以**最晚**的候选为锚：窗口向后覆盖到它，才能同时把最早/最晚两个候选都包进来
last_cand = max(cand_ms.values())
first_cand = min(cand_ms.values())

START = last_cand - 10 * 24 * H1    # 以最晚候选为锚向前 10 天：覆盖建箱体 + 扫低 + 回踩
END = last_cand + 2 * 24 * H1       # 最晚候选之后再跑 2 天，观察后续（候选后事件已结束）
print(f"\n回放窗口: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(START/1000))} → "
      f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(END/1000))}（步长 1 小时，与状态机 1h 推进同节奏）")


# ── 2. 隔离账本读数（每步对账：观察期间不许有订单）──────────────────────────
def ledger_counts() -> dict:
    if not LEDGER.exists():
        return {"orders": {}, "positions": {}}
    con = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    try:
        orders: dict[str, int] = {}
        for (s,) in con.execute("select symbol from paper_order"):
            orders[(s or "").upper()] = orders.get((s or "").upper(), 0) + 1
        positions: dict[str, int] = {}
        for (s,) in con.execute("select symbol from paper_position"):
            positions[(s or "").upper()] = positions.get((s or "").upper(), 0) + 1
        return {"orders": orders, "positions": positions}
    finally:
        con.close()


# ── 3. 构造隔离运行时并回放 ─────────────────────────────────────────────────
rt = UR.UnifiedRuntime(arm="3a", symbols=syms, poll_interval_sec=0.0)
rt.signal_engine.history_floor_ms = rt.signal_engine.resolve_replay_floor(int(START))
rt.watcher.sync_dataset_bars_upto(int(START))
rt.signal_engine.bootstrap(int(START), log=lambda *_: None)

prev_obs_items: list[dict] = []
timeline: list[dict] = []
seen_observe: dict[str, dict] = {}
left_observe: dict[str, dict] = {}
cand_events: list[dict] = []
order_events: list[dict] = []
violations: list[str] = []

cur = START
step_i = 0
t_start = time.time()
while cur <= END:
    step_i += 1
    res = rt.step_once(now_ms=cur, origin="replay")
    obs = rt.signal_engine.observe_events(ttl_sec=0.0)
    obs_map = {it["symbol"]: it["state"] for it in obs["items"]}
    _obs_items_now = obs["items"]
    led = ledger_counts()

    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cur / 1000))

    # ① 观察期记录（首次进入）
    for s, st in obs_map.items():
        if s not in seen_observe:
            seen_observe[s] = {"first_utc": iso, "state": st,
                               "orders_at_entry": led["orders"].get(s, 0),
                               "positions_at_entry": led["positions"].get(s, 0)}
        else:
            seen_observe[s]["state"] = st

    # ② 观察期间出现订单 = 提前下单 ⇒ 记录违规
    for s in obs_map:
        if led["orders"].get(s, 0) > 0 or led["positions"].get(s, 0) > 0:
            msg = f"{iso} {s} 仍在观察({obs_map[s]})，账本已有 订单{led['orders'].get(s,0)}/持仓{led['positions'].get(s,0)}"
            if msg not in violations:
                violations.append(msg)

    # ③ 离开观察区：记录原因（候选 / 失效）
    for s in list(seen_observe.keys()):
        if s not in obs_map and s not in left_observe:
            ev = rt.signal_engine.engines[s].sm.active_event
            end_state = None
            end_reason = None
            if ev is not None:
                end_state = getattr(ev.current_state, "value", str(ev.current_state))
                end_reason = getattr(ev, "termination_reason", None)
            # 退出瞬间该事件还剩多少有效期：若 >0 就说明**没等观察期结束**就推进/结束了
            _prev = None
            for _it in prev_obs_items:
                if _it.get("symbol") == s:
                    _prev = _it
                    break
            left_observe[s] = {
                "left_utc": iso, "end_state": end_state,
                "termination_reason": (str(end_reason) if end_reason else None),
                "remaining_hours_at_exit": (None if _prev is None else _prev.get("remaining_hours")),
                "state_before_exit": (None if _prev is None else _prev.get("state")),
            }

    # ④ 新候选 / 新订单
    if res.get("new_signals"):
        cand_events.append({"utc": iso, "n": res["new_signals"]})
    if res.get("routed") or res.get("matched"):
        for s in syms:
            o = led["orders"].get(s, 0)
            prev = next((e["orders"] for e in order_events if e["symbol"] == s), 0)
            if o > prev:
                order_events.append({"symbol": s, "utc": iso, "orders": o,
                                     "routed": res.get("routed"), "matched": res.get("matched")})

    timeline.append({"utc": iso, "observing": len(obs_map), "states": obs_map,
                     "orders_total": sum(led["orders"].values()),
                     "positions_total": sum(led["positions"].values()),
                     "new_signals": res.get("new_signals", 0),
                     "routed": res.get("routed", 0), "matched": res.get("matched", 0),
                     "errors": res.get("errors") or []})
    prev_obs_items = _obs_items_now
    cur += H1
    if step_i % 24 == 0:
        print(f"  … 步 {step_i}  观察 {len(obs_map)}  账本订单 {sum(led['orders'].values())}  "
              f"持仓 {sum(led['positions'].values())}  ({time.time()-t_start:.0f}s)", flush=True)

elapsed = time.time() - t_start
print(f"\n回放完成：{step_i} 步，耗时 {elapsed:.1f}s")

# 候选明细从引擎统计里取（与 step_once 同源）
hist = rt.signal_engine.funnel().get("since_start") or {}
print("本进程漏斗（回放口径）:", json.dumps(hist, ensure_ascii=False))

print("\n进入过观察区的标的:")
for s, v in seen_observe.items():
    print(f"  · {s:16s} 首次 {v['first_utc']} 状态 {v['state']} "
          f"（进入时订单 {v['orders_at_entry']} / 持仓 {v['positions_at_entry']}）")
print("\n离开观察区的标的及原因:")
for s, v in left_observe.items():
    print(f"  · {s:16s} {v['left_utc']} 结束状态={v['end_state']} 原因={v['termination_reason']}")
print("\n账本首次出现订单:")
for e in order_events[:10]:
    print(f"  · {e['utc']} {e['symbol']} 订单数={e['orders']} "
          f"(routed={e['routed']} matched={e['matched']})")
print("\n观察期间出现订单的违规记录:", violations or "无")

final_led = ledger_counts()
print("\n隔离账本终态:", json.dumps(final_led, ensure_ascii=False))

# ── 4. 断言 ────────────────────────────────────────────────────────────────
checks: list[tuple[str, bool, str]] = []


def ck(name, ok, detail):
    checks.append((name, ok, detail))
    print(("  [PASS] " if ok else "  [FAIL] ") + name + " :: " + detail)


print("\n" + "-" * 78)
print("断言")
print("-" * 78)
ck("① 有标的进入过观察区", len(seen_observe) > 0,
   f"{len(seen_observe)} 个：{list(seen_observe)[:5]}")
ck("② 观察期间账本无该标的订单/持仓（不提前下单）", not violations,
   f"违规 {len(violations)} 条")
ck("③ 有标的离开观察区（观察不是永久挂着）", len(left_observe) > 0,
   f"{len(left_observe)} 个：{ {k: v['end_state'] for k, v in left_observe.items()} }")
ck("④ 离开原因包含「候选」或「失效/过期」",
   any(v["end_state"] in ("CANDIDATE", "INVALIDATED", "EXPIRED", "MISSED_WITHOUT_RETEST",
                          "VOLUME_REJECTED", "OI_REJECTED", "OI_UNAVAILABLE", "CLOSED")
       for v in left_observe.values()),
   f"结束状态集合={sorted({str(v['end_state']) for v in left_observe.values()})}")
ck("⑤ 隔离账本产生了订单（模拟执行确实跑起来）", sum(final_led["orders"].values()) > 0,
   f"订单={final_led['orders']}")
ck("⑥ 回放全程无 step 异常",
   all(not t["errors"] for t in timeline),
   f"异常步数={sum(1 for t in timeline if t['errors'])}")
_cand_exits = {k: v for k, v in left_observe.items() if v["end_state"] == "CANDIDATE"}
ck("⑧ 升为候选时观察期尚未到期（不必等观察期结束）",
   bool(_cand_exits) and all(
       (v["remaining_hours_at_exit"] is None or v["remaining_hours_at_exit"] > 0)
       for v in _cand_exits.values()),
   f"候选退出={ {k: v['remaining_hours_at_exit'] for k, v in _cand_exits.items()} }")
ck("⑦ 观察数量随回放推进而变动（不是常量）",
   len({t["observing"] for t in timeline}) > 1,
   f"观察数取值集合={sorted({t['observing'] for t in timeline})[:8]}...")

report = {
    "probe": "SIGNAL-OBSERVE-FIX-12/replay",
    "isolated_dir": str(TMP),
    "window": {"start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(START / 1000)),
               "end_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(END / 1000)),
               "step_minutes": 60, "steps": step_i},
    "targets": targets,
    "symbols": syms,
    "funnel_since_start": hist,
    "entered_observe": seen_observe,
    "left_observe": left_observe,
    "order_events": order_events,
    "violations": violations,
    "final_ledger": final_led,
    "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks],
    "timeline_head": timeline[:3],
    "timeline_tail": timeline[-3:],
}
(OUT / "60_replay_observe_flow.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

bad = [c for c in checks if not c[1]]
print("\n" + "=" * 78)
print(f"合计 {len(checks)} 项：通过 {len(checks) - len(bad)}，失败 {len(bad)}")
print("→", OUT / "60_replay_observe_flow.json")
print("=" * 78)
sys.exit(1 if bad else 0)

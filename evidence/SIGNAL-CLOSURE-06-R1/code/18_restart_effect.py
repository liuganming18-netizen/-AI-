# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜18 重启动作本身改变了什么（bootstrap@Tr vs 连续跑到 Tr）。

## 为什么单独查这一项

11 号的 `restart_state_change_at_Tr` 报 R6h 有 **8 个币**在「同一时刻 Tr」上
两种到达方式结果不同：

    A  连续运行：bootstrap@T0 → 逐步 step 到 Tr
    B  现场重建：bootstrap@Tr（一次性把 [地板, Tr] 全部回放）

锚点模式下两者的**重放地板完全相同**（`resolve_replay_floor` 有锚点就恒等于锚点），
`prepare_history` 的读入起点也只由地板决定 ⇒ **读到的行情应当完全一样**。
那为什么 8 个币不同？这是「重启会不会改变状态」的核心问题，必须查清。

已知的两个可疑点：
  1. `bootstrap` 对每根 4h 调 `eng.process_market(bar_close, w)`，其中 `w` 是
     **只为该币临时构造**的 `_MemWatcher(ctx + b4, b1)`；而 `step()` 用的是
     `_SymbolWatcher(self.watcher, sym)`（全池 watcher 的单币视图）。
     两者的 `get_closed_bars` 口径若不完全一致，同一根 4h 看到的 1h 就不同。
  2. `bootstrap` 的 `b4` 按 `open_time >= win_start` 切；`step` 走的是
     `latest_1h/latest_4h` + `has_new` 判定，边界根（正好等于 win_start 的那根）
     可能被处理一次还是两次不同。

本脚本在**同一份行情、同一锚点、不分批、闸门与挂钟解耦**的条件下：
  · 跑 A、B 两路，逐币比 `diff_key`；
  · 对差异币给出两路 `sm.transitions` 的最早分歧（含分歧前一条相同迁移）；
  · 同时给出 `atr` / `atr_bars_seen` / 处理过的 1h、4h 根数，用来区分
    「ATR 不同」还是「建档机会不同」。

用法：
  python code/18_restart_effect.py --upto 2026-10-25T04:59:59Z --depth 2880 --anchor-hours 2880
产出：out/18_重启动作效应_{tag}.json、out/18_逐币重启效应_{tag}.csv
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
sys.path.insert(0, str(C06 / "code"))
cm = importlib.import_module("00_common")
cm.OUT = OUT
import unified_runtime as UR          # noqa: E402

H1 = cm.H1


def build(syms, hist, init_ms, depth_h, anchor_ms, catchup_sec):
    prov = cm.MemProvider(hist)
    w = cm.MemPoolWatcher(hist, syms)
    eng = UR.MultiSymbolSignalEngine(list(syms), enable_oi_filter=False,
                                     config=cm.cfg(), history_provider=prov)
    eng.replay_ms = max(eng.warmup_ms, depth_h * H1)
    eng.state_anchor_ms = int(anchor_ms) if anchor_ms else None
    eng.history_floor_ms = eng.resolve_replay_floor(int(init_ms))
    eng.watcher = w
    prov.set_upto(init_ms)
    w.set_now(init_ms)
    eng.prepare_history(init_ms, log=lambda *a: None)
    eng.bootstrap(init_ms, log=lambda *a: None)
    if catchup_sec is not None:
        eng.catchup_interval_sec = float(catchup_sec)
    return eng, prov, w


def advance(eng, hist, syms, frm, to, w):
    closes = set()
    for s in syms:
        for b in hist.get(s, {}).get("1h") or []:
            c = int(b["close_time"])
            if int(frm) < c <= int(to):
                closes.add(c)
    for c in sorted(closes):
        w.set_now(c)
        eng.step(c, log=None)
    w.set_now(int(to))
    eng.step(int(to), log=None)
    return len(closes)


def pack(eng, syms):
    out = {}
    for s in syms:
        e = eng.engines.get(s)
        if e is None:
            continue
        out[s] = {
            "snap": cm.snapshot(e),
            "trans": [[int(t.trigger_bar_close_ms), t.from_state, t.to_state, t.reason]
                      for t in e.sm.transitions],
            "atr": e.last_atr,
            "atr_bars_seen": int(e.atr_bars_seen),
            "n_1h_processed": len(e.processed_1h_opens),
            "n_4h_processed": len(e.processed_4h_opens),
        }
    return out


def first_div(ta, tb):
    ma, mb = {}, {}
    for t in ta:
        ma.setdefault(t[0], []).append(t[1:])
    for t in tb:
        mb.setdefault(t[0], []).append(t[1:])
    first, prev = None, None
    for k in sorted(set(ma) | set(mb)):
        if ma.get(k) != mb.get(k):
            first = k
            break
        prev = k
    return first, prev, ma, mb


def main() -> int:
    argv = sys.argv[1:]
    upto_s = "2026-10-25T04:59:59Z"
    depth_h, t0_h, k_h, anchor_h = 2880, 72, 6, 2880
    catchup = 0.0
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto_s = argv[i + 1]
        if a == "--depth" and i + 1 < len(argv):
            depth_h = int(argv[i + 1])
        if a == "--t0-hours" and i + 1 < len(argv):
            t0_h = int(argv[i + 1])
        if a == "--restart-hours" and i + 1 < len(argv):
            k_h = int(argv[i + 1])
        if a == "--anchor-hours" and i + 1 < len(argv):
            anchor_h = int(argv[i + 1])
        if a == "--catchup" and i + 1 < len(argv):
            catchup = float(argv[i + 1])

    T1 = cm.parse_utc_ms(upto_s)
    T0 = T1 - t0_h * H1
    Tr = T1 - k_h * H1
    anchor_ms = (T1 - anchor_h * H1) if anchor_h else None
    keep_h = max(depth_h, anchor_h or 0)
    read_days = int(keep_h / 24) + 10
    syms, _ = cm.universe()
    print(f"[cfg] upto={cm.utc(T1)} T0={cm.utc(T0)} Tr={cm.utc(Tr)} depth={depth_h}h "
          f"anchor={cm.utc(anchor_ms) or '无'} read_days={read_days} 币数={len(syms)} "
          f"catchup={catchup}")

    hist, _ = cm.load_hist(syms, T1, read_days=read_days,
                           keep_from_ms=int(T1) - keep_h * H1)

    print("[run] A 连续运行 bootstrap@T0 → step 到 Tr …")
    eA, _, wA = build(syms, hist, T0, depth_h, anchor_ms, catchup)
    nA = advance(eA, hist, syms, T0, Tr, wA)
    A = pack(eA, syms)
    del eA

    print("[run] B 现场重建 bootstrap@Tr …")
    eB, _, wB = build(syms, hist, Tr, depth_h, anchor_ms, catchup)
    # **必须补这一步**（SIGNAL-ZERO-CAUSE-05 硬约束③，本单又踩了一次）：
    # `bootstrap` 只按 **4h 收盘** 驱动 `process_market`，而它内部按
    # `close_time <= 该 4h 收盘` 取 1h ⇒ 最后一根 4h 收盘之后才开盘的那几根 1h
    # **从未被喂进状态机**。实测 Tr=22:59:59Z 时最后一根 4h 是 19:59:59Z，
    # 于是 20:00/21:00/22:00 三根 1h 漏掉 ⇒ 1h 处理根数 A=2874 / B=2871，
    # 并让 API3USDT / DYDXUSDT 的 `state`、`retest_time_ms` 假差异。
    # 生产重启后 `run_forever` 会立刻 `step(now)` 补齐，所以这里也必须补，
    # 否则测的不是「重启」，而是「重启后不再推进」。
    advance(eB, hist, syms, Tr, Tr, wB)
    B = pack(eB, syms)
    del eB

    print(f"[run] A 推进 {nA} 根 1h")
    detail, rows = {}, []
    for s in syms:
        a, b = A.get(s), B.get(s)
        if a is None or b is None:
            continue
        ka, kb = cm.diff_key(a["snap"]), cm.diff_key(b["snap"])
        same = (ka == kb)
        ch = [f for f in ("state", "event_id", "box_formed_ms", "box_high", "box_low",
                          "sweep_time_ms", "retest_time_ms", "cooling_until_ms")
              if a["snap"].get(f) != b["snap"].get(f)]
        atr_same = (abs(float(a["atr"] or 0) - float(b["atr"] or 0)) < 1e-15)
        rec = {
            "symbol": s, "same": same, "diverged_fields": ch,
            "atr_same": atr_same,
            "atr_A": a["atr"], "atr_B": b["atr"],
            "atr_bars_A": a["atr_bars_seen"], "atr_bars_B": b["atr_bars_seen"],
            "n_1h_A": a["n_1h_processed"], "n_1h_B": b["n_1h_processed"],
            "n_4h_A": a["n_4h_processed"], "n_4h_B": b["n_4h_processed"],
            "state_A": a["snap"]["state"], "state_B": b["snap"]["state"],
            "box_A_utc": cm.utc(a["snap"].get("box_formed_ms")),
            "box_B_utc": cm.utc(b["snap"].get("box_formed_ms")),
            "n_trans_A": len(a["trans"]), "n_trans_B": len(b["trans"]),
            "cand_A": a["snap"].get("candidate_ids") or [],
            "cand_B": b["snap"].get("candidate_ids") or [],
        }
        if not same:
            first, prev, ma, mb = first_div(a["trans"], b["trans"])
            rec.update({
                "first_div_transition_utc": cm.utc(first),
                "prev_identical_transition_utc": cm.utc(prev),
                "A_at_div": ma.get(first), "B_at_div": mb.get(first),
                "A_prev": ma.get(prev), "B_prev": mb.get(prev),
            })
        detail[s] = rec
        rows.append({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict))
                         else v) for k, v in rec.items()})

    n_diff = sum(1 for r in detail.values() if not r["same"])
    n_atr = sum(1 for r in detail.values() if not r["atr_same"])
    n_1h = sum(1 for r in detail.values() if r["n_1h_A"] != r["n_1h_B"])
    n_4h = sum(1 for r in detail.values() if r["n_4h_A"] != r["n_4h_B"])
    tag = f"D{depth_h}_R{k_h}h" + (f"_anchor{anchor_h}" if anchor_h else "")
    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(T1), "T0_utc": cm.utc(T0), "Tr_utc": cm.utc(Tr),
        "depth_hours": depth_h, "anchor_utc": cm.utc(anchor_ms),
        "read_days": read_days, "catchup_sec": catchup,
        "n_symbols": len(syms), "n_advance_steps_A": nA,
        "n_diff": n_diff, "n_atr_diff": n_atr,
        "n_1h_count_diff": n_1h, "n_4h_count_diff": n_4h,
        "definition": ("A = 连续运行到 Tr；B = 在 Tr 现场重建。两者重放地板相同（锚点），"
                       "读到的行情相同 ⇒ 任何差异都只能来自「bootstrap 一次性回放」与"
                       "「逐根 step 推进」这两条路径本身。"),
        "detail": detail,
        "verdict": ("PASS（重启动作不改变任何币的有效状态）" if n_diff == 0
                    else f"FAIL（{n_diff} 个币在重启动作后状态不同，必须逐项解释）"),
    }
    cm.dump(f"18_重启动作效应_{tag}.json", out)
    cm.dump_csv(f"18_逐币重启效应_{tag}.csv", rows)
    print(f"[verdict] {out['verdict']}")
    print(f"  ATR 不同 {n_atr} | 1h 处理根数不同 {n_1h} | 4h 处理根数不同 {n_4h}")
    for s, r in detail.items():
        if not r["same"]:
            print(f"    {s:14s} 字段={r['diverged_fields']} ATR同={r['atr_same']} "
                  f"1h {r['n_1h_A']}→{r['n_1h_B']} 4h {r['n_4h_A']}→{r['n_4h_B']} "
                  f"分歧@ {r.get('first_div_transition_utc')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

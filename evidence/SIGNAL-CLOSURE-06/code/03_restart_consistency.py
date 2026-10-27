# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜03 真正的重启一致性（共同起点 + 共同截止）。

工单第 2 条。ZERO-CAUSE-05 的 S1/S3 是「不同时刻各自全新启动」，历史长度天然不同，
那不是生产重启。生产的重启是：

    同一实例 bootstrap@T0 → 连续跑到 Tr → **按 [Tr-W, Tr] 重建** → 跑到 T1

本脚本固定 `T0` 与 `T1`，只改「最后一次重启发生在什么时候」：

  C        持续运行        bootstrap@T0 ─────────────────────→ T1
  R{k}h    截止前 k 小时重启  bootstrap@T0 ─→ Tr ⤴重建 ───────→ T1
  R2x      两次重启        bootstrap@T0 → Tr1 ⤴ → Tr2 ⤴ ────→ T1

**一个必须先说清的结构事实**：重启会把内存状态整块丢弃，终态只由**最后一次**重启之后的
重放决定。所以「重启几次」本身不产生额外影响，真正的影响变量是**最后一次重启离截止多远**
（越近 ⇒ 覆盖历史越短 ⇒ 越可能丢状态）。`R2x` 就是用来验证这一点的：它的终态必须
与「只重启在 Tr2 的那一档」逐币完全相同。

**本版相对上一版的两处更正**：
  ① 跑生产类 `MultiSymbolSignalEngine`（`00_common.prod_session`），不是手搭引擎；
  ② `R{k}h` 不再用「只 bootstrap@Tr」的等价捷径冒充重启 —— 真跑两段：
     第一段 bootstrap@T0 跑到 Tr，**把引擎整个丢掉**，再新建一个在 Tr 重建。
     `R2x` 真跑三段。等价性另外单独校验（`phase1_irrelevant`）。

用法：
  python code/03_restart_consistency.py --depth 1160
  python code/03_restart_consistency.py --depth 2880
  python code/03_restart_consistency.py --depth 2880 --genuine R6h,R2x
产出：out/03_重启一致性_D{depth}.json、out/03_逐币重启对照_D{depth}.csv
"""
from __future__ import annotations

import gc
import importlib
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

H1 = cm.H1
#: 最后一次重启距截止的小时数（越小 = 覆盖历史越短 = 越容易丢状态）
RESTART_OFFSETS_H = [12, 6, 3, 1]

DIFF_KEYS = ["state", "box_formed_ms", "box_high", "box_low", "sweep_time_ms",
             "retest_time_ms", "cooling_until_ms"]


def main() -> int:
    argv = sys.argv[1:]
    upto = None
    depth = None
    t0_hours = 72
    syms_arg = None
    offsets = RESTART_OFFSETS_H
    genuine = {"R6h", "R2x"}
    anchor_h = None
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto = cm.parse_utc_ms(argv[i + 1])
        if a == "--depth" and i + 1 < len(argv):
            depth = int(argv[i + 1])
        if a == "--t0-hours" and i + 1 < len(argv):
            t0_hours = int(argv[i + 1])
        if a == "--symbols" and i + 1 < len(argv):
            syms_arg = argv[i + 1].split(",")
        if a == "--offsets" and i + 1 < len(argv):
            offsets = [int(x) for x in argv[i + 1].split(",")]
        if a == "--genuine" and i + 1 < len(argv):
            genuine = {x.strip() for x in argv[i + 1].split(",") if x.strip()}
        if a == "--anchor-hours" and i + 1 < len(argv):
            anchor_h = int(argv[i + 1])
    if upto is None:
        upto = cm.last_closed_1h_close_ms()
    W_h = int(depth) if depth else int(cm.warmup_ms() / H1)
    W = W_h * H1
    syms = syms_arg or cm.universe()[0]
    tag = f"D{W_h}" + (f"_anchor{anchor_h}" if anchor_h else "")
    T1 = upto
    T0 = T1 - t0_hours * H1
    anchor_ms = (T1 - anchor_h * H1) if anchor_h else None
    print(f"[cfg] upto={cm.utc(T1)} depth={W_h}h symbols={len(syms)} "
          f"T0={cm.utc(T0)} genuine={sorted(genuine)} "
          f"anchor={cm.utc(anchor_ms) if anchor_ms else '无（相对窗口）'}")

    t0 = time.time()
    keep_h = max(W_h, anchor_h or 0)
    # 读入天数按实际保留深度裁（不再固定 190 天）：1h 只保留 keep_h 小时，
    # 多读的天数纯属浪费，而在 2880h 深度下正是它把进程顶到 OOM。
    read_days = int(keep_h / 24) + 10
    # **分批**：全池 526 币 × 120 天 1h 同进程会被 OOM 杀掉（实测 exit 137）。
    # 每批独立建 hist、跑完即释放，最后按币合并快照 —— 比对口径不变（仍是同一份
    # 行情、同一 T0/T1、同一锚点），只是把内存峰值切成几段。
    BATCH = int(os.environ.get("WX_BATCH_SYMBOLS", "130"))
    batches = [syms[i:i + BATCH] for i in range(0, len(syms), BATCH)]
    print(f"[cfg] read_days={read_days} batches={len(batches)}×~{BATCH}")

    res = {k: {} for k in ["C"] + [f"R{k}h" for k in offsets] + ["R2x"]}
    timings, restart_change = {}, {}

    for bi, bs in enumerate(batches, 1):
        tb = time.time()
        hist, _ = cm.load_hist(bs, upto, read_days=read_days,
                               keep_from_ms=int(upto) - keep_h * H1)

        def sess(init_ms, end_ms, _h=hist, _s=bs, _force=False):
            return cm.prod_session(_s, _h, init_ms, end_ms, replay_hours=W_h,
                                   advance="1h", anchor_ms=anchor_ms,
                                   force_step=_force)

        def _merge(key, snap, dt):
            res[key].update(snap)
            timings[key] = round(timings.get(key, 0.0) + dt, 1)

        # ── C：持续运行 ────────────────────────────────────────────────────
        t = time.time()
        _merge("C", cm.pool_snapshot(sess(T0, T1)), time.time() - t)

        # ── R{k}h：真两段（第一段结果丢弃）────────────────────────────────
        for k in offsets:
            key = f"R{k}h"
            Tr = T1 - k * H1
            t = time.time()
            if key in genuine:
                sess_eng = sess(T0, Tr)                  # 第一段：跑完就丢
                snap1 = cm.pool_snapshot(sess_eng)
                # **重启本身改变了多少状态**的直接度量：同一时刻 Tr，
                # 一路从 T0 连续跑来的终态 vs 在 Tr 现场重建的终态。
                # `sess(Tr, Tr)` 必须带 `force_step=True`：否则 bootstrap 之后
                # 最后几根 1h 没喂进去，两边差 3 根 1h，这个数字会虚高。
                eng_d = sess(Tr, Tr, _force=True)
                direct = cm.pool_snapshot(eng_d)
                # 自证：两边 1h 处理根数必须相等，否则「0 差异」可能只是
                # 因为两边都没处理到那些 1h（空转）。
                # 属性在**状态机**上，叫 `sm.processed_1h_bar_opens`
                # （引擎上另有一个 `processed_1h_opens`；写错层级会永远读到 0，
                #  自证就变成摆设 —— 这里踩过一次）。
                n1h_c = [len(getattr(e.sm, "processed_1h_bar_opens", set()) or set())
                         for e in sess_eng.engines.values()]
                n1h_d = [len(getattr(e.sm, "processed_1h_bar_opens", set()) or set())
                         for e in eng_d.engines.values()]
                rc = restart_change.setdefault(key, {"n_symbols_compared": 0,
                                                     "n_diff_phase1_vs_direct": 0})
                rc["n_symbols_compared"] += len(bs)
                rc["n_diff_phase1_vs_direct"] += sum(
                    1 for s in bs
                    if cm.diff_key(snap1.get(s) or {}) != cm.diff_key(direct.get(s) or {}))
                rc.setdefault("n_1h_processed_min_continuous", 10 ** 9)
                rc.setdefault("n_1h_processed_min_direct", 10 ** 9)
                if n1h_c:
                    rc["n_1h_processed_min_continuous"] = min(
                        rc["n_1h_processed_min_continuous"], min(n1h_c))
                if n1h_d:
                    rc["n_1h_processed_min_direct"] = min(
                        rc["n_1h_processed_min_direct"], min(n1h_d))
                rc["n_1h_equal"] = (sorted(n1h_c) == sorted(n1h_d))
            _merge(key, cm.pool_snapshot(sess(Tr, T1)), time.time() - t)

        # ── R2x：真三段（两次重启，最后一次也在 6h 前 ⇒ 必须与 R6h 逐币相同）
        Tr1, Tr2 = T1 - 12 * H1, T1 - 6 * H1
        t = time.time()
        cm.pool_snapshot(sess(T0, Tr1))                   # 第 1 段，丢弃
        cm.pool_snapshot(sess(Tr1, Tr2))                  # 第 2 段，丢弃
        _merge("R2x", cm.pool_snapshot(sess(Tr2, T1)), time.time() - t)

        print(f"  [批 {bi}/{len(batches)}] {len(bs)} 币 {time.time()-tb:.1f}s")
        del hist
        gc.collect()

    print(f"[load+run] {time.time()-t0:.1f}s  场景耗时 " +
          ", ".join(f"{k}={v}s" for k, v in timings.items()))

    # ── 比较 ──────────────────────────────────────────────────────────────
    diffs, rows, cand_log_diff = {}, [], {}
    for k in res:
        if k == "C":
            continue
        d = []
        n_log = 0
        for sym in syms:
            a, b = res["C"].get(sym) or {}, res[k].get(sym) or {}
            if a.get("candidate_ids") != b.get("candidate_ids"):
                n_log += 1
            ch = [f for f in DIFF_KEYS if a.get(f) != b.get(f)]
            cids = a.get("candidate_ids") != b.get("candidate_ids")
            if ch:
                d.append({"symbol": sym, "fields": ch, "candidate_ids_differ": cids,
                          "C": {f: a.get(f) for f in ch},
                          "other": {f: b.get(f) for f in ch},
                          "C_box_utc": cm.utc(a.get("box_formed_ms")),
                          "other_box_utc": cm.utc(b.get("box_formed_ms")),
                          "C_candidates": a.get("candidate_ids"),
                          "other_candidates": b.get("candidate_ids")})
        diffs[k] = d
        cand_log_diff[k] = n_log

    count_irrelevant = ("R2x" in res and "R6h" in res
                        and all(cm.diff_key(res["R2x"].get(s) or {})
                                == cm.diff_key(res["R6h"].get(s) or {})
                                for s in syms))

    for sym in syms:
        row = {"symbol": sym}
        for k in res:
            s = res[k].get(sym) or {}
            row[f"{k}_state"] = s.get("state")
            row[f"{k}_box_utc"] = cm.utc(s.get("box_formed_ms"))
            row[f"{k}_n_cand"] = s.get("n_candidates")
            row[f"{k}_n_trans"] = s.get("n_transitions")
        row["any_restart_diff"] = any(
            cm.diff_key(res["C"].get(sym) or {}) != cm.diff_key(res[k].get(sym) or {})
            for k in res if k != "C")
        rows.append(row)

    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "depth_hours": W_h,
        "depth_mode": tag,
        "T0_utc": cm.utc(T0), "T1_utc": cm.utc(T1), "t0_hours": t0_hours,
        "restart_offsets_h": offsets,
        "genuine_two_phase": sorted(genuine),
        "n_symbols": len(syms),
        "n_ready": {k: len(v) for k, v in res.items()},
        "n_diff_vs_continuous": {k: len(v) for k, v in diffs.items()},
        "n_candidate_log_diff": cand_log_diff,
        "candidate_log_note": ("候选日志（candidate_ids）按构造随「有效重放深度」增长："
                               "C 的有效深度 = W + t0_hours，R{k}h = W + k。"
                               "**它单独统计，不并入有效状态差异**；设了绝对锚点后"
                               "两者深度完全相同，这一项也必须为 0。"),
        "anchor_utc": cm.utc(anchor_ms),
        "anchor_hours": anchor_h,
        "restart_count_irrelevant": count_irrelevant,
        "restart_state_change_at_Tr": restart_change,
        "total_candidates": {k: sum((res[k].get(s) or {}).get("n_candidates", 0)
                                    for s in syms) for k in res},
        "state_distribution": {
            k: {st: sum(1 for s in syms if (res[k].get(s) or {}).get("state") == st)
                for st in sorted({(res[k].get(s) or {}).get("state") for s in syms})}
            for k in res},
        "elapsed_sec_per_scenario": timings,
        "diff_detail": {k: v[:80] for k, v in diffs.items()},
        "runner": "MultiSymbolSignalEngine（生产类）via 00_common.prod_session",
        "definition": ("重启 = 丢弃内存状态，按 [重启时刻-W, 重启时刻] 重建后继续。"
                       "C 与各 R 共用同一 T0、同一 T1，只改最后一次重启的时刻 —— "
                       "这是生产重启的真实方向（重启让已覆盖历史变短，而不是变长）。"),
    }
    cm.dump(f"03_重启一致性_{tag}.json", out)
    cm.dump_csv(f"03_逐币重启对照_{tag}.csv", rows)
    print("  候选: " + " / ".join(f"{k}={out['total_candidates'][k]}" for k in res))
    print("  C vs 各重启点差异: " +
          ", ".join(f"{k}={len(v)}" for k, v in diffs.items()))
    print(f"  重启次数无关（R2x == R6h）: {count_irrelevant}")
    if restart_change:
        print(f"  重启本身改变的状态（T0 连续跑到 Tr vs 在 Tr 现场重建）: {restart_change}")
    for k in diffs:
        for d in diffs[k][:4]:
            print(f"    {d['symbol']:14s} {k:6s} fields={d['fields']} "
                  f"C={d['C_box_utc']} other={d['other_box_utc']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

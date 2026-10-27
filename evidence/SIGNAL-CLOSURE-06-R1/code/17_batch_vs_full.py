# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜17 分批 vs 不分批（把「币池被切小」这个变量单独拎出来）。

## 背景

  · 16 号已证：**全池 526 一次跑** 与 **6 币子集** 的逐币转移序列**完全一致**
    （72 步全同、闸门跳过都 1 次）⇒ 币池规模本身不影响推进。
  · 但 11 号（526 全池）给出的这 6 个币的转移数是 31/25/31/34/42/34，
    而不分批的 16 号给的是 35/26/28/36/38/32 —— **同一份代码、同一参数、同一行情**。
  · 唯一剩下的差别是：06 的 `03_restart_consistency.py` 把全池**切成 130 币一批**
    （`WX_BATCH_SYMBOLS=130`，注释说是为了防 OOM）。

`step()` 的闸门用的是 `watcher.global_last_open("1h"/"4h")`，即**当前币池**的最大水位。
分批后每批只有 130 币 ⇒ 水位由这 130 币决定 ⇒ 若某批的币在某根 1h 上集体缺数据，
`g1` 不前进 ⇒ `stale` 成立 ⇒ 在 `catchup_interval_sec`（真实挂钟 60s）内**整轮静默跳过**。

也就是说：**分批不只是「内存技巧」，它会改变状态推进**。而工单明确禁止
「通过挑选重放深度或缩小币池掩盖差异」—— 分批等价于把币池切小。

## 本脚本做什么

对同一组目标币，跑 4 种组合，只跑 C（T0→T1），逐币记录转移数 / 候选数：

  A `full_once__catchup60`   全池一次，闸门默认（= 16 号口径）
  B `full_once__catchup0`    全池一次，`catchup_interval_sec=0`（与挂钟解耦）
  C `batch130__catchup60`    130 币一批，闸门默认（= 11 号/06 的 03 口径）
  D `batch130__catchup0`     130 币一批，闸门解耦

判读：
  · 若 C 复现 11 号的 31/25/31/34/42/34 ⇒ **分批就是元凶**，11 号的全池结论作废；
  · 若 B == A 且 D == B ⇒ 把闸门与挂钟解耦后，分批无害，可放心并行/分批；
  · 若 D != B ⇒ 分批另有影响，必须禁止分批。

用法：python code/17_batch_vs_full.py --upto 2026-10-25T04:59:59Z
产出：out/17_分批对比.json
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
sys.path.insert(0, str(C06 / "code"))
sys.path.insert(0, str(HERE))
cm = importlib.import_module("00_common")
cm.OUT = OUT

# 复用 16 号的会话构造（同一条生产路径，只是把 step 包一层记闸门状态）
_m16 = importlib.import_module("16_step_gate_diagnosis")
H1 = cm.H1
TARGETS = _m16.TARGETS


def main() -> int:
    argv = sys.argv[1:]
    upto_s = "2026-10-25T04:59:59Z"
    depth_h, t0_h, batch = 2880, 72, 130
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto_s = argv[i + 1]
        if a == "--depth" and i + 1 < len(argv):
            depth_h = int(argv[i + 1])
        if a == "--t0-hours" and i + 1 < len(argv):
            t0_h = int(argv[i + 1])
        if a == "--batch" and i + 1 < len(argv):
            batch = int(argv[i + 1])

    T1 = cm.parse_utc_ms(upto_s)
    T0 = T1 - t0_h * H1
    anchor_ms = T1 - depth_h * H1
    keep_from = T1 - depth_h * H1
    read_days = int(depth_h / 24) + 10
    allsyms, _ = cm.universe()
    print(f"[cfg] upto={cm.utc(T1)} T0={cm.utc(T0)} depth={depth_h}h anchor={cm.utc(anchor_ms)} "
          f"read_days={read_days} 全池={len(allsyms)} batch={batch}")

    results = {}

    def record(tag, groups, catchup):
        """groups: [[syms...], ...]；每批独立建 hist，跑完即释放。"""
        agg, n_skip, n_cand = {}, 0, 0
        for bs in groups:
            hist, _ = cm.load_hist(bs, T1, read_days=read_days, keep_from_ms=keep_from)
            if catchup is not None:
                # 把 catchup 传进 run_c 需要临时改默认；这里用 monkey-patch 的方式：
                # run_c 内部在建好引擎后读 `_m16` 的模块级默认，故改为在包装层设置。
                r = _m16.run_c(bs, hist, T0, T1, depth_h, anchor_ms, TARGETS,
                               catchup_sec=catchup)
            else:
                r = _m16.run_c(bs, hist, T0, T1, depth_h, anchor_ms, TARGETS)
            n_skip += r["n_gate_skipped"]
            for s, sn in r["snapshot"].items():
                agg[s] = {"n_transitions": sn["n_transitions"],
                          "n_candidates": sn["n_candidates"], "state": sn["state"]}
            n_cand += sum(v["n_candidates"] for v in r["snapshot"].values())
            del hist
        results[tag] = {"per_symbol": agg, "n_gate_skipped": n_skip,
                        "n_candidates_total_on_targets": n_cand,
                        "n_groups": len(groups), "catchup_sec": catchup}
        print(f"  [{tag}] 跳过={n_skip} 目标币候选合计={n_cand} | " +
              " ".join(f"{s[:8]}:{agg.get(s,{}).get('n_transitions')}" for s in TARGETS))

    full = [list(allsyms)]
    groups = [list(allsyms)[i:i + batch] for i in range(0, len(allsyms), batch)]

    print("[run] A 全池一次 / 闸门默认")
    record("A_full_once__catchup60", full, None)
    print("[run] B 全池一次 / 闸门与挂钟解耦")
    record("B_full_once__catchup0", full, 0.0)
    print(f"[run] C 分批 {batch} / 闸门默认")
    record("C_batch__catchup60", groups, None)
    print(f"[run] D 分批 {batch} / 闸门解耦")
    record("D_batch__catchup0", groups, 0.0)

    def key(tag):
        return tuple(results[tag]["per_symbol"].get(s, {}).get("n_transitions") for s in TARGETS)

    a, b, c, d = key("A_full_once__catchup60"), key("B_full_once__catchup0"), \
        key("C_batch__catchup60"), key("D_batch__catchup0")
    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(T1), "T0_utc": cm.utc(T0),
        "depth_hours": depth_h, "anchor_utc": cm.utc(anchor_ms),
        "read_days": read_days, "batch_size": batch, "targets": TARGETS,
        "transitions_by_scenario": {"A_full_once__catchup60": a, "B_full_once__catchup0": b,
                                    "C_batch__catchup60": c, "D_batch__catchup0": d},
        "results": results,
        "A_eq_B": a == b, "A_eq_C": a == c, "A_eq_D": a == d,
        "verdict": {
            "batch_changes_result": a != c,
            "catchup_clock_affects_result": (a != b) or (c != d),
            "conclusion": (
                ("分批改变状态推进 ⇒ 11 号（分批）的全池结论不可用，必须不分批重跑；"
                 if a != c else "分批不改变结果；")
                + ("闸门与真实挂钟耦合会改变结果 ⇒ 离线回放必须把 catchup 设为 0；"
                   if ((a != b) or (c != d)) else "闸门与挂钟解耦与否不影响结果。")),
        },
    }
    cm.dump("17_分批对比.json", out)
    print(f"[verdict] A==B {a==b} | A==C {a==c} | A==D {a==d}")
    print(f"  A={a}")
    print(f"  B={b}")
    print(f"  C={c}")
    print(f"  D={d}")
    print(f"  {out['verdict']['conclusion']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

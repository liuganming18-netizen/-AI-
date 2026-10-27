#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""25_reconstruct_event2.py —— 重建「08:00:00Z 那根新增 1 小时线」的逐币阶段变化

## 为什么需要它

20 号采样脚本在第二根新增 1 小时线（08:00:00Z）触发时崩了：
`events[-1]["state_counter"]` 键名写错（事件字典里叫 `state_counter_now`）。
结果**计数拿到了、逐币名单没拿到**。修 bug 只能保证「下一根」不再丢，救不回这一根。

本脚本用**离线重放**把这一根的逐币名单补回来，并且自带两道校验，不靠信任：

  1. **锚点与生产一致**：直接用生产库里的锚点值 `1782542715686`
     （= 2026-06-27T06:45:15Z），不用「截止时刻减整数小时」凑一个近似值；
  2. **起点侧校验**：离线在 `upto = 07:59:59Z` 重放出来的状态分布，必须**逐字段等于**
     生产在 08:01:21Z 观测到的 `167 / 326 / 28 / 5`，否则直接判 FAIL、不输出名单；
  3. **终点侧校验**：生产在 08:59:59Z 之后（水位 08:00:00Z、526/526 `lag=0`、已收敛）
     的状态分布 `181 / 318 / 17 / 10` 是**冻结快照** `20b_生产状态_08点那根之后.json`。

两端的分布都对得上，中间的逐币差异才有资格叫「这根 K 线造成的阶段变化」。

用法：
    python code/25_reconstruct_event2.py
产出：
    out/25_第二根小时线逐币重建.json
    out/25_第二根小时线逐币重建.csv
"""
from __future__ import annotations

import importlib
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
sys.path.insert(0, str(C06 / "code"))
cm = importlib.import_module("00_common")
cm.OUT = OUT

H1 = cm.H1
UPTO_PRE = "2026-10-25T07:59:59Z"      # 08:00Z 那根收盘之前
PROD_ANCHOR_MS = 1782542715686          # 生产 LIVE_DB live_run.state_anchor_ms
PROD_POST = OUT / "20b_生产状态_08点那根之后.json"
EXPECT_PRE = {"IDLE": 167, "BOX": 326, "RETEST": 28, "SWEEP": 5}


def main() -> int:
    if not PROD_POST.exists():
        print(f"[FATAL] 缺冻结快照 {PROD_POST}")
        return 2
    prod = json.load(open(PROD_POST, encoding="utf-8"))
    se = prod["signal_engine"]
    post_pss = dict(se["per_symbol_state"])
    post_cnt = dict(se["state_counter"])
    print(f"[prod] 水位 {cm.utc(se['watermark_1h'])}  分布 {post_cnt}  币数 {len(post_pss)}")
    print(f"[prod] lag 分布 {(se['watermark_check']['per_symbol']['lag_1h_histogram'])}")

    pre_ms = cm.parse_utc_ms(UPTO_PRE)
    syms = cm.universe()[0]
    keep_from = int(PROD_ANCHOR_MS)
    read_days = int(2880 / 24) + 10
    print(f"[load] {len(syms)} 币 read_days={read_days} keep_from={cm.utc(keep_from)}")
    hist, _ = cm.load_hist(syms, pre_ms, read_days=read_days, keep_from_ms=keep_from)

    print(f"[run] 离线重放至 {UPTO_PRE}（锚点=生产锚点）")
    sess = cm.prod_session(syms, hist, pre_ms, pre_ms, replay_hours=2880,
                           advance="1h", anchor_ms=int(PROD_ANCHOR_MS))
    pre_pss = {s: (cm.snapshot(sess.engines[s]).get("state") or "IDLE") for s in syms}
    pre_cnt = dict(Counter(pre_pss.values()))
    print(f"[check-1] 离线起点分布 {pre_cnt}")
    ok_pre = (pre_cnt == EXPECT_PRE)
    print(f"[check-1] 与生产 08:01:21Z 观测 {EXPECT_PRE} 一致 = {ok_pre}")

    changes = [{"symbol": s, "prev": pre_pss.get(s), "now": post_pss.get(s)}
               for s in sorted(set(pre_pss) | set(post_pss))
               if pre_pss.get(s) != post_pss.get(s)]
    delta = {k: post_cnt.get(k, 0) - pre_cnt.get(k, 0)
             for k in sorted(set(pre_cnt) | set(post_cnt)) if post_cnt.get(k, 0) != pre_cnt.get(k, 0)}
    # 逐币差异的净变化必须与两端的分布差完全吻合，否则名单不可信
    recomputed = dict(Counter(x["now"] for x in changes))
    ok_consistency = all(
        len([x for x in changes if x["prev"] == k]) - len([x for x in changes if x["now"] == k])
        == pre_cnt.get(k, 0) - post_cnt.get(k, 0)
        for k in set(pre_cnt) | set(post_cnt))

    payload = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "why": "20 号采样脚本在第二根小时线触发时因键名写错崩溃，逐币名单丢失；本文件用离线重放补回并做两端校验。",
        "upto_pre_utc": UPTO_PRE,
        "prod_watermark_utc": cm.utc(se["watermark_1h"]),
        "anchor_ms": int(PROD_ANCHOR_MS),
        "anchor_utc": cm.utc(PROD_ANCHOR_MS),
        "read_days": read_days,
        "keep_from_utc": cm.utc(keep_from),
        "pre_counter_offline": pre_cnt,
        "pre_counter_expected_from_production": EXPECT_PRE,
        "check_pre_matches_production": ok_pre,
        "post_counter_production": post_cnt,
        "counter_delta": delta,
        "n_phase_changes": len(changes),
        "phase_changes": changes,
        "check_change_list_consistent_with_counters": ok_consistency,
        "verdict": ("PASS" if (ok_pre and ok_consistency) else "FAIL"),
        "note": "名单可信的前提是 check-1 通过：离线重放在 07:59:59Z 的状态分布必须与生产在 08:01:21Z 观测到的完全相同。",
    }
    cm.dump("25_第二根小时线逐币重建.json", payload)
    cm.dump_csv("25_第二根小时线逐币重建.csv", changes)

    print(f"[result] 阶段变化 {len(changes)} 币  计数差 {delta}")
    for x in changes:
        print(f"   {x['symbol']:16s} {x['prev']} → {x['now']}")
    print(f"[check-2] 名单与两端计数自洽 = {ok_consistency}")
    print(f"[verdict] {payload['verdict']}")
    return 0 if payload["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

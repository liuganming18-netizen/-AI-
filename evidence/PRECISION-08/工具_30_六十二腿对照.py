#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-08 工具 30：62 条成交腿的 A / B 候选方案对照表。

三个候选判定（全部在**本进程内**实现，生产代码零改动）：

  A      : `low ≤ fill ≤ high`（闭区间，零容差）
  B(k=0) : `over ≤ raw × slip`（取整**不**占容差；= P06 的「容差=滑点允许量」）
  B(k=1) : `over ≤ raw × slip + step`（取整占一档；= P07 实测「27 条全放行」）

另外回答三个只有做过才敢说的量：
  ① 27 条越界腿里，R5 / R6 / R7 各能覆盖多少（是否真的「R7 只是重复 R6」）；
  ② 滑点从 3bps 加倍到 6bps（variant C 成本压力测试）时，A 的拒绝数怎么变
     —— 如果拒绝数随滑点参数上升，A 就把「保守成本假设」判成了「坏成交」；
  ③ 27 条腿落在多少个 run 上（= 采纳 A 会让多少 run 失效）。

输出：表2_62条腿_候选方案对照.csv / 六十二腿对照.json / 六十二腿对照原始输出.txt
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal
from typing import Any, Dict, List

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
P07 = os.path.join(ROOT, "evidence", "PRECISION-07")

D = Decimal
SLIP_BASE = D("0.0003")      # config.py:87   3 bps
SLIP_STRESS = D("0.0006")    # config.py:91   6 bps（variant C）


def fill_of(raw: D, step: D, is_buy: bool, slip: D):
    if is_buy:
        ps = raw * (D(1) + slip)
        pr = (ps / step).to_integral_value(rounding="ROUND_CEILING") * step
    else:
        ps = raw * (D(1) - slip)
        pr = (ps / step).to_integral_value(rounding="ROUND_FLOOR") * step
    return ps, pr


def over_of(fill: D, lo: D, hi: D, is_buy: bool) -> D:
    return max(D(0), fill - hi) if is_buy else max(D(0), lo - fill)


def judge(over: D, raw: D, step: D, slip: D, k) -> bool:
    """True=放行。k=None ⇒ 方案 A（零容差）。"""
    if k is None:
        return over == 0
    return over <= raw * slip + step * D(k)


def main() -> int:
    df = pd.read_csv(os.path.join(P07, "表2_62条腿分类.csv"), encoding="utf-8-sig")
    rows: List[Dict[str, Any]] = []

    for _, r in df.iterrows():
        raw, step = D(str(r["参考价raw"])), D(str(r["步长"]))
        lo, hi = D(str(r["K线L"])), D(str(r["K线H"]))
        rec = D(str(r["记录成交价"]))
        leg = str(r["腿"])

        # 方向：以「哪一侧能复现记录价」为准（float 尾差 1e-9 内视作复现）
        cands = []
        for is_buy in (True, False):
            _, pr = fill_of(raw, step, is_buy, SLIP_BASE)
            if pr == rec or abs(pr - rec) <= D("1e-9"):
                cands.append(is_buy)
        if len(cands) == 1:
            is_buy = cands[0]
        elif len(cands) == 2:
            is_buy = str(r["越界方向"]) == "above_high" or leg == "入场"
        else:
            is_buy = leg == "入场"

        ps, pr = fill_of(raw, step, is_buy, SLIP_BASE)
        over = over_of(pr, lo, hi, is_buy)
        slip_over = over_of(ps, lo, hi, is_buy)
        round_over = over - slip_over

        # 6bps 压力测试下的同一腿
        ps6, pr6 = fill_of(raw, step, is_buy, SLIP_STRESS)
        over6 = over_of(pr6, lo, hi, is_buy)

        # 闸门归属（谁先命中）
        gate = "—"
        if pr <= 0:
            gate = "R5 取整后≤0"
        elif step > raw:
            gate = "R6 step>raw"
        elif over > 0:
            gate = "仅 R7"
        reason = ("R5" if pr <= 0 else "R6" if step > raw
                  else "R7" if over > 0 else "—")

        rows.append({
            "标的": r["标的"], "run_id": r["run_id"], "腿": leg, "reason": r["reason"],
            "方向": "买" if is_buy else "卖",
            "raw": str(raw), "step": str(step), "记录成交价": str(rec),
            "复算成交价": str(pr), "low": str(lo), "high": str(hi),
            "越界": str(over),
            "越界%": (float(over / raw) * 100.0 if raw else 0.0),
            "滑点段越界": str(slip_over), "取整段越界": str(round_over),
            "取整/滑点位移倍数": (float((pr - ps) / (ps - raw))
                                  if (ps - raw) != 0 else None),
            "方案A": "允许" if judge(over, raw, step, SLIP_BASE, None) else "拒绝",
            "方案B_k0": "允许" if judge(over, raw, step, SLIP_BASE, 0) else "拒绝",
            "方案B_k1": "允许" if judge(over, raw, step, SLIP_BASE, 1) else "拒绝",
            "6bps_越界": str(over6),
            "6bps_方案A": "允许" if over6 == 0 else "拒绝",
            "闸门归属": gate, "谁先拦": reason,
        })

    # ── 滑点敏感度：A 的拒绝数随滑点参数怎么变 ──
    # 注意：方向已在 3bps 下判定完毕（见上方 is_buy），这里只改滑点重算越界，
    # 不再重新匹配记录价（记录价是按 3bps 落盘的）。
    sens = {}
    for bps in (3, 5, 6, 10):
        slip = D(bps) / D(10000)
        n_out = 0
        new_legs = []
        for _, r in df.iterrows():
            raw, step = D(str(r["参考价raw"])), D(str(r["步长"]))
            lo, hi = D(str(r["K线L"])), D(str(r["K线H"]))
            rec = D(str(r["记录成交价"]))
            leg = str(r["腿"])
            cs = [b for b in (True, False)
                  if abs(fill_of(raw, step, b, SLIP_BASE)[1] - rec) <= D("1e-9")]
            is_buy = (cs[0] if len(cs) == 1
                      else (leg == "入场" if len(cs) == 2 else leg == "入场"))
            _, pr_b = fill_of(raw, step, is_buy, slip)
            if over_of(pr_b, lo, hi, is_buy) > 0:
                n_out += 1
                if over_of(fill_of(raw, step, is_buy, SLIP_BASE)[1], lo, hi, is_buy) == 0:
                    new_legs.append(f"{r['标的']}/{leg}")
        sens[f"{bps}bps"] = {"reject": n_out, "allow": len(df) - n_out,
                             "newly_rejected_vs_3bps": new_legs}

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(HERE, "表2_62条腿_候选方案对照.csv"),
               index=False, encoding="utf-8-sig")

    n = len(out)
    cnt = lambda col, v: int((out[col] == v).sum())                       # noqa: E731
    rej_a = out[out["方案A"] == "拒绝"]
    rej_b0 = out[out["方案B_k0"] == "拒绝"]
    rej_b1 = out[out["方案B_k1"] == "拒绝"]
    runs_a = sorted(set(rej_a["run_id"]))
    runs_all = sorted(set(out["run_id"]))

    gate_counts = out["谁先拦"].value_counts().to_dict()
    # 6bps 下 A 的拒绝数（只统计 3bps 时还在区间内的腿，看「压力测试是否被规则吃掉」）
    inside3 = out[out["方案A"] == "允许"]
    inside3_6 = inside3[inside3["6bps_方案A"] == "拒绝"]

    doc = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "slip_base": str(SLIP_BASE), "slip_stress": str(SLIP_STRESS),
        "legs_total": n,
        "A": {"reject": len(rej_a), "allow": n - len(rej_a),
              "runs_affected": len(runs_a), "runs_total": len(runs_all)},
        "B_k0": {"reject": len(rej_b0), "allow": n - len(rej_b0)},
        "B_k1": {"reject": len(rej_b1), "allow": n - len(rej_b1)},
        "gate_owner": gate_counts,
        "diff_A_vs_B0": int((out["方案A"] != out["方案B_k0"]).sum()),
        "diff_A_vs_B1": int((out["方案A"] != out["方案B_k1"]).sum()),
        "diff_B0_vs_B1": int((out["方案B_k0"] != out["方案B_k1"]).sum()),
        "slippage_sensitivity": sens,
        "stress_6bps": {
            "A_reject_at_6bps": cnt("6bps_方案A", "拒绝"),
            "extra_rejects_from_3_to_6bps": len(inside3_6),
            "extra_list": [f"{x['标的']}/{x['腿']}" for _, x in inside3_6.iterrows()],
        },
        "notional_unavailable": "62 腿表无 qty ⇒ 只能给价格级失真，给不出名义额加权",
    }
    with open(os.path.join(HERE, "六十二腿对照.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    L = ["PRECISION-08 / 工具 30 · 62 条成交腿的候选方案对照", "=" * 78,
         f"滑点基准 {SLIP_BASE}（3bps）；压力 {SLIP_STRESS}（6bps，variant C）",
         "",
         f"{'方案':<14}{'允许':>6}{'拒绝':>6}",
         f"{'A 零容差':<14}{n-len(rej_a):>6}{len(rej_a):>6}",
         f"{'B k=0（取整不占容差）':<14}{n-len(rej_b0):>6}{len(rej_b0):>6}",
         f"{'B k=1（取整占一档）':<14}{n-len(rej_b1):>6}{len(rej_b1):>6}",
         "",
         f"A 与 B(k=0) 判定不同的腿：{doc['diff_A_vs_B0']} 条",
         f"A 与 B(k=1) 判定不同的腿：{doc['diff_A_vs_B1']} 条",
         f"B(k=0) 与 B(k=1) 不同的腿：{doc['diff_B0_vs_B1']} 条",
         "",
         "闸门归属（谁先命中，互斥）："]
    for k, v in sorted(gate_counts.items()):
        L.append(f"  {k:<14}{v:>4} 条")
    L += ["",
          f"采纳 A 会让 {len(runs_a)} / {len(runs_all)} 个 run 失效（该 run 至少一条腿被拒）",
          "",
          "滑点敏感度（方案 A 的拒绝数随滑点参数变化）："]
    for k, v in sens.items():
        L.append(f"  {k:<6} 拒绝 {v['reject']:>2} / 62"
                 + (f"   新变拒绝：{v['newly_rejected_vs_3bps']}"
                    if v["newly_rejected_vs_3bps"] else ""))
    L += ["",
          "6bps 成本压力测试（variant C）下的方案 A：",
          f"  3bps 时拒绝 {len(rej_a)} 条；6bps 时拒绝 {doc['stress_6bps']['A_reject_at_6bps']} 条",
          f"  仅因滑点从 3→6bps 而**新变**拒绝的腿：{len(inside3_6)} 条 "
          f"{doc['stress_6bps']['extra_list']}"]
    if inside3_6 is not None and len(inside3_6):
        L.append("  ⇒ 成本压力测试本身会触发「成交越界」拒绝：把滑点调保守，"
                 "规则就把成交判成非法。这是 A 的**结构性副作用**，与数据好坏无关。")
    L += ["", "逐腿（按越界金额降序）：", ""]
    L.append(f"  {'标的':<14}{'腿':<5}{'方向':<4}{'step':>9}{'raw':>12}"
             f"{'fill':>11}{'越界':>10}{'A':>5}{'B0':>5}{'B1':>5}{'6bps':>6}")
    for _, x in rej_a.sort_values("越界", key=lambda s: s.map(lambda v: -float(v))).iterrows():
        L.append(f"  {x['标的']:<14}{x['腿']:<5}{x['方向']:<4}{x['step']:>9}"
                 f"{x['raw']:>12}{x['复算成交价']:>11}{x['越界']:>10}"
                 f"{x['方案A']:>5}{x['方案B_k0']:>5}{x['方案B_k1']:>5}{x['6bps_方案A']:>6}")

    txt = "\n".join(L)
    with open(os.path.join(HERE, "六十二腿对照原始输出.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)

    ok = (n == 62 and len(rej_a) == 27
          and doc["diff_A_vs_B0"] == 0 and len(rej_b1) == 0)
    print(f"\n自检：62 腿 / A 拒 27 / A≡B(k=0) / B(k=1) 全放行 → "
          f"{'通过' if ok else '失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-08 工具 10：核查现有成交模型（只读，不改任何生产文件）。

回答工单「一、先核查现有模型」的五个问题，并把答案做成**可复算的事实**，
而不是叙述：

  ① raw_price 从哪来、是什么时间；
  ② 滑点是「成交价偏移」还是「独立成本」；
  ③ 取整方向及其依据；
  ④ K 线高低点与模拟成交的关系；
  ⑤ 现有实现 / 已明确约定 / 尚未决定，分别是什么。

关键复算（全部用 Decimal，不用 float 猜）：
  · 62 条腿里，参考价 raw 是否都落在 [low, high] 内；
  · 把滑点置 0（只留取整）、把取整置 0（只留滑点），分别有多少条腿越界
    —— 这一步回答「R7 到底在拦什么」；
  · 取整段 / 滑点段的倍数分布；
  · 步长 ≤ raw 的越界腿有多少（即 R6 抓不到、只有 R7 能抓的那些）。

输出：现有模型核查.json / 现有模型核查原始输出.txt
"""
from __future__ import annotations

import json
import os
import re
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
P07 = os.path.join(ROOT, "evidence", "PRECISION-07")
SRC = os.path.join(ROOT, "策略", "WyckoffMaster")

import pandas as pd                                                          # noqa: E402

SLIP_BPS = 3.0                      # config.py:87（下面会从源码里再核一次）
SLIP = Decimal("0.0003")
Q12 = Decimal("1e-12")


# ────────────────────────── 源码事实抽取 ──────────────────────────
def _read(rel: str) -> List[str]:
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read().splitlines()


def _find(lines: List[str], pat: str) -> List[Dict[str, Any]]:
    out = []
    rx = re.compile(pat)
    for i, ln in enumerate(lines, 1):
        if rx.search(ln):
            out.append({"line": i, "text": ln.strip()[:160]})
    return out


def source_facts() -> Dict[str, Any]:
    fill = _read("策略/WyckoffMaster/trading_core/fill.py")
    eng = _read("策略/WyckoffMaster/trading_core/engine.py")
    cfg = _read("策略/WyckoffMaster/event_trend_v0_1/config.py")
    adp = _read("策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py")

    slip_cfg = _find(cfg, r"slippage_bps")
    return {
        "fill.py": {
            "round_buy_price": _find(fill, r"def round_buy_price"),
            "round_sell_price": _find(fill, r"def round_sell_price"),
            "price_breakdown": _find(fill, r"def price_breakdown"),
            "ROUND_CEILING": _find(fill, r"ROUND_CEILING"),
            "ROUND_FLOOR": _find(fill, r"ROUND_FLOOR"),
            "guard": _find(fill, r"def _guard"),
            "strict_gates_doc": _find(fill, r"取整后价格必须有限且 > 0|原价必须有限且 > 0"),
            "protocol": _find(fill, r"def quote_entry|def quote_exit|class FillAdapter"),
            "fill_adapter_no_bar": _find(fill, r"bar_low|bar_high|high:|low:"),
            "slippage_bps_line": _find(fill, r"slippage_bps: float"),
        },
        "engine.py": {
            "entry_raw_open": _find(eng, r"raw_open = float\(arr\[\"open\"\]\[i\]\)"),
            "entry_call": _find(eng, r"quote = self\.adapter\.quote_entry"),
            "exit_gap_call": _find(eng, r"fill = self\.adapter\.quote_exit\(target_px=dec\.target_px"),
            "entry_reject_block": _find(eng, r"# 入场价格非法/规格非法"),
            "invalid_reasons_appends": _find(eng, r"invalid_reasons\.append"),
            "run_valid": _find(eng, r"\"run_valid\": not invalid_reasons"),
            "qty_rejects": _find(eng, r"qty_below_min_step|notional_below_min"),
            "prep_signal_align": _find(eng, r"np\.searchsorted\(t15_sec, tsig_confirm"),
            "bar_high_low_local": _find(eng, r"hi, lo, cl = \(float\(arr\[\"high\"\]\[i\]\)"),
        },
        "config.py": {"slippage_bps": slip_cfg},
        "engine_adapter.py": {"strict_true": _find(adp, r"strict=True")},
    }


# ────────────────────────── 62 腿复算 ──────────────────────────
def _d(x) -> Decimal:
    return Decimal(str(x))


def recompute(raw: Decimal, step: Decimal, is_buy: bool,
              slip: Decimal = SLIP) -> Tuple[Decimal, Decimal]:
    if is_buy:
        post_slip = raw * (Decimal(1) + slip)
        post_round = (post_slip / step).to_integral_value(rounding="ROUND_CEILING") * step
    else:
        post_slip = raw * (Decimal(1) - slip)
        post_round = (post_slip / step).to_integral_value(rounding="ROUND_FLOOR") * step
    return post_slip, post_round


def over_amount(fill: Decimal, low: Decimal, high: Decimal, is_buy: bool) -> Decimal:
    """越界金额（>0 才越界）。买看 high，卖看 low。"""
    if is_buy:
        return max(Decimal(0), fill - high)
    return max(Decimal(0), low - fill)


def analyse_legs() -> Dict[str, Any]:
    df = pd.read_csv(os.path.join(P07, "表2_62条腿分类.csv"), encoding="utf-8-sig")
    rows: List[Dict[str, Any]] = []
    problems: List[str] = []

    for _, r in df.iterrows():
        raw = _d(r["参考价raw"])
        step = _d(r["步长"])
        fill_rec = _d(r["记录成交价"])
        lo, hi = _d(r["K线L"]), _d(r["K线H"])
        leg = str(r["腿"])

        # 方向：用「哪一侧复算能逐位复现记录价」反推，不靠命名约定
        cands = []
        tail = False
        for is_buy in (True, False):
            ps, pr = recompute(raw, step, is_buy)
            if pr == fill_rec:
                cands.append((is_buy, ps, pr))
            elif abs(pr - fill_rec) <= Decimal("1e-9"):
                # 落盘是 float：317.78000000000003 这类尾差不是算错
                cands.append((is_buy, ps, pr))
                tail = True
        if len(cands) == 1:
            is_buy, post_slip, post_round = cands[0]
            dir_source = "复算唯一匹配（含 float 尾差）" if tail else "复算唯一匹配"
        elif len(cands) == 2:
            # 两个方向都能复现（raw 在网格上且滑点未跨档）：用越界方向定夺
            od = str(r["越界方向"])
            is_buy = (od == "above_high") or (leg == "入场" and od == "inside")
            post_slip, post_round = recompute(raw, step, is_buy)
            dir_source = "复算两向皆中→按越界方向/腿别"
        else:
            # 记录价复现不出来：如实标注，不硬套
            is_buy = leg == "入场"
            post_slip, post_round = recompute(raw, step, is_buy)
            dir_source = "复算未匹配（存疑）"
            problems.append(f"{r['标的']}/{leg}: 复算 {post_round} != 记录 {fill_rec}")

        slip_delta = post_slip - raw
        round_delta = post_round - post_slip
        over = over_amount(post_round, lo, hi, is_buy)
        slip_over = over_amount(post_slip, lo, hi, is_buy)
        round_over = over_amount(post_round, lo, hi, is_buy) - slip_over
        raw_in_range = (lo <= raw <= hi)

        # 反事实：只留取整（滑点=0）/ 只留滑点（不取整）
        only_round_slip, only_round = recompute(raw, step, is_buy, slip=Decimal(0))
        over_only_round = over_amount(only_round, lo, hi, is_buy)
        over_only_slip = over_amount(post_slip, lo, hi, is_buy)

        rows.append({
            "标的": r["标的"], "run_id": r["run_id"], "腿": leg, "reason": r["reason"],
            "方向": "买" if is_buy else "卖", "方向来源": dir_source,
            "raw": str(raw), "step": str(step), "记录成交价": str(fill_rec),
            "post_slip": str(post_slip), "post_round": str(post_round),
            "low": str(lo), "high": str(hi),
            "raw_in_range": raw_in_range,
            "over": str(over),
            "over_pct": (float(over / raw) * 100.0 if raw else 0.0),
            "slip_over": str(slip_over), "round_over": str(round_over),
            "slip_delta": str(slip_delta), "round_delta": str(round_delta),
            "round_to_slip_ratio": (float(round_delta / slip_delta)
                                    if slip_delta != 0 else None),
            "over_only_round": str(over_only_round),
            "over_only_slip": str(over_only_slip),
            "step_gt_raw": bool(step > raw),
            "fill_le_zero": bool(post_round <= 0),
            "记录价越界": str(over_amount(fill_rec, lo, hi, is_buy)),
            "float_tail": tail,
        })

    # ── 汇总 ──
    n = len(rows)
    inside = [x for x in rows if Decimal(x["over"]) == 0]
    outside = [x for x in rows if Decimal(x["over"]) > 0]
    raw_out = [x for x in rows if not x["raw_in_range"]]
    only_round_out = [x for x in rows if Decimal(x["over_only_round"]) > 0]
    only_slip_out = [x for x in rows if Decimal(x["over_only_slip"]) > 0]
    both = [x for x in rows if Decimal(x["over_only_round"]) > 0
            and Decimal(x["over_only_slip"]) > 0]
    ratios = [x["round_to_slip_ratio"] for x in outside
              if x["round_to_slip_ratio"] is not None]
    step_le_raw_outside = [x for x in outside if not x["step_gt_raw"]]
    combined_only = [x for x in outside
                     if Decimal(x["over_only_round"]) == 0
                     and Decimal(x["over_only_slip"]) == 0]

    summary = {
        "legs_total": n,
        "legs_inside": len(inside),
        "legs_outside": len(outside),
        "raw_price_outside_bar": len(raw_out),
        "raw_price_outside_list": [f"{x['标的']}/{x['腿']}" for x in raw_out],
        "outside_caused_by_round_only": len(only_round_out),
        "outside_caused_by_slip_only": len(only_slip_out),
        "outside_caused_by_both": len(both),
        "outside_only_when_combined": len(combined_only),
        "outside_only_when_combined_list": [f"{x['标的']}/{x['腿']}" for x in combined_only],
        "float_tail_legs": len([x for x in rows if x["float_tail"]]),
        "round_to_slip_ratio_min": (min(ratios) if ratios else None),
        "round_to_slip_ratio_median": (sorted(ratios)[len(ratios) // 2] if ratios else None),
        "round_to_slip_ratio_max": (max(ratios) if ratios else None),
        "outside_but_step_le_raw": len(step_le_raw_outside),
        "outside_but_step_le_raw_list": [f"{x['标的']}/{x['腿']}" for x in step_le_raw_outside],
        "outside_and_fill_le_zero": len([x for x in outside if x["fill_le_zero"]]),
        "recompute_mismatch": problems,
    }
    return {"summary": summary, "legs": rows}


# ────────────────────────── 主流程 ──────────────────────────
def main() -> int:
    facts = source_facts()
    legs = analyse_legs()
    s = legs["summary"]

    doc = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "slippage_bps_used": SLIP_BPS,
        "source_facts": facts,
        "leg_recompute": legs,
    }
    with open(os.path.join(HERE, "现有模型核查.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    L: List[str] = []
    A = L.append
    A("PRECISION-08 / 工具 10 · 现有成交模型核查（只读）")
    A("=" * 78)
    A("")
    A("① 取整方向（fill.py）")
    for k in ("round_buy_price", "round_sell_price"):
        for h in facts["fill.py"][k]:
            A(f"   L{h['line']:<5} {h['text']}")
    for h in facts["fill.py"]["ROUND_CEILING"] + facts["fill.py"]["ROUND_FLOOR"]:
        A(f"   L{h['line']:<5} {h['text']}")
    A("")
    A("② 严格闸门（strict=True 时）")
    for h in facts["fill.py"]["strict_gates_doc"]:
        A(f"   L{h['line']:<5} {h['text']}")
    A(f"   生产路径 engine_adapter.py：")
    for h in facts["engine_adapter.py"]["strict_true"]:
        A(f"   L{h['line']:<5} {h['text']}")
    A("")
    A("③ 协议是否收 K 线区间（R7 的落点问题）")
    for h in facts["fill.py"]["protocol"]:
        A(f"   L{h['line']:<5} {h['text']}")
    A(f"   协议内出现 bar_low/bar_high 的行数：{len(facts['fill.py']['fill_adapter_no_bar'])}")
    A("")
    A("④ 引擎里的四个成交点")
    for h in facts["engine.py"]["entry_call"]:
        A(f"   入场  L{h['line']:<5} {h['text']}")
    for h in facts["engine.py"]["exit_gap_call"]:
        A(f"   出场  L{h['line']:<5} {h['text']}")
    A("   入场被拒时**没有**写 invalid_reasons：")
    for h in facts["engine.py"]["entry_reject_block"]:
        A(f"   L{h['line']:<5} {h['text']}")
    A("   现有 invalid_reasons 只有三处（全是出场）：")
    for h in facts["engine.py"]["invalid_reasons_appends"]:
        A(f"   L{h['line']:<5} {h['text']}")
    A("")
    A("⑤ 滑点参数")
    for h in facts["config.py"]["slippage_bps"]:
        A(f"   config.py L{h['line']:<5} {h['text']}")
    A("")
    A("=" * 78)
    A("62 条成交腿复算（Decimal，滑点 3bps，方向由「哪一侧能逐位复现记录价」反推）")
    A(f"   总腿数                : {s['legs_total']}")
    A(f"   区间内 / 越界         : {s['legs_inside']} / {s['legs_outside']}")
    A(f"   ★ 参考价 raw 越出区间 : {s['raw_price_outside_bar']}  "
      f"{s['raw_price_outside_list']}")
    A(f"   只留取整（滑点=0）越界: {s['outside_caused_by_round_only']}")
    A(f"   只留滑点（不取整）越界: {s['outside_caused_by_slip_only']}")
    A(f"   两者各自都越界        : {s['outside_caused_by_both']}")
    A(f"   单独都不越界、叠加才越界: {s['outside_only_when_combined']} "
      f"{s['outside_only_when_combined_list']}")
    A(f"   取整段/滑点段 倍数    : min={s['round_to_slip_ratio_min']}  "
      f"median={s['round_to_slip_ratio_median']}  max={s['round_to_slip_ratio_max']}")
    A(f"   越界但 step ≤ raw     : {s['outside_but_step_le_raw']} "
      f"（这些 R6 抓不到，只有 R7 能抓）")
    A(f"   越界且取整后 ≤ 0      : {s['outside_and_fill_le_zero']}（由 R5 拦）")
    A(f"   记录价含 float 尾差   : {s['float_tail_legs']} 条（不影响取整结论）")
    A(f"   复算与记录不一致      : {len(s['recompute_mismatch'])}")
    for p in s["recompute_mismatch"]:
        A(f"      !! {p}")
    A("")
    A("逐腿（越界腿）：")
    A(f"   {'标的':<14}{'腿':<5}{'方向':<4}{'raw':>12}{'fill':>12}"
      f"{'low':>12}{'high':>12}{'越界':>11}{'取整/滑点':>11}")
    for x in legs["legs"]:
        if Decimal(x["over"]) <= 0:
            continue
        ratio = x["round_to_slip_ratio"]
        A(f"   {x['标的']:<14}{x['腿']:<5}{x['方向']:<4}{x['raw']:>12}{x['记录成交价']:>12}"
          f"{x['low']:>12}{x['high']:>12}{x['over']:>11}"
          f"{(f'{ratio:,.0f}x' if ratio is not None else '-'):>11}")
    txt = "\n".join(L)
    with open(os.path.join(HERE, "现有模型核查原始输出.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)

    ok = (s["legs_total"] == 62 and not s["recompute_mismatch"]
          and s["legs_inside"] == 35 and s["legs_outside"] == 27)
    print(f"\n自检：62 腿、复算 62/62、区间内 35 / 越界 27 → "
          f"{'通过' if ok else '失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

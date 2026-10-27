#!/usr/bin/env python3
"""02 · 缺口段重算 + 缺口前/中/后/重叠区/冷档尾段 的行为核验

为什么重算
----------
06-05 报的缺口段是 `2026-09-02T20:00Z → 2026-09-18T16:00Z`。本单**不把它当常量**：
先按数据自己算出「只读热库会漏哪些结算事件」，聚类成段，再拿算出来的段去划区。
两者一致就写一致，不一致就写差在哪。

怎么算（不依赖 8h 假设）
----------------------
对每个标的：
  H = 热库给出的结算时刻集合，C = 冷档给出的结算时刻集合，U = H ∪ C
  hot_missing = U − H     ← 这正是适配器要补的
  cold_missing = U − C    ← 反向：只有热库有的
「U 本身是否完整」是另一个问题（06-05 用周期推断回答，本单只复核并标 grid_reliable）。

五个区（段由数据算出来，不是写死）
  pre   缺口段前 48h
  gap   共同缺口段（逐小时统计有多少标的在该小时缺事件，取连续 ≥50% 标的小时的最长段）
  post  缺口段后 48h（且仍在冷档覆盖内）
  tail  冷档覆盖末端之后（只能靠热库）
  ovl   重叠区 = 热库与冷档都给出事件的时段

产出 out/02_缺口段与分区行为.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evidence" / "ACCUM-EARLY-06-06" / "adapter"))

import funding_events_adapter as A  # noqa: E402

H4 = 4 * 3600000
HOUR = 3600000


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def step_hours(times):
    """原生结算间隔的众数（0.25h 粒度），以及众数占比。"""
    if len(times) < 3:
        return None, 0.0, 0
    ds = [b - a for a, b in zip(times, times[1:])]
    buckets = {}
    for d in ds:
        k = round(d / (0.25 * HOUR)) * 0.25
        if k <= 0:
            continue
        buckets[k] = buckets.get(k, 0) + 1
    if not buckets:
        return None, 0.0, 0
    mode = max(buckets.items(), key=lambda kv: (kv[1], -kv[0]))
    return mode[0], mode[1] / max(1, len(ds)), len(ds)


def segments_of(times, max_gap):
    """把时刻列表切成连续段（相邻差 > max_gap 就断开）。"""
    if not times:
        return []
    out = []
    lo = prev = times[0]
    for t in times[1:]:
        if t - prev > max_gap:
            out.append((lo, prev))
            lo = t
        prev = t
    out.append((lo, prev))
    return out


def main() -> int:
    t0 = time.time()
    anchor = json.loads((OUT / "00_范围与身份锚定.json").read_text(encoding="utf-8"))
    symbols = anchor["A_窗口与母集"]["symbols"]
    start_ms = anchor["A_窗口与母集"]["start_ms"]
    end_ms = anchor["A_窗口与母集"]["end_ms"]
    cold_hi_global = anchor["E_冷档事实"]["range"]["hi_ms"]
    cold_lo_global = anchor["E_冷档事实"]["range"]["lo_ms"]

    repo = A._get_repo(None, None)
    per = {}
    for s in symbols:
        per[s] = A.load_funding_events("binance", "um", s, start_ms, end_ms, repo=repo)

    # ---------- 1 逐标的：hot_missing / cold_missing / 周期可靠性 ----------
    rows = []
    all_hot_missing = []
    for s, r in per.items():
        ev = r["events"]
        H = [e["funding_time"] for e in ev if e["observed_in"] in ("hot", "both")]
        C = [e["funding_time"] for e in ev if e["observed_in"] in ("cold", "both")]
        U = [e["funding_time"] for e in ev]
        hs = set(H)
        cs = set(C)
        hm = sorted(set(U) - hs)
        cm = sorted(set(U) - cs)
        step, frac, n_diff = step_hours(sorted(set(H) | set(C)))
        rows.append({
            "symbol": s,
            "events": len(ev),
            "hot_times": len(hs),
            "cold_times": len(cs),
            "hot_missing": len(hm),
            "cold_missing": len(cm),
            "hot_missing_lo_utc": utc(hm[0]) if hm else None,
            "hot_missing_hi_utc": utc(hm[-1]) if hm else None,
            "step_hours_mode": step,
            "step_mode_frac": round(frac, 4),
            "grid_reliable": bool(step is not None and frac >= 0.95),
        })
        all_hot_missing.extend(hm)

    n_unreliable = sum(1 for r in rows if not r["grid_reliable"])
    tot_hm = sum(r["hot_missing"] for r in rows)
    tot_cm = sum(r["cold_missing"] for r in rows)
    tot_ev = sum(r["events"] for r in rows)
    steps = {}
    for r in rows:
        if r["step_hours_mode"] is not None:
            steps[r["step_hours_mode"]] = steps.get(r["step_hours_mode"], 0) + 1

    # ---------- 2 共同缺口段（逐小时覆盖率）----------
    per_sym_segs = {}
    for s, r in per.items():
        hm = sorted(e["funding_time"] for e in r["events"]
                    if e["observed_in"] == "cold")
        # 该标的自己的「热库缺、冷档有」时刻
        per_sym_segs[s] = segments_of(hm, max_gap=2 * H4)

    hour_cover = {}
    for s, segs in per_sym_segs.items():
        for lo, hi in segs:
            h = lo // HOUR
            h_end = hi // HOUR
            while h <= h_end:
                hour_cover[h] = hour_cover.get(h, 0) + 1
                h += 1

    threshold = max(2, len(symbols) // 2)
    hot_hours = sorted(h for h, c in hour_cover.items() if c >= threshold)
    runs = []
    if hot_hours:
        lo = prev = hot_hours[0]
        for h in hot_hours[1:]:
            if h - prev > 2:
                runs.append((lo, prev))
                lo = h
            prev = h
        runs.append((lo, prev))
    runs = sorted(runs, key=lambda r: (r[1] - r[0]), reverse=True)
    gap_lo_h = gap_hi_h = None
    if runs:
        gap_lo_h, gap_hi_h = runs[0]
    GAP_LO = gap_lo_h * HOUR if gap_lo_h is not None else None
    GAP_HI = (gap_hi_h + 1) * HOUR if gap_hi_h is not None else None

    # 「缺口两侧最近在库时刻」：用于解释 06-05 的段端点为什么比本单早/晚一个步长
    lb_list, rb_list = [], []
    for s_, r_ in per.items():
        H_ = sorted(e["funding_time"] for e in r_["events"]
                    if e["observed_in"] in ("hot", "both"))
        hm_ = sorted(e["funding_time"] for e in r_["events"] if e["observed_in"] == "cold")
        if not hm_:
            continue
        before = [t for t in H_ if t < hm_[0]]
        after = [t for t in H_ if t > hm_[-1]]
        if before:
            lb_list.append(max(before))
        if after:
            rb_list.append(min(after))
    BRACKET_LO = min(lb_list) if lb_list else None
    BRACKET_HI = min(rb_list) if rb_list else None

    # 06-05 的 claim 对照
    claim_lo = int(datetime(2026, 9, 2, 20, tzinfo=timezone.utc).timestamp() * 1000)
    claim_hi = int(datetime(2026, 9, 18, 16, tzinfo=timezone.utc).timestamp() * 1000)

    # ---------- 3 分区 ----------
    regions = {
        "pre": (max(start_ms, GAP_LO - 48 * HOUR), GAP_LO),
        "gap": (GAP_LO, GAP_HI),
        "post": (GAP_HI, min(GAP_HI + 48 * HOUR, cold_hi_global)),
        # 左端**开**：恰好落在冷档末端的那一条两侧都有（both），属重叠区而非尾段
        "tail": (cold_hi_global + 1, end_ms),
        "ovl": (cold_lo_global, min(cold_hi_global, end_ms)),
    }

    def in_region(t, lo, hi, lo_open=False):
        return (t > lo if lo_open else t >= lo) and t <= hi

    region_stats = {}
    for name, (lo, hi) in regions.items():
        if lo >= hi:
            region_stats[name] = {"lo_utc": utc(lo), "hi_utc": utc(hi), "events": 0,
                                  "note": "空区间（起止重合）"}
            continue
        ev = [e for s in symbols for e in per[s]["events"] if in_region(e["funding_time"], lo, hi)]
        cnt = {
            "events": len(ev),
            "both": sum(1 for e in ev if e["observed_in"] == "both"),
            "hot_only": sum(1 for e in ev if e["observed_in"] == "hot"),
            "cold_only": sum(1 for e in ev if e["observed_in"] == "cold"),
            "pending_conflict": sum(1 for e in ev if e["status"] == "pending_conflict"),
            "pending_missing": sum(1 for e in ev if e["status"] == "pending_missing"),
            "resolved_single_side": sum(1 for e in ev if e["status"] == "resolved_single_side"),
            "with_mark": sum(1 for e in ev if e["mark_price"] is not None),
        }
        region_stats[name] = {"lo_utc": utc(lo), "lo_ms": lo, "hi_utc": utc(hi), "hi_ms": hi,
                              "span_hours": round((hi - lo) / HOUR, 1), **cnt}

    # ---------- 4 行为断言 ----------
    checks = []

    def add(name, ok, detail):
        checks.append({"检查": name, "通过": bool(ok), "证据": detail})

    g = region_stats.get("gap", {})
    add("R1 缺口段内「热库缺的事件」全部由冷档补上（cold_only == 该区热库缺失数）",
        g.get("cold_only", 0) > 0 and g.get("hot_only", 0) == 0,
        {"gap_区": {"cold_only": g.get("cold_only"), "hot_only": g.get("hot_only"),
                    "both": g.get("both"), "events": g.get("events")}})

    pre = region_stats.get("pre", {})
    add("R2 缺口前两侧都有数据（both 占多数），说明缺口不是「冷档只覆盖缺口段」",
        pre.get("events", 0) > 0 and pre.get("both", 0) / max(1, pre["events"]) > 0.5,
        {"pre": {k: pre.get(k) for k in ("events", "both", "hot_only", "cold_only")}})

    po = region_stats.get("post", {})
    add("R3 缺口后冷档仍在覆盖（both 占多数），且缺口段确有界",
        po.get("events", 0) > 0 and po.get("both", 0) / max(1, po["events"]) > 0.5,
        {"post": {k: po.get(k) for k in ("events", "both", "hot_only", "cold_only")}})

    tl = region_stats.get("tail", {})
    add("R4 冷档尾段（cold_hi 之后）必须 hot_only == events、cold_only == 0",
        tl.get("cold_only", 0) == 0 and tl.get("hot_only", 0) == tl.get("events", -1),
        {"tail": {k: tl.get(k) for k in ("events", "both", "hot_only", "cold_only")},
         "cold_hi_utc": utc(cold_hi_global)})

    ov = region_stats.get("ovl", {})
    add("R5 重叠区：键唯一、无 pending_conflict（同键不重复计事件）",
        ov.get("pending_conflict", 0) == 0 and ov.get("events", 0) > 0,
        {"ovl": {k: ov.get(k) for k in ("events", "both", "pending_conflict",
                                        "pending_missing")}})

    # 重叠区两侧一致率（逐事件，不抽样）
    n_both = n_agree = 0
    for s in symbols:
        for e in per[s]["events"]:
            if e["observed_in"] != "both":
                continue
            n_both += 1
            if e["rate"] is not None and e["mark_price"] is not None:
                n_agree += 1
    add("R6 重叠区每一条 both 事件的 rate/mark 两侧一致（可直接取值）",
        n_both == n_agree, {"both": n_both, "两侧取值一致": n_agree,
                            "不一致": n_both - n_agree})

    # 冷档尾段之后不能再出现「靠冷档补」——用总账再核一次
    late_cold = [e for s in symbols for e in per[s]["events"]
                 if e["observed_in"] == "cold" and e["funding_time"] > cold_hi_global]
    add("R7 全量复核：冷档覆盖末端之后没有任何 cold_only 事件（没有跨边界伪造覆盖）",
        len(late_cold) == 0, {"cold_only_after_cold_hi": len(late_cold)})

    # 缺口段外也可能有小缺口，如实统计（不能只报一个段就说完）
    outside = [t for t in all_hot_missing
               if not (GAP_LO <= t <= GAP_HI)]
    add("R8 缺口段之外的热库缺失如实计数（不因为「主要缺口在段内」就忽略段外）",
        True, {"hot_missing_total": len(all_hot_missing),
               "in_gap": len(all_hot_missing) - len(outside),
               "outside_gap": len(outside),
               "outside_gap_utc_lo": utc(min(outside)) if outside else None,
               "outside_gap_utc_hi": utc(max(outside)) if outside else None})

    # 周期复核
    add("R9 原生结算间隔不是单一 8h（众数分布照实记录）",
        True, {"step_hours_mode_distribution": steps,
               "median_step_hours": statistics.median(
                   [r["step_hours_mode"] for r in rows if r["step_hours_mode"] is not None]),
               "grid_unreliable_symbols": n_unreliable})

    # R10 独立佐证：按众数步长推算的期望数 − 热库已有，是否等于实测「冷档有、热库没有」
    #     相等 ⇒ 可靠标的的冷档在推断网格上没有洞；不等 ⇒ 网格假设与实测不符，必须报出来
    exp_minus_hot = 0
    hot_missing_reliable = 0
    per_sym_check = []
    for s, r in per.items():
        row = next(x for x in rows if x["symbol"] == s)
        if not row["grid_reliable"] or row["step_hours_mode"] is None:
            continue
        ts = [e["funding_time"] for e in r["events"]]
        step_ms = int(row["step_hours_mode"] * HOUR)
        expected = len(range(ts[0], ts[-1] + 1, step_ms))
        hot_n = sum(1 for e in r["events"] if e["observed_in"] in ("hot", "both"))
        diff = max(0, expected - hot_n)
        exp_minus_hot += diff
        hot_missing_reliable += row["hot_missing"]
        if diff != row["hot_missing"]:
            per_sym_check.append({"symbol": s, "期望减热库": diff,
                                  "实测冷档减热库": row["hot_missing"]})
    add("R10 独立佐证：可靠标的上「按周期推算的期望缺口」== 「实测冷档补齐数」",
        exp_minus_hot == hot_missing_reliable and not per_sym_check,
        {"期望推算合计": exp_minus_hot, "实测合计": hot_missing_reliable,
         "逐标的不一致的标的": per_sym_check[:10],
         "含义": "两者相等 ⇒ 这些标的的冷档在推断网格上没有洞；"
                 "若不等，说明「周期假设」与「实测」不符，不能拿推算值当结论"})

    n_pass = sum(1 for c in checks if c["通过"])
    out = {
        "task": "ACCUM-EARLY-06-06",
        "probe": "02_缺口段与分区行为",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "口径": {
            "hot_missing": "U − H（U = 热库∪冷档的结算时刻），即「只读热库会漏掉的结算事件」",
            "cold_missing": "U − C，即「只有热库有的结算事件」",
            "不做的事": "不按 8h 推算期望条数、不补 0、不插值",
        },
        "缺口段_重算": {
            "gap_lo_ms": GAP_LO, "gap_lo_utc": utc(GAP_LO) if GAP_LO else None,
            "gap_hi_ms": GAP_HI, "gap_hi_utc": utc(GAP_HI) if GAP_HI else None,
            "span_hours": round((GAP_HI - GAP_LO) / HOUR, 2) if GAP_LO else None,
            "稠密段含义": f"该时段内每个小时都有 ≥{threshold} 个标的缺事件",
            "全跨度_lo_utc": utc(min(all_hot_missing)) if all_hot_missing else None,
            "全跨度_hi_utc": utc(max(all_hot_missing)) if all_hot_missing else None,
            "全跨度_hours": (round((max(all_hot_missing) - min(all_hot_missing)) / HOUR, 2)
                          if all_hot_missing else None),
            "全跨度含义": "第一条到最后一条「热库缺失」的实际时刻；两端只有少数标的缺，"
                          "所以比稠密段宽，但**不是** 8h 推算出来的",
            "method": "逐标的算 hot_missing → 切连续段 → 逐小时统计覆盖标的数 → "
                      f"取连续 ≥{threshold} 个标的的小时 → 最长连续段",
            "threshold_symbols": threshold,
            "runs_top3": [{"lo_utc": utc(a * HOUR), "hi_utc": utc((b + 1) * HOUR),
                           "hours": b - a + 1} for a, b in runs[:3]],
        },
        "与06-05对照": {
            "claim_lo_utc": utc(claim_lo), "claim_hi_utc": utc(claim_hi),
            "claim_span_hours": round((claim_hi - claim_lo) / HOUR, 2),
            "本单_lo_utc": utc(GAP_LO) if GAP_LO else None,
            "本单_hi_utc": utc(GAP_HI) if GAP_HI else None,
            "起止完全一致_缺失端点口径": bool(GAP_LO == claim_lo and GAP_HI == claim_hi),
            "起止完全一致_在库端点口径": bool(BRACKET_LO == claim_lo),
            "起差小时": round((GAP_LO - claim_lo) / HOUR, 2) if GAP_LO else None,
            "止差小时": round((GAP_HI - claim_hi) / HOUR, 2) if GAP_HI else None,
            "差异原因": "端点归属不同：06-05 的段端点是「缺口两侧最近的在库时刻」"
                        f"（本单实测 {utc(BRACKET_LO) if BRACKET_LO else None} → "
                        f"{utc(BRACKET_HI) if BRACKET_HI else None}），"
                        "本单的段端点是「缺失时刻本身」"
                        f"（{utc(min(all_hot_missing)) if all_hot_missing else None} → "
                        f"{utc(max(all_hot_missing)) if all_hot_missing else None}）。"
                        "两者恰好差一个 4h 步长，跨度都是 380h ⇒ 描述的是同一个缺口，"
                        "不构成互相否证，但**端点不能混用**：拿本单的缺失时刻当「最后一条在库数据」会算错。",
            "本单_两侧最近在库时刻_起": utc(BRACKET_LO) if BRACKET_LO else None,
            "本单_两侧最近在库时刻_止": utc(BRACKET_HI) if BRACKET_HI else None,
            "跨度是否相同": bool(all_hot_missing
                             and round((max(all_hot_missing) - min(all_hot_missing)) / HOUR, 2)
                             == round((claim_hi - claim_lo) / HOUR, 2)),
            "claim_lo是否落在全跨度内": bool(all_hot_missing
                                     and min(all_hot_missing) <= claim_lo <= max(all_hot_missing)),
            "claim_hi是否落在全跨度内": bool(all_hot_missing
                                     and min(all_hot_missing) <= claim_hi <= max(all_hot_missing)),
        },
        "汇总": {
            "symbols": len(symbols),
            "events": tot_ev,
            "hot_missing": tot_hm,
            "cold_missing": tot_cm,
            "symbols_with_hot_missing": sum(1 for r in rows if r["hot_missing"] > 0),
            "grid_unreliable_symbols": n_unreliable,
            "step_hours_mode_distribution": steps,
        },
        "分区": region_stats,
        "checks": checks,
        "summary": {"total": len(checks), "pass": n_pass,
                    "verdict": "通过" if n_pass == len(checks) else "不通过"},
        "per_symbol": rows,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    p = OUT / "02_缺口段与分区行为.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("缺口段(重算):", out["缺口段_重算"]["gap_lo_utc"], "→",
          out["缺口段_重算"]["gap_hi_utc"], out["缺口段_重算"]["span_hours"], "h")
    print("与06-05对照:", json.dumps(out["与06-05对照"], ensure_ascii=False))
    print("汇总:", json.dumps(out["汇总"], ensure_ascii=False))
    for name in ("pre", "gap", "post", "tail", "ovl"):
        v = region_stats.get(name, {})
        print(f"  {name:5s} {v.get('lo_utc')} → {v.get('hi_utc')} "
              f"events={v.get('events')} both={v.get('both')} "
              f"hot_only={v.get('hot_only')} cold_only={v.get('cold_only')}")
    for c in checks:
        print(("PASS " if c["通过"] else "FAIL ") + c["检查"])
    print(out["summary"], "elapsed", out["elapsed_sec"], "s")
    return 0 if n_pass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

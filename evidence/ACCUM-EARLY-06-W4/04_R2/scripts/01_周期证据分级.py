# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R2 · 01 周期证据分级（每标的）

把「周期」拆成三类互不替代的证据：
  · 官方：交易所 `/fapi/v1/fundingInfo` 快照，含 `updateTime` ⇒ 官方值自 updateTime 起生效。
          交易所**不提供**变更流水 ⇒ 早于 updateTime 的时段没有官方证据。
  · 观测推断：历史结算间隔的规律。**只能推断**，且缺失记录本身会改变观察到的间隔。
  · 不可判定：样本不足、间隔非整数倍、相位冲突。

本脚本只做证据分级，不下完整性结论（完整性在 02）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r2 as L
import funding_events_adapter as FA

CHECKS: list = []
SERIES_LO = 1500000000000      # 2017-07，早于全部样本
CONTEXT_DAYS = 30


def add(name: str, ok: bool, evidence) -> None:
    CHECKS.append({"检查": name, "通过": bool(ok), "证据": evidence})


def runs_summary(times: list) -> dict:
    runs = L.detect_runs(times, min_len=1)
    major = [r for r in runs if r["count"] >= 3]
    minor = [r for r in runs if r["count"] < 3]
    return {
        "n_runs": len(runs),
        "major_runs": [{"interval_h": r["interval_h"], "count": r["count"],
                        "start_utc": L.iso(r["start_ms"]), "end_utc": L.iso(r["end_ms"])}
                       for r in major],
        "minor_runs": [{"interval_h": r["interval_h"], "count": r["count"],
                        "at_utc": L.iso(r["start_ms"])} for r in minor],
        "distinct_intervals": sorted({r["interval_h"] for r in runs}),
        "n_major_runs": len(major),
        "n_minor_runs": len(minor),
    }


def switch_points(runs: list) -> list:
    """连续两个「≥3 次」的段之间，间隔值变化 ⇒ 周期切换点（单次异常不算切换）。"""
    major = [r for r in runs if r["count"] >= 3]
    out = []
    for i in range(1, len(major)):
        a, b = major[i - 1], major[i]
        if a["interval_h"] != b["interval_h"]:
            out.append({"at_utc": L.iso(b["start_ms"]), "from_h": a["interval_h"],
                        "to_h": b["interval_h"], "at_ms": b["start_ms"],
                        "prev_run_count": a["count"], "next_run_count": b["count"]})
    return out


def ms_jitter(times: list) -> dict:
    """毫秒尾数分布：R1 用精确毫秒相等匹配预期时刻，尾数非 0 就会被判成缺失。"""
    c = Counter(t % 1000 for t in times)
    n = sum(c.values())
    return {"n": n, "zero": c.get(0, 0),
            "nonzero": n - c.get(0, 0),
            "nonzero_frac": round((n - c.get(0, 0)) / n, 6) if n else None,
            "max_ms": max(c) if c else None,
            "top": dict(sorted(c.items())[:8])}


def main() -> int:
    cands = L.load_candidates()
    syms = sorted({c["symbol"] for c in cands})
    holds = {}
    for c in cands:
        e = L.to_ms(c["entry_bar_utc"])
        x = L.to_ms(c["exit_utc"]) if c.get("exit_utc") else None
        holds.setdefault(c["symbol"], []).append(
            {"candidate_id": c["candidate_id"], "entry_ms": e, "exit_ms": x,
             "entry_utc": c["entry_bar_utc"], "exit_utc": c.get("exit_utc")})

    off = L.official_index()
    repo = FA._get_repo(None, None)
    win_hi = max(h["exit_ms"] for hs in holds.values() for h in hs if h["exit_ms"])

    per_symbol = {}
    for s in syms:
        res = FA.load_funding_events("binance", "um", s, SERIES_LO, win_hi + 7 * L.DAY, repo=repo)
        times = sorted(e["funding_time"] for e in res["events"])
        r = runs_summary(times)
        sp = switch_points(L.detect_runs(times))
        jit = ms_jitter(times)

        # 最近 90 天视角（贴近持仓）
        recent_lo = min(h["entry_ms"] for h in holds[s]) - 90 * L.DAY
        recent = [t for t in times if t >= recent_lo]
        recent_step = L.mode_step_hours(recent)
        full_step = L.mode_step_hours(times)

        o = off.get(s)
        off_rec = None
        if o:
            off_rec = {"interval_hours": o["interval_hours"],
                       "update_ms": o["update_ms"], "update_utc": L.iso(o["update_ms"]),
                       "disclaimer": o["disclaimer"]}
            # 交叉核对：官方值自 updateTime 起是否与观测一致
            if o["update_ms"] and o["interval_hours"]:
                after = [t for t in times if t > o["update_ms"]]
                iv_after = [round(x, 2) for x in L.intervals_h(after)]
                step = float(o["interval_hours"])
                agree = [x for x in iv_after if abs(x - step) < 1e-9]
                # 缺失记录会让间隔变成 k×step（k≥2）；这**不是**矛盾，是缺口的痕迹。
                mult = [x for x in iv_after if x > step and abs(x / step - round(x / step)) < 1e-9
                        and round(x / step) >= 2]
                # 比官方周期更短、或不是整数倍 ⇒ 与官方值真矛盾（可能是别的周期段）
                contra = [x for x in iv_after if abs(x - step) >= 1e-9
                          and not (x > step and abs(x / step - round(x / step)) < 1e-9
                                   and round(x / step) >= 2)]
                off_rec["observed_after_update"] = {
                    "n_intervals": len(iv_after),
                    "agree_with_official": len(agree),
                    "multiples_of_official": dict(Counter(mult)),
                    "contradicting_intervals": dict(Counter(contra)),
                    "consistent": len(contra) == 0,
                    "判据": "间隔 == 官方值 ⇒ 一致；间隔是官方值的整数倍 ⇒ 缺口痕迹（不算矛盾）；"
                            "间隔比官方值更短或非整数倍 ⇒ 与官方值矛盾",
                }

        per_symbol[s] = {
            "official": off_rec,
            "observed": {
                "n_events": len(times),
                "first_utc": L.iso(times[0]) if times else None,
                "last_utc": L.iso(times[-1]) if times else None,
                "full_step_h": full_step,
                "recent_90d_step_h": recent_step,
                "runs": r,
                "switch_points": sp,
                "ms_jitter": jit,
            },
            "holds": holds[s],
        }

    # 汇总
    n_off = sum(1 for s in syms if per_symbol[s]["official"])
    n_off_consistent = sum(1 for s in syms
                           if per_symbol[s]["official"]
                           and per_symbol[s]["official"].get("observed_after_update", {}).get("consistent"))
    n_off_inconsistent = sum(1 for s in syms
                             if per_symbol[s]["official"]
                             and per_symbol[s]["official"].get("observed_after_update")
                             and not per_symbol[s]["official"]["observed_after_update"]["consistent"])
    n_switch = sum(1 for s in syms if per_symbol[s]["observed"]["switch_points"])
    n_multi = sum(1 for s in syms
                  if len(per_symbol[s]["observed"]["runs"]["distinct_intervals"]) > 1)
    jitter_nonzero = sum(per_symbol[s]["observed"]["ms_jitter"]["nonzero"] for s in syms)
    jitter_total = sum(per_symbol[s]["observed"]["ms_jitter"]["n"] for s in syms)

    out = {
        "工单": "ACCUM-EARLY-06-W4-R2",
        "口径": {
            "证据三类": ["official_snapshot（官方，含 updateTime）",
                         "inferred_*（观测推断）", "undetermined（不可判定）"],
            "为什么观测间隔不能证明原生周期":
                "① 缺失记录会把 4h 看成 8h/12h，观察到的「规律」本身被缺失污染；"
                "② 周期可变更（实测有标的在窗口内 8h→4h→1h 来回切）；"
                "③ 历史间隔规律只说明「过去这样结算」，不说明交易所当时的规则文本。",
            "官方证据的边界":
                "`updateTime` 是该行最后一次更新时间，交易所不提供变更流水 ⇒ "
                "只能界定「官方值自 updateTime 起生效」，更早时段无官方证据。",
        },
        "汇总": {
            "symbols": len(syms),
            "official_hit": n_off,
            "official_consistent_with_observed_after_update": n_off_consistent,
            "official_inconsistent_with_observed": n_off_inconsistent,
            "symbols_with_switch_points": n_switch,
            "symbols_with_multiple_intervals": n_multi,
            "events_total": jitter_total,
            "events_ms_suffix_nonzero": jitter_nonzero,
            "events_ms_suffix_nonzero_frac": round(jitter_nonzero / jitter_total, 6),
        },
        "per_symbol": per_symbol,
        "checks": CHECKS,
    }

    add("P1 48 标的全有官方快照记录（interval 字段非空）",
        all(per_symbol[s]["official"] and per_symbol[s]["official"]["interval_hours"]
            for s in syms),
        {"缺 interval": [s for s in syms
                        if not (per_symbol[s]["official"]
                                and per_symbol[s]["official"]["interval_hours"])]})
    add("P2 官方值与观测在 updateTime 之后无矛盾（否则必须判冲突）",
        n_off_inconsistent == 0,
        {"矛盾标的": [s for s in syms
                     if per_symbol[s]["official"]
                     and per_symbol[s]["official"].get("observed_after_update")
                     and not per_symbol[s]["official"]["observed_after_update"]["consistent"]]})
    add("P3 确实存在周期切换的标的（说明「一个标的只有一个周期」是错的）",
        n_switch >= 1,
        {"有切换点的标的数": n_switch,
         "样例": [{"symbol": s, "switch_points": per_symbol[s]["observed"]["switch_points"][:3]}
                  for s in syms if per_symbol[s]["observed"]["switch_points"]][:4]})
    add("P4 毫秒尾数非 0 的事件占相当比例（精确毫秒匹配必然误判）",
        jitter_nonzero > 0,
        {"非零尾数事件": jitter_nonzero, "占比": round(jitter_nonzero / jitter_total, 4),
         "最大尾数ms": max(per_symbol[s]["observed"]["ms_jitter"]["max_ms"] or 0 for s in syms)})

    (L.OUT / "01_周期证据分级.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = sum(1 for c in CHECKS if c["通过"])
    print(f"标的 {len(syms)} | 官方命中 {n_off} | 官方与观测矛盾 {n_off_inconsistent}")
    print(f"有周期切换点 {n_switch} 个标的 | 多间隔 {n_multi} 个标的")
    print(f"事件 {jitter_total} | 毫秒尾数非0 {jitter_nonzero}"
          f"（{jitter_nonzero / jitter_total:.2%}）")
    print(f"checks {ok}/{len(CHECKS)}")
    for c in CHECKS:
        if not c["通过"]:
            print("  FAIL", c["检查"], json.dumps(c["证据"], ensure_ascii=False)[:400])
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

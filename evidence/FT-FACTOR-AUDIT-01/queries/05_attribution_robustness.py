#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FT-FACTOR-AUDIT-01 / 05 过滤归因、仓位竞争、极值剔除、不确定性与多重比较独立核对

只读：FT-FACTOR-02/out、FT-FACTOR-03/out 的逐笔成交文件
输出：out/13_过滤归因与仓位竞争核对_v1.csv、out/14_逐笔差异明细_v1.csv、
      out/15_极值剔除核对_v1.csv、out/16_不确定性与多重比较核对_v1.csv、
      out/17_年度切片核对_v1.csv

归因定义（与反重力口径对齐后独立实现）：
  被基线过滤的交易 = 消融组有、基线组没有的同 (pair, open_timestamp) 交易
  其 PnL 取消融组里的实际值（反事实口径），<0 记为拦截亏损噪音，>0 记为误伤盈利机会
  基线有、消融组没有 → 反直觉差异，标"无法归因"（通常来自权益路径/仓位规模差异）
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
from collections import defaultdict
from datetime import datetime, timezone

csv.field_size_limit(10 ** 9)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(BASE))
AG3 = os.path.join(ROOT, "evidence", "FT-FACTOR-03", "out")
AG2 = os.path.join(ROOT, "evidence", "FT-FACTOR-02", "out")
OUT = os.path.join(BASE, "out")

MAX_OPEN = 3
STRATS = [
    ("Baseline (完整过滤)", "WyckoffEventTrendBaseline"),
    ("No OI", "WyckoffAblationNoOI"),
    ("No BreakoutVol", "WyckoffAblationNoBreakoutVol"),
    ("No RetestVol", "WyckoffAblationNoRetestVol"),
    ("No GapFilter", "WyckoffAblationNoGapFilter"),
]
SOURCES = {
    "复用旧批次(36币)": (AG2, "trades_research_{s}.csv"),
    "新增批次(38币)": (AG3, "trades_NewBatches38_{s}.csv"),
    "全池合并(74币)": (AG3, "trades_ExpandedAll74_{s}.csv"),
}


def load(path):
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["pnl"] = float(r["profit_abs"])
        r["o"] = int(r["open_timestamp"])
        r["c"] = int(r["close_timestamp"])
    return rows


def key(t):
    """逐笔对应主键：候选事件 ID（enter_tag）。比 (pair, open_ts) 更稳：
    去掉跳空上限后同一候选的入场 15m 会提前，若按开仓时刻匹配会被误判成'被过滤'。"""
    return t["enter_tag"]


def concurrency_at(trades, ts):
    """在 ts 时刻已持有的仓位数量（开仓时刻 < ts <= 平仓时刻）。"""
    return sum(1 for t in trades if t["o"] < ts <= t["c"])


def main():
    data = {}
    for g, (d, tpl) in SOURCES.items():
        for s, strat in STRATS:
            data[(g, s)] = load(os.path.join(d, tpl.format(s=strat)))

    # ---------- 1. 归因核对 ----------
    attr = []
    detail = []
    for g in SOURCES:
        base = data[(g, "Baseline (完整过滤)")]
        bset = {key(t): t for t in base}
        for s, _ in STRATS[1:]:
            abl = data[(g, s)]
            aset = {key(t): t for t in abl}
            filtered = [t for k, t in aset.items() if k not in bset]
            missing = [t for k, t in bset.items() if k not in aset]
            # 对照口径：若按 (pair, open_ts) 匹配，会把"同候选但入场位移"误判为被过滤
            pair_key = lambda t: (t["pair"], t["o"])
            bp = {pair_key(t) for t in base}
            filtered_pairkey = [t for t in abl if pair_key(t) not in bp]
            noise = [t for t in filtered if t["pnl"] < -1e-12]
            opp = [t for t in filtered if t["pnl"] > 1e-12]
            # 共有交易的 PnL 差异（权益路径/仓位规模影响）
            common = [k for k in aset if k in bset]
            diffs = [aset[k]["pnl"] - bset[k]["pnl"] for k in common]
            shifts = [k for k in common if aset[k]["o"] != bset[k]["o"]]
            # 仓位竞争：消融组在该笔开仓时刻的并发数
            slot_blocked = 0
            for t in filtered:
                if concurrency_at(abl, t["o"]) >= MAX_OPEN:
                    slot_blocked += 1
            max_conc = 0
            for t in abl:
                max_conc = max(max_conc, concurrency_at(abl, t["o"] + 1))
            attr.append(
                {
                    "group": g,
                    "strategy_group": s,
                    "baseline_trades": len(base),
                    "ablation_trades": len(abl),
                    "common_trades": len(common),
                    "filtered_out_count": len(filtered),
                    "filtered_out_count_by_pair_open_ts": len(filtered_pairkey),
                    "noise_count": len(noise),
                    "noise_saved_u": round(sum(t["pnl"] for t in noise), 6),
                    "opportunity_count": len(opp),
                    "opportunity_missed_u": round(sum(t["pnl"] for t in opp), 6),
                    "net_filter_gain_u": round(-sum(t["pnl"] for t in filtered), 6),
                    "baseline_only_count": len(missing),
                    "baseline_only_pnl_u": round(sum(t["pnl"] for t in missing), 6),
                    "common_trade_pnl_diff_abs_sum": round(sum(abs(x) for x in diffs), 6),
                    "common_trade_pnl_diff_max_abs": round(max((abs(x) for x in diffs), default=0.0), 6),
                    "same_candidate_fill_shift_count": len(shifts),
                    "same_candidate_fill_shift_detail": "; ".join(
                        f"{bset[k]['pair']} {bset[k]['o']}->{aset[k]['o']} pnl {bset[k]['pnl']:.2f}->{aset[k]['pnl']:.2f}"
                        for k in shifts
                    ),
                    "max_concurrent_positions": max_conc,
                    "slot_blocked_trades": slot_blocked,
                    "attribution_status": (
                        "可逐笔对应" if not missing else f"{len(missing)} 笔无法归因（基线独有）"
                    ),
                }
            )
            for t in filtered:
                detail.append({"group": g, "strategy_group": s, "kind": "被过滤",
                               "pair": t["pair"], "open_utc": datetime.fromtimestamp(t["o"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                               "close_utc": datetime.fromtimestamp(t["c"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                               "pnl_u": round(t["pnl"], 6),
                               "class": "拦截亏损噪音" if t["pnl"] < 0 else "误伤盈利机会",
                               "concurrency_at_open": concurrency_at(abl, t["o"])})
            for t in missing:
                detail.append({"group": g, "strategy_group": s, "kind": "基线独有",
                               "pair": t["pair"], "open_utc": datetime.fromtimestamp(t["o"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                               "close_utc": datetime.fromtimestamp(t["c"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                               "pnl_u": round(t["pnl"], 6), "class": "无法归因",
                               "concurrency_at_open": concurrency_at(base, t["o"])})

    with open(os.path.join(OUT, "13_过滤归因与仓位竞争核对_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(attr[0].keys()))
        w.writeheader()
        for r in attr:
            w.writerow(r)
    with open(os.path.join(OUT, "14_逐笔差异明细_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(detail[0].keys()))
        w.writeheader()
        for r in detail:
            w.writerow(r)

    # ---------- 2. 与自报归因对照 ----------
    ag06 = {}
    with open(os.path.join(AG3, "06_扩大样本因子消融对照与噪音过滤总表.csv"), encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            ag06[(r["样本池组别"], r["策略组别"])] = r

    def f(x):
        try:
            return float(str(x).replace("U", "").replace("笔", "").split("/")[0].strip())
        except (TypeError, ValueError):
            return None

    attr_cmp = []
    for r in attr:
        a = ag06.get((r["group"], r["strategy_group"]), {})
        attr_cmp.append({
            "group": r["group"],
            "strategy_group": r["strategy_group"],
            "filtered_mine_by_candidate_id": r["filtered_out_count"],
            "filtered_mine_by_pair_open_ts": r["filtered_out_count_by_pair_open_ts"],
            "noise_mine": f"{r['noise_count']}笔/{abs(r['noise_saved_u']):.2f}U",
            "noise_ag": a.get("拦截亏损噪音(笔/金额)", ""),
            "noise_match": f"{r['noise_count']}笔/{abs(r['noise_saved_u']):.2f}U" == str(a.get("拦截亏损噪音(笔/金额)", "")).replace(" ", ""),
            "opp_mine": f"{r['opportunity_count']}笔/{r['opportunity_missed_u']:.2f}U",
            "opp_ag": a.get("误伤盈利机会(笔/金额)", ""),
            "opp_match": f"{r['opportunity_count']}笔/{r['opportunity_missed_u']:.2f}U" == str(a.get("误伤盈利机会(笔/金额)", "")).replace(" ", ""),
            "gain_mine": r["net_filter_gain_u"],
            "gain_ag": f(a.get("净过滤增益(U)")),
            "gain_diff": (round(r["net_filter_gain_u"] - f(a.get("净过滤增益(U)")), 6) if f(a.get("净过滤增益(U)")) is not None else ""),
            "slot_mine": r["slot_blocked_trades"],
            "slot_ag": a.get("仓位挤占交易数", ""),
            "max_conc_mine": r["max_concurrent_positions"],
            "hit_max_open_trades": r["max_concurrent_positions"] >= MAX_OPEN,
        })
    with open(os.path.join(OUT, "13b_归因与自报对照_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(attr_cmp[0].keys()))
        w.writeheader()
        for r in attr_cmp:
            w.writerow(r)

    # ---------- 3. 极值剔除 ----------
    ext = []
    for s, _ in STRATS:
        ts = data[("全池合并(74币)", s)]
        net = sum(t["pnl"] for t in ts)
        mx = max(ts, key=lambda t: t["pnl"]) if ts else None
        by_sym = defaultdict(float)
        for t in ts:
            by_sym[t["pair"]] += t["pnl"]
        top_sym = max(by_sym.items(), key=lambda x: x[1]) if by_sym else ("", 0.0)
        ext.append({
            "strategy_group": s,
            "net_all_u": round(net, 6),
            "max_win_u": round(mx["pnl"], 6) if mx else 0.0,
            "max_win_pair": mx["pair"] if mx else "",
            "net_ex_max_win_u": round(net - mx["pnl"], 6) if mx else round(net, 6),
            "top_symbol": top_sym[0],
            "top_symbol_pnl_u": round(top_sym[1], 6),
            "net_ex_top_symbol_u": round(net - top_sym[1], 6),
            "trades": len(ts),
        })
    with open(os.path.join(OUT, "15_极值剔除核对_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ext[0].keys()))
        w.writeheader()
        for r in ext:
            w.writerow(r)

    # ---------- 4. 年度切片 ----------
    yrs = []
    for g in SOURCES:
        for s, _ in STRATS:
            by = defaultdict(list)
            for t in data[(g, s)]:
                by[datetime.fromtimestamp(t["o"] / 1000, tz=timezone.utc).strftime("%Y")].append(t)
            for y, ts in sorted(by.items()):
                yrs.append({"group": g, "strategy_group": s, "year": y, "trades": len(ts),
                            "wins": sum(1 for t in ts if t["pnl"] > 0),
                            "net_u": round(sum(t["pnl"] for t in ts), 6),
                            "expectancy_u": round(sum(t["pnl"] for t in ts) / len(ts), 6)})
    with open(os.path.join(OUT, "17_年度切片核对_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(yrs[0].keys()))
        w.writeheader()
        for r in yrs:
            w.writerow(r)

    # ---------- 5. Block Bootstrap + 置换检验 + Holm ----------
    BLOCK = 30 * 24 * 3600
    rnd = random.Random(20261020)

    def blocks_of(trades, t0):
        d = defaultdict(float)
        for t in trades:
            d[int((t["o"] - t0) // (BLOCK * 1000))] += t["pnl"]
        return d

    def boot_diff(a, b, reps=1000):
        t0 = min([t["o"] for t in a] + [t["o"] for t in b])
        ba, bb = blocks_of(a, t0), blocks_of(b, t0)
        keys = sorted(set(ba) | set(bb))
        out = []
        for _ in range(reps):
            pick = [rnd.choice(keys) for _ in keys]
            out.append(sum(ba.get(k, 0.0) for k in pick) - sum(bb.get(k, 0.0) for k in pick))
        out.sort()
        mean = sum(out) / len(out)
        lo, hi = out[int(0.025 * len(out))], out[int(0.975 * len(out)) - 1]
        p = 2 * min(sum(1 for x in out if x <= 0) / len(out), sum(1 for x in out if x >= 0) / len(out))
        return mean, lo, hi, min(1.0, p)

    def perm_p(a, b, reps=2000):
        """交易级置换检验：净收益总和之差。"""
        va = [t["pnl"] for t in a]
        vb = [t["pnl"] for t in b]
        obs = sum(va) - sum(vb)
        pool = va + vb
        cnt = 0
        for _ in range(reps):
            rnd.shuffle(pool)
            d = sum(pool[: len(va)]) - sum(pool[len(va):])
            if abs(d) >= abs(obs) - 1e-12:
                cnt += 1
        return (cnt + 1) / (reps + 1)

    stat = []
    for g in ("全池合并(74币)",):
        base = data[(g, "Baseline (完整过滤)")]
        for s, _ in STRATS[1:]:
            abl = data[(g, s)]
            mean, lo, hi, p = boot_diff(base, abl)
            stat.append({
                "group": g,
                "comparison": f"Baseline vs {s}",
                "raw_net_diff_u": round(sum(t["pnl"] for t in base) - sum(t["pnl"] for t in abl), 6),
                "boot_mean_diff_u": round(mean, 6),
                "boot_ci95_low_u": round(lo, 6),
                "boot_ci95_high_u": round(hi, 6),
                "boot_p_empirical": round(p, 4),
                "perm_p_trade_level": round(perm_p(base, abl), 4),
                "ci_crosses_zero": lo <= 0 <= hi,
            })
    # Holm over 4 ablations (同一族)，并另给出 12 次比较的口径提示
    ps = sorted(range(len(stat)), key=lambda i: stat[i]["perm_p_trade_level"])
    m = len(ps)
    prev = 0.0
    for rank, i in enumerate(ps):
        adj = min(1.0, (m - rank) * stat[i]["perm_p_trade_level"])
        adj = max(adj, prev)
        prev = adj
        stat[i]["holm_adj_p_perm"] = round(adj, 4)
    for r in stat:
        r["holm_scope"] = "仅 4 个消融（未对 3 组×4 因子=12 次比较校正）"

    with open(os.path.join(OUT, "16_不确定性与多重比较核对_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(stat[0].keys()))
        w.writeheader()
        for r in stat:
            w.writerow(r)

    # ---------- 6. 摘要 ----------
    print("=== 归因核对（全池74币）===")
    for r in attr:
        if r["group"] != "全池合并(74币)":
            continue
        print(f"  {r['strategy_group']:20s} filtered={r['filtered_out_count']:2d} "
              f"noise={r['noise_count']}/{r['noise_saved_u']:.2f} opp={r['opportunity_count']}/{r['opportunity_missed_u']:.2f} "
              f"gain={r['net_filter_gain_u']:+.2f} baseline_only={r['baseline_only_count']} "
              f"maxconc={r['max_concurrent_positions']} slotblocked={r['slot_blocked_trades']} "
              f"common_diff_max={r['common_trade_pnl_diff_max_abs']}")
    print("=== 归因与自报逐项对照（仅列不一致项）===")
    for r in attr_cmp:
        if r["noise_match"] and r["opp_match"] and r["gain_diff"] is not None and abs(r["gain_diff"]) < 0.02:
            continue
        print(f"  DIFF {r['group']} {r['strategy_group']}: 过滤笔数(候选ID)={r['filtered_mine_by_candidate_id']} "
              f"(按pair+时刻={r['filtered_mine_by_pair_open_ts']}); "
              f"noise {r['noise_mine']} vs {r['noise_ag']}; opp {r['opp_mine']} vs {r['opp_ag']}; "
              f"gain {r['gain_mine']:+.2f} vs {r['gain_ag']} (差 {r['gain_diff']})")
    print("=== 仓位竞争 ===")
    for r in attr:
        print(f"  {r['group']:14s} {r['strategy_group']:20s} maxconc={r['max_concurrent_positions']} "
              f"hit_limit={r['max_concurrent_positions'] >= MAX_OPEN} slotblocked={r['slot_blocked_trades']} "
              f"fill_shift={r['same_candidate_fill_shift_count']}")
    print("=== 极值剔除 ===")
    for r in ext:
        print(f"  {r['strategy_group']:20s} net={r['net_all_u']:+.2f} ex_max={r['net_ex_max_win_u']:+.2f} "
              f"top={r['top_symbol']}({r['top_symbol_pnl_u']:+.2f}) ex_top={r['net_ex_top_symbol_u']:+.2f}")
    print("=== Bootstrap / 置换 ===")
    for r in stat:
        print(f"  {r['comparison']:34s} raw={r['raw_net_diff_u']:+.2f} boot_mean={r['boot_mean_diff_u']:+.2f} "
              f"CI=[{r['boot_ci95_low_u']:+.2f},{r['boot_ci95_high_u']:+.2f}] boot_p={r['boot_p_empirical']} "
              f"perm_p={r['perm_p_trade_level']} holm={r['holm_adj_p_perm']}")
    print("=== 年度切片（全池基线）===")
    for r in yrs:
        if r["group"] == "全池合并(74币)" and r["strategy_group"] == "Baseline (完整过滤)":
            print("  ", r)


if __name__ == "__main__":
    main()

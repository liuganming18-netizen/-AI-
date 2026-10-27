#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FT-FACTOR-AUDIT-01 / 06 迟到批次 WyckoffPVVariant_* 独立核对

背景：本审计 2026-10-20 19:32 开始时，out/ 下只有 WyckoffEventTrendBaseline /
WyckoffAblation* 共 10 个回测。19:45~19:52 反重力又落了 4 个
`backtest-result-Full74-WyckoffPVVariant_*.zip` 与对应逐笔 CSV，19:53 补写
`05_全池清单接入与价量研究变体执行报告.md` 与 versions.json。
因此原报告里"PVVariant 无报告、不纳入核对"的表述已过时，本脚本补做核对。

只读：evidence/FT-FACTOR-03/{out,user_data}，不重跑回测、不修改对方产物。
输出：out/18_PV变体批次锁定_v1.json
      out/19_PV变体逐笔重算与自报对照_v1.csv
      out/20_PV变体过滤归因_v1.csv
      out/21_PV变体极值与稳健性_v1.csv
      out/22_PV变体年度切片与并发_v1.csv

方法学要点：这一批 4 个组"统一关闭 OI"，变量严格单一，是本轮唯一能干净隔离
价量因子的对照；但它同时把 OI 也关掉了，所以不能用来评价 F_OI。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

csv.field_size_limit(10 ** 9)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(BASE))
AG3 = os.path.join(ROOT, "evidence", "FT-FACTOR-03")
OUT = os.path.join(BASE, "out")

MAX_OPEN = 3
# (显示名, 策略类名, 对应被移除的因子)
ARMS = [
    ("PV基线 (关闭OI+完整价量)", "WyckoffPVVariant_Baseline", "—"),
    ("PV No BreakoutVol", "WyckoffPVVariant_NoBreakoutVol", "F_VOL_BO"),
    ("PV No RetestVol", "WyckoffPVVariant_NoRetestVol", "F_VOL_RT"),
    ("PV No GapFilter", "WyckoffPVVariant_NoGapFilter", "F_GAP_IN"),
]


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d * 100, (c + r) / d * 100)


def load_trades(path):
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["pair"] = r.get("pair") or r.get("\ufeffpair")
        r["pnl"] = float(r["profit_abs"])
        r["pr"] = float(r["profit_ratio"])
        r["o"] = int(r["open_timestamp"])
        r["c"] = int(r["close_timestamp"])
    return rows


def dd_variants(trades):
    """(真实平仓权益回撤, 剔除首笔后的回撤)。"""
    def calc(seq):
        eq = peak = mdd = 0.0
        for t in sorted(seq, key=lambda x: x["c"]):
            eq += t["pnl"]
            peak = max(peak, eq)
            mdd = max(mdd, peak - eq)
        return mdd

    return calc(trades), (calc(trades[1:]) if len(trades) > 1 else 0.0)


def metrics(trades):
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 1e-12]
    losses = [t for t in trades if t["pnl"] < -1e-12]
    net = sum(t["pnl"] for t in trades)
    gp = sum(t["pnl"] for t in wins)
    gl = sum(t["pnl"] for t in losses)
    mdd, mdd_x = dd_variants(trades)
    lo, hi = wilson(len(wins), n)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "flats": n - len(wins) - len(losses),
        "win_rate_pct": round(len(wins) / n * 100, 4) if n else None,
        "wilson95_low_pct": round(lo, 4) if n else None,
        "wilson95_high_pct": round(hi, 4) if n else None,
        "net_u": round(net, 6),
        "profit_factor": round(gp / abs(gl), 6) if gl else None,
        "avg_win_u": round(gp / len(wins), 6) if wins else None,
        "avg_loss_u": round(gl / len(losses), 6) if losses else None,
        "expectancy_u": round(net / n, 6) if n else None,
        "dd_true_u": round(mdd, 6),
        "dd_excl_first_u": round(mdd_x, 6),
        "max_win_u": round(max((t["pnl"] for t in trades), default=0.0), 6),
        "max_loss_u": round(min((t["pnl"] for t in trades), default=0.0), 6),
    }


def concurrency_at(trades, ts):
    return sum(1 for t in trades if t["o"] < ts <= t["c"])


def max_concurrency(trades):
    ev = []
    for t in trades:
        ev.append((t["o"], 1))
        ev.append((t["c"], -1))
    ev.sort()
    cur = mx = 0
    for _, d in ev:
        cur += d
        mx = max(mx, cur)
    return mx


def ts_utc(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x > 1e11:
        x /= 1000.0
    try:
        return datetime.fromtimestamp(x, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError):
        return ""


def main():
    os.makedirs(OUT, exist_ok=True)
    sdir = os.path.join(AG3, "user_data", "strategies")
    disk_hash = {}
    for fn in sorted(os.listdir(sdir)):
        if fn.endswith(".py"):
            disk_hash[fn] = sha256_file(os.path.join(sdir, fn))

    # ---------- 1. 批次锁定 + 归档/CSV 一致性 ----------
    lock = {
        "work_order": "FT-FACTOR-AUDIT-01",
        "batch": "WyckoffPVVariant_*（Full74，统一关闭 OI 的价量研究变体）",
        "arrived_after_audit_start": True,
        "arrival_note": "逐笔 CSV 19:45~19:52 落盘，报告与 versions.json 19:53 落盘；"
                        "本审计 19:32 启动时该批不存在，原报告'无报告、不纳入'的表述已被本批推翻，故补做核对。",
        "artifacts": [],
    }
    data = {}
    for label, strat, factor in ARMS:
        zp = os.path.join(AG3, "out", f"backtest-result-Full74-{strat}.zip")
        cp = os.path.join(AG3, "out", f"trades_Full74_{strat}.csv")
        rec = {"strategy": strat, "removed_factor": factor, "zip": os.path.basename(zp),
               "csv": os.path.basename(cp), "zip_sha256": sha256_file(zp) if os.path.exists(zp) else "",
               "csv_sha256": sha256_file(cp) if os.path.exists(cp) else ""}
        if os.path.exists(zp):
            z = zipfile.ZipFile(zp)
            names = z.namelist()
            jname = next((n for n in names if n.endswith(".json") and "_config" not in n), None)
            sname = next((n for n in names if n.endswith(".py")), None)
            d = json.loads(z.read(jname))
            sk = list(d["strategy"].keys())[0]
            zt = d["strategy"][sk].get("trades", [])
            inner = sha256_bytes(z.read(sname)) if sname else ""
            rec.update({
                "run_artifact_id": jname.replace(".json", ""),
                "strategy_in_zip": sk,
                "trades_in_archive": len(zt),
                "strategy_py_in_zip_sha256": inner,
                "strategy_py_on_disk_sha256": disk_hash.get(f"{sk}.py", ""),
                "strategy_py_match": inner == disk_hash.get(f"{sk}.py", ""),
                "backtest_start_utc": ts_utc(d["strategy"][sk].get("backtest_start_ts")),
                "backtest_end_utc": ts_utc(d["strategy"][sk].get("backtest_end_ts")),
                "timeframe": d["strategy"][sk].get("timeframe"),
                "max_open_trades": d["strategy"][sk].get("max_open_trades"),
                "fee": d["strategy"][sk].get("fee"),
            })
            csv_rows = load_trades(cp) if os.path.exists(cp) else []
            data[label] = csv_rows
            zset = sorted((t["pair"], int(t["open_timestamp"]), round(float(t["profit_abs"]), 8)) for t in zt)
            cset = sorted((t["pair"], t["o"], round(t["pnl"], 8)) for t in csv_rows)
            rec.update({"zip_vs_csv_identical": zset == cset,
                        "only_in_zip": len(set(zset) - set(cset)),
                        "only_in_csv": len(set(cset) - set(zset))})
        else:
            data[label] = []
        lock["artifacts"].append(rec)

    # 交叉校验：PV 基线应与 WyckoffAblationNoOI 完全同源（同一份 OI 关闭价量基线）
    no_oi = load_trades(os.path.join(AG3, "out", "trades_ExpandedAll74_WyckoffAblationNoOI.csv"))
    pv_base = data["PV基线 (关闭OI+完整价量)"]
    lock["crosscheck_pv_baseline_vs_ablationNoOI"] = {
        "ablationNoOI_trades": len(no_oi),
        "pv_baseline_trades": len(pv_base),
        "same_trade_set": sorted((t["pair"], t["o"], round(t["pnl"], 8)) for t in no_oi)
                         == sorted((t["pair"], t["o"], round(t["pnl"], 8)) for t in pv_base),
        "ablationNoOI_net_u": round(sum(t["pnl"] for t in no_oi), 6),
        "pv_baseline_net_u": round(sum(t["pnl"] for t in pv_base), 6),
    }

    with open(os.path.join(OUT, "18_PV变体批次锁定_v1.json"), "w", encoding="utf-8") as fh:
        json.dump(lock, fh, ensure_ascii=False, indent=2)

    # ---------- 2. 重算 vs 自报 ----------
    ag07 = {}
    p07 = os.path.join(AG3, "out", "07_基础价量研究变体执行与逐笔清单.csv")
    if os.path.exists(p07):
        with open(p07, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                ag07[r["strategy_name"]] = r

    rows = []
    for label, strat, factor in ARMS:
        m = metrics(data[label])
        a = ag07.get(strat, {})
        rows.append({
            "strategy_group": label,
            "removed_factor": factor,
            "trades_mine": m["trades"],
            "trades_ag": a.get("trade_count", ""),
            "trades_match": str(m["trades"]) == str(a.get("trade_count", "")),
            "wl_mine": f"{m['wins']}/{m['losses']}/{m['flats']}",
            "wl_ag": f"{a.get('win_count','')}/{a.get('loss_count','')}/0" if a else "",
            "winrate_mine": m["win_rate_pct"],
            "wilson_mine": f"[{m['wilson95_low_pct']}, {m['wilson95_high_pct']}]",
            "net_mine": m["net_u"],
            "net_ag": a.get("net_pnl_u", ""),
            "net_diff": (round(m["net_u"] - float(a["net_pnl_u"]), 6) if a.get("net_pnl_u") else ""),
            "pf_mine": m["profit_factor"],
            "avg_win_mine": m["avg_win_u"],
            "avg_loss_mine": m["avg_loss_u"],
            "expectancy_mine": m["expectancy_u"],
            "dd_true_mine": m["dd_true_u"],
            "dd_excl_first_mine": m["dd_excl_first_u"],
            "dd_ag": a.get("dd_u", "未披露"),
            "max_win_mine": m["max_win_u"],
            "max_loss_mine": m["max_loss_u"],
            "max_concurrent_mine": max_concurrency(data[label]),
            "hit_max_open_trades": max_concurrency(data[label]) >= MAX_OPEN,
        })
    with open(os.path.join(OUT, "19_PV变体逐笔重算与自报对照_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # ---------- 3. 过滤归因（基线 = PV 基线，按候选 ID enter_tag 匹配）----------
    base = data["PV基线 (关闭OI+完整价量)"]
    bset = {t["enter_tag"]: t for t in base}
    bpair = {(t["pair"], t["o"]) for t in base}
    attr = []
    detail = []
    for label, strat, factor in ARMS[1:]:
        abl = data[label]
        aset = {t["enter_tag"]: t for t in abl}
        filtered = [t for k, t in aset.items() if k not in bset]
        missing = [t for k, t in bset.items() if k not in aset]
        noise = [t for t in filtered if t["pnl"] < -1e-12]
        opp = [t for t in filtered if t["pnl"] > 1e-12]
        common = [k for k in aset if k in bset]
        diffs = [aset[k]["pnl"] - bset[k]["pnl"] for k in common]
        shifts = [k for k in common if aset[k]["o"] != bset[k]["o"]]
        attr.append({
            "removed_factor": factor,
            "strategy_group": label,
            "baseline_trades": len(base),
            "ablation_trades": len(abl),
            "common_trades": len(common),
            "filtered_out_count": len(filtered),
            "filtered_out_count_by_pair_open_ts": sum(1 for t in abl if (t["pair"], t["o"]) not in bpair),
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
                for k in shifts),
            "slot_blocked_trades": sum(1 for t in filtered if concurrency_at(abl, t["o"]) >= MAX_OPEN),
            "attribution_status": "可逐笔对应" if not missing else f"{len(missing)} 笔无法归因（基线独有）",
        })
        for t in filtered:
            detail.append({"strategy_group": label, "removed_factor": factor, "kind": "被过滤",
                           "pair": t["pair"], "enter_tag": t["enter_tag"],
                           "open_utc": datetime.fromtimestamp(t["o"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                           "close_utc": datetime.fromtimestamp(t["c"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                           "pnl_u": round(t["pnl"], 6),
                           "class": "拦截亏损噪音" if t["pnl"] < 0 else "误伤盈利机会",
                           "concurrency_at_open": concurrency_at(abl, t["o"])})
        for t in missing:
            detail.append({"strategy_group": label, "removed_factor": factor, "kind": "基线独有",
                           "pair": t["pair"], "enter_tag": t["enter_tag"],
                           "open_utc": datetime.fromtimestamp(t["o"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                           "close_utc": datetime.fromtimestamp(t["c"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                           "pnl_u": round(t["pnl"], 6), "class": "无法归因",
                           "concurrency_at_open": concurrency_at(base, t["o"])})
    with open(os.path.join(OUT, "20_PV变体过滤归因_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(attr[0].keys()))
        w.writeheader()
        for r in attr:
            w.writerow(r)
    with open(os.path.join(OUT, "20b_PV变体逐笔差异明细_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(detail[0].keys()))
        w.writeheader()
        for r in detail:
            w.writerow(r)

    # ---------- 4. 极值剔除 ----------
    ext = []
    for label, strat, factor in ARMS:
        ts = data[label]
        net = sum(t["pnl"] for t in ts)
        mx = max(ts, key=lambda t: t["pnl"]) if ts else None
        by_sym = defaultdict(float)
        for t in ts:
            by_sym[t["pair"]] += t["pnl"]
        top = max(by_sym.items(), key=lambda x: x[1]) if by_sym else ("", 0.0)
        ext.append({
            "strategy_group": label,
            "removed_factor": factor,
            "net_all_u": round(net, 6),
            "max_win_u": round(mx["pnl"], 6) if mx else 0.0,
            "max_win_pair": mx["pair"] if mx else "",
            "net_ex_max_win_u": round(net - mx["pnl"], 6) if mx else round(net, 6),
            "top_symbol": top[0],
            "top_symbol_pnl_u": round(top[1], 6),
            "net_ex_top_symbol_u": round(net - top[1], 6),
            "trades": len(ts),
        })
    with open(os.path.join(OUT, "21_PV变体极值与稳健性_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ext[0].keys()))
        w.writeheader()
        for r in ext:
            w.writerow(r)

    # ---------- 5. 年度切片 + 置换检验 + Holm ----------
    rnd = random.Random(20261020)

    def perm_p(a, b, reps=2000):
        va = [t["pnl"] for t in a]
        vb = [t["pnl"] for t in b]
        obs = sum(va) - sum(vb)
        pool = va + vb
        cnt = 0
        for _ in range(reps):
            rnd.shuffle(pool)
            if abs(sum(pool[: len(va)]) - sum(pool[len(va):])) >= abs(obs) - 1e-12:
                cnt += 1
        return (cnt + 1) / (reps + 1)

    yrs = []
    for label, strat, factor in ARMS:
        by_open = defaultdict(list)
        by_close = defaultdict(list)
        for t in data[label]:
            by_open[datetime.fromtimestamp(t["o"] / 1000, tz=timezone.utc).strftime("%Y")].append(t)
            by_close[datetime.fromtimestamp(t["c"] / 1000, tz=timezone.utc).strftime("%Y")].append(t)
        for basis, d in (("按开仓时间", by_open), ("按平仓时间", by_close)):
            for y, ts in sorted(d.items()):
                yrs.append({"strategy_group": label, "year_basis": basis, "year": y,
                            "trades": len(ts),
                            "wins": sum(1 for t in ts if t["pnl"] > 1e-12),
                            "net_u": round(sum(t["pnl"] for t in ts), 6),
                            "expectancy_u": round(sum(t["pnl"] for t in ts) / len(ts), 6),
                            "max_concurrent_in_year": max_concurrency(ts)})
    with open(os.path.join(OUT, "22_PV变体年度切片与并发_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(yrs[0].keys()))
        w.writeheader()
        for r in yrs:
            w.writerow(r)

    stat = []
    for label, strat, factor in ARMS[1:]:
        abl = data[label]
        stat.append({
            "removed_factor": factor,
            "comparison": f"PV基线 vs {label}",
            "raw_net_diff_u": round(sum(t["pnl"] for t in base) - sum(t["pnl"] for t in abl), 6),
            "perm_p_trade_level": round(perm_p(base, abl), 4),
        })
    order = sorted(range(len(stat)), key=lambda i: stat[i]["perm_p_trade_level"])
    m = len(order)
    prev = 0.0
    for rank, i in enumerate(order):
        adj = max(min(1.0, (m - rank) * stat[i]["perm_p_trade_level"]), prev)
        prev = adj
        stat[i]["holm_adj_p"] = round(adj, 4)
    for r in stat:
        r["holm_scope"] = f"仅本批 {m} 个价量消融；与前一批 4 个消融同属一个因子家族，合并应为 {m + 4} 次"
    with open(os.path.join(OUT, "22b_PV变体置换检验与Holm_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(stat[0].keys()))
        w.writeheader()
        for r in stat:
            w.writerow(r)

    # ---------- 6. 摘要 ----------
    print("=== 批次锁定 ===")
    label_of = {s: l for l, s, _ in ARMS}
    for a in lock["artifacts"]:
        print(f"  {a['strategy']:34s} zip={a.get('trades_in_archive')} csv={len(data[label_of[a['strategy']]])} "
              f"identical={a.get('zip_vs_csv_identical')} py_match={a.get('strategy_py_match')} "
              f"window={a.get('backtest_start_utc')}~{a.get('backtest_end_utc')} max_open={a.get('max_open_trades')}")
    print("=== PV 基线 vs WyckoffAblationNoOI 同源校验 ===")
    print("  ", lock["crosscheck_pv_baseline_vs_ablationNoOI"])
    print("=== 重算 vs 自报 ===")
    for r in rows:
        print(f"  {r['strategy_group']:26s} n={r['trades_mine']:3d}/{r['trades_ag']:<3} net={r['net_mine']:>8.2f}/{r['net_ag']:<8} "
              f"PF={r['pf_mine']} exp={r['expectancy_mine']:+.3f} dd_true={r['dd_true_mine']:.3f} dd_x1={r['dd_excl_first_mine']:.3f} "
              f"maxconc={r['max_concurrent_mine']} hit={r['hit_max_open_trades']}")
    print("=== 过滤归因（基线=PV基线）===")
    for r in attr:
        print(f"  {r['removed_factor']:12s} filtered={r['filtered_out_count']:2d} (pair口径={r['filtered_out_count_by_pair_open_ts']:2d}) "
              f"noise={r['noise_count']}/{r['noise_saved_u']:.2f} opp={r['opportunity_count']}/{r['opportunity_missed_u']:.2f} "
              f"gain={r['net_filter_gain_u']:+.2f} base_only={r['baseline_only_count']} "
              f"slotblocked={r['slot_blocked_trades']} shift={r['same_candidate_fill_shift_count']}")
    print("=== 极值剔除 ===")
    for r in ext:
        print(f"  {r['strategy_group']:26s} net={r['net_all_u']:+.2f} max={r['max_win_u']:+.2f}({r['max_win_pair']}) "
              f"ex_max={r['net_ex_max_win_u']:+.2f} top={r['top_symbol']}({r['top_symbol_pnl_u']:+.2f}) ex_top={r['net_ex_top_symbol_u']:+.2f}")
    print("=== 置换检验 ===")
    for r in stat:
        print(f"  {r['removed_factor']:12s} raw={r['raw_net_diff_u']:+.2f} p={r['perm_p_trade_level']} holm={r['holm_adj_p']}")


if __name__ == "__main__":
    main()

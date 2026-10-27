# -*- coding: utf-8 -*-
"""FT-EXIT-AUDIT-01 / 05：分别评价「结构止损」与「20% 止损」

四套方案统一 R 口径（R = 盈亏 / 开仓时权益 × 0.5%）：
  C0  FT-ENTRY-09  结构P0 + 240h超时 + 2R跟踪（参照）
  C1  FT-STOP-20-01 固定20% + 240h超时 + 2R跟踪（参照）
  S   FT-EXIT-01    结构P0 + 仅初始止损
  P   FT-EXIT-01    固定20% + 仅初始止损

硬约束（用户要求）：
  · 不把「少卖出」当改善 —— 未平仓的浮盈浮亏单列，不计入已实现
  · 不把「已平仓胜率变化」当改善 —— 仅止损方案的已平仓胜率必然趋 0，不可比
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = ROOT / "evidence/FT-EXIT-01"
OUT = ROOT / "evidence/FT-EXIT-AUDIT-01/out"
SUF = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
INIT, RISK = 2000.0, 0.005


def read_csv(p: Path):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_pat(pat: str, gfun):
    D = {}
    for p in sorted(ROOT.glob(pat)):
        g = gfun(p)
        rows = read_csv(p)
        if not rows:
            continue
        cl = sorted((int(r["close_timestamp"]), float(r["profit_abs"])) for r in rows)
        for r in rows:
            o = int(r["open_timestamp"])
            eq = INIT + sum(pa for ct, pa in cl if ct <= o)
            D.setdefault(g, []).append({
                "pair": r["pair"], "pnl": float(r["profit_abs"]),
                "R": float(r["profit_abs"]) / (eq * RISK), "eq": eq,
                "exit": r["exit_reason"], "dur_h": float(r["trade_duration"]) / 60.0,
                "o": o, "c": int(r["close_timestamp"]),
                "amt": float(r["amount"]), "entry": float(r["open_rate"]),
                "stop": float(r["stop_loss_abs"]),
                "fund": float(r["funding_fees"]) if r.get("funding_fees") not in (None, "") else 0.0,
            })
    return D


SETS = {
    "C0": load_pat("evidence/FT-ENTRY-09/out/trades_Wyckoff_C1_Vs*.csv",
                   lambda p: "C1_" + p.stem.replace("trades_Wyckoff_C1_", "")),
    "C1": load_pat("evidence/FT-STOP-20-01/out/trades_Wyckoff_C1_Stop20_*.csv",
                   lambda p: "C1_" + p.stem.replace("trades_Wyckoff_C1_Stop20_", "")),
    "S": load_pat("evidence/FT-EXIT-01/out/trades_Wyckoff_C1_S_P0_*.csv",
                  lambda p: "C1_" + p.stem.replace("trades_Wyckoff_C1_S_P0_", "")),
    "P": load_pat("evidence/FT-EXIT-01/out/trades_Wyckoff_C1_P_Stop20_*.csv",
                  lambda p: "C1_" + p.stem.replace("trades_Wyckoff_C1_P_Stop20_", "")),
}
NAME = {"C0": "C0 结构P0+超时+跟踪", "C1": "C1 20%+超时+跟踪",
        "S": "S 结构P0 仅止损", "P": "P 20% 仅止损"}


def mdd_group(rows):
    """单组回撤：每组从 2000 起，各自一条权益曲线。"""
    eq, peak, m = INIT, INIT, 0.0
    for r in sorted(rows, key=lambda x: x["c"]):
        eq += r["pnl"]
        peak = max(peak, eq)
        m = max(m, (peak - eq) / peak if peak > 0 else 0)
    return m * 100


def mdd(rows, groups=None):
    """返回 (逐组回撤中位, 逐组回撤最差)；groups 为 {组名: [trades]}。"""
    if not groups:
        return round(mdd_group(rows), 2), round(mdd_group(rows), 2)
    vals = sorted(mdd_group(v) for v in groups.values() if v)
    if not vals:
        return None, None
    return round(vals[len(vals) // 2], 2), round(vals[-1], 2)


def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
    return xs[i]


R = {}
print("=" * 118)
print("【1】统一 R 口径总表（四方案）")
print("=" * 118)
print(f"{'方案':<22}{'笔数':>5}{'已实现R':>10}{'未实现R':>10}{'总R':>10}{'平均R':>9}"
      f"{'已实现净额':>12}{'未实现净额':>12}{'回撤中位%':>10}{'持仓中位h':>10}{'止损距价%中位':>12}")
for k in ("C0", "C1", "S", "P"):
    rows = [r for g in SETS[k] for r in SETS[k][g]]
    if not rows:
        print(f"{k:<22}  无数据")
        continue
    closed = [r for r in rows if r["exit"] != "force_exit"]
    still = [r for r in rows if r["exit"] == "force_exit"]
    devs = sorted((r["stop"] - r["entry"]) / r["entry"] * 100 for r in rows if r["entry"])
    durs = sorted(r["dur_h"] for r in rows)
    d = {
        "笔数": len(rows),
        "已实现R": round(sum(r["R"] for r in closed), 2),
        "未实现R": round(sum(r["R"] for r in still), 2),
        "总R": round(sum(r["R"] for r in rows), 2),
        "平均R": round(sum(r["R"] for r in rows) / len(rows), 4),
        "已实现净额": round(sum(r["pnl"] for r in closed), 2),
        "未实现净额": round(sum(r["pnl"] for r in still), 2),
        "全口径净额": round(sum(r["pnl"] for r in rows), 2),
        "最大回撤_中位%": mdd(rows, SETS[k])[0],
        "最大回撤_最差%": mdd(rows, SETS[k])[1],
        "持仓中位h": round(durs[len(durs) // 2], 1),
        "止损距价%中位": round(devs[len(devs) // 2], 3),
        "已平仓笔数": len(closed), "未平仓笔数": len(still),
        "已平仓胜率%": round(sum(1 for r in closed if r["pnl"] > 0) / len(closed) * 100, 2) if closed else None,
        "全口径胜率%": round(sum(1 for r in rows if r["pnl"] > 0) / len(rows) * 100, 2),
        "资金费合计": round(sum(r["fund"] for r in rows), 4),
        "退出结构": dict(Counter(r["exit"] for r in rows)),
    }
    R[k] = d
    print(f"{NAME[k]:<22}{d['笔数']:>5}{d['已实现R']:>10.2f}{d['未实现R']:>10.2f}"
          f"{d['总R']:>10.2f}{d['平均R']:>9.4f}{d['已实现净额']:>12.2f}{d['未实现净额']:>12.2f}"
          f"{str(d['最大回撤_中位%']):>10}{d['持仓中位h']:>10.1f}{d['止损距价%中位']:>12.3f}")

print()
print("=" * 118)
print("【2】已实现 / 未实现 分解（未实现不是真钱，单列）")
print("=" * 118)
print(f"  {'方案':<22}{'笔数':>5}{'已平仓':>7}{'未平仓':>7}{'未平仓占比%':>12}"
      f"{'已实现R':>10}{'未实现R':>10}{'已平仓胜率%':>12}{'全口径胜率%':>12}")
for k in ("C0", "C1", "S", "P"):
    d = R.get(k)
    if not d:
        continue
    print(f"  {NAME[k]:<22}{d['笔数']:>5}{d['已平仓笔数']:>7}{d['未平仓笔数']:>7}"
          f"{d['未平仓笔数']/d['笔数']*100:>12.1f}{d['已实现R']:>10.2f}{d['未实现R']:>10.2f}"
          f"{str(d['已平仓胜率%']):>12}{d['全口径胜率%']:>12.2f}")

print()
print("=" * 118)
print("【3】每笔 R 分布（左尾越厚越危险）")
print("=" * 118)
print(f"  {'方案':<22}{'P1':>9}{'P5':>9}{'中位':>9}{'P95':>9}{'最差':>9}{'最好':>9}"
      f"{'亏损笔数':>9}{'亏损R合计':>11}")
for k in ("C0", "C1", "S", "P"):
    rows = [r for g in SETS[k] for r in SETS[k][g]]
    if not rows:
        continue
    Rs = [r["R"] for r in rows]
    R.setdefault(k, {})["R_P1"] = round(q(Rs, .01), 3)
    R[k]["R_P5"] = round(q(Rs, .05), 3)
    R[k]["R_中位"] = round(q(Rs, .5), 3)
    R[k]["R_P95"] = round(q(Rs, .95), 3)
    R[k]["R_最差"] = round(min(Rs), 3)
    R[k]["R_最好"] = round(max(Rs), 3)
    R[k]["亏损笔数"] = sum(1 for x in Rs if x < 0)
    R[k]["亏损R合计"] = round(sum(x for x in Rs if x < 0), 2)
    print(f"  {NAME[k]:<22}{q(Rs,.01):>9.3f}{q(Rs,.05):>9.3f}{q(Rs,.5):>9.3f}"
          f"{q(Rs,.95):>9.3f}{min(Rs):>9.3f}{max(Rs):>9.3f}"
          f"{R[k]['亏损笔数']:>9}{R[k]['亏损R合计']:>11.2f}")

print()
print("=" * 118)
print("【4】期末未平仓集中度（判断「全口径净额」是否靠少数浮盈撑着）")
print("=" * 118)
for k in ("S", "P"):
    rows = [r for g in SETS[k] for r in SETS[k][g] if r["exit"] == "force_exit"]
    if not rows:
        print(f"  {NAME[k]}：无期末未平仓")
        continue
    rows.sort(key=lambda r: -r["pnl"])
    tot = sum(r["pnl"] for r in rows)
    top5 = sum(r["pnl"] for r in rows[:5])
    print(f"  {NAME[k]}：{len(rows)} 笔未平仓，未实现净额 {tot:+.2f} USDT")
    print(f"    前 5 笔贡献 {top5:+.2f}（占 {top5/tot*100 if tot else 0:.1f}%）；"
          f"持仓中位 {q([r['dur_h'] for r in rows],.5):.0f}h，最长 {max(r['dur_h'] for r in rows):.0f}h")
    for r in rows[:5]:
        print(f"      {r['pair']:<16} 浮盈 {r['pnl']:>9.2f}  持仓 {r['dur_h']:>8.0f}h  R={r['R']:>7.3f}")
    R.setdefault(k, {})["未平仓集中度"] = {
        "笔数": len(rows), "未实现净额": round(tot, 2),
        "前5贡献": round(top5, 2), "前5占比%": round(top5 / tot * 100, 1) if tot else None,
        "持仓中位h": round(q([r["dur_h"] for r in rows], .5), 1)}

print()
print("=" * 118)
print("【5】机制对照：止损距离 / 仓位 / 名义 / 每笔亏损")
print("=" * 118)
print(f"  {'方案':<22}{'止损距价%中位':>13}{'名义占权益%中位':>15}{'每笔风险%中位':>13}"
      f"{'每笔已实现R均值':>15}{'每笔亏损USDT中位':>16}")
for k in ("C0", "C1", "S", "P"):
    rows = [r for g in SETS[k] for r in SETS[k][g]]
    if not rows:
        continue
    devs = sorted((r["stop"] - r["entry"]) / r["entry"] * 100 for r in rows if r["entry"])
    notl = sorted(r["amt"] * r["entry"] / r["eq"] * 100 for r in rows)
    risk = sorted(r["amt"] * (r["entry"] - r["stop"]) / r["eq"] * 100 for r in rows)
    closed = [r for r in rows if r["exit"] != "force_exit"]
    losses = sorted(abs(r["pnl"]) for r in rows if r["pnl"] < 0)
    d = R.setdefault(k, {})
    d["名义占权益%中位"] = round(notl[len(notl) // 2], 2)
    d["每笔风险%中位"] = round(risk[len(risk) // 2], 4)
    d["每笔已实现R均值"] = round(sum(r["R"] for r in closed) / len(closed), 4) if closed else None
    d["每笔亏损USDT中位"] = round(losses[len(losses) // 2], 3) if losses else None
    print(f"  {NAME[k]:<22}{devs[len(devs)//2]:>13.3f}{notl[len(notl)//2]:>15.2f}"
          f"{risk[len(risk)//2]:>13.4f}{str(d['每笔已实现R均值']):>15}{str(d['每笔亏损USDT中位']):>16}")

print()
print("=" * 118)
print("【6】13 个季度总 R（看是否只在个别时段成立）")
print("=" * 118)
import datetime as dt


def qq(ts):
    x = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc)
    return f"{x.year}Q{(x.month - 1) // 3 + 1}"


qs = sorted({qq(r["c"]) for k in SETS for g in SETS[k] for r in SETS[k][g]})
tbl = defaultdict(dict)
for k in ("C0", "C1", "S", "P"):
    acc = defaultdict(float)
    for g in SETS[k]:
        for r in SETS[k][g]:
            acc[qq(r["c"])] += r["R"]
    for q in qs:
        tbl[q][k] = round(acc.get(q, 0.0), 2)
print(f"  {'季度':<9}{'C0':>9}{'C1':>9}{'S':>9}{'P':>9}")
for q in qs:
    print(f"  {q:<9}{tbl[q]['C0']:>9.2f}{tbl[q]['C1']:>9.2f}{tbl[q]['S']:>9.2f}{tbl[q]['P']:>9.2f}")
R["_季度"] = {q: tbl[q] for q in qs}


print()
print("=" * 118)
print("【7】实际亏损超出 0.5% 风险预算的分布（跳空穿透止损，不是计划风险）")
print("=" * 118)
print(f"  {'方案':<22}{'亏损笔数':>8}{'亏>1.0R':>9}{'亏>1.5R':>9}{'亏>2R':>8}{'亏>5R':>8}"
      f"{'最差R':>9}{'最差占权益%':>12}{'亏损R合计':>11}")
for k in ("C0", "C1", "S", "P"):
    rows = [r for g in SETS[k] for r in SETS[k][g]]
    if not rows:
        continue
    Rs = [r["R"] for r in rows]
    loss = [x for x in Rs if x < 0]
    worst = min(rows, key=lambda r: r["R"])
    d = R.setdefault(k, {})
    d["亏>1.0R"] = sum(1 for x in loss if x < -1.0)
    d["亏>1.5R"] = sum(1 for x in loss if x < -1.5)
    d["亏>2R"] = sum(1 for x in loss if x < -2.0)
    d["亏>5R"] = sum(1 for x in loss if x < -5.0)
    d["最差单笔占权益%"] = round(worst["pnl"] / worst["eq"] * 100, 3)
    print(f"  {NAME[k]:<22}{len(loss):>8}{d['亏>1.0R']:>9}{d['亏>1.5R']:>9}{d['亏>2R']:>8}"
          f"{d['亏>5R']:>8}{min(Rs):>9.3f}{d['最差单笔占权益%']:>12.3f}{sum(loss):>11.2f}")

print()
print("=" * 118)
print("【8】把「少卖出」剥离：同笔数折算（每笔口径 vs 笔数口径）")
print("=" * 118)
base = R.get("S", {})
if base:
    for k in ("S", "P"):
        d = R.get(k)
        if not d:
            continue
        per = d["已实现R"] / d["已平仓笔数"] if d["已平仓笔数"] else None
        d["每笔已实现R"] = round(per, 4) if per is not None else None
    nS = R["S"]["已平仓笔数"]
    perS = R["S"]["每笔已实现R"]
    perP = R["P"]["每笔已实现R"]
    print(f"  S：{nS} 笔已平仓，每笔 {perS:+.4f}R → 合计 {R['S']['已实现R']:+.2f}R")
    print(f"  P：{R['P']['已平仓笔数']} 笔已平仓，每笔 {perP:+.4f}R → 合计 {R['P']['已实现R']:+.2f}R")
    print(f"  差额 {R['P']['已实现R'] - R['S']['已实现R']:+.2f}R 中：")
    print(f"    · 每笔幅度差异贡献 (每笔差 × S 的笔数) = ({perP - perS:+.4f}) × {nS} = "
          f"{(perP - perS) * nS:+.2f}R")
    print(f"    · 笔数差异贡献 (P 少的笔数 × P 的每笔) = ({nS - R['P']['已平仓笔数']}) × ({perP:+.4f}) = "
          f"{(nS - R['P']['已平仓笔数']) * perP:+.2f}R")
    R["_同笔数折算"] = {"S笔数": nS, "S每笔R": perS, "P笔数": R["P"]["已平仓笔数"], "P每笔R": perP,
                   "每笔差异贡献": round((perP - perS) * nS, 2),
                   "笔数差异贡献": round((nS - R["P"]["已平仓笔数"]) * perP, 2)}

(OUT / "05_评价与对照.json").write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8")
print(f"\n写出 {OUT/'05_评价与对照.json'}")

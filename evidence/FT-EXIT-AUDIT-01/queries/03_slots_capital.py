# -*- coding: utf-8 -*-
"""FT-EXIT-AUDIT-01 / 03：长期持仓是否真实占用资金与槽位

三件事：
 1. 时间加权：任意时刻被占用的仓位槽位数、被锁定的名义资金额
 2. 长期持仓（>240h / >720h）各自占掉多少「槽位小时」和「资金·小时」
 3. 槽位挤占：确认表里 CONFIRMED 的候选有多少最终没成交
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = ROOT / "evidence/FT-EXIT-01"
OUT = ROOT / "evidence/FT-EXIT-AUDIT-01/out"
SUF = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
SCHEMES = {"S": "Wyckoff_C1_S_P0", "P": "Wyckoff_C1_P_Stop20"}
INIT = 2000.0
MAXSLOT = 3


def read_csv(p: Path):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def eid(tag: str) -> str:
    m = re.search(r"(ev_FT_ENTRY_\d+_binance_[A-Z0-9]+_\d+)", tag or "")
    return m.group(1) if m else (tag or "")


def load(scheme: str, suf: str):
    p = EXP / f"out/trades_{SCHEMES[scheme]}_{suf}.csv"
    if not p.exists():
        return None
    rows = read_csv(p)
    if not rows:
        return []
    cl = sorted((int(r["close_timestamp"]), float(r["profit_abs"])) for r in rows)
    for r in rows:
        o = int(r["open_timestamp"])
        r["_eq"] = INIT + sum(pa for ct, pa in cl if ct <= o)
        r["_o"] = o
        r["_c"] = int(r["close_timestamp"])
        r["_amt"] = float(r["amount"])
        r["_entry"] = float(r["open_rate"])
        r["_stake"] = float(r["stake_amount"])
        r["_notional"] = r["_amt"] * r["_entry"]
        r["_dur_h"] = float(r["trade_duration"]) / 60.0
        r["_pnl"] = float(r["profit_abs"])
    return rows


def confirmed_count(scheme: str, suf: str):
    p = EXP / f"out/audit_confirmation_{SCHEMES[scheme]}_{suf}.csv"
    if not p.exists():
        return None, None
    c = Counter()
    for r in read_csv(p):
        c[r.get("decision", "?")] += 1
    return c.get("CONFIRMED", 0), dict(c)


R = {}
print("=" * 104)
print("【1】时间加权占用（每组独立回测，各自 3 个槽位、各自 2000 USDT）")
print("=" * 104)
print(f"{'方案':<3}{'组':<10}{'笔数':>5}{'峰值并发':>8}{'平均并发':>9}{'满仓时间%':>10}"
      f"{'平均占用资金':>12}{'峰值占用资金':>12}{'占用/权益%':>10}{'>240h笔':>8}{'>720h笔':>8}{'最长h':>9}")

for scheme in ("S", "P"):
    R[scheme] = {}
    for suf in SUF:
        rows = load(scheme, suf)
        if rows is None:
            print(f"{scheme:<3}{suf:<10}  缺产物")
            continue
        if not rows:
            print(f"{scheme:<3}{suf:<10}  空")
            continue

        # 事件点
        ev = []
        for r in rows:
            ev.append((r["_o"], +1, r["_notional"]))
            ev.append((r["_c"], -1, r["_notional"]))
        ev.sort(key=lambda x: (x[0], x[1]))
        span = ev[-1][0] - ev[0][0]

        cur_n = 0
        cur_cap = 0.0
        slot_area = 0.0
        cap_area = 0.0
        full_time = 0.0
        peak_n = 0
        peak_cap = 0.0
        prev = None
        for ts, d, notional in ev:
            if prev is not None:
                dt = ts - prev
                slot_area += cur_n * dt
                cap_area += cur_cap * dt
                if cur_n >= MAXSLOT:
                    full_time += dt
            cur_n += d
            cur_cap += d * notional
            peak_n = max(peak_n, cur_n)
            peak_cap = max(peak_cap, cur_cap)
            prev = ts

        avg_slot = slot_area / span if span else 0
        avg_cap = cap_area / span if span else 0
        # 时间加权平均权益（用于把资金占用换算成占权益比）
        eq_ev = sorted((r["_c"], r["_pnl"]) for r in rows)
        eq_area = 0.0
        prev = None
        eq = INIT
        i = 0
        # 简化：按每笔平仓时点推进
        last = ev[0][0]
        for ts, pnl in eq_ev:
            if ts > last:
                eq_area += eq * (ts - last)
                last = ts
            eq += pnl
        eq_area += eq * (ev[-1][0] - last) if ev[-1][0] > last else 0
        avg_eq = eq_area / span if span else INIT

        conf, conf_all = confirmed_count(scheme, suf)
        long240 = [r for r in rows if r["_dur_h"] > 240]
        long720 = [r for r in rows if r["_dur_h"] > 720]

        R[scheme][suf] = {
            "笔数": len(rows),
            "跨度天": round(span / 86400000, 1),
            "峰值并发": peak_n,
            "平均并发": round(avg_slot, 4),
            "满仓时间占比%": round(full_time / span * 100, 2) if span else None,
            "平均占用资金": round(avg_cap, 2),
            "峰值占用资金": round(peak_cap, 2),
            "平均占用/平均权益%": round(avg_cap / avg_eq * 100, 2) if avg_eq else None,
            "时间加权平均权益": round(avg_eq, 2),
            ">240h笔数": len(long240),
            ">720h笔数": len(long720),
            "最长持仓h": round(max(r["_dur_h"] for r in rows), 1),
            "槽位小时_合计": round(slot_area / 3600000, 1),
            "槽位小时_>240h笔贡献": round(sum(r["_dur_h"] for r in long240), 1),
            "资金小时_合计": round(cap_area / 3600000, 1),
            "资金小时_>240h笔贡献": round(sum(r["_dur_h"] * r["_notional"] for r in long240), 1),
            "确认CONFIRMED": conf,
            "确认表决策": conf_all,
            "成交/确认%": round(len(rows) / conf * 100, 1) if conf else None,
        }
        d = R[scheme][suf]
        print(f"{scheme:<3}{suf:<10}{d['笔数']:>5}{d['峰值并发']:>8}{d['平均并发']:>9.3f}"
              f"{d['满仓时间占比%']:>10.1f}{d['平均占用资金']:>12.1f}{d['峰值占用资金']:>12.1f}"
              f"{d['平均占用/平均权益%']:>10.1f}{d['>240h笔数']:>8}{d['>720h笔数']:>8}"
              f"{d['最长持仓h']:>9.0f}")

print()
print("=" * 104)
print("【2】长期持仓到底占了多少资源")
print("=" * 104)
for scheme in ("S", "P"):
    ok = [v for v in R[scheme].values() if "笔数" in v]
    if not ok:
        continue
    sh = sum(v["槽位小时_合计"] for v in ok)
    shl = sum(v["槽位小时_>240h笔贡献"] for v in ok)
    ch = sum(v["资金小时_合计"] for v in ok)
    chl = sum(v["资金小时_>240h笔贡献"] for v in ok)
    print(f"  {scheme} 组（{len(ok)}/9 组）")
    print(f"    槽位小时合计 {sh:>12.0f}   其中 >240h 持仓贡献 {shl:>12.0f}  ({shl/sh*100 if sh else 0:.1f}%)")
    print(f"    资金·小时合计 {ch:>12.0f}   其中 >240h 持仓贡献 {chl:>12.0f}  ({chl/ch*100 if ch else 0:.1f}%)")
    print(f"    平均并发 {sum(v['平均并发'] for v in ok)/len(ok):.3f} / 3 槽位；"
          f"满仓时间占比均值 {sum(v['满仓时间占比%'] for v in ok)/len(ok):.1f}%")
    print(f"    >240h 笔数合计 {sum(v['>240h笔数'] for v in ok)}，>720h 合计 {sum(v['>720h笔数'] for v in ok)}")

print()
print("=" * 104)
print("【3】槽位挤占：CONFIRMED 候选 vs 实际成交")
print("=" * 104)
print(f"  {'方案':<3}{'组':<10}{'CONFIRMED':>11}{'实际成交':>9}{'未成交':>8}{'成交率%':>9}  确认表决策")
for scheme in ("S", "P"):
    for suf in SUF:
        d = R[scheme].get(suf, {})
        if "笔数" not in d:
            continue
        conf = d["确认CONFIRMED"] or 0
        print(f"  {scheme:<3}{suf:<10}{conf:>11}{d['笔数']:>9}{conf - d['笔数']:>8}"
              f"{d['成交/确认%']:>9.1f}  {d['确认表决策']}")

(OUT / "03_资金与槽位.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n写出 {OUT/'03_资金与槽位.json'}")

# -*- coding: utf-8 -*-
"""FT-STOP-20-02-NOHOLD 逐笔验证与机制拆解（03）

回答四件事：
 1. A/B 两组是否真的没有 timeout_240h 退出（改动是否生效，是否被别处逻辑替代）
 2. A 组笔数 631→451 的机制：长期占仓锁死 3 个槽位
 3. 最大回撤 36.28%→61.12% 翻倍的成因
 4. 13 个季度时间块四方案对比
"""
import csv
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence/FT-STOP-20-02-NOHOLD/out"
INIT, RISK = 2000.0, 0.005
MATRIX = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
H = 3600 * 1000


def read_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def eid(tag):
    m = re.search(r"(ev_FT_ENTRY_\d+_binance_[A-Z0-9]+_\d+)", tag or "")
    return m.group(1) if m else (tag or "")


def load(pattern, gfun):
    D = {}
    for p in sorted(glob.glob(str(pattern))):
        g = gfun(p)
        rows = read_csv(p)
        if not rows:
            continue
        cl = sorted((int(r["close_timestamp"]), float(r["profit_abs"])) for r in rows)
        for r in rows:
            o = int(r["open_timestamp"])
            eq = INIT + sum(pa for ct, pa in cl if ct <= o)
            D.setdefault(g, {})[eid(r["enter_tag"])] = {
                "pair": r["pair"],
                "entry": float(r["open_rate"]),
                "close": float(r["close_rate"]),
                "profit_abs": float(r["profit_abs"]),
                "exit": r["exit_reason"],
                "dur_h": float(r["trade_duration"]) / 60.0,
                "R": float(r["profit_abs"]) / (eq * RISK),
                "open_ts": o,
                "close_ts": int(r["close_timestamp"]),
                "eq": eq,
            }
    return D


SETS = {
    "C0": load(ROOT / "evidence/FT-ENTRY-09/out/trades_Wyckoff_C1_Vs*.csv",
               lambda p: Path(p).stem.replace("trades_Wyckoff_", "")),
    "C1": load(ROOT / "evidence/FT-STOP-20-01/out/trades_Wyckoff_C1_Stop20_*.csv",
               lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_Stop20_", "")),
    "A": load(OUT / "trades_Wyckoff_C1_Stop20NoHold_*.csv",
              lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_Stop20NoHold_", "")),
    "B": load(OUT / "trades_Wyckoff_C1_NoHold_*.csv",
              lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_NoHold_", "")),
}
R = {}

# ---------- 1. 改动是否生效 ----------
print("=" * 96)
print("【1】改动是否生效：timeout_240h 退出计数")
print("=" * 96)
t1 = {}
for k, D in SETS.items():
    c = Counter(r["exit"] for g in D for r in D[g].values())
    t1[k] = {"笔数": sum(c.values()), "退出原因": dict(c),
             "timeout_240h": c.get("timeout_240h", 0),
             "force_exit": c.get("force_exit", 0)}
    print(f"  {k:<4} 笔数 {sum(c.values()):>4}  timeout_240h={c.get('timeout_240h', 0):>4}  "
          f"force_exit={c.get('force_exit', 0):>2}  {dict(c)}")
R["01_改动生效"] = t1

# 持仓上限是否仍被 240h 截断（A/B 应有超过 240h 的持仓）
print("\n  持仓时长分布（证明 240h 天花板已消失）：")
t1b = {}
for k, D in SETS.items():
    d = sorted(r["dur_h"] for g in D for r in D[g].values())
    over = sum(1 for x in d if x > 240)
    t1b[k] = {"中位h": round(d[len(d) // 2], 1), "最大h": round(max(d), 1),
              "超240h笔数": over, "超480h笔数": sum(1 for x in d if x > 480),
              "超1000h笔数": sum(1 for x in d if x > 1000)}
    print(f"  {k:<4} 中位 {d[len(d)//2]:>7.1f}h  最大 {max(d):>8.1f}h  "
          f">240h {over:>4} 笔  >480h {t1b[k]['超480h笔数']:>4} 笔  >1000h {t1b[k]['超1000h笔数']:>3} 笔")
R["01b_持仓分布"] = t1b

# ---------- 2. 笔数减少的机制 ----------
print()
print("=" * 96)
print("【2】A 组 631→451 的机制：槽位占用")
print("=" * 96)
t2 = {}
for k in ("C1", "A", "C0", "B"):
    D = SETS[k]
    # 每组是独立回测（各自 3 个槽位），必须逐组算再汇总
    per_group_conc, per_group_avg, per_group_peak = {}, {}, {}
    for g in D:
        ev = []
        for r in D[g].values():
            ev.append((r["open_ts"], 1))
            ev.append((r["close_ts"], -1))
        ev.sort(key=lambda x: (x[0], x[1]))
        cur = mx = 0
        area = 0
        prev = None
        for ts, dlt in ev:
            if prev is not None:
                area += cur * (ts - prev)
            cur += dlt
            mx = max(mx, cur)
            prev = ts
        span = ev[-1][0] - ev[0][0]
        per_group_conc[g] = mx
        per_group_peak[g] = mx
        per_group_avg[g] = round(area / span, 3) if span else 0.0
    n = len(per_group_avg) or 1
    avg = sum(per_group_avg.values()) / n
    t2[k] = {"逐组峰值并发": per_group_conc,
             "逐组平均并发": per_group_avg,
             "平均并发": round(avg, 3),
             "峰值并发中位": sorted(per_group_conc.values())[len(per_group_conc) // 2],
             "占用槽位比": round(avg / 3, 3)}
    print(f"  {k:<4} 逐组峰值并发 {sorted(per_group_conc.values())}  逐组平均并发 "
          f"{round(avg,3):>6.3f}  占 3 槽位比 {avg/3*100:>5.1f}%")
R["02_槽位占用"] = t2

# 事件级差集：C1 有而 A 没有
c1_all = {(g, e) for g in SETS["C1"] for e in SETS["C1"][g]}
a_all = {(g, e) for g in SETS["A"] for e in SETS["A"][g]}
b_all = {(g, e) for g in SETS["B"] for e in SETS["B"][g]}
c0_all = {(g, e) for g in SETS["C0"] for e in SETS["C0"][g]}
lost = c1_all - a_all
new_in_a = a_all - c1_all
print(f"\n  C1 事件 {len(c1_all)}  A 事件 {len(a_all)}  A⊂C1 ? {len(new_in_a) == 0}  "
      f"C1 有而 A 无 {len(lost)}  A 有而 C1 无 {len(new_in_a)}")
print(f"  C0 事件 {len(c0_all)}  B 事件 {len(b_all)}  B⊂C0 ? {len(b_all - c0_all) == 0}  "
      f"C0 有而 B 无 {len(c0_all - b_all)}")
R["02b_事件集合"] = {"C1": len(c1_all), "A": len(a_all), "A是C1子集": len(new_in_a) == 0,
                     "C1有A无": len(lost), "A有C1无": len(new_in_a),
                     "C0": len(c0_all), "B": len(b_all), "B是C0子集": len(b_all - c0_all) == 0,
                     "C0有B无": len(c0_all - b_all)}

# C1 的 466 笔超时单在 A 里变成了什么
timeout_in_c1 = {(g, e) for g in SETS["C1"] for e, r in SETS["C1"][g].items() if r["exit"] == "timeout_240h"}
trans = Counter()
for g, e in timeout_in_c1:
    if (g, e) in a_all:
        trans[SETS["A"][g][e]["exit"]] += 1
    else:
        trans["（A 中不存在）"] += 1
print(f"\n  C1 的 {len(timeout_in_c1)} 笔 240h 超时单在 A 中的去向：{dict(trans)}")
R["02c_超时单去向"] = {"C1超时笔数": len(timeout_in_c1), "去向": dict(trans)}

# ---------- 3. 最大回撤成因 ----------
print()
print("=" * 96)
print("【3】最大回撤成因")
print("=" * 96)
t3 = {}
for k in ("C0", "C1", "A", "B"):
    rows = sorted((r for g in SETS[k] for r in SETS[k][g].values()), key=lambda r: r["close_ts"])
    eq = INIT
    peak = eq
    peak_ts = rows[0]["open_ts"] if rows else None
    mdd = 0.0
    seg = None
    for r in rows:
        if eq > peak:
            peak, peak_ts = eq, r["close_ts"]
        eq += r["profit_abs"]
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > mdd:
            mdd = dd
            seg = (peak_ts, r["close_ts"], peak, eq)
    # 回撤段内的交易
    inside = [r for r in rows if seg and seg[0] <= r["close_ts"] <= seg[1]]
    worst = sorted(inside, key=lambda r: r["profit_abs"])[:5]
    t3[k] = {
        "最大回撤%": round(mdd * 100, 2),
        "回撤段起": seg[0] if seg else None,
        "回撤段止": seg[1] if seg else None,
        "段内笔数": len(inside),
        "段内净额": round(sum(r["profit_abs"] for r in inside), 2),
        "段内退出原因": dict(Counter(r["exit"] for r in inside)),
        "段内最差5笔": [{"pair": r["pair"], "profit": round(r["profit_abs"], 2),
                     "exit": r["exit"], "dur_h": round(r["dur_h"], 1)} for r in worst],
        "末权益": round(eq, 2),
    }
    print(f"  {k:<4} 最大回撤 {mdd*100:>6.2f}%  段内 {len(inside):>4} 笔 净 "
          f"{t3[k]['段内净额']:>10.2f}  末权益 {eq:>9.2f}  {t3[k]['段内退出原因']}")
R["03_回撤"] = t3

# A 组「一直没被止损」的长持仓
print("\n  A 组持仓最久的 8 笔（验证是否靠超时兜底才平掉的）：")
a_rows = sorted((r for g in SETS["A"] for r in SETS["A"][g].values()),
                key=lambda r: -r["dur_h"])[:8]
for r in a_rows:
    print(f"    {r['pair']:<16} {r['dur_h']:>8.1f}h  {r['exit']:<20} "
          f"{r['profit_abs']:>10.2f}  R={r['R']:>8.3f}")
R["03b_A最长持仓"] = [{"pair": r["pair"], "dur_h": round(r["dur_h"], 1), "exit": r["exit"],
                     "profit_abs": round(r["profit_abs"], 2), "R": round(r["R"], 4)} for r in a_rows]

# ---------- 4. 季度时间块 ----------
print()
print("=" * 96)
print("【4】13 个季度时间块（总 R）")
print("=" * 96)


def quarter(ts):
    import datetime as dt
    d = dt.datetime.utcfromtimestamp(ts / 1000)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


qs = sorted({quarter(r["close_ts"]) for k in SETS for g in SETS[k] for r in SETS[k][g].values()})
tbl = defaultdict(dict)
for k in ("C0", "C1", "A", "B"):
    acc = defaultdict(float)
    for g in SETS[k]:
        for r in SETS[k][g].values():
            acc[quarter(r["close_ts"])] += r["R"]
    for q in qs:
        tbl[q][k] = round(acc.get(q, 0.0), 2)
print(f"  {'季度':<9}{'C0基线':>10}{'C1 20%有超时':>14}{'A 20%无超时':>13}{'B ATR无超时':>13}")
for q in qs:
    print(f"  {q:<9}{tbl[q]['C0']:>10.2f}{tbl[q]['C1']:>14.2f}{tbl[q]['A']:>13.2f}{tbl[q]['B']:>13.2f}")
R["04_季度"] = {q: tbl[q] for q in qs}

# ---------- 5. 逐组 9 组 ----------
print()
print("=" * 96)
print("【5】9 组逐组总 R")
print("=" * 96)


def mR(D, g):
    rows = list(D.get(g, {}).values())
    if not rows:
        return None, None, None
    return (len(rows), round(sum(r["R"] for r in rows), 2),
            round(sum(r["profit_abs"] for r in rows), 2))


print(f"  {'组':<16}{'C0笔/R':>18}{'C1笔/R':>18}{'A笔/R':>18}{'B笔/R':>18}")
per_group = {}
for g in [f"C1_{s}" for s in MATRIX]:
    line = f"  {g:<16}"
    per_group[g] = {}
    for k in ("C0", "C1", "A", "B"):
        n, r_, p = mR(SETS[k], g)
        per_group[g][k] = {"笔数": n, "总R": r_, "净额": p}
        line += f"{f'{n}/{r_}' if n else '—':>18}"
    print(line)
R["05_逐组"] = per_group

(OUT / "03_验证与拆解.json").write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str),
                                 encoding="utf-8")
print(f"\n写出 {OUT/'03_验证与拆解.json'}")

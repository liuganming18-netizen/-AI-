# -*- coding: utf-8 -*-
"""FT-STOP-20-02-NOHOLD 机制与尾部风险核验（04）

 1. A 组 20% 止损机制是否仍生效（止损距开仓、风险占权益）
 2. 去掉超时后，原本靠 240h 强平的那批单结局如何
 3. 极端亏损与 R 分布尾部是否恶化
 4. A 组 8 笔「C1 没有」的事件是什么
"""
import csv
import glob
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence/FT-STOP-20-02-NOHOLD/out"
INIT, RISK = 2000.0, 0.005
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
                "pair": r["pair"], "entry": float(r["open_rate"]),
                "close": float(r["close_rate"]), "amount": float(r["amount"]),
                "profit_abs": float(r["profit_abs"]), "exit": r["exit_reason"],
                "dur_h": float(r["trade_duration"]) / 60.0,
                "R": float(r["profit_abs"]) / (eq * RISK),
                "open_ts": o, "close_ts": int(r["close_timestamp"]), "eq": eq,
                "sl": float(r["stop_loss_abs"]) if r.get("stop_loss_abs") not in (None, "") else None,
                "init_sl": float(r["initial_stop_loss_abs"]) if r.get("initial_stop_loss_abs") not in (None, "") else None,
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

# ---------- 1. A 组 20% 止损机制核验 ----------
print("=" * 96)
print("【1】A 组 20% 止损机制是否仍生效（逐笔，全 451 笔）")
print("=" * 96)
a_rows = [r for g in SETS["A"] for r in SETS["A"][g].values()]
c1_rows = [r for g in SETS["C1"] for r in SETS["C1"][g].values()]

for nm, rows in (("C1 20%有超时", c1_rows), ("A 20%无超时", a_rows)):
    # freqtrade 的 initial_stop_loss_abs 是默认哨兵值（入场价 1%），不代表自定义止损；
    # 自定义止损实际落在 stop_loss_abs：止损退出时应 = 入场×0.80，跟踪退出时已被上移。
    devs = []
    for r in rows:
        if r["sl"] and r["entry"]:
            devs.append((r["sl"] - r["entry"]) / r["entry"])
    devs.sort()
    n = len(devs)
    ok = sum(1 for d in devs if abs(d + 0.20) < 0.005)
    raised = sum(1 for d in devs if d > 0.005)
    sl_exit = [r for r in rows if r["exit"] == "stop_loss"]
    sl_dev = sorted((r["sl"] - r["entry"]) / r["entry"] for r in sl_exit if r["sl"])
    print(f"  {nm:<12} 止损价字段 {n}/{len(rows)} 笔")
    print(f"      止损退出 {len(sl_exit)} 笔中，止损价距开仓：中位 {sl_dev[len(sl_dev)//2]*100:>9.4f}%  "
          f"落在 −20%±0.5% 内 {sum(1 for d in sl_dev if abs(d+0.20)<0.005)} 笔  "
          f"区间 [{sl_dev[0]*100:.3f}%, {sl_dev[-1]*100:.3f}%]")
    print(f"      止损价被上移（>开仓价）{raised} 笔（= 2R 已触发、跟踪生效）")
    R.setdefault("01_止损机制", {})[nm] = {
        "有止损价字段笔数": n, "总笔数": len(rows),
        "止损退出笔数": len(sl_exit),
        "止损退出_中位距开仓%": round(sl_dev[len(sl_dev) // 2] * 100, 4),
        "止损退出_在20%±0.5%内": sum(1 for d in sl_dev if abs(d + 0.20) < 0.005),
        "止损退出_最小%": round(sl_dev[0] * 100, 4),
        "止损退出_最大%": round(sl_dev[-1] * 100, 4),
        "止损价被上移笔数": raised}

# 风险占权益
for nm, rows in (("C1", c1_rows), ("A", a_rows)):
    rr = sorted(abs(r["profit_abs"]) for r in rows)
    print(f"  {nm} 单笔 |盈亏| 中位 {rr[len(rr)//2]:>8.2f} USDT（≈ 权益 0.5% × 约 "
          f"{rr[len(rr)//2]/(INIT*RISK):.2f}R）")

# ---------- 2. 原本靠 240h 强平那批单的结局 ----------
print()
print("=" * 96)
print("【2】C1 的 466 笔 240h 超时单，在 A 中结局对比")
print("=" * 96)
c1_to = {e: r for g in SETS["C1"] for e, r in SETS["C1"][g].items() if r["exit"] == "timeout_240h"}
a_all = {(g, e): r for g in SETS["A"] for e, r in SETS["A"][g].items()}
c1_all = {(g, e): r for g in SETS["C1"] for e, r in SETS["C1"][g].items()}

pairs = []
for (g, e), r1 in c1_all.items():
    if r1["exit"] != "timeout_240h":
        continue
    r2 = a_all.get((g, e))
    pairs.append((r1, r2))

both = [(a, b) for a, b in pairs if b]
gone = [a for a, b in pairs if not b]
print(f"  C1 超时单 {len(pairs)} 笔 → A 中仍存在 {len(both)} 笔、消失 {len(gone)} 笔")
print(f"  仍存在的 {len(both)} 笔：C1 总 R {sum(a['R'] for a,_ in both):>9.2f} → "
      f"A 总 R {sum(b['R'] for _,b in both):>9.2f}  （差 {sum(b['R']-a['R'] for a,b in both):>+9.2f}）")
print(f"  持仓：C1 恒为 240.0h → A 中位 {sorted(b['dur_h'] for _,b in both)[len(both)//2]:>7.1f}h、"
      f"最大 {max(b['dur_h'] for _,b in both):>7.1f}h")
print(f"  消失的 {len(gone)} 笔：C1 总 R {sum(a['R'] for a in gone):>9.2f}（这些信号在 A 中没进得来）")
print(f"  仍存在的 {len(both)} 笔退出原因：{dict(Counter(b['exit'] for _, b in both))}")
R["02_超时单结局"] = {
    "C1超时单": len(pairs), "A中仍存在": len(both), "A中消失": len(gone),
    "仍存在_C1总R": round(sum(a["R"] for a, _ in both), 2),
    "仍存在_A总R": round(sum(b["R"] for _, b in both), 2),
    "仍存在_差值": round(sum(b["R"] - a["R"] for a, b in both), 2),
    "消失_C1总R": round(sum(a["R"] for a in gone), 2),
    "仍存在_A持仓中位h": round(sorted(b["dur_h"] for _, b in both)[len(both) // 2], 1),
    "仍存在_A持仓最大h": round(max(b["dur_h"] for _, b in both), 1),
    "仍存在_A退出": dict(Counter(b["exit"] for _, b in both)),
}

# ---------- 3. 尾部风险 ----------
print()
print("=" * 96)
print("【3】尾部风险：R 分布与最大单笔亏损")
print("=" * 96)


def quant(xs, q):
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


print(f"  {'方案':<6}{'笔数':>5}{'R_P1':>9}{'R_P5':>9}{'R_中位':>9}{'R_P95':>9}"
      f"{'最差R':>10}{'最好R':>9}{'最差单笔USDT':>13}{'占开仓权益%':>12}")
t3 = {}
for k in ("C0", "C1", "A", "B"):
    rows = [r for g in SETS[k] for r in SETS[k][g].values()]
    Rs = [r["R"] for r in rows]
    worst = min(rows, key=lambda r: r["profit_abs"])
    t3[k] = {"笔数": len(rows), "R_P1": round(quant(Rs, .01), 3), "R_P5": round(quant(Rs, .05), 3),
             "R_中位": round(quant(Rs, .5), 3), "R_P95": round(quant(Rs, .95), 3),
             "最差R": round(min(Rs), 3), "最好R": round(max(Rs), 3),
             "最差单笔USDT": round(worst["profit_abs"], 2),
             "最差单笔pair": worst["pair"], "最差单笔exit": worst["exit"],
             "最差单笔占权益%": round(worst["profit_abs"] / worst["eq"] * 100, 3),
             "亏损笔数": sum(1 for x in Rs if x < 0),
             "亏损R合计": round(sum(x for x in Rs if x < 0), 2)}
    print(f"  {k:<6}{len(rows):>5}{quant(Rs,.01):>9.3f}{quant(Rs,.05):>9.3f}{quant(Rs,.5):>9.3f}"
          f"{quant(Rs,.95):>9.3f}{min(Rs):>10.3f}{max(Rs):>9.3f}"
          f"{worst['profit_abs']:>13.2f}{t3[k]['最差单笔占权益%']:>12.3f}")
R["03_尾部"] = t3

# A 组「持仓 >240h」那 324 笔的表现
print("\n  A 组按持仓时长分组（>240h 的部分就是原方案会被强平的部分）：")
a_by = {}
for nm, lo, hi in (("≤240h", 0, 240), (">240h", 240, 1e9)):
    sub = [r for r in a_rows if lo < r["dur_h"] <= hi]
    if not sub:
        continue
    a_by[nm] = {"笔数": len(sub), "胜率%": round(sum(1 for r in sub if r["profit_abs"] > 0) / len(sub) * 100, 2),
                "总R": round(sum(r["R"] for r in sub), 2),
                "净额": round(sum(r["profit_abs"] for r in sub), 2),
                "退出": dict(Counter(r["exit"] for r in sub))}
    print(f"    {nm:<8} {len(sub):>4} 笔  胜率 {a_by[nm]['胜率%']:>6.2f}%  总 R "
          f"{a_by[nm]['总R']:>9.2f}  净 {a_by[nm]['净额']:>9.2f}  {a_by[nm]['退出']}")
R["03b_A按持仓"] = a_by

# ---------- 4. A 组 8 笔 C1 没有的事件 ----------
print()
print("=" * 96)
print("【4】A 有而 C1 无的 8 笔事件（路径分歧）")
print("=" * 96)
c1_keys = {(g, e) for g in SETS["C1"] for e in SETS["C1"][g]}
diff = [(g, e, r) for g in SETS["A"] for e, r in SETS["A"][g].items() if (g, e) not in c1_keys]
diff.sort(key=lambda x: x[2]["open_ts"])
for g, e, r in diff:
    print(f"  {g:<16} {r['pair']:<16} 开 {r['open_ts']}  持仓 {r['dur_h']:>7.1f}h  "
          f"{r['exit']:<20} {r['profit_abs']:>9.2f}")
R["04_新增事件"] = [{"组": g, "事件": e, "pair": r["pair"], "open_ts": r["open_ts"],
                  "dur_h": round(r["dur_h"], 1), "exit": r["exit"],
                  "profit_abs": round(r["profit_abs"], 2)} for g, e, r in diff]

# ---------- 5. R 桥：C1 → A 的差额从哪来 ----------
print()
print("=" * 96)
print("【5】R 桥：C1（−34.54R）→ A（−29.41R）的 +5.13R 从哪来")
print("=" * 96)
bridge = {}
# ① C1 超时单在 A 中仍存在
s1 = [(c1_all[(g, e)], a_all[(g, e)]) for (g, e) in c1_all
      if c1_all[(g, e)]["exit"] == "timeout_240h" and (g, e) in a_all]
bridge["①超时单延续（317 笔）"] = round(sum(b["R"] - a["R"] for a, b in s1), 2)
# ② C1 超时单在 A 中消失
s2 = [c1_all[(g, e)] for (g, e) in c1_all
      if c1_all[(g, e)]["exit"] == "timeout_240h" and (g, e) not in a_all]
bridge["②超时单消失（149 笔）"] = round(-sum(a["R"] for a in s2), 2)
# ③ 非超时单：仍在的按 R 重算，消失的把 C1 的 R 扣掉
s3a = [(c1_all[(g, e)], a_all[(g, e)]) for (g, e) in c1_all
       if c1_all[(g, e)]["exit"] != "timeout_240h" and (g, e) in a_all]
s3b = [c1_all[(g, e)] for (g, e) in c1_all
       if c1_all[(g, e)]["exit"] != "timeout_240h" and (g, e) not in a_all]
bridge["③非超时单 R 重算（%d 笔）" % len(s3a)] = round(sum(b["R"] - a["R"] for a, b in s3a), 2)
bridge["④非超时单消失（%d 笔）" % len(s3b)] = round(-sum(a["R"] for a in s3b), 2)
# ⑤ A 新增
bridge["⑤A 新增事件（%d 笔）" % len(diff)] = round(sum(r["R"] for _, _, r in diff), 2)

c1_total = round(sum(r["R"] for g in SETS["C1"] for r in SETS["C1"][g].values()), 2)
a_total = round(sum(r["R"] for g in SETS["A"] for r in SETS["A"][g].values()), 2)
print(f"  C1 总 R = {c1_total:>9.2f}")
for k, v in bridge.items():
    print(f"  {k:<28}{v:>+10.2f}")
print(f"  {'四项合计':<28}{sum(bridge.values()):>+10.2f}")
print(f"  A 总 R = {c1_total + sum(bridge.values()):>8.2f}  （实测 {a_total:.2f}，残差 "
      f"{c1_total + sum(bridge.values()) - a_total:+.4f}）")
R["05_R桥"] = {"C1总R": c1_total, "分项": bridge, "分项合计": round(sum(bridge.values()), 2),
              "推算A总R": round(c1_total + sum(bridge.values()), 2), "实测A总R": a_total,
              "残差": round(c1_total + sum(bridge.values()) - a_total, 4)}

(OUT / "04_机制与尾部.json").write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8")
print(f"\n写出 {OUT/'04_机制与尾部.json'}")

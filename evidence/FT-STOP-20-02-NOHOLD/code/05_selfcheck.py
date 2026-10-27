# -*- coding: utf-8 -*-
"""FT-STOP-20-02-NOHOLD 自检（05）

不信任何自报汇总：报告里的每个关键数字都从原始 trades CSV / 策略源码重算一遍，逐项断言。
任一项不成立即 FAIL，不隐藏、不四舍五入掩盖。
"""
import csv
import glob
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
AUD = ROOT / "evidence" / "FT-STOP-20-02-NOHOLD"
OUT = AUD / "out"
INIT, RISK = 2000.0, 0.005

CHECKS = []


def ck(name, got, want, tol=0.0):
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        ok = abs(got - want) <= tol
    else:
        ok = got == want
    CHECKS.append((ok, name, got, want, tol))
    return ok


def read_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def eid(tag):
    m = re.search(r"(ev_FT_ENTRY_\d+_binance_[A-Z0-9]+_\d+)", tag or "")
    return m.group(1) if m else (tag or "")


def load(pat, gfun):
    D = {}
    for p in sorted(glob.glob(str(pat))):
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
                "profit_abs": float(r["profit_abs"]), "exit": r["exit_reason"],
                "dur_h": float(r["trade_duration"]) / 60.0,
                "R": float(r["profit_abs"]) / (eq * RISK),
                "open_ts": o, "close_ts": int(r["close_timestamp"]), "eq": eq,
                "sl": float(r["stop_loss_abs"]),
            }
    return D


S = {
    "C0": load(ROOT / "evidence/FT-ENTRY-09/out/trades_Wyckoff_C1_Vs*.csv",
               lambda p: Path(p).stem.replace("trades_Wyckoff_", "")),
    "C1": load(ROOT / "evidence/FT-STOP-20-01/out/trades_Wyckoff_C1_Stop20_*.csv",
               lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_Stop20_", "")),
    "A": load(OUT / "trades_Wyckoff_C1_Stop20NoHold_*.csv",
              lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_Stop20NoHold_", "")),
    "B": load(OUT / "trades_Wyckoff_C1_NoHold_*.csv",
              lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_NoHold_", "")),
}


def rows_of(k):
    return [r for g in S[k] for r in S[k][g].values()]


def mdd(rows):
    eq, peak, m = INIT, INIT, 0.0
    for r in sorted(rows, key=lambda x: x["close_ts"]):
        eq += r["profit_abs"]
        peak = max(peak, eq)
        m = max(m, (peak - eq) / peak if peak > 0 else 0)
    return m * 100


# ---------- A. 四方案核心指标 ----------
EXP = {
    # 笔数, 胜率%, 净额, 总R, 平均R, MDD%, 持仓中位h
    "C0": (740, 23.11, 327.07, 37.97, 0.0513, 35.24, 14.8),
    "C1": (631, 50.08, -349.04, -34.54, -0.0547, 36.28, 240.0),
    "A": (451, 29.71, -322.03, -29.41, -0.0652, 61.12, 469.2),
    "B": (740, 23.11, 361.54, 41.44, 0.0560, 34.93, 14.8),
}
for k, (n, wr, pnl, totR, avgR, dd, dmed) in EXP.items():
    rows = rows_of(k)
    got_n = len(rows)
    got_wr = sum(1 for r in rows if r["profit_abs"] > 0) / got_n * 100
    got_pnl = sum(r["profit_abs"] for r in rows)
    got_R = sum(r["R"] for r in rows)
    got_avg = got_R / got_n
    got_dd = mdd(rows)
    durs = sorted(r["dur_h"] for r in rows)
    got_med = durs[len(durs) // 2]
    ck(f"{k} 笔数", got_n, n)
    ck(f"{k} 胜率%", round(got_wr, 2), wr, 0.005)
    ck(f"{k} 净额", round(got_pnl, 2), pnl, 0.005)
    ck(f"{k} 总R", round(got_R, 2), totR, 0.005)
    ck(f"{k} 平均R", round(got_avg, 4), avgR, 0.00005)
    ck(f"{k} 最大回撤%", round(got_dd, 2), dd, 0.005)
    ck(f"{k} 持仓中位h", round(got_med, 1), dmed, 0.05)

# PF
for k, want in (("C0", 1.06), ("C1", 0.82), ("A", 0.894), ("B", 1.066)):
    rows = rows_of(k)
    gp = sum(r["profit_abs"] for r in rows if r["profit_abs"] > 0)
    gl = abs(sum(r["profit_abs"] for r in rows if r["profit_abs"] < 0))
    ck(f"{k} PF", round(gp / gl, 3), want, 0.0005)

# ---------- B. 超时退出归零 ----------
for k, want in (("C0", 20), ("C1", 466), ("A", 0), ("B", 0)):
    c = Counter(r["exit"] for r in rows_of(k))
    ck(f"{k} timeout_240h 计数", c.get("timeout_240h", 0), want)

# 退出结构
ck("C0 退出结构", dict(Counter(r["exit"] for r in rows_of("C0"))),
   {"timeout_240h": 20, "stop_loss": 544, "trailing_stop_loss": 176})
ck("C1 退出结构", dict(Counter(r["exit"] for r in rows_of("C1"))),
   {"timeout_240h": 466, "stop_loss": 134, "trailing_stop_loss": 28, "force_exit": 3})
ck("A 退出结构", dict(Counter(r["exit"] for r in rows_of("A"))),
   {"trailing_stop_loss": 174, "stop_loss": 274, "force_exit": 3})
ck("B 退出结构", dict(Counter(r["exit"] for r in rows_of("B"))),
   {"trailing_stop_loss": 196, "stop_loss": 544})

# ---------- C. 持仓天花板消失 ----------
a_d = [r["dur_h"] for r in rows_of("A")]
ck("A >240h 笔数", sum(1 for x in a_d if x > 240), 324)
ck("A >480h 笔数", sum(1 for x in a_d if x > 480), 223)
ck("A >1000h 笔数", sum(1 for x in a_d if x > 1000), 77)
ck("A 最长持仓h", round(max(a_d), 1), 2838.8, 0.05)
b_d = [r["dur_h"] for r in rows_of("B")]
ck("B >240h 笔数", sum(1 for x in b_d if x > 240), 20)
ck("B 最长持仓h", round(max(b_d), 1), 371.0, 0.05)
for k in ("C0", "C1"):
    ck(f"{k} 无超 240h 持仓", sum(1 for r in rows_of(k) if r["dur_h"] > 240.5), 0)

# ---------- D. 事件集合 ----------
c0k = {(g, e) for g in S["C0"] for e in S["C0"][g]}
bk = {(g, e) for g in S["B"] for e in S["B"][g]}
c1k = {(g, e) for g in S["C1"] for e in S["C1"][g]}
ak = {(g, e) for g in S["A"] for e in S["A"][g]}
ck("C0 事件数", len(c0k), 740)
ck("B 事件数", len(bk), 740)
ck("B 与 C0 事件集合完全相同", bk == c0k, True)
ck("C1 事件数", len(c1k), 631)
ck("A 事件数", len(ak), 451)
ck("C1 有 A 无", len(c1k - ak), 188)
ck("A 有 C1 无", len(ak - c1k), 8)

# ---------- E. 超时单去向 ----------
to = {(g, e) for (g, e) in c1k if S["C1"][g][e]["exit"] == "timeout_240h"}
ck("C1 超时单数", len(to), 466)
still = to & ak
gone = to - ak
ck("超时单在 A 中仍存在", len(still), 317)
ck("超时单在 A 中消失", len(gone), 149)
ck("仍存在_317笔_C1总R", round(sum(S["C1"][g][e]["R"] for g, e in still), 2), 48.87, 0.005)
ck("仍存在_317笔_A总R", round(sum(S["A"][g][e]["R"] for g, e in still), 2), 55.94, 0.005)
ck("消失_149笔_C1总R", round(sum(S["C1"][g][e]["R"] for g, e in gone), 2), 37.98, 0.005)
ck("仍存在_A退出结构", dict(Counter(S["A"][g][e]["exit"] for g, e in still)),
   {"trailing_stop_loss": 148, "stop_loss": 169})
ck("仍存在_A持仓中位h",
   round(sorted(S["A"][g][e]["dur_h"] for g, e in still)[len(still) // 2], 1), 630.2, 0.05)

# ---------- F. R 桥残差 ----------
both_to = [(S["C1"][g][e], S["A"][g][e]) for g, e in still]
br1 = sum(b["R"] - a["R"] for a, b in both_to)
br2 = -sum(S["C1"][g][e]["R"] for g, e in gone)
s3a = [(g, e) for (g, e) in c1k if S["C1"][g][e]["exit"] != "timeout_240h" and (g, e) in ak]
s3b = [(g, e) for (g, e) in c1k if S["C1"][g][e]["exit"] != "timeout_240h" and (g, e) not in ak]
br3 = sum(S["A"][g][e]["R"] - S["C1"][g][e]["R"] for g, e in s3a)
br4 = -sum(S["C1"][g][e]["R"] for g, e in s3b)
br5 = sum(S["A"][g][e]["R"] for g, e in (ak - c1k))
c1tot = sum(r["R"] for r in rows_of("C1"))
atot = sum(r["R"] for r in rows_of("A"))
ck("R桥 ①超时延续", round(br1, 2), 7.07, 0.005)
ck("R桥 ②超时消失", round(br2, 2), -37.98, 0.005)
ck("R桥 ③非超时重算", round(br3, 2), -0.53, 0.005)
ck("R桥 ④非超时消失", round(br4, 2), 21.00, 0.005)
ck("R桥 ⑤A新增", round(br5, 2), 15.57, 0.005)
ck("R桥 分项合计", round(br1 + br2 + br3 + br4 + br5, 2), 5.13, 0.005)
ck("R桥 残差", round(c1tot + (br1 + br2 + br3 + br4 + br5) - atot, 6), 0.0, 1e-6)
ck("R桥 非超时单消失笔数", len(s3b), 39)
ck("R桥 非超时单重算笔数", len(s3a), 126)

# ---------- G. 止损机制仍生效 ----------
for k, n_sl in (("C1", 134), ("A", 274)):
    sl = [r for r in rows_of(k) if r["exit"] == "stop_loss"]
    ck(f"{k} 止损退出笔数", len(sl), n_sl)
    dev = sorted((r["sl"] - r["entry"]) / r["entry"] for r in sl)
    ck(f"{k} 止损价中位距开仓%", round(dev[len(dev) // 2] * 100, 4),
       -19.9940 if k == "C1" else -19.9925, 0.00005)
    ck(f"{k} 止损价全在 −20%±0.5%", sum(1 for d in dev if abs(d + 0.20) < 0.005), n_sl)
    raised = sum(1 for r in rows_of(k) if (r["sl"] - r["entry"]) / r["entry"] > 0.005)
    ck(f"{k} 止损价被上移笔数", raised, 18 if k == "C1" else 134)

# ---------- H. A 按持仓分组 ----------
a_le = [r for r in rows_of("A") if r["dur_h"] <= 240]
a_gt = [r for r in rows_of("A") if r["dur_h"] > 240]
ck("A ≤240h 笔数", len(a_le), 127)
ck("A ≤240h 胜率%", round(sum(1 for r in a_le if r["profit_abs"] > 0) / len(a_le) * 100, 2), 6.30, 0.005)
ck("A ≤240h 总R", round(sum(r["R"] for r in a_le), 2), -101.92, 0.005)
ck("A >240h 笔数", len(a_gt), 324)
ck("A >240h 胜率%", round(sum(1 for r in a_gt if r["profit_abs"] > 0) / len(a_gt) * 100, 2), 38.89, 0.005)
ck("A >240h 总R", round(sum(r["R"] for r in a_gt), 2), 72.51, 0.005)

# ---------- I. 槽位占用 ----------
for k, want in (("C1", 0.548), ("A", 1.133), ("C0", 0.141), ("B", 0.149)):
    per = []
    for g in S[k]:
        ev = []
        for r in S[k][g].values():
            ev += [(r["open_ts"], 1), (r["close_ts"], -1)]
        ev.sort(key=lambda x: (x[0], x[1]))
        cur = area = 0
        prev = None
        for ts, dlt in ev:
            if prev is not None:
                area += cur * (ts - prev)
            cur += dlt
            prev = ts
        span = ev[-1][0] - ev[0][0]
        per.append(area / span if span else 0)
    ck(f"{k} 逐组平均并发", round(sum(per) / len(per), 3), want, 0.0005)

# ---------- J. 季度 ----------
import datetime as dt


def q(ts):
    d = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


acc = {}
for r in rows_of("A"):
    acc[q(r["close_ts"])] = acc.get(q(r["close_ts"]), 0) + r["R"]
acc1 = {}
for r in rows_of("C1"):
    acc1[q(r["close_ts"])] = acc1.get(q(r["close_ts"]), 0) + r["R"]
ck("A 2023Q1-Q3 合计R", round(sum(acc.get(f"2023Q{i}", 0) for i in (1, 2, 3)), 2), -78.56, 0.005)
ck("C1 2023Q1-Q3 合计R", round(sum(acc1.get(f"2023Q{i}", 0) for i in (1, 2, 3)), 2), 1.67, 0.005)
ck("季度数", len({q(r["close_ts"]) for k in S for r in rows_of(k)}), 13)

# ---------- K. 源码层面：超时确实被删 ----------
A_BASE = AUD / "user_data/strategies/WyckoffStop20NoHoldBase.py"
B_BASE = AUD / "user_data/strategies/WyckoffNoHoldBase.py"
A_ORIG = ROOT / "evidence/FT-STOP-20-01/user_data/strategies/WyckoffStop20ParamExplorationBase.py"
B_ORIG = ROOT / "evidence/FT-ENTRY-09/user_data/strategies/WyckoffParamExplorationBase.py"
for nm, p in (("A基类", A_BASE), ("B基类", B_BASE)):
    src = p.read_text(encoding="utf-8")
    ck(f"{nm} 无 timeout_240h 字符串", "timeout_240h" in src, False)
    ck(f"{nm} 无 240*3600 判断", "240 * 3600" in src, False)
for nm, p in (("A原基类", A_ORIG), ("B原基类", B_ORIG)):
    src = p.read_text(encoding="utf-8")
    ck(f"{nm} 有 timeout_240h", "timeout_240h" in src, True)
# 差异行数：除注释/类名/路径/超时外不应有别的改动
import difflib
for nm, newp, oldp in (("A", A_BASE, A_ORIG), ("B", B_BASE, B_ORIG)):
    nl = newp.read_text(encoding="utf-8").splitlines()
    ol = oldp.read_text(encoding="utf-8").splitlines()
    diff = [l for l in difflib.unified_diff(ol, nl, lineterm="", n=0)
            if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    ck(f"{nm} 源码差异行数", len(diff), 14)

# ---------- L. 18 组回测产物齐全 ----------
zips = glob.glob(str(OUT / "backtest-result-*.zip"))
ck("回测结果包数量", len(zips), 18)
trades = glob.glob(str(OUT / "trades_*.csv"))
ck("逐笔 CSV 数量", len(trades), 18)
ck("每组都有成交", all(len(read_csv(p)) > 0 for p in trades), True)

# ---------- 输出 ----------
n_ok = sum(1 for ok, *_ in CHECKS if ok)
print("=" * 90)
print(f"自检结果：{n_ok}/{len(CHECKS)} PASS")
print("=" * 90)
for ok, name, got, want, tol in CHECKS:
    if not ok:
        print(f"  FAIL  {name}: 实测 {got}  期望 {want}  容差 {tol}")
print(f"\n{'全部通过' if n_ok == len(CHECKS) else f'{len(CHECKS)-n_ok} 项未通过'}")
(AUD / "out" / "05_自检结果.json").write_text(
    json.dumps({"通过": n_ok, "总数": len(CHECKS),
                "明细": [{"通过": ok, "项目": nm, "实测": str(g), "期望": str(w), "容差": t}
                       for ok, nm, g, w, t in CHECKS]},
               ensure_ascii=False, indent=2),
    encoding="utf-8")
print(f"写出 {OUT/'05_自检结果.json'}")

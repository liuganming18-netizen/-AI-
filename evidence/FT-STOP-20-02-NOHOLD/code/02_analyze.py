# -*- coding: utf-8 -*-
"""FT-STOP-20-02-NOHOLD 对照分析（02）

四套方案同口径比较（都用「每笔风险预算」折算 R，尺度无关）：
  C0 基线      ：ATR 结构止损 + 240h 超时（FT-ENTRY-09）
  C1 20%有超时 ：20% 固定止损 + 240h 超时（FT-STOP-20-01）
  A  20%无超时 ：20% 固定止损 + 无超时（本实验）
  B  ATR无超时 ：ATR 结构止损 + 无超时（本实验）

R_i = profit_abs_i / (开仓时权益 × 0.005)
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
MATRIX = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]


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
                "amount": float(r["amount"]),
                "notional": float(r["amount"]) * float(r["open_rate"]),
                "eq": eq,
                "profit_abs": float(r["profit_abs"]),
                "exit": r["exit_reason"],
                "dur_h": float(r["trade_duration"]) / 60.0,
                "R": float(r["profit_abs"]) / (eq * RISK),
                "open_ts": o,
                "close_ts": int(r["close_timestamp"]),
            }
    return D


SETS = {
    "C0_基线_ATR止损_有超时": load(
        ROOT / "evidence/FT-ENTRY-09/out/trades_Wyckoff_C1_Vs*.csv",
        lambda p: Path(p).stem.replace("trades_Wyckoff_", ""),
    ),
    "C1_20%止损_有超时": load(
        ROOT / "evidence/FT-STOP-20-01/out/trades_Wyckoff_C1_Stop20_*.csv",
        lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_Stop20_", ""),
    ),
    "A_20%止损_无超时": load(
        ROOT / "evidence/FT-STOP-20-02-NOHOLD/out/trades_Wyckoff_C1_Stop20NoHold_*.csv",
        lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_Stop20NoHold_", ""),
    ),
    "B_ATR止损_无超时": load(
        ROOT / "evidence/FT-STOP-20-02-NOHOLD/out/trades_Wyckoff_C1_NoHold_*.csv",
        lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_NoHold_", ""),
    ),
}


def metrics(D):
    rows = [r for g in D for r in D[g].values()]
    if not rows:
        return None
    n = len(rows)
    wins = [r for r in rows if r["profit_abs"] > 0]
    gp = sum(r["profit_abs"] for r in wins)
    gl = abs(sum(r["profit_abs"] for r in rows if r["profit_abs"] < 0))
    seq = sorted(rows, key=lambda r: r["close_ts"])
    eq = INIT
    peak = eq
    mdd = 0.0
    for r in seq:
        eq += r["profit_abs"]
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak if peak > 0 else 0)
    durs = sorted(r["dur_h"] for r in rows)
    Rs = [r["R"] for r in rows]
    return {
        "笔数": n,
        "胜率%": round(len(wins) / n * 100, 2),
        "净额": round(sum(r["profit_abs"] for r in rows), 2),
        "收益率%": round(sum(r["profit_abs"] for r in rows) / INIT * 100, 2),
        "PF": round(gp / gl, 3) if gl > 0 else None,
        "总R": round(sum(Rs), 2),
        "平均R": round(sum(Rs) / n, 4),
        "最大回撤%": round(mdd * 100, 2),
        "最大单笔亏损%权益": round(min(r["profit_abs"] / r["eq"] for r in rows) * 100, 3),
        "持仓中位h": round(durs[len(durs) // 2], 1),
        "持仓均值h": round(sum(durs) / n, 1),
        "持仓最大h": round(max(durs), 1),
        "名义占权益%中位": round(sorted(r["notional"] / r["eq"] for r in rows)[n // 2] * 100, 2),
        "退出原因": dict(Counter(r["exit"] for r in rows)),
    }


summary = {}
for k, D in SETS.items():
    summary[k] = metrics(D)

# 逐组对比
group_rows = []
for g in [f"C1_{s}" for s in MATRIX]:
    row = {"组": g}
    for k, D in SETS.items():
        m = metrics({g: D.get(g, {})})
        row[k] = m
    group_rows.append(row)

print("=" * 96)
print("四套方案总账（9 组汇总，R 口径尺度无关）")
print("=" * 96)
print(f"{'方案':<24}{'笔数':>5}{'胜率%':>8}{'净额':>11}{'总R':>9}{'平均R':>9}{'PF':>7}{'回撤%':>8}{'持仓中位':>9}")
for k, m in summary.items():
    if not m:
        print(f"{k:<24}  （无数据）")
        continue
    print(f"{k:<24}{m['笔数']:>5}{m['胜率%']:>8.2f}{m['净额']:>11.2f}{m['总R']:>9.2f}"
          f"{m['平均R']:>9.4f}{str(m['PF']):>7}{m['最大回撤%']:>8.2f}{m['持仓中位h']:>9.1f}")

print()
print("退出结构：")
for k, m in summary.items():
    if m:
        print(f"  {k:<24}{m['退出原因']}")

(OUT / "02_四方案对照.json").write_text(
    json.dumps({"汇总": summary, "逐组": group_rows}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(f"\n写出 {OUT/'02_四方案对照.json'}")

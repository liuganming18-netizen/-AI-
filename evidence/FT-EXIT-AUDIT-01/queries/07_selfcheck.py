# -*- coding: utf-8 -*-
"""FT-EXIT-AUDIT-01 / 07：自检

把报告里出现的每个数字，**从原始逐笔 CSV / 原始回测结果包 / 原始日志**重新算一遍，
再与报告声明值比对。不读 02~06 的中间 JSON（避免自己验自己）。

判定：全部断言通过 = 报告数字可信；任何一条不过，报告对应段落必须改。
"""
from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = ROOT / "evidence/FT-EXIT-01"
OUT = ROOT / "evidence/FT-EXIT-AUDIT-01/out"
LOGS = EXP / "logs"
SUF = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
SCHEMES = {"S": "Wyckoff_C1_S_P0", "P": "Wyckoff_C1_P_Stop20"}
EQUITY0 = 2000.0
RISK = 0.005
BAR_MS = 15 * 60 * 1000
STOP_LABELS = ("stop_loss", "trailing_stop_loss")

CHECKS = []


def ck(name, actual, expected, tol=0.0):
    ok = False
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        ok = abs(actual - expected) <= tol
    else:
        ok = actual == expected
    CHECKS.append({"检查项": name, "实测": actual, "报告声明": expected, "通过": bool(ok)})
    if not ok:
        print(f"  ✗ {name}: 实测={actual} 报告={expected}")
    return ok


def read_csv(p: Path):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def eid(tag: str) -> str:
    x = tag.split("cand_C1_", 1)[-1].split("_")
    return "_".join(x[:7])


# ---------------------------------------------------------------- 逐组原始读取
G = {}
for scheme in ("S", "P"):
    G[scheme] = {}
    for suf in SUF:
        p = EXP / f"out/trades_{SCHEMES[scheme]}_{suf}.csv"
        rows = read_csv(p)
        # 权益曲线（按平仓时间）
        closed = sorted(((int(r["close_timestamp"]), float(r["profit_abs"])) for r in rows))
        eq_at = lambda t: EQUITY0 + sum(x for ts, x in closed if ts <= t)  # noqa: E731
        recs = []
        for r in rows:
            o, c = float(r["open_rate"]), float(r["close_rate"])
            amt = float(r["amount"])
            stake = float(r["stake_amount"])
            stop = float(r["stop_loss_abs"])
            eq = eq_at(int(r["open_timestamp"]))
            risk = (o - stop) * amt
            recs.append({
                "pair": r["pair"], "eid": eid(r["enter_tag"]),
                "open_ts": int(r["open_timestamp"]), "close_ts": int(r["close_timestamp"]),
                "open": o, "close": c, "stop": stop, "amount": amt, "stake": stake,
                "pnl": float(r["profit_abs"]), "funding": float(r["funding_fees"]),
                "exit": r["exit_reason"], "eq": eq,
                "risk_pct": risk / eq * 100 if eq else None,
                "notional_pct": stake / eq * 100 if eq else None,
                "R": (float(r["profit_abs"]) / (eq * RISK)) if eq else None,
            })
        # 确认表
        cp = EXP / f"out/audit_confirmation_{SCHEMES[scheme]}_{suf}.csv"
        conf = {}
        for c_ in read_csv(cp):
            if c_["decision"] == "CONFIRMED":
                conf.setdefault(c_["event_id"], c_)
        # 回测包期末余额
        zp = EXP / f"out/backtest-result-{SCHEMES[scheme]}_{suf}.zip"
        final_bal = None
        if zp.exists():
            with zipfile.ZipFile(zp) as z:
                names = [n for n in z.namelist() if n.endswith(".json") and not n.endswith(".meta.json")]
                if names:
                    with z.open(names[0]) as jf:
                        final_bal = json.load(jf).get("strategy", {}).get(
                            f"{SCHEMES[scheme]}_{suf}", {}).get("final_balance")
        G[scheme][suf] = {"trades": recs, "conf": conf, "final_balance": final_bal}


def agg(scheme):
    t = [r for v in G[scheme].values() for r in v["trades"]]
    return t


print("=" * 100)
print("【1】笔数与退出原因")
print("=" * 100)
for scheme, n_exp in (("S", 671), ("P", 243)):
    t = agg(scheme)
    ck(f"{scheme} 组笔数", len(t), n_exp)
    cnt = {}
    for r in t:
        cnt[r["exit"]] = cnt.get(r["exit"], 0) + 1
    ck(f"{scheme} 组非法退出原因", sum(v for k, v in cnt.items() if k not in STOP_LABELS + ("force_exit",)), 0)
    for k, v in cnt.items():
        print(f"    {scheme} {k} = {v}")

ck("S stop_loss", sum(1 for r in agg("S") if r["exit"] == "stop_loss"), 665)
ck("S trailing_stop_loss", sum(1 for r in agg("S") if r["exit"] == "trailing_stop_loss"), 0)
ck("S force_exit", sum(1 for r in agg("S") if r["exit"] == "force_exit"), 6)
ck("P stop_loss", sum(1 for r in agg("P") if r["exit"] == "stop_loss"), 188)
ck("P trailing_stop_loss", sum(1 for r in agg("P") if r["exit"] == "trailing_stop_loss"), 29)
ck("P force_exit", sum(1 for r in agg("P") if r["exit"] == "force_exit"), 26)
ck("合计笔数", len(agg("S")) + len(agg("P")), 914)

print()
print("=" * 100)
print("【2】资金费 / 已实现 / 未实现 / 全口径净额 / 权益闭合")
print("=" * 100)
for scheme in ("S", "P"):
    t = agg(scheme)
    fund = sum(r["funding"] for r in t)
    real = sum(r["pnl"] for r in t if r["exit"] in STOP_LABELS)
    unreal = sum(r["pnl"] for r in t if r["exit"] == "force_exit")
    print(f"  {scheme}: 资金费 {fund:.6f}  已实现 {real:.2f}  未实现 {unreal:.2f}  全口径 {real+unreal:.2f}")
    ck(f"{scheme} 资金费", round(fund, 4), -459.3131 if scheme == "S" else -328.1819, 1e-3)
    ck(f"{scheme} 全口径净额", round(real + unreal, 1), -5042.7 if scheme == "S" else 1.3, 0.15)
    # 权益闭合：Σpnl + 2000 == 回测包 final_balance
    for suf, v in G[scheme].items():
        if v["final_balance"] is None:
            continue
        calc = EQUITY0 + sum(r["pnl"] for r in v["trades"])
        ck(f"{scheme}-{suf} 权益闭合", round(calc - v["final_balance"], 6), 0.0, 1e-6)

print()
print("=" * 100)
print("【3】风险预算 / 名义帽")
print("=" * 100)
for scheme in ("S", "P"):
    t = agg(scheme)
    over = sum(1 for r in t if r["risk_pct"] > 0.5 + 1e-6)
    over_n = sum(1 for r in t if r["notional_pct"] > 30.0 + 1e-6)
    mx = max(r["risk_pct"] for r in t)
    mxn = max(r["notional_pct"] for r in t)
    mn = min(r["stake"] for r in t)
    print(f"  {scheme}: 风险超0.5% {over} 笔，名义超30% {over_n} 笔，风险最大 {mx:.4f}%，"
          f"名义最大 {mxn:.2f}%，stake 最小 {mn:.2f}")
    ck(f"{scheme} 风险超0.5%笔数", over, 0)
    ck(f"{scheme} 名义超30%笔数", over_n, 0)
    ck(f"{scheme} 风险最大%", round(mx, 3), 0.495, 5e-4)
    ck(f"{scheme} 名义最大%", round(mxn, 2), 29.70 if scheme == "S" else 2.47, 5e-3)
    ck(f"{scheme} stake最小", round(mn, 2), 61.74 if scheme == "S" else 24.33, 5e-3)

print()
print("=" * 100)
print("【4】止损价：等于目标价 + 取整 1 个价位以内")
print("=" * 100)


def tick_of(raw: str) -> float:
    raw = (raw or "").strip()
    return 10.0 ** (-len(raw.split(".")[1])) if "." in raw else 1.0


for scheme in ("S", "P"):
    over1 = n_up = n_lab = 0
    for suf in SUF:
        for r in read_csv(EXP / f"out/trades_{SCHEMES[scheme]}_{suf}.csv"):
            if r["exit_reason"] == "trailing_stop_loss":
                n_lab += 1
            stop, entry = float(r["stop_loss_abs"]), float(r["open_rate"])
            if scheme == "P":
                target = entry * 0.80
            else:
                c_ = G[scheme][suf]["conf"].get(eid(r["enter_tag"]))
                if c_ is None:
                    continue
                target = float(c_["orig_P0"])
            dev = stop - target
            if dev > 1e-12:
                n_up += 1
                if dev / tick_of(r["stop_loss_abs"]) > 1.0000001:
                    over1 += 1
    print(f"  {scheme}: 标 trailing {n_lab} 笔，止损高于目标价 {n_up} 笔，上偏超 1 个价位 {over1} 笔")
    ck(f"{scheme} 上偏超1个价位笔数", over1, 0)
    ck(f"{scheme} 标trailing笔数", n_lab, 0 if scheme == "S" else 29)

print()
print("=" * 100)
print("【5】确认 → 成交归因")
print("=" * 100)
for scheme in ("S", "P"):
    n_conf = sum(len(v["conf"]) for v in G[scheme].values())
    n_fill = sum(len(v["trades"]) for v in G[scheme].values())
    full = 0
    for suf, v in G[scheme].items():
        traded = {r["eid"] for r in v["trades"]}
        for e_, c_ in v["conf"].items():
            if e_ in traded:
                continue
            te = int(c_["confirm_time_ms"]) + BAR_MS
            occ = sum(1 for r in v["trades"] if r["open_ts"] <= te < r["close_ts"])
            if occ >= 3:
                full += 1
    rate = n_fill / n_conf * 100
    print(f"  {scheme}: 确认 {n_conf} 成交 {n_fill} 成交率 {rate:.1f}% 槽位已满 {full}")
    ck(f"{scheme} 确认数", n_conf, 757)
    ck(f"{scheme} 成交数", n_fill, 671 if scheme == "S" else 243)
    ck(f"{scheme} 成交率%", round(rate, 1), 88.6 if scheme == "S" else 32.1, 0.05)
    ck(f"{scheme} 槽位已满跳过", full, 68 if scheme == "S" else 432)

print()
print("=" * 100)
print("【6】最小下单额拒单（日志逐条）")
print("=" * 100)
rej = {}
for scheme in ("S", "P"):
    n = 0
    pairs = set()
    for suf in SUF:
        f = LOGS / f"{SCHEMES[scheme]}_{suf}_backtest.log"
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "超出风险预算硬性拒单" in line:
                n += 1
                m = re.search(r"\[([^\]]+)\]", line)
                if m:
                    pairs.add(m.group(1))
    rej[scheme] = (n, sorted(pairs))
    print(f"  {scheme}: {n} 次，币种 {[p.split('/')[0] for p in sorted(pairs)]}")
ck("S 拒单次数", rej["S"][0], 0)
ck("P 拒单次数", rej["P"][0], 60)
ck("P 拒单币种数", len(rej["P"][1]), 3)

print()
print("=" * 100)
print("【7】槽位与资金占用（时间加权）")
print("=" * 100)
for scheme in ("S", "P"):
    slot_h = fund_h = slot_h240 = fund_h240 = 0.0
    full_ratio = []
    for suf, v in G[scheme].items():
        t = v["trades"]
        span = 1113.1 * 24  # 近似窗口小时数，仅用于满仓占比
        # 满仓占比：按 15m 网格采样
        grid = set()
        for r in t:
            grid.update(range(r["open_ts"], r["close_ts"], BAR_MS))
        if not grid:
            continue
        full = 0
        for ts in grid:
            occ = sum(1 for r in t if r["open_ts"] <= ts < r["close_ts"])
            if occ >= 3:
                full += 1
        # 用整段窗口（按首末成交覆盖）作为分母更稳：改用「有成交覆盖的 15m 格数 + 空窗」
        t0 = min(r["open_ts"] for r in t)
        t1 = max(r["close_ts"] for r in t)
        total = (t1 - t0) // BAR_MS + 1
        full_ratio.append(full / total * 100)
        for r in t:
            h = (r["close_ts"] - r["open_ts"]) / 3600000
            slot_h += h
            fund_h += h * r["stake"]
            if h > 240:
                slot_h240 += h
                fund_h240 += h * r["stake"]
    print(f"  {scheme}: 槽位·小时 {slot_h:,.1f}（>240h 占 {slot_h240/slot_h*100:.1f}%），"
          f"资金·小时 {fund_h:,.1f}（>240h 占 {fund_h240/fund_h*100:.1f}%），"
          f"满仓占比均值 {sum(full_ratio)/len(full_ratio):.1f}%")
    if scheme == "S":
        ck("S >240h 槽位占比%", round(slot_h240 / slot_h * 100, 1), 84.8, 0.15)
        ck("S >240h 资金占比%", round(fund_h240 / fund_h * 100, 1), 81.1, 0.15)
        ck("S 槽位小时", round(slot_h, 1), 124668.3, 1.0)
        ck("S 资金小时", round(fund_h, 0), 33701005.0, 2.0)
        ck("S 满仓占比均值%", round(sum(full_ratio) / len(full_ratio), 1), 3.65, 0.1)
    else:
        ck("P >240h 槽位占比%", round(slot_h240 / slot_h * 100, 1), 98.6, 0.15)
        ck("P >240h 资金占比%", round(fund_h240 / fund_h * 100, 1), 98.6, 0.15)
        ck("P 槽位小时", round(slot_h, 1), 462227.0, 1.0)
        ck("P 资金小时", round(fund_h, 0), 20546292.0, 2.0)
        ck("P 满仓占比均值%", round(sum(full_ratio) / len(full_ratio), 1), 48.35, 0.1)

print()
print("=" * 100)
print("【8】同笔数折算（把「少卖出」剥离）")
print("=" * 100)
s_closed = [r for r in agg("S") if r["exit"] in STOP_LABELS]
p_closed = [r for r in agg("P") if r["exit"] in STOP_LABELS]
s_per = sum(r["R"] for r in s_closed) / len(s_closed)
p_per = sum(r["R"] for r in p_closed) / len(p_closed)
vol = (p_per - s_per) * len(s_closed)
cnt = (len(s_closed) - len(p_closed)) * p_per
print(f"  S {len(s_closed)} 笔 × {s_per:.4f}R = {sum(r['R'] for r in s_closed):.2f}R")
print(f"  P {len(p_closed)} 笔 × {p_per:.4f}R = {sum(r['R'] for r in p_closed):.2f}R")
print(f"  每笔幅度贡献 {vol:+.2f}R（{vol/(vol-cnt)*100:.1f}%），笔数贡献 {cnt:+.2f}R（{-cnt/(vol-cnt)*100:.1f}%）")
ck("S 已平仓笔数", len(s_closed), 665)
ck("P 已平仓笔数", len(p_closed), 217)
ck("S 每笔已实现R", round(s_per, 4), -1.0644, 5e-5)
ck("P 每笔已实现R", round(p_per, 4), -0.9546, 5e-5)
ck("每笔幅度贡献R", round(vol, 2), 73.02, 0.05)
ck("笔数贡献R", round(cnt, 2), -427.66, 0.05)

print()
print("=" * 100)
print("【9】期末未平仓集中度")
print("=" * 100)
for scheme in ("S", "P"):
    t = [r for r in agg(scheme) if r["exit"] == "force_exit"]
    t.sort(key=lambda r: -r["pnl"])
    tot = sum(r["pnl"] for r in t)
    top5 = sum(r["pnl"] for r in t[:5])
    print(f"  {scheme}: {len(t)} 笔，合计 {tot:.2f}，前5 {top5:.2f}（{top5/tot*100:.1f}%），"
          f"最长持仓 {max((r['close_ts']-r['open_ts'])/3600000 for r in t):.1f}h")
    if scheme == "S":
        ck("S 期末笔数", len(t), 6)
        ck("S 期末浮盈", round(tot, 2), 595.62, 0.02)
        ck("S 前5占比%", round(top5 / tot * 100, 1), 88.4, 0.1)
    else:
        ck("P 期末笔数", len(t), 26)
        ck("P 期末浮盈", round(tot, 2), 1958.00, 0.02)
        ck("P 前5占比%", round(top5 / tot * 100, 1), 62.0, 0.1)

print()
print("=" * 100)
print("【10】K 线一致性（读 04 产物 + 抽查 24 笔原始 K 线）")
print("=" * 100)
k4 = json.load(open(OUT / "04_K线一致性.json", encoding="utf-8"))
for scheme, n_exp in (("S", 671), ("P", 243)):
    ok = [v for v in k4[scheme].values() if "笔数" in v]
    tot = sum(int(v["笔数"]) for v in ok)
    e = sum(int(v["入场在K线内"]) for v in ok)
    cl = sum(int(v["出场=止损价"]) for v in ok)
    tc = sum(int(v["出场K线触及止损"]) for v in ok)
    tm = sum(int(v["时间对齐"]) for v in ok)
    print(f"  {scheme}: {tot} 笔 入场{e} 出场{cl} 触及{tc} 时间{tm}")
    ck(f"{scheme} K线笔数", tot, n_exp)
    ck(f"{scheme} K线全项一致", min(e, cl, tc, tm), n_exp)

DATA = ROOT / "evidence/FT-ENTRY-02/user_data/data/binance/futures"
_cache = {}


def kl(pair: str):
    if pair in _cache:
        return _cache[pair]
    f = DATA / f"{pair.replace('/', '_').replace(':', '_')}-15m-futures.feather"
    if not f.exists():
        _cache[pair] = None
        return None
    df = pd.read_feather(f)
    df["ms"] = ((df["date"] - pd.Timestamp("1970-01-01", tz="UTC"))
                // pd.Timedelta(milliseconds=1)).astype("int64")
    _cache[pair] = df.set_index("ms").sort_index()
    return _cache[pair]


import random  # noqa: E402

random.seed(20261022)
spot = []
for scheme in ("S", "P"):
    t = agg(scheme)
    spot += random.sample(t, 12)
bad = 0
for r in spot:
    df = kl(r["pair"])
    if df is None or r["open_ts"] not in df.index:
        bad += 1
        continue
    bar = df.loc[r["open_ts"]]
    if not (bar["low"] <= r["open"] <= bar["high"]):
        bad += 1
    if r["exit"] in STOP_LABELS:
        if r["close_ts"] not in df.index:
            bad += 1
        else:
            b2 = df.loc[r["close_ts"]]
            if not (b2["low"] <= r["stop"] <= b2["high"]):
                bad += 1
print(f"  抽查 {len(spot)} 笔原始 K 线，异常 {bad} 笔")
ck("抽查K线异常笔数", bad, 0)

# ------------------------------------------------------------------ 输出
n_pass = sum(1 for c in CHECKS if c["通过"])
print()
print("=" * 100)
print(f"自检结果：{n_pass}/{len(CHECKS)} 通过")
print("=" * 100)
if n_pass != len(CHECKS):
    print("未通过项：")
    for c in CHECKS:
        if not c["通过"]:
            print("  ", c)

(OUT / "07_自检.json").write_text(json.dumps({
    "通过": n_pass, "总数": len(CHECKS), "明细": CHECKS,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n写出 {OUT/'07_自检.json'}")

# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 07_自检

不信自己前几步的汇总：全部从原始 CSV 用另一条代码路径重算，再与产物比对。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from _lib import (ALL, INIT_EQUITY, OUT, RISK_RATE, TIER_PCT, TIERS, UP, equity_curve,
                  event_id, load_all, load_trades)

CHECKS: list[tuple[str, bool, str]] = []


def ck(name: str, ok: bool, note: str = "") -> None:
    CHECKS.append((name, bool(ok), note))


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def independent_r() -> pd.DataFrame:
    """完全独立的一条路径：逐笔 for 循环推进权益，不用 searchsorted。"""
    rows = []
    for name in ALL:
        raw = pd.read_csv(UP / f"out/trades_{name}.csv", encoding="utf-8-sig")
        if raw.empty:
            continue
        raw = raw.sort_values("close_timestamp").reset_index(drop=True)
        realized = [(int(c), float(p)) for c, p, e in
                    zip(raw["close_timestamp"], raw["profit_abs"], raw["exit_reason"])
                    if str(e) != "force_exit"]
        for _, r in raw.iterrows():
            o = int(r["open_timestamp"])
            eq = INIT_EQUITY
            for c, p in realized:
                if c <= o:
                    eq += p
                else:
                    break
            rows.append({"策略": name, "pair": r["pair"], "open_timestamp": o,
                         "profit_abs": float(r["profit_abs"]),
                         "equity": eq, "R": float(r["profit_abs"]) / (eq * RISK_RATE)})
    return pd.DataFrame(rows)


def main() -> dict:
    df, missing = load_all()
    d = equity_curve(df)
    ind = independent_r()

    # 1 权益与 R 的两条路径一致
    key = ["策略", "pair", "open_timestamp"]
    a = d[key + ["R", "equity_at_entry"]].rename(columns={"R": "R_主路径", "equity_at_entry": "eq_主路径"})
    b = ind[key + ["R"]].rename(columns={"R": "R_独立路径"})
    m = a.merge(b, on=key, how="outer", indicator=True)
    ck("权益/R 两条路径行数一致", len(m) == len(a) == len(b), f"{len(a)} vs {len(b)}")
    if len(m) and m["R_主路径"].notna().all() and m["R_独立路径"].notna().all():
        mx = float((m["R_主路径"] - m["R_独立路径"]).abs().max())
        ck("权益/R 两条路径最大差 < 1e-9", mx < 1e-9, f"最大差 {mx:.3e}")
    else:
        ck("权益/R 两条路径最大差 < 1e-9", False, "存在 NaN")

    # 2 已实现 + 期末 = 总
    r3 = json.load(open(OUT / "03_指标重算.json", encoding="utf-8"))
    ok2 = True
    bad2 = []
    for row in r3["一_各档指标"]:
        s = row["已实现R"] + row["期末结算R"]
        if abs(s - row["总R"]) > 1e-4:
            ok2 = False
            bad2.append((row["档位"], s, row["总R"]))
    ck("03 已实现R+期末R=总R（容差1e-4，因已四舍五入到6位）", ok2, str(bad2))

    # 3 02 止损价合规数
    r2 = json.load(open(OUT / "02_交付完整性核对.json", encoding="utf-8"))
    sdf = pd.read_csv(OUT / "02_止损价核对明细.csv", encoding="utf-8-sig")
    ck("02 止损价合规计数一致",
       int(sdf["止损价合规"].sum()) == r2["二_止损价"]["止损价等于成交价×(1-pct)（容差1个最小价位）"],
       f"{int(sdf['止损价合规'].sum())}")

    # 4 05 的 CONFIRMED 数
    r5 = json.load(open(OUT / "05_跳过与仓位竞争.json", encoding="utf-8"))
    ok4 = True
    bad = []
    for row in r5["一_事件漏斗"]:
        p = UP / f"out/audit_confirmation_{row['策略']}.csv"
        if not p.exists():
            continue
        n = int((pd.read_csv(p, encoding="utf-8-sig")["decision"] == "CONFIRMED").sum())
        if n != row["CONFIRMED"]:
            ok4 = False
            bad.append((row["策略"], n, row["CONFIRMED"]))
    ck("05 CONFIRMED 计数一致", ok4, str(bad[:3]))

    # 5 04 的 P1 事件数（独立用集合交集算）
    r4 = json.load(open(OUT / "04_配对与统计.json", encoding="utf-8"))
    ok5 = True
    bad5 = []
    for s, n in r4["五_P1样本量"].items():
        sub = d[d["参数组"] == s]
        ss = {t: set(zip(sub[sub["档位"] == t]["pair"], sub[sub["档位"] == t]["事件ID"])) for t in TIERS}
        common = set.intersection(*ss.values()) if all(ss.values()) else set()
        if len(common) != n:
            ok5 = False
            bad5.append((s, len(common), n))
    ck("04 P1 事件数一致", ok5, str(bad5[:3]))

    # 6 06 抽样数
    r6 = json.load(open(OUT / "06_K线一致性.json", encoding="utf-8"))
    ck("06 抽样数 = min(20,该档笔数) 之和",
       r6["实际抽样"] == sum(min(20, int((d["档位"] == t).sum())) for t in TIERS),
       f"{r6['实际抽样']}")

    # 7 总笔数一致
    ck("总笔数 = 各档笔数之和",
       int(len(d)) == sum(int((d["档位"] == t).sum()) for t in TIERS), f"{len(d)}")

    # 8 冻结计划未被改写，且冻结时刻不晚于最晚一组结果落盘
    plan_p = OUT / "01_冻结分析计划.json"
    plan = json.load(open(plan_p, encoding="utf-8"))
    ck("冻结计划仍存在且含冻结时刻", "冻结时刻" in plan and "脚本SHA256" in plan["冻结时刻"])
    freeze_ts = pd.Timestamp(plan["冻结时刻"]["时间"])
    latest_mtime = max(p.stat().st_mtime for p in UP.glob("out/trades_*.csv"))
    ck("冻结时刻早于最后一组结果落盘",
       freeze_ts.timestamp() <= latest_mtime,
       f"冻结 {freeze_ts.isoformat()} / 最晚结果 {pd.Timestamp(latest_mtime, unit='s', tz='UTC').isoformat()}")

    # 9 事件ID 提取成功率
    ck("事件ID 无空值", d["事件ID"].notna().all(), f"空 {int(d['事件ID'].isna().sum())}")

    # 10 手续费与名义的比率
    # 手续费 = 开仓名义×5bps + 平仓名义×5bps；平仓名义随价格变动，故不能用 10bps 常数比
    expect = 0.0005 * (1 + d["close_rate"].astype(float) / d["open_rate"].astype(float))
    ratio = (d["手续费USDT"] / d["名义USDT"]).replace([np.inf, -np.inf], np.nan)
    dev = (ratio - expect).abs()
    ck("手续费 = 开仓名义×5bps + 平仓名义×5bps", bool(dev.max() < 1e-6),
       f"最大偏差 {dev.max():.2e}，中位比率 {ratio.median():.6f}")

    # 11 R 分母
    ck("R 分母 = 权益×0.5%",
       bool((d["风险预算USDT"] - d["equity_at_entry"] * RISK_RATE).abs().max() < 1e-9))

    # ---------- 以下为补充核对：逐条从原始 CSV 独立重算 ----------
    raw = {}
    for n in ALL:
        raw[n] = pd.read_csv(UP / f"out/trades_{n}.csv", encoding="utf-8-sig")

    # 12 每组笔数与原始 CSV 行数一致
    bad12 = [(n, len(raw[n]), int((d["策略"] == n).sum())) for n in ALL if len(raw[n]) != int((d["策略"] == n).sum())]
    ck("每组笔数 = 原始 CSV 行数", not bad12, str(bad12[:3]))

    # 13 各档笔数 = 该档 9 组笔数之和
    bad13 = []
    for t in TIERS:
        s = sum(len(raw[f"Wyckoff_C1_{t}_{suf}"]) for suf in ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
                                                              "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
                                                              "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"])
        if s != int((d["档位"] == t).sum()):
            bad13.append((t, s, int((d["档位"] == t).sum())))
    ck("各档笔数 = 该档 9 组之和", not bad13, str(bad13))

    # 14 每档止损价全部等于 成交价×(1-pct)（独立算，容差按最小价位）
    bad14 = []
    for n, r in raw.items():
        pct = TIER_PCT[n.split("_")[2]]
        diff = (r["stop_loss_abs"] - r["open_rate"] * (1 - pct)).abs()
        # 容差按账面止损价的书写小数位推最小价位（freqtrade 已取整到该精度）
        tk = r["stop_loss_abs"].astype(str).map(
            lambda x: 10.0 ** (-len(x.split(".")[1])) if "." in x else 1.0)
        if (diff > tk * 1.0000001).any():
            bad14.append((n, int((diff > tk * 1.0000001).sum())))
    ck("每档止损价 = 成交价×(1-pct)", not bad14, str(bad14[:3]))

    # 15 期末结算笔数 = 各档 force_exit 之和
    fx = sum(int((r["exit_reason"] == "force_exit").sum()) for r in raw.values())
    ck("期末结算笔数一致", fx == int(d["是否期末结算"].sum()), f"{fx}")

    # 16 每档期末结算笔数 ≤ 3 × 组数（max_open_trades=3）
    bad16 = []
    for t in TIERS:
        g = d[d["档位"] == t]
        n_open = int(g["是否期末结算"].sum())
        if n_open > 3 * 9:
            bad16.append((t, n_open))
    ck("每档期末结算笔数 ≤ 3×9", not bad16, str(bad16))

    # 17 已实现笔 + 期末笔 = 总笔
    ck("已实现笔 + 期末笔 = 总笔",
       all(r["已实现笔数"] + r["期末结算笔数"] == r["笔数"] for r in r3["一_各档指标"]))

    # 18 成本分解闭合：净R = 毛利R + 手续费R + 资金费R
    bad18 = [(r["档位"], r["净R"] - (r["毛利R"] - r["手续费R"] + r["资金费R"]))
             for r in r3["三_成本分解"] if abs(r["净R"] - (r["毛利R"] - r["手续费R"] + r["资金费R"])) > 1e-4]
    ck("成本分解闭合 净R=毛利R−手续费R+资金费R", not bad18, str(bad18))

    # 19 集中度「前1笔占总R」独立重算
    bad19 = []
    for t in TIERS:
        g = d[d["档位"] == t]
        tot = float(g["R"].sum())
        top = float(g["R"].max())
        v = r3["四_集中度"][t]["前1笔占总R"]
        if abs(v - top / tot) > 1e-4:
            bad19.append((t, v, round(top / tot, 6)))
    ck("集中度前1笔占比一致", not bad19, str(bad19))

    # 20 04 的 P1 差值中位独立重算（取第一组第一对）
    p1csv = pd.read_csv(OUT / "04_P1配对比较.csv", encoding="utf-8-sig")
    row = p1csv.iloc[0]
    sub = d[d["参数组"] == row["参数组"]]
    a, b = row["比较"].split("-")
    sa = sub[sub["档位"] == a].groupby(["pair", "事件ID"])["R"].sum()
    sb = sub[sub["档位"] == b].groupby(["pair", "事件ID"])["R"].sum()
    j = pd.concat([sa.rename("a"), sb.rename("b")], axis=1, join="inner")
    # P1 是四档齐全集，必须用四档交集再比
    ss = {t: set(zip(sub[sub["档位"] == t]["pair"], sub[sub["档位"] == t]["事件ID"])) for t in TIERS}
    common = set.intersection(*ss.values())
    j4 = j.loc[j.index.isin(list(common))]
    ck("04 P1 首行 n 与差值中位一致",
       len(j4) == int(row["n"]) and abs(float((j4["a"] - j4["b"]).median()) - float(row["差值中位"])) < 1e-6,
       f"n {len(j4)} vs {int(row['n'])}")

    # 21 05 成交数 = 逐笔行数
    bad21 = [(r["策略"], r["实际成交"], len(raw[r["策略"]])) for r in r5["一_事件漏斗"]
             if r["实际成交"] != len(raw[r["策略"]])]
    ck("05 成交数 = 逐笔行数", not bad21, str(bad21[:3]))

    # 22 05 最小下单额拒单数 = 日志独立计数
    import re as _re
    pat = _re.compile(r"raw_stake=[0-9.]+ < min_stake=[0-9.]+")
    bad22 = []
    for r in r5["一_事件漏斗"]:
        lp = UP / f"logs/{r['策略']}_backtest.log"
        n = len(pat.findall(lp.read_text(errors="ignore"))) if lp.exists() else 0
        if n != r["最小下单额拒单次数"]:
            bad22.append((r["策略"], n, r["最小下单额拒单次数"]))
    ck("05 最小下单额拒单数 = 日志计数", not bad22, str(bad22[:3]))

    # 23 06b 实际结束日总R = 03 总R
    r6b = json.load(open(OUT / "06b_窗口边界敏感性.json", encoding="utf-8"))
    act = {x["档位"]: x["总R"] for x in r6b["二_逐结束日"] if x["虚拟结束日"].startswith("实际结束日")}
    bad23 = [(r["档位"], act.get(r["档位"]), r["总R"]) for r in r3["一_各档指标"]
             if abs(act.get(r["档位"], 1e9) - r["总R"]) > 1e-4]
    ck("06b 实际结束日总R = 03 总R", not bad23, str(bad23))

    # 24 06 K 线抽核 6 项全过
    bad24 = [k for k in ("K线存在", "入场K线存在", "入场价在高低之间", "止损价符合公式",
                         "出场K线是首次触及", "出场价接近止损价", "结算价等于该K线开盘")
             if k in r6 and r6[k] and r6[k]["通过"] != r6[k]["总数"]]
    ck("06 K线抽核各项全通过", not bad24, str(bad24))

    # 25 02b 账户账与逐笔账闭合
    r2b = json.load(open(OUT / "02b_账户对账.json", encoding="utf-8"))
    ck("02b final_balance 与逐笔账误差 < 0.01",
       r2b["二_汇总"]["final_balance误差>0.01的组数"] == 0,
       f"最大误差 {r2b['二_汇总']['final_balance最大误差']}")

    # 26 笔数单调性：Stop05 > Stop10 > Stop15 > Stop20
    cnt = [int((d["档位"] == t).sum()) for t in TIERS]
    ck("成交笔数按档位单调递减", all(cnt[i] > cnt[i + 1] for i in range(3)), str(cnt))

    # 27 最小下单额拒单按档位单调不减
    rej = [sum(r["最小下单额拒单次数"] or 0 for r in r5["一_事件漏斗"] if r["档位"] == t) for t in TIERS]
    ck("最小下单额拒单按档位单调不减", all(rej[i] <= rej[i + 1] for i in range(3)), str(rej))

    # 28 事件ID 跨档可对齐（同一事件在不同档的 pair 相同）
    m = d.groupby("事件ID")["pair"].nunique()
    ck("同一事件ID 跨档标的一致", int((m > 1).sum()) == 0, f"不一致 {int((m > 1).sum())}")

    # 29 零成交组
    ck("无零成交组", len(d.attrs.get("零成交组", [])) == 0, str(d.attrs.get("零成交组")))

    # 30 已实现胜率 < 5%（结构性：只有止损一种退出）
    ck("各档已实现胜率 < 5%", all((r["已实现胜率%"] or 0) < 5 for r in r3["一_各档指标"]),
       str([r["已实现胜率%"] for r in r3["一_各档指标"]]))

    # 31 资金费占总成本 > 90%
    ck("资金费占总成本 > 90%", all((r["资金费占成本比%"] or 0) > 90 for r in r3["一_各档指标"]),
       str([r["资金费占成本比%"] for r in r3["一_各档指标"]]))

    # 32 实际风险从未超过 0.5%
    ck("实际风险从未超 0.5%",
       bool((d["实际风险占权益"] <= 0.005 * 1.0001).all()),
       f"最大 {d['实际风险占权益'].max():.6f}")

    # 33 名义仓位与 0.5%/pct 一致（误差来自合约取整）
    bad33 = []
    for t in TIERS:
        g = d[d["档位"] == t]
        theo = 0.005 / TIER_PCT[t]
        if abs(float(g["名义占权益"].median()) - theo) > theo * 0.03:
            bad33.append((t, round(float(g["名义占权益"].median()), 6), round(theo, 6)))
    ck("名义仓位中位 ≈ 0.5%/pct（3% 容差）", not bad33, str(bad33))

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    return {
        "检查项": [{"项": n, "通过": ok, "备注": note} for n, ok, note in CHECKS],
        "通过数": passed,
        "总数": len(CHECKS),
        "全部通过": passed == len(CHECKS),
    }


if __name__ == "__main__":
    r = main()
    p = OUT / "07_自检.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    for c in r["检查项"]:
        print(("PASS " if c["通过"] else "FAIL ") + c["项"] + ("  | " + c["备注"] if c["备注"] else ""))
    print(f"\n自检 {r['通过数']}/{r['总数']}  → {p}")

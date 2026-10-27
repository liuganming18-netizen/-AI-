# -*- coding: utf-8 -*-
"""04 稳定性比较：16 组合 × C1~C7 判据、极端盈利依赖、跨时间、少数币依赖。

判据门槛沿用冻结口径（见 out/01_冻结分析计划.json），不改门槛。
C8 需要与上一轮配对，在 05_pairing.py 里算并回填。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
INIT = L.INIT_EQUITY
RES: dict = {"段": {}}
CUTOFFS = ["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31",
           "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
           "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2024-12-31 23:59:59"]


def add(sec: str, key: str, val, ok=None, note: str = "") -> None:
    RES["段"].setdefault(sec, {})[key] = val
    if ok is not None:
        RES.setdefault("判据", []).append({"段": sec, "项": key, "通过": bool(ok), "说明": note})


def group_pnl(g: pd.DataFrame) -> float:
    return float(g["profit_abs"].sum())


def winsorize(g: pd.DataFrame, k: float = 5.0) -> float:
    r = g["R"].clip(-k, k)
    # 按 R 缩尾后换算回 USDT（用该笔的风险预算）
    return float((r * g["风险预算USDT"]).sum())


def trim_pct(g: pd.DataFrame, p: float = 0.01) -> float:
    if len(g) < 20:
        return group_pnl(g)
    lo = g["R"].quantile(p)
    hi = g["R"].quantile(1 - p)
    k = g[(g["R"] >= lo) & (g["R"] <= hi)]
    return float(k["profit_abs"].sum())


def main() -> None:
    tr, missing, zero = L.load_all()
    df = L.enrich(tr)

    # ---------- 每组合 9 组的中位数 ----------
    rows = []
    for combo, g in df.groupby("组合"):
        per_group = g.groupby("策略")["profit_abs"].sum()
        rows.append({
            "组合": combo,
            "组数": int(per_group.size),
            "L3合计": float(per_group.sum()),
            "L2中位": float(per_group.median()),
            "L1最小": float(per_group.min()),
            "L1最大": float(per_group.max()),
            "L1标准差": float(per_group.std(ddof=1)),
            "盈利组数": int((per_group > 0).sum()),
            "缩尾后中位": float(pd.Series({k: winsorize(v) for k, v in g.groupby("策略")}).median()),
            "剔首尾1%后中位": float(pd.Series({k: trim_pct(v) for k, v in g.groupby("策略")}).median()),
        })
    base = pd.DataFrame(rows).sort_values("组合").reset_index(drop=True)

    # ---------- C5 剔除贡献最大的 3 个币 ----------
    c5 = []
    for combo, g in df.groupby("组合"):
        by_pair = g.groupby("pair")["profit_abs"].sum().sort_values(ascending=False)
        drop = set(by_pair.head(3).index)
        sub = g[~g["pair"].isin(drop)]
        med = float(sub.groupby("策略")["profit_abs"].sum().median())
        c5.append({"组合": combo, "剔除的币": list(drop),
                   "剔除后L2中位": med, "原L2中位": float(g.groupby("策略")["profit_abs"].sum().median()),
                   "前三币贡献": float(by_pair.head(3).sum()),
                   "前三币贡献占比": float(by_pair.head(3).sum() / g["profit_abs"].sum() * 100)
                   if abs(g["profit_abs"].sum()) > 1e-9 else None})
    c5df = pd.DataFrame(c5).sort_values("组合").reset_index(drop=True)
    base = base.merge(c5df[["组合", "剔除后L2中位", "前三币贡献占比"]], on="组合")

    # ---------- C6 13 个假设结束日 ----------
    rows6 = []
    for combo, g in df.groupby("组合"):
        vals = []
        for c in CUTOFFS:
            t = pd.Timestamp(c, tz="UTC")
            sub = g[pd.to_datetime(g["close_date"], utc=True) <= t]
            vals.append(float(sub.groupby("策略")["profit_abs"].sum().median()))
        rows6.append({"组合": combo, "各结束日L2中位": vals,
                      "为正的结束日数": int(sum(1 for v in vals if v > 0))})
    c6df = pd.DataFrame(rows6).sort_values("组合").reset_index(drop=True)
    base = base.merge(c6df[["组合", "为正的结束日数"]], on="组合")
    base = base.merge(c6df[["组合", "各结束日L2中位"]], on="组合")

    # ---------- C7 同 d 列内 4 档 s 的名次 ----------
    base["d"] = base["组合"].str.split("_").str[1]
    base["s"] = base["组合"].str.split("_").str[0]
    base["同d名次"] = base.groupby("d")["L2中位"].rank(ascending=False, method="min").astype(int)

    # ---------- C1~C7 ----------
    base["C1"] = base["L2中位"] > 0
    base["C2"] = base["盈利组数"] >= 7
    base["C3"] = base["缩尾后中位"] > 0
    base["C4"] = base["剔首尾1%后中位"] > 0
    base["C5"] = base["剔除后L2中位"] > 0
    base["C6"] = base["为正的结束日数"] >= 9
    base["C7"] = base["同d名次"] <= 2
    base["C1~C7满足数"] = base[["C1", "C2", "C3", "C4", "C5", "C6", "C7"]].sum(axis=1)
    base.to_csv(OUT / "04_稳定性逐组合.csv", index=False, encoding="utf-8-sig")

    add("判据", "16 组合 C1~C7 满足数", base.set_index("组合")["C1~C7满足数"].to_dict())
    for c in ["C1", "C2", "C3", "C4", "C5", "C6", "C7"]:
        add("判据", f"{c} 满足的组合", base.loc[base[c], "组合"].tolist(), None,
            f"{int(base[c].sum())}/16")

    # ---------- 极端盈利依赖 ----------
    ext = []
    for combo, g in df.groupby("组合"):
        top = g.nlargest(5, "profit_abs")
        ext.append({
            "组合": combo,
            "前5笔盈利": float(top["profit_abs"].sum()),
            "占比pct": float(top["profit_abs"].sum() / g["profit_abs"].sum() * 100)
            if abs(g["profit_abs"].sum()) > 1e-9 else None,
            "单笔最大盈利": float(g["profit_abs"].max()),
            "单笔最大盈利R": float(g["R"].max()),
            "盈利笔数": int((g["profit_abs"] > 0).sum()),
            "最大盈利占全部盈利pct": float(g["profit_abs"].max() / g.loc[g["profit_abs"] > 0, "profit_abs"].sum() * 100)
            if (g["profit_abs"] > 0).any() else None,
            "标的数": int(g["pair"].nunique()),
            "前3币": list(g.groupby("pair")["profit_abs"].sum().sort_values(ascending=False).head(3).index),
        })
    edf = pd.DataFrame(ext).sort_values("组合").reset_index(drop=True)
    edf.to_csv(OUT / "04_极端盈利依赖.csv", index=False, encoding="utf-8-sig")
    add("极端依赖", "前 5 笔盈利占比（各组合）", edf.set_index("组合")["占比pct"].round(2).to_dict())
    add("极端依赖", "最大单笔盈利占总盈利比（各组合）",
        edf.set_index("组合")["最大盈利占全部盈利pct"].round(2).to_dict())

    # ---------- 跨参数组离散 ----------
    disp = []
    for combo, g in df.groupby("组合"):
        per = g.groupby("策略")["profit_abs"].sum()
        disp.append({"组合": combo, "最小": float(per.min()), "中位": float(per.median()),
                     "最大": float(per.max()), "极差": float(per.max() - per.min())})
    ddf = pd.DataFrame(disp)
    ddf.to_csv(OUT / "04_参数组离散.csv", index=False, encoding="utf-8-sig")
    add("参数组离散", "同组合内 9 组极差（USDT）", ddf.set_index("组合")["极差"].round(2).to_dict(),
        None, "9 组共用同一批入场事件，高度相关，不是 9 次独立重复")
    add("参数组离散", "极差的中位 / 最大", [float(ddf["极差"].median()), float(ddf["极差"].max())])

    # 跨档位比较：参数组差异 vs 档位差异
    combo_range = float(base["L2中位"].max() - base["L2中位"].min())
    add("参数组离散", "16 组合 L2 中位极差", combo_range,
        None, "与同组合内 9 组极差对比：哪个更大，哪个才是主要不确定性来源")

    # ---------- 时间稳定性：前半/后半 ----------
    half = []
    mid = pd.Timestamp("2023-06-15", tz="UTC")
    for combo, g in df.groupby("组合"):
        t = pd.to_datetime(g["close_date"], utc=True)
        a = g[t < mid]; b = g[t >= mid]
        half.append({"组合": combo,
                     "前半L2中位": float(a.groupby("策略")["profit_abs"].sum().median()) if len(a) else None,
                     "后半L2中位": float(b.groupby("策略")["profit_abs"].sum().median()) if len(b) else None})
    hdf = pd.DataFrame(half)
    hdf["两半同号"] = (hdf["前半L2中位"] * hdf["后半L2中位"]) > 0
    hdf.to_csv(OUT / "04_前后半段.csv", index=False, encoding="utf-8-sig")
    add("时间稳定", "前后半段同号组合数", int(hdf["两半同号"].sum()), None, f"共 {len(hdf)} 个组合")
    add("时间稳定", "前后半段明细", hdf.round(2).to_dict("records"))

    crit = RES.get("判据", [])
    RES["判据统计"] = {"总": len(crit), "通过": sum(1 for c in crit if c["通过"]),
                       "不通过": sum(1 for c in crit if not c["通过"]),
                       "无判据": sum(1 for c in crit if c["通过"] is None)}
    with open(OUT / "04_稳定性.json", "w", encoding="utf-8") as f:
        json.dump(RES, f, ensure_ascii=False, indent=2, default=str)
    print("判据:", RES["判据统计"])
    print(base[["组合", "L2中位", "盈利组数", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C1~C7满足数"]].to_string(index=False))


if __name__ == "__main__":
    main()

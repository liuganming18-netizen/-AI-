# -*- coding: utf-8 -*-
"""04b 稳定性判据 C1~C8 机械裁决 + 三套稳健口径 + 假设结束日敏感性。

判据全部来自 out/01_冻结分析计划.json，逐条机器判定，不人工取舍。
排序对象（预登记）：主指标 = 已实现R（9 个参数组取中位）；辅助 = MTM 最大回撤（9 组取中位）。
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
SEED = 20261022


def rank_of(s: pd.Series) -> dict:
    """越大越好，返回 {档位: 名次}。"""
    order = s.sort_values(ascending=False)
    return {k: i + 1 for i, k in enumerate(order.index)}


def main() -> None:
    per = pd.read_csv(OUT / "03_逐组指标.csv", encoding="utf-8-sig")
    per = per[per["状态"] == "ok"]
    pair = pd.read_csv(OUT / "04_两两配对_BH.csv", encoding="utf-8-sig") \
        if (OUT / "04_两两配对_BH.csv").exists() else pd.DataFrame()
    dec = pd.read_csv(OUT / "04_Δ分解.csv", encoding="utf-8-sig")
    with open(OUT / "04_配对与分解.json", encoding="utf-8") as f:
        pj = json.load(f)

    tiers = [t for t in L.TIERS if t in set(per["档位"])]
    verdict: dict = {"工单": "FT-STOP-GRID-AUDIT-02 / 04b 稳定性裁决", "已落盘组数": int(len(per)),
                     "档位": tiers}

    # ---------- 主排序 ----------
    base_real = per.groupby("档位")["已实现R"].median().reindex(tiers)
    base_total = per.groupby("档位")["总R"].median().reindex(tiers)
    base_dd = per.groupby("档位")["MTM最大回撤USDT"].median().reindex(tiers)
    verdict["主指标"] = {
        "已实现R中位": base_real.round(4).to_dict(),
        "总R中位": base_total.round(4).to_dict(),
        "MTM最大回撤中位": base_dd.round(2).to_dict(),
        "已实现R名次": rank_of(base_real),
        "回撤名次（小者优）": {k: i + 1 for i, k in enumerate(base_dd.sort_values().index)},
    }

    # ---------- C1：P1 上已实现中位 R 最优 ----------
    p1 = pj.get("P1", {}).get("各档已实现R中位", {})
    c1 = None
    if p1:
        s = pd.Series({k: v for k, v in p1.items() if v is not None})
        c1 = {"值": s.round(4).to_dict(), "名次": rank_of(s)}
        best = s.idxmax()
        c1["判定"] = {"最优档": best}
    verdict["C1_P1已实现中位最优"] = c1

    # ---------- C2：三个对手的区间均不含 0，≥7/9 参数组 ----------
    c2 = {"明细": pj.get("C2_配对显著性", {}), "判定": {}}
    for t in tiers:
        d = pj.get("C2_配对显著性", {}).get(t, {})
        n_all = d.get("按参数组（3 个对手全胜的组数）")
        c2["判定"][t] = {"三对手全胜参数组数": n_all, "满足": bool(n_all is not None and n_all >= 7)}
    verdict["C2_配对显著性"] = c2

    # ---------- C3：9 个参数组方向一致 ≥7 ----------
    c3 = {"判定": {}}
    if len(pair):
        for t in tiers:
            sub = pair[(pair["档位A"] == t) | (pair["档位B"] == t)].copy()
            if sub.empty:
                continue
            sub["d"] = np.where(sub["档位A"] == t, sub["ΔR中位"], -sub["ΔR中位"])
            per_opp = {}
            for opp, g in sub.groupby(np.where(sub["档位A"] == t, sub["档位B"], sub["档位A"])):
                sign = np.sign(g["d"].median())
                agree = int((np.sign(g["d"]) == sign).sum())
                per_opp[str(opp)] = {"方向": float(sign), "一致组数": agree, "总组数": int(len(g)),
                                     "满足": bool(agree >= 7)}
            c3["判定"][t] = {"对手": per_opp,
                             "满足": bool(per_opp and all(v["满足"] for v in per_opp.values()))}
    verdict["C3_参数组方向一致"] = c3

    # ---------- C4：13 个季度块方向一致 ≥9 ----------
    # 用 03 的逐笔重建季度 R：按 (档位,参数组,季度) 汇总，再对每个对手比较块内中位
    from importlib import import_module
    p4 = import_module("04_pairing")
    d2 = p4.add_r(p4.load_side(L.UP))
    d2v = d2[d2["事件ID"].notna()]
    key = ["参数组", "pair", "事件ID"]
    c4 = {"判定": {}}
    for t in tiers:
        others = [o for o in tiers if o != t]
        detail = {}
        for o in others:
            a = d2v[d2v["档位"] == t].set_index(key + ["季度"])
            b = d2v[d2v["档位"] == o].set_index(key + ["季度"])
            a = a[~a.index.duplicated()]
            b = b[~b.index.duplicated()]
            common = a.index.intersection(b.index)
            if len(common) < 10:
                detail[o] = {"块数": 0, "一致块": 0, "满足": False}
                continue
            diff = (a.loc[common, "R"] - b.loc[common, "R"])
            q = pd.Series(diff.to_numpy(), index=pd.Index([i[-1] for i in common], name="q"))
            s = q.groupby("q").sum()
            agree = int((np.sign(s) == np.sign(s.sum())).sum())
            detail[o] = {"块数": int(len(s)), "一致块": agree, "满足": bool(agree >= 9)}
        c4["判定"][t] = {"对手": detail,
                         "满足": bool(detail and all(v["满足"] for v in detail.values()))}
    verdict["C4_季度块一致"] = c4

    # ---------- C5：缩尾 ±5R、剔除首尾 1% 后排序不变 ----------
    d2w = d2.copy()
    d2w["R"] = d2w["R"].clip(-5, 5)
    real_w = d2w[d2w["exit_reason"] != "force_exit"]
    w = real_w.groupby(["策略", "档位"])["R"].sum().reset_index()
    w = w.groupby("档位")["R"].median().reindex(tiers)
    # 剔除首尾 1%（按 R 分位）
    lo, hi = d2["R"].quantile(0.01), d2["R"].quantile(0.99)
    d2t = d2[(d2["R"] >= lo) & (d2["R"] <= hi)]
    real_t = d2t[d2t["exit_reason"] != "force_exit"]
    t1 = real_t.groupby(["策略", "档位"])["R"].sum().reset_index()
    t1 = t1.groupby("档位")["R"].median().reindex(tiers)
    verdict["C5_缩尾与截尾"] = {
        "原始": base_real.round(4).to_dict(),
        "缩尾±5R": w.round(4).to_dict(),
        "剔除首尾1%": t1.round(4).to_dict(),
        "原始名次": rank_of(base_real),
        "缩尾名次": rank_of(w),
        "截尾名次": rank_of(t1),
        "判定": {t: bool(rank_of(base_real).get(t) == rank_of(w).get(t) == rank_of(t1).get(t))
                 for t in tiers},
    }

    # ---------- C6：剔除贡献最大的 3 个币后排序不变 ----------
    by_pair = real_w.groupby("pair")["R"].sum().sort_values(ascending=False)
    drop3 = set(by_pair.index[:3])
    d6 = real_w[~real_w["pair"].isin(drop3)]
    g6 = d6.groupby(["策略", "档位"])["R"].sum().reset_index().groupby("档位")["R"].median().reindex(tiers)
    verdict["C6_剔除3币"] = {
        "剔除的币": sorted(drop3),
        "剔除后": g6.round(4).to_dict(),
        "剔除后名次": rank_of(g6),
        "判定": {t: bool(rank_of(base_real).get(t) == rank_of(g6).get(t)) for t in tiers},
    }

    # ---------- C7：已实现 R > 0 且期末浮盈占总 R < 30% ----------
    per2 = per.copy()
    per2["期末占比"] = np.where(per2["总R"].abs() > 1e-12,
                            per2["期末R"] / per2["总R"], np.nan)
    c7 = {}
    for t in tiers:
        s = per2[per2["档位"] == t]
        c7[t] = {
            "已实现R中位": float(s["已实现R"].median()),
            "期末R中位": float(s["期末R"].median()),
            "期末占比中位": float(s["期末占比"].median()) if s["期末占比"].notna().any() else None,
            "满足组数": int(((s["已实现R"] > 0) & (s["期末占比"] < 0.30)).sum()),
            "组数": int(len(s)),
            "满足": bool((s["已实现R"] > 0).all() and (s["期末占比"].dropna() < 0.30).all()),
        }
    verdict["C7_已实现为主"] = c7

    # ---------- C8：Δ 两边都成交 / Δ总R ≥ 50% ----------
    c8 = {}
    for t in tiers:
        s = dec[dec["档位"] == t]
        if s.empty:
            continue
        c8[t] = {"C8满足组数": int((s["C8_盈利保护为主"] == True).sum()), "组数": int(len(s)),
                 "Δ两边都成交占Δ总R中位": float(s["Δ两边都成交占Δ总R"].median())
                 if s["Δ两边都成交占Δ总R"].notna().any() else None,
                 "满足": bool((s["C8_盈利保护为主"] == True).sum() >= max(5, len(s) // 2 + 1))}
    verdict["C8_盈利保护为主"] = c8

    # ---------- 汇总 ----------
    summary = {}
    for t in tiers:
        items = {
            "C1": bool(c1 and c1["名次"].get(t) == 1),
            "C2": bool(c2["判定"].get(t, {}).get("满足")),
            "C3": bool(c3["判定"].get(t, {}).get("满足")),
            "C4": bool(c4["判定"].get(t, {}).get("满足")),
            "C5": bool(verdict["C5_缩尾与截尾"]["判定"].get(t)),
            "C6": bool(verdict["C6_剔除3币"]["判定"].get(t)),
            "C7": bool(c7.get(t, {}).get("满足")),
            "C8": bool(c8.get(t, {}).get("满足")),
        }
        summary[t] = {"判据": items, "满足条数": int(sum(items.values())),
                      "未满足": [k for k, v in items.items() if not v]}
    verdict["汇总"] = summary
    verdict["裁决说明"] = ("满足条数最多者不得直接称为更稳定；必须逐条列出未满足项。"
                     "C1/C2/C3/C4 针对收益排序，C5/C6 针对稳健性，C7 针对收益来源，C8 针对归因。")

    with open(OUT / "04b_稳定性裁决.json", "w", encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=2, default=str)

    print("主指标 已实现R中位:", base_real.round(2).to_dict())
    print("主指标 总R中位:", base_total.round(2).to_dict())
    print("MTM回撤中位:", base_dd.round(1).to_dict())
    for t in tiers:
        v = summary[t]
        print(f"  {t}: {v['满足条数']}/8 未满足={v['未满足']}")


if __name__ == "__main__":
    main()

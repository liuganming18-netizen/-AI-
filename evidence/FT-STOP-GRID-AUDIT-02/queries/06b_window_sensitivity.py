# -*- coding: utf-8 -*-
"""06b 假设结束日敏感性：13 个假想结束日下的盯市权益与档位排序。

动机：本轮"改善"里有多少只是"把仓位拿到期末"的账面浮盈？把结束日往前挪，
      看档位排序和总收益是否稳定。权益用 15m 收盘盯市（含未平仓），不是已实现曲线。
"""
from __future__ import annotations

import json
from importlib import import_module

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
m3 = import_module("03_metrics")

ENDS = ["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31",
        "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
        "2024-03-31", "2024-06-30", "2024-09-30", "2024-11-30", "2024-12-31"]


def ms_of(day: str) -> int:
    return int(pd.Timestamp(day, tz="UTC").value // 10 ** 6)


def equity_at(g: pd.DataFrame, E: int) -> float:
    """E 时刻的盯市权益：已实现 + 未平仓按 15m 收盘价盯市（含已发生资金费）。"""
    realized = float(g.loc[g["close_ms"] <= E, "profit_abs"].sum())
    eq = L.INIT_EQUITY + realized
    for _, r in g[(g["open_ms"] <= E) & (g["close_ms"] > E)].iterrows():
        px_s = m3.price_15m(r["pair"])
        if px_s is None:
            continue
        px = float(m3._ffill_at(px_s, np.array([E]))[0])
        if not np.isfinite(px):
            continue
        ev, fee, tot = m3.funding_events(r["pair"], int(r["open_ms"]), E, float(r["amount"]))
        eq += (px - float(r["open_rate"])) * float(r["amount"]) \
            - float(r["amount"]) * float(r["open_rate"]) * float(r["fee_open"]) + tot
    return eq


def main() -> None:
    df, missing = L.load_all()
    d = L.equity_curve(df)
    rows = []
    for E_day in ENDS:
        E = ms_of(E_day)
        for name, g in d.groupby("策略", sort=False):
            g2 = g[g["open_ms"] <= E]
            if g2.empty:
                continue
            eq = equity_at(g2, E)
            rows.append({
                "结束日": E_day, "策略": name, "档位": L.tier_of(name), "参数组": L.suffix_of(name),
                "盯市权益": eq, "总R": (eq - L.INIT_EQUITY) / (L.INIT_EQUITY * L.RISK_RATE),
                "已实现R": float(g2.loc[g2["close_ms"] <= E, "R"].sum()),
            })
    w = pd.DataFrame(rows)
    w.to_csv(OUT / "06b_假设结束日.csv", index=False, encoding="utf-8-sig")

    res = {"工单": "FT-STOP-GRID-AUDIT-02 / 06b 假设结束日敏感性",
           "结束日": ENDS, "口径": "15m 收盘盯市（含未平仓浮盈与已发生资金费）", "分结束日": {}}
    for E_day in ENDS:
        s = w[w["结束日"] == E_day]
        if s.empty:
            continue
        med = s.groupby("档位")["总R"].median()
        order = med.sort_values(ascending=False)
        res["分结束日"][E_day] = {
            "组数": int(s["策略"].nunique()),
            "各档总R中位": med.round(3).to_dict(),
            "排序": list(order.index),
            "最优档": str(order.index[0]) if len(order) else None,
        }
    # 排序稳定度
    orders = [tuple(v["排序"]) for v in res["分结束日"].values() if v["排序"]]
    res["排序稳定度"] = {
        "不同排序个数": len(set(orders)),
        "最常见排序": max(set(orders), key=orders.count) if orders else None,
        "最常见出现次数": orders.count(max(set(orders), key=orders.count)) if orders else 0,
        "总结束日数": len(orders),
    }
    with open(OUT / "06b_假设结束日.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)

    print("结束日数", len(res["分结束日"]))
    for k, v in res["分结束日"].items():
        print(f"  {k}: 最优={v['最优档']} 排序={'>'.join(v['排序'])}")
    print("稳定度", res["排序稳定度"])


if __name__ == "__main__":
    main()

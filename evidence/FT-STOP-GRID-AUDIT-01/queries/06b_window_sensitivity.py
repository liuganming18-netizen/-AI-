# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 06b_窗口边界敏感性

为什么必须做：这套设计里唯一的平仓方式是止损，所以每个组合在窗口末最多留 3 笔浮盈。
「总 R」几乎完全由这 3 笔在**回测结束日**的浮盈决定。若不检验窗口边界，
就等于用「2024-12-31 恰好持有哪 3 个币」来给止损档位排名。

做法（不重跑任何回测）：
  对 13 个季度末 q，把窗口假想成「在 q 结束」：
    总 R(q) = Σ(close_ms ≤ q 的已实现 R) + Σ(当时仍持仓的浮动 R)
  浮动 R 用该币在 q 末那根 15m K 线的收盘价换算：R ≈ (price_q / open_rate − 1) / pct
  （忽略手续费与资金费，只做量级与排序判断；误差在报告里单列）
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from _lib import OUT, TIERS, equity_curve, load_all, load_kline

WINDOW_END_MS = 1735686000000  # 2024-12-31 23:00 UTC，与订单审计 q_end 一致
WINDOW_START_MS = 1638316800000  # 2021-12-01 00:00 UTC，与 timerange 起点一致


def quarter_ends() -> list[tuple[str, int]]:
    out = []
    for y in range(2021, 2025):
        for q, (m, d) in enumerate([(3, 31), (6, 30), (9, 30), (12, 31)], start=1):
            ts = pd.Timestamp(year=y, month=m, day=d, hour=23, minute=45, tz="UTC")
            ms = int(ts.value // 10 ** 6)
            if WINDOW_START_MS <= ms < WINDOW_END_MS:
                out.append((f"{y}Q{q}", ms))
    out.append(("实际结束日2024-12-31", WINDOW_END_MS))
    return out


def main() -> dict:
    df, missing = load_all()
    d = equity_curve(df)

    cache: dict[str, pd.DataFrame | None] = {}

    def price_at(pair: str, ms: int) -> float | None:
        if pair not in cache:
            cache[pair] = load_kline(pair)
        k = cache[pair]
        if k is None or k.empty:
            return None
        s = k[k["ms"] <= ms]
        return float(s["close"].iloc[-1]) if len(s) else None

    qs = quarter_ends()
    rows = []
    for (qname, qms) in qs:
        for t in TIERS:
            g = d[d["档位"] == t]
            if qms >= WINDOW_END_MS:
                # 实际结束日：直接用逐笔的期末结算 R，保证与 §3 完全同口径
                realized = g[~g["是否期末结算"]]["R"].sum()
                float_r = g[g["是否期末结算"]]["R"].sum()
                n_open = int(g["是否期末结算"].sum())
                missing_k = 0
                rows.append({
                    "虚拟结束日": qname, "档位": t,
                    "已实现R": round(float(realized), 6),
                    "浮动R": round(float(float_r), 6),
                    "总R": round(float(realized) + float(float_r), 6),
                    "当时持仓笔数": n_open, "缺K线笔数": missing_k,
                })
                continue
            realized = g[(~g["是否期末结算"]) & (g["close_ms"] <= qms)]["R"].sum()
            open_now = g[(g["open_ms"] <= qms) & (g["close_ms"] > qms)]
            float_r, n_open, missing_k = 0.0, 0, 0
            for _, r in open_now.iterrows():
                px = price_at(r["pair"], qms)
                if px is None:
                    missing_k += 1
                    continue
                amt = float(r["amount"])
                notional = amt * float(r["open_rate"])
                # 未平仓到 q 时点：价差 + 已付的开仓费 + 按持有比例摊到的资金费
                gross = (px - float(r["open_rate"])) * amt
                open_fee = notional * float(r["fee_open"])
                held = max(qms - int(r["open_ms"]), 0)
                total = max(int(r["close_ms"]) - int(r["open_ms"]), 1)
                fund = float(r["资金费USDT"]) * min(held / total, 1.0)
                float_r += (gross - open_fee + fund) / float(r["风险预算USDT"])
                n_open += 1
            rows.append({
                "虚拟结束日": qname, "档位": t,
                "已实现R": round(float(realized), 6),
                "浮动R": round(float(float_r), 6),
                "总R": round(float(realized) + float_r, 6),
                "当时持仓笔数": n_open,
                "缺K线笔数": missing_k,
            })
    rdf = pd.DataFrame(rows)
    rdf.to_csv(OUT / "06b_窗口边界敏感性.csv", index=False, encoding="utf-8-sig")

    summary = {}
    for t in TIERS:
        g = rdf[rdf["档位"] == t]
        summary[t] = {
            "虚拟结束日数": int(len(g)),
            "总R最小": round(float(g["总R"].min()), 6),
            "总R中位": round(float(g["总R"].median()), 6),
            "总R最大": round(float(g["总R"].max()), 6),
            "总R极差": round(float(g["总R"].max() - g["总R"].min()), 6),
            "正R的结束日数": int((g["总R"] > 0).sum()),
            "已实现R最小": round(float(g["已实现R"].min()), 6),
            "已实现R最大": round(float(g["已实现R"].max()), 6),
            "浮动R最小": round(float(g["浮动R"].min()), 6),
            "浮动R最大": round(float(g["浮动R"].max()), 6),
            "当时持仓笔数中位": float(g["当时持仓笔数"].median()),
        }

    # 排名是否随结束日翻转
    rank = rdf.pivot_table(index="虚拟结束日", columns="档位", values="总R")
    rank_order = rank.rank(axis=1, ascending=False)
    flips = {t: int((rank_order[t] == 1).sum()) for t in TIERS}
    # 某结束日四档全为 0 时，第一名没有意义，单独标出
    best_by_q = {}
    for q, row in rank.iterrows():
        if float(row.abs().sum()) < 1e-12:
            best_by_q[q] = "全部为0（无意义）"
        else:
            best_by_q[q] = str(row.idxmax())
    meaningful = {q: v for q, v in best_by_q.items() if v != "全部为0（无意义）"}

    return {
        "一_各档敏感性": summary,
        "二_逐结束日": rdf.to_dict("records"),
        "三_第一名次数": flips,
        "四_各结束日第一名": best_by_q,
        "五_有意义的结束日数": len(meaningful),
        "六_第一名是否唯一": len(set(meaningful.values())) == 1,
        "六_说明": "浮动R 已扣开仓费与按持有比例摊到的资金费；平仓费尚未发生故未扣，故与实际结算口径仍有小幅差（差 1 个平仓费）",
    }


if __name__ == "__main__":
    r = main()
    p = OUT / "06b_窗口边界敏感性.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print(f"已写出 {p} ({p.stat().st_size} 字节)")
    print(json.dumps(r["一_各档敏感性"], ensure_ascii=False, indent=1))
    print("各结束日第一名：", json.dumps(r["四_各结束日第一名"], ensure_ascii=False))
    print("有意义的结束日数：", r["五_有意义的结束日数"], " 第一名是否唯一：", r["六_第一名是否唯一"])

# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 03_指标重算

从逐笔重算，不采信上游汇总。严格按冻结计划第二节：
  · 已实现 R 与期末未实现 R 分列，主结论用已实现
  · 成本三列（毛利/手续费/资金费），不在 profit_abs 外重复扣
  · 胜率只用已实现笔
  · 回撤两套（R 口径、USDT 口径）
  · 集中度必报（单笔/前5笔/单币/前3币/期末浮盈前3）
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from _lib import OUT, TIER_PCT as PCT, TIERS, equity_curve, load_all, max_drawdown_r

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 60)


def tier_block(g: pd.DataFrame) -> dict:
    r = g[~g["是否期末结算"]]
    o = g[g["是否期末结算"]]
    pnl = pd.to_numeric(g["profit_abs"], errors="coerce")
    rp = pd.to_numeric(r["profit_abs"], errors="coerce")
    win = rp[rp > 0].sum()
    loss = rp[rp < 0].sum()
    total_r = float(g["R"].sum())
    return {
        "笔数": int(len(g)),
        "事件数": int(g["事件ID"].nunique()),
        "标的数": int(g["pair"].nunique()),
        "已实现笔数": int(len(r)),
        "期末结算笔数": int(len(o)),
        "已实现R": round(float(r["R"].sum()), 6),
        "期末结算R": round(float(o["R"].sum()), 6),
        "总R": round(total_r, 6),
        "期末R占总R比": round(float(o["R"].sum()) / total_r, 6) if abs(total_r) > 1e-12 else None,
        "已实现净额USDT": round(float(rp.sum()), 4),
        "期末浮盈USDT": round(float(pd.to_numeric(o["profit_abs"], errors="coerce").sum()), 4),
        "全口径净额USDT": round(float(pnl.sum()), 4),
        "已实现胜率%": round(float((rp > 0).mean() * 100), 4) if len(r) else None,
        "已实现平均R": round(float(r["R"].mean()), 6) if len(r) else None,
        "已实现中位R": round(float(r["R"].median()), 6) if len(r) else None,
        "已实现PF": round(float(win / abs(loss)), 6) if loss < 0 else None,
        "最差单笔R": round(float(g["R"].min()), 6) if len(g) else None,
        "最好单笔R": round(float(g["R"].max()), 6) if len(g) else None,
        "亏损超1.5R笔数": int((g["R"] < -1.5).sum()),
        "亏损超2R笔数": int((g["R"] < -2.0).sum()),
        "手续费R合计": round(float(g["手续费R"].sum()), 6),
        "资金费R合计": round(float(g["资金费R"].sum()), 6),
        "成本R合计": round(float(g["手续费R"].sum() + g["资金费R"].sum()), 6),
        "持仓小时中位": round(float(g["持仓小时"].median()), 2) if len(g) else None,
        "持仓小时均值": round(float(g["持仓小时"].mean()), 2) if len(g) else None,
        "持仓小时_p90": round(float(g["持仓小时"].quantile(0.9)), 2) if len(g) else None,
        "持仓小时_最大": round(float(g["持仓小时"].max()), 2) if len(g) else None,
        "持仓超240h笔数": int((g["持仓小时"] > 240).sum()),
        "持仓超240h占比%": round(float((g["持仓小时"] > 240).mean() * 100), 2) if len(g) else None,
        "资金费占名义_中位%": round(float((g["资金费USDT"] / g["名义USDT"]).median() * 100), 4) if len(g) else None,
        "资金费占名义_最负%": round(float((g["资金费USDT"] / g["名义USDT"]).min() * 100), 4) if len(g) else None,
        "资金费占成本比%": round(float(g["资金费USDT"].abs().sum() /
                                    max(g["资金费USDT"].abs().sum() + g["手续费USDT"].abs().sum(), 1e-12) * 100), 2) if len(g) else None,
        "最大回撤R": round(max_drawdown_r(pd.to_numeric(g.sort_values("close_ms")["R"], errors="coerce").to_numpy()), 6),
        "最大回撤USDT": round(max_drawdown_r(pd.to_numeric(g.sort_values("close_ms")["profit_abs"], errors="coerce").to_numpy()), 4),
        "名义占权益中位": round(float(g["名义占权益"].median()), 6),
        "实际风险占权益中位": round(float(g["实际风险占权益"].median()), 6),
    }


def concentration(g: pd.DataFrame) -> dict:
    rr = g["R"].astype(float)
    order = rr.sort_values(ascending=False)
    tot = float(rr.sum())
    pos = order[order > 0]
    by_pair = g.groupby("pair")["R"].sum().sort_values(ascending=False)
    o = g[g["是否期末结算"]]
    orr = o["R"].astype(float).sort_values(ascending=False)
    return {
        "总R": round(tot, 6),
        "前1笔占总R": round(float(order.iloc[0]) / tot, 6) if tot and len(order) else None,
        "前5笔占总R": round(float(order.head(5).sum()) / tot, 6) if tot and len(order) else None,
        "前5笔占正R": round(float(order.head(5).sum()) / float(pos.sum()), 6) if pos.sum() > 0 else None,
        "单币最大贡献占总R": round(float(by_pair.iloc[0]) / tot, 6) if tot and len(by_pair) else None,
        "最大贡献币": str(by_pair.index[0]) if len(by_pair) else None,
        "前3币占总R": round(float(by_pair.head(3).sum()) / tot, 6) if tot and len(by_pair) else None,
        "期末浮盈前3占总浮盈": round(float(orr.head(3).sum()) / float(orr.sum()), 6) if len(orr) and abs(orr.sum()) > 1e-12 else None,
        "期末浮盈笔数": int(len(o)),
    }


def tail_recurrence(d: pd.DataFrame) -> dict:
    """9 个参数组是不是真的独立重复？

    如果同一批尾部事件（同 pair 同 event）在 9 个组里反复贡献正 R，
    那么「9 组同向」不是稳健性证据，只是同一批彩票被数了 9 次。
    """
    ev = d.groupby(["档位", "参数组", "pair", "事件ID"], as_index=False)["R"].sum()
    out = {}
    for t in TIERS:
        sub = ev[ev["档位"] == t]
        if sub.empty:
            continue
        agg = sub.groupby(["pair", "事件ID"]).agg(R合计=("R", "sum"), 出现组数=("参数组", "nunique"))
        top = agg.sort_values("R合计", ascending=False).head(5)
        n_groups = sub["参数组"].nunique()
        out[t] = {
            "参数组数": int(n_groups),
            "独立事件数": int(len(agg)),
            "事件重复度": round(float(len(sub) / max(len(agg), 1)), 3),
            "前5大正R事件": [
                {"pair": str(p), "事件ID": str(e), "R合计": round(float(r["R合计"]), 4),
                 "出现组数": int(r["出现组数"]), "占组数比": round(float(r["出现组数"]) / n_groups, 3)}
                for (p, e), r in top.iterrows()
            ],
            "前5大事件R合计": round(float(top["R合计"].sum()), 4),
            "全部事件R合计": round(float(agg["R合计"].sum()), 4),
            "前5占比": round(float(top["R合计"].sum()) / float(agg["R合计"].sum()), 4)
            if abs(float(agg["R合计"].sum())) > 1e-12 else None,
        }
    return out


def main() -> dict:
    df, missing = load_all()
    d = equity_curve(df)

    per_group = []
    for (t, s), g in d.groupby(["档位", "参数组"], sort=False):
        row = {"档位": t, "参数组": s}
        row.update(tier_block(g))
        per_group.append(row)
    pg = pd.DataFrame(per_group)
    order = {t: i for i, t in enumerate(TIERS)}
    pg["_o"] = pg["档位"].map(order)
    pg = pg.sort_values(["_o", "参数组"]).drop(columns="_o")
    pg.to_csv(OUT / "03_各组指标.csv", index=False, encoding="utf-8-sig")

    per_tier = []
    conc = {}
    for t in TIERS:
        g = d[d["档位"] == t]
        row = {"档位": t}
        row.update(tier_block(g))
        per_tier.append(row)
        conc[t] = concentration(g)
    pt = pd.DataFrame(per_tier)
    pt.to_csv(OUT / "03_各档指标.csv", index=False, encoding="utf-8-sig")

    # 9 组的中位数与同向比例（避免把 9 组当独立样本）
    med = pg.groupby("档位").agg(
        组数=("参数组", "count"),
        笔数中位=("笔数", "median"),
        已实现R中位=("已实现R", "median"),
        总R中位=("总R", "median"),
        已实现胜率中位=("已实现胜率%", "median"),
        已实现R正组数=("已实现R", lambda x: int((x > 0).sum())),
        总R正组数=("总R", lambda x: int((x > 0).sum())),
    ).reset_index()
    med.to_csv(OUT / "03_各档九组稳健汇总.csv", index=False, encoding="utf-8-sig")

    # 成本分解：把「毛利 R」从净 R 里剥出来
    cost = []
    for t in TIERS:
        g = d[d["档位"] == t]
        net = float(g["R"].sum())
        fee = float(g["手续费R"].sum())
        fund = float(g["资金费R"].sum())
        cost.append({
            "档位": t,
            "笔数": int(len(g)),
            "净R": round(net, 6),
            "手续费R": round(fee, 6),
            "资金费R": round(fund, 6),
            "毛利R": round(net + fee - fund, 6),  # profit_abs = 价差 − 手续费 + 资金费 ⇒ 价差 = 净 + 手续费 − 资金费
            "每笔手续费R": round(fee / len(g), 6) if len(g) else None,
            "每笔资金费R": round(fund / len(g), 6) if len(g) else None,
            "每笔成本R": round((fee + fund) / len(g), 6) if len(g) else None,
            "理论每笔往返手续费R": round(2 * 0.0005 / PCT[t], 6),
        })
    cdf = pd.DataFrame(cost)
    cdf.to_csv(OUT / "03_成本分解.csv", index=False, encoding="utf-8-sig")

    return {
        "一_各档指标": per_tier,
        "二_九组稳健汇总": med.to_dict("records"),
        "三_成本分解": cost,
        "四_集中度": conc,
        "七_尾部事件跨组复现": tail_recurrence(d),
        "五_缺失组": missing,
        "六_各组笔数合计": int(len(d)),
    }


if __name__ == "__main__":
    r = main()
    p = OUT / "03_指标重算.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print(f"已写出 {p} ({p.stat().st_size} 字节)")
    print(pd.DataFrame(r["一_各档指标"]).T.to_string())
    print("\n成本分解：")
    print(pd.DataFrame(r["三_成本分解"]).to_string(index=False))
    print("\n集中度：")
    print(json.dumps(r["四_集中度"], ensure_ascii=False, indent=1))

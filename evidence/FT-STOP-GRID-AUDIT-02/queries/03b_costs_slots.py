# -*- coding: utf-8 -*-
"""03b 账户、槽位与成本"只算一次"核对。

四项：
  1 盈亏闭合恒等式：profit_abs =（平仓价−开仓价）×数量 − 开仓费 − 平仓费 + 资金费
    —— 这一条同时证明"手续费只扣一次、资金费只计一次"（不闭合就说明漏算或重算）
  2 并发槽位：任一组同时持仓数 ≤ 3；同时占用的名义 ≤ 90% 权益、风险 ≤ 1.5% 权益
  3 单笔名义 ≤ 30% 权益；实际风险覆盖比（实际风险 / 0.5% 预算）分布
  4 手续费占名义的比例是否恰为 5bp×2
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
MAX_SLOTS = 3
MAX_TOTAL_RISK = 0.015
MAX_NOTIONAL = 0.90
MAX_ONE_NOTIONAL = 0.30


def main() -> None:
    df, missing = L.load_all()
    d = L.equity_curve(df)

    # ---------- 1 盈亏闭合 ----------
    lhs = pd.to_numeric(d["profit_abs"], errors="coerce")
    rhs = (pd.to_numeric(d["close_rate"], errors="coerce") -
           pd.to_numeric(d["open_rate"], errors="coerce")) * pd.to_numeric(d["amount"], errors="coerce") \
        - d["手续费USDT"] + d["资金费USDT"]
    err = (lhs - rhs).abs()
    closure = {
        "笔数": int(len(d)),
        "最大绝对误差": float(err.max()),
        "误差>1e-6 笔数": int((err > 1e-6).sum()),
        "误差>1e-3 笔数": int((err > 1e-3).sum()),
        "相对误差中位": float((err / lhs.abs().clip(lower=1e-9)).median()),
    }

    # ---------- 2 并发槽位 ----------
    slot_rows = []
    for name, g in d.groupby("策略", sort=False):
        ev = []
        for _, r in g.iterrows():
            ev.append((int(r["open_ms"]), 1, float(r["名义占权益"]), float(r["实际风险占权益"])))
            ev.append((int(r["close_ms"]), -1, -float(r["名义占权益"]), -float(r["实际风险占权益"])))
        # 同一时刻先处理平仓、再处理开仓（与引擎"先检查退出、再看入场"一致）
        ev.sort(key=lambda x: (x[0], x[1]))
        cur = 0
        mx = 0
        notional = 0.0
        risk = 0.0
        mx_notional = 0.0
        mx_risk = 0.0
        for _, dc, dn, dr in ev:
            cur += dc
            notional += dn
            risk += dr
            mx = max(mx, cur)
            mx_notional = max(mx_notional, notional)
            mx_risk = max(mx_risk, risk)
        slot_rows.append({
            "策略": name, "档位": L.tier_of(name), "参数组": L.suffix_of(name),
            "最大并发持仓": mx,
            "最大并发名义占权益pct": mx_notional * 100,
            "最大并发风险占权益pct": mx_risk * 100,
            "单笔最大名义占权益pct": float(g["名义占权益"].max() * 100),
            "风险覆盖比中位": float(g["风险覆盖比"].median()),
            "风险覆盖比最小": float(g["风险覆盖比"].min()),
            "风险覆盖比最大": float(g["风险覆盖比"].max()),
        })
    sl = pd.DataFrame(slot_rows)
    sl.to_csv(OUT / "03b_槽位与风险占用.csv", index=False, encoding="utf-8-sig")

    # ---------- 4 手续费率 ----------
    # fee_open / fee_close 是费率（不是金额）；手续费应恰为 数量×(开仓价+平仓价)×0.0005
    fo = pd.to_numeric(d["fee_open"], errors="coerce")
    fc = pd.to_numeric(d["fee_close"], errors="coerce")
    amt = pd.to_numeric(d["amount"], errors="coerce")
    op = pd.to_numeric(d["open_rate"], errors="coerce")
    cl = pd.to_numeric(d["close_rate"], errors="coerce")
    fee_expect = amt * (op + cl) * L.FEE
    fee_err = (d["手续费USDT"] - fee_expect).abs()
    fee_chk = {
        "fee_open 全为0.0005": bool((fo - L.FEE).abs().max() < 1e-15),
        "fee_close 全为0.0005": bool((fc - L.FEE).abs().max() < 1e-15),
        "手续费重算最大误差": float(fee_err.max()),
        "手续费重算误差>1e-6笔数": int((fee_err > 1e-6).sum()),
        "手续费占单边名义中位bp": float((d["手续费USDT"] / d["名义USDT"] * 1e4).median()),
        "说明": "占名义比例会随盈亏变动（分母只含开仓价），恒等式以数量×(开+平)×5bp 为准",
    }

    res = {
        "工单": "FT-STOP-GRID-AUDIT-02 / 03b 账户、槽位与成本",
        "组数": int(d["策略"].nunique()), "笔数": int(len(d)),
        "一_盈亏闭合恒等式": closure,
        "二_槽位与占用": {
            "最大并发持仓（全组最大）": int(sl["最大并发持仓"].max()) if len(sl) else None,
            "超过3槽的组数": int((sl["最大并发持仓"] > MAX_SLOTS).sum()) if len(sl) else None,
            "最大并发名义占权益pct（全组最大）": float(sl["最大并发名义占权益pct"].max()) if len(sl) else None,
            "超过90%的组数": int((sl["最大并发名义占权益pct"] > MAX_NOTIONAL * 100).sum()) if len(sl) else None,
            "最大并发风险占权益pct（全组最大）": float(sl["最大并发风险占权益pct"].max()) if len(sl) else None,
            "超过1.5%的组数": int((sl["最大并发风险占权益pct"] > MAX_TOTAL_RISK * 100).sum()) if len(sl) else None,
            "单笔最大名义占权益pct（全组最大）": float(sl["单笔最大名义占权益pct"].max()) if len(sl) else None,
            "单笔超30%的组数": int((sl["单笔最大名义占权益pct"] > MAX_ONE_NOTIONAL * 100).sum()) if len(sl) else None,
            "风险覆盖比中位（全组）": float(sl["风险覆盖比中位"].median()) if len(sl) else None,
            "风险覆盖比区间": [float(sl["风险覆盖比最小"].min()), float(sl["风险覆盖比最大"].max())]
            if len(sl) else None,
        },
        "三_手续费率": fee_chk,
        "四_说明": ("风险覆盖比 = 实际风险(名义×档位比例) / (开仓时权益×0.5%)。"
                "下单额用下单时价格、止损用实际成交价，两者不等时该比值不会恰好为 1。"),
    }
    with open(OUT / "03b_槽位与成本.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)

    print("组数", res["组数"], "笔数", res["笔数"])
    print("盈亏闭合 最大误差", closure["最大绝对误差"], " 超差笔数", closure["误差>1e-6 笔数"])
    print("槽位", res["二_槽位与占用"])
    print("手续费", fee_chk)


if __name__ == "__main__":
    main()

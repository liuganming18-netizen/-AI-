# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 02b_账户记录对账

三方核对：逐笔账（trades CSV）↔ 账户账（结果包里的 final_balance / wallet_stats）
          ↔ 期末持仓（left_open_trades / force_exit）
同时取出 freqtrade 自报的 rejected_signals（被挡掉的入场信号数），
这是「跳过信号」最直接的账户级证据。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from _lib import ALL, INIT_EQUITY, OUT, TIERS, UP, equity_curve, load_all


def zip_summary(name: str) -> dict | None:
    p = UP / f"out/backtest-result-{name}.zip"
    if not p.exists():
        return None
    with zipfile.ZipFile(p) as z:
        names = [x for x in z.namelist() if x.endswith(".json") and not x.endswith(".meta.json")]
        if not names:
            return None
        d = json.loads(z.read(names[0]))
    st = d.get("strategy", {}).get(name)
    if st is None:
        st = next(iter(d.get("strategy", {}).values()), None)
    return st


def main() -> dict:
    df, missing = load_all()
    d = equity_curve(df)

    rows = []
    for name in ALL:
        st = zip_summary(name)
        tr = d[d["策略"] == name]
        row = {"策略": name, "档位": name.split("_")[2], "参数组": "_".join(name.split("_")[3:])}
        if st is None:
            row["结果包"] = "缺失"
            rows.append(row)
            continue
        row["结果包"] = "有"
        row["自报成交数"] = st.get("total_trades")
        row["逐笔行数"] = int(len(tr))
        row["自报final_balance"] = round(float(st.get("final_balance") or 0), 6)
        row["逐笔重算final_balance"] = round(float(INIT_EQUITY + tr["profit_abs"].sum()), 6) if len(tr) else INIT_EQUITY
        row["对账误差"] = round(row["自报final_balance"] - row["逐笔重算final_balance"], 6)
        row["自报profit_total_abs"] = round(float(st.get("profit_total_abs") or 0), 6)
        row["逐笔profit_abs合计"] = round(float(tr["profit_abs"].sum()), 6) if len(tr) else 0.0
        lot = st.get("left_open_trades")
        row["自报left_open_trades"] = len(lot) if isinstance(lot, (list, tuple)) else lot
        row["逐笔force_exit数"] = int(tr["是否期末结算"].sum()) if len(tr) else 0
        row["自报rejected_signals"] = st.get("rejected_signals")
        row["自报winrate%"] = round(float(st.get("winrate") or 0) * 100, 4)
        real = tr[~tr["是否期末结算"]] if len(tr) else tr
        row["逐笔已实现胜率%"] = round(float((real["profit_abs"] > 0).mean() * 100), 4) if len(real) else None
        row["自报max_drawdown_account%"] = round(float(st.get("max_drawdown_account") or 0) * 100, 4)
        row["自报holding_avg_h"] = round(float(st.get("holding_avg_s") or 0) / 3600, 4)
        row["逐笔持仓均值h"] = round(float(tr["持仓小时"].mean()), 4) if len(tr) else None
        row["自报trailing_stop"] = st.get("trailing_stop")
        row["自报use_custom_stoploss"] = st.get("use_custom_stoploss")
        row["自报timerange"] = st.get("timerange")
        row["自报max_open_trades"] = st.get("max_open_trades")
        rows.append(row)

    t = pd.DataFrame(rows)
    t.to_csv(OUT / "02b_账户对账.csv", index=False, encoding="utf-8-sig")

    ok = t[t["结果包"] == "有"]
    res = {
        "一_对账": rows,
        "二_汇总": {
            "有结果包组数": int(len(ok)),
            "缺失组数": int((t["结果包"] == "缺失").sum()),
            "成交数与逐笔行数全等": bool((ok["自报成交数"] == ok["逐笔行数"]).all()) if len(ok) else None,
            "final_balance最大误差": float(ok["对账误差"].abs().max()) if len(ok) else None,
            "final_balance误差>0.01的组数": int((ok["对账误差"].abs() > 0.01).sum()) if len(ok) else None,
            "left_open_trades与force_exit一致的组数": int((pd.to_numeric(ok["自报left_open_trades"], errors="coerce") == ok["逐笔force_exit数"]).sum()) if len(ok) else None,
            "left_open_trades不一致的组数": int((pd.to_numeric(ok["自报left_open_trades"], errors="coerce") != ok["逐笔force_exit数"]).sum()) if len(ok) else None,
            "left_open_trades字段说明": "自报为列表但元素为空（pair 全 None），且长度与 force_exit 笔数不一致 ⇒ 该字段不能用来判断期末持仓",
            "自报winrate全为0": bool((ok["自报winrate%"] == 0).all()) if len(ok) else None,
            "rejected_signals合计": int(pd.to_numeric(ok["自报rejected_signals"], errors="coerce").fillna(0).sum()) if len(ok) else None,
            "trailing_stop自报值": sorted(set(ok["自报trailing_stop"].astype(str))) if len(ok) else None,
            "use_custom_stoploss自报值": sorted(set(ok["自报use_custom_stoploss"].astype(str))) if len(ok) else None,
            "timerange自报值": sorted(set(ok["自报timerange"].astype(str))) if len(ok) else None,
        },
    }
    # 分档汇总
    tier = []
    for tt in TIERS:
        g = ok[ok["档位"] == tt]
        if g.empty:
            continue
        tier.append({
            "档位": tt,
            "组数": int(len(g)),
            "成交数合计": int(g["逐笔行数"].sum()),
            "final_balance中位": round(float(g["自报final_balance"].median()), 4),
            "rejected_signals合计": int(pd.to_numeric(g["自报rejected_signals"], errors="coerce").fillna(0).sum()),
            "left_open_trades合计": int(pd.to_numeric(g["自报left_open_trades"], errors="coerce").fillna(0).sum()),
            "自报max_drawdown_account%中位": round(float(g["自报max_drawdown_account%"].median()), 4),
            "自报holding_avg_h中位": round(float(g["自报holding_avg_h"].median()), 2),
        })
    res["三_分档"] = tier
    return res


if __name__ == "__main__":
    r = main()
    p = OUT / "02b_账户对账.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print(f"已写出 {p} ({p.stat().st_size} 字节)")
    print(json.dumps(r["二_汇总"], ensure_ascii=False, indent=2))
    print(pd.DataFrame(r["三_分档"]).to_string(index=False))

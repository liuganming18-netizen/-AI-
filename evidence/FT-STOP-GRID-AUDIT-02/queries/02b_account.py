# -*- coding: utf-8 -*-
"""02b 账户对账：自报 final_balance / rejected_signals / 期末持仓 / 逐笔重算。

is_open 与 left_open_trades 在上一轮已被证明不可用，这里重新核一遍并给结论。
"""
from __future__ import annotations

import json
import zipfile

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT


def read_pkg(name: str) -> dict | None:
    p = L.UP / f"out/backtest-result-{name}.zip"
    if not p.exists():
        return None
    with zipfile.ZipFile(p) as zf:
        names = [n for n in zf.namelist() if n.endswith(".json") and not n.endswith(".meta.json")]
        if not names:
            return None
        d = json.loads(zf.read(names[0]))
    return d.get("strategy", {}).get(name)


def main() -> dict:
    df, missing = L.load_all()
    d = L.equity_curve(df) if not df.empty else pd.DataFrame()

    rows, per_group = [], []
    for name in L.ALL:
        st = read_pkg(name)
        g = d[d["策略"] == name] if not d.empty else pd.DataFrame()
        rec = {"策略": name, "档位": L.tier_of(name), "参数组": L.suffix_of(name)}
        if st is None:
            rec["状态"] = "无结果包"
            per_group.append(rec)
            continue
        n_rows = int(len(g))
        n_final = int(g["是否期末结算"].sum()) if n_rows else 0
        recomputed = float(g["profit_abs"].sum()) + L.INIT_EQUITY if n_rows else None
        fb = st.get("final_balance")
        lot = st.get("left_open_trades")
        lot_len = len(lot) if isinstance(lot, list) else None
        lot_trades = None
        if isinstance(lot, list) and lot:
            first = lot[0]
            if isinstance(first, dict):
                lot_trades = first.get("trades")
        rec.update({
            "状态": "有成交" if n_rows else "零成交",
            "逐笔行数": n_rows,
            "自报成交数": st.get("total_trades"),
            "期末结算笔数": n_final,
            "自报 final_balance": fb,
            "逐笔重算余额": recomputed,
            "误差": (recomputed - fb) if (fb is not None and recomputed is not None) else None,
            "rejected_signals": st.get("rejected_signals"),
            "left_open_trades_长度": lot_len,
            "left_open_trades_TOTAL_trades": lot_trades,
            "left_open_trades与force_exit一致": (lot_trades == n_final) if lot_trades is not None else None,
            "自报 trailing_stop": st.get("trailing_stop"),
            "自报 use_custom_stoploss": st.get("use_custom_stoploss"),
            "自报 timerange": st.get("timerange"),
            "自报 max_drawdown_account": st.get("max_drawdown_account"),
            "自报 winrate": st.get("winrate"),
            "自报 max_open_trades": st.get("max_open_trades"),
        })
        per_group.append(rec)

    pg = pd.DataFrame(per_group)
    ok = pg[pg["状态"] != "无结果包"]

    def agg(col):
        v = pd.to_numeric(ok[col], errors="coerce")
        return {"非空": int(v.notna().sum()), "最大绝对误差": float(v.abs().max()) if v.notna().any() else None}

    summary = {
        "有结果包组数": int(len(ok)),
        "缺失组数": int(len(pg) - len(ok)),
        "逐笔行数=自报成交数": int((pd.to_numeric(ok["逐笔行数"], errors="coerce") ==
                                pd.to_numeric(ok["自报成交数"], errors="coerce")).sum()),
        "final_balance 误差": agg("误差"),
        "final_balance 误差 > 0.01 的组数": int((pd.to_numeric(ok["误差"], errors="coerce").abs() > 0.01).sum()),
        "rejected_signals 合计": int(pd.to_numeric(ok["rejected_signals"], errors="coerce").fillna(0).sum()),
        "rejected_signals 按档": {t: int(pd.to_numeric(ok.loc[ok["档位"] == t, "rejected_signals"],
                                                      errors="coerce").fillna(0).sum()) for t in L.TIERS},
        "left_open_trades 与 force_exit 一致的组数":
            int(ok["left_open_trades与force_exit一致"].fillna(False).sum()),
        "left_open_trades 与 force_exit 不一致的组数":
            int((ok["left_open_trades与force_exit一致"] == False).sum()),  # noqa: E712
        "left_open_trades 形态": sorted({str(type(x).__name__) for x in ok["left_open_trades_长度"]}),
        "自报 trailing_stop 取值": sorted({str(x) for x in ok["自报 trailing_stop"]}),
        "自报 use_custom_stoploss 取值": sorted({str(x) for x in ok["自报 use_custom_stoploss"]}),
        "自报 timerange 取值": sorted({str(x) for x in ok["自报 timerange"]}),
        "自报 max_open_trades 取值": sorted({str(x) for x in ok["自报 max_open_trades"]}),
        "is_open 全表取值": sorted({str(x) for x in (d["is_open"].unique() if not d.empty else [])}),
    }

    # 逐档：期末持仓占用
    tier_final = []
    for t in L.TIERS:
        g = d[d["档位"] == t]
        if g.empty:
            continue
        tier_final.append({
            "档位": t, "期末结算笔数": int(g["是否期末结算"].sum()),
            "期末浮盈USDT": float(g.loc[g["是否期末结算"], "profit_abs"].sum()),
            "期末浮盈R": float(g.loc[g["是否期末结算"], "R"].sum()),
        })

    pg.to_csv(OUT / "02b_账户对账.csv", index=False, encoding="utf-8-sig")
    return {"一_汇总": summary, "二_分档期末": tier_final,
            "三_缺失组": missing, "四_零成交组": d.attrs.get("零成交组", []) if not d.empty else []}


if __name__ == "__main__":
    r = main()
    p = OUT / "02b_账户对账.json"
    p.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(r["一_汇总"], ensure_ascii=False, indent=1, default=str))
    print(f"\n写出 {p}")

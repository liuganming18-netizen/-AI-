# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 06_系统K线抽核

冻结计划第九节：每档随机 20 笔（共 80 笔），种子 20261022。
核对：入场K线存在、成交价落在[low,high]、止损价=成交价×(1-pct)、
      止损出场那根K线 low≤止损价且是首次触及、期末结算价=结算那根K线开盘价。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from _lib import OUT, TIERS, equity_curve, load_all, load_kline

SEED = 20261022
PER_TIER = 20


def main() -> dict:
    df, missing = load_all()
    d = equity_curve(df)

    rng = np.random.default_rng(SEED)
    picks = []
    for t in TIERS:
        g = d[d["档位"] == t]
        if g.empty:
            continue
        k = min(PER_TIER, len(g))
        idx = rng.choice(len(g), size=k, replace=False)
        picks.append(g.iloc[np.sort(idx)])
    sample = pd.concat(picks, ignore_index=True)

    checks = []
    cache: dict[str, pd.DataFrame] = {}
    for _, r in sample.iterrows():
        pair = r["pair"]
        if pair not in cache:
            k = load_kline(pair)
            cache[pair] = k if k is not None else pd.DataFrame()
        k = cache[pair]
        row = {"策略": r["策略"], "档位": r["档位"], "pair": pair, "事件ID": r["事件ID"],
               "exit_reason": r["exit_reason"], "open_ms": int(r["open_ms"]),
               "close_ms": int(r["close_ms"]), "open_rate": float(r["open_rate"]),
               "stop_loss_abs": float(r["stop_loss_abs"]) if pd.notna(r["stop_loss_abs"]) else None,
               "close_rate": float(r["close_rate"])}
        if k.empty:
            row["K线存在"] = False
            row["结论"] = "缺K线"
            checks.append(row)
            continue
        row["K线存在"] = True

        bar = k[k["ms"] == int(r["open_ms"])]
        if bar.empty:
            row["入场K线存在"] = False
        else:
            b = bar.iloc[0]
            row["入场K线存在"] = True
            row["入场价在高低之间"] = bool(b["low"] * (1 - 1e-9) <= r["open_rate"] <= b["high"] * (1 + 1e-9))

        pct = float(r["止损比例"])
        tk = 10.0 ** (-len(str(r["stop_loss_abs"]).split(".")[1])) if "." in str(r["stop_loss_abs"]) else 1.0
        row["止损价符合公式"] = bool(
            row["stop_loss_abs"] is not None and
            abs(row["stop_loss_abs"] - float(r["open_rate"]) * (1 - pct)) <= max(tk, abs(float(r["open_rate"])) * 1e-6) * 1.0000001)

        if str(r["exit_reason"]) in ("stop_loss", "trailing_stop_loss"):
            seg = k[(k["ms"] >= int(r["open_ms"])) & (k["ms"] <= int(r["close_ms"]))]
            breach = seg[seg["low"] <= row["stop_loss_abs"] + 1e-12] if row["stop_loss_abs"] else seg.iloc[0:0]
            row["首次触及K线ms"] = int(breach["ms"].iloc[0]) if len(breach) else None
            row["出场K线是首次触及"] = bool(len(breach) and int(breach["ms"].iloc[0]) == int(r["close_ms"]))
            row["出场价接近止损价"] = bool(abs(row["close_rate"] - row["stop_loss_abs"]) <= max(abs(row["stop_loss_abs"]) * 1e-6, 1e-12))
        elif str(r["exit_reason"]) == "force_exit":
            bar2 = k[k["ms"] == int(r["close_ms"])]
            if bar2.empty:
                row["结算K线存在"] = False
            else:
                row["结算K线存在"] = True
                row["结算价等于该K线开盘"] = bool(abs(float(bar2.iloc[0]["open"]) - row["close_rate"]) <= max(abs(row["close_rate"]) * 1e-9, 1e-12))
        checks.append(row)

    cdf = pd.DataFrame(checks)
    cdf.to_csv(OUT / "06_K线抽核明细.csv", index=False, encoding="utf-8-sig")

    def rate(col):
        if col not in cdf:
            return None
        v = cdf[col].dropna()
        return {"通过": int(v.sum()), "总数": int(len(v)), "通过率": round(float(v.mean()) * 100, 2) if len(v) else None}

    res = {
        "抽样种子": SEED,
        "每档抽样": PER_TIER,
        "实际抽样": int(len(cdf)),
        "各档抽样": {t: int((cdf["档位"] == t).sum()) for t in TIERS},
        "K线存在": rate("K线存在"),
        "入场K线存在": rate("入场K线存在"),
        "入场价在高低之间": rate("入场价在高低之间"),
        "止损价符合公式": rate("止损价符合公式"),
        "出场K线是首次触及": rate("出场K线是首次触及"),
        "出场价接近止损价": rate("出场价接近止损价"),
        "结算价等于该K线开盘": rate("结算价等于该K线开盘"),
        "异常明细": cdf[(cdf.get("入场价在高低之间") == False) |
                        (cdf.get("止损价符合公式") == False) |
                        (cdf.get("出场K线是首次触及") == False) |
                        (cdf.get("结算价等于该K线开盘") == False)][
            ["档位", "pair", "exit_reason", "open_rate", "stop_loss_abs", "close_rate"]].head(20).to_dict("records"),
    }
    return res


if __name__ == "__main__":
    r = main()
    p = OUT / "06_K线一致性.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print(f"已写出 {p} ({p.stat().st_size} 字节)")
    print(json.dumps({k: v for k, v in r.items() if k != "异常明细"}, ensure_ascii=False, indent=2))

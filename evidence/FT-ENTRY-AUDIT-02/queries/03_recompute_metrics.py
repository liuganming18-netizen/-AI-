# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-02 · 03 从逐笔独立重算关键指标（含回撤三口径）

只读 FT-ENTRY-01 的 trades_*.csv 与归档包内 wallet.feather。
产出：
  out/11_逐笔指标独立重算_v1.csv
  out/12_回撤口径三分解_v1.csv
  out/13_费用与风险预算核对_v1.csv
  out/14_日度权益序列_v1.csv
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import zipfile

import numpy as np
import pandas as pd

csv.field_size_limit(10 ** 9)

BASE = "/Users/boos/Desktop/威科夫因子共振趋势"
ENTRY = os.path.join(BASE, "evidence/FT-ENTRY-01")
OUT = os.path.join(BASE, "evidence/FT-ENTRY-AUDIT-02/out")
INIT = 2000.0

ARMS = ["WyckoffBaseline", "WyckoffEntryA1_NearTop", "WyckoffEntryA2_AtBoxHigh",
        "WyckoffEntryC1_StopSweep", "WyckoffEntryC2_StopBar"]


def wilson(k, n, z=1.95996):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n)))
    return max(0.0, (c - z * r) / den), min(1.0, (c + z * r) / den)


def dd_from_series(vals, init):
    eq = init
    peak = init
    mdd = 0.0
    mdd_pct = 0.0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        d = peak - eq
        if d > mdd:
            mdd = d
            mdd_pct = d / peak * 100 if peak > 0 else 0.0
    return mdd, mdd_pct


def load(arm):
    df = pd.read_csv(os.path.join(ENTRY, "out", f"trades_{arm}.csv"), encoding="utf-8-sig")
    if "pair" not in df.columns:
        df = df.rename(columns={df.columns[0]: "pair"})
    df["profit_abs"] = df["profit_abs"].astype(float)
    return df.sort_values("close_timestamp").reset_index(drop=True)


def wallet_curve(arm):
    z = zipfile.ZipFile(os.path.join(ENTRY, "out", f"backtest-result-{arm}.zip"))
    n = [x for x in z.namelist() if x.endswith("_wallet.feather")][0]
    w = pd.read_feather(io.BytesIO(z.read(n)))
    w["date"] = pd.to_datetime(w["date"], utc=True)
    return w[["date", "balance"]].sort_values("date").reset_index(drop=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    rows, dd_rows, fee_rows = [], [], []
    daily = {}

    for a in ARMS:
        df = load(a)
        p = df["profit_abs"]
        n = len(df)
        w = int((p > 0).sum())
        l = int((p < 0).sum())
        z0 = int((p == 0).sum())
        gp = float(p[p > 0].sum())
        gl = float(abs(p[p < 0].sum()))
        lo, hi = wilson(w, n)

        # 回撤三口径
        dd_closed, dd_closed_pct = dd_from_series(p.tolist(), INIT)
        dd_excl, dd_excl_pct = dd_from_series(p.tolist()[1:], INIT) if n > 1 else (0.0, 0.0)
        wc = wallet_curve(a)
        bal = wc["balance"].to_numpy(dtype=float)
        peak = np.maximum.accumulate(bal)
        dd_series = peak - bal
        i = int(np.argmax(dd_series))
        mtm_abs = float(dd_series[i])
        peak_at = float(peak[i])
        mtm_pct = mtm_abs / peak_at * 100 if peak_at > 0 else 0.0
        # 权益峰值（用于确认 2000 起点之后是否曾盈利）
        max_eq = float(bal.max())
        min_eq = float(bal.min())
        # wallet 曲线是否含浮动盈亏：跳变点是否全部落在平仓时刻
        chg = np.where(np.abs(np.diff(bal)) > 1e-9)[0]
        chg_ts = set(wc["date"].astype("int64").to_numpy()[chg])
        close_ts = set(pd.to_datetime(df["close_date"], utc=True).astype("int64"))
        has_floating = len(chg_ts - close_ts) > 0

        rows.append({
            "arm": a, "trades": n, "wins": w, "losses": l, "zero_pnl": z0,
            "win_rate_pct": round(w / n * 100, 4) if n else 0.0,
            "wilson_lo_pct": round(lo * 100, 2), "wilson_hi_pct": round(hi * 100, 2),
            "net_pnl": round(float(p.sum()), 4),
            "net_return_pct_of_2000": round(float(p.sum()) / INIT * 100, 4),
            "gross_profit": round(gp, 4), "gross_loss": round(gl, 4),
            "profit_factor": round(gp / gl, 4) if gl > 0 else float("inf"),
            "expectancy_u": round(float(p.sum()) / n, 4) if n else 0.0,
            "avg_win_u": round(gp / w, 4) if w else 0.0,
            "avg_loss_u": round(-gl / l, 4) if l else 0.0,
            "payoff_ratio": round((gp / w) / (gl / l), 4) if w and l else 0.0,
            "median_pnl": round(float(p.median()), 4),
            "max_win": round(float(p.max()), 4), "max_loss": round(float(p.min()), 4),
            "final_balance": round(INIT + float(p.sum()), 4),
            "wallet_final": round(float(bal[-1]), 4),
        })

        dd_rows.append({
            "arm": a,
            "dd_closed_equity_u": round(dd_closed, 4),
            "dd_closed_equity_pct": round(dd_closed_pct, 4),
            "dd_closed_excl_first_u": round(dd_excl, 4),
            "dd_closed_excl_first_pct": round(dd_excl_pct, 4),
            "dd_mtm_wallet_u": round(mtm_abs, 4),
            "dd_mtm_wallet_pct": round(mtm_pct, 4),
            "mtm_dd_end_utc": str(wc["date"].iloc[i]),
            "wallet_max": round(max_eq, 4), "wallet_min": round(min_eq, 4),
            "mtm_minus_closed_u": round(mtm_abs - dd_closed, 4),
            "wallet_change_points": len(chg),
            "wallet_change_points_at_trade_close": len(chg_ts & close_ts),
            "wallet_has_floating_pnl": has_floating,
            "uses_excl_first": "否" if abs(dd_closed - dd_excl) > 1e-6 else "无法区分（两者相等）",
        })

        # 费用与风险预算
        fee_open = float(df["fee_open"].sum()) if "fee_open" in df else float("nan")
        fee_close = float(df["fee_close"].sum()) if "fee_close" in df else float("nan")
        # 实际手续费 = stake*fee_open + amount*close_rate*fee_close
        fee_row = df["stake_amount"] * df["fee_open"] + df["amount"] * df["close_rate"] * df["fee_close"]
        fee_abs = float(fee_row.sum())
        funding_row = df["funding_fees"].astype(float) if "funding_fees" in df else pd.Series(0.0, index=df.index)
        funding = float(funding_row.sum())

        # 风险预算核对：equity_at_open 由 wallet 曲线按开仓时刻取，risk=equity*0.005
        # 由 stake=qty*rate 与 qty=risk/r_dist 可得 r_dist/rate = risk/stake
        wc_idx = wc.set_index("date")["balance"]
        eq_at_open = pd.Series(
            [float(wc_idx.asof(pd.Timestamp(t, unit="ms", tz="UTC"))) for t in df["open_timestamp"]],
            index=df.index)
        eq_at_open = eq_at_open.fillna(INIT)
        risk_budget = eq_at_open * 0.005
        implied_stop_pct = (risk_budget / df["stake_amount"] * 100).clip(upper=200)
        pre_cost = p - funding_row + fee_row  # profit_abs = gross + funding - fees ⇒ gross = p - funding + fees

        fee_rows.append({
            "arm": a,
            "trades": n,
            "fee_open_rate": round(float(df["fee_open"].iloc[0]), 6),
            "fee_close_rate": round(float(df["fee_close"].iloc[0]), 6),
            "total_fee_u": round(float(fee_abs), 4),
            "total_funding_u": round(funding, 4),
            "net_pnl_u": round(float(p.sum()), 4),
            "pre_cost_pnl_u": round(float(pre_cost.sum()), 4),
            "fee_share_of_gross_loss_pct": round(abs(float(fee_abs)) / abs(float(p.sum())) * 100, 2) if p.sum() != 0 else float("nan"),
            "cost_total_u": round(float(fee_abs - funding), 4),
            "avg_stake_u": round(float(df["stake_amount"].mean()), 4),
            "avg_stake_pct_of_2000": round(float(df["stake_amount"].mean()) / INIT * 100, 2),
            "max_stake_pct_of_2000": round(float(df["stake_amount"].max()) / INIT * 100, 2),
            "avg_equity_at_open": round(float(eq_at_open.mean()), 2),
            "avg_risk_budget_u": round(float(risk_budget.mean()), 4),
            "avg_implied_stop_distance_pct": round(float(implied_stop_pct.mean()), 3),
            "median_implied_stop_distance_pct": round(float(implied_stop_pct.median()), 3),
            "stake_at_30pct_cap_count": int((df["stake_amount"] >= eq_at_open * 0.30 * 0.995).sum()),
            "slippage_3bps_would_cost_u": round(float(((df["stake_amount"] + df["amount"] * df["close_rate"]) * 0.0003).sum()), 4),
        })

        # 日度权益（按日取最后一个 balance）
        d = wc.set_index("date")["balance"].resample("1D").last().ffill()
        daily[a] = d

    pd.DataFrame(rows).to_csv(os.path.join(OUT, "11_逐笔指标独立重算_v1.csv"),
                              index=False, encoding="utf-8-sig")
    pd.DataFrame(dd_rows).to_csv(os.path.join(OUT, "12_回撤口径三分解_v1.csv"),
                                 index=False, encoding="utf-8-sig")
    pd.DataFrame(fee_rows).to_csv(os.path.join(OUT, "13_费用与风险预算核对_v1.csv"),
                                  index=False, encoding="utf-8-sig")

    ddf = pd.DataFrame(daily).sort_index()
    ddf.index.name = "date"
    ddf.reset_index().to_csv(os.path.join(OUT, "14_日度权益序列_v1.csv"),
                             index=False, encoding="utf-8-sig")

    print("=== 独立重算（与自报对照）===")
    for r in rows:
        print(f"  {r['arm']:32s} n={r['trades']:4d} W/L={r['wins']:3d}/{r['losses']:3d} "
              f"wr={r['win_rate_pct']:6.2f}% [{r['wilson_lo_pct']:.2f},{r['wilson_hi_pct']:.2f}] "
              f"net={r['net_pnl']:+10.3f} PF={r['profit_factor']:6.4f} exp={r['expectancy_u']:+8.3f}")
    print("\n=== 回撤三口径 ===")
    for r in dd_rows:
        print(f"  {r['arm']:32s} 平仓权益={r['dd_closed_equity_u']:8.3f}({r['dd_closed_equity_pct']:5.2f}%) "
              f"剔除首笔={r['dd_closed_excl_first_u']:8.3f} MTM={r['dd_mtm_wallet_u']:8.3f}({r['dd_mtm_wallet_pct']:5.2f}%) "
              f"差={r['mtm_minus_closed_u']:+8.3f} 口径={r['uses_excl_first']}")
    print("\n=== 费用与风险预算 ===")
    for r in fee_rows:
        print(f"  {r['arm']:32s} 费={r['total_fee_u']:9.2f} 资金费={r['total_funding_u']:+8.2f} "
              f"净={r['net_pnl_u']:+9.2f} 扣费前={r['pre_cost_pnl_u']:+9.2f} "
              f"费占亏损={r['fee_share_of_gross_loss_pct']:6.2f}% "
              f"均仓={r['avg_stake_u']:7.1f}({r['avg_stake_pct_of_2000']:5.2f}%) "
              f"均止损距={r['avg_implied_stop_distance_pct']:6.3f}% "
              f"3bps滑点将再扣={r['slippage_3bps_would_cost_u']:7.2f}U")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FLOW-DATA-01 第一步：盘点既有 Parquet 的字段覆盖（只读，不写生产库）。

产出（全部写入 evidence/FLOW-DATA-01/runs/）：
  inv_candles.csv   逐 (标的,周期) 的行数/起止/非空率/断档
  inv_oi.csv        逐标的 OI 数量与名义金额覆盖
  inv_metrics.csv   逐标的 6 个官方 metrics 字段覆盖
  inv_funding.csv   逐标的资金费覆盖
  inv_symbols.csv   标的全集与在各家族的在场情况

口径约定（写死，报告里照抄）：
  · 时间一律 UTC，毫秒时间戳换算；candles 用 open_time，metrics/oi 用 ts，funding 用 funding_time。
  · 「断档」= 相邻两条观测的间隔 > 1.5 × 名义周期；同时记录最大间隔。
  · 「非空率」= 非 NaN 行数 / 总行数；不用 0 填充。
"""
import json
import os
import sys
import glob
import math
from datetime import datetime, timezone

import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
PQ = os.path.join(ROOT, "数据", "市场数据", "parquet")
OUT = os.path.join(ROOT, "evidence", "FLOW-DATA-01", "runs")
os.makedirs(OUT, exist_ok=True)

TF_MS = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
         "4h": 14_400_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000}


def iso(ms):
    if ms is None or (isinstance(ms, float) and math.isnan(ms)):
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gap_stats(ts_sorted, period_ms):
    """返回 (最大间隔分钟, 断档次数)。断档阈值 1.5×名义周期。"""
    if len(ts_sorted) < 2:
        return None, 0
    d = pd.Series(ts_sorted).diff().dropna()
    thr = 1.5 * period_ms
    return round(float(d.max()) / 60000.0, 3), int((d > thr).sum())


def nz(df, c):
    if c not in df.columns:
        return None
    return round(float(df[c].notna().mean()), 6)


# ── 1. candles ────────────────────────────────────────────────────────────────
def do_candles():
    rows = []
    base = os.path.join(PQ, "candles", "binance", "um")
    syms = sorted(os.listdir(base))
    for i, sym in enumerate(syms):
        for tf in sorted(os.listdir(os.path.join(base, sym))):
            files = sorted(glob.glob(os.path.join(base, sym, tf, "*.parquet")))
            if not files:
                continue
            df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
            if df.empty:
                continue
            ts = df["open_time"].astype("int64")
            order = ts.argsort()
            ts_s = ts.iloc[order].reset_index(drop=True)
            maxgap, ngaps = gap_stats(ts_s, TF_MS.get(tf, 0) or 1)
            rows.append({
                "symbol": sym, "timeframe": tf,
                "rows": int(len(df)),
                "first_utc": iso(ts_s.iloc[0]), "last_utc": iso(ts_s.iloc[-1]),
                "dup_ts": int(len(ts) - ts.nunique()),
                "nz_volume": nz(df, "volume"),
                "nz_quote_volume": nz(df, "quote_volume"),
                "nz_taker_buy_volume": nz(df, "taker_buy_volume"),
                "nz_taker_buy_quote_volume": nz(df, "taker_buy_quote_volume"),
                "nz_trade_count": nz(df, "trade_count"),
                "nz_available_at": nz(df, "available_at"),
                "max_gap_min": maxgap, "n_gaps": ngaps,
                "zero_volume_rows": int((df["volume"] == 0).sum()) if "volume" in df else None,
            })
        if i % 50 == 0:
            print(f"candles {i}/{len(syms)} {sym}", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "inv_candles.csv"), index=False)
    print("candles done", len(rows), flush=True)


# ── 2. oi ─────────────────────────────────────────────────────────────────────
def do_oi():
    rows = []
    base = os.path.join(PQ, "oi", "binance", "um")
    for sym in sorted(os.listdir(base)):
        files = sorted(glob.glob(os.path.join(base, sym, "*.parquet")))
        if not files:
            continue
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        if df.empty:
            continue
        ts = df["ts"].astype("int64")
        ts_s = ts.sort_values().reset_index(drop=True)
        periods = sorted(df["period"].dropna().unique().tolist()) if "period" in df else []
        maxgap, ngaps = gap_stats(ts_s, 300_000)
        rows.append({
            "symbol": sym, "rows": int(len(df)),
            "first_utc": iso(ts_s.iloc[0]), "last_utc": iso(ts_s.iloc[-1]),
            "periods": "|".join(str(p) for p in periods),
            "nz_open_interest": nz(df, "open_interest"),
            "nz_open_interest_value": nz(df, "open_interest_value"),
            "nz_oi_ccy": nz(df, "oi_ccy"), "nz_oi_usd": nz(df, "oi_usd"),
            "sources": "|".join(sorted(df["source"].dropna().astype(str).unique().tolist())[:6]) if "source" in df else "",
            "max_gap_min": maxgap, "n_gaps": ngaps,
        })
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "inv_oi.csv"), index=False)
    print("oi done", len(rows), flush=True)


# ── 3. metrics ───────────────────────────────────────────────────────────────
MET_COLS = ["sum_open_interest", "sum_open_interest_value",
            "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
            "count_long_short_ratio", "sum_taker_long_short_vol_ratio",
            "retail_account_ls_ratio", "top_position_ls_ratio"]


def do_metrics():
    rows = []
    base = os.path.join(PQ, "metrics", "binance", "um")
    syms = sorted(os.listdir(base))
    for i, sym in enumerate(syms):
        files = sorted(glob.glob(os.path.join(base, sym, "*.parquet")))
        if not files:
            continue
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        if df.empty:
            continue
        ts = df["ts"].astype("int64")
        ts_s = ts.sort_values().reset_index(drop=True)
        maxgap, ngaps = gap_stats(ts_s, 300_000)
        rec = {"symbol": sym, "rows": int(len(df)),
               "first_utc": iso(ts_s.iloc[0]), "last_utc": iso(ts_s.iloc[-1]),
               "dup_ts": int(len(ts) - ts.nunique()),
               "max_gap_min": maxgap, "n_gaps": ngaps}
        for c in MET_COLS:
            rec["nz_" + c] = nz(df, c)
        # 别名是否逐值相同（retail==count_long_short, top_position==sum_toptrader）
        if "retail_account_ls_ratio" in df and "count_long_short_ratio" in df:
            a = df[["retail_account_ls_ratio", "count_long_short_ratio"]].dropna()
            rec["alias_retail_eq_count_lsr"] = round(float((a.iloc[:, 0] == a.iloc[:, 1]).mean()), 6) if len(a) else None
        if "top_position_ls_ratio" in df and "sum_toptrader_long_short_ratio" in df:
            b = df[["top_position_ls_ratio", "sum_toptrader_long_short_ratio"]].dropna()
            rec["alias_toppos_eq_sum_top"] = round(float((b.iloc[:, 0] == b.iloc[:, 1]).mean()), 6) if len(b) else None
        rows.append(rec)
        if i % 50 == 0:
            print(f"metrics {i}/{len(syms)} {sym}", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "inv_metrics.csv"), index=False)
    print("metrics done", len(rows), flush=True)


# ── 4. funding ───────────────────────────────────────────────────────────────
def do_funding():
    rows = []
    base = os.path.join(PQ, "funding", "binance", "um")
    for f in sorted(glob.glob(os.path.join(base, "*.parquet"))):
        sym = os.path.basename(f)[:-len(".parquet")]
        df = pd.read_parquet(f)
        if df.empty:
            rows.append({"symbol": sym, "rows": 0})
            continue
        ts = df["funding_time"].astype("int64")
        ts_s = ts.sort_values().reset_index(drop=True)
        d = ts_s.diff().dropna() / 3_600_000.0
        rows.append({
            "symbol": sym, "rows": int(len(df)),
            "first_utc": iso(ts_s.iloc[0]), "last_utc": iso(ts_s.iloc[-1]),
            "nz_funding_rate": nz(df, "funding_rate"), "nz_mark_price": nz(df, "mark_price"),
            "median_interval_h": round(float(d.median()), 3) if len(d) else None,
            "interval_modes": "|".join(f"{k:g}h×{v}" for k, v in d.round(3).value_counts().head(4).items()) if len(d) else "",
            "max_gap_h": round(float(d.max()), 3) if len(d) else None,
        })
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "inv_funding.csv"), index=False)
    print("funding done", len(rows), flush=True)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "candles"):
        do_candles()
    if which in ("all", "oi"):
        do_oi()
    if which in ("all", "metrics"):
        do_metrics()
    if which in ("all", "funding"):
        do_funding()

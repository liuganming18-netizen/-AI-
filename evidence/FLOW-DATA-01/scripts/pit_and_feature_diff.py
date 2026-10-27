#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FLOW-DATA-01 第四步（续）：PIT 时间审计 + 与反重力新增特征逐值比对。

两件事严格分开：
  A) 算术正确性：用**和反重力完全相同的取数规则**（同一条 K 线、同一个 metrics 行）
     独立重算，逐字段求差 ⇒ 只暴露算错，不掺时间口径。
  B) 时间正确性（PIT）：检查该 K 线 / 该 metrics 桶在信号时刻**是否已经收盘可用**。

输出 runs/pit_audit.csv（227 行）、runs/feature_diff.csv、runs/cross_source_taker.csv
"""
import json
import os

import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
PQ = os.path.join(ROOT, "数据", "市场数据", "parquet")
FEAT = os.path.join(ROOT, "evidence", "FLOW-OI-01", "candidate_flow_oi_features.csv")
OUT = os.path.join(ROOT, "evidence", "FLOW-DATA-01", "runs")
os.makedirs(OUT, exist_ok=True)

_C = {}
_M = {}


def load(sym, kind, tf="1h"):
    cache = _C if kind == "candles" else _M
    if sym in cache:
        return cache[sym]
    if kind == "candles":
        d = os.path.join(PQ, "candles", "binance", "um", sym, tf)
        cols = ["open_time", "close_time", "open", "high", "low", "close",
                "volume", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]
    elif kind == "metrics":
        d = os.path.join(PQ, "metrics", "binance", "um", sym)
        cols = ["ts", "sum_open_interest", "sum_open_interest_value",
                "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
                "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
    else:
        d = os.path.join(PQ, "oi", "binance", "um", sym)
        cols = ["ts", "open_interest", "open_interest_value"]
    if not os.path.isdir(d):
        cache[sym] = pd.DataFrame()
        return cache[sym]
    fs = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
    df = pd.concat([pd.read_parquet(os.path.join(d, f), columns=cols) for f in fs],
                   ignore_index=True)
    tcol = "open_time" if "open_time" in df.columns else "ts"
    df = df.sort_values(tcol).drop_duplicates(tcol).reset_index(drop=True)
    # ⚠ 落盘事实：candles 的 OHLCV 在 Parquet 里是**字符串**类型，
    #   直接 sum() 会做字符串拼接（本脚本首跑即踩到）。这里显式转数值。
    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_volume", "taker_buy_quote_volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    cache[sym] = df
    return df


def drop_cache(sym):
    _C.pop(sym, None)
    _M.pop(sym, None)


# ── A. 逐值比对 ──────────────────────────────────────────────────────────────
def recompute_row(row):
    """用反重力同款取数规则重算，返回 dict。"""
    sym, ts = row["symbol"], int(row["snapshot_time_ms"])
    out = {}
    c = load(sym, "candles")
    if c.empty:
        return out
    cp = c[c["open_time"] <= ts]
    if cp.empty:
        return out
    bar = cp.iloc[-1]
    g = lambda k: float(bar[k])
    vol, qvol = g("volume"), g("quote_volume")
    tbq, tbv = g("taker_buy_quote_volume"), g("taker_buy_volume")
    out["bar_open"] = g("open")
    out["bar_high"] = g("high")
    out["bar_low"] = g("low")
    out["bar_close"] = g("close")
    out["bar_volume"] = vol
    out["bar_quote_volume"] = qvol
    out["bar_taker_buy_quote"] = tbq
    out["bar_taker_sell_quote"] = qvol - tbq
    out["bar_taker_buy_net_quote"] = 2.0 * tbq - qvol
    out["bar_taker_buy_ratio"] = (tbq / qvol) if qvol > 0 else None
    rng = g("high") - g("low")
    out["bar_upper_shadow_ratio"] = ((g("high") - max(g("open"), g("close"))) / (rng + 1e-8)) if rng > 0 else 0.0
    out["bar_body_ratio"] = (abs(g("close") - g("open")) / (rng + 1e-8)) if rng > 0 else 0.0
    out["bar_return"] = (g("close") - g("open")) / (g("open") + 1e-8)

    w = c[(c["open_time"] > ts - 86_400_000) & (c["open_time"] <= ts)]
    if len(w):
        tq = float(w["quote_volume"].sum())
        ttq = float(w["taker_buy_quote_volume"].sum())
        tv = float(w["volume"].sum())
        ttv = float(w["taker_buy_volume"].sum())
        mq = float(w["quote_volume"].mean())
        out["bar_volume_ratio_24h"] = (qvol / (mq + 1e-8)) if mq > 0 else 1.0
        out["quote_cvd_24h"] = 2.0 * ttq - tq
        out["base_cvd_24h"] = 2.0 * ttv - tv
        out["norm_cvd_24h"] = (out["quote_cvd_24h"] / (tq + 1e-8)) if tq > 0 else 0.0
        out["taker_buy_ratio_24h"] = (ttq / (tq + 1e-8)) if tq > 0 else None
    else:
        out["bar_volume_ratio_24h"] = 1.0
        out["quote_cvd_24h"] = 0.0
        out["base_cvd_24h"] = 0.0
        out["norm_cvd_24h"] = 0.0
        out["taker_buy_ratio_24h"] = None

    m = load(sym, "metrics")
    if not m.empty:
        mp = m[m["ts"] <= ts]
        if not mp.empty and (ts - int(mp["ts"].iloc[-1])) <= 7_200_000:
            cur = mp.iloc[-1]
            out["top_position_ls_ratio"] = cur["sum_toptrader_long_short_ratio"]
            out["top_account_ls_ratio"] = cur["count_toptrader_long_short_ratio"]
            out["global_account_ls_ratio"] = cur["count_long_short_ratio"]
            out["taker_ls_vol_ratio"] = cur["sum_taker_long_short_vol_ratio"]
            out["oi_base"] = cur["sum_open_interest"]
            out["oi_quote"] = cur["sum_open_interest_value"]
            prev = m[m["ts"] <= ts - 86_400_000]
            if not prev.empty:
                p = prev.iloc[-1]
                pb, pq = float(p["sum_open_interest"]), float(p["sum_open_interest_value"])
                out["delta_oi_base_24h"] = (out["oi_base"] - pb) / (pb + 1e-8) if pb > 0 else None
                out["delta_oi_quote_24h"] = (out["oi_quote"] - pq) / (pq + 1e-8) if pq > 0 else None
                out["delta_top_pos_ratio_24h"] = (out["top_position_ls_ratio"] - float(p["sum_toptrader_long_short_ratio"])) if out["top_position_ls_ratio"] else None
    return out


CMP_FIELDS = ["bar_open", "bar_high", "bar_low", "bar_close", "bar_volume", "bar_quote_volume",
              "bar_taker_buy_quote", "bar_taker_sell_quote", "bar_taker_buy_net_quote",
              "bar_taker_buy_ratio", "bar_upper_shadow_ratio", "bar_body_ratio", "bar_return",
              "bar_volume_ratio_24h", "quote_cvd_24h", "base_cvd_24h", "norm_cvd_24h",
              "taker_buy_ratio_24h", "top_position_ls_ratio", "top_account_ls_ratio",
              "global_account_ls_ratio", "taker_ls_vol_ratio", "oi_base", "oi_quote",
              "delta_oi_base_24h", "delta_oi_quote_24h", "delta_top_pos_ratio_24h"]


def main():
    f = pd.read_csv(FEAT)
    rows, pit = [], []
    for sym, grp in f.groupby("symbol", sort=True):
        for _, r in grp.iterrows():
            ts = int(r["snapshot_time_ms"])
            rec = recompute_row(r)
            if not rec:
                continue
            line = {"candidate_id": r["candidate_id"], "symbol": sym, "snapshot_time_ms": ts}
            for c in CMP_FIELDS:
                theirs = r.get(c)
                mine = rec.get(c)
                line[c + "_theirs"] = theirs
                line[c + "_mine"] = mine
                try:
                    a, b = float(theirs), float(mine)
                    d = abs(a - b)
                    line[c + "_absdiff"] = d
                    line[c + "_flag"] = ("OK" if d <= max(1e-9, 1e-9 * abs(a))
                                         else ("NEAR" if d <= 1e-6 * max(1.0, abs(a)) else "DIFF"))
                except (TypeError, ValueError):
                    line[c + "_absdiff"] = None
                    line[c + "_flag"] = ("NULL_SAME"
                                         if (pd.isna(theirs) and (mine is None or pd.isna(mine)))
                                         else "NULL_DIFF")
            rows.append(line)

            # ── PIT ──
            c = load(sym, "candles")
            cp = c[c["open_time"] <= ts]
            if cp.empty:
                continue
            bar = cp.iloc[-1]
            b_open, b_close = int(bar["open_time"]), int(bar["close_time"])
            m = load(sym, "metrics")
            mp = m[m["ts"] <= ts] if not m.empty else pd.DataFrame()
            m_ts = int(mp["ts"].iloc[-1]) if len(mp) else None
            pit.append({
                "candidate_id": r["candidate_id"], "symbol": sym, "snapshot_time_ms": ts,
                "snapshot_utc": pd.to_datetime(ts, unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "bar_open_time": b_open, "bar_close_time": b_close,
                "bar_open_utc": pd.to_datetime(b_open, unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "min_into_bar": round((ts - b_open) / 60000.0, 3),
                "bar_closed_at_signal": bool(b_close < ts),
                "future_minutes_in_bar": round(max(0.0, (b_close - ts) / 60000.0), 3),
                "metrics_ts": m_ts,
                "metrics_bucket_end": (m_ts + 300000) if m_ts else None,
                "metrics_closed_at_signal": bool(m_ts is not None and (m_ts + 300000) <= ts),
                "metrics_future_minutes": round(max(0.0, ((m_ts + 300000) - ts) / 60000.0), 3) if m_ts else None,
            })
        drop_cache(sym)
        print(f"[pit] {sym} done", flush=True)

    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(OUT, "feature_diff.csv"), index=False)
    p = pd.DataFrame(pit)
    p.to_csv(os.path.join(OUT, "pit_audit.csv"), index=False)

    # 汇总：逐字段 flag 计数
    summ = []
    for c in CMP_FIELDS:
        col = c + "_flag"
        if col not in d:
            continue
        vc = d[col].value_counts().to_dict()
        summ.append({"field": c, **{k: int(v) for k, v in vc.items()}})
    pd.DataFrame(summ).fillna(0).to_csv(os.path.join(OUT, "feature_diff_summary.csv"), index=False)

    print("feature rows", len(d), "pit rows", len(p))
    print("bar_closed_at_signal:", p.bar_closed_at_signal.value_counts().to_dict())
    print("metrics_closed_at_signal:", p.metrics_closed_at_signal.value_counts().to_dict())
    print(pd.DataFrame(summ).fillna(0).to_string())


if __name__ == "__main__":
    main()

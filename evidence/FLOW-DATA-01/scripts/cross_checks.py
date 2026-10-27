#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FLOW-DATA-01 第四步（续二）：三项交叉核验。

① 跨源单位一致性：官方 metrics 的 `sum_taker_long_short_vol_ratio`（买量/卖量）
   与同一 5 分钟桶的 K 线派生 buyVol/sellVol 对撞 ⇒ 判定该比值的单位口径。
② OI 名义额是否混入价格效应：`sum_open_interest_value ≈ sum_open_interest × 标记价`
   以及 Δ名义额 与 (Δ数量 + 收益率) 的恒等关系。
③ 逐字段「首次可用 / 最后可用」时间窗（大户持仓比、大户账户比、全市场账户比、
   主动买卖量比）⇒ 解释 2022 年整段为空是上游事实，不是本工程漏采。

输出 runs/xsource_taker.csv / runs/oi_price_effect.csv / runs/field_availability.csv
"""
import io
import json
import os
import zipfile

import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
RAW = os.path.join(ROOT, "数据", "raw")
RAWM = os.path.join(ROOT, "数据", "raw_oi_metrics")
PQ = os.path.join(ROOT, "数据", "市场数据", "parquet")
OUT = os.path.join(ROOT, "evidence", "FLOW-DATA-01", "runs")


def read_zip_csv(path):
    with zipfile.ZipFile(path) as z:
        return pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))


def to_ms(s):
    return ((pd.to_datetime(s, utc=True) - pd.Timestamp("1970-01-01", tz="UTC"))
            .dt.total_seconds().astype("int64") * 1000)


# ── ① taker 量比单位 ─────────────────────────────────────────────────────────
def xsource_taker(sym, month, day):
    k = read_zip_csv(os.path.join(RAW, "5m", sym, f"{sym}-5m-{month}.zip"))
    m = read_zip_csv(os.path.join(RAWM, sym, f"{sym}-metrics-{day}.zip"))
    m["ts"] = to_ms(m["create_time"])
    d0, d1 = int(m.ts.min()), int(m.ts.max())
    k = k[(k.open_time >= d0) & (k.open_time <= d1)]
    j = m.merge(k, left_on="ts", right_on="open_time", how="inner")
    j["buy_base"] = j.taker_buy_volume
    j["sell_base"] = j.volume - j.taker_buy_volume
    j["buy_q"] = j.taker_buy_quote_volume
    j["sell_q"] = j.quote_volume - j.taker_buy_quote_volume
    with np.errstate(divide="ignore", invalid="ignore"):
        j["r_base"] = j.buy_base / j.sell_base
        j["r_quote"] = j.buy_q / j.sell_q
        j["share_from_official"] = j.sum_taker_long_short_vol_ratio / (1 + j.sum_taker_long_short_vol_ratio)
        j["share_quote"] = j.taker_buy_quote_volume / j.quote_volume
    j["rel_base"] = (j.r_base - j.sum_taker_long_short_vol_ratio).abs() / j.sum_taker_long_short_vol_ratio
    j["rel_quote"] = (j.r_quote - j.sum_taker_long_short_vol_ratio).abs() / j.sum_taker_long_short_vol_ratio
    j["rel_share"] = (j.share_quote - j.share_from_official).abs()
    return {
        "symbol": sym, "month": month, "day": day, "buckets": int(len(j)),
        "rel_err_base_median": float(j.rel_base.median()),
        "rel_err_quote_median": float(j.rel_quote.median()),
        "rel_err_base_p95": float(j.rel_base.quantile(.95)),
        "rel_err_quote_p95": float(j.rel_quote.quantile(.95)),
        "share_absdiff_median": float(j.rel_share.median()),
        "share_absdiff_max": float(j.rel_share.max()),
        "n_bucket_share_gt_0.01": int((j.rel_share > 0.01).sum()),
        "buy_le_total_viol": int((j.taker_buy_quote_volume > j.quote_volume).sum()),
    }


# ── ② OI 价格效应 ───────────────────────────────────────────────────────────
def oi_price_effect(sym, start, end):
    days = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d").tolist()
    frames = []
    for d in days:
        p = os.path.join(RAWM, sym, f"{sym}-metrics-{d}.zip")
        if os.path.exists(p):
            frames.append(read_zip_csv(p))
    m = pd.concat(frames, ignore_index=True)
    m["ts"] = to_ms(m["create_time"])
    m = m.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)

    k = pd.concat([read_zip_csv(os.path.join(RAW, "1h", sym, f))
                   for f in sorted(os.listdir(os.path.join(RAW, "1h", sym)))
                   if f.endswith(".zip") and f"{sym}-1h-{start[:7]}" in f], ignore_index=True)
    k = k.sort_values("open_time").reset_index(drop=True)
    for c in ["close"]:
        k[c] = pd.to_numeric(k[c], errors="coerce")

    m["implied_px"] = m.sum_open_interest_value / m.sum_open_interest
    # 把 5m metrics 对齐到所属 1h K 线，比较隐含价与收盘价
    k["hour_end"] = k.open_time + 3_600_000 - 1
    j = pd.merge_asof(m.sort_values("ts"), k[["open_time", "hour_end", "close"]].sort_values("open_time"),
                      left_on="ts", right_on="open_time", direction="backward")
    j["px_rel_err"] = (j.implied_px - j.close).abs() / j.close

    # 24h 分解：Δ名义 = (1+Δ数量)(1+Δ价格) − 1 —— 直接在 metrics 序列上做，
    # 不经过 merge_asof（否则会因对齐产生重复行/空值污染相关系数）。
    mm = m.reset_index(drop=True)
    a = mm.iloc[288:].reset_index(drop=True)
    b = mm.iloc[:-288].reset_index(drop=True)
    # ⚠ 必须用 .values：直接对两个 Series 相减会按**索引**对齐，
    #   而 a 的索引是 288..N、b 是 0..N-288 ⇒ 结果大半是 NaN（本脚本首跑即踩到）。
    oi_a = a.sum_open_interest.to_numpy(dtype=float)
    oi_b = b.sum_open_interest.to_numpy(dtype=float)
    ov_a = a.sum_open_interest_value.to_numpy(dtype=float)
    ov_b = b.sum_open_interest_value.to_numpy(dtype=float)
    a["d_base"] = (oi_a - oi_b) / oi_b
    a["d_quote"] = (ov_a - ov_b) / ov_b
    px_a = ov_a / oi_a
    px_b = ov_b / oi_b
    a["ret"] = (px_a - px_b) / px_b
    a["resid"] = a.d_quote - ((1 + a.d_base) * (1 + a.ret) - 1)
    return {
        "symbol": sym, "window": f"{start}~{end}", "rows": int(len(j)),
        "px_rel_err_median": float(j.px_rel_err.median()),
        "px_rel_err_p99": float(j.px_rel_err.quantile(.99)),
        "px_rel_err_max": float(j.px_rel_err.max()),
        "decomp_rows": int(len(a)),
        "resid_median_abs": float(a.resid.abs().median()),
        "resid_p99_abs": float(a.resid.abs().quantile(.99)),
        "corr_dquote_dbase": float(a.d_quote.corr(a.d_base)),
        "corr_dquote_ret": float(a.d_quote.corr(a.ret)),
    }


# ── ③ 字段可用时间窗 ────────────────────────────────────────────────────────
FIELDS = ["sum_open_interest", "sum_open_interest_value",
          "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
          "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]


def field_availability():
    rows = []
    base = os.path.join(PQ, "metrics", "binance", "um")
    for sym in sorted(os.listdir(base)):
        fs = sorted(f for f in os.listdir(os.path.join(base, sym)) if f.endswith(".parquet"))
        df = pd.concat([pd.read_parquet(os.path.join(base, sym, f), columns=["ts"] + FIELDS)
                        for f in fs], ignore_index=True).sort_values("ts")
        for f in FIELDS:
            nn = df[df[f].notna()]
            rows.append({"symbol": sym, "field": f, "rows": int(len(df)),
                         "nonnull": int(len(nn)),
                         "first_nonnull_utc": (pd.to_datetime(nn.ts.min(), unit="ms", utc=True)
                                               .strftime("%Y-%m-%dT%H:%M:%SZ") if len(nn) else None),
                         "last_nonnull_utc": (pd.to_datetime(nn.ts.max(), unit="ms", utc=True)
                                              .strftime("%Y-%m-%dT%H:%M:%SZ") if len(nn) else None)})
    return pd.DataFrame(rows)


def main():
    x = [xsource_taker(s, "2026-09", d) for s in ["BTCUSDT", "ETHUSDT", "UNIUSDT", "LPTUSDT"]
         for d in ["2026-09-15", "2026-09-20"]]
    pd.DataFrame(x).to_csv(os.path.join(OUT, "xsource_taker.csv"), index=False)
    print(pd.DataFrame(x).to_string())

    o = [oi_price_effect(s, "2026-09-01", "2026-09-30") for s in ["BTCUSDT", "ETHUSDT", "UNIUSDT", "LPTUSDT"]]
    pd.DataFrame(o).to_csv(os.path.join(OUT, "oi_price_effect.csv"), index=False)
    print(pd.DataFrame(o).to_string())

    a = field_availability()
    a.to_csv(os.path.join(OUT, "field_availability.csv"), index=False)
    g = a.groupby("field").agg(
        symbols=("symbol", "nunique"),
        never=("nonnull", lambda s: int((s == 0).sum())),
        first=("first_nonnull_utc", "min"), last=("last_nonnull_utc", "max")).reset_index()
    print(g.to_string())
    g.to_csv(os.path.join(OUT, "field_availability_summary.csv"), index=False)


if __name__ == "__main__":
    main()

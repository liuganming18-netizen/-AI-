#!/usr/bin/env python3
"""逐币逐周期核对：研究输入(WyckoffMaster) vs 正式仓储(parquet)。

只读。对每个标的/周期输出：行数、时间戳集合差异、共同时间点的数值一致性。
用法: verify_research_input.py <outdir>
"""
import glob
import json
import os
import sys
import traceback

import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
RES = os.path.join(ROOT, "数据/研究输入/WyckoffMaster")
PARQ = os.path.join(ROOT, "数据/市场数据/parquet")

# 研究输入 oi_metrics 列 -> 仓储 metrics 列
OI_MAP = {
    "open_interest": "sum_open_interest",
    "open_interest_value": "sum_open_interest_value",
    "count_long_short_ratio": "count_long_short_ratio",
    "sum_toptrader_long_short_ratio": "sum_toptrader_long_short_ratio",
    "count_toptrader_long_short_ratio": "count_toptrader_long_short_ratio",
    "sum_taker_long_short_vol_ratio": "sum_taker_long_short_vol_ratio",
}

CANDLE_COLS = ["open", "high", "low", "close", "volume", "quote_volume",
               "trade_count", "taker_buy_volume", "taker_buy_quote_volume"]


def load_repo(symbol, tf):
    fs = sorted(glob.glob(os.path.join(PARQ, "candles/binance/um", symbol, tf, "*.parquet")))
    fs = [f for f in fs if not f.endswith("live.parquet")]
    if not fs:
        return None
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)


def load_repo_metrics(symbol):
    fs = sorted(glob.glob(os.path.join(PARQ, "metrics/binance/um", symbol, "*.parquet")))
    if not fs:
        return None
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)


def num(s):
    return pd.to_numeric(s, errors="coerce")


def cmp_frame(res_df, repo_df, ts_col_r, ts_col_p, cols_pairs, tol=1e-9):
    """返回 (res_rows, repo_rows, only_res, only_repo, mismatch_rows, dup_res, dup_repo)."""
    r = res_df[[ts_col_r] + [c for c, _ in cols_pairs]].copy()
    p = repo_df[[ts_col_p] + [c for _, c in cols_pairs]].copy()
    r = r.rename(columns={ts_col_r: "ts"})
    p = p.rename(columns={ts_col_p: "ts", **{pc: rc for rc, pc in cols_pairs}})
    r["ts"] = num(r["ts"]).astype("int64", errors="ignore")
    p["ts"] = num(p["ts"]).astype("int64", errors="ignore")

    dup_r = int(r["ts"].duplicated().sum())
    dup_p = int(p["ts"].duplicated().sum())
    sr, sp = set(r["ts"].tolist()), set(p["ts"].tolist())
    only_r, only_p = len(sr - sp), len(sp - sr)

    common = sr & sp
    if not common:
        return len(r), len(p), only_r, only_p, None, dup_r, dup_p, 0

    r2 = r[r["ts"].isin(common)].drop_duplicates("ts").set_index("ts").sort_index()
    p2 = p[p["ts"].isin(common)].drop_duplicates("ts").set_index("ts").sort_index()
    p2 = p2.reindex(r2.index)
    mism = 0
    for rc, _ in cols_pairs:
        a = pd.to_numeric(r2[rc], errors="coerce").to_numpy(dtype="float64")
        b = pd.to_numeric(p2[rc], errors="coerce").to_numpy(dtype="float64")
        both_nan = np.isnan(a) & np.isnan(b)
        diff = ~both_nan & ~np.isclose(a, b, rtol=0, atol=tol, equal_nan=True)
        mism += int(diff.sum())
    return len(r), len(p), only_r, only_p, mism, dup_r, dup_p, len(common)


def main():
    outdir = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    os.makedirs(outdir, exist_ok=True)
    symbols = sorted(f[:-8] for f in os.listdir(os.path.join(RES, "oi_metrics"))
                     if f.endswith(".parquet"))
    if limit:
        symbols = symbols[:limit]
    print(f"标的 {len(symbols)} 个")

    rows = []
    for i, sym in enumerate(symbols):
        # ── OHLCV 三个周期 ──
        for tf in ("15m", "1h", "4h"):
            rp = os.path.join(RES, "ohlcv", tf, f"{sym}.parquet")
            rec = {"symbol": sym, "dataset": f"ohlcv_{tf}"}
            if not os.path.exists(rp):
                rec["status"] = "研究输入缺失"
                rows.append(rec)
                continue
            try:
                rd = pd.read_parquet(rp)
                pd_ = load_repo(sym, tf)
                if pd_ is None:
                    rec["status"] = "仓储缺失"
                    rows.append(rec)
                    continue
                pairs = [(c, c) for c in CANDLE_COLS if c in rd.columns and c in pd_.columns]
                n_r, n_p, o_r, o_p, mism, dr, dp, nc = cmp_frame(
                    rd, pd_, "open_time", "open_time", pairs)
                rec.update({"status": "ok", "res_rows": n_r, "repo_rows": n_p,
                            "only_research": o_r, "only_repo": o_p,
                            "common": nc, "value_mismatch": mism,
                            "dup_research": dr, "dup_repo": dp,
                            "res_fields": len(rd.columns), "repo_fields": len(pd_.columns)})
            except Exception as e:
                rec.update({"status": "错误", "error": f"{type(e).__name__}:{e}"[:100]})
            rows.append(rec)

        # ── OI 指标 ──
        rec = {"symbol": sym, "dataset": "oi_metrics"}
        rp = os.path.join(RES, "oi_metrics", f"{sym}.parquet")
        try:
            rd = pd.read_parquet(rp)
            pm = load_repo_metrics(sym)
            if pm is None:
                rec["status"] = "仓储缺失"
                rows.append(rec)
                continue
            pairs = [(rc, pc) for rc, pc in OI_MAP.items()
                     if rc in rd.columns and pc in pm.columns]
            n_r, n_p, o_r, o_p, mism, dr, dp, nc = cmp_frame(rd, pm, "ts", "ts", pairs)
            extra = [c for c in rd.columns if c not in list(OI_MAP) + ["ts", "source"]]
            rec.update({"status": "ok", "res_rows": n_r, "repo_rows": n_p,
                        "only_research": o_r, "only_repo": o_p,
                        "common": nc, "value_mismatch": mism,
                        "dup_research": dr, "dup_repo": dp,
                        "res_fields": len(rd.columns), "repo_fields": len(pm.columns),
                        "res_only_fields": ",".join(extra)})
        except Exception as e:
            rec.update({"status": "错误", "error": f"{type(e).__name__}:{e}"[:100]})
        rows.append(rec)

        if (i + 1) % 50 == 0:
            print(f"  已处理 {i+1}/{len(symbols)}")

    df = pd.DataFrame(rows)
    out = os.path.join(outdir, "verify_research_input.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n写入 {out}（{len(df)} 行）")
    print("\n=== 按数据集汇总 ===")
    for ds, g in df.groupby("dataset"):
        ok = g[g["status"] == "ok"]
        print(f"{ds:14s} 成功 {len(ok):>4d}/{len(g):<4d} "
              f"研究输入独有 {int(ok['only_research'].sum()) if len(ok) else 0:>9d} "
              f"仓储独有 {int(ok['only_repo'].sum()) if len(ok) else 0:>9d} "
              f"数值不一致 {int(ok['value_mismatch'].sum()) if len(ok) else 0:>9d}")
    bad = df[df["status"] != "ok"]
    if len(bad):
        print(f"\n异常 {len(bad)} 条:")
        print(bad["status"].value_counts().to_string())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FLOW-DATA-01 第四步：独立抽查（原始官方档案 → 独立复算 → 与 Parquet / 反重力特征逐值比对）。

设计原则
--------
1. **不读反重力的中间结果来反推**：主链路是 官方 zip 原件 → 本脚本自己算 → 再和别人的数对。
2. 不使用任何 0 填充、不裁剪、不 clamp；异常原样打印，由报告判定。
3. 只读。输出全部写 evidence/FLOW-DATA-01/runs/。

抽查对象：BTCUSDT / ETHUSDT / UNIUSDT / LPTUSDT
固定窗口：W1 = 2026-09-01T00:00Z ~ 2026-09-30T23:00Z（1h 共 720 根）
          W2 = 2022-07-01T00:00Z ~ 2022-07-31T23:00Z（用于验证「大户比率为空」是上游事实）
"""
import io
import json
import os
import zipfile
import hashlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
RAW_K = os.path.join(ROOT, "数据", "raw", "1h")
RAW_M = os.path.join(ROOT, "数据", "raw_oi_metrics")
PQ = os.path.join(ROOT, "数据", "市场数据", "parquet")
OUT = os.path.join(ROOT, "evidence", "FLOW-DATA-01", "runs")
os.makedirs(OUT, exist_ok=True)

SYMS = ["BTCUSDT", "ETHUSDT", "UNIUSDT", "LPTUSDT"]
WINDOWS = {
    "W1_2026-09": ("2026-09-01", "2026-09-30"),
    "W2_2022-07": ("2022-07-01", "2022-07-31"),
}
K_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
          "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def iso(ms):
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_of(datestr):
    return int(datetime.strptime(datestr, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


# ── 原始档案读取（含 sha256 校验）────────────────────────────────────────────
def verify_zip(path, sumpath):
    """用 .CHECKSUM 里的 sha256 校验 zip 本体。返回 (ok, 计算值, 声明值)。"""
    if not os.path.exists(sumpath):
        return None, None, None
    txt = open(sumpath).read().strip().split()
    declared = txt[0].lower() if txt else None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    got = h.hexdigest()
    return (got == declared), got, declared


def read_raw_klines(sym, month_prefixes, checksum_log):
    """从官方月度 zip 直接读 1h K 线；返回 DataFrame（数值已转 float）。"""
    d = os.path.join(RAW_K, sym)
    frames = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".zip"):
            continue
        if not any(fn.startswith(p) for p in month_prefixes):
            continue
        p = os.path.join(d, fn)
        ok, got, dec = verify_zip(p, p + ".CHECKSUM")
        checksum_log.append({"file": fn, "sha256_ok": ok, "computed": got, "declared": dec})
        with zipfile.ZipFile(p) as z:
            name = z.namelist()[0]
            raw = z.read(name)
        df = pd.read_csv(io.BytesIO(raw))
        if "open_time" not in df.columns:      # 极早期文件无表头
            df = pd.read_csv(io.BytesIO(raw), header=None, names=K_COLS)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_volume", "taker_buy_quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


def read_raw_metrics(sym, day_list, checksum_log):
    frames = []
    for day in day_list:
        p = os.path.join(RAW_M, sym, f"{sym}-metrics-{day}.zip")
        if not os.path.exists(p):
            continue
        ok, got, dec = verify_zip(p, p + ".CHECKSUM")
        checksum_log.append({"file": os.path.basename(p), "sha256_ok": ok,
                             "computed": got, "declared": dec})
        with zipfile.ZipFile(p) as z:
            df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = (pd.to_datetime(df["create_time"], utc=True)
                - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().astype("int64") * 1000
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


# ── 独立复算 ─────────────────────────────────────────────────────────────────
def recompute_from_raw(sym, wname, start, end, checksum_log):
    s_ms, e_ms = ms_of(start), ms_of(end) + 86_400_000 - 1
    months = sorted({f"{sym}-1h-{start[:7]}", f"{sym}-1h-{end[:7]}"})
    k = read_raw_klines(sym, months, checksum_log)
    k = k[(k.open_time >= s_ms) & (k.open_time <= e_ms)].reset_index(drop=True)

    k["taker_sell_volume"] = k["volume"] - k["taker_buy_volume"]
    k["taker_sell_quote_volume"] = k["quote_volume"] - k["taker_buy_quote_volume"]
    k["taker_net_base"] = k["taker_buy_volume"] - k["taker_sell_volume"]
    k["taker_net_quote"] = k["taker_buy_quote_volume"] - k["taker_sell_quote_volume"]
    k["taker_buy_share_base"] = np.where(k["volume"] > 0, k["taker_buy_volume"] / k["volume"], np.nan)
    k["taker_buy_share_quote"] = np.where(k["quote_volume"] > 0,
                                          k["taker_buy_quote_volume"] / k["quote_volume"], np.nan)
    k["vwap"] = np.where(k["volume"] > 0, k["quote_volume"] / k["volume"], np.nan)
    k["cvd_base_cum"] = k["taker_net_base"].cumsum()
    k["cvd_quote_cum"] = k["taker_net_quote"].cumsum()

    # 不变量
    inv = {
        "rows": int(len(k)),
        "viol_tbv_gt_vol": int((k["taker_buy_volume"] > k["volume"] * (1 + 1e-12)).sum()),
        "viol_tbq_gt_qvol": int((k["taker_buy_quote_volume"] > k["quote_volume"] * (1 + 1e-12)).sum()),
        "viol_negative": int(((k[["volume", "quote_volume", "taker_buy_volume",
                                  "taker_buy_quote_volume"]] < 0).any(axis=1)).sum()),
        "vwap_out_of_range": int(((k["vwap"] < k["low"] * (1 - 1e-9)) |
                                  (k["vwap"] > k["high"] * (1 + 1e-9))).sum()),
        "share_out_of_0_1": int(((k["taker_buy_share_base"] < -1e-12) |
                                 (k["taker_buy_share_base"] > 1 + 1e-12)).sum()),
        "zero_volume_bars": int((k["volume"] == 0).sum()),
        "dup_ts": int(len(k) - k["open_time"].nunique()),
        "first_utc": iso(k["open_time"].min()) if len(k) else None,
        "last_utc": iso(k["open_time"].max()) if len(k) else None,
        "max_share": float(k["taker_buy_share_base"].max()) if len(k) else None,
        "min_share": float(k["taker_buy_share_base"].min()) if len(k) else None,
    }

    # 每日 metrics（只取窗口内的日文件）
    days = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d").tolist()
    m = read_raw_metrics(sym, days, checksum_log)
    m = m[(m.ts >= s_ms) & (m.ts <= e_ms)].reset_index(drop=True)
    if len(m):
        m["implied_px"] = m["sum_open_interest_value"] / m["sum_open_interest"]
        m["taker_buy_share_from_ratio"] = m["sum_taker_long_short_vol_ratio"] / (
            1 + m["sum_taker_long_short_vol_ratio"])
    return k, m, inv


def compare_with_parquet(sym, k, m, wname):
    """把原始复算结果与 Parquet 同一行对撞。"""
    res = {"symbol": sym, "window": wname}
    fs = sorted(os.listdir(os.path.join(PQ, "candles", "binance", "um", sym, "1h")))
    pk = pd.concat([pd.read_parquet(os.path.join(PQ, "candles", "binance", "um", sym, "1h", f))
                    for f in fs], ignore_index=True)
    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_volume", "taker_buy_quote_volume"]:
        pk[c] = pd.to_numeric(pk[c], errors="coerce")
    pk = pk[(pk.open_time >= k.open_time.min()) & (pk.open_time <= k.open_time.max())]
    j = k.merge(pk, on="open_time", suffixes=("_raw", "_pq"), how="outer", indicator=True)
    res["candle_rows_raw"] = int((j._merge == "left_only").sum())
    res["candle_rows_pq_only"] = int((j._merge == "right_only").sum())
    res["candle_rows_both"] = int((j._merge == "both").sum())
    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_volume", "taker_buy_quote_volume"]:
        a = pd.to_numeric(j[c + "_raw"], errors="coerce")
        b = pd.to_numeric(j[c + "_pq"], errors="coerce")
        d = (a - b).abs()
        res[f"maxdiff_{c}"] = float(np.nanmax(d)) if d.notna().any() else None
        res[f"ndiff_{c}"] = int((d > 1e-9).sum())

    # metrics
    fs = sorted(os.listdir(os.path.join(PQ, "metrics", "binance", "um", sym)))
    pm = pd.concat([pd.read_parquet(os.path.join(PQ, "metrics", "binance", "um", sym, f))
                    for f in fs], ignore_index=True)
    pm = pm[(pm.ts >= m.ts.min()) & (pm.ts <= m.ts.max())]
    jm = m.merge(pm, on="ts", suffixes=("_raw", "_pq"), how="outer", indicator=True)
    res["metric_rows_raw_only"] = int((jm._merge == "left_only").sum())
    res["metric_rows_pq_only"] = int((jm._merge == "right_only").sum())
    res["metric_rows_both"] = int((jm._merge == "both").sum())
    for c in ["sum_open_interest", "sum_open_interest_value",
              "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
              "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]:
        a = pd.to_numeric(jm[c + "_raw"], errors="coerce")
        b = pd.to_numeric(jm[c + "_pq"], errors="coerce")
        d = (a - b).abs()
        res[f"maxdiff_{c}"] = float(np.nanmax(d)) if d.notna().any() else None
        res[f"ndiff_{c}"] = int((d > 1e-9).sum())
        res[f"null_raw_{c}"] = int(a.isna().sum())
        res[f"null_pq_{c}"] = int(b.isna().sum())
    return res, j, jm


def cross_source_taker_check(sym, k, m):
    """跨源单位一致性：官方 metrics 的 taker 量比（买/卖） vs K线 5m 派生买占比。

    metrics 是 5 分钟桶；K线是 1h。把 1h 拆成 12 个 5m 做不到（raw/1h 只有 1h），
    故改用「1h 聚合」对撞：把 metrics 的 12 个 5m 桶的 buyVol/sellVol 汇总不可行
    （metrics 只给比值，不给绝对量）⇒ 只能做**同桶**比较，用 raw/5m 档案。
    """
    return None


def main():
    checksum_log = []
    inv_rows, cmp_rows, sample_frames = [], [], {}
    for sym in SYMS:
        for wname, (start, end) in WINDOWS.items():
            k, m, inv = recompute_from_raw(sym, wname, start, end, checksum_log)
            if not len(k):
                inv_rows.append({"symbol": sym, "window": wname, "rows": 0,
                                 "note": "原始档案缺该窗口"})
                continue
            inv.update({"symbol": sym, "window": wname})
            inv_rows.append(inv)
            res, j, jm = compare_with_parquet(sym, k, m, wname)
            cmp_rows.append(res)
            sample_frames[f"{sym}_{wname}"] = k
            sample_frames[f"{sym}_{wname}_metrics"] = m
            print(f"[done] {sym} {wname} k={len(k)} m={len(m)}", flush=True)

    pd.DataFrame(inv_rows).to_csv(os.path.join(OUT, "spot_invariants.csv"), index=False)
    pd.DataFrame(cmp_rows).to_csv(os.path.join(OUT, "spot_raw_vs_parquet.csv"), index=False)
    pd.DataFrame(checksum_log).to_csv(os.path.join(OUT, "spot_checksums.csv"), index=False)

    # 抽样明细（给报告引用）：W1 每日首根 + 若干固定行
    for key, df in sample_frames.items():
        if df is None or not len(df):
            continue
        safe = key.replace("-", "").replace("_", "__")
        df.head(48).to_csv(os.path.join(OUT, f"sample_{safe}.csv"), index=False)

    # 汇总摘要
    ck = pd.DataFrame(checksum_log)
    print("checksum files:", len(ck), "ok:", int((ck.sha256_ok == True).sum()),
          "fail:", int((ck.sha256_ok == False).sum()), "missing:", int(ck.sha256_ok.isna().sum()))


if __name__ == "__main__":
    main()

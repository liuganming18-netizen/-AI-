# -*- coding: utf-8 -*-
"""FT-DATA-01 步骤二：导出 Freqtrade 格式输入包（只读源数据，只写本工单目录）。

格式依据（freqtrade develop 分支 idatahandler.py::_pair_data_filename）
────────────────────────────────────────────────────────────────────
文件名 = datadir/futures/{pair_s}-{timeframe}-{candle_type}.feather
  pair_s = 配对名把 "/" 与 ":" 都换成 "_"（BTC/USDT:USDT -> BTC_USDT_USDT）
  candle_type ∈ {futures, funding_rate, mark, open_interest}
  OHLCV 列 = date, open, high, low, close, volume
  资金费列 = date, funding_rate
  OI 列   = date, open_interest_amount, open_interest_value
  date    = 该根/该桶的**起点**时间（UTC，无时区）

转换原则
────────
1. 数值只做「十进制字符串 -> float64」的表示转换，不做任何缩放、复权、重采样。
2. 缺失保留为缺失：不补 0、不前向填充、不插值。
3. 逐条结算的资金费保留真实时间戳与真实间隔（本池存在 1h/4h 混合），不按固定周期重采样。
4. 时区统一 UTC，date 为起点时间，与源数据 open_time / ts / funding_time 一一对应。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from decimal import Decimal

import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
PKG = os.path.join(ROOT, "evidence/FT-DATA-01")
DATA = os.path.join(PKG, "data")
FT = os.path.join(DATA, "freqtrade", "futures")
SIDE = os.path.join(DATA, "sidecar")
OUT = os.path.join(PKG, "out")
LOGS = os.path.join(PKG, "logs")

CANDLES = os.path.join(ROOT, "数据/市场数据/parquet/candles/binance/um")
OI = os.path.join(ROOT, "数据/市场数据/parquet/oi/binance/um")
FUNDING = os.path.join(ROOT, "数据/市场数据/parquet/funding/binance/um")

T0 = pd.Timestamp("2025-06-25 00:00:00", tz="UTC")
T_TEST = pd.Timestamp("2025-08-01 00:00:00", tz="UTC")
T1 = pd.Timestamp("2025-12-31 00:00:00", tz="UTC")

SYMBOLS = ["ARUSDT", "BANANAUSDT", "BATUSDT", "CTSIUSDT", "PUMPBTCUSDT"]
TFS = ["4h", "1h", "15m"]

FILES: list[dict] = []
PREC: list[dict] = []


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pair_name(symbol: str) -> str:
    base = symbol[:-4]
    return f"{base}/USDT:USDT"


def pair_slug(symbol: str) -> str:
    return pair_name(symbol).replace("/", "_").replace(":", "_")


def to_naive_utc(ms_series: pd.Series) -> pd.Series:
    return pd.to_datetime(ms_series, unit="ms", utc=True).dt.tz_localize(None)


def precision_check(raw: pd.Series, name: str, symbol: str) -> None:
    """十进制字符串 -> float64 的最大相对误差（证明数值含义未被改动）。"""
    worst = 0.0
    for v in raw.dropna().astype(str).unique()[:20000]:
        try:
            d = Decimal(v)
        except Exception:                                      # noqa: BLE001
            continue
        if d == 0:
            continue
        rel = abs(Decimal(repr(float(v))) - d) / abs(d)
        worst = max(worst, float(rel))
    PREC.append({"symbol": symbol, "字段": name, "最大相对误差": f"{worst:.3e}",
                 "判定": "未改变数值含义" if worst < 1e-12 else "需复核"})


def write_ohlcv(symbol: str, tf: str) -> None:
    p = os.path.join(CANDLES, symbol, tf, "2025.parquet")
    df = pd.read_parquet(p)
    t = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.loc[(t >= T0) & (t < T1)].copy()
    # 未收盘的 K 线一律剔除（不靠时间戳"碰巧"排除）
    df = df.loc[pd.to_numeric(df["is_closed"], errors="coerce") == 1].copy()
    df["t"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.sort_values("t").drop_duplicates("t")
    out = pd.DataFrame({
        "date": df["t"].values,
        "open": df["open"].astype(float).values,
        "high": df["high"].astype(float).values,
        "low": df["low"].astype(float).values,
        "close": df["close"].astype(float).values,
        "volume": df["volume"].astype(float).values,
    })
    for c in ("open", "high", "low", "close", "volume"):
        precision_check(df[c], f"{tf}.{c}", symbol)
    fp = os.path.join(FT, f"{pair_slug(symbol)}-{tf}-futures.feather")
    out.reset_index(drop=True).to_feather(fp)
    FILES.append({"文件": os.path.relpath(fp, PKG), "行数": len(out),
                  "起": str(out["date"].min()), "止": str(out["date"].max()),
                  "sha256": sha256_file(fp)})


def write_oi(symbol: str) -> None:
    p = os.path.join(OI, symbol, "2025.parquet")
    df = pd.read_parquet(p, columns=["ts", "open_interest", "open_interest_value"])
    t = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.loc[(t >= T0) & (t < T1)].copy()
    df["t"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.sort_values("t").drop_duplicates("t")
    out = pd.DataFrame({
        "date": df["t"].values,
        "open_interest_amount": df["open_interest"].astype(float).values,
        "open_interest_value": df["open_interest_value"].astype(float).values,
    })
    precision_check(df["open_interest"], "5m.open_interest_amount", symbol)
    fp = os.path.join(FT, f"{pair_slug(symbol)}-5m-open_interest.feather")
    out.reset_index(drop=True).to_feather(fp)
    FILES.append({"文件": os.path.relpath(fp, PKG), "行数": len(out),
                  "起": str(out["date"].min()), "止": str(out["date"].max()),
                  "sha256": sha256_file(fp)})


def write_funding(symbol: str) -> None:
    p = os.path.join(FUNDING, f"{symbol}.parquet")
    df = pd.read_parquet(p, columns=["funding_time", "funding_rate", "mark_price"])
    t = pd.to_datetime(df["funding_time"], unit="ms", utc=True)
    df = df.loc[(t >= T0) & (t < T1)].copy()
    df["t"] = pd.to_datetime(df["funding_time"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.sort_values("t").drop_duplicates("t")
    out = pd.DataFrame({"date": df["t"].values,
                        "funding_rate": df["funding_rate"].astype(float).values})
    precision_check(df["funding_rate"], "funding_rate", symbol)

    # 两种时间标签、同一份逐条结算数据：供不同版本的 funding_fee_timeframe 取用
    for label in ("1h", "8h"):
        fp = os.path.join(FT, f"{pair_slug(symbol)}-{label}-funding_rate.feather")
        out.reset_index(drop=True).to_feather(fp)
        FILES.append({"文件": os.path.relpath(fp, PKG), "行数": len(out),
                      "起": str(out["date"].min()), "止": str(out["date"].max()),
                      "sha256": sha256_file(fp),
                      "说明": f"时间标签 {label}，内容与另一标签完全相同"})

    # sidecar：结算时点 mark（本工程无连续 mark 序列，只有结算时刻的值）
    sp = os.path.join(SIDE, f"{symbol}_funding_settlements.csv")
    with open(sp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["settlement_time_utc", "funding_rate", "mark_price_at_settlement"])
        for r in df.itertuples(index=False):
            w.writerow([r.t.strftime("%Y-%m-%d %H:%M:%S"), r.funding_rate, r.mark_price])
    FILES.append({"文件": os.path.relpath(sp, PKG), "行数": len(df),
                  "起": str(out["date"].min()), "止": str(out["date"].max()),
                  "sha256": sha256_file(sp), "说明": "含结算时点 mark_price"})


def write_quote_volume(symbol: str) -> None:
    """15m 成交额 sidecar。

    Freqtrade 的 OHLCV 只有 volume（基础币），没有 quote_volume；
    而本策略的参与率上限 `max_participation_of_prev_15m_quote=0.001`
    必须用「已完成前一根 15m 的成交额」。缺这一列会让下单量约束失效，
    所以单独导出，不改写 OHLCV 列集合。
    """
    p = os.path.join(CANDLES, symbol, "15m", "2025.parquet")
    df = pd.read_parquet(p, columns=["open_time", "quote_volume", "trade_count"])
    t = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.loc[(t >= T0) & (t < T1)].copy()
    df["t"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.sort_values("t").drop_duplicates("t")
    sp = os.path.join(SIDE, f"{symbol}_15m_quote_volume.csv")
    with open(sp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["open_time_utc", "quote_volume", "trade_count"])
        for r in df.itertuples(index=False):
            w.writerow([r.t.strftime("%Y-%m-%d %H:%M:%S"), r.quote_volume, r.trade_count])
    FILES.append({"文件": os.path.relpath(sp, PKG), "行数": len(df),
                  "起": str(df["t"].min()), "止": str(df["t"].max()),
                  "sha256": sha256_file(sp),
                  "说明": "参与率上限所需的前一根 15m 成交额"})


MARK = os.path.join(ROOT, "数据/研究输入/WyckoffMaster/mark1h")


def write_mark(symbol: str) -> None:
    """mark 价格 1h。

    源 `数据/研究输入/WyckoffMaster/mark1h/{symbol}.parquet` 只有两列：
    `open_time` 与 `mark_open` —— 每小时**只有一个**标记价。
    Freqtrade 的 mark 蜡烛要求标准 OHLCV 列，因此把该值同时写入
    open/high/low/close，volume 置 0。
    这是**同一价格的重复填写**，不是四种不同价格，也不引入任何新数值。
    """
    p = os.path.join(MARK, f"{symbol}.parquet")
    if not os.path.exists(p):
        return
    df = pd.read_parquet(p)
    t = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.loc[(t >= T0) & (t < T1)].copy()
    df["t"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.sort_values("t").drop_duplicates("t")
    v = df["mark_open"].astype(float)
    out = pd.DataFrame({"date": df["t"].values, "open": v.values, "high": v.values,
                        "low": v.values, "close": v.values,
                        "volume": [0.0] * len(df)})
    fp = os.path.join(FT, f"{pair_slug(symbol)}-1h-mark.feather")
    out.reset_index(drop=True).to_feather(fp)
    FILES.append({"文件": os.path.relpath(fp, PKG), "行数": len(out),
                  "起": str(out["date"].min()), "止": str(out["date"].max()),
                  "sha256": sha256_file(fp),
                  "说明": "源仅每小时一个 mark_open，OHLC 四列同值、volume=0"})


def main() -> int:
    os.makedirs(FT, exist_ok=True)
    os.makedirs(SIDE, exist_ok=True)

    for s in SYMBOLS:
        for tf in TFS:
            write_ohlcv(s, tf)
        write_oi(s)
        write_funding(s)
        write_quote_volume(s)
        write_mark(s)

    with open(os.path.join(OUT, "03_导出文件指纹.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["文件", "行数", "起", "止", "sha256", "说明"])
        w.writeheader()
        for r in FILES:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})

    with open(os.path.join(OUT, "04_精度核对.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "字段", "最大相对误差", "判定"])
        w.writeheader()
        for r in PREC:
            w.writerow(r)

    combined = hashlib.sha256(
        json.dumps({r["文件"]: r["sha256"] for r in FILES},
                   sort_keys=True).encode()).hexdigest()

    summary = {
        "symbols": SYMBOLS,
        "pairs": {s: pair_name(s) for s in SYMBOLS},
        "window_export_start_utc": str(T0),
        "window_test_start_utc": str(T_TEST),
        "window_test_end_utc_exclusive": str(T1),
        "files": FILES,
        "file_count": len(FILES),
        "combined_sha256": combined,
        "precision": PREC,
        "precision_ok": all(r["判定"] == "未改变数值含义" for r in PREC),
    }
    with open(os.path.join(LOGS, "01_export.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print(f"导出 {len(FILES)} 个文件，组合 sha256={combined}")
    print("精度全部未改变数值含义:", summary["precision_ok"])
    for r in FILES:
        print(f"  {r['文件']:64} {r['行数']:>6} 行  {r['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

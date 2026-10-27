# -*- coding: utf-8 -*-
"""FT-LOW4H-GRID-AUDIT-02 公共加载与工具。只读上游 FT-LOW4H-GRID-01，不改任何上游文件。"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
UP = BASE_DIR / "evidence/FT-LOW4H-GRID-01"
PREV = BASE_DIR / "evidence/FT-STOP-GRID-02"          # 上一轮（2R + L10）原始账本
PREV_AUDIT = BASE_DIR / "evidence/FT-STOP-GRID-AUDIT-02"
OLD_AUDIT = BASE_DIR / "evidence/FT-LOW4H-GRID-AUDIT-01"
HERE = BASE_DIR / "evidence/FT-LOW4H-GRID-AUDIT-02"
OUT = HERE / "out"
DATA = BASE_DIR / "evidence/FT-FACTOR-03/user_data/data/binance/futures"

S_TIERS = ["S05", "S10", "S15", "S20"]
D_TIERS = ["D05", "D10", "D15", "D20"]
S_PCT = {"S05": 0.05, "S10": 0.10, "S15": 0.15, "S20": 0.20}
D_PCT = {"D05": 0.05, "D10": 0.10, "D15": 0.15, "D20": 0.20}
PARAMS = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
          "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
          "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
COMBOS = [f"{s}_{d}" for s in S_TIERS for d in D_TIERS]
ALL = [f"Wyckoff_C1_{s}_{d}_{p}" for s in S_TIERS for d in D_TIERS for p in PARAMS]

INIT_EQUITY = 2000.0
RISK_RATE = 0.005
FEE = 0.0005
BAR_4H_MS = 4 * 3600 * 1000
BAR_15M_MS = 15 * 60 * 1000
EXPECT_GROUPS = 144
EXPECT_COMBOS = 16


def load_plan() -> dict:
    with open(OUT / "01_冻结分析计划.json", encoding="utf-8") as f:
        return json.load(f)


def parse(name: str) -> tuple[str, str, str]:
    p = name.split("_")          # Wyckoff C1 S05 D05 Vs04 Z02
    return p[2], p[3], "_".join(p[4:])


def combo_of(name: str) -> str:
    s, d, _ = parse(name)
    return f"{s}_{d}"


def param_of(name: str) -> str:
    return parse(name)[2]


def s_of(name: str) -> float:
    return S_PCT[parse(name)[0]]


def d_of(name: str) -> float:
    return D_PCT[parse(name)[1]]


def load_trades(name: str) -> pd.DataFrame | None:
    """None = 文件不存在；空表 = 存在但零成交。"""
    p = UP / f"out/trades_{name}.csv"
    if not p.exists():
        return None
    if p.stat().st_size == 0:
        return pd.DataFrame()
    df = pd.read_csv(p, encoding="utf-8-sig")
    if df.empty:
        return pd.DataFrame()
    df["策略"] = name
    df["组合"] = combo_of(name)
    df["参数组"] = param_of(name)
    df["s"] = s_of(name)
    df["d"] = d_of(name)
    df["open_ms"] = pd.to_numeric(df["open_timestamp"], errors="coerce").astype("int64")
    df["close_ms"] = pd.to_numeric(df["close_timestamp"], errors="coerce").astype("int64")
    df["持仓小时"] = pd.to_numeric(df["trade_duration"], errors="coerce") / 60.0
    df["期末结算"] = df["exit_reason"].astype(str).eq("force_exit")
    return df


def load_all() -> tuple[pd.DataFrame, list[str], list[str]]:
    frames, missing, zero = [], [], []
    for n in ALL:
        d = load_trades(n)
        if d is None:
            missing.append(n)
        elif d.empty:
            zero.append(n)
        else:
            frames.append(d)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return out, missing, zero


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """按组（单账户）补权益、R、成本、风险占用。"""
    out = []
    for strat, g in df.groupby("策略", sort=False):
        g = g.sort_values(["close_ms", "open_ms"]).reset_index(drop=True)
        closed = g.loc[~g["期末结算"], ["close_ms", "profit_abs"]]
        closed = closed.groupby("close_ms", as_index=False)["profit_abs"].sum()
        cum_ms = closed["close_ms"].to_numpy()
        cum_val = INIT_EQUITY + np.cumsum(closed["profit_abs"].to_numpy())
        idx = np.searchsorted(cum_ms, g["open_ms"].to_numpy(), side="right") - 1
        g["开仓时权益"] = np.where(idx >= 0, cum_val[np.clip(idx, 0, None)], INIT_EQUITY)
        out.append(g)
    res = pd.concat(out, ignore_index=True)
    res["风险预算USDT"] = res["开仓时权益"] * RISK_RATE
    res["R"] = res["profit_abs"] / res["风险预算USDT"]
    amt = pd.to_numeric(res["amount"], errors="coerce")
    res["名义USDT"] = amt * pd.to_numeric(res["open_rate"], errors="coerce")
    res["名义占权益"] = res["名义USDT"] / res["开仓时权益"]
    # 计划风险 = 名义 × s（初始止损距离）
    res["计划风险USDT"] = res["名义USDT"] * res["s"]
    res["计划风险占权益"] = res["计划风险USDT"] / res["开仓时权益"]
    res["风险覆盖比"] = res["计划风险USDT"] / res["风险预算USDT"]
    # fee_open / fee_close 是费率不是金额
    fo = pd.to_numeric(res["fee_open"], errors="coerce").fillna(0.0)
    fc = pd.to_numeric(res["fee_close"], errors="coerce").fillna(0.0)
    res["开仓费USDT"] = amt * pd.to_numeric(res["open_rate"], errors="coerce") * fo
    res["平仓费USDT"] = amt * pd.to_numeric(res["close_rate"], errors="coerce") * fc
    res["手续费USDT"] = res["开仓费USDT"] + res["平仓费USDT"]
    res["手续费R"] = res["手续费USDT"] / res["风险预算USDT"]
    res["资金费USDT"] = pd.to_numeric(res["funding_fees"], errors="coerce").fillna(0.0)
    res["资金费R"] = res["资金费USDT"] / res["风险预算USDT"]
    res["价差盈亏USDT"] = (pd.to_numeric(res["close_rate"], errors="coerce") -
                          pd.to_numeric(res["open_rate"], errors="coerce")) * amt
    res["实际成交亏损USDT"] = res["价差盈亏USDT"] + res["资金费USDT"]
    return res


def ms_of_series(s: pd.Series) -> np.ndarray:
    return ((s - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy(dtype=np.int64)


_K: dict[tuple[str, str], pd.DataFrame] = {}


def kline(pair: str, tf: str) -> pd.DataFrame | None:
    key = (pair, tf)
    if key in _K:
        return _K[key]
    sym = pair.replace("/", "_").replace(":", "_")
    p = DATA / f"{sym}-{tf}-futures.feather"
    if not p.exists():
        return None
    df = pd.read_feather(p)
    df["ms"] = ms_of_series(df["date"])
    _K[key] = df
    return df


def load_4h(pair: str) -> pd.DataFrame | None:
    """按策略源码的方式重建：open_ms = date.astype(int64)（该版本 freqtrade 为毫秒），close_ms = open_ms + 4h。"""
    df = kline(pair, "4h")
    if df is None or df.empty:
        return None
    out = pd.DataFrame({
        "open_ms": df["ms"].astype(np.int64),
        "close_ms": df["ms"].astype(np.int64) + BAR_4H_MS,
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
    })
    return out


def load_15m(pair: str) -> pd.DataFrame | None:
    df = kline(pair, "15m")
    if df is None or df.empty:
        return None
    return pd.DataFrame({
        "ms": df["ms"].astype(np.int64),
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
    })


def tick_of(raw) -> float:
    s = str(raw or "").strip()
    if "." not in s:
        return 1.0
    return 10.0 ** (-len(s.split(".")[1]))


def sign_test_p(pos: int, neg: int) -> float:
    """双侧符号检验（精确二项）。"""
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def bh_fdr(pvals: list[float]) -> list[float]:
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        v = pvals[i] * m / (rank + 1)
        prev = min(prev, v)
        adj[i] = min(1.0, prev)
    return adj


def max_dd(eq: np.ndarray) -> tuple[float, float, int, int]:
    """返回 (最大回撤金额, 占峰值%, 峰索引, 谷索引)。"""
    if len(eq) == 0:
        return 0.0, 0.0, 0, 0
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    i = int(np.argmax(dd))
    j = int(np.argmax(eq[: i + 1]))
    pct = float(dd[i] / peak[i] * 100) if peak[i] > 0 else 0.0
    return float(dd[i]), pct, j, i

# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-02 公共加载与工具。只读上游 FT-STOP-GRID-02。"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
UP = BASE_DIR / "evidence/FT-STOP-GRID-02"
HERE = BASE_DIR / "evidence/FT-STOP-GRID-AUDIT-02"
OUT = HERE / "out"
DATA = BASE_DIR / "evidence/FT-ENTRY-02/user_data/data/binance/futures"

TIERS = ["Stop05", "Stop10", "Stop15", "Stop20"]
TIER_PCT = {"Stop05": 0.05, "Stop10": 0.10, "Stop15": 0.15, "Stop20": 0.20}
SUFFIXES = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
            "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
            "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
ALL = [f"Wyckoff_C1_{t}_{s}" for t in TIERS for s in SUFFIXES]

INIT_EQUITY = 2000.0
RISK_RATE = 0.005
FEE = 0.0005
STOP_LABELS = ("stop_loss", "trailing_stop_loss")
BAR_4H_MS = 4 * 3600 * 1000
BAR_15M_MS = 15 * 60 * 1000


def load_plan() -> dict:
    with open(OUT / "01_冻结分析计划.json", encoding="utf-8") as f:
        return json.load(f)


def tier_of(name: str) -> str:
    return name.split("_")[2]


def suffix_of(name: str) -> str:
    return "_".join(name.split("_")[3:])


def event_id(tag: str) -> str | None:
    """从 enter_tag 提取事件 ID：cand_C1_ev_<策略>_<币>_<起>_<止> 的后 7 段。"""
    if not isinstance(tag, str) or "cand_C1_" not in tag:
        return None
    return "_".join(tag.split("cand_C1_", 1)[-1].split("_")[:7])


def ms_of(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("int64")


def load_trades(name: str) -> pd.DataFrame | None:
    """返回 None = 文件不存在（未跑完）；返回空 DataFrame = 文件存在但零成交。"""
    p = UP / f"out/trades_{name}.csv"
    if not p.exists():
        return None
    if p.stat().st_size == 0:
        return pd.DataFrame()
    df = pd.read_csv(p, encoding="utf-8-sig")
    if df.empty:
        return pd.DataFrame()
    df["策略"] = name
    df["档位"] = tier_of(name)
    df["参数组"] = suffix_of(name)
    df["止损比例"] = TIER_PCT[tier_of(name)]
    df["事件ID"] = df["enter_tag"].map(event_id)
    df["open_ms"] = ms_of(df["open_timestamp"])
    df["close_ms"] = ms_of(df["close_timestamp"])
    df["持仓小时"] = pd.to_numeric(df["trade_duration"], errors="coerce") / 60.0
    df["是否期末结算"] = df["exit_reason"].astype(str).eq("force_exit")
    return df


def load_all(require: int = 36) -> tuple[pd.DataFrame, list[str]]:
    """返回 (逐笔合并表, 未落盘的组)。零成交但已落盘的组单独记在 attrs 里。"""
    frames, missing, zero = [], [], []
    for n in ALL:
        d = load_trades(n)
        if d is None:
            missing.append(n)
        elif d.empty:
            zero.append(n)
        else:
            frames.append(d)
    if not frames:
        out = pd.DataFrame()
    else:
        out = pd.concat(frames, ignore_index=True)
    out.attrs["零成交组"] = zero
    return out, missing


def equity_curve(df: pd.DataFrame) -> pd.DataFrame:
    """按平仓时间累积权益，返回带 equity_at_entry 的逐笔表。

    权益 = 2000 + Σ(close_ms ≤ open_ms 的 profit_abs)。
    """
    out = []
    for strat, g in df.groupby("策略", sort=False):
        g = g.sort_values(["close_ms", "open_ms"]).reset_index(drop=True)
        closed = g.loc[~g["是否期末结算"], ["close_ms", "profit_abs"]]
        closed = closed.groupby("close_ms", as_index=False)["profit_abs"].sum()
        cum_ms = closed["close_ms"].to_numpy()
        cum_val = INIT_EQUITY + np.cumsum(closed["profit_abs"].to_numpy())
        idx = np.searchsorted(cum_ms, g["open_ms"].to_numpy(), side="right") - 1
        eq = np.where(idx >= 0, cum_val[np.clip(idx, 0, None)], INIT_EQUITY)
        g["equity_at_entry"] = eq
        out.append(g)
    res = pd.concat(out, ignore_index=True)
    res["风险预算USDT"] = res["equity_at_entry"] * RISK_RATE
    res["R"] = res["profit_abs"] / res["风险预算USDT"]
    res["名义USDT"] = pd.to_numeric(res["amount"], errors="coerce") * pd.to_numeric(res["open_rate"], errors="coerce")
    res["名义占权益"] = res["名义USDT"] / res["equity_at_entry"]
    res["实际风险USDT"] = res["名义USDT"] * res["止损比例"]
    res["实际风险占权益"] = res["实际风险USDT"] / res["equity_at_entry"]
    res["风险覆盖比"] = res["实际风险USDT"] / res["风险预算USDT"]
    # fee_open / fee_close 是**费率**（恒 0.0005），不是金额
    amt = pd.to_numeric(res["amount"], errors="coerce")
    fo = pd.to_numeric(res["fee_open"], errors="coerce").fillna(0)
    fc = pd.to_numeric(res["fee_close"], errors="coerce").fillna(0)
    res["开仓费USDT"] = amt * pd.to_numeric(res["open_rate"], errors="coerce") * fo
    res["平仓费USDT"] = amt * pd.to_numeric(res["close_rate"], errors="coerce") * fc
    res["手续费USDT"] = res["开仓费USDT"] + res["平仓费USDT"]
    res["手续费R"] = res["手续费USDT"] / res["风险预算USDT"]
    res["资金费USDT"] = pd.to_numeric(res["funding_fees"], errors="coerce").fillna(0)
    res["资金费R"] = res["资金费USDT"] / res["风险预算USDT"]
    res["价差盈亏USDT"] = (pd.to_numeric(res["close_rate"], errors="coerce") -
                          pd.to_numeric(res["open_rate"], errors="coerce")) * amt
    res["毛利R"] = res["价差盈亏USDT"] / res["风险预算USDT"]
    return res


def tick_of(raw) -> float:
    """按书写小数位推最小价位。"""
    s = str(raw or "").strip()
    if "." not in s:
        return 1.0
    return 10.0 ** (-len(s.split(".")[1]))


def max_drawdown_r(r: np.ndarray) -> float:
    if len(r) == 0:
        return 0.0
    cum = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    dd = peak - cum
    return float(dd.max())


def max_drawdown_usdt(pnl: np.ndarray) -> float:
    return max_drawdown_r(pnl)


def kline_path(pair: str, tf: str = "15m") -> Path:
    sym = pair.replace("/", "_").replace(":", "_")
    return DATA / f"{sym}-{tf}-futures.feather"


def load_kline(pair: str, tf: str = "15m") -> pd.DataFrame | None:
    p = kline_path(pair, tf)
    if not p.exists():
        return None
    df = pd.read_feather(p)
    df["ms"] = (df["date"] - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)
    return df


_K4H_CACHE: dict[str, pd.DataFrame] = {}


def load_4h(pair: str) -> pd.DataFrame | None:
    """按策略源码的方式重建 4h 表：close_ms = open_ms + 4h，l10 = low.rolling(10).min()。"""
    if pair in _K4H_CACHE:
        return _K4H_CACHE[pair]
    df = load_kline(pair, "4h")
    if df is None or df.empty:
        return None
    df = df.copy()
    df["open_ms"] = df["ms"].astype("int64")
    df["close_ms"] = df["open_ms"] + BAR_4H_MS
    df["l10"] = df["low"].rolling(10).min()
    out = df[["open_ms", "close_ms", "open", "high", "low", "close", "l10"]].copy()
    _K4H_CACHE[pair] = out
    return out


def trailing_path(pair: str, open_rate: float, pct: float,
                  entry_ms: int, exit_ms: int) -> dict:
    """按上游源码逻辑，逐步重建一笔交易的 2R 激活与 L10 棘轮轨迹。

    只使用 close_ms <= 决策时刻 的 4h K 线；决策时刻取 15m 退出 K 线的开盘时间。
    返回激活时刻、激活后止损轨迹、以及"若在退出 K 线开盘时决策"的最终止损。
    """
    initial_stop = open_rate * (1.0 - pct)
    actual_r = open_rate * pct
    trigger_2r = open_rate + 2.0 * actual_r

    df4 = load_4h(pair)
    if df4 is None:
        return {"ok": False, "reason": "no_4h"}

    # 全窗口：从入场到退出（含退出 K 线开盘时刻）
    sub = df4[(df4["close_ms"] >= entry_ms) & (df4["close_ms"] <= exit_ms)]
    if sub.empty:
        return {"ok": False, "reason": "no_4h_in_window", "initial_stop": initial_stop,
                "actual_r": actual_r, "trigger_2r": trigger_2r,
                "activated": False, "final_stop": initial_stop}

    closes = sub["close"].to_numpy(dtype=float)
    l10s = sub["l10"].to_numpy(dtype=float)
    close_ms = sub["close_ms"].to_numpy(dtype=np.int64)

    # 2R 激活：第一条已收盘 4h 的 close >= trigger_2r
    hit = np.where(closes >= trigger_2r)[0]
    activated = bool(len(hit) > 0)
    act_ms = int(close_ms[hit[0]]) if activated else None
    act_idx = int(hit[0]) if activated else None

    # 棘轮：激活后每根已收盘 4h 的 l10 只上移
    stop = initial_stop
    path = []
    if activated:
        for i in range(act_idx, len(l10s)):
            v = l10s[i]
            if math.isfinite(v) and v > stop:
                stop = v
                path.append({"close_ms": int(close_ms[i]), "stop": float(stop)})

    return {
        "ok": True,
        "initial_stop": float(initial_stop),
        "actual_r": float(actual_r),
        "trigger_2r": float(trigger_2r),
        "activated": activated,
        "activate_ms": act_ms,
        "final_stop": float(stop),
        "ratchet_steps": len(path),
        "path": path,
        "max_4h_close": float(np.nanmax(closes)) if len(closes) else None,
    }

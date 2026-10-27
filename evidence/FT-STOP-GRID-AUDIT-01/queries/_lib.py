# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 公共加载与工具。只读上游。"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
UP = BASE_DIR / "evidence/FT-STOP-GRID-01"
HERE = BASE_DIR / "evidence/FT-STOP-GRID-AUDIT-01"
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

    权益 = 2000 + Σ(close_ms ≤ open_ms 的 profit_abs)。同一时刻多笔按 close_ms 排序。
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
    # fee_open / fee_close 是**费率**（实测恒为 0.0005），不是金额：
    # 开仓费 = 数量 × 开仓价 × 费率；平仓费 = 数量 × 平仓价 × 费率
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


def kline_path(pair: str) -> Path:
    sym = pair.replace("/", "_").replace(":", "_")
    return DATA / f"{sym}-15m-futures.feather"


def load_kline(pair: str) -> pd.DataFrame | None:
    p = kline_path(pair)
    if not p.exists():
        return None
    df = pd.read_feather(p)
    df["ms"] = (df["date"] - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)
    return df

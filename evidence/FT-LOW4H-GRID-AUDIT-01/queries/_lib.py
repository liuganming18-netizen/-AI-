# -*- coding: utf-8 -*-
"""FT-LOW4H-GRID-AUDIT-01 公共加载与工具。只读上游 FT-LOW4H-GRID-01。

本文件是「144 组独立审核」的公共层：
  - 上游发现（目录/策略/结果）做成**容错**的：上游不存在时返回空，不抛异常，
    这样开跑前核对可以在上游尚未产出时先跑，并把结论写成 BLOCKED；
  - 资金费、盯市权益曲线等实现直接复用 FT-STOP-GRID-AUDIT-02 已验证版本
    （2,216 笔逐笔零误差、98,671 个资金事件），不重新发明。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE_DIR / "evidence/FT-LOW4H-GRID-AUDIT-01"
OUT = HERE / "out"

# 本轮审核对象（低点止损 + 立即跟踪，16 种组合 × 9 个参数组）
UP = BASE_DIR / "evidence/FT-LOW4H-GRID-01"
# 上一轮（四档固定止损 + 2R 跟踪），配对与「上一轮原始账本」的唯一来源
PREV = BASE_DIR / "evidence/FT-STOP-GRID-02"
# 上上一轮（四档固定止损，无跟踪），仅用于历史对照，不作为本轮配对基准
PREV0 = BASE_DIR / "evidence/FT-STOP-GRID-01"

DATA = BASE_DIR / "evidence/FT-ENTRY-02/user_data/data/binance/futures"
FT = BASE_DIR / "evidence/FT-BASELINE-01/.venv/lib/python3.11/site-packages/freqtrade"

# 9 个参数组（与 FT-STOP-GRID-01/02、FT-ENTRY-09 同一套 C1 参数网格）
SUFFIXES = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
            "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
            "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
N_SUFFIX = len(SUFFIXES)
EXPECT_COMBOS = 16
EXPECT_GROUPS = EXPECT_COMBOS * N_SUFFIX   # 144

INIT_EQUITY = 2000.0
RISK_RATE = 0.005
FEE = 0.0005
BAR_4H_MS = 4 * 3600 * 1000
BAR_15M_MS = 15 * 60 * 1000
R_UNIT = INIT_EQUITY * RISK_RATE            # 10 USDT = 1R（初始尺度）


# ------------------------------------------------------------------ 上游发现
def up_state() -> dict:
    """上游是否已产出、产出到什么程度。只做文件系统观察，不读结果内容。"""
    st = {
        "上游目录": str(UP),
        "目录存在": UP.exists(),
        "策略目录存在": (UP / "user_data/strategies").exists(),
        "结果目录存在": (UP / "out").exists(),
        "策略文件数": 0,
        "逐笔结果文件数": 0,
        "逐笔结果字节数": 0,
        "报告文件": [],
        "code 脚本": [],
    }
    if st["策略目录存在"]:
        st["策略文件数"] = len(list((UP / "user_data/strategies").glob("*.py")))
    if st["结果目录存在"]:
        fs = sorted((UP / "out").glob("trades_*.csv"))
        st["逐笔结果文件数"] = len(fs)
        st["逐笔结果字节数"] = int(sum(p.stat().st_size for p in fs))
    if UP.exists():
        st["报告文件"] = sorted(p.name for p in UP.glob("*.md"))
        st["code 脚本"] = sorted(p.name for p in (UP / "code").glob("*.py")) if (UP / "code").exists() else []
    st["可用"] = st["逐笔结果文件数"] > 0
    return st


GRID_RE = re.compile(r"^Wyckoff_(?P<mode>[A-Za-z0-9]+)_(?P<combo>.+)_(?P<vs>Vs\d{2})_(?P<z>Z\d{2})\.py$")


def discover_groups() -> tuple[list[str], list[str], list[dict]]:
    """从上游策略文件名发现 (组合列表, 完整策略名列表, 逐文件解析明细)。

    组合名不预设：16 种组合的命名由上游决定，本函数只做结构校验，
    这样「参数映射无错位」是**核出来的**，不是抄来的。
    """
    d = UP / "user_data/strategies"
    if not d.exists():
        return [], [], []
    rows, combos, names = [], set(), []
    for p in sorted(d.glob("*.py")):
        m = GRID_RE.match(p.name)
        if not m:
            continue
        combo = m.group("combo")
        suf = f"{m.group('vs')}_{m.group('z')}"
        names.append(p.stem)
        combos.add(combo)
        rows.append({"文件": p.name, "组合": combo, "参数组": suf, "模式": m.group("mode")})
    return sorted(combos), sorted(names), rows


def load_plan() -> dict:
    p = OUT / "01_冻结分析计划.json"
    if not p.exists():
        raise SystemExit("缺少 out/01_冻结分析计划.json：比较口径必须先冻结再读结果")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ 逐笔加载
def _ms_of_series(s: pd.Series) -> np.ndarray:
    v = (pd.to_datetime(s, utc=True) - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)
    return np.asarray(v, dtype=np.int64)


def event_id(tag: str) -> str | None:
    """从 enter_tag 提取事件 ID（与上一轮同一实现）：cand_C1_ev_<策略>_<币>_<起>_<止> 的后 7 段。"""
    if not isinstance(tag, str) or "cand_C1_" not in tag:
        return None
    return "_".join(tag.split("cand_C1_", 1)[-1].split("_")[:7])


def load_trades(path: Path, name: str | None = None) -> pd.DataFrame | None:
    """None = 文件不存在；空表 = 文件存在但零成交。"""
    if not path.exists():
        return None
    if path.stat().st_size == 0:
        return pd.DataFrame()
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        return pd.DataFrame()
    df["策略"] = name or path.stem.replace("trades_", "")
    df["事件ID"] = df["enter_tag"].map(event_id)
    df["open_ms"] = pd.to_numeric(df["open_timestamp"], errors="coerce").astype("int64")
    df["close_ms"] = pd.to_numeric(df["close_timestamp"], errors="coerce").astype("int64")
    df["持仓小时"] = pd.to_numeric(df["trade_duration"], errors="coerce") / 60.0
    df["是否期末结算"] = df["exit_reason"].astype(str).eq("force_exit")
    df["币"] = df["pair"].astype(str).str.replace("/", "_", regex=False).str.split("_").str[0]
    return df


def load_all_groups(names: list[str]) -> tuple[pd.DataFrame, list[str], list[str]]:
    frames, missing, zero = [], [], []
    for n in names:
        d = load_trades(UP / f"out/trades_{n}.csv", n)
        if d is None:
            missing.append(n)
        elif d.empty:
            zero.append(n)
        else:
            frames.append(d)
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df, missing, zero


# ------------------------------------------------------------ 权益与风险口径
def equity_curve(df: pd.DataFrame, group_col: str = "策略") -> pd.DataFrame:
    """权益 = 2000 + Σ(close_ms ≤ open_ms 的已实现盈亏)；风险预算 = 权益 × 0.5%。"""
    out = []
    for _, g in df.groupby(group_col, sort=False):
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
    amt = pd.to_numeric(res["amount"], errors="coerce")
    op = pd.to_numeric(res["open_rate"], errors="coerce")
    cl = pd.to_numeric(res["close_rate"], errors="coerce")
    res["风险预算USDT"] = res["equity_at_entry"] * RISK_RATE
    res["R"] = res["profit_abs"] / res["风险预算USDT"]
    res["名义USDT"] = amt * op
    res["名义占权益"] = res["名义USDT"] / res["equity_at_entry"]
    # fee_open / fee_close 是**费率**（恒 0.0005），不是金额
    res["手续费USDT"] = amt * op * pd.to_numeric(res["fee_open"], errors="coerce").fillna(0) \
        + amt * cl * pd.to_numeric(res["fee_close"], errors="coerce").fillna(0)
    res["资金费USDT"] = pd.to_numeric(res["funding_fees"], errors="coerce").fillna(0)
    res["价差盈亏USDT"] = (cl - op) * amt
    return res


# ------------------------------------------------------------------ 行情数据
_PX: dict = {}
_FUND: dict = {}
_MARK: dict = {}
_K4H: dict = {}


def _sym(pair: str) -> str:
    return str(pair).replace("/", "_").replace(":", "_")


def price_15m(pair: str):
    if pair in _PX:
        return _PX[pair]
    p = DATA / f"{_sym(pair)}-15m-futures.feather"
    if not p.exists():
        _PX[pair] = None
        return None
    df = pd.read_feather(p, columns=["date", "close"])
    _PX[pair] = (_ms_of_series(df["date"]), df["close"].to_numpy(dtype=np.float64))
    return _PX[pair]


def funding_8h(pair: str):
    if pair in _FUND:
        return _FUND[pair]
    p = DATA / f"{_sym(pair)}-8h-funding_rate.feather"
    if not p.exists():
        _FUND[pair] = None
        return None
    df = pd.read_feather(p, columns=["date", "funding_rate"])
    _FUND[pair] = (_ms_of_series(df["date"]), df["funding_rate"].to_numpy(dtype=np.float64))
    return _FUND[pair]


def mark_1h(pair: str):
    if pair in _MARK:
        return _MARK[pair]
    p = DATA / f"{_sym(pair)}-1h-mark.feather"
    if not p.exists():
        _MARK[pair] = None
        return None
    df = pd.read_feather(p, columns=["date", "close"])
    _MARK[pair] = (_ms_of_series(df["date"]), df["close"].to_numpy(dtype=np.float64))
    return _MARK[pair]


def load_4h(pair: str) -> pd.DataFrame | None:
    """按策略源码方式重建 4h：close_ms = open_ms + 4h；l10 = low.rolling(10).min()。"""
    if pair in _K4H:
        return _K4H[pair]
    p = DATA / f"{_sym(pair)}-4h-futures.feather"
    if not p.exists():
        _K4H[pair] = None
        return None
    df = pd.read_feather(p)
    df["open_ms"] = _ms_of_series(df["date"]).astype("int64")
    df["close_ms"] = df["open_ms"] + BAR_4H_MS
    df["l10"] = df["low"].rolling(10).min()
    out = df[["open_ms", "close_ms", "open", "high", "low", "close", "l10"]].copy()
    _K4H[pair] = out
    return out


def _ffill_at(series, t: np.ndarray) -> np.ndarray:
    """取 t 时刻及之前最近一个已知值（不偷看未来）。"""
    if series is None:
        return np.full(len(t), np.nan)
    ms, v = series
    idx = np.searchsorted(ms, t, side="right") - 1
    ok = idx >= 0
    out = np.full(len(t), np.nan)
    out[ok] = v[idx[ok]]
    return out


def funding_events(pair: str, open_ms: int, close_ms: int, amount: float):
    """资金费事件与逐事件费用。

    口径（FT-STOP-GRID-AUDIT-02 已验证：2,216/2,216 笔逐笔零误差）：
      1) 资金费时间戳带亚秒抖动，先归一整秒；
      2) 窗口 = [开仓, 平仓] 闭区间；
      3) 标记价取事件时刻之前最近一个**非空**值。
    """
    f = funding_8h(pair)
    mk = mark_1h(pair)
    if f is None:
        return np.array([], dtype=np.int64), np.array([]), 0.0
    ms, rate = f
    fs = (ms // 1000) * 1000
    first = int(np.searchsorted(fs, open_ms, side="left"))
    last = int(np.searchsorted(fs, close_ms, side="right"))
    if last <= first:
        return np.array([], dtype=np.int64), np.array([]), 0.0
    ev_ms = fs[first:last]
    ev_rate = rate[first:last]
    if mk is None:
        return ev_ms, np.zeros(len(ev_ms)), 0.0
    mms, mv = mk
    mv = pd.Series(mv).ffill().to_numpy()
    k = np.searchsorted(mms, ms[first:last], side="right") - 1
    mkpx = np.where(k >= 0, mv[np.clip(k, 0, None)], np.nan)
    fee = -(ev_rate * mkpx * amount)
    ok = np.isfinite(fee)
    if not ok.all():
        ev_ms, fee = ev_ms[ok], fee[ok]
    return ev_ms, fee, float(fee.sum())


def max_drawdown_usdt(pnl: np.ndarray) -> float:
    if len(pnl) == 0:
        return 0.0
    cum = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    return float((peak - cum).max())


def tick_of(raw) -> float:
    s = str(raw or "").strip()
    if "." not in s:
        return 1.0
    return 10.0 ** (-len(s.split(".")[1]))

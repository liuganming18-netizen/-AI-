# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-04 公共只读工具。

复用自 evidence/FT-ENTRY-AUDIT-03/queries/_common.py，改动仅三处：
1. 被审对象由 FT-ENTRY-02 改为 FT-ENTRY-03；
2. 去掉 C 路线（本轮工单明确"沿用已有失败归因，不重复分析"）；
3. 额外保留 FT-ENTRY-02 的只读路径，用于「新版基线是否与旧版同一批成交」的跨批比对。

边界：
- 只读 evidence/FT-ENTRY-03 的逐笔 CSV、归档 zip、策略源码与 4h/1h 数据；
- 只在本目录 out/ 与 logs/ 写文件；
- 不调用 freqtrade、不重跑回测、不打开 2026 留出期。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
ENTRY03 = os.path.join(ROOT, "evidence/FT-ENTRY-03")
OUT03 = os.path.join(ENTRY03, "out")
DATA4H = os.path.join(ENTRY03, "user_data/data/binance/futures")
ENTRY02 = os.path.join(ROOT, "evidence/FT-ENTRY-02")   # 仅用于跨批基线比对
OUT02 = os.path.join(ENTRY02, "out")
AUDIT = os.path.join(ROOT, "evidence/FT-ENTRY-AUDIT-04")
OUT = os.path.join(AUDIT, "out")
AUDIT03_OUT = os.path.join(ROOT, "evidence/FT-ENTRY-AUDIT-03/out")  # 仅用于数据指纹交叉比对

# 9 组 A：编号 -> (策略名, Δ_entry 倍数, k_vol 倍数)。与 FT-ENTRY-02 完全同一套网格，未新增参数。
CELLS_A: List[Tuple[str, str, float, float]] = [
    ("A01", "Wyckoff_A_L06_V08", 0.6, 0.8),
    ("A02", "Wyckoff_A_L06_V10", 0.6, 1.0),
    ("A03", "Wyckoff_A_L06_V12", 0.6, 1.2),
    ("A04", "Wyckoff_A_L04_V08", 0.4, 0.8),
    ("A05", "Wyckoff_A_L04_V10", 0.4, 1.0),
    ("A06", "Wyckoff_A_L04_V12", 0.4, 1.2),
    ("A07", "Wyckoff_A_L02_V08", 0.2, 0.8),
    ("A08", "Wyckoff_A_L02_V10", 0.2, 1.0),
    ("A09", "Wyckoff_A_L02_V12", 0.2, 1.2),
]

BASELINE = "WyckoffBaseline"
FEE = 0.0005
START_MS = 1638316800000  # 2021-12-01T00:00:00Z
END_MS = 1735689600000    # 2025-01-01T00:00:00Z

# 本轮从 74 池剔除的 4 个标的（上架过晚，研究期内无法完成 200 根 4h 预热）
DROPPED_FROM_74 = ["AERO", "AIXBT", "AVA", "CGPT"]


# ── 读取 ────────────────────────────────────────────────────────────────
def load_trades(strategy: str, src: str = OUT03) -> pd.DataFrame:
    path = os.path.join(src, f"trades_{strategy}.csv")
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["open_ts"] = df["open_timestamp"].astype("int64")
    df["close_ts"] = df["close_timestamp"].astype("int64")
    df["pair_s"] = df["pair"].str.replace("/", "_", regex=False).str.replace(":", "_", regex=False)
    parsed = df["enter_tag"].map(parse_tag)
    df["tag_prefix"] = [p[0] for p in parsed]
    df["event_id"] = [p[1] for p in parsed]
    df["box_ms"] = [p[2] for p in parsed]
    df["sig_close_ms"] = [p[3] for p in parsed]
    # 毛利（价格口径）与总成本（含手续费+资金费）
    df["gross_pnl"] = df["amount"] * (df["close_rate"] - df["open_rate"])
    df["fee_est"] = df["amount"] * df["open_rate"] * df["fee_open"] + df["amount"] * df["close_rate"] * df["fee_close"]
    df["funding"] = df["funding_fees"].astype(float)
    df["cost_total"] = df["gross_pnl"] - df["profit_abs"]
    df["is_win"] = df["profit_abs"] > 0
    # 已实现权益路径（按平仓时间）与入场时权益（按开仓时点之前已平仓盈亏累计）
    d_close = df.sort_values("close_ts").reset_index(drop=True)
    d_close["equity_after_close"] = 2000.0 + d_close["profit_abs"].cumsum()
    close_ts_arr = d_close["close_ts"].to_numpy()
    eq_arr = d_close["equity_after_close"].to_numpy()
    pos = np.searchsorted(close_ts_arr, df["open_ts"].to_numpy(), side="right")
    eq_entry = np.where(pos > 0, eq_arr[np.clip(pos - 1, 0, len(eq_arr) - 1)], 2000.0)
    df["equity_before"] = eq_entry
    df["stake_pct"] = df["stake_amount"] / df["equity_before"]
    # 止损距离用「反推 P0」而不是 freqtrade 的 -99% 占位止损
    df["r_dist"] = df["equity_before"] * 0.005 * df["open_rate"] / df["stake_amount"]
    df["p0_implied"] = df["open_rate"] - df["r_dist"]
    df["stop_dist_pct"] = df["r_dist"] / df["open_rate"]
    return df.sort_values("open_ts").reset_index(drop=True)


def parse_tag(tag: str) -> Tuple[str, str, int, int]:
    parts = tag.split("_")
    close_ms = int(parts[-1])
    box_ms = int(parts[-2])
    if tag.startswith("cand_A_"):
        prefix = "cand_A_"
    elif tag.startswith("cand_C_"):
        prefix = "cand_C_"
    elif tag.startswith("cand_"):
        prefix = "cand_"
    else:
        raise ValueError(f"未知 enter_tag: {tag}")
    suffix = f"_{close_ms}"
    assert tag.endswith(suffix), tag
    # event_id 保留 4h 建档时刻（状态机口径 ev_{strategy}_{venue}_{symbol}_{box_ms}）
    event_id = tag[len(prefix):-len(suffix)]
    assert event_id.endswith(str(box_ms)), (tag, event_id)
    return prefix, event_id, box_ms, close_ms


# ── 4h 箱体回算（独立于对方快照，直接从 feather 重算 H/L/A） ──────────────
_DF4H_CACHE: Dict[str, pd.DataFrame] = {}


def _wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(highs)
    if n == 0:
        return np.array([], dtype=np.float64)
    tr = np.zeros(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return atr
    atr[period - 1] = float(np.mean(tr[:period]))
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / float(period)
    return atr


def load_4h(pair_s: str) -> Optional[pd.DataFrame]:
    if pair_s in _DF4H_CACHE:
        return _DF4H_CACHE[pair_s]
    path = os.path.join(DATA4H, f"{pair_s}-4h-futures.feather")
    if not os.path.exists(path):
        _DF4H_CACHE[pair_s] = None  # type: ignore[assignment]
        return None
    df = pd.read_feather(path)
    highs = df["high"].to_numpy(np.float64)
    lows = df["low"].to_numpy(np.float64)
    closes = df["close"].to_numpy(np.float64)
    df["atr"] = _wilder_atr(highs, lows, closes, 14)
    df["open_ms"] = pd.to_datetime(df["date"], utc=True).astype("int64")
    df["close_ms"] = df["open_ms"] + 4 * 3600 * 1000
    _DF4H_CACHE[pair_s] = df
    return df


def box_at(pair_s: str, box_ms: int) -> Optional[Dict[str, float]]:
    """按策略口径重算该 4h 收盘时刻的箱体：最近 42 根 4h 的 H/L + 当根 ATR。"""
    df = load_4h(pair_s)
    if df is None:
        return None
    sub = df[df["close_ms"] <= box_ms]
    if len(sub) < 42:
        return None
    last42 = sub.tail(42)
    atr = float(sub["atr"].iloc[-1])
    if not np.isfinite(atr) or atr <= 0:
        return None
    return {
        "H": float(last42["high"].max()),
        "L": float(last42["low"].min()),
        "A": atr,
        "bars_available": int(len(sub)),
        "box_bar_close_ms": int(sub["close_ms"].iloc[-1]),
    }


# ── 指标 ────────────────────────────────────────────────────────────────
def wilson_ci(wins: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def metrics(df: pd.DataFrame) -> Dict[str, float]:
    n = len(df)
    wins = int((df["profit_abs"] > 0).sum())
    flats = int((df["profit_abs"] == 0).sum())
    losses = n - wins - flats
    net = float(df["profit_abs"].sum())
    gross_win = float(df.loc[df["profit_abs"] > 0, "profit_abs"].sum())
    gross_loss = float(-df.loc[df["profit_abs"] < 0, "profit_abs"].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    lo, hi = wilson_ci(wins, n)
    dd = realized_drawdown(df)
    avg_win = float(df.loc[df["profit_abs"] > 0, "profit_abs"].mean()) if wins else float("nan")
    avg_loss = float(df.loc[df["profit_abs"] < 0, "profit_abs"].mean()) if losses else float("nan")
    return {
        "trades": n,
        "wins": wins,
        "flats": flats,
        "losses": losses,
        "win_rate": wins / n if n else float("nan"),
        "wilson_lo": lo,
        "wilson_hi": hi,
        "net_pnl": net,
        "expectancy": net / n if n else float("nan"),
        "profit_factor": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / abs(avg_loss)) if losses and np.isfinite(avg_loss) and avg_loss != 0 else float("nan"),
        "gross_pnl": float(df["gross_pnl"].sum()),
        "fee_total": float(df["fee_est"].sum()),
        "funding_total": float(df["funding"].sum()),
        "max_dd_realized": dd,
        "avg_stake_pct": float(df["stake_pct"].mean()) if n else float("nan"),
        "avg_stop_dist_pct": float(df["stop_dist_pct"].mean()) if n else float("nan"),
        "avg_hold_h": float(df["trade_duration"].mean() / 60.0) if n else float("nan"),
        "unique_events": int(df["event_id"].nunique()),
        "coins": int(df["pair"].nunique()),
    }


def realized_drawdown(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    d = df.sort_values("close_ts")
    eq = 2000.0 + d["profit_abs"].cumsum().to_numpy()
    peak = np.maximum.accumulate(np.concatenate([[2000.0], eq]))
    dd = peak[1:] - eq
    return float(dd.max())


def block_index(ts_ms: int, block_days: int = 30) -> int:
    return int((ts_ms - START_MS) // (block_days * 86400 * 1000))


def block_series(df: pd.DataFrame, n_blocks: int, block_days: int = 30) -> np.ndarray:
    out = np.zeros(n_blocks, dtype=np.float64)
    if df.empty:
        return out
    idx = ((df["close_ts"].to_numpy() - START_MS) // (block_days * 86400 * 1000)).astype(int)
    vals = df["profit_abs"].to_numpy(np.float64)
    for i, v in zip(idx, vals):
        if 0 <= i < n_blocks:
            out[i] += v
    return out


def n_blocks(block_days: int = 30) -> int:
    return int(np.ceil((END_MS - START_MS) / (block_days * 86400 * 1000)))


def paired_bootstrap(diff: np.ndarray, reps: int = 5000, seed: int = 20261020) -> Tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    n = len(diff)
    idx = rng.integers(0, n, size=(reps, n))
    sums = diff[idx].sum(axis=1)
    return float(np.percentile(sums, 2.5)), float(np.percentile(sums, 97.5)), float(sums.std(ddof=1))


def block_permutation_p(diff: np.ndarray, reps: int = 5000, seed: int = 20261020) -> float:
    """块级符号翻转置换：零假设下块内两组可交换。"""
    rng = np.random.default_rng(seed)
    obs = abs(diff.sum())
    signs = rng.choice([-1.0, 1.0], size=(reps, len(diff)))
    perm = np.abs((signs * diff).sum(axis=1))
    return float((perm >= obs).sum() / reps)


def holm(pvals: List[float]) -> List[float]:
    m = len(pvals)
    order = np.argsort(pvals)
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        val = min(1.0, (m - rank) * pvals[i])
        running = max(running, val)
        adj[i] = running
    return adj


def jdump(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


def write_csv(df: pd.DataFrame, name: str) -> str:
    path = os.path.join(OUT, name)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path

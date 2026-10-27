# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-08 公共只读工具（接续企稳确认审核）。

只读上游：
- evidence/FT-ENTRY-07/            被审交付（27 组逐笔、确认/下单审计、更正后汇总）
- evidence/FT-ENTRY-AUDIT-07/      已完成的独立重算、规则核对、指纹锁定
- evidence/FT-ENTRY-02/            C0 基线原始逐笔（FT-07 的 C0 与其逐字节同一）
- evidence/FT-ENTRY-03/            输入资格与样本分层（4h 200 预热口径来源）
- evidence/FT-ENTRY-AUDIT-02/out/02_币池与数据覆盖_v1.csv  74 币逐币数据区间

边界：
- 只在本目录 out/ 与 logs/ 写文件；
- 不调用 freqtrade、不重跑回测、不改策略、不新增参数、不打开 2025+ 留出期；
- 4h 箱体 H/L/A 仅按已锁定 4h 数据与冻结规则**重算核对**（用于入场下限反事实），
  不重跑信号/撮合/账户；H 与审计账本 frozen_H 逐笔对拍作为正确性证明。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
ENTRY02 = os.path.join(ROOT, "evidence/FT-ENTRY-02")
ENTRY03 = os.path.join(ROOT, "evidence/FT-ENTRY-03")
ENTRY07 = os.path.join(ROOT, "evidence/FT-ENTRY-07")
AUDIT07 = os.path.join(ROOT, "evidence/FT-ENTRY-AUDIT-07")
AUDIT02 = os.path.join(ROOT, "evidence/FT-ENTRY-AUDIT-02")
OUT02 = os.path.join(ENTRY02, "out")
OUT07 = os.path.join(ENTRY07, "out")
LOGS07 = os.path.join(ENTRY07, "logs")
AUDIT = os.path.join(ROOT, "evidence/FT-ENTRY-AUDIT-08")
OUT = os.path.join(AUDIT, "out")
LOGS = os.path.join(AUDIT, "logs")
DATA = os.path.join(ENTRY07, "user_data/data/binance/futures")

# 9 组 C 网格：grid -> (C0 策略名, C1 策略名, C2 策略名, Vs, Z)
GRIDS: List[Tuple[str, str, str, str, float, float]] = [
    ("Vs04_Z02", "Wyckoff_C_Vs04_Z02", "Wyckoff_C1_Vs04_Z02", "Wyckoff_C2_Vs04_Z02", 0.4, 0.2),
    ("Vs04_Z04", "Wyckoff_C_Vs04_Z04", "Wyckoff_C1_Vs04_Z04", "Wyckoff_C2_Vs04_Z04", 0.4, 0.4),
    ("Vs04_Z06", "Wyckoff_C_Vs04_Z06", "Wyckoff_C1_Vs04_Z06", "Wyckoff_C2_Vs04_Z06", 0.4, 0.6),
    ("Vs06_Z02", "Wyckoff_C_Vs06_Z02", "Wyckoff_C1_Vs06_Z02", "Wyckoff_C2_Vs06_Z02", 0.6, 0.2),
    ("Vs06_Z04", "Wyckoff_C_Vs06_Z04", "Wyckoff_C1_Vs06_Z04", "Wyckoff_C2_Vs06_Z04", 0.6, 0.4),
    ("Vs06_Z06", "Wyckoff_C_Vs06_Z06", "Wyckoff_C1_Vs06_Z06", "Wyckoff_C2_Vs06_Z06", 0.6, 0.6),
    ("Vs08_Z02", "Wyckoff_C_Vs08_Z02", "Wyckoff_C1_Vs08_Z02", "Wyckoff_C2_Vs08_Z02", 0.8, 0.2),
    ("Vs08_Z04", "Wyckoff_C_Vs08_Z04", "Wyckoff_C1_Vs08_Z04", "Wyckoff_C2_Vs08_Z04", 0.8, 0.4),
    ("Vs08_Z06", "Wyckoff_C_Vs08_Z06", "Wyckoff_C1_Vs08_Z06", "Wyckoff_C2_Vs08_Z06", 0.8, 0.6),
]

START_MS = 1638316800000  # 2021-12-01T00:00:00Z
END_MS = 1735689600000    # 2025-01-01T00:00:00Z
BLOCK_DAYS = 30
REPS = 5000
SEED = 20261020
BAR_MS = 3600 * 1000
B4_MS = 4 * 3600 * 1000
M15_MS = 15 * 60 * 1000
BOX_BARS = 42
ATR_PERIOD = 14
RECOVERY_ATR = 0.1
STOP_BUFFER_ATR = 0.1
ENTRY_LOW_OLD_ATR = 0.2   # C0 / 旧 C1/C2 下限：L - 0.2A
RETEST_ZONE_DEFAULT = 0.2  # 仅用于缺档说明，不参与判定


# ── IO ────────────────────────────────────────────────────────────────
def sha256_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(df: pd.DataFrame, name: str) -> str:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def jdump(path: str, obj) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return path


# ── 逐笔与审计读取 ────────────────────────────────────────────────────
def grid_of(path: str, mode: str) -> str:
    b = os.path.basename(path)
    pre = f"trades_Wyckoff_{mode}_"
    for prefix in ("trades_Wyckoff_C1_", "trades_Wyckoff_C2_", "trades_Wyckoff_C_",
                   "audit_orders_Wyckoff_C1_", "audit_orders_Wyckoff_C2_",
                   "audit_confirmation_Wyckoff_C1_", "audit_confirmation_Wyckoff_C2_"):
        if b.startswith(prefix):
            return b[len(prefix):-4]
    return b


def _event_from_tag(tag: str) -> Tuple[str, int]:
    """enter_tag -> (纯事件键 event_id, 该 tag 的尾随毫秒)。

    三种模式的 tag 分别以 cand_C_ / cand_C1_ / cand_C2_ 开头，必须去掉候选前缀，
    否则同一物理事件会被当成三个不同事件（本轮已踩坑修复）。
    """
    import re
    s = str(tag)
    tail = int(s.rsplit("_", 1)[1]) if s.rsplit("_", 1)[-1].isdigit() else 0
    m = re.search(r"(ev_[A-Za-z0-9_]+?_\d+)_\d+$", s)
    if m:
        return m.group(1), tail
    return s, tail


def load_trades(mode: str) -> pd.DataFrame:
    """mode in {'C','C1','C2'}；返回 9 网格合并逐笔，含事件键与修正口径快速止损。"""
    rows = []
    for g, s0, s1, s2, _vs, _z in GRIDS:
        strat = {"C": s0, "C1": s1, "C2": s2}[mode]
        p = os.path.join(OUT07, f"trades_{strat}.csv")
        if not os.path.isfile(p):
            continue
        d = pd.read_csv(p, encoding="utf-8-sig")
        if d.empty:
            continue
        d["grid"] = g
        d["strategy"] = strat
        d["mode"] = mode
        rows.append(d)
    if not rows:
        return pd.DataFrame()
    d = pd.concat(rows, ignore_index=True)
    ev, tail = [], []
    for t in d["enter_tag"]:
        e, s = _event_from_tag(t)
        ev.append(e)
        tail.append(s)
    d["event_id"] = ev
    d["tag_tail_ms"] = tail
    d["symbol"] = d["pair"].str.split("/").str[0]
    d["event_key"] = d["symbol"] + "|" + d["event_id"]
    # 原始信号 1h 收盘：C0 的 tag 尾巴即信号柱收盘；C1/C2 尾巴是确认柱收盘，减 1h
    d["sig_ms"] = np.where(d["mode"] == "C", d["tag_tail_ms"], d["tag_tail_ms"] - BAR_MS)
    # 修正口径：MFE=(max_rate-open_rate)/open_rate < 3%，持仓<=720min，净亏损
    d["mfe_pct"] = (d["max_rate"] - d["open_rate"]) / d["open_rate"]
    d["quick_stop"] = ((d["trade_duration"] <= 720) & (d["profit_abs"] < 0)
                       & (d["mfe_pct"] < 0.03))
    d["is_win"] = d["profit_abs"] > 0
    return d


def load_confirmation(mode: str) -> pd.DataFrame:
    assert mode in ("C1", "C2")
    rows = []
    for g, _s0, s1, s2, _vs, _z in GRIDS:
        strat = s1 if mode == "C1" else s2
        p = os.path.join(OUT07, f"audit_confirmation_{strat}.csv")
        if not os.path.isfile(p):
            continue
        d = pd.read_csv(p, encoding="utf-8-sig")
        if d.empty:
            continue
        d["grid"] = g
        rows.append(d)
    d = pd.concat(rows, ignore_index=True)
    d["cand_id"] = ("cand_" + mode + "_" + d["event_id"] + "_"
                    + d["confirm_time_ms"].astype("int64").astype(str))
    d["cand_key"] = d["grid"] + "|" + d["cand_id"]
    return d


def load_orders(mode: str) -> pd.DataFrame:
    assert mode in ("C1", "C2")
    rows = []
    for g, _s0, s1, s2, _vs, _z in GRIDS:
        strat = s1 if mode == "C1" else s2
        p = os.path.join(OUT07, f"audit_orders_{strat}.csv")
        if not os.path.isfile(p):
            continue
        d = pd.read_csv(p, encoding="utf-8-sig")
        if d.empty:
            continue
        d["grid"] = g
        rows.append(d)
    d = pd.concat(rows, ignore_index=True)
    d["cand_key"] = d["grid"] + "|" + d["candidate_id"]
    return d


# ── 4h 箱体重算（冻结规则，仅几何） ───────────────────────────────────
_4H_CACHE: Dict[str, pd.DataFrame] = {}


def _load_4h(symbol: str) -> Optional[pd.DataFrame]:
    if symbol in _4H_CACHE:
        return _4H_CACHE[symbol]
    p = os.path.join(DATA, f"{symbol}_USDT_USDT-4h-futures.feather")
    if not os.path.isfile(p):
        _4H_CACHE[symbol] = None
        return None
    d = pd.read_feather(p)[["date", "high", "low", "close"]].copy()
    d["close_ms"] = d["date"].astype("int64") + B4_MS
    _4H_CACHE[symbol] = d
    return d


def wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
               period: int = ATR_PERIOD) -> np.ndarray:
    n = len(highs)
    if n == 0:
        return np.array([], dtype=np.float64)
    tr = np.zeros(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    atr = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return atr
    atr[period - 1] = float(np.mean(tr[:period]))
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / float(period)
    return atr


def box_for_event(symbol: str, box_ms: int) -> Optional[dict]:
    """按 event_id 尾部 4h 收盘时刻重算 42 根箱体 H/L 与 ATR(14)。

    与 FT-ENTRY-07 基类一致：df_4h close_ms<=bar_close，取 tail(42)；
    ATR 取该 4h 收盘 bar 的 Wilder ATR(14)。
    """
    d = _load_4h(symbol)
    if d is None:
        return None
    sub = d[d["close_ms"] <= int(box_ms)]
    if len(sub) < BOX_BARS:
        return None
    last = sub.tail(BOX_BARS)
    atr = wilder_atr(sub["high"].to_numpy(np.float64),
                     sub["low"].to_numpy(np.float64),
                     sub["close"].to_numpy(np.float64), ATR_PERIOD)
    a = float(atr[-1])
    if not np.isfinite(a) or a <= 0:
        return None
    h = float(last["high"].max())
    low = float(last["low"].min())
    er = None
    c = last["close"].to_numpy(np.float64)
    if len(c) > 1:
        denom = float(np.abs(np.diff(c)).sum())
        er = float(abs(c[-1] - c[0]) / denom) if denom > 0 else None
    return {"symbol": symbol, "box_ms": int(box_ms), "H": h, "L": low, "A": a,
            "width_atr": (h - low) / a, "ER": er,
            "n_4h": int(len(sub)), "atr_ok": bool(np.isfinite(a) and a > 0)}


# ── 15m 执行窗口 ──────────────────────────────────────────────────────
_M15_CACHE: Dict[str, pd.DataFrame] = {}


def _load_15m(symbol: str) -> Optional[pd.DataFrame]:
    if symbol in _M15_CACHE:
        return _M15_CACHE[symbol]
    p = os.path.join(DATA, f"{symbol}_USDT_USDT-15m-futures.feather")
    if not os.path.isfile(p):
        _M15_CACHE[symbol] = None
        return None
    d = pd.read_feather(p)[["date", "open", "close"]].copy()
    d["open_ms"] = d["date"].astype("int64")
    _M15_CACHE[symbol] = d
    return d


def exec_bars(symbol: str, confirm_ms: int) -> List[dict]:
    """确认柱收盘后 4 根 15m（候选有效窗口 [T, T+60m)）及其开仓成交价。

    信号 bar 的 open 为门槛价（populate_entry_trend 用 open）；信号 bar 之后
    第 1 根 15m 的 open 为实际成交价（T7 已证实际成交=确认收盘+15m）。
    """
    d = _load_15m(symbol)
    if d is None:
        return []
    sub = d[(d["open_ms"] >= int(confirm_ms)) & (d["open_ms"] < int(confirm_ms) + 4 * M15_MS)]
    out = []
    for r in sub.itertuples():
        nxt = d[d["open_ms"] == r.open_ms + M15_MS]
        out.append({"gate_ms": int(r.open_ms), "gate_open": float(r.open),
                    "fill_price": float(nxt["open"].iloc[0]) if len(nxt) else None})
    return out


# ── 块级统计（与 AUDIT-04/05/06/07 完全一致的常数与方法） ──────────────
def n_blocks(block_days: int = BLOCK_DAYS) -> int:
    return int(np.ceil(((END_MS - START_MS) / 86400000.0) / block_days))


def block_index(ts_ms: int, block_days: int = BLOCK_DAYS) -> int:
    return int((int(ts_ms) - START_MS) // (block_days * 86400000))


def block_sums(values: np.ndarray, block_ids: np.ndarray, nb: int) -> np.ndarray:
    out = np.zeros(nb, dtype=float)
    for v, b in zip(values, block_ids):
        if 0 <= b < nb:
            out[b] += float(v)
    return out


def block_bootstrap_mean(values: np.ndarray, block_ids: np.ndarray, nb: int,
                         reps: int = REPS, seed: int = SEED):
    """按 30 天块重采样，返回事件级均值的 95% 百分位 CI（按块内事件数加权）。"""
    rng = np.random.default_rng(seed)
    cnt = np.array([(block_ids == b).sum() for b in range(nb)], dtype=float)
    sm = np.array([values[block_ids == b].sum() for b in range(nb)], dtype=float)
    nonempty = np.where(cnt > 0)[0]
    if len(nonempty) == 0:
        return (None, None)
    pick = rng.choice(nonempty, size=(reps, len(nonempty)), replace=True)
    means = sm[pick].sum(axis=1) / cnt[pick].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def block_permutation_p(values: np.ndarray, block_ids: np.ndarray, nb: int,
                        reps: int = REPS, seed: int = SEED) -> float:
    """块级符号翻转置换：观测 |均值| 对比置换分布。"""
    rng = np.random.default_rng(seed)
    sums = block_sums(values, block_ids, nb)
    n = len(values)
    obs = abs(float(sums.sum()) / n)
    hits = 0
    for _ in range(reps):
        signs = rng.choice([-1.0, 1.0], size=nb)
        perm = abs(float((sums * signs).sum()) / n)
        if perm >= obs - 1e-15:
            hits += 1
    return (hits + 1) / (reps + 1)


def holm(pvals: List[float]) -> List[float]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    out = [0.0] * m
    prev = 0.0
    for rank, i in enumerate(order):
        val = min(1.0, (m - rank) * pvals[i])
        val = max(prev, val)
        out[i] = val
        prev = val
    return out


def maxt_fwer(series_list: List[np.ndarray], block_ids: np.ndarray, nb: int,
              reps: int = REPS, seed: int = SEED) -> Tuple[float, float]:
    """单步 maxT：同一组块符号同时施加到全部比较格，控制族错误率（均值口径）。"""
    mat = np.vstack([block_sums(s, block_ids, nb) for s in series_list])
    ns = np.array([len(s) for s in series_list], dtype=float)
    obs = float(np.abs(mat.sum(axis=1) / ns).max())
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(reps):
        signs = rng.choice([-1.0, 1.0], size=nb)
        perm = float(np.abs((mat * signs).sum(axis=1) / ns).max())
        if perm >= obs - 1e-15:
            hits += 1
    return (hits + 1) / (reps + 1), obs


def wilson_ci(k: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(100 * (c - h), 4), round(100 * (c + h), 4))


# ── 事件级面板 ────────────────────────────────────────────────────────
def event_panel(mode: str, grid: Optional[str] = None) -> pd.DataFrame:
    """事件级面板：每物理候选一行，PnL 为同事件同网格多笔成交之和。"""
    d = load_trades(mode)
    if grid is not None:
        d = d[d["grid"] == grid]
    if d.empty:
        return pd.DataFrame(columns=["grid", "event_key", "pnl", "win", "qs",
                                     "n_fills", "sig_ms", "symbol"])
    g = d.groupby(["grid", "event_key"], as_index=False).agg(
        pnl=("profit_abs", "sum"),
        n_fills=("profit_abs", "size"),
        qs=("quick_stop", "max"),
        sig_ms=("sig_ms", "min"),
        symbol=("symbol", "first"),
        pair=("pair", "first"),
    )
    g["win"] = (g["pnl"] > 0).astype(int)
    g["qs"] = g["qs"].astype(int)
    return g

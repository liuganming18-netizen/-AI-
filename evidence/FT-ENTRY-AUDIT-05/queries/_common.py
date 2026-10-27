# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-05 公共只读工具。

被审对象：evidence/FT-ENTRY-04/（新增「A 路线成交价严格低于冻结 H」约束）
旧对照：evidence/FT-ENTRY-03/（无该约束）

与 FT-ENTRY-AUDIT-04/queries/_common.py 的差异：
1. 新增 FT-ENTRY-04 的加载与指纹锁定；
2. 统计方法（30 天块、配对 Bootstrap、块级符号翻转置换、Holm、maxT）
   与 AUDIT-04/queries/04_stats_A.py 完全一致，不按结果更换检验；
3. 新增「成交约束」专属的拒单归因与仓位竞争双向检查。

边界：
- 只读 evidence/FT-ENTRY-03、evidence/FT-ENTRY-04、evidence/FT-ENTRY-AUDIT-04 的产物；
- 只在本目录 out/ 与 logs/ 写文件；
- 不调用 freqtrade、不重跑回测、不修改上游任何文件、不打开 2026 留出期。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
ENTRY03 = os.path.join(ROOT, "evidence/FT-ENTRY-03")
ENTRY04 = os.path.join(ROOT, "evidence/FT-ENTRY-04")
OUT03 = os.path.join(ENTRY03, "out")
OUT04 = os.path.join(ENTRY04, "out")
AUDIT = os.path.join(ROOT, "evidence/FT-ENTRY-AUDIT-05")
OUT = os.path.join(AUDIT, "out")
AUDIT04_OUT = os.path.join(ROOT, "evidence/FT-ENTRY-AUDIT-04/out")

# 9 组 A：编号 -> (策略名, Δ_entry 倍数, k_vol 倍数)。与 FT-ENTRY-03 完全同一套网格。
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
BLOCK_DAYS = 30
REPS = 5000
SEED = 20261020


# ── 通用 IO ────────────────────────────────────────────────────────────
def sha256_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(df: pd.DataFrame, name: str) -> str:
    path = os.path.join(OUT, name)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def jdump(path: str, obj) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return path


def parse_tag(tag: str):
    """enter_tag -> (prefix, event_id, box_ms, sig_close_ms)

    enter_tag 尾部带 1h 收盘毫秒，事件键必须只留 ev_..._{box_ms}（见 AUDIT-03 口径）。
    """
    s = str(tag)
    parts = s.split("_")
    if len(parts) < 4:
        return ("", s, None, None)
    prefix = parts[0]
    box_ms = None
    sig_ms = None
    ev = ""
    for p in parts[1:]:
        if p.isdigit() and len(p) >= 12:
            if box_ms is None:
                box_ms = int(p)
            else:
                sig_ms = int(p)
        else:
            ev += ("_" if ev else "") + p
    return (prefix, ev, box_ms, sig_ms)


# ── 逐笔读取 ───────────────────────────────────────────────────────────
def load_trades(strategy: str, src: str = OUT03) -> pd.DataFrame:
    path = os.path.join(src, f"trades_{strategy}.csv")
    if not os.path.isfile(path):
        return pd.DataFrame()
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        return df
    df["open_ts"] = df["open_timestamp"].astype("int64")
    df["close_ts"] = df["close_timestamp"].astype("int64")
    df["pair_s"] = df["pair"].str.replace("/", "_", regex=False).str.replace(":", "_", regex=False)
    parsed = df["enter_tag"].map(parse_tag)
    df["tag_prefix"] = [p[0] for p in parsed]
    df["event_id"] = [p[1] for p in parsed]
    df["box_ms"] = [p[2] for p in parsed]
    df["sig_close_ms"] = [p[3] for p in parsed]
    df["gross_pnl"] = df["amount"] * (df["close_rate"] - df["open_rate"])
    df["fee_est"] = (df["amount"] * df["open_rate"] * df["fee_open"]
                     + df["amount"] * df["close_rate"] * df["fee_close"])
    df["funding"] = df["funding_fees"].astype(float)
    df["cost_total"] = df["gross_pnl"] - df["profit_abs"]
    df["is_win"] = df["profit_abs"] > 0
    return df


def add_equity(df: pd.DataFrame, wallet: float = 2000.0) -> pd.DataFrame:
    """已实现权益路径：按平仓时间累计已实现盈亏取峰值回撤（真实平仓权益口径）。

    注意：不含浮亏，是回撤下界（沿用 AUDIT-03/04 口径）。
    """
    if df.empty:
        return df
    d = df.sort_values("close_ts").reset_index(drop=True).copy()
    d["equity_after_close"] = wallet + d["profit_abs"].cumsum()
    d["peak"] = d["equity_after_close"].cummax()
    d["dd_abs"] = d["peak"] - d["equity_after_close"]
    df["max_dd_realized"] = float(d["dd_abs"].max())
    df["final_equity"] = float(d["equity_after_close"].iloc[-1])
    return df


def metrics(df: pd.DataFrame) -> Dict:
    """独立重算的绩效指标集合。缺项用 None，不用 0 冒充。"""
    if df is None or df.empty:
        return {
            "trades": 0, "wins": None, "losses": None, "flat": None,
            "win_rate_pct": None, "avg_win_u": None, "avg_loss_u": None,
            "payoff_ratio": None, "expectancy_u": None, "net_pnl_u": None,
            "profit_factor": None, "max_dd_realized": None, "final_equity": None,
            "gross_pnl_u": None, "cost_total_u": None, "fee_u": None, "funding_u": None,
        }
    p = df["profit_abs"].to_numpy(dtype=float)
    wins_arr = p[p > 0]
    losses_arr = p[p < 0]
    n = len(p)
    wins = int(len(wins_arr))
    losses = int(len(losses_arr))
    flat = int((p == 0).sum())
    avg_win = float(wins_arr.mean()) if wins else None
    avg_loss = float(losses_arr.mean()) if losses else None
    gross_win = float(wins_arr.sum()) if wins else 0.0
    gross_loss = float(-losses_arr.sum()) if losses else 0.0
    payoff = (avg_win / abs(avg_loss)) if (avg_win is not None and avg_loss not in (None, 0.0)) else None
    pf = (gross_win / gross_loss) if gross_loss > 1e-12 else None
    d2 = add_equity(df)
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate_pct": round(100.0 * wins / n, 4) if n else None,
        "avg_win_u": round(avg_win, 4) if avg_win is not None else None,
        "avg_loss_u": round(avg_loss, 4) if avg_loss is not None else None,
        "payoff_ratio": round(payoff, 4) if payoff is not None else None,
        "expectancy_u": round(float(p.mean()), 4) if n else None,
        "net_pnl_u": round(float(p.sum()), 4) if n else None,
        "profit_factor": round(pf, 4) if pf is not None else None,
        "max_dd_realized": round(d2["max_dd_realized"], 4) if n else None,
        "final_equity": round(d2["final_equity"], 4) if n else None,
        "gross_pnl_u": round(float(df["gross_pnl"].sum()), 4) if n else None,
        "cost_total_u": round(float(df["cost_total"].sum()), 4) if n else None,
        "fee_u": round(float(df["fee_est"].sum()), 4) if n else None,
        "funding_u": round(float(df["funding"].sum()), 4) if n else None,
    }


# ── 块级统计（与 AUDIT-04/04_stats_A.py 完全一致）────────────────────
def n_blocks(block_days: int = BLOCK_DAYS) -> int:
    total_days = (END_MS - START_MS) / 86400000.0
    return int(np.ceil(total_days / block_days))


def block_series(df: pd.DataFrame, nb: int, block_days: int = BLOCK_DAYS) -> np.ndarray:
    """按平仓时间把已实现盈亏聚合到 30 天块。"""
    out = np.zeros(nb, dtype=float)
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        idx = int((int(r["close_ts"]) - START_MS) // (block_days * 86400000))
        if 0 <= idx < nb:
            out[idx] += float(r["profit_abs"])
    return out


def paired_bootstrap(d: np.ndarray, reps: int = REPS, seed: int = SEED):
    rng = np.random.default_rng(seed)
    n = len(d)
    idx = rng.integers(0, n, size=(reps, n))
    means = d[idx].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi), float(d.std(ddof=1))


def block_permutation_p(d: np.ndarray, reps: int = REPS, seed: int = SEED) -> float:
    rng = np.random.default_rng(seed)
    obs = abs(float(d.sum()))
    n = len(d)
    hits = 0
    for _ in range(reps):
        signs = rng.choice([-1.0, 1.0], size=n)
        if abs(float((d * signs).sum())) >= obs:
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


def maxt_fwer(diffs: Dict[str, np.ndarray], reps: int = REPS, seed: int = SEED) -> Tuple[float, float]:
    """单步 maxT：同一组块符号同时施加到全部比较格，控制族错误率。"""
    mat = np.vstack([diffs[c] for c in diffs])
    obs_max = float(np.abs(mat.sum(axis=1)).max())
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(reps):
        signs = rng.choice([-1.0, 1.0], size=mat.shape[1])
        perm = np.abs((mat * signs).sum(axis=1)).max()
        if perm >= obs_max:
            hits += 1
    return (hits + 1) / (reps + 1), obs_max


def wilson_ci(k: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(100 * (c - h), 4), round(100 * (c + h), 4))

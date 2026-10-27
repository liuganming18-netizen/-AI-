# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-07 公共只读工具（企稳确认效果独立审核）。

被审/锁定对象：
- evidence/FT-ENTRY-02/   C 路线 9 组有限阈值探索（共同样本与 C0 基线来源）
- evidence/FT-ENTRY-03/   旧基类逐笔（A 路线无约束对照）
- evidence/FT-ENTRY-04/   fill<H 约束逐笔 + audit_orders_*.csv 账本
- evidence/FT-ENTRY-05/   反重力拒单归因（其 ΔPnL 口径待闭合核对）
- evidence/FT-ENTRY-06/   反重力 C 路线结构归因（本轮三入口假设来源，尚未给三入口结果）

待接收对象：反重力「企稳确认」三入口新交付（C0/C1/C2 逐笔与代码），
到达前凡依赖新结果的核对项一律标「等待」，不拿旧统计代替。

边界：
- 只读上游 evidence/FT-ENTRY-02..06 与本轮依赖的 AUDIT-04/05/06 产物；
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
ENTRY02 = os.path.join(ROOT, "evidence/FT-ENTRY-02")
ENTRY03 = os.path.join(ROOT, "evidence/FT-ENTRY-03")
ENTRY04 = os.path.join(ROOT, "evidence/FT-ENTRY-04")
ENTRY05 = os.path.join(ROOT, "evidence/FT-ENTRY-05")
ENTRY06 = os.path.join(ROOT, "evidence/FT-ENTRY-06")
OUT02 = os.path.join(ENTRY02, "out")
OUT03 = os.path.join(ENTRY03, "out")
OUT04 = os.path.join(ENTRY04, "out")
AUDIT06 = os.path.join(ROOT, "evidence/FT-ENTRY-AUDIT-06")
AUDIT = os.path.join(ROOT, "evidence/FT-ENTRY-AUDIT-07")
OUT = os.path.join(AUDIT, "out")
LOGS = os.path.join(AUDIT, "logs")

# 9 组 A 网格（与 FT-ENTRY-03/04 同一套）。编号 -> (策略名, Δ_entry 倍数, k_vol 倍数)
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

# 9 组 C 网格（V_ratio × Zone），共同样本网格
CELLS_C: List[Tuple[str, str, float, float]] = [
    ("C-Vs04-Z02", "Wyckoff_C_Vs04_Z02", 0.4, 0.2),
    ("C-Vs04-Z04", "Wyckoff_C_Vs04_Z04", 0.4, 0.4),
    ("C-Vs04-Z06", "Wyckoff_C_Vs04_Z06", 0.4, 0.6),
    ("C-Vs06-Z02", "Wyckoff_C_Vs06_Z02", 0.6, 0.2),
    ("C-Vs06-Z04", "Wyckoff_C_Vs06_Z04", 0.6, 0.4),
    ("C-Vs06-Z06", "Wyckoff_C_Vs06_Z06", 0.6, 0.6),
    ("C-Vs08-Z02", "Wyckoff_C_Vs08_Z02", 0.8, 0.2),
    ("C-Vs08-Z04", "Wyckoff_C_Vs08_Z04", 0.8, 0.4),
    ("C-Vs08-Z06", "Wyckoff_C_Vs08_Z06", 0.8, 0.6),
]

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


def norm(s: str) -> str:
    return str(s).replace("/", "").replace(":", "").strip().upper()


def parse_tag(tag: str):
    """enter_tag -> (prefix, event_id, box_ms, sig_close_ms)

    enter_tag 尾部带 1h 收盘毫秒，事件键必须只留 ev_..._{box_ms}（AUDIT-03 口径）。
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


def cand_id_to_key(strat: str, pair: str, cand_id: str) -> Tuple[str, str, int]:
    """candidate_id（= enter_tag）-> (strategy, norm_pair, box_ms) 匹配主键。"""
    _, _, box_ms, _ = parse_tag(cand_id)
    return (strat, norm(pair), int(box_ms))


# ── C 路线共同样本（FT-ENTRY-02 九组）──────────────────────────────────
def load_c_trades() -> pd.DataFrame:
    """读取 FT-ENTRY-02 C 路线 9 组逐笔，附加 strategy 与事件键。"""
    rows = []
    for _code, strat, _vr, _z in CELLS_C:
        p = os.path.join(OUT02, f"trades_{strat}.csv")
        if not os.path.isfile(p):
            continue
        df = pd.read_csv(p, encoding="utf-8-sig")
        if df.empty:
            continue
        df["strategy"] = strat
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    allt = pd.concat(rows, ignore_index=True)
    parsed = allt["enter_tag"].map(parse_tag)
    allt["event_id"] = [p[1] for p in parsed]
    allt["box_ms"] = [p[2] for p in parsed]
    allt["sig_close_ms"] = [p[3] for p in parsed]
    pair_n = allt["pair"].map(lambda x: str(x).split("/")[0])
    allt["symbol"] = pair_n
    # 物理候选键：不带网格/信号毫秒（AUDIT-03 事件口径）
    allt["cand_key"] = [
        f"{sy}|{ev}|{bm}" for sy, ev, bm in zip(pair_n, allt["event_id"], allt["box_ms"])
    ]
    return allt


def load_a_trades(src: str = OUT03) -> pd.DataFrame:
    """读取 9 组 A 路线逐笔（src=OUT03/OUT04），附加事件键。"""
    rows = []
    for _code, strat, _d, _k in CELLS_A:
        p = os.path.join(src, f"trades_{strat}.csv")
        if not os.path.isfile(p):
            continue
        df = pd.read_csv(p, encoding="utf-8-sig")
        if df.empty:
            continue
        df["strategy"] = strat
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    allt = pd.concat(rows, ignore_index=True)
    parsed = allt["enter_tag"].map(parse_tag)
    allt["event_id"] = [p[1] for p in parsed]
    allt["box_ms"] = [p[2] for p in parsed]
    allt["sig_close_ms"] = [p[3] for p in parsed]
    allt["key"] = [cand_id_to_key(s, p, t) for s, p, t in
                   zip(allt["strategy"], allt["pair"], allt["enter_tag"])]
    return allt


def load_audit_orders04() -> pd.DataFrame:
    """读取 FT-ENTRY-04 运行期自写 audit_orders_*.csv（A 路线 9 组合计）。"""
    rows = []
    for _code, strat, _d, _k in CELLS_A:
        p = os.path.join(OUT04, f"audit_orders_{strat}.csv")
        if not os.path.isfile(p):
            continue
        aud = pd.read_csv(p, encoding="utf-8-sig")
        if aud.empty:
            continue
        rows.append(aud)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


# ── 块级统计（与 AUDIT-04/05/06 完全一致；新交付到达后沿用）────────────
def n_blocks(block_days: int = BLOCK_DAYS) -> int:
    total_days = (END_MS - START_MS) / 86400000.0
    return int(np.ceil(total_days / block_days))


def block_series(df: pd.DataFrame, nb: int, block_days: int = BLOCK_DAYS) -> np.ndarray:
    out = np.zeros(nb, dtype=float)
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        idx = int((int(r["close_timestamp"]) - START_MS) // (block_days * 86400000))
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

# -*- coding: utf-8 -*-
"""WX_EVENT_TREND_V0_1 真实特征计算库。

包含：
1. 4h Wilder ATR(14) 严格递推实现；
2. 42 根 4h 收盘价的方向效率比 ER；
3. 突破前 20 根 1h 成交量中位数 V20；
4. 真实 6 小时持仓数量增长率提取（严禁任何 mock / 假指标）。
"""
from __future__ import annotations

import math
from typing import Optional, Tuple, Union
import numpy as np
import pandas as pd


def compute_wilder_atr_series(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """计算 Wilder ATR(14) 序列。
    
    定义：
    - 首根 TR = high - low
    - 之后 TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
    - 前 14 个 TR 的算术平均作为首个 ATR(14) 值 (index = 13)
    - 之后递推: ATR_t = (ATR_{t-1} * (period - 1) + TR_t) / period
    """
    n = len(highs)
    if n == 0:
        return np.array([], dtype=np.float64)
    
    tr = np.zeros(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)
        
    atr = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return atr
        
    # 前 period 个 TR 的算术平均作为初值
    atr[period - 1] = np.mean(tr[:period])
    alpha = 1.0 / float(period)
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / float(period)
        
    return atr


def compute_efficiency_ratio(closes: np.ndarray) -> Optional[float]:
    """计算给定收盘价序列的方向效率比 ER。
    
    公式: abs(last - first) / sum(abs(adjacent difference))
    分母为 0 则返回 None (不建档)。
    """
    if len(closes) < 2:
        return None
    net_change = abs(closes[-1] - closes[0])
    diffs = np.abs(np.diff(closes))
    total_path = np.sum(diffs)
    if total_path <= 1e-12:
        return None
    return float(net_change / total_path)


def compute_v20_median_volume(volumes: np.ndarray, current_idx: int, lookback: int = 20) -> Optional[float]:
    """计算突破小时之前 20 根完整 1h K 线成交量的中位数，不包含突破小时本身。"""
    if current_idx < lookback:
        return None
    window = volumes[current_idx - lookback: current_idx]
    if len(window) < lookback:
        return None
    return float(np.median(window))


#: OI 统计桶默认周期（官方 metrics / OI 接口均为 5 分钟桶，`ts` 是**桶起点**）
OI_BUCKET_MS = 300_000

#: 状态串：值本身可用，但「可得时刻」是假设出来的（桶结束），不是交易所证明的
OI_STATUS_ASSUMED = "ok_assumed"


def _as_of_index(event_time: np.ndarray, available_at: np.ndarray,
                 target_ms: int) -> int:
    """在「决策时刻已可得」的行里取观测时刻最新的一行；没有就返回 -1。

    ⚠ 这里是本单的核心修正点：判可用必须看 `available_at`，
    **不能**用 `event_time <= target`。`ts` 是 5 分钟桶的起点，
    桶起点 ≤ 决策时刻只说明桶开始了，桶里剩下的时间还在未来。
    """
    if len(event_time) == 0:
        return -1
    ok = np.isfinite(available_at) & (available_at <= float(target_ms))
    if not ok.any():
        return -1
    idx = np.flatnonzero(ok)
    return int(idx[np.argmax(event_time[idx])])


def extract_real_oi_growth_ex(
    oi_timestamps: np.ndarray,
    oi_values: np.ndarray,
    target_time_ms: int,
    lookback_ms: int = 6 * 3600 * 1000,
    max_distance_ms: int = 15 * 60 * 1000,
    oi_available_at: Optional[np.ndarray] = None,
    bucket_ms: int = OI_BUCKET_MS,
) -> dict:
    """带 PIT 判定的 OI 增幅提取，返回完整判定明细（供审计与策略入口使用）。

    `oi_available_at` 是每个观测**最早能被读到**的时刻（毫秒）。
    未提供时**不猜**：按「桶结束」当作假设时刻，并把 `assumed=True` 写进结果，
    由调用方决定是拒用（strict）还是照用但判本次运行不合格（declared_assumption）。

    返回字段：
      `oi_curr` / `oi_past` / `growth` / `status` / `assumed` /
      `curr_event_ms` / `curr_available_ms` / `past_event_ms` / `past_available_ms` /
      `decision_time_ms` / `bucket_ms` / `detail`
    """
    n = len(oi_timestamps)
    ts = np.asarray(oi_timestamps, dtype=np.float64) if n else np.array([], np.float64)
    vals = np.asarray(oi_values, dtype=np.float64) if n else np.array([], np.float64)
    d = int(target_time_ms)

    if oi_available_at is None:
        # 没有可得时刻证据 ⇒ 用「桶结束」当假设时刻，并如实标记 assumed
        avail = ts + float(bucket_ms) if n else np.array([], np.float64)
        assumed = True
    else:
        avail = np.asarray(oi_available_at, dtype=np.float64)
        if avail.size != n:
            raise ValueError(
                f"oi_available_at 长度 {avail.size} 与 oi_timestamps 长度 {n} 不一致")
        assumed = False

    out = {
        "oi_curr": None, "oi_past": None, "growth": None, "status": "oi_empty",
        "assumed": assumed, "curr_event_ms": None, "curr_available_ms": None,
        "past_event_ms": None, "past_available_ms": None,
        "decision_time_ms": d, "bucket_ms": int(bucket_ms), "detail": "",
    }
    if n == 0:
        out["detail"] = "无 OI 观测行"
        return out

    # ── 当刻值 ──
    idx_curr = _as_of_index(ts, avail, d)
    if idx_curr < 0:
        out["status"] = "oi_curr_missing_before_start"
        out["detail"] = f"决策时刻 {d} 之前没有任何已可得的 OI 观测"
        return out
    t_curr = int(ts[idx_curr])
    out["curr_event_ms"] = t_curr
    out["curr_available_ms"] = int(avail[idx_curr])
    if abs(d - t_curr) > max_distance_ms:
        out["status"] = f"oi_curr_too_far_{abs(d - t_curr)}ms"
        out["detail"] = (f"最近可得观测的**观测时刻**距决策 {abs(d - t_curr)}ms "
                         f"> 上限 {max_distance_ms}ms")
        return out
    val_curr = float(vals[idx_curr])
    if not np.isfinite(val_curr) or val_curr <= 0.0:
        out["status"] = "oi_curr_non_positive"
        out["detail"] = f"当刻 OI 非正或非有限：{val_curr}"
        return out

    # ── 6 小时前 ──
    target_past = d - int(lookback_ms)
    idx_past = _as_of_index(ts, avail, target_past)
    if idx_past < 0:
        out["status"] = "oi_past_missing_before_start"
        out["detail"] = f"决策时刻-回看窗口 {target_past} 之前没有已可得的 OI 观测"
        return out
    t_past = int(ts[idx_past])
    out["past_event_ms"] = t_past
    out["past_available_ms"] = int(avail[idx_past])
    if abs(target_past - t_past) > max_distance_ms:
        out["status"] = f"oi_past_too_far_{abs(target_past - t_past)}ms"
        out["detail"] = (f"回看点最近可得观测距回看点 {abs(target_past - t_past)}ms "
                         f"> 上限 {max_distance_ms}ms")
        return out
    val_past = float(vals[idx_past])
    if not np.isfinite(val_past) or val_past <= 0.0:
        out["status"] = "oi_past_non_positive"
        out["detail"] = f"6 小时前 OI 非正或非有限：{val_past}"
        return out

    out.update({
        "oi_curr": val_curr, "oi_past": val_past,
        "growth": (val_curr / val_past) - 1.0,
        "status": OI_STATUS_ASSUMED if assumed else "ok",
        "detail": ("可得时刻按「桶结束」假设，非交易所证明" if assumed
                   else "可得时刻由调用方提供"),
    })
    return out


def extract_real_oi_growth(
    oi_timestamps: np.ndarray,
    oi_values: np.ndarray,
    target_time_ms: int,
    lookback_ms: int = 6 * 3600 * 1000,
    max_distance_ms: int = 15 * 60 * 1000,
    oi_available_at: Optional[np.ndarray] = None,
    bucket_ms: int = OI_BUCKET_MS,
) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    """从真实持仓序列提取 6 小时前与当刻持仓数量及增幅。

    规则（FRAME-PIT-01 修正后）：
    - 候选观测必须**在决策时刻已可得**（`available_at <= T`），不是「观测时刻 <= T」；
    - OI(T) 取其中观测时刻最新的一条，且观测时刻距 T 不超过 15 分钟；
    - OI(T-6h) 同理，以 T-6h 为决策时刻；
    - 任一缺失、非正或超距 ⇒ 返回 `(None, None, None, 原因)`；
    - 值可用但可得时刻是假设出来的 ⇒ 状态为 `ok_assumed`（**不是** `ok`）。

    旧的 `searchsorted(ts, T, side="right") - 1` 会把「起点正好等于 T 的那个桶」
    当成已知，而那个桶覆盖的是 T 之后的 5 分钟 ⇒ 未来数据。
    """
    r = extract_real_oi_growth_ex(
        oi_timestamps, oi_values, target_time_ms,
        lookback_ms=lookback_ms, max_distance_ms=max_distance_ms,
        oi_available_at=oi_available_at, bucket_ms=bucket_ms,
    )
    return r["oi_curr"], r["oi_past"], r["growth"], r["status"]

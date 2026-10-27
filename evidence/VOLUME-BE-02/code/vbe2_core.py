# -*- coding: utf-8 -*-
"""VOLUME-BE-02 · 核心算法与真实路径执行模块。

本模块封装实际特征提取与持仓退出逻辑，供回测统计脚本与验证测试脚本共同调用，
杜绝在测试脚本中写“另行构造的条件表达式”。

功能清单：
1. `extract_entry_features`: 严格因果时序的 1h / 15m 特征提取（close_time <= entry_ms）；
2. `simulate_position_exit`: 严格生命周期 [entry_ms, A_exit_time_ms] 的持仓退出模拟与同根冲突判定；
3. `calculate_exact_funding`: 逐事件累加真实资金费，无比例估算。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

TAKER_FEE = 0.0005
SLIPPAGE = 0.0003


def extract_entry_features(df_1h: pd.DataFrame, df_15m: pd.DataFrame, entry_ms: int) -> Dict[str, Any]:
    """严格因果提取：只能使用 close_time <= entry_ms 的已闭合 K 线。"""
    h1_closed = df_1h[df_1h["close_time"] <= entry_ms]
    m15_closed = df_15m[df_15m["close_time"] <= entry_ms]

    if len(h1_closed) < 2:
        raise ValueError(f"Insufficient closed 1h bars before {entry_ms}")

    breakout_1h = h1_closed.iloc[-1]
    prev_1h = h1_closed.iloc[-2]
    last_15m = m15_closed.iloc[-1] if len(m15_closed) > 0 else None

    # 1. 突破 1h bar 量能
    b_vol = float(breakout_1h["volume"])
    # 历史 20 根均量（排除当根）
    vols_prev_b = h1_closed["volume"].iloc[:-1].tail(20)
    b_volsma = float(vols_prev_b.mean()) if len(vols_prev_b) >= 5 else b_vol
    is_breakout_vol_dryup = bool(b_vol < b_volsma)

    # 2. 突破前夕 1h (prev_1h) 回调形态三分类
    p_close = float(prev_1h["close"])
    p_open = float(prev_1h["open"])
    p_vol = float(prev_1h["volume"])
    vols_prev_p = h1_closed["volume"].iloc[:-2].tail(20)
    p_volsma = float(vols_prev_p.mean()) if len(vols_prev_p) >= 5 else p_vol
    p_drop = bool(p_close < p_open)

    prev_1h_low_vol_drop = bool(p_drop and (p_vol < p_volsma))
    prev_1h_high_vol_drop = bool(p_drop and (p_vol >= p_volsma))
    prev_1h_non_drop = bool(not p_drop)

    prev_1h_category = "low_vol_drop" if prev_1h_low_vol_drop else ("high_vol_drop" if prev_1h_high_vol_drop else "non_drop")

    # 3. 入场前最后一根已闭合 15m (m15) 形态三分类
    if last_15m is not None:
        m_close = float(last_15m["close"])
        m_open = float(last_15m["open"])
        m_vol = float(last_15m["volume"])
        vols_prev_m = m15_closed["volume"].iloc[:-1].tail(20)
        m_volsma = float(vols_prev_m.mean()) if len(vols_prev_m) >= 5 else m_vol
        m_drop = bool(m_close < m_open)
        m15_low_vol_drop = bool(m_drop and (m_vol < m_volsma))
        m15_high_vol_drop = bool(m_drop and (m_vol >= m_volsma))
        m15_non_drop = bool(not m_drop)
        m15_category = "low_vol_drop" if m15_low_vol_drop else ("high_vol_drop" if m15_high_vol_drop else "non_drop")
    else:
        m15_low_vol_drop = False
        m15_high_vol_drop = False
        m15_non_drop = True
        m15_category = "non_drop"

    return {
        "breakout_1h_vol": b_vol,
        "breakout_1h_volsma": b_volsma,
        "is_breakout_vol_dryup": is_breakout_vol_dryup,
        "prev_1h_close": p_close,
        "prev_1h_open": p_open,
        "prev_1h_vol": p_vol,
        "prev_1h_volsma": p_volsma,
        "prev_1h_category": prev_1h_category,
        "prev_1h_low_vol_drop": prev_1h_low_vol_drop,
        "prev_1h_high_vol_drop": prev_1h_high_vol_drop,
        "prev_1h_non_drop": prev_1h_non_drop,
        "m15_category": m15_category,
        "m15_low_vol_drop": m15_low_vol_drop,
        "m15_high_vol_drop": m15_high_vol_drop,
        "m15_non_drop": m15_non_drop,
    }


def calculate_exact_funding(events: List[dict], entry_ms: int, exit_ms: int, qty: float, direction: int = 1) -> float:
    """逐事件累加真实资金费，无任何比例估算（对齐 trading_core/funding.py 跳过非有限值）。"""
    cum = 0.0
    for ev in events or []:
        t = int(ev.get("time_ms", 0))
        if entry_ms < t <= exit_ms:
            r = ev.get("rate")
            m = ev.get("mark_price")
            if r is not None and m is not None:
                r_f, m_f = float(r), float(m)
                if math.isfinite(r_f) and math.isfinite(m_f) and m_f > 0:
                    cost = m_f * qty * r_f * direction
                    cum += cost
    return float(cum)


def simulate_position_exit(
    df_15m: pd.DataFrame,
    *,
    entry_ms: int,
    a_exit_ms: int,
    entry_px: float,
    qty: float,
    init_stop: float,
    a_entry_fee: float,
    fund_events: List[dict]
) -> Dict[str, Any]:
    """严格持仓生命周期 [entry_ms, a_exit_ms] 的退出模拟，包含同根冲突保守执行。"""
    # 候选 15m bars：必须在持仓存续期间 entry_ms <= timestamp < a_exit_ms
    m15_during = df_15m[(df_15m["timestamp"] >= entry_ms) & (df_15m["timestamp"] < a_exit_ms)].copy()

    pos_exit_triggered = False
    pos_exit_time_ms = None
    pos_exit_px = None
    pos_exit_reason = None
    pos_exit_net = None
    same_bar_conflict_occurred = False

    for b_idx in range(len(m15_during)):
        curr_bar = m15_during.iloc[b_idx]
        curr_low = float(curr_bar["low"])

        curr_c = float(curr_bar["close"])
        curr_o = float(curr_bar["open"])
        curr_v = float(curr_bar["volume"])
        
        # 历史 20 根 15m 均量
        curr_idx = curr_bar.name
        vols_m15 = df_15m["volume"].iloc[max(0, curr_idx - 20):curr_idx]
        curr_vsma = float(vols_m15.mean()) if len(vols_m15) >= 5 else curr_v

        is_vol_drop = (curr_c < curr_o) and (curr_c < entry_px) and (curr_v < curr_vsma)
        hit_stop = (curr_low <= init_stop)

        # 同根冲突检测：当根既满足缩量下跌又跌破初始止损
        if is_vol_drop and hit_stop:
            same_bar_conflict_occurred = True
            # 保守原则：严格按原初始止损退出，不可提前逃脱
            break

        # 若未发生同根冲突，但跌破初始止损，终止推进（由原止损退出）
        if hit_stop:
            break

        # 若满足缩量下跌且未破止损，计划下一根开盘退出
        if is_vol_drop:
            next_bar_idx = curr_idx + 1
            if next_bar_idx < len(df_15m):
                next_bar = df_15m.iloc[next_bar_idx]
                target_exit_ms = int(next_bar["timestamp"])

                # 严格生命周期约束：必须严格早于原策略平仓时刻 a_exit_ms！
                if target_exit_ms < a_exit_ms:
                    next_o = float(next_bar["open"])
                    if next_o <= init_stop:
                        # 下一根开盘直接跳空跌破止损，按原止损
                        break

                    pos_exit_triggered = True
                    pos_exit_time_ms = target_exit_ms
                    pos_exit_px = next_o * (1.0 - SLIPPAGE)
                    pos_exit_reason = "low_volume_fall_exit"

                    # 真实费用结算
                    exit_fee = pos_exit_px * qty * TAKER_FEE
                    exact_funding = calculate_exact_funding(fund_events, entry_ms, pos_exit_time_ms, qty, 1)
                    gross_pnl = (pos_exit_px - entry_px) * qty
                    pos_exit_net = gross_pnl - a_entry_fee - exit_fee - exact_funding
                    break
                else:
                    break

    return {
        "pos_exit_triggered": pos_exit_triggered,
        "pos_exit_time_ms": pos_exit_time_ms,
        "pos_exit_px": pos_exit_px,
        "pos_exit_reason": pos_exit_reason,
        "pos_exit_net": pos_exit_net,
        "same_bar_conflict_occurred": same_bar_conflict_occurred,
    }

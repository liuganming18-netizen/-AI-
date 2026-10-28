# -*- coding: utf-8 -*-
"""威科夫单一主策略 · 多因子全数据共振决策评估引擎。

落实用户最新核心指令：
1. 让所有已采集的数据（OI 持仓量、聪明钱阶段 VWAP 成本、主动买卖比 Taker、大户/散户多空比）
   全面、实质性地参与到威科夫主策略决策与风控仓位管理中。
2. 严格坚守威科夫单一主策略主线，不搞多策略分叉。
3. 杜绝一票否决误杀：以动态置信度打分（Resonance Score）与动态仓位调节倍数（Position Multiplier）
   实现软性共振调节，缺数据时不伪造、给中性分，不因单个数据缺失拒杀信号。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("wyckoff_factor_resonance")


def evaluate_resonance(
    symbol: str,
    decision_ms: int,
    current_price: float,
    box_low: float,
    box_high: float,
    box_atr: float,
    phase_start_ms: Optional[int] = None,
    oi_growth_6h: Optional[float] = None,
    current_oi: Optional[float] = None,
    whale_vwap_override: Optional[float] = None,
    taker_ratio_override: Optional[float] = None,
    top_pos_ls_override: Optional[float] = None,
) -> Dict[str, Any]:
    """综合计算 OI、聪明钱巨鲸成本、主动买卖比、大户散户多空比等多因子共振评分。

    返回:
      score: 0 ~ 100 综合共振置信度得分
      position_multiplier: 0.5 ~ 1.3 动态仓位调节倍数
      factors: 各分项指标真实数值、状态与得分
      summary: 紧凑中文评价摘要
    """
    import metrics_feed as MF

    base_score = 40.0  # 威科夫基础形态完成度基准分 (4h箱体蓄势 + 1h突破/扫低)

    factors: Dict[str, Any] = {}
    summaries = []

    # ────────────────────────────────────────────────────────────────
    # 1. 持仓量 OI 因子 (最高 15 分)
    # ────────────────────────────────────────────────────────────────
    oi_score = 6.0  # 中性默认分
    oi_status = "neutral"
    growth = oi_growth_6h

    # 若未传入，尝试从 metrics_feed 中推算
    if growth is None:
        try:
            m_tail = MF.read_metrics_tail(symbol, decision_ms, max_row_groups=3)
            if len(m_tail) >= 2:
                # 寻找 6 小时前 (约 72 根 5m bar)
                cur_oi = float(m_tail[-1].get("sum_open_interest") or 0.0)
                target_ts = decision_ms - 6 * 3600 * 1000
                past_row = None
                for r in m_tail:
                    if int(r.get("ts") or 0) <= target_ts:
                        past_row = r
                if past_row and float(past_row.get("sum_open_interest") or 0.0) > 0:
                    past_oi = float(past_row["sum_open_interest"])
                    growth = (cur_oi - past_oi) / past_oi
                    current_oi = cur_oi
        except Exception:
            pass

    if growth is not None:
        if growth >= 0.05:
            oi_score = 15.0
            oi_status = "strong_expansion"
            summaries.append(f"OI强增+{growth*100:.1f}%")
        elif growth >= 0.02:
            oi_score = 12.0
            oi_status = "healthy_expansion"
            summaries.append(f"OI增+{growth*100:.1f}%")
        elif growth >= 0.0:
            oi_score = 8.0
            oi_status = "mild_expansion"
            summaries.append(f"OI平稳+{growth*100:.1f}%")
        elif growth >= -0.03:
            oi_score = 4.0
            oi_status = "mild_contraction"
            summaries.append(f"OI微减{growth*100:.1f}%")
        else:
            oi_score = -4.0
            oi_status = "sharp_contraction"
            summaries.append(f"OI减仓{growth*100:.1f}%")
    else:
        summaries.append("OI中性")

    factors["oi"] = {
        "growth_6h": growth,
        "current_oi": current_oi,
        "score": oi_score,
        "status": oi_status,
        "available": growth is not None,
    }

    # ────────────────────────────────────────────────────────────────
    # 2. 聪明钱巨鲸成本 VWAP 因子 (最高 15 分)
    # ────────────────────────────────────────────────────────────────
    whale_score = 6.0
    whale_status = "neutral"
    vwap = whale_vwap_override
    p_from = int(phase_start_ms or (decision_ms - 42 * 4 * 3600 * 1000))

    if vwap is None:
        try:
            avg_res = MF.phase_avg_price(symbol, p_from, decision_ms)
            vwap = avg_res.get("阶段均价")
        except Exception:
            pass

    if vwap is not None and vwap > 0 and current_price > 0:
        ratio = (current_price - vwap) / vwap
        if 0.0 <= ratio <= 0.05:
            whale_score = 15.0
            whale_status = "optimal_support"
            summaries.append("站稳巨鲸成本")
        elif 0.05 < ratio <= 0.12:
            whale_score = 11.0
            whale_status = "safe_markup"
            summaries.append(f"高于成本{ratio*100:.1f}%")
        elif -0.03 <= ratio < 0.0:
            whale_score = 8.0
            whale_status = "testing_cost"
            summaries.append("回踩巨鲸成本")
        elif ratio > 0.12:
            whale_score = 5.0
            whale_status = "extended"
            summaries.append("稍偏离成本")
        else:
            whale_score = -4.0
            whale_status = "below_cost"
            summaries.append("跌破成本")
    else:
        summaries.append("巨鲸均价中性")

    factors["whale_cost"] = {
        "vwap": vwap,
        "premium_ratio": ((current_price - vwap) / vwap) if (vwap and current_price) else None,
        "score": whale_score,
        "status": whale_status,
        "available": vwap is not None,
    }

    # ────────────────────────────────────────────────────────────────
    # 3. 主动买卖量比 Taker Ratio 因子 (最高 15 分)
    # ────────────────────────────────────────────────────────────────
    taker_score = 6.0
    taker_status = "neutral"
    taker_ratio = taker_ratio_override

    if taker_ratio is None:
        try:
            met = MF.latest_metrics(symbol, decision_ms)
            if met.get("found"):
                t_block = met.get("主动买卖量比") or {}
                taker_ratio = t_block.get("值")
        except Exception:
            pass

    if taker_ratio is not None and taker_ratio > 0:
        if taker_ratio >= 1.20:
            taker_score = 15.0
            taker_status = "heavy_buying"
            summaries.append(f"买盘强(Taker {taker_ratio:.2f})")
        elif taker_ratio >= 1.05:
            taker_score = 12.0
            taker_status = "active_buying"
            summaries.append(f"Taker {taker_ratio:.2f}")
        elif taker_ratio >= 0.95:
            taker_score = 7.0
            taker_status = "balanced"
            summaries.append(f"Taker均衡 {taker_ratio:.2f}")
        elif taker_ratio >= 0.85:
            taker_score = 3.0
            taker_status = "mild_selling"
            summaries.append(f"卖盘偏重 {taker_ratio:.2f}")
        else:
            taker_score = -4.0
            taker_status = "heavy_selling"
            summaries.append(f"卖盘压制 {taker_ratio:.2f}")
    else:
        summaries.append("Taker中性")

    factors["taker_ratio"] = {
        "value": taker_ratio,
        "score": taker_score,
        "status": taker_status,
        "available": taker_ratio is not None,
    }

    # ────────────────────────────────────────────────────────────────
    # 4. 大户与散户多空比意愿因子 (最高 15 分)
    # ────────────────────────────────────────────────────────────────
    ls_score = 6.0
    ls_status = "neutral"
    top_pos_ls = top_pos_ls_override
    retail_ls = None

    if top_pos_ls is None:
        try:
            met = MF.latest_metrics(symbol, decision_ms)
            if met.get("found"):
                ratios = met.get("多空比") or {}
                top_pos = ratios.get("top_position_ls_ratio") or {}
                top_pos_ls = top_pos.get("值")
                ret_pos = ratios.get("retail_account_ls_ratio") or {}
                retail_ls = ret_pos.get("值")
        except Exception:
            pass

    if top_pos_ls is not None and top_pos_ls > 0:
        if top_pos_ls >= 1.20:
            ls_score = 15.0
            ls_status = "whale_bullish"
            summaries.append(f"大户做多 {top_pos_ls:.2f}")
        elif top_pos_ls >= 1.05:
            ls_score = 11.0
            ls_status = "mild_bullish"
            summaries.append(f"大户多空 {top_pos_ls:.2f}")
        elif top_pos_ls >= 0.95:
            ls_score = 6.0
            ls_status = "neutral"
            summaries.append(f"多空均衡 {top_pos_ls:.2f}")
        else:
            ls_score = -4.0
            ls_status = "whale_bearish"
            summaries.append(f"大户偏空 {top_pos_ls:.2f}")
    else:
        summaries.append("多空比中性")

    factors["long_short"] = {
        "top_position_ratio": top_pos_ls,
        "retail_ratio": retail_ls,
        "score": ls_score,
        "status": ls_status,
        "available": top_pos_ls is not None,
    }

    # ────────────────────────────────────────────────────────────────
    # 5. 综合共振总分与仓位调节倍数
    # ────────────────────────────────────────────────────────────────
    raw_total = base_score + oi_score + whale_score + taker_score + ls_score
    total_score = max(0.0, min(100.0, round(raw_total, 1)))

    if total_score >= 80.0:
        pos_mult = 1.3
        grade = "强共振"
    elif total_score >= 65.0:
        pos_mult = 1.0
        grade = "健康共振"
    elif total_score >= 50.0:
        pos_mult = 0.8
        grade = "基础达标"
    else:
        pos_mult = 0.5
        grade = "偏弱防守"

    summary_text = f"共振分{total_score:.0f}({grade}) · " + " · ".join(summaries)

    return {
        "resonance_score": total_score,
        "position_multiplier": pos_mult,
        "grade": grade,
        "summary": summary_text,
        "factors": factors,
    }

# -*- coding: utf-8 -*-
"""FT-FACTOR-03 基础价量研究变体消融组 3：WyckoffPVVariant_NoGapFilter
消融因子：F_GAP_IN (移除入场跳空上限过滤)
规范定义：
- 属于基础价量研究变体，与价量基线保持完全同一的 OI 关闭设置（ablation_enable_oi_filter = False）；
- 移除入场跳空上限过滤（放宽上限为 999999.0，使跳空不被过滤拦截）；
- 保留突破放量、回踩缩量等其他规则。
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffPVVariant_NoGapFilter(WyckoffEventTrendBaseline):
    ablation_enable_oi_filter: bool = False
    ablation_max_gap_up_atr: float = 999999.0

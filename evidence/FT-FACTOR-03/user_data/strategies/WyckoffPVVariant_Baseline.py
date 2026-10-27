# -*- coding: utf-8 -*-
"""FT-FACTOR-03 基础价量研究变体基线策略：WyckoffPVVariant_Baseline
规范定义：
- 属于基础价量研究变体（Research Variant），明确区别于原策略（含OI）；
- 统一关闭 OI 过滤（ablation_enable_oi_filter = False）；
- 保留突破放量、回踩缩量、入场跳空上限等完整基础价量过滤条件。
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffPVVariant_Baseline(WyckoffEventTrendBaseline):
    ablation_enable_oi_filter: bool = False

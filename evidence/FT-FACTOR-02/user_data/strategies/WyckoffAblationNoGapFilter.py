# -*- coding: utf-8 -*-
"""FT-FACTOR-02 消融实验组 4：WyckoffAblationNoGapFilter
消融因子：F_GAP_IN (移除入场跳空上限过滤)
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffAblationNoGapFilter(WyckoffEventTrendBaseline):
    ablation_max_gap_up_atr: float = 999999.0

# -*- coding: utf-8 -*-
"""FT-FACTOR-02 消融实验组 1：WyckoffAblationNoOI
消融因子：F_OI (移除 6h OI 增幅 >= 2% 过滤)
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffAblationNoOI(WyckoffEventTrendBaseline):
    ablation_enable_oi_filter: bool = False

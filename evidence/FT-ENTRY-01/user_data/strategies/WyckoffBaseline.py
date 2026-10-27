# -*- coding: utf-8 -*-
"""FT-ENTRY-01 对照基准：WyckoffBaseline
原基线策略：突破 H + 0.1A，突破放量 >= 1.5 V20，6h OI 增幅 >= 2%，初始止损 P0 = Lr - 0.1A
"""
from WyckoffEntryTimingBase import WyckoffEntryTimingBase


class WyckoffBaseline(WyckoffEntryTimingBase):
    timing_mode = "baseline"

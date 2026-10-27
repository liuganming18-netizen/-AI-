# -*- coding: utf-8 -*-
"""FT-ENTRY-01 路线 A2：WyckoffEntryA2_AtBoxHigh
箱顶临界线提前入场：RETEST 确认后，close >= H，温和放量 volume >= 1.0 V20，6h OI 增幅 >= 2%，初始止损 P0 = Lr - 0.1A
"""
from WyckoffEntryTimingBase import WyckoffEntryTimingBase


class WyckoffEntryA2_AtBoxHigh(WyckoffEntryTimingBase):
    timing_mode = "A2"

# -*- coding: utf-8 -*-
"""FT-ENTRY-01 路线 A1：WyckoffEntryA1_NearTop
突破前逼近入场：RETEST 确认后，close >= H - 0.5A，温和放量 volume >= 1.0 V20，6h OI 增幅 >= 2%，初始止损 P0 = Lr - 0.1A
"""
from WyckoffEntryTimingBase import WyckoffEntryTimingBase


class WyckoffEntryA1_NearTop(WyckoffEntryTimingBase):
    timing_mode = "A1"

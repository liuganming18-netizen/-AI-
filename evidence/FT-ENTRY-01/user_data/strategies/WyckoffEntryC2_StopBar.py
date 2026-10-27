# -*- coding: utf-8 -*-
"""FT-ENTRY-01 路线 C2：WyckoffEntryC2_StopBar
缩量回踩早期入场 (当根低点防线)：SWEEP 后首次缩量企稳 (volume <= 0.8 Vs 且收阳)，6h OI 增幅 >= 0%，初始止损 P0 = low_bar - 0.1A (当根低点)
"""
from WyckoffEntryTimingBase import WyckoffEntryTimingBase


class WyckoffEntryC2_StopBar(WyckoffEntryTimingBase):
    timing_mode = "C2"

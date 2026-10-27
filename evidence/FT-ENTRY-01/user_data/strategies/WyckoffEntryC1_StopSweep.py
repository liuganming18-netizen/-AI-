# -*- coding: utf-8 -*-
"""FT-ENTRY-01 路线 C1：WyckoffEntryC1_StopSweep
缩量回踩早期入场 (扫低防线)：SWEEP 后首次缩量企稳 (volume <= 0.8 Vs 且收阳)，6h OI 增幅 >= 0%，初始止损 P0 = S - 0.1A (历史扫低点)
"""
from WyckoffEntryTimingBase import WyckoffEntryTimingBase


class WyckoffEntryC1_StopSweep(WyckoffEntryTimingBase):
    timing_mode = "C1"

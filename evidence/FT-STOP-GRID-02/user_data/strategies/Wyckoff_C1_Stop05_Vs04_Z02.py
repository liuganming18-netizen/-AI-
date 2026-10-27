# -*- coding: utf-8 -*-
"""FT-STOP-GRID-02: 5%止损+2R跟踪方案 (Vs04_Z02)"""
from WyckoffStopGrid02Base import WyckoffStopGrid02Base

class Wyckoff_C1_Stop05_Vs04_Z02(WyckoffStopGrid02Base):
    timing_mode = "C1"
    stoploss_pct = 0.05
    c_vol_ratio_vs = 0.4
    c_retest_zone_atr = 0.2

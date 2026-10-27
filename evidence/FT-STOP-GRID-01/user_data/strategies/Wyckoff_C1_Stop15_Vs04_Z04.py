# -*- coding: utf-8 -*-
"""FT-STOP-GRID-01: 固定15%止损方案 (Vs04_Z04)"""
from WyckoffStopGridBase import WyckoffStopGridBase

class Wyckoff_C1_Stop15_Vs04_Z04(WyckoffStopGridBase):
    timing_mode = "C1"
    stoploss_pct = 0.15
    c_vol_ratio_vs = 0.4
    c_retest_zone_atr = 0.4

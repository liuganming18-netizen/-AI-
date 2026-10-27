# -*- coding: utf-8 -*-
"""FT-STOP-GRID-01: 固定20%止损方案 (Vs04_Z04)"""
from WyckoffStopGridBase import WyckoffStopGridBase

class Wyckoff_C1_Stop20_Vs04_Z04(WyckoffStopGridBase):
    timing_mode = "C1"
    stoploss_pct = 0.2
    c_vol_ratio_vs = 0.4
    c_retest_zone_atr = 0.4

# -*- coding: utf-8 -*-
"""FT-STOP-GRID-01: 固定5%止损方案 (Vs08_Z04)"""
from WyckoffStopGridBase import WyckoffStopGridBase

class Wyckoff_C1_Stop05_Vs08_Z04(WyckoffStopGridBase):
    timing_mode = "C1"
    stoploss_pct = 0.05
    c_vol_ratio_vs = 0.8
    c_retest_zone_atr = 0.4

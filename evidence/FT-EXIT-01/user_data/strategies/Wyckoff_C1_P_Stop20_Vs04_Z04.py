# -*- coding: utf-8 -*-
"""FT-EXIT-01: 固定20%仅初始止损方案 (Vs04_Z04)"""
from WyckoffExit01Base import WyckoffExit01Base

class Wyckoff_C1_P_Stop20_Vs04_Z04(WyckoffExit01Base):
    timing_mode = "C1"
    stoploss_scheme = "P"
    c_vol_ratio_vs = 0.4
    c_retest_zone_atr = 0.4

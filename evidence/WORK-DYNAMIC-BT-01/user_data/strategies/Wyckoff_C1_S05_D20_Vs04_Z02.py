# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01 固定 d 组：B 固定 d=20%｜s=5%, d=20%｜Vs04_Z02"""
from WyckoffLow4hGridBase import WyckoffLow4hGridBase


class Wyckoff_C1_S05_D20_Vs04_Z02(WyckoffLow4hGridBase):
    stoploss_s: float = 0.05
    trail_d: float = 0.2
    c_vol_ratio_vs: float = 0.4
    c_retest_zone_atr: float = 0.2
    timing_mode: str = "C1"

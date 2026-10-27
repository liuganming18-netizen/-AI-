# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01 固定 d 组：P1 一致性对照：固定 d=8%｜s=5%, d=8%｜Vs08_Z04"""
from WyckoffLow4hGridBase import WyckoffLow4hGridBase


class Wyckoff_C1_S05_D08_Vs08_Z04(WyckoffLow4hGridBase):
    stoploss_s: float = 0.05
    trail_d: float = 0.08
    c_vol_ratio_vs: float = 0.8
    c_retest_zone_atr: float = 0.4
    timing_mode: str = "C1"

# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01 动态 d 组：P2 一致性对照：动态类，切档阈值不可达｜s=5%, d 0.08→0.2→0.05｜Vs06_Z06"""
from WyckoffLow4hDynBase import WyckoffLow4hDynBase


class Wyckoff_C1_S05_DYN999_Vs06_Z06(WyckoffLow4hDynBase):
    stoploss_s: float = 0.05
    trail_d: float = 0.05          # 动态组不使用该字段（由 dyn_d1/2/3 取代），保留仅为签名一致
    dyn_d1: float = 0.08
    dyn_d2: float = 0.2
    dyn_d3: float = 0.05
    dyn_trigger_1: float = 9.99
    dyn_trigger_2: float = 9.99
    c_vol_ratio_vs: float = 0.6
    c_retest_zone_atr: float = 0.6
    timing_mode: str = "C1"

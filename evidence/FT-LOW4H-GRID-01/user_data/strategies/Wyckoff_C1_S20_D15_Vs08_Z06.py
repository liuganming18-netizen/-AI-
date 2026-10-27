# -*- coding: utf-8 -*-
from WyckoffLow4hGridBase import WyckoffLow4hGridBase


class Wyckoff_C1_S20_D15_Vs08_Z06(WyckoffLow4hGridBase):
    stoploss_s: float = 0.2
    trail_d: float = 0.15
    c_vol_ratio_vs: float = 0.8
    c_retest_zone_atr: float = 0.6
    timing_mode: str = "C1"

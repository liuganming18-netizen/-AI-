# -*- coding: utf-8 -*-
from WyckoffLow4hGridBase import WyckoffLow4hGridBase


class Wyckoff_C1_S10_D05_Vs06_Z04(WyckoffLow4hGridBase):
    stoploss_s: float = 0.1
    trail_d: float = 0.05
    c_vol_ratio_vs: float = 0.6
    c_retest_zone_atr: float = 0.4
    timing_mode: str = "C1"

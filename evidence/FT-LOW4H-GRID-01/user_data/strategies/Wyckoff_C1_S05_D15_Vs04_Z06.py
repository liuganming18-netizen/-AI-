# -*- coding: utf-8 -*-
from WyckoffLow4hGridBase import WyckoffLow4hGridBase


class Wyckoff_C1_S05_D15_Vs04_Z06(WyckoffLow4hGridBase):
    stoploss_s: float = 0.05
    trail_d: float = 0.15
    c_vol_ratio_vs: float = 0.4
    c_retest_zone_atr: float = 0.6
    timing_mode: str = "C1"

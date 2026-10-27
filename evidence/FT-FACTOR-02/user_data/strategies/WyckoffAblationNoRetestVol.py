# -*- coding: utf-8 -*-
"""FT-FACTOR-02 消融实验组 3：WyckoffAblationNoRetestVol
消融因子：F_VOL_RT (移除回踩 0.8x Vs 缩量过滤)
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffAblationNoRetestVol(WyckoffEventTrendBaseline):
    ablation_retest_vol_multiplier: float = 999999.0

# -*- coding: utf-8 -*-
"""FT-FACTOR-02 消融实验组 2：WyckoffAblationNoBreakoutVol
消融因子：F_VOL_BO (移除突破 1.5x V20 放量过滤)
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffAblationNoBreakoutVol(WyckoffEventTrendBaseline):
    ablation_breakout_vol_multiplier: float = 0.0

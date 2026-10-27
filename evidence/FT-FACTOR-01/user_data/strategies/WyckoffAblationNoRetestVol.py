# -*- coding: utf-8 -*-
"""FT-FACTOR-01 消融实验组 3：WyckoffAblationNoRetestVol

消融因子：F_VOL_RT (回踩 K 线成交量 <= 0.8 * Vs 缩量过滤)
设置：ablation_retest_vol_multiplier = 999999.0
其余所有状态机规则、风控、仓位、止损、费用保持 100% 严格一致。
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffAblationNoRetestVol(WyckoffEventTrendBaseline):
    ablation_retest_vol_multiplier: float = 999999.0

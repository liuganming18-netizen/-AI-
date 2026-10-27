# -*- coding: utf-8 -*-
"""FT-FACTOR-01 消融实验组 2：WyckoffAblationNoBreakoutVol

消融因子：F_VOL_BO (突破 K 线成交量 >= 1.5 * V20 过滤)
设置：ablation_breakout_vol_multiplier = 0.0
其余所有状态机规则、风控、仓位、止损、费用保持 100% 严格一致。
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffAblationNoBreakoutVol(WyckoffEventTrendBaseline):
    ablation_breakout_vol_multiplier: float = 0.0

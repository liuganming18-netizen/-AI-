# -*- coding: utf-8 -*-
"""FT-FACTOR-01 消融实验组 4：WyckoffAblationNoGapFilter

消融因子：F_GAP_IN (次根 15m 开盘跳空上限过滤：max_gap_up_atr = 0.5)
设置：ablation_max_gap_up_atr = 999999.0
其余所有状态机规则、风控、仓位、止损、费用保持 100% 严格一致。
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffAblationNoGapFilter(WyckoffEventTrendBaseline):
    ablation_max_gap_up_atr: float = 999999.0

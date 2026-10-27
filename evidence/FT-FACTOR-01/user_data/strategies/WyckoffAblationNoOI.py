# -*- coding: utf-8 -*-
"""FT-FACTOR-01 消融实验组 1：WyckoffAblationNoOI

消融因子：F_OI (突破时 6h OI 增幅 >= 2% 过滤)
设置：ablation_enable_oi_filter = False
其余所有状态机规则、风控、仓位、止损、费用保持 100% 严格一致。
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffAblationNoOI(WyckoffEventTrendBaseline):
    ablation_enable_oi_filter: bool = False

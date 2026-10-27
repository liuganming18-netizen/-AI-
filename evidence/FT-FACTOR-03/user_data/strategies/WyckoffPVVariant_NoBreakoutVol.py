# -*- coding: utf-8 -*-
"""FT-FACTOR-03 基础价量研究变体消融组 1：WyckoffPVVariant_NoBreakoutVol
消融因子：F_VOL_BO (移除突破 1.5x V20 放量过滤)
规范定义：
- 属于基础价量研究变体，与价量基线保持完全同一的 OI 关闭设置（ablation_enable_oi_filter = False）；
- 移除突破放量过滤（ablation_breakout_vol_multiplier = 0.0）；
- 保留回踩缩量、跳空上限等其他规则。
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffPVVariant_NoBreakoutVol(WyckoffEventTrendBaseline):
    ablation_enable_oi_filter: bool = False
    ablation_breakout_vol_multiplier: float = 0.0

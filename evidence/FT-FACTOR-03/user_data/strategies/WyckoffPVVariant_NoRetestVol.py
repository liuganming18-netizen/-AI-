# -*- coding: utf-8 -*-
"""FT-FACTOR-03 基础价量研究变体消融组 2：WyckoffPVVariant_NoRetestVol
消融因子：F_VOL_RT (移除回踩 0.8x Vs 缩量过滤)
规范定义：
- 属于基础价量研究变体，与价量基线保持完全同一的 OI 关闭设置（ablation_enable_oi_filter = False）；
- 移除回踩缩量过滤（放宽上限为 999999.0，使回踩无论放量缩量均不被过滤）；
- 保留突破放量、跳空上限等其他规则。
"""
from WyckoffEventTrendBaseline import WyckoffEventTrendBaseline


class WyckoffPVVariant_NoRetestVol(WyckoffEventTrendBaseline):
    ablation_enable_oi_filter: bool = False
    ablation_retest_vol_multiplier: float = 999999.0

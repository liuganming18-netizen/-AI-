# -*- coding: utf-8 -*-
"""
Freqtrade 官方策略适配: WyckoffEventTrendBaseline
基准策略: WX_EVENT_TREND_V0_1 低频事件趋势策略 (原策略基线)
"""

from datetime import datetime, timezone
import math
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy, merge_informative_pair
from freqtrade.persistence import Trade


def compute_wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """计算 Wilder ATR(14) 序列 (与原策略算法严格字节一致)."""
    n = len(highs)
    if n == 0:
        return np.array([], dtype=np.float64)
    tr = np.zeros(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)
    atr = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return atr
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / float(period)
    return atr


def compute_efficiency_ratio(closes: np.ndarray) -> Optional[float]:
    """计算给定收盘价序列的方向效率比 ER."""
    if len(closes) < 2:
        return None
    net_change = abs(closes[-1] - closes[0])
    diffs = np.abs(np.diff(closes))
    total_path = np.sum(diffs)
    if total_path <= 1e-12:
        return None
    return float(net_change / total_path)


class WyckoffEventTrendBaseline(IStrategy):
    """
    威科夫事件趋势低频基准策略 (Freqtrade 官方标准接口实现)
    """
    INTERFACE_VERSION = 3

    # 主执行周期 15m
    timeframe = '15m'
    can_short = False

    # 永续合约模式
    trading_mode = "futures"
    margin_mode = "isolated"

    # 跟踪止损与退出由策略内部动态管理，禁用固定硬止盈
    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    use_custom_stoploss = True
    process_only_new_candles = True

    # 预热根数 (4h 需预热至少 200 根以稳定 Wilder ATR)
    startup_candle_count: int = 250

    def informative_pairs(self) -> List[Tuple[str, str]]:
        """注册 1h 和 4h 辅助周期."""
        if not hasattr(self, 'dp') or not self.dp:
            return []
        pairs = self.dp.current_whitelist()
        informative = []
        for pair in pairs:
            informative.append((pair, '1h'))
            informative.append((pair, '4h'))
        return informative

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """计算各周期特征并合并."""
        if not self.dp:
            return dataframe

        pair = metadata['pair']
        
        # 1. 获取 4h 数据并计算 ATR(14)、H、L、ER
        inf_4h = self.dp.get_pair_dataframe(pair, '4h')
        if not inf_4h.empty:
            highs_4h = inf_4h['high'].values
            lows_4h = inf_4h['low'].values
            closes_4h = inf_4h['close'].values
            
            # 计算 Wilder ATR(14)
            inf_4h['atr_4h'] = compute_wilder_atr(highs_4h, lows_4h, closes_4h, period=14)
            
            # 42 根 4h 的最高 High 与最低 Low
            inf_4h['box_h'] = inf_4h['high'].rolling(window=42).max()
            inf_4h['box_l'] = inf_4h['low'].rolling(window=42).min()
            
            # 42 根 ER 效率比
            er_vals = np.full(len(inf_4h), np.nan)
            for i in range(41, len(inf_4h)):
                window_closes = closes_4h[i - 41: i + 1]
                er_vals[i] = compute_efficiency_ratio(window_closes) or np.nan
            inf_4h['er_4h'] = er_vals
            
            # 跟踪止损基础：最近 10 根 4h 的最低价
            inf_4h['trailing_low_10_4h'] = inf_4h['low'].rolling(window=10).min()
            
            # 箱体资格判断: 2 <= (H-L)/A <= 8 且 ER <= 0.30
            inf_4h['box_valid'] = (
                (inf_4h['box_h'] - inf_4h['box_l']) >= 2.0 * inf_4h['atr_4h']
            ) & (
                (inf_4h['box_h'] - inf_4h['box_l']) <= 8.0 * inf_4h['atr_4h']
            ) & (
                inf_4h['er_4h'] <= 0.30
            )

            dataframe = merge_informative_pair(dataframe, inf_4h, self.timeframe, '4h', ffill=True)

        # 2. 获取 1h 数据并计算 20 根成交量中位数 V20
        inf_1h = self.dp.get_pair_dataframe(pair, '1h')
        if not inf_1h.empty:
            inf_1h['v20_median'] = inf_1h['volume'].shift(1).rolling(window=20).median()
            dataframe = merge_informative_pair(dataframe, inf_1h, self.timeframe, '1h', ffill=True)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        基于事件链在 15m 上生成入场信号
        严格因果时序: 突破发生在已闭合的 1h bar，订单在下一个 15m 开盘执行
        """
        dataframe['enter_long'] = 0
        dataframe['enter_tag'] = ''

        # 检查是否具备必要字段
        required_cols = ['box_valid_4h', 'box_h_4h', 'box_l_4h', 'atr_4h_4h', 'close_1h', 'volume_1h', 'v20_median_1h']
        for col in required_cols:
            if col not in dataframe.columns:
                return dataframe

        # 简化版突破信号检测 (用于基线框架跑通，后续根据真实状态机事件进一步精细对齐)
        # 突破条件: 4h 箱体有效，1h 收盘突破 H + 0.1A，成交量 >= 1.5 * V20
        breakout_cond = (
            (dataframe['box_valid_4h'] == True) &
            (dataframe['close_1h'] > dataframe['box_h_4h'] + 0.1 * dataframe['atr_4h_4h']) &
            (dataframe['volume_1h'] >= 1.5 * dataframe['v20_median_1h'])
        )

        # 仅在突破发生后的第一个 15m bar 触发入场
        entry_trigger = breakout_cond & (~breakout_cond.shift(1).fillna(False))
        dataframe.loc[entry_trigger, 'enter_long'] = 1
        dataframe.loc[entry_trigger, 'enter_tag'] = 'wyckoff_breakout_v0_1'

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """主退出信号置 0，全部通过 custom_stoploss 和 custom_exit 管理."""
        dataframe['exit_long'] = 0
        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        """
        入场跳空防护校验:
        - 开盘价不得高于突破小时收盘 + 0.5A
        - 开盘价不得低于 H
        """
        # 获取当前 15m 最新数据
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df.empty:
            return True
        last_row = df.iloc[-1]
        
        box_h = last_row.get('box_h_4h', 0.0)
        atr = last_row.get('atr_4h_4h', 0.0)
        breakout_close = last_row.get('close_1h', rate)

        # 跳空限制校验
        if rate > breakout_close + 0.5 * atr:
            return False
        if rate < box_h:
            return False

        return True

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        """
        仓位风险预算:
        - 每笔预算风险为提交时账户权益的 0.5%
        - 单仓名义金额上限为权益的 30%
        """
        total_equity = self.wallets.get_total_stake_amount()
        risk_budget = total_equity * 0.005  # 0.5% 风险

        # 获取 ATR 估算初始止损距离 (默认约为 1.5 * ATR)
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        atr = df.iloc[-1].get('atr_4h_4h', current_rate * 0.05) if not df.empty else current_rate * 0.05
        stop_dist = max(atr * 1.5, current_rate * 0.01)

        # 名义金额计算: risk_budget / (stop_dist / current_rate)
        stake_amount = risk_budget * (current_rate / stop_dist)

        # 单仓名义金额最多权益 30%
        max_position_stake = total_equity * 0.30
        stake_amount = min(stake_amount, max_position_stake, max_stake)
        if min_stake:
            stake_amount = max(stake_amount, min_stake)

        return float(stake_amount)

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, after_fill: bool,
                        **kwargs) -> Optional[float]:
        """
        动态止损管理:
        1. 初始止损: P0 = Entry - R (约为 1.5 * ATR)
        2. 跟踪止损: 首次达到 +2R 后启用，每 4h 取最近 10 根 4h 最低价，只升不降
        """
        # 从 trade 记录中获取入场价格
        entry_price = trade.open_rate

        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df.empty:
            return -0.05

        last_row = df.iloc[-1]
        atr = last_row.get('atr_4h_4h', entry_price * 0.05)
        r_unit = max(atr * 1.5, entry_price * 0.01)

        # 判断是否达到 2R 激活阈值
        highest_rate = trade.max_rate or current_rate
        is_trailing_activated = (highest_rate >= entry_price + 2.0 * r_unit)

        if is_trailing_activated:
            # 跟踪止损模式: 取最近 10 根 4h 的最低 low
            trailing_stop_px = last_row.get('trailing_low_10_4h_4h', entry_price - r_unit)
            # 只上调不下调
            stop_price = max(trailing_stop_px, entry_price - r_unit)
        else:
            # 初始固定止损
            stop_price = entry_price - r_unit

        # 换算为相对于当前价格的负比例
        if current_rate <= 0:
            return -0.05
        ratio = (stop_price - current_rate) / current_rate
        return min(ratio, -0.001)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        """
        超时退出:
        最长持仓 10 个自然日 (240 小时)，到期按下一根 15m 开盘退出
        """
        hold_hours = (current_time - trade.open_date_utc).total_seconds() / 3600.0
        if hold_hours >= 240.0:
            return 'timeout_10d'
        return None

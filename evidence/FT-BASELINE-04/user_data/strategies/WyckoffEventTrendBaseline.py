# -*- coding: utf-8 -*-
"""FT-BASELINE-04 官方标准策略实现：WyckoffEventTrendBaseline

严格复用原策略状态机与规则契约（P0与资金费最小收尾）：
1. 复用 EventStateMachine、Wilder ATR(14)、ER、V20、6h OI增幅；
2. 决策时钟与候选时序严格对齐 (1h突破收盘确认时刻 snapshot_time_ms 产生信号，下一根15m开盘入场)；
3. 价格区间过滤 [H, min(H+0.5A, breakout_close+0.5A)]；
4. 仓位公式 qty = equity * 0.005 / (entry - P0)，配合单仓30%、总名义90%、流动性0.1%截断；
5. 【核心整改·P0强绑定与防假止损】：
   - 开仓前 confirm_trade_entry 校验：必须存在有效候选且 P0 > 0 且 P0 < rate，否则立即拒单并记录原因；
   - 止损回调 custom_stoploss 校验：彻底废除 (actual_entry * 0.90) 防御性假止损！若发生绑定异常直接抛出 RuntimeError 中止回测，标记运行无效；
   - 严格依照冻结规则 §4 实现 2R 跟踪止损：入场后已收盘 4h bar 的 close >= actual_entry + 2.0 * actual_R 触发，抬升至已收盘 10 根 4h 最低 low (L10)，只升不降；
6. 240小时超时出场。
"""
from __future__ import annotations

import logging
import math
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from pandas import DataFrame

# 加入项目根目录以导入原策略实现
BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade
from freqtrade.exchange import Binance

# ── 历史回测退市币种保护钩子 ──
# 2026年 Binance API 将 2025年历史币种 PUMPBTC 标记为 active=False。
# 为确保冻结测试样本在历史回测时段（2025-08-01 ~ 2025-12-31）完整参与撮合，
# 策略模块在内存层包装 reload_markets，将回测白名单币种标记为活跃，0 修改 Freqtrade 核心库。
_orig_reload_markets = Binance.reload_markets

def _patched_reload_markets(self, *args, **kwargs):
    _orig_reload_markets(self, *args, **kwargs)
    for p in ["PUMPBTC/USDT:USDT", "AR/USDT:USDT", "BANANA/USDT:USDT", "BAT/USDT:USDT", "CTSI/USDT:USDT"]:
        if p in self._markets:
            self._markets[p]["active"] = True

Binance.reload_markets = _patched_reload_markets

logger = logging.getLogger(__name__)


def _to_timestamp_ms(dt: datetime) -> int:
    """安全将 datetime 转为 UTC 毫秒时间戳。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class WyckoffEventTrendBaseline(IStrategy):
    INTERFACE_VERSION = 3

    # 主执行周期 15m
    timeframe = "15m"
    can_short = False

    # 原策略依靠 P0 止损、2R 动态跟踪止损与 240h 超时出场
    minimal_roi = {
        "0": 100.0
    }
    stoploss = -0.99
    use_custom_stoploss = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # 预热需要至少 50 根 4h 柱以完成 42 箱体 + 14 ATR 预热 (约 200 小时 = 800 根 15m)
    startup_candle_count: int = 800

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.data_dir = config.get("datadir", os.path.join(BASE_DIR, "evidence/FT-BASELINE-04/user_data/data/binance"))
        # 缓存每个 pair 的候选及元数据
        self.pair_candidates: Dict[str, list] = {}
        self.pair_4h_df: Dict[str, pd.DataFrame] = {}
        self.quote_volume_cache: Dict[str, pd.Series] = {}
        # 跟踪每个持仓的动态止损位与 2R 激活状态: trade_id -> dict
        self.active_trade_stop_state: Dict[int, dict] = {}

        # ── 历史回测退市标的活跃度补全（非侵入式运行时适配）──
        import gc
        from freqtrade.exchange import Exchange
        for obj in gc.get_objects():
            if isinstance(obj, Exchange) and hasattr(obj, "_markets") and isinstance(obj._markets, dict):
                for pair_key in ["PUMPBTC/USDT:USDT", "AR/USDT:USDT", "BANANA/USDT:USDT", "BAT/USDT:USDT", "CTSI/USDT:USDT"]:
                    if pair_key in obj._markets:
                        obj._markets[pair_key]["active"] = True
                        if hasattr(obj, "_api") and hasattr(obj._api, "markets") and pair_key in obj._api.markets:
                            obj._api.markets[pair_key]["active"] = True

    def informative_pairs(self) -> List[Tuple[str, str]]:
        """向 DataProvider 声明需要 4h 与 1h 周期数据。"""
        pairs = self.config["exchange"]["pair_whitelist"]
        informative = []
        for pair in pairs:
            informative.append((pair, "4h"))
            informative.append((pair, "1h"))
        return informative

    def _load_oi_and_sidecar(self, pair: str) -> Tuple[np.ndarray, np.ndarray, pd.Series]:
        """加载 5m OI 序列与 15m quote volume sidecar。"""
        slug = pair.replace("/", "_").replace(":", "_")
        candidates_oi = [
            os.path.join(self.data_dir, f"futures/{slug}-5m-open_interest.feather"),
            os.path.join(self.data_dir, f"{slug}-5m-open_interest.feather"),
            os.path.join(BASE_DIR, f"evidence/FT-BASELINE-04/user_data/data/binance/futures/{slug}-5m-open_interest.feather"),
            os.path.join(BASE_DIR, f"evidence/FT-DATA-01/data/freqtrade/futures/{slug}-5m-open_interest.feather"),
        ]
        
        f_oi = next((p for p in candidates_oi if os.path.exists(p)), None)
        if f_oi:
            df_oi = pd.read_feather(f_oi)
            oi_ts = df_oi["date"].astype("int64").to_numpy()
            oi_vals = df_oi["open_interest_amount"].to_numpy(dtype=np.float64)
        else:
            logger.warning(f"[{pair}] 未找到 5m OI 数据！")
            oi_ts = np.array([], dtype=np.int64)
            oi_vals = np.array([], dtype=np.float64)

        # 加载 sidecar quote volume
        f_sidecar = os.path.join(BASE_DIR, f"evidence/FT-DATA-01/data/freqtrade/sidecar/{slug}-15m-quote_volume.csv")
        qv_series = pd.Series(dtype=np.float64)
        if os.path.exists(f_sidecar):
            df_sc = pd.read_csv(f_sidecar)
            sc_ts = pd.to_datetime(df_sc["date"], utc=True).astype("int64")
            qv_series = pd.Series(df_sc["quote_volume"].values, index=sc_ts)

        return oi_ts, oi_vals, qv_series

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        symbol_clean = pair.split("/")[0]

        # 1. 获取 4h 与 1h 数据
        df_4h = self.dp.get_pair_dataframe(pair=pair, timeframe="4h")
        df_1h = self.dp.get_pair_dataframe(pair=pair, timeframe="1h")

        if df_4h.empty or df_1h.empty:
            logger.warning(f"Pair {pair} 缺少 4h 或 1h 数据，直接返回")
            dataframe["cand_valid"] = 0
            return dataframe

        # 计算 4h ATR(14)
        df_4h = df_4h.copy()
        highs_4h = df_4h["high"].to_numpy(dtype=np.float64)
        lows_4h = df_4h["low"].to_numpy(dtype=np.float64)
        closes_4h = df_4h["close"].to_numpy(dtype=np.float64)
        df_4h["atr"] = compute_wilder_atr_series(highs_4h, lows_4h, closes_4h, period=14)

        # 4h 包含当根及前9根共10根的滚动最低 low
        df_4h["l10"] = df_4h["low"].rolling(10).min()
        df_4h["open_ms"] = df_4h["date"].astype("int64")
        df_4h["close_ms"] = df_4h["open_ms"] + 4 * 3600 * 1000

        # 保存 4h 关键序列供 custom_stoploss 查找
        self.pair_4h_df[pair] = df_4h[["close_ms", "close", "low", "l10"]].copy()

        # 加载 5m OI 与 quote_volume
        oi_ts, oi_vals, qv_series = self._load_oi_and_sidecar(pair)
        self.quote_volume_cache[pair] = qv_series

        # 2. 驱动原策略 EventStateMachine
        sm = EventStateMachine(config=EventTrendConfig(), symbol=f"{symbol_clean}USDT", enable_oi_filter=True)
        sm.pit.register_oi(oi_ts, oi_vals)

        df_1h = df_1h.copy()
        df_1h["open_time_ms"] = df_1h["date"].astype("int64")
        df_1h["close_time_ms"] = df_1h["open_time_ms"] + 3600 * 1000

        records_1h = df_1h.to_dict("records")
        for r in records_1h:
            t_open = int(r["open_time_ms"])
            t_close = int(r["close_time_ms"])

            # 4h 节点检查建箱体
            if t_close % (4 * 3600 * 1000) == 0:
                sub_4h = df_4h[df_4h["close_ms"] <= t_close]
                if len(sub_4h) >= 42:
                    last_42 = sub_4h.tail(42)
                    atr_val = float(sub_4h["atr"].iloc[-1])
                    sm.check_and_form_box(
                        bar_4h_close_ms=t_close,
                        history_42_highs=last_42["high"].to_numpy(np.float64),
                        history_42_lows=last_42["low"].to_numpy(np.float64),
                        history_42_closes=last_42["close"].to_numpy(np.float64),
                        current_atr_a=atr_val
                    )

            # 推进 1h bar
            sm.process_1h_bar(
                open_time_ms=t_open,
                open_p=float(r["open"]),
                high_p=float(r["high"]),
                low_p=float(r["low"]),
                close_p=float(r["close"]),
                volume_v=float(r["volume"]),
                oi_timestamps=oi_ts,
                oi_values=oi_vals
            )

        self.pair_candidates[pair] = sm.candidates
        logger.info(f"[{pair}] 状态机推进完成，产出候选数: {len(sm.candidates)}")

        # 3. 将候选信息投射到 15m dataframe
        dataframe["cand_valid"] = 0
        dataframe["cand_p0"] = np.nan
        dataframe["cand_h"] = np.nan
        dataframe["cand_max_price"] = np.nan
        dataframe["cand_r"] = np.nan
        dataframe["cand_id"] = ""

        df_dates_ms = dataframe["date"].astype("int64")

        for c in sm.candidates:
            # 候选突破确认时刻 (1h 收盘): snapshot_time_ms
            # 最早执行时刻 (15m 开盘): execution_earliest_ms
            t_sig_start = c.snapshot_time_ms
            t_sig_end = t_sig_start + 4 * 15 * 60 * 1000  # 4 根 15m 有效期

            mask = (df_dates_ms >= t_sig_start) & (df_dates_ms < t_sig_end)
            dataframe.loc[mask, "cand_valid"] = 1
            dataframe.loc[mask, "cand_p0"] = c.initial_stop_price
            dataframe.loc[mask, "cand_h"] = c.box_high_h
            dataframe.loc[mask, "cand_max_price"] = c.max_entry_price
            dataframe.loc[mask, "cand_r"] = c.initial_risk_r
            dataframe.loc[mask, "cand_id"] = c.candidate_id

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 入场条件：
        # 1. 处于候选有效窗口 (cand_valid == 1)
        # 2. 价格处于有效区间：[H, max_entry_price]
        mask = (
            (dataframe["cand_valid"] == 1) &
            (dataframe["open"] >= dataframe["cand_h"]) &
            (dataframe["open"] <= dataframe["cand_max_price"])
        )

        dataframe.loc[mask, "enter_long"] = 1
        dataframe.loc[mask, "enter_tag"] = dataframe.loc[mask, "cand_id"]

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        """开仓前候选与 P0 严格校验：缺少有效候选/P0时，直接拒绝入场并记录明确原因。"""
        cands = self.pair_candidates.get(pair, [])
        cand = next((c for c in cands if c.candidate_id == entry_tag), None)
        if not cand:
            curr_ms = _to_timestamp_ms(current_time)
            cand = next((c for c in cands if c.snapshot_time_ms <= curr_ms < c.snapshot_time_ms + 3600*1000), None)

        if not cand:
            logger.error(f"[{pair}] 开仓拒单：未找到与 entry_tag={entry_tag} 匹配的有效候选！拒绝入场！")
            return False

        if cand.initial_stop_price is None or math.isnan(cand.initial_stop_price) or cand.initial_stop_price <= 0:
            logger.error(f"[{pair}] 开仓拒单：候选 {cand.candidate_id} 缺少有效 P0 ({cand.initial_stop_price})！拒绝入场！")
            return False

        if cand.initial_stop_price >= rate:
            logger.error(f"[{pair}] 开仓拒单：候选 {cand.candidate_id} 的 P0 ({cand.initial_stop_price}) >= 入场价 ({rate})！拒绝入场！")
            return False

        # 跳空滑点保护：若实际入场价超过 max_entry_price 则拒单
        if rate > cand.max_entry_price:
            logger.warning(f"[{pair}] 开仓拒单：入场价 {rate} 超过上限 {cand.max_entry_price}")
            return False

        return True

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        """精确实现原策略仓位规则：
        qty = (equity * 0.005) / (rate - P0)
        stake = qty * rate
        并实施单仓 30% 截断与 quote volume 0.1% 流动性截断
        """
        cands = self.pair_candidates.get(pair, [])
        cand = next((c for c in cands if c.candidate_id == entry_tag), None)
        if not cand:
            curr_ms = _to_timestamp_ms(current_time)
            cand = next((c for c in cands if c.snapshot_time_ms <= curr_ms < c.snapshot_time_ms + 3600*1000), None)

        if not cand:
            return 0.0

        p0 = cand.initial_stop_price
        r_dist = current_rate - p0
        if r_dist <= 0:
            return 0.0

        # 获取当前总权益
        equity = self.wallets.get_total_stake_amount()
        risk_budget = equity * 0.005
        qty = risk_budget / r_dist
        raw_stake = qty * current_rate

        # 约束 1：单仓名义金额不超过权益的 30%
        max_single = equity * 0.30
        stake = min(raw_stake, max_single)

        # 约束 2：流动性截断（不超过前 15m quote volume 的 0.1%）
        qv_cache = self.quote_volume_cache.get(pair)
        if qv_cache is not None and not qv_cache.empty:
            curr_ms = _to_timestamp_ms(current_time)
            prior_vols = qv_cache.loc[qv_cache.index < curr_ms]
            if not prior_vols.empty:
                prev_qv = prior_vols.iloc[-1]
                if prev_qv > 0:
                    liq_cap = prev_qv * 0.001
                    stake = min(stake, liq_cap)

        return float(stake)

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, after_fill: bool,
                        **kwargs) -> Optional[float]:
        """严格落实原策略冻结规则 §4 的动态止损（P0强绑定，严禁使用假止损）：
        1. 必须精准绑定候选提取真实 P0；若发生绑定异常直接抛出 RuntimeError 中止运行，标记运行无效！
        2. 必须使用入场后【已收盘 4h bar 的 close】判定 2R 激活，绝不用盘中最高价；
        3. 激活条件：close_4h >= actual_entry_price + 2.0 * actual_R；
        4. 激活后，止损位抬升至当前已收盘 10 根 4h 的最低 low (L10)，只上调不下调；
        5. 未激活 2R 时坚决维持真实 P0。
        """
        # 获取候选信息以提取真实 P0
        cands = self.pair_candidates.get(pair, [])
        cand = next((c for c in cands if c.candidate_id == trade.enter_tag), None)
        if not cand:
            trade_open_ms = _to_timestamp_ms(trade.open_date_utc)
            cand = next((c for c in cands if c.snapshot_time_ms <= trade_open_ms < c.snapshot_time_ms + 3600*1000), None)

        if not cand or cand.initial_stop_price is None or math.isnan(cand.initial_stop_price) or cand.initial_stop_price <= 0:
            err_msg = (f"[{pair}] 持仓 trade_id={trade.id} 候选或 P0 绑定异常！"
                       f"严禁静默使用假止损！cand={cand}")
            logger.error(err_msg)
            raise RuntimeError(err_msg)

        actual_entry = float(trade.open_rate)
        p0 = float(cand.initial_stop_price)
        actual_r = actual_entry - p0
        trigger_2r = actual_entry + 2.0 * actual_r

        # 初始化或获取该持仓的动态状态
        t_id = trade.id or 0
        state = self.active_trade_stop_state.get(t_id)
        if state is None:
            state = {
                "cand_id": cand.candidate_id,
                "p0": p0,
                "current_stop": p0,
                "is_2r_activated": False,
                "entry_time_ms": _to_timestamp_ms(trade.open_date_utc)
            }
            self.active_trade_stop_state[t_id] = state

        current_ms = _to_timestamp_ms(current_time)
        entry_ms = state["entry_time_ms"]

        # 检查入场后已收盘的 4h bar
        df_4h_info = self.pair_4h_df.get(pair)
        if df_4h_info is not None and not df_4h_info.empty:
            # 必须严格满足：4h 收盘时间 <= 当前时间，且 4h 收盘时间 >= 入场时间 (避免入场前的历史 4h 误激活)
            sub_4h = df_4h_info[(df_4h_info["close_ms"] <= current_ms) & (df_4h_info["close_ms"] >= entry_ms)]
            if not sub_4h.empty:
                # 检查 2R 激活状态 (只要期间任意一根已收盘 4h 的 close >= trigger_2r)
                if not state["is_2r_activated"]:
                    max_4h_close = float(sub_4h["close"].max())
                    if max_4h_close >= trigger_2r:
                        state["is_2r_activated"] = True
                        logger.info(f"[{pair}] 持仓 {t_id} (候选 {cand.candidate_id}) 4h 收盘价 {max_4h_close} 突破 2R 阈值 {trigger_2r}，正式激活跟踪止损！")

                # 如果已激活，获取最新已收盘 4h 的 L10 进行抬升（只上调不下调）
                if state["is_2r_activated"]:
                    latest_l10 = float(sub_4h["l10"].iloc[-1])
                    if math.isfinite(latest_l10) and latest_l10 > state["current_stop"]:
                        state["current_stop"] = latest_l10

        effective_stop = state["current_stop"]

        # 换算为相对于 current_rate 的负值比例
        if current_rate <= effective_stop:
            return 0.0  # 触发立即市价止损出场

        stoploss_ratio = (effective_stop - current_rate) / current_rate
        return float(stoploss_ratio)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        # 超时出场：持仓达到 240 小时强平
        holding_seconds = (_to_timestamp_ms(current_time) - _to_timestamp_ms(trade.open_date_utc)) / 1000.0
        if holding_seconds >= 240 * 3600:
            return "timeout_240h"
        return None

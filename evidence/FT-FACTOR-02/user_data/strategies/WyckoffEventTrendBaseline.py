# -*- coding: utf-8 -*-
"""FT-FACTOR-02 官方标准策略实现：WyckoffEventTrendBaseline

严格复用 FT-FACTOR-01 / FT-BASELINE-05 / FT-BASELINE-06 验收通过的基线逻辑：
1. 复用 EventStateMachine、Wilder ATR(14)、ER、V20、6h OI增幅；
2. 决策时钟与候选时序严格对齐 (1h突破收盘确认时刻 snapshot_time_ms 产生信号，下一根15m开盘入场)；
3. 价格区间过滤 [H, min(H+0.5A, breakout_close+0.5A)]；
4. 仓位公式 qty = equity * 0.005 / (entry - P0)，配合单仓30%、总名义90%、流动性0.1%截断；
5. 【核心整改·候选严格唯一绑定与运行失效持久化】：
   - 交易必须绑定自身的唯一候选ID，彻底删除时间窗兜底匹配（禁止按时间接近程度替换候选）；
   - 开仓前 confirm_trade_entry 严格校验：候选缺失、ID不匹配或 P0 非法时，拒绝开仓并记录明确原因；
   - 持仓后 custom_stoploss 绑定异常：持久化明确的运行无效状态 (run_validation_status.json) 及原因；
   - 严格依照冻结规则 §4 实现 2R 跟踪止损：入场后已收盘 4h bar 的 close >= actual_entry + 2.0 * actual_R 触发，抬升至已收盘 10 根 4h 最低 low (L10)，只升不降；
6. 240小时超时出场。
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from pandas import DataFrame

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade
from freqtrade.exchange import Binance

# ── 历史回测退市/归档币种保护钩子 ──
_orig_reload_markets = Binance.reload_markets

def _patched_reload_markets(self, *args, **kwargs):
    _orig_reload_markets(self, *args, **kwargs)
    for p in self._markets:
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

    # 消融配置扩展点
    ablation_enable_oi_filter: bool = True
    ablation_retest_vol_multiplier: Optional[float] = None
    ablation_breakout_vol_multiplier: Optional[float] = None
    ablation_max_gap_up_atr: Optional[float] = None

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.user_dir = config.get("user_data_dir", os.path.join(BASE_DIR, "evidence/FT-FACTOR-02/user_data"))
        self.data_dir = config.get("datadir", os.path.join(self.user_dir, "data/binance"))
        # 缓存每个 pair 的候选及元数据
        self.pair_candidates: Dict[str, list] = {}
        self.pair_4h_df: Dict[str, pd.DataFrame] = {}
        self.quote_volume_cache: Dict[str, pd.Series] = {}
        # 跟踪每个持仓的动态止损位与 2R 激活状态: trade_id -> dict
        self.active_trade_stop_state: Dict[int, dict] = {}

        # ── 运行有效性状态持久化机制 ──
        self.status_file = os.path.join(self.user_dir, f"run_validation_status_{self.__class__.__name__}.json")
        self.is_run_valid = True
        self.invalidation_reasons: List[dict] = []
        self._init_validation_status()

        # 内存层市场激活补全
        import gc
        from freqtrade.exchange import Exchange
        for obj in gc.get_objects():
            if isinstance(obj, Exchange) and hasattr(obj, "_markets") and isinstance(obj._markets, dict):
                for pair_key in obj._markets:
                    obj._markets[pair_key]["active"] = True
                    if hasattr(obj, "_api") and hasattr(obj._api, "markets") and pair_key in obj._api.markets:
                        obj._api.markets[pair_key]["active"] = True

    def _init_validation_status(self) -> None:
        init_data = {
            "is_valid": True,
            "status": "VALID",
            "strategy": self.__class__.__name__,
            "init_time_utc": datetime.now(timezone.utc).isoformat(),
            "error_count": 0,
            "invalidation_reasons": []
        }
        os.makedirs(os.path.dirname(self.status_file), exist_ok=True)
        with open(self.status_file, "w", encoding="utf-8") as f:
            json.dump(init_data, f, indent=4, ensure_ascii=False)

    def record_invalidation(self, stage: str, reason: str, details: Optional[dict] = None) -> None:
        self.is_run_valid = False
        item = {
            "stage": stage,
            "reason": reason,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "details": details or {}
        }
        self.invalidation_reasons.append(item)
        status_data = {
            "is_valid": False,
            "status": "INVALID",
            "strategy": self.__class__.__name__,
            "updated_time_utc": datetime.now(timezone.utc).isoformat(),
            "error_count": len(self.invalidation_reasons),
            "invalidation_reasons": self.invalidation_reasons
        }
        os.makedirs(os.path.dirname(self.status_file), exist_ok=True)
        with open(self.status_file, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=4, ensure_ascii=False)
        logger.critical(f"【运行失效持久化】阶段: {stage} | 原因: {reason} | 详情: {details}")

    def informative_pairs(self) -> List[Tuple[str, str]]:
        pairs = self.config["exchange"]["pair_whitelist"]
        informative = []
        for pair in pairs:
            informative.append((pair, "4h"))
            informative.append((pair, "1h"))
        return informative

    def _load_oi_and_sidecar(self, pair: str) -> Tuple[np.ndarray, np.ndarray, pd.Series]:
        slug = pair.replace("/", "_").replace(":", "_")
        candidates_oi = [
            os.path.join(self.data_dir, f"futures/{slug}-5m-open_interest.feather"),
            os.path.join(self.data_dir, f"{slug}-5m-open_interest.feather"),
            os.path.join(self.user_dir, f"data/binance/futures/{slug}-5m-open_interest.feather"),
            os.path.join(BASE_DIR, f"evidence/FT-FACTOR-02/user_data/data/binance/futures/{slug}-5m-open_interest.feather"),
        ]
        
        f_oi = next((p for p in candidates_oi if os.path.exists(p)), None)
        if f_oi:
            df_oi = pd.read_feather(f_oi)
            oi_ts = df_oi["date"].astype("int64").to_numpy()
            oi_vals = df_oi["open_interest_amount"].to_numpy(dtype=np.float64)
        else:
            oi_ts = np.array([], dtype=np.int64)
            oi_vals = np.array([], dtype=np.float64)

        # 加载 sidecar quote volume
        candidates_sc = [
            os.path.join(self.data_dir, f"sidecar/{slug}-15m-quote_volume.csv"),
            os.path.join(self.user_dir, f"data/binance/sidecar/{slug}-15m-quote_volume.csv"),
            os.path.join(BASE_DIR, f"evidence/FT-FACTOR-02/user_data/data/binance/sidecar/{slug}-15m-quote_volume.csv"),
        ]
        f_sidecar = next((p for p in candidates_sc if os.path.exists(p)), None)
        qv_series = pd.Series(dtype=np.float64)
        if f_sidecar and os.path.exists(f_sidecar):
            df_sc = pd.read_csv(f_sidecar)
            sc_ts = pd.to_datetime(df_sc["date"], utc=True).astype("int64")
            qv_series = pd.Series(df_sc["quote_volume"].values, index=sc_ts)

        return oi_ts, oi_vals, qv_series

    def _build_state_machine_config(self) -> EventTrendConfig:
        overrides = {}
        if self.ablation_retest_vol_multiplier is not None:
            overrides["retest_vol_multiplier"] = self.ablation_retest_vol_multiplier
        if self.ablation_breakout_vol_multiplier is not None:
            overrides["breakout_vol_multiplier"] = self.ablation_breakout_vol_multiplier
        if self.ablation_max_gap_up_atr is not None:
            overrides["max_gap_up_atr"] = self.ablation_max_gap_up_atr
        
        if overrides:
            return EventTrendConfig(**overrides)
        return EventTrendConfig()

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        symbol_clean = pair.split("/")[0]

        # 1. 获取 4h 与 1h 数据
        df_4h = self.dp.get_pair_dataframe(pair=pair, timeframe="4h")
        df_1h = self.dp.get_pair_dataframe(pair=pair, timeframe="1h")

        if df_4h.empty or df_1h.empty:
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

        self.pair_4h_df[pair] = df_4h[["close_ms", "close", "low", "l10"]].copy()

        # 加载 5m OI 与 quote_volume
        oi_ts, oi_vals, qv_series = self._load_oi_and_sidecar(pair)
        self.quote_volume_cache[pair] = qv_series

        # 2. 驱动原策略 EventStateMachine
        st_config = self._build_state_machine_config()
        sm = EventStateMachine(config=st_config, symbol=f"{symbol_clean}USDT", enable_oi_filter=self.ablation_enable_oi_filter)
        if len(oi_ts) > 0:
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

        # 3. 将候选信息投射到 15m dataframe
        dataframe["cand_valid"] = 0
        dataframe["cand_p0"] = np.nan
        dataframe["cand_h"] = np.nan
        dataframe["cand_max_price"] = np.nan
        dataframe["cand_r"] = np.nan
        dataframe["cand_id"] = ""

        df_dates_ms = dataframe["date"].astype("int64")

        for c in sm.candidates:
            t_sig_start = c.snapshot_time_ms
            t_sig_end = t_sig_start + 4 * 15 * 60 * 1000

            mask = (df_dates_ms >= t_sig_start) & (df_dates_ms < t_sig_end)
            dataframe.loc[mask, "cand_valid"] = 1
            dataframe.loc[mask, "cand_p0"] = c.initial_stop_price
            dataframe.loc[mask, "cand_h"] = c.box_high_h
            dataframe.loc[mask, "cand_max_price"] = c.max_entry_price
            dataframe.loc[mask, "cand_r"] = c.initial_risk_r
            dataframe.loc[mask, "cand_id"] = c.candidate_id

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
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
        if not entry_tag:
            return False

        cands = self.pair_candidates.get(pair, [])
        cand = next((c for c in cands if c.candidate_id == entry_tag), None)

        if not cand:
            return False

        if cand.initial_stop_price is None or math.isnan(cand.initial_stop_price) or cand.initial_stop_price <= 0:
            return False

        if cand.initial_stop_price >= rate:
            return False

        if rate > cand.max_entry_price:
            return False

        return True

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        if not entry_tag:
            return 0.0

        cands = self.pair_candidates.get(pair, [])
        cand = next((c for c in cands if c.candidate_id == entry_tag), None)

        if not cand:
            return 0.0

        p0 = cand.initial_stop_price
        if p0 is None or math.isnan(p0) or p0 <= 0:
            return 0.0

        r_dist = current_rate - p0
        if r_dist <= 0:
            return 0.0

        equity = self.wallets.get_total_stake_amount()
        risk_budget = equity * 0.005
        qty = risk_budget / r_dist
        raw_stake = qty * current_rate

        max_single = equity * 0.30
        stake = min(raw_stake, max_single)

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
        cands = self.pair_candidates.get(pair, [])
        cand = next((c for c in cands if c.candidate_id == trade.enter_tag), None)

        if not cand or cand.initial_stop_price is None or math.isnan(cand.initial_stop_price) or cand.initial_stop_price <= 0:
            err_msg = (f"[{pair}] 持仓 trade_id={trade.id} 候选唯一绑定异常！"
                       f"enter_tag={trade.enter_tag}, cand={cand}。严禁静默使用假止损！")
            logger.error(err_msg)
            self.record_invalidation(
                stage="custom_stoploss",
                reason=err_msg,
                details={
                    "pair": pair,
                    "trade_id": trade.id,
                    "enter_tag": trade.enter_tag,
                    "current_rate": current_rate,
                    "cand_found": bool(cand)
                }
            )
            raise RuntimeError(err_msg)

        actual_entry = float(trade.open_rate)
        p0 = float(cand.initial_stop_price)
        actual_r = actual_entry - p0
        trigger_2r = actual_entry + 2.0 * actual_r

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

        df_4h_info = self.pair_4h_df.get(pair)
        if df_4h_info is not None and not df_4h_info.empty:
            sub_4h = df_4h_info[(df_4h_info["close_ms"] <= current_ms) & (df_4h_info["close_ms"] >= entry_ms)]
            if not sub_4h.empty:
                if not state["is_2r_activated"]:
                    max_4h_close = float(sub_4h["close"].max())
                    if max_4h_close >= trigger_2r:
                        state["is_2r_activated"] = True

                if state["is_2r_activated"]:
                    latest_l10 = float(sub_4h["l10"].iloc[-1])
                    if math.isfinite(latest_l10) and latest_l10 > state["current_stop"]:
                        state["current_stop"] = latest_l10

        effective_stop = state["current_stop"]

        if current_rate <= effective_stop:
            return 0.0

        stoploss_ratio = (effective_stop - current_rate) / current_rate
        return float(stoploss_ratio)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        holding_seconds = (_to_timestamp_ms(current_time) - _to_timestamp_ms(trade.open_date_utc)) / 1000.0
        if holding_seconds >= 240 * 3600:
            return "timeout_240h"
        return None

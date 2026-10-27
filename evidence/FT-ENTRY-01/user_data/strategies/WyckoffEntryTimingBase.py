# -*- coding: utf-8 -*-
"""FT-ENTRY-01 提前入场双路线验证策略基类与状态机

严格落实工单与用户确认规范：
1. A 组（突破前入场）：
   - A1: RETEST 确认后，收盘站上箱体上部区间 (close >= H - 0.5A)，温和放量 (volume >= 1.0 V20)，6h OI >= 2%，初始止损 P0 = Lr - 0.1A
   - A2: RETEST 确认后，收盘突破箱顶基准线 (close >= H)，温和放量 (volume >= 1.0 V20)，6h OI >= 2%，初始止损 P0 = Lr - 0.1A
2. C 组（缩量回踩早期入场）：
   - C1: SWEEP 后首次缩量企稳 (volume <= 0.8 Vs, 收阳, 在回踩区间)，6h OI >= 0%，初始止损 P0 = S - 0.1A (扫低历史点)
   - C2: SWEEP 后首次缩量企稳 (volume <= 0.8 Vs, 收阳, 在回踩区间)，6h OI >= 0%，初始止损 P0 = low_bar - 0.1A (当根低点)
3. 基准组（Baseline）：
   - 原策略完整规则：突破 H + 0.1A，放量 >= 1.5 V20，6h OI >= 2%，初始止损 P0 = Lr - 0.1A
4. 共同约束：
   - 使用已收盘 1h 数据决策，下一根 15m 开盘执行；
   - 严格杜绝借用未来止损点；
   - 失败样本 100% 纳入，禁止反向挑选。
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
from 策略.WyckoffMaster.event_trend_v0_1.contracts import (
    BoxVersion, CandidateSnapshot, EventDossier, EventState, RawTrace, StateTransition
)
from 策略.WyckoffMaster.event_trend_v0_1.features import (
    OI_BUCKET_MS, OI_STATUS_ASSUMED, compute_efficiency_ratio,
    compute_v20_median_volume, compute_wilder_atr_series
)
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine, HOUR_MS
from 策略.WyckoffMaster.trading_core.pit_guard import DecisionClock, PitMode

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade
from freqtrade.exchange import Binance

logger = logging.getLogger(__name__)


def _to_timestamp_ms(dt: datetime) -> int:
    """安全将 datetime 转为 UTC 毫秒时间戳。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class TimingEventStateMachine(EventStateMachine):
    """支持提前入场双路线扩展的确定性状态机。"""

    def __init__(
        self,
        config: Optional[EventTrendConfig] = None,
        symbol: str = "SOLUSDT",
        venue: str = "binance",
        enable_oi_filter: bool = True,
        timing_mode: str = "baseline"
    ):
        super().__init__(config=config, symbol=symbol, enable_oi_filter=enable_oi_filter)
        self.venue = venue
        self.timing_mode = timing_mode

    def process_1h_bar(
        self,
        open_time_ms: int,
        open_p: float,
        high_p: float,
        low_p: float,
        close_p: float,
        volume_v: float,
        oi_timestamps: np.ndarray,
        oi_values: np.ndarray
    ) -> Optional[CandidateSnapshot]:
        """扩展 1h 处理逻辑以支持 A 组与 C 组提前触发。"""
        if open_time_ms in self.processed_1h_bar_opens:
            return None
        self.processed_1h_bar_opens.add(open_time_ms)

        clock = DecisionClock.for_bar(open_time_ms, HOUR_MS, source="1h_bar_close")
        decision_ms = clock.decision_time_ms
        close_time_ms = decision_ms
        self.last_clock = clock

        self.pit.assert_candles_closed(clock.close_time_ms, decision_ms, label="1h")

        self.h1_open_times.append(open_time_ms)
        self.h1_close_times.append(close_time_ms)
        self.h1_opens.append(open_p)
        self.h1_highs.append(high_p)
        self.h1_lows.append(low_p)
        self.h1_closes.append(close_p)
        self.h1_volumes.append(volume_v)

        current_idx = len(self.h1_closes) - 1
        event = self.active_event
        if event is None or close_time_ms <= event.created_at_ms:
            return None

        box = event.box
        H = box.high_h
        L = box.low_l
        A = box.atr_a

        if close_p < L:
            self.consecutive_closes_below_l += 1
        else:
            self.consecutive_closes_below_l = 0

        if close_time_ms - event.created_at_ms > self.config.breakout_max_total_hours * 3600 * 1000:
            self._terminate_event(
                event, EventState.EXPIRED, close_time_ms,
                "事件总体观察超时(>14天)", {"elapsed_hours": (close_time_ms - event.created_at_ms) / 3600000}
            )
            return None

        st = event.current_state

        # ── 阶段 A: BOX 等待 SWEEP ──
        if st == EventState.BOX:
            if close_time_ms - event.created_at_ms > self.config.sweep_max_hours * 3600 * 1000:
                self._terminate_event(
                    event, EventState.EXPIRED, close_time_ms,
                    "建档后7天内未发生扫低", {"elapsed_hours": (close_time_ms - event.created_at_ms) / 3600000}
                )
                return None

            if low_p < (L - self.config.sweep_max_overshoot_atr * A) or self.consecutive_closes_below_l >= 2:
                self._terminate_event(
                    event, EventState.INVALIDATED, close_time_ms,
                    "跌破L-0.5A或连续2根收盘破L",
                    {"low": low_p, "limit": L - self.config.sweep_max_overshoot_atr * A}
                )
                return None

            is_sweep = (
                (L - self.config.sweep_max_overshoot_atr * A <= low_p < L) and
                (close_p >= L + self.config.sweep_min_recovery_atr * A) and
                (close_p <= H)
            )
            if is_sweep:
                event.sweep_time_ms = close_time_ms
                event.sweep_low_s = low_p
                event.sweep_volume_vs = volume_v

                trace = RawTrace(
                    trace_id=f"trc_sweep_{event.event_id}_{close_time_ms}",
                    symbol=self.symbol, venue=self.venue, timeframe="1h",
                    bar_open_ms=open_time_ms, bar_close_ms=close_time_ms, available_at_ms=close_time_ms,
                    trace_type="SWEEP_BAR",
                    price_low=low_p, price_high=high_p, price_close=close_p, volume=volume_v,
                    features={"L": L, "A": A, "overshoot": L - low_p}
                )
                event.traces.append(trace)
                self.traces.append(trace)

                self._make_transition(
                    event, EventState.SWEEP, close_time_ms,
                    "扫低回收确认(破L不超0.5A且同根收回L+0.1A)",
                    {"sweep_low": low_p, "sweep_volume": volume_v, "close": close_p}
                )
            return None

        # ── 阶段 B: SWEEP 等待 RETEST (包含 C1 / C2 早期入场触发点) ──
        elif st == EventState.SWEEP:
            assert event.sweep_time_ms is not None
            assert event.sweep_low_s is not None
            assert event.sweep_volume_vs is not None

            if close_time_ms - event.sweep_time_ms > self.config.retest_max_hours * 3600 * 1000:
                self._terminate_event(
                    event, EventState.EXPIRED, close_time_ms,
                    "扫低后72小时内未发生缩量回踩", {"elapsed_hours": (close_time_ms - event.sweep_time_ms) / 3600000}
                )
                return None

            if low_p < (event.sweep_low_s - self.config.retest_fail_buffer_atr * A) or self.consecutive_closes_below_l >= 2:
                self._terminate_event(
                    event, EventState.INVALIDATED, close_time_ms,
                    "跌破S-0.1A或连续2根收盘破L",
                    {"low": low_p, "s_limit": event.sweep_low_s - self.config.retest_fail_buffer_atr * A}
                )
                return None

            if close_p > (H + self.config.breakout_buffer_atr * A):
                self._terminate_event(
                    event, EventState.MISSED_WITHOUT_RETEST, close_time_ms,
                    "回踩前价格已收盘突破H+0.1A(missed_without_retest)",
                    {"close": close_p, "h_limit": H + self.config.breakout_buffer_atr * A}
                )
                return None

            is_retest = (
                (L <= low_p <= L + self.config.retest_max_zone_atr * A) and
                (low_p > event.sweep_low_s) and
                (close_p >= L + self.config.retest_recovery_atr * A) and
                (close_p >= open_p) and
                (volume_v <= self.config.retest_vol_multiplier * event.sweep_volume_vs)
            )

            if is_retest:
                event.retest_time_ms = close_time_ms
                event.retest_low_lr = low_p

                trace = RawTrace(
                    trace_id=f"trc_retest_{event.event_id}_{close_time_ms}",
                    symbol=self.symbol, venue=self.venue, timeframe="1h",
                    bar_open_ms=open_time_ms, bar_close_ms=close_time_ms, available_at_ms=close_time_ms,
                    trace_type="RETEST_BAR",
                    price_low=low_p, price_high=high_p, price_close=close_p, volume=volume_v,
                    features={"Lr": low_p, "Vs": event.sweep_volume_vs, "vol_ratio": volume_v / event.sweep_volume_vs}
                )
                event.traces.append(trace)
                self.traces.append(trace)

                self._make_transition(
                    event, EventState.RETEST, close_time_ms,
                    "缩量回踩确认(在L~L+0.5A高于S且收阳量<=0.8Vs)",
                    {"retest_low": low_p, "volume": volume_v, "vs": event.sweep_volume_vs}
                )

                # ── C 组触发点：缩量回踩首次企稳即刻生成候选！──
                if self.timing_mode in ("C1", "C2"):
                    # 检查 C-OI-1 条件：6h OI 增幅 >= 0%
                    if self.pit.oi_available_at is None:
                        self.pit.register_oi(oi_timestamps, oi_values)
                    oi_res = self.pit.read_oi(
                        decision_ms,
                        lookback_ms=self.config.oi_lookback_hours * 3600 * 1000,
                        max_distance_ms=self.config.oi_max_time_distance_sec * 1000,
                    )
                    oi_curr = oi_res["oi_curr"]
                    oi_past = oi_res["oi_past"]
                    oi_growth = oi_res["growth"]
                    oi_status = oi_res["status"]

                    # 若开启 OI 过滤且未达标（要求 >= 0% 资金正向流入）
                    if self.enable_oi_filter:
                        if oi_growth is None or oi_growth < 0.0:
                            self._terminate_event(
                                event, EventState.OI_REJECTED, close_time_ms,
                                f"回踩阶段6h OI增长为负({(oi_growth or 0)*100:.2f}%)，资金未流入",
                                {"oi_growth": oi_growth}
                            )
                            return None

                    # 确定止损依据
                    if self.timing_mode == "C1":
                        # C1: 以历史已确定的扫低最低价设防 (P0 = S - 0.1A)
                        initial_stop = event.sweep_low_s - self.config.initial_stop_buffer_atr * A
                    else:
                        # C2: 以当根回踩 K 线的绝对低点设防 (P0 = low_p - 0.1A)
                        initial_stop = low_p - self.config.initial_stop_buffer_atr * A

                    initial_risk_r = close_p - initial_stop
                    cand_id = f"cand_{self.timing_mode}_{event.event_id}_{close_time_ms}"
                    earliest_exec = close_time_ms + 15 * 60 * 1000

                    snapshot = CandidateSnapshot.create(
                        candidate_id=cand_id,
                        event_id=event.event_id,
                        strategy_version=f"{self.config.strategy_version}_{self.timing_mode}",
                        symbol=self.symbol,
                        venue=self.venue,
                        snapshot_time_ms=close_time_ms,
                        available_at_ms=close_time_ms,
                        box_high_h=H,
                        box_low_l=L,
                        box_atr_a=A,
                        box_er=box.efficiency_ratio_er,
                        sweep_low_s=event.sweep_low_s,
                        retest_low_lr=low_p,
                        breakout_close=close_p,
                        breakout_volume=volume_v,
                        v20_median_volume=volume_v,
                        volume_ratio=1.0,
                        oi_breakout=oi_curr,
                        oi_6h_ago=oi_past,
                        oi_growth_6h=oi_growth,
                        initial_stop_price=initial_stop,
                        initial_risk_r=initial_risk_r,
                        max_entry_price=close_p + 0.5 * A,
                        min_entry_price=L - 0.2 * A,
                        execution_earliest_ms=earliest_exec,
                        pit_proven=bool(oi_status == "ok"),
                        pit_assumption_note="C组缩量回踩早期入场快照",
                        missing_fields=[]
                    )

                    event.candidate_id = cand_id
                    event.ended_at_ms = close_time_ms
                    event.termination_reason = f"C组提前入场成功({self.timing_mode})"
                    self.completed_events.append(event)
                    self.candidates.append(snapshot)
                    self.active_event = None
                    self.cooling_until_ms = close_time_ms + self.config.cooling_hours * 3600 * 1000
                    return snapshot

            return None

        # ── 阶段 C: RETEST 等待 BREAKOUT (包含 A1 / A2 突破前触发点及 Baseline) ──
        elif st == EventState.RETEST:
            assert event.retest_time_ms is not None
            assert event.retest_low_lr is not None

            if close_time_ms - event.retest_time_ms > self.config.breakout_max_hours_after_retest * 3600 * 1000:
                self._terminate_event(
                    event, EventState.EXPIRED, close_time_ms,
                    "回踩后7天内未发生突破", {"elapsed_hours": (close_time_ms - event.retest_time_ms) / 3600000}
                )
                return None

            if low_p < (event.retest_low_lr - self.config.initial_stop_buffer_atr * A) or self.consecutive_closes_below_l >= 2:
                self._terminate_event(
                    event, EventState.INVALIDATED, close_time_ms,
                    "跌破Lr-0.1A或连续2根收盘破L",
                    {"low": low_p, "lr_limit": event.retest_low_lr - self.config.initial_stop_buffer_atr * A}
                )
                return None

            # 计算 V20
            vols_arr = np.array(self.h1_volumes, dtype=np.float64)
            v20 = compute_v20_median_volume(vols_arr, current_idx, self.config.breakout_vol_lookback)

            # ── A1 / A2 突破前提前入场检验 ──
            if self.timing_mode in ("A1", "A2"):
                target_level = (H - 0.5 * A) if self.timing_mode == "A1" else H
                # 触发条件：收盘达到目标价位，且当根未超过突破上限 H + 0.1A (确保属于突破前)
                is_pre_bo_trigger = (close_p >= target_level) and (close_p <= H + self.config.breakout_buffer_atr * A)

                if is_pre_bo_trigger:
                    # 1. 温和放量检验: volume >= 1.0 * V20
                    if v20 is None or volume_v < 1.0 * v20:
                        return None  # 仅未达量，继续等待下一根，不直接终止

                    # 2. OI 检验: 6h OI 增幅 >= 2%
                    if self.pit.oi_available_at is None:
                        self.pit.register_oi(oi_timestamps, oi_values)
                    oi_res = self.pit.read_oi(
                        decision_ms,
                        lookback_ms=self.config.oi_lookback_hours * 3600 * 1000,
                        max_distance_ms=self.config.oi_max_time_distance_sec * 1000,
                    )
                    oi_curr = oi_res["oi_curr"]
                    oi_past = oi_res["oi_past"]
                    oi_growth = oi_res["growth"]
                    oi_status = oi_res["status"]

                    if self.enable_oi_filter and (oi_growth is None or oi_growth < self.config.oi_min_growth_pct):
                        return None  # OI 未达标继续等待

                    # 3. 满足 A 组提前触发！初始止损沿用历史已确立的 Lr - 0.1A
                    initial_stop = event.retest_low_lr - self.config.initial_stop_buffer_atr * A
                    initial_risk_r = close_p - initial_stop
                    cand_id = f"cand_{self.timing_mode}_{event.event_id}_{close_time_ms}"
                    earliest_exec = close_time_ms + 15 * 60 * 1000

                    snapshot = CandidateSnapshot.create(
                        candidate_id=cand_id,
                        event_id=event.event_id,
                        strategy_version=f"{self.config.strategy_version}_{self.timing_mode}",
                        symbol=self.symbol,
                        venue=self.venue,
                        snapshot_time_ms=close_time_ms,
                        available_at_ms=close_time_ms,
                        box_high_h=target_level,
                        box_low_l=L,
                        box_atr_a=A,
                        box_er=box.efficiency_ratio_er,
                        sweep_low_s=event.sweep_low_s,
                        retest_low_lr=event.retest_low_lr,
                        breakout_close=close_p,
                        breakout_volume=volume_v,
                        v20_median_volume=v20,
                        volume_ratio=volume_v / v20 if v20 else 1.0,
                        oi_breakout=oi_curr,
                        oi_6h_ago=oi_past,
                        oi_growth_6h=oi_growth,
                        initial_stop_price=initial_stop,
                        initial_risk_r=initial_risk_r,
                        max_entry_price=close_p + 0.5 * A,
                        min_entry_price=target_level - 0.1 * A,
                        execution_earliest_ms=earliest_exec,
                        pit_proven=bool(oi_status == "ok"),
                        pit_assumption_note=f"A组突破前提前入场快照({self.timing_mode})",
                        missing_fields=[]
                    )

                    event.candidate_id = cand_id
                    event.ended_at_ms = close_time_ms
                    event.termination_reason = f"A组提前入场成功({self.timing_mode})"
                    self.completed_events.append(event)
                    self.candidates.append(snapshot)
                    self.active_event = None
                    self.cooling_until_ms = close_time_ms + self.config.cooling_hours * 3600 * 1000
                    return snapshot

            # ── Benchmark 原策略突破检查 ──
            if self.timing_mode == "baseline":
                prev_close = self.h1_closes[current_idx - 1]
                breakout_level = H + self.config.breakout_buffer_atr * A

                if prev_close <= breakout_level and close_p > breakout_level:
                    if v20 is None or volume_v < self.config.breakout_vol_multiplier * v20:
                        self._terminate_event(
                            event, EventState.VOLUME_REJECTED, close_time_ms,
                            "突破成交量不足1.5倍V20",
                            {"volume": volume_v, "v20": v20}
                        )
                        return None

                    if self.pit.oi_available_at is None:
                        self.pit.register_oi(oi_timestamps, oi_values)
                    oi_res = self.pit.read_oi(
                        decision_ms,
                        lookback_ms=self.config.oi_lookback_hours * 3600 * 1000,
                        max_distance_ms=self.config.oi_max_time_distance_sec * 1000,
                    )
                    oi_curr = oi_res["oi_curr"]
                    oi_past = oi_res["oi_past"]
                    oi_growth = oi_res["growth"]
                    oi_status = oi_res["status"]

                    if self.enable_oi_filter and (oi_growth is None or oi_growth < self.config.oi_min_growth_pct):
                        self._terminate_event(
                            event, EventState.OI_REJECTED, close_time_ms,
                            f"6小时OI增长率不足2%(实际: {(oi_growth or 0) * 100:.2f}%)",
                            {"oi_growth": oi_growth}
                        )
                        return None

                    cand_id = f"cand_{event.event_id}_{close_time_ms}"
                    initial_stop = event.retest_low_lr - self.config.initial_stop_buffer_atr * A
                    initial_risk_r = close_p - initial_stop
                    earliest_exec = close_time_ms + 15 * 60 * 1000

                    snapshot = CandidateSnapshot.create(
                        candidate_id=cand_id,
                        event_id=event.event_id,
                        strategy_version=self.config.strategy_version,
                        symbol=self.symbol,
                        venue=self.venue,
                        snapshot_time_ms=close_time_ms,
                        available_at_ms=close_time_ms,
                        box_high_h=H,
                        box_low_l=L,
                        box_atr_a=A,
                        box_er=box.efficiency_ratio_er,
                        sweep_low_s=event.sweep_low_s,
                        retest_low_lr=event.retest_low_lr,
                        breakout_close=close_p,
                        breakout_volume=volume_v,
                        v20_median_volume=v20,
                        volume_ratio=volume_v / v20 if v20 else 1.0,
                        oi_breakout=oi_curr,
                        oi_6h_ago=oi_past,
                        oi_growth_6h=oi_growth,
                        initial_stop_price=initial_stop,
                        initial_risk_r=initial_risk_r,
                        max_entry_price=close_p + self.config.max_gap_up_atr * A,
                        min_entry_price=H,
                        execution_earliest_ms=earliest_exec,
                        pit_proven=bool(oi_status == "ok"),
                        pit_assumption_note="Baseline突破快照",
                        missing_fields=[]
                    )

                    event.candidate_id = cand_id
                    event.ended_at_ms = close_time_ms
                    event.termination_reason = "突破成功(Baseline)"
                    self.completed_events.append(event)
                    self.candidates.append(snapshot)
                    self.active_event = None
                    self.cooling_until_ms = close_time_ms + self.config.cooling_hours * 3600 * 1000
                    return snapshot

            return None

        return None


class WyckoffEntryTimingBase(IStrategy):
    """提前入场双路线验证回测基类"""

    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = False

    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    use_custom_stoploss = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    startup_candle_count: int = 800

    timing_mode: str = "baseline"
    enable_oi_filter: bool = True

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.user_dir = config.get("user_data_dir", os.path.join(BASE_DIR, "evidence/FT-ENTRY-01/user_data"))
        self.data_dir = config.get("datadir", os.path.join(self.user_dir, "data/binance"))
        self.pair_candidates: Dict[str, list] = {}
        self.pair_4h_df: Dict[str, pd.DataFrame] = {}
        self.quote_volume_cache: Dict[str, pd.Series] = {}
        self.active_trade_stop_state: Dict[int, dict] = {}
        self.status_file = os.path.join(self.user_dir, f"run_validation_status_{self.__class__.__name__}.json")
        self.is_run_valid = True

    def informative_pairs(self):
        pairs = self.config["exchange"]["pair_whitelist"]
        informative = []
        for pair in pairs:
            informative.append((pair, "4h"))
            informative.append((pair, "1h"))
        return informative

    def _read_oi_series(self, pair: str) -> Tuple[np.ndarray, np.ndarray, pd.Series]:
        slug = pair.replace("/", "_").replace(":", "_")
        candidates_oi = [
            os.path.join(self.data_dir, f"futures/{slug}-5m-open_interest.feather"),
            os.path.join(self.data_dir, f"{slug}-5m-open_interest.feather"),
            os.path.join(self.user_dir, f"data/binance/futures/{slug}-5m-open_interest.feather"),
            os.path.join(BASE_DIR, f"evidence/FT-FACTOR-03/user_data/data/binance/futures/{slug}-5m-open_interest.feather"),
            os.path.join(BASE_DIR, f"evidence/FT-FACTOR-02/user_data/data/binance/futures/{slug}-5m-open_interest.feather"),
        ]
        f_oi = next((p for p in candidates_oi if os.path.exists(p)), None)
        if f_oi:
            try:
                df_oi = pd.read_feather(f_oi)
                oi_ts = df_oi["date"].astype("int64").to_numpy()
                oi_vals = df_oi["open_interest_amount"].to_numpy(dtype=np.float64)
            except Exception as e:
                logger.warning(f"读取 OI 失败: {f_oi}: {e}")
                oi_ts = np.array([], dtype=np.int64)
                oi_vals = np.array([], dtype=np.float64)
        else:
            oi_ts = np.array([], dtype=np.int64)
            oi_vals = np.array([], dtype=np.float64)

        candidates_sc = [
            os.path.join(self.data_dir, f"sidecar/{slug}-15m-quote_volume.csv"),
            os.path.join(self.user_dir, f"data/binance/sidecar/{slug}-15m-quote_volume.csv"),
            os.path.join(BASE_DIR, f"evidence/FT-FACTOR-03/user_data/data/binance/sidecar/{slug}-15m-quote_volume.csv"),
            os.path.join(BASE_DIR, f"evidence/FT-FACTOR-02/user_data/data/binance/sidecar/{slug}-15m-quote_volume.csv"),
        ]
        f_sc = next((p for p in candidates_sc if os.path.exists(p)), None)
        qv_series = pd.Series(dtype=np.float64)
        if f_sc and os.path.exists(f_sc):
            try:
                df_sc = pd.read_csv(f_sc)
                sc_ts = pd.to_datetime(df_sc["date"], utc=True).astype("int64")
                qv_series = pd.Series(df_sc["quote_volume"].values, index=sc_ts)
            except Exception as e:
                logger.warning(f"读取 quote_volume 失败: {f_sc}: {e}")

        return oi_ts, oi_vals, qv_series

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        clean_symbol = pair.split("/")[0].replace("1000", "") + "USDT"

        df_4h = self.dp.get_pair_dataframe(pair=pair, timeframe="4h")
        df_1h = self.dp.get_pair_dataframe(pair=pair, timeframe="1h")

        if df_4h.empty or df_1h.empty:
            dataframe["cand_valid"] = 0
            return dataframe

        df_4h = df_4h.copy()
        highs_4h = df_4h["high"].to_numpy(dtype=np.float64)
        lows_4h = df_4h["low"].to_numpy(dtype=np.float64)
        closes_4h = df_4h["close"].to_numpy(dtype=np.float64)
        df_4h["atr"] = compute_wilder_atr_series(highs_4h, lows_4h, closes_4h, period=14)
        df_4h["l10"] = df_4h["low"].rolling(10).min()
        df_4h["open_ms"] = df_4h["date"].astype("int64")
        df_4h["close_ms"] = df_4h["open_ms"] + 4 * 3600 * 1000

        self.pair_4h_df[pair] = df_4h[["close_ms", "close", "low", "l10"]].copy()

        oi_ts, oi_vals, qv_series = self._read_oi_series(pair)
        self.quote_volume_cache[pair] = qv_series

        cfg = EventTrendConfig(strategy_name="FT_ENTRY_01")
        sm = TimingEventStateMachine(
            config=cfg,
            symbol=clean_symbol,
            timing_mode=self.timing_mode,
            enable_oi_filter=self.enable_oi_filter
        )
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
            dataframe.loc[mask, "cand_h"] = c.min_entry_price
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

        if rate < cand.min_entry_price or rate > cand.max_entry_price:
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
            return -0.99

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

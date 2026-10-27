# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜动态跟踪距离(d)派生基类。

与 `WyckoffLow4hGridBase` 的**唯一差别**：`custom_stoploss` 里的跟踪距离 `d`
从「固定常数 `trail_d`」改为「按已收盘 4h close 相对实际入场价的涨幅」分档。

切档规则（本单预先锁定，不按结果调整）
──────────────────────────────────────
· stage 1：`d = dyn_d1`（默认 0.08）—— 入场即为 stage 1
· stage 2：`d = dyn_d2`（默认 0.20）—— 首次出现「已收盘 4h close / 实际入场价 − 1 ≥ dyn_trigger_1（默认 0.10）」
· stage 3：`d = dyn_d3`（默认 0.05）—— 首次出现「≥ dyn_trigger_2（默认 0.35）」
· **阶段只前进，不因回撤退档**；同一根 K 线同时满足 35% 时直接进 stage 3（先判高阈值）。
· 判定使用「已收盘」4h K 线（`close_ms <= current_time`），与该 bar 的 low 同一时刻可知，
  不使用未收盘 K 线、不向前追溯成交。

止损口径（与基线完全一致）
──────────────────────────
· 初始止损 = 实际成交价 × (1 − stoploss_s)，stoploss_s 固定 0.05
· 跟踪候选价 = 最近一根已收盘 4h low × (1 − d)
· 生效止损 = max(初始止损, 上一生效止损, 本次候选价)，**只上移不下移**
  ⇒ 「放宽 d」只会减缓后续抬升，绝不会把既有止损下移。
· 无时间止损、无保本、无 ROI、无主动退出、不加仓、不加 72h 换仓。

其余全部继承基线（C1 入场、仓位 `风险预算 0.5% / (价×s)`、最小下单量硬拒单、
0.1% 前 15m 成交额参与率上限、单笔 30% 权益上限、最多 3 仓）。
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime
from typing import Optional

import pandas as pd

from WyckoffLow4hGridBase import WyckoffLow4hGridBase, _to_timestamp_ms

logger = logging.getLogger(__name__)

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
TRACE_DIR = os.path.join(BASE_DIR, "evidence/WORK-DYNAMIC-BT-01/out/stage_trace")


class WyckoffLow4hDynBase(WyckoffLow4hGridBase):
    """动态 d 版本。参数为类属性，派生类按组覆盖。"""

    # 三档跟踪距离
    dyn_d1: float = 0.08
    dyn_d2: float = 0.20
    dyn_d3: float = 0.05
    # 两个切档阈值（相对实际入场价的涨幅）
    dyn_trigger_1: float = 0.10
    dyn_trigger_2: float = 0.35

    def _dyn_stage_for_gain(self, gain: float, prev_stage: int) -> int:
        """按涨幅返回**可达到的最高阶段**；只前进不退档。先判高阈值（可一次跨到 3）。"""
        if gain >= self.dyn_trigger_2:
            return 3
        if gain >= self.dyn_trigger_1:
            return 2
        return 1

    def _dyn_d_of_stage(self, stage: int) -> float:
        return {1: float(self.dyn_d1), 2: float(self.dyn_d2), 3: float(self.dyn_d3)}[stage]

    def _append_stage_trace(self, rows: list) -> None:
        if not rows:
            return
        try:
            os.makedirs(TRACE_DIR, exist_ok=True)
            p = os.path.join(TRACE_DIR, f"stage_trace_{self.__class__.__name__}.csv")
            hdr = not os.path.exists(p)
            pd.DataFrame(rows).to_csv(p, mode="a", header=hdr, index=False, encoding="utf-8-sig")
        except Exception as exc:                                  # noqa: BLE001
            logger.warning(f"写切档轨迹失败: {exc}")

    def custom_stoploss(self, pair: str, trade, current_time: datetime,
                        current_rate: float, current_profit: float, after_fill: bool,
                        **kwargs) -> Optional[float]:
        cands = self.pair_candidates.get(pair, [])
        cand = next((c for c in cands if c.candidate_id == trade.enter_tag), None)

        if not cand or cand.initial_stop_price is None or math.isnan(cand.initial_stop_price) or cand.initial_stop_price <= 0:
            logger.error(
                f"[{pair}] 持仓 trade_id={trade.id} 候选唯一绑定异常！"
                f"enter_tag={trade.enter_tag}, cand={cand}。严禁静默使用假止损！")
            return -0.99

        actual_entry = float(trade.open_rate)
        s = float(getattr(self, "stoploss_s", 0.05))
        initial_stop = actual_entry * (1.0 - s)

        t_id = trade.id or 0
        state = self.active_trade_stop_state.get(t_id)
        if state is None:
            state = {
                "cand_id": cand.candidate_id,
                "initial_stop": initial_stop,
                "current_effective_stop": initial_stop,
                "last_bar_close_ms": 0,
                "entry_time_ms": _to_timestamp_ms(trade.open_date_utc),
                "dyn_stage": 1,
                "stage_change_count": 0,
            }
            self.active_trade_stop_state[t_id] = state
            self._append_stage_trace([{
                "strategy": self.__class__.__name__,
                "trade_id": t_id,
                "pair": pair,
                "candidate_id": cand.candidate_id,
                "event": "ENTRY",
                "event_time_ms": _to_timestamp_ms(current_time),
                "bar_close_ms": None,
                "bar_low": None,
                "bar_close": None,
                "actual_entry": actual_entry,
                "gain_vs_entry": 0.0,
                "stage": 1,
                "d": float(self.dyn_d1),
                "candidate_stop": None,
                "prev_effective_stop": initial_stop,
                "effective_stop": initial_stop,
                "stage_changed": False,
            }])

        current_ms = _to_timestamp_ms(current_time)

        df_4h_info = self.pair_4h_df.get(pair)
        if df_4h_info is not None and not df_4h_info.empty:
            sub_4h = df_4h_info[df_4h_info["close_ms"] <= current_ms]
            if not sub_4h.empty:
                latest_bar = sub_4h.iloc[-1]
                bar_close_ms = int(latest_bar["close_ms"])
                bar_low = float(latest_bar["low"])
                bar_close = float(latest_bar["close"])

                if bar_close_ms > state["last_bar_close_ms"]:
                    # ① 先用**同一根已收盘** 4h 的 close 判阶段（与 low 同一时刻可知）
                    gain = (bar_close / actual_entry - 1.0) if actual_entry > 0 else 0.0
                    prev_stage = int(state.get("dyn_stage", 1))
                    reached = self._dyn_stage_for_gain(gain, prev_stage)
                    new_stage = max(prev_stage, reached)          # 只前进
                    stage_changed = new_stage != prev_stage
                    if stage_changed:
                        state["dyn_stage"] = new_stage
                        state["stage_change_count"] = int(state.get("stage_change_count", 0)) + 1

                    d = self._dyn_d_of_stage(state["dyn_stage"])
                    candidate_stop = bar_low * (1.0 - d)
                    prev_effective = state["current_effective_stop"]
                    new_effective = max(state["initial_stop"], prev_effective, candidate_stop)
                    state["last_bar_close_ms"] = bar_close_ms
                    if new_effective > prev_effective:
                        state["current_effective_stop"] = new_effective

                    self._append_stage_trace([{
                        "strategy": self.__class__.__name__,
                        "trade_id": t_id,
                        "pair": pair,
                        "candidate_id": cand.candidate_id,
                        "event": "BAR_UPDATE",
                        "event_time_ms": current_ms,
                        "bar_close_ms": bar_close_ms,
                        "bar_low": bar_low,
                        "bar_close": bar_close,
                        "actual_entry": actual_entry,
                        "gain_vs_entry": round(gain, 8),
                        "stage": state["dyn_stage"],
                        "d": d,
                        "candidate_stop": candidate_stop,
                        "prev_effective_stop": prev_effective,
                        "effective_stop": state["current_effective_stop"],
                        "stage_changed": bool(stage_changed),
                    }])

        effective_stop = state["current_effective_stop"]

        if current_rate <= effective_stop:
            return 0.0

        stoploss_ratio = (effective_stop - current_rate) / current_rate
        return float(stoploss_ratio)

# -*- coding: utf-8 -*-
"""WX_EVENT_TREND_V0_1 执行适配器：高保真对接 trading_core 共享交易核心。

本文件落实 WORK-07 第四次独立复核的阻断项 1、2：

1. **真实身份贯穿**：候选 ID → `signal_id` → `order_id` → 成交，逐环由共享核心真实产出，
   适配器只做**精确等值匹配**。已删除「绝对时间差 ≤900000ms 匹配」「按输入顺序消耗
   可用成交池」「按时间距离取最近候选」「`ord_{candidate_id}` 拼接订单号」四类兜底；
   身份无法解析时**拒绝发单**，成交找不到订单时标记 `identity_status="order_missing_for_trade"`。
2. **冻结执行规则落实**：
   · 跟踪止损的 2R 激活门槛改用**实际成交价与实际初始风险 R**（不再用 `breakout_close`
     与候选暂估 `initial_risk_r`）；
   · `max_holding_ms` 接入共享核心，到期后第一根 15m bar 开盘退出；
   · 订单名义金额按**已完成的前一根 15m 成交额 × 0.1%** 限制（缺成交额 fail-closed 拒绝）。
3. 跳空取消、跌回箱体取消、单仓 30% / 总名义 90% 上限保持不变。
4. PIT 仍显式标注为**未证明**（`pit_proven=False`），不伪造发布/到达时间。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import EventTrendConfig
from .contracts import CandidateSnapshot, ValidationResult
from ..trading_core.contracts import (ExecutionInputError, ExecPhase,
                                      PositionSide, Side)
from ..trading_core.engine import TradingCore
from ..trading_core.fill import FillQuote, SimulatedBarFillAdapter
from ..trading_core.ledger import InMemoryLedger
from ..trading_core.order import OrderIntent
from ..trading_core.policy import (ACTION_OPEN, EntryDecision, ExecutionPolicy,
                                   check_entry)
from ..trading_core.position import Position

# 身份结论码
IDENTITY_OK = "ok"
IDENTITY_ORDER_MISSING = "order_missing_for_trade"
IDENTITY_NOT_EXECUTED = "not_executed"


@dataclass(frozen=True)
class EventTrendSignalParams:
    """策略信号参数适配。

    ⚠️ `price_step` **没有默认值**，这是刻意的：
    2026-10-17 之前这里是 `price_step: float = 0.01`，并被套用到**所有标的**，
    对价格 0.004~0.025 的 SPACE 会把卖价取整成 **0.0**（零元卖出），
    AIGENSYN / TRUTH / COTI 同样被砍掉 3~4 位有效小数。
    现在必须由调用方按**交易所合约规格**显式传入（十进制字符串优先，避免浮点往返）。
    """
    price_step: str


@dataclass(frozen=True)
class EventTrendExecParams:
    """执行与风控参数适配。

    `initial_equity` 的唯一事实源是 `EventTrendConfig.initial_equity`；
    本字段只是**占位默认值**，`run_event_trend_backtest()` 总会用调用方/配置值覆盖它，
    不允许在此处另立一个数值（否则同一参数出现两个默认值）。
    """
    initial_equity: float = 10000.0
    risk_per_trade: float = 0.005
    max_portfolio_risk: float = 0.015
    max_portfolio_margin_pct: float = 0.90      # 冻结规则：总名义金额最多权益 90%
    max_concurrent_trades: int = 3
    max_leverage_per_symbol: float = 1.0        # 1.0 * 0.90 = 0.90 总名义上限
    fee_rate: float = 0.0005
    maker_fee_rate: float = 0.0002
    taker_fee_rate: float = 0.0005
    slippage_bps: float = 3.0
    slippage_rate: float = 0.0003
    funding_model: str = "real_events"
    enable_trailing_exit: bool = True
    close_open_positions_at_end: bool = True
    # 冻结规则 §4「最长持仓 10 个自然日，到期按下一根可执行 15m 开盘退出」
    max_holding_ms: Optional[int] = 10 * 24 * 3600 * 1000
    # 冻结规则 §5「订单名义金额不得超过已完成的前一根 15m bar 成交额的 0.1%」
    participation_rate: Optional[float] = 0.001
    # 规则与参数身份（支持 FT-LOW4H 与既有规范兼容）
    rule: str = "WX_EVENT_TREND_V0_1"
    model_version: str = "FT-LOW4H-EXP-V2.2"
    stoploss_s: Optional[float] = None
    trail_d: float = 0.05
    trailing_trigger_r: float = 2.0


def _normalize_to_ms(ts: Any) -> int:
    """时间戳鲁棒归一化为毫秒（兼容秒级 10 位数与毫秒级 13 位数）。"""
    if ts is None:
        return -1
    v = int(ts)
    if 0 < v < 10000000000:
        return v * 1000
    return v


def _round_protect_price(px: float, step: Optional[Any]) -> float:
    """保护价（多头止损）：按价格步进向下取整（更低=更保守）。"""
    if step is None:
        return float(px)
    try:
        s = float(step)
        if s <= 0:
            return float(px)
        dec_px = Decimal(str(px))
        dec_s = Decimal(str(step).strip())
        return float((dec_px / dec_s).to_integral_value(rounding="ROUND_FLOOR") * dec_s)
    except Exception:
        return float(px)


def build_exit_channel_15m(
    df_15m: pd.DataFrame,
    candidates: List[CandidateSnapshot],
    df_4h: Optional[pd.DataFrame],
    rule: str = "WX_EVENT_TREND_V0_1",
    trail_d: float = 0.05,
    price_step: Optional[str] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """构建逐 15m bar 精确生效的跟踪止损通道。

    若 rule == "FT-LOW4H"：
      · 跟踪止损线为最新已闭合 4h 的 low * (1 - trail_d) 向下取整。
      · 严格因果：仅当 4h close_time > candidate.execution_earliest_ms 时才提供跟踪价，
        杜绝借用入场前历史 bar；
    若 rule != "FT-LOW4H"（旧 WX_EVENT_TREND_V0_1 规则）：
      · roll10 滚动 10 根已完成 4h 最低价。
    """
    n15 = len(df_15m)
    el = np.full(n15, np.nan, dtype=np.float64)
    eh = np.full(n15, np.nan, dtype=np.float64)
    if df_4h is None or len(df_4h) == 0 or n15 == 0:
        return el, eh

    ts15 = df_15m["timestamp"].to_numpy(np.int64)
    t4 = df_4h["close_time"].to_numpy(np.int64)
    c4 = df_4h["close"].to_numpy(np.float64)
    l4 = df_4h["low"].to_numpy(np.float64)

    starts = sorted({_normalize_to_ms(c.execution_earliest_ms) for c in candidates})
    if not starts:
        return el, eh

    if rule == "FT-LOW4H":
        d_val = float(trail_d)
        for j in range(n15):
            t = int(ts15[j])
            k = int(np.searchsorted(t4, t, side="left")) - 1   # 满足 t4[k] < t (已完成 4h)
            if k < 0:
                continue
            # 因果硬约束：只有在该候选入场时间后收盘的 4h bar 才能作为跟踪来源
            start = None
            for s in starts:
                if s < int(t4[k]):  # 严格 t4[k] > s（入场之后已收盘）
                    start = s
                else:
                    break
            if start is None:
                continue
            cand_stop = _round_protect_price(float(l4[k]) * (1.0 - d_val), price_step)
            el[j] = cand_stop
            eh[j] = float(c4[k])
    else:
        roll10 = pd.Series(l4).rolling(window=10, min_periods=10).min().to_numpy(np.float64)
        for j in range(n15):
            t = int(ts15[j])
            k = int(np.searchsorted(t4, t, side="left")) - 1   # 满足 t4[k] < t
            if k < 0 or not np.isfinite(roll10[k]):
                continue
            start = None
            for s in starts:                                    # starts 已升序
                if s <= int(t4[k]):
                    start = s
                else:
                    break
            if start is None:
                continue
            el[j] = float(roll10[k])
            eh[j] = float(c4[k])
    return el, eh


class EventTrendExecutionPolicy(ExecutionPolicy):
    """落地冻结的跳空/跌回取消、名义仓位上限、参与率与**实际成交价 2R 跟踪止损**。"""

    def __init__(self, *, signal_params, exec_params, strategy_key: str,
                 candidates: List[CandidateSnapshot],
                 timeframe: str = "1h", exec_timeframe: str = "15m"):
        super().__init__(signal_params=signal_params, exec_params=exec_params,
                         strategy_key=strategy_key, timeframe=timeframe,
                         exec_timeframe=exec_timeframe)
        self.candidates = list(candidates)
        self.by_candidate_id: Dict[str, CandidateSnapshot] = {}
        for c in self.candidates:
            if c.candidate_id in self.by_candidate_id:
                raise ExecutionInputError(f"duplicate_candidate_id:{c.candidate_id}")
            self.by_candidate_id[c.candidate_id] = c
        symbols = {c.symbol for c in self.candidates}
        if len(symbols) > 1:
            raise ExecutionInputError(f"mixed_candidate_symbols:{sorted(symbols)}")
        self.symbol = next(iter(symbols)) if symbols else None
        # 实际成交上下文（同一时刻同币最多一仓 ⇒ 单份即可）
        self.entry_ctx: Optional[Dict[str, Any]] = None
        self.rule = getattr(exec_params, "rule", "FT-LOW4H")
        self.stoploss_s = getattr(exec_params, "stoploss_s", None)
        self.trail_d = getattr(exec_params, "trail_d", 0.05)
        self.trailing_trigger_r = getattr(exec_params, "trailing_trigger_r", 0.0)
        self.instrument_spec = None

    # ── 身份解析：只认 candidate_id，禁止时间近似 / 最近候选 ──
    def resolve_candidate(self, signal_id: Optional[str]) -> Optional[CandidateSnapshot]:
        if not signal_id:
            return None
        return self.by_candidate_id.get(str(signal_id))

    def decide_entry(self, *, symbol: str, signal_type: int, direction: int,
                     raw_price: float, stop_ref: float, quote: FillQuote,
                     positions: Dict[str, Position], cash: float, equity_mtm: float,
                     signal_id: str, order_sequence: int,
                     signal_bar_open_ms: Optional[int] = None,
                     signal_score: Optional[float] = None) -> EntryDecision:
        """身份严格解析 → 跳空/跌回取消 → 名义上限 → 发单意图。"""
        cand = self.resolve_candidate(signal_id)
        if cand is None:
            # 身份不可解析 ⇒ 明确拒绝，绝不用时间近似或最近候选兜底
            return EntryDecision(
                False, f"candidate_identity_unresolved(signal_id={signal_id})")
        if cand.symbol != symbol:
            return EntryDecision(
                False, f"candidate_symbol_mismatch({cand.symbol}!={symbol})")
        expect_sig_bar = _normalize_to_ms(cand.snapshot_time_ms - 3600 * 1000)
        if _normalize_to_ms(signal_bar_open_ms) != expect_sig_bar:
            # 信号 bar 与候选快照不一致 ⇒ 身份链不可信，拒绝
            return EntryDecision(
                False,
                f"candidate_signal_bar_mismatch({signal_bar_open_ms}!={expect_sig_bar})")

        # 1. 跳空限制：开盘高于突破小时收盘 + 0.5A 则取消
        if raw_price > cand.max_entry_price:
            return EntryDecision(
                False,
                f"canceled_gap_above_0.5a (open={raw_price} > max={cand.max_entry_price})")
        # 2. 跌回箱体限制：开盘低于箱体上沿 H 则取消
        if raw_price < cand.min_entry_price:
            return EntryDecision(
                False,
                f"canceled_fell_back_into_box (open={raw_price} < H={cand.min_entry_price})")

        # 初始止损与实际风险计算（统一 FT-LOW4H 5% 初始止损与保护价向下取整）
        actual_entry = float(quote.entry_fill)
        ps_step = str(self.p.price_step)

        if self.rule == "FT-LOW4H" and self.stoploss_s is not None and float(self.stoploss_s) > 0:
            s = float(self.stoploss_s)
            if direction == 1:
                stop_raw = actual_entry * (1.0 - s)
                stop_px = _round_protect_price(stop_raw, ps_step)
                stop_dist = actual_entry - stop_px
            else:
                stop_raw = actual_entry * (1.0 + s)
                dec_px = Decimal(str(stop_raw))
                dec_s = Decimal(ps_step)
                stop_px = float((dec_px / dec_s).to_integral_value(rounding="ROUND_CEILING") * dec_s)
                stop_dist = stop_px - actual_entry
        else:
            stop_px = float(cand.initial_stop_price)
            stop_dist = actual_entry - stop_px

        # 3. 初始止损不低于入场价取消
        if (direction == 1 and stop_px >= actual_entry) or (direction == -1 and stop_px <= actual_entry) or stop_dist <= 0:
            return EntryDecision(False, "invalid_signal_stop")

        # 4. 底层风控基础校验（使用实际生效的 stop_px 校验）
        d = check_entry(positions=positions, symbol=symbol, direction=direction,
                        stop_ref=stop_px, entry_fill=quote.entry_fill, cash=cash,
                        equity_mtm=equity_mtm, constraints=self.constraints,
                        fee_rate=self.constraints.fee_rate)
        if not d.accepted:
            return EntryDecision(False, d.reason, risk=d)

        # 数量：FT-LOW4H 规范按实际止损距离 stop_dist 计算风险敞口数量；旧规则使用底层风控数量
        if self.rule == "FT-LOW4H" and self.stoploss_s is not None and float(self.stoploss_s) > 0:
            risk_fraction = float(getattr(self.cfg, "risk_per_trade", 0.005))
            risk_budget = equity_mtm * risk_fraction
            target_qty = risk_budget / stop_dist
        else:
            target_qty = float(d.quantity)

        if target_qty <= 0 or not math.isfinite(target_qty):
            return EntryDecision(False, "nonpositive_qty", risk=d)

        # 5. 规则 §4：单仓名义金额最多权益 30%
        max_notional_per_pos = 0.30 * equity_mtm
        if actual_entry * target_qty > max_notional_per_pos:
            target_qty = max_notional_per_pos / actual_entry

        # 6. 规则 §4：总名义金额最多权益 90%
        curr_notional = sum(abs(p.qty * p.entry_price) for p in positions.values())
        max_total_notional = 0.90 * equity_mtm
        if curr_notional + (actual_entry * target_qty) > max_total_notional:
            avail_notional = max(0.0, max_total_notional - curr_notional)
            target_qty = avail_notional / actual_entry

        if self.instrument_spec is not None and getattr(self.instrument_spec, "qty_step", None):
            from ..trading_core.fill import round_qty_down
            target_qty = round_qty_down(target_qty, self.instrument_spec.qty_step)

        if target_qty <= 0.0001:
            return EntryDecision(False, "notional_cap_exceeded_zero_qty", risk=d)

        # 7. 记录实际成交上下文
        is_immediate = (self.rule == "FT-LOW4H" and self.stoploss_s is not None) or (float(self.trailing_trigger_r) <= 0 and self.stoploss_s is not None)
        self.entry_ctx = {
            "candidate_id": cand.candidate_id,
            "actual_entry_price": actual_entry,
            "actual_initial_stop": stop_px,
            "actual_initial_risk_r": stop_dist,
            "trigger_level": actual_entry + (2.0 if not is_immediate else float(self.trailing_trigger_r)) * stop_dist,
            "activated": True if is_immediate else False,
        }

        ps = PositionSide.LONG if direction == 1 else PositionSide.SHORT
        side = Side.BUY if direction == 1 else Side.SELL
        intent = OrderIntent(
            strategy_key=self.strategy_key, symbol=symbol, side=side,
            position_side=ps, order_type=self.entry_order_type(),
            quantity=float(target_qty), action=ACTION_OPEN,
            phase=ExecPhase.C_ENTRY.value, signal_id=signal_id,
            order_sequence=int(order_sequence),
            stop_price=float(stop_px), reference_price=float(raw_price),
            reason="entry",
            extra={"signal_type": int(signal_type), "direction": int(direction),
                   "signal_score": signal_score, "signal_bar_open_ms": signal_bar_open_ms,
                   "candidate_id": cand.candidate_id,
                   "entry_fill": float(quote.entry_fill), "stop_fill": float(quote.stop_fill),
                   "unit_risk": float(stop_dist)}
        )
        return EntryDecision(True, "", intent=intent, risk=d)

    # ── 跟踪止损：激活门槛用实际成交价 + 实际 R；通道值由 15m 精确通道提供 ──
    def trail_level(self, *, direction: int, exit_low: float,
                    exit_high: float, current_stop: float) -> Optional[float]:
        ctx = self.entry_ctx
        if ctx is None:
            return None
        if not ctx["activated"]:
            ref = float(exit_high)          # 已完成 4h bar 的收盘价
            if math.isfinite(ref) and ref >= float(ctx["trigger_level"]):
                ctx["activated"] = True
        if not ctx["activated"]:
            return None
        if direction == 1:
            lvl = float(exit_low)
            if math.isfinite(lvl) and lvl > current_stop:
                return lvl
        elif direction == -1:
            lvl = float(exit_high)
            if math.isfinite(lvl) and lvl < current_stop:
                return lvl
        return None


def build_signal_frame_from_candidates(
    df_1h: pd.DataFrame,
    candidates: List[CandidateSnapshot],
    df_15m: Optional[pd.DataFrame] = None,
    df_4h: Optional[pd.DataFrame] = None
) -> pd.DataFrame:
    """根据候选快照构建 1h 信号帧。

    · `signal_id` 列直接承载 `candidate_id` ⇒ 共享核心据此贯穿 Order/Fill/Trade 身份；
    · `exit_low_curr` / `exit_high_curr` 保持为全 NaN 占位列：真实跟踪止损通道改由
      `build_exit_channel_15m()` 逐 15m bar 精确提供（见 `run_event_trend_backtest`）。
    """
    n = len(df_1h)
    ts_1h = df_1h["timestamp"].to_numpy(np.int64)

    signals = np.zeros(n, dtype=np.int64)
    directions = np.zeros(n, dtype=np.int64)
    stop_prices = np.full(n, np.nan, dtype=np.float64)
    confirm_times = np.zeros(n, dtype=np.int64)
    signal_ids = np.array([""] * n, dtype=object)

    cand_map = {c.snapshot_time_ms: c for c in candidates}

    for i in range(n):
        t_open = ts_1h[i]
        t_close = t_open + 3600 * 1000
        c = cand_map.get(t_close)
        if c is not None:
            signals[i] = 1
            directions[i] = 1
            stop_prices[i] = c.initial_stop_price
            signal_ids[i] = c.candidate_id
            # 确认时间 = 候选最早执行时刻（下一根严格更晚的 15m 开盘，秒级）
            confirm_times[i] = int(_normalize_to_ms(c.execution_earliest_ms) // 1000)
        else:
            confirm_times[i] = int(t_close // 1000)

    return pd.DataFrame({
        "timestamp": ts_1h,
        "signal": signals,
        "direction": directions,
        "stop_price": stop_prices,
        "confirm_time": confirm_times,
        "signal_id": signal_ids,
        "exit_low_curr": np.full(n, np.nan, dtype=np.float64),
        "exit_high_curr": np.full(n, np.nan, dtype=np.float64),
    })


def run_event_trend_backtest(
    symbol: str,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    candidates: List[CandidateSnapshot],
    funding_events: List[dict],
    df_4h: Optional[pd.DataFrame] = None,
    variant: str = "A",  # 'A': 基准完整, 'B': 关OI, 'C': 成本压力测试
    initial_equity: Optional[float] = None,
    config: Optional[EventTrendConfig] = None,
    instrument_spec: Optional[Any] = None,
    price_step: Optional[str] = None,
) -> Dict[str, Any]:
    """运行基于 trading_core 的事件驱动回测，建立**身份级**候选↔成交对账。

    成交精度（2026-10-17 起强制）
    ──────────────────────────────
    · 首选 `instrument_spec`：交易所 `exchangeInfo` 的 tickSize / stepSize /
      minQty / minNotional，同时启用「数量步长 / 最小量 / 最小名义额」闸门。
    · 没有规格时，必须由调用方**显式**给出 `price_step`（十进制字符串），
      结果标记 `spec_status="declared_no_spec"`，**不得**作为正式回测通过。
    · 两者都没有 ⇒ 直接抛错（禁止退回 0.01 默认值，也禁止用 0 跳过精度约束）。
    """
    cfg = config or EventTrendConfig()
    eq = cfg.initial_equity if initial_equity is None else float(initial_equity)

    # ── 价格步长：只认规格或显式声明，绝不回退到固定 0.01 ──
    if instrument_spec is not None:
        instrument_spec.validate()
        if instrument_spec.tick <= 0:
            raise ExecutionInputError("invalid_instrument_spec_tick_nonpositive")
        step_text = str(instrument_spec.price_step)
        spec_status = "spec_applied"
    elif price_step is not None:
        try:
            _tick = Decimal(str(price_step).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ExecutionInputError(f"invalid_price_step:{price_step!r}") from exc
        if not _tick.is_finite() or _tick <= 0:
            raise ExecutionInputError(f"invalid_price_step_nonpositive:{price_step!r}")
        step_text = str(price_step).strip()
        spec_status = "declared_no_spec"
    else:
        raise ExecutionInputError(
            "instrument_spec_required: 必须提供交易所合约规格（或显式 price_step）；"
            "禁止用固定 0.01 默认值成交")

    # 冻结规则 §5 的参与率上限依赖「已完成前一根 15m bar 的成交额」。
    # 缺该列 ⇒ 直接报错，而不是悄悄跳过约束（也不用 0 / 成交量×价格冒充成交额）。
    if "quote_volume" not in df_15m.columns:
        raise ExecutionInputError(
            "missing_quote_volume_for_participation_cap: 15m 帧必须提供 quote_volume")

    # 费率与滑点映射
    if variant == "C":
        taker_fee_bps = cfg.stress_taker_fee_bps
        slippage_bps = cfg.stress_slippage_bps
    else:
        taker_fee_bps = cfg.taker_fee_bps
        slippage_bps = cfg.slippage_bps

    fee_rate = taker_fee_bps * 1e-4
    slippage_rate = slippage_bps * 1e-4

    rule = getattr(cfg, "rule", "WX_EVENT_TREND_V0_1")
    model_version = getattr(cfg, "model_version", "FT-LOW4H-EXP-V2.2")
    stoploss_s = getattr(cfg, "stoploss_s", None)
    trail_d = getattr(cfg, "trail_d", 0.05)
    trailing_trigger_r = getattr(cfg, "trailing_trigger_r", 2.0)
    if rule == "FT-LOW4H":
        if stoploss_s is None:
            stoploss_s = 0.05
        trailing_trigger_r = 0.0
    max_holding_ms = (int(cfg.max_holding_days) * 24 * 3600 * 1000
                      if getattr(cfg, "max_holding_days", None) is not None else None)

    exec_params = EventTrendExecParams(
        initial_equity=eq,
        risk_per_trade=cfg.risk_budget_per_trade,
        max_portfolio_risk=cfg.max_total_risk_budget,
        max_portfolio_margin_pct=cfg.max_total_notional,
        max_concurrent_trades=cfg.max_active_positions,
        fee_rate=fee_rate,
        taker_fee_rate=fee_rate,
        maker_fee_rate=0.0002,
        slippage_bps=slippage_bps,
        slippage_rate=slippage_rate,
        funding_model="real_events",
        enable_trailing_exit=True,
        close_open_positions_at_end=True,
        max_holding_ms=max_holding_ms,
        participation_rate=float(cfg.max_participation_of_prev_15m_quote),
        rule=rule,
        model_version=model_version,
        stoploss_s=stoploss_s,
        trail_d=trail_d,
        trailing_trigger_r=trailing_trigger_r,
    )

    signal_params = EventTrendSignalParams(price_step=step_text)

    # 注入具备跳空/跌回保护、参与率与 FT-LOW4H 跟踪止损的高保真执行策略
    policy = EventTrendExecutionPolicy(
        signal_params=signal_params,
        exec_params=exec_params,
        strategy_key=cfg.strategy_name,
        candidates=candidates,
        timeframe="1h",
        exec_timeframe="15m"
    )
    if instrument_spec is not None:
        policy.instrument_spec = instrument_spec
    fill_adapter = SimulatedBarFillAdapter(
        slippage_bps=slippage_bps,
        taker_fee_rate=fee_rate,
        maker_fee_rate=0.0002,
        strict=True,          # 价格非法/规格非法 ⇒ 拒绝成交（不再静默取整成 0）
    )
    ledger = InMemoryLedger()

    sig_frame = build_signal_frame_from_candidates(df_1h, candidates,
                                                   df_15m=df_15m, df_4h=df_4h)

    def signal_fn(sig_bars, params, unit, price_step):
        return sig_frame

    def to_epoch_seconds(ts_array, unit):
        return (ts_array // 1000).astype(np.int64)

    core = TradingCore(
        policy=policy,
        fill_adapter=fill_adapter,
        signal_fn=signal_fn,
        to_epoch_seconds=to_epoch_seconds,
        strategy_key=cfg.strategy_name,
        strategy_id=cfg.strategy_name,
        strategy_version=cfg.strategy_version,
        timeframe="1h",
        exec_timeframe="15m",
        ledger=ledger,
        instrument_specs=({symbol: instrument_spec} if instrument_spec is not None else None),
    )

    # 逐 15m bar 精确的跟踪止损通道
    df_15m_aligned = df_15m.copy()
    el_chan, eh_chan = build_exit_channel_15m(
        df_15m_aligned, candidates, df_4h,
        rule=rule, trail_d=trail_d, price_step=step_text
    )
    df_15m_aligned["exit_low_chan"] = el_chan
    df_15m_aligned["exit_high_chan"] = eh_chan
    if "funding_rate" not in df_15m_aligned.columns:
        df_15m_aligned["funding_rate"] = np.nan

    market_data = {
        symbol: {
            "signal_bars": df_1h,
            "intrabar_bars": df_15m_aligned,
            "funding_events": funding_events,
            "time_unit": "ms"
        }
    }

    result = core.run(market_data, time_unit="ms")

    trades = result.get("trades", [])
    tc_block = result.get("trading_core") or {}
    orders = tc_block.get("orders", [])
    ledger_events = [e.to_dict() for e in ledger.events()]

    # ── 身份索引（严格等值，不做时间近似 / 不做顺序消耗）──
    trades_by_signal: Dict[str, dict] = {}
    orphan_trades: List[dict] = []
    for idx, tr in enumerate(trades):
        sid = tr.get("signal_id")
        if not sid:
            orphan_trades.append({"index": idx, "symbol": tr.get("symbol"),
                                  "reason": "trade_missing_signal_id"})
            continue
        if sid in trades_by_signal:
            orphan_trades.append({"index": idx, "symbol": tr.get("symbol"),
                                  "signal_id": sid,
                                  "reason": "duplicate_signal_id_in_trades"})
            continue
        trades_by_signal[str(sid)] = tr

    open_orders_by_signal: Dict[str, dict] = {}
    for o in orders:
        if o.get("action") == ACTION_OPEN and o.get("status") == "FILLED":
            open_orders_by_signal.setdefault(str(o.get("signal_id")), o)

    reject_reason_by_signal: Dict[str, str] = {}
    for e in ledger_events:
        if e.get("event_type") == "SIGNAL_REJECTED":
            pl = e.get("payload") or {}
            sid = pl.get("signal_id")
            if sid:
                reject_reason_by_signal.setdefault(str(sid), str(pl.get("reason", "")))

    validation_results: List[ValidationResult] = []
    identity_breaks: List[dict] = []

    for cand in candidates:
        tr = trades_by_signal.get(cand.candidate_id)
        order = open_orders_by_signal.get(cand.candidate_id)

        if tr is not None:
            if order is None:
                identity_status = IDENTITY_ORDER_MISSING
                identity_breaks.append({"candidate_id": cand.candidate_id,
                                        "reason": "trade_without_open_order"})
            else:
                identity_status = IDENTITY_OK

            real_qty = float(tr.get("qty", tr.get("quantity", 0.0)))
            gross_pnl = float(tr.get("gross_pnl", 0.0))
            net_pnl = float(tr.get("net_pnl", tr.get("pnl", 0.0)))
            fee_paid = float(tr.get("fees", tr.get("entry_fee", 0.0) + tr.get("exit_fee", 0.0)))
            slip_loss = float(tr.get("slippage_loss", 0.0))
            funding_fee = float(tr.get("funding_fee", 0.0))

            entry_px = float(tr.get("entry_price", 0.0))
            exit_px = float(tr.get("exit_price", 0.0))
            # 实际生效初始止损与实际 R
            actual_stop = float(order.get("stop_price") if order is not None and order.get("stop_price") is not None else cand.initial_stop_price)
            actual_r = entry_px - actual_stop
            net_r = (net_pnl / (actual_r * real_qty)) if (actual_r > 0 and real_qty > 0) else 0.0
            mfe = float(tr.get("mfe", 0.0))
            mae = float(tr.get("mae", 0.0))
            mfe_r = (mfe * entry_px / actual_r) if actual_r > 0 else 0.0
            mae_r = (mae * entry_px / actual_r) if actual_r > 0 else 0.0
            entry_ms = tr.get("entry_time")
            exit_ms = tr.get("exit_time")
            hold_h = ((float(exit_ms) - float(entry_ms)) / 3600000.0
                      if entry_ms is not None and exit_ms is not None else None)

            val = ValidationResult(
                validation_id=f"val_{cand.candidate_id}_{variant}",
                candidate_id=cand.candidate_id,
                event_id=cand.event_id,
                symbol=cand.symbol,
                strategy_version=cand.strategy_version,
                run_variant=variant,
                executed=True,
                identity_status=identity_status,
                order_id=(str(order.get("order_id")) if order is not None else None),
                fill_time_ms=entry_ms,
                entry_price=entry_px,
                executed_qty=real_qty,
                actual_stop_price=actual_stop,
                actual_r=actual_r,
                exit_time_ms=exit_ms,
                exit_price=exit_px,
                exit_reason=str(tr.get("exit_reason", "")),
                holding_duration_hours=hold_h,
                fee_paid=fee_paid,
                slippage_cost=slip_loss,
                funding_cost=funding_fee,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                net_pnl_r=net_r,
                mfe=mfe,
                mae=mae,
                mfe_r=mfe_r,
                mae_r=mae_r
            )
        else:
            reason = reject_reason_by_signal.get(
                cand.candidate_id,
                "no_signal_dispatched_or_unmatched_in_intrabar_grid")
            val = ValidationResult(
                validation_id=f"val_{cand.candidate_id}_{variant}",
                candidate_id=cand.candidate_id,
                event_id=cand.event_id,
                symbol=cand.symbol,
                strategy_version=cand.strategy_version,
                run_variant=variant,
                executed=False,
                identity_status=IDENTITY_NOT_EXECUTED,
                execution_canceled_reason=reason
            )
        validation_results.append(val)

    total_net_pnl = float(sum(t.get("net_pnl", 0.0) for t in trades))
    total_fees = float(sum(t.get("fees", t.get("entry_fee", 0.0) + t.get("exit_fee", 0.0))
                           for t in trades))
    total_funding = float(sum(t.get("funding_fee", 0.0) for t in trades))
    total_slippage = float(sum(t.get("slippage_loss", 0.0) for t in trades))
    total_slip_pure = float(sum(t.get("slippage_loss_pure", 0.0) for t in trades))
    total_rounding = float(sum(t.get("rounding_loss", 0.0) for t in trades))
    precision_block = result.get("precision", {}) or {}
    # 正式回测通过与否：规格必须「已验证适用于该窗口」且运行未因精度问题失效
    formal_pass_allowed = bool(
        instrument_spec is not None
        and getattr(instrument_spec, "formal_pass_allowed", False)
        and precision_block.get("run_valid", True))

    executed_vals = [v for v in validation_results if v.executed]
    identity_chain_ok = (
        not identity_breaks
        and not orphan_trades
        and all(v.identity_status == IDENTITY_OK for v in executed_vals)
        and len(executed_vals) == len(trades)
    )

    return {
        "variant": variant,
        "engine_result": result,
        "validation_results": validation_results,
        "ledger_events": ledger_events,
        "orders": orders,
        "spec_status": spec_status,
        "instrument_spec": (instrument_spec.to_dict() if instrument_spec is not None else None),
        "precision": result.get("precision", {}),
        "identity": {
            "chain_ok": bool(identity_chain_ok),
            "identity_breaks": identity_breaks,
            "orphan_trades": orphan_trades,
            "trades_total": len(trades),
            "trades_matched_by_identity": len(trades_by_signal),
            "candidates_total": len(candidates),
            "candidates_executed": len(executed_vals),
        },
        "summary": {
            "initial_equity": eq,
            "final_equity": float(result.get("final_equity", eq)),
            "net_pnl": total_net_pnl,
            "trade_count": len(trades),
            "order_count": int(tc_block.get("orders_count", len(orders))),
            "fee_total": total_fees,
            "slippage_total": total_slippage,
            # 成本分账（加性；slippage_total == slippage_pure_total + rounding_total）
            "slippage_pure_total": total_slip_pure,
            "rounding_total": total_rounding,
            "slippage_split_ok": abs(total_slippage - (total_slip_pure + total_rounding)) < 1e-9,
            "funding_total": total_funding,
            "spec_status": spec_status,
            "formal_pass_allowed": formal_pass_allowed,
        }
    }

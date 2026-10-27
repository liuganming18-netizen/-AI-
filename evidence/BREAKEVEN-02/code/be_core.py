# -*- coding: utf-8 -*-
"""BREAKEVEN-02 · 保本止损对照收口内核（复用 BREAKEVEN-01，消除串行资金耦合）。

设计契约（逐条对应 BREAKEVEN-02 工单）
────────────────────────────────────────
1. 【复用不重写】：原样继承 BREAKEVEN-01 的保本止损公式与撮合规则，不重写框架。
2. 【固定仓位配对】：通过在 BreakevenMixin 中注入 fixed_quantities，锁定同一笔交易
   的入场时间、价格、开仓数量与初始止损，彻底排除上一笔交易平仓收益导致账户权益
   变动从而改变下一笔开仓数量的串行资金耦合干扰（解决 OPUSDT 的 0.112375 U 仓位扰动）。
3. 【四时刻与跳空规则清晰定义】：
   - 触发时刻 (Trigger Bar)：本根 15m high (多头) / low (空头) 触及 entry + k*R 的 bar。
   - 下单/更新时刻 (Update Bar)：本根 15m 相位 D 末尾，止损抬升至保本价（只收紧不下调）。
   - 生效时刻 (Applied Bar)：下一根 15m bar（Trigger Bar + 1）的开盘（相位 A/B）起生效。
   - 跳空成交规则：若生效后开盘跳空跌破止损，按真实开盘价与不利滑点成交，绝不强行按保本价成交。
4. 【保本价覆盖已知成本】：
   覆盖入场费 + 预计出场费 (taker) + 出场不利滑点 + 出场价格取整 + 触发时刻已发生的资金费。
   触发后未来新发生的资金费结算单列归因为「后续费用造成的保本后小亏」。
"""
from __future__ import annotations

import dataclasses
import math
from decimal import Decimal
from typing import Any, Dict, List, Optional

from WyckoffMaster.event_trend_v0_1 import engine_adapter as _ea
from WyckoffMaster.event_trend_v0_1.engine_adapter import EventTrendExecutionPolicy
from WyckoffMaster.trading_core.fill import price_breakdown

BE_CONTRACT_VERSION = "breakeven_02_v1.0.0"

# 当前生效的保本阈值与固定仓位（None = 不启用）。worker 内串行调用，无并发冲突。
_ACTIVE: Dict[str, Any] = {
    "r": None,
    "policy": None,
    "fixed_quantities": {}  # signal_id -> float
}


def _dec(x: Any) -> Decimal:
    return Decimal(str(x))


def _floor_tick(px: float, step: str) -> float:
    d = (_dec(px) / _dec(step)).to_integral_value(rounding="ROUND_FLOOR") * _dec(step)
    return float(d)


def _ceil_tick(px: float, step: str) -> float:
    d = (_dec(px) / _dec(step)).to_integral_value(rounding="ROUND_CEILING") * _dec(step)
    return float(d)


def net_at_stop(*, direction: int, entry_price: float, qty: float, entry_fee: float,
                cum_funding: float, fee_rate: float, slippage_rate: float,
                price_step: str, stop_px: float) -> Dict[str, float]:
    """给定「触发价 stop_px」，用与引擎完全相同的取整/滑点/费用口径计算平仓净额。"""
    bd = price_breakdown(raw_price=stop_px, is_buy=(direction == -1),
                         price_step=price_step, slippage_rate=slippage_rate)
    fill = float(Decimal(bd["post_round_price"]))
    gross = (fill - entry_price) * qty * direction
    exit_fee = fill * qty * fee_rate
    net = gross - entry_fee - exit_fee - cum_funding
    return {"exit_fill": fill, "gross": gross, "exit_fee": exit_fee, "net": net}


def breakeven_stop_price(*, direction: int, entry_price: float, qty: float,
                         entry_fee: float, cum_funding: float, fee_rate: float,
                         slippage_rate: float, price_step: str) -> Dict[str, Any]:
    """解出「净额 >= 0」的最小收紧幅度触发价（含滑点/取整/双边费用/已发生资金费）。

    解析初值（忽略取整）：
      多头：X >= (qty*P + entry_fee + funding) / (qty*(1-s)*(1-f))
      空头：X <= (qty*P - entry_fee - funding) / (qty*(1+s)*(1+f))
    再按 tick 逐步收紧并用 `net_at_stop` 精确复核，直到净额 >= 0。
    """
    if qty <= 0 or not math.isfinite(qty):
        raise ValueError("breakeven_qty_nonpositive")
    s = float(slippage_rate)
    f = float(fee_rate)
    if direction == 1:
        x = (qty * entry_price + entry_fee + cum_funding) / (qty * (1.0 - s) * (1.0 - f))
        x = _ceil_tick(x, price_step)
        for _ in range(64):
            chk = net_at_stop(direction=1, entry_price=entry_price, qty=qty,
                              entry_fee=entry_fee, cum_funding=cum_funding,
                              fee_rate=f, slippage_rate=s, price_step=price_step,
                              stop_px=x)
            if chk["net"] >= 0.0:
                return {"stop_px": x, **chk, "ticks_adjusted": _}
            x = float(_dec(x) + _dec(price_step))
        raise RuntimeError("breakeven_solver_no_converge_long")
    x = (qty * entry_price - entry_fee - cum_funding) / (qty * (1.0 + s) * (1.0 + f))
    x = _floor_tick(x, price_step)
    for _ in range(64):
        chk = net_at_stop(direction=-1, entry_price=entry_price, qty=qty,
                          entry_fee=entry_fee, cum_funding=cum_funding,
                          fee_rate=f, slippage_rate=s, price_step=price_step,
                          stop_px=x)
        if chk["net"] >= 0.0:
            return {"stop_px": x, **chk, "ticks_adjusted": _}
        x = float(_dec(x) - _dec(price_step))
    raise RuntimeError("breakeven_solver_no_converge_short")


class BreakevenMixin:
    """保本逻辑与固定仓位本体。

    MRO 要求：本 Mixin 必须排在被装饰的执行策略之前
    （class BreakevenPolicy(BreakevenMixin, EventTrendExecutionPolicy)）。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.breakeven_r = _ACTIVE["r"]
        self.fixed_quantities = dict(_ACTIVE.get("fixed_quantities") or {})
        self._be: Dict[str, dict] = {}
        _ACTIVE["policy"] = self

    def decide_entry(self, *args, **kwargs):
        """入场决策重写：若存在预锁定的固定数量（基准 A 组数量），直接锁定仓位。"""
        dec = super().decide_entry(*args, **kwargs)
        if dec.accepted and dec.intent is not None:
            sig_id = str(kwargs.get("signal_id"))
            fq = self.fixed_quantities.get(sig_id)
            if fq is not None and fq > 0:
                new_intent = dataclasses.replace(dec.intent, quantity=float(fq))
                dec = dataclasses.replace(dec, intent=new_intent)
        return dec

    # ── 每个信号一份状态（按 signal_id 唯一索引）──
    def _state(self, pos) -> dict:
        key = str(getattr(pos, "signal_id", None) or pos.symbol)
        st = self._be.get(key)
        if st is None:
            r_abs = (pos.entry_price - pos.initial_stop) if pos.direction == 1 \
                else (pos.initial_stop - pos.entry_price)
            st = {
                "signal_id": pos.signal_id, "symbol": pos.symbol,
                "direction": int(pos.direction),
                "entry_time": int(pos.entry_time),
                "entry_price": float(pos.entry_price),
                "initial_stop": float(pos.initial_stop),
                "qty": float(pos.qty), "R_abs": float(r_abs),
                "bar_ordinal": 0,          # 已进入相位 D 的 bar 计数（含入场 bar）
                "triggered": False,
                "trigger_ordinal": None, "trigger_px": None,
                "be_px": None, "be_net_at_trigger": None,
                "update_ordinal": None, "applied_ordinal": None,
                "stop_before": None, "stop_after": None,
                "same_bar_ambiguous": False,
                "bar_high_after_entry_max": None,
            }
            self._be[key] = st
        return st

    def _threshold(self, st: dict) -> Optional[float]:
        if not self.breakeven_r:
            return None
        if st["R_abs"] <= 0 or not math.isfinite(st["R_abs"]):
            return None
        return st["entry_price"] + st["direction"] * self.breakeven_r * st["R_abs"]

    def decide_intrabar_exit(self, *, pos, high: float, low: float, symbol: str):
        st = self._state(pos)
        st["bar_ordinal"] += 1
        hi, lo = float(high), float(low)
        if st["bar_high_after_entry_max"] is None:
            st["bar_high_after_entry_max"] = hi
        else:
            st["bar_high_after_entry_max"] = max(st["bar_high_after_entry_max"], hi)

        # ① 先用未抬升的止损判退出（保守处理：同根不假设有利先后）
        dec = super().decide_intrabar_exit(pos=pos, high=hi, low=lo, symbol=symbol)
        thr = self._threshold(st)
        if dec.should_exit:
            if thr is not None and not st["triggered"]:
                reached = (hi >= thr) if pos.direction == 1 else (lo <= thr)
                if reached:
                    st["same_bar_ambiguous"] = True
            return dec
        if thr is None or st["triggered"]:
            return dec

        # ② 本根没打到止损 ⇒ 判浮盈是否达到 kR（用本根 high/low，方向对应）
        reached = (hi >= thr) if pos.direction == 1 else (lo <= thr)
        if not reached:
            return dec

        st["triggered"] = True
        st["trigger_ordinal"] = int(st["bar_ordinal"])
        st["trigger_px"] = float(thr)
        res = breakeven_stop_price(
            direction=pos.direction, entry_price=pos.entry_price, qty=pos.qty,
            entry_fee=pos.entry_fee, cum_funding=float(pos.cum_funding_fee),
            fee_rate=float(self.cfg.taker_fee_rate),
            slippage_rate=float(self.cfg.slippage_bps) * 1e-4,
            price_step=str(self.price_step))
        st["be_px"] = float(res["stop_px"])
        st["be_net_at_trigger"] = float(res["net"])
        st["stop_before"] = float(pos.current_stop)

        # ③ 只能收紧；原止损已更高 ⇒ 不动（不下调）
        if pos.direction == 1:
            new_stop = max(float(pos.current_stop), float(res["stop_px"]))
        else:
            new_stop = min(float(pos.current_stop), float(res["stop_px"]))
        st["stop_after"] = float(new_stop)
        if new_stop != float(pos.current_stop):
            pos.current_stop = float(new_stop)
            # 止损在本根相位 D 末尾更新；下一根 bar 的相位 A/B 起才可能成交
            st["update_ordinal"] = int(st["bar_ordinal"])
            st["applied_ordinal"] = int(st["bar_ordinal"]) + 1
            pos.stop_trace.append({"ts": None, "stop": float(new_stop),
                                   "reason": "breakeven_%gR" % float(self.breakeven_r)})
        else:
            st["update_ordinal"] = int(st["bar_ordinal"])
            st["applied_ordinal"] = None
        return dec


class BreakevenPolicy(BreakevenMixin, EventTrendExecutionPolicy):
    """正式事件趋势策略 + 保本 + 固定仓位支持。"""


def install(breakeven_r: Optional[float], fixed_quantities: Optional[Dict[str, float]] = None) -> None:
    """把保本阈值和固定仓位装进内核；None ⇒ 还原成原始类（保证 A 逐位一致）。"""
    _ACTIVE["r"] = breakeven_r
    _ACTIVE["policy"] = None
    _ACTIVE["fixed_quantities"] = dict(fixed_quantities or {})
    if breakeven_r is None and not fixed_quantities:
        _ea.EventTrendExecutionPolicy = EventTrendExecutionPolicy
    else:
        _ea.EventTrendExecutionPolicy = BreakevenPolicy


def last_policy_state() -> Dict[str, dict]:
    p = _ACTIVE.get("policy")
    return dict(getattr(p, "_be", {}) or {}) if p is not None else {}

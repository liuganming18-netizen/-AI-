# -*- coding: utf-8 -*-
"""BREAKEVEN-01 · 保本止损对照内核（只做加法，不改正式策略）。

设计约束（逐条对应工单）
────────────────────────
1. **不新写撮合框架**：本模块不实现任何成交价、费用、资金费、取整逻辑，
   全部由 `trading_core`（`fill.py` / `funding.py` / `engine.py`）原样完成。
   本模块只做一件事：在 bar 内止损判定之前/之后，按「浮盈达到 kR」把
   `Position.current_stop` 抬到保本价。
2. **唯一差异是保本触发条件**：入场信号、成交、初始止损、价格路径、费用、
   滑点、参与率、最长持仓、2R 跟踪止损全部沿用 `EventTrendExecutionPolicy`。
3. **不得用整根 bar 的最高价抬完止损再按抬升前价格成交**：
   本实现先在**未抬升的**止损上判是否触发（`super().decide_intrabar_exit`），
   只有在「本根没打到止损」时才抬止损，且抬升**从下一根 bar 起生效**
   （下一根相位 A/B 才可能用它成交）。同根既到阈值又打到止损 ⇒ 按原止损退出，
   并记入「同根先后不明」清单。
4. **保本价覆盖开仓与预计平仓费用**：入场费 + 预计出场费（taker）+ 出场滑点 +
   出场取整 + **已发生**的资金费（`Position.cum_funding_fee`，触发时刻已知，
   不引用未来结算）。求出的价用与 `fill.py` 完全相同的十进制取整函数复核净额，
   不足则按一个 tick 继续收紧，直到净额 >= 0。
5. **只能收紧**：多头 `new = max(old, be)`、空头 `new = min(old, be)`；
   原止损已更高时返回 None（不下调）。

R 的定义
────────
`R = |实际入场价 − 初始止损价|`，入场时固定，与冻结规则 §4 一致。
阈值价 = 入场价 + k·R（多头）/ 入场价 − k·R（空头）。
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Dict, List, Optional

from WyckoffMaster.event_trend_v0_1 import engine_adapter as _ea
from WyckoffMaster.event_trend_v0_1.engine_adapter import EventTrendExecutionPolicy
from WyckoffMaster.trading_core.fill import price_breakdown

BE_CONTRACT_VERSION = "breakeven_01_v1.0.0"

# 当前生效的保本阈值（None = 不启用）。worker 内串行调用，无并发写。
_ACTIVE: Dict[str, Any] = {"r": None, "policy": None}


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
    """给定「触发价 stop_px」，用与引擎完全相同的取整/滑点/费用口径算净额。"""
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
    """解出「净额 >= 0」的最小收紧幅度对应的触发价（含滑点/取整/双边费用/已发生资金费）。

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
    """保本逻辑本体。与具体执行策略解耦，便于在合成路径上单独验证。

    MRO 要求：本 Mixin 必须排在被装饰的执行策略**之前**
    （`class BreakevenPolicy(BreakevenMixin, EventTrendExecutionPolicy)`），
    这样 `super()` 才会落到真实的 `decide_intrabar_exit`。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.breakeven_r = _ACTIVE["r"]
        self._be: Dict[str, dict] = {}
        _ACTIVE["policy"] = self

    # ── 每个**信号**一份状态 ──
    # 注意：必须按 signal_id 索引，不能按 symbol。同一 run 内同一标的可能先后开多笔，
    # 按 symbol 索引会把前一笔的触发记录覆盖掉（本单第一版就踩过，已修正）。
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

        # ① 先用**未抬升的**止损判触发（保守：同根不假设有利先后）
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

        # ③ 只能收紧；原止损已更高 ⇒ 不动（记录，不下调）
        if pos.direction == 1:
            new_stop = max(float(pos.current_stop), float(res["stop_px"]))
        else:
            new_stop = min(float(pos.current_stop), float(res["stop_px"]))
        st["stop_after"] = float(new_stop)
        if new_stop != float(pos.current_stop):
            pos.current_stop = float(new_stop)
            # 止损在本根相位 D 末尾更新；**下一根 bar 的相位 A/B 起才可能用它成交**
            st["update_ordinal"] = int(st["bar_ordinal"])
            st["applied_ordinal"] = int(st["bar_ordinal"]) + 1
            pos.stop_trace.append({"ts": None, "stop": float(new_stop),
                                   "reason": "breakeven_%gR" % float(self.breakeven_r)})
        else:
            st["update_ordinal"] = int(st["bar_ordinal"])
            st["applied_ordinal"] = None
        return dec


class BreakevenPolicy(BreakevenMixin, EventTrendExecutionPolicy):
    """正式事件趋势策略 + 保本（唯一差异就是 Mixin 里的触发与抬升）。"""


def install(breakeven_r: Optional[float]) -> None:
    """把保本阈值装进内核；`None` ⇒ 还原成正式策略的原始类（保证 A 逐位一致）。"""
    _ACTIVE["r"] = breakeven_r
    _ACTIVE["policy"] = None
    if breakeven_r is None:
        _ea.EventTrendExecutionPolicy = EventTrendExecutionPolicy
    else:
        _ea.EventTrendExecutionPolicy = BreakevenPolicy


def last_policy_state() -> Dict[str, dict]:
    p = _ACTIVE.get("policy")
    return dict(getattr(p, "_be", {}) or {}) if p is not None else {}

# -*- coding: utf-8 -*-
"""BREAKEVEN-02 · 固定仓位配对内核（复用 BREAKEVEN-01，不重写框架）。

本模块只做**两件**事，都不碰撮合/费用/取整：

1. `FixedQtyMixin`：把入场数量替换成**冻结值**（来自基线 A 的逐笔记录）。
   这样 A/B/C 三组的入场时间、入场价、数量、初始止损完全相同，
   串行权益变化（前一笔被保本提前平仓 ⇒ 后一笔仓位变化）被彻底切断。
2. `install()`：把「保本阈值 + 冻结数量」一起装进引擎。

保本逻辑本身**一行都没有重写**：直接继承
`evidence/BREAKEVEN-01/code/be_core.py` 的 `BreakevenMixin` / `BreakevenPolicy`。

为什么固定数量就够（不需要固定权益）
────────────────────────────────────
净额 `net = (exit_fill − entry_price)·qty − entry_fee − exit_fee − funding`
只依赖 qty 与价格路径，**不依赖账户权益**。权益只影响「下单数量」和「风控是否放行」。
因此：把数量钉死 + 每个信号单独隔离成一次回放 ⇒ 逐笔净额与账户权益路径完全解耦。
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import Any, Dict, Optional

_ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
_BE1_CODE = os.path.join(_ROOT, "evidence/BREAKEVEN-01/code")
for _p in (_ROOT, f"{_ROOT}/策略", f"{_ROOT}/框架/backend", _BE1_CODE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import be_core  # noqa: E402  BREAKEVEN-01 的唯一保本实现（原样复用）
from WyckoffMaster.event_trend_v0_1 import engine_adapter as _ea  # noqa: E402
from WyckoffMaster.trading_core.policy import EntryDecision  # noqa: E402

BE2_CONTRACT_VERSION = "breakeven_02_fixedqty_v1.0.0"

#: 当前生效的冻结数量（None = 不冻结，退回正式策略的行为）。worker 内串行调用。
_ACTIVE: Dict[str, Any] = {"r": None, "qty": None, "policy": None}


class FixedQtyMixin:
    """入场数量替换成冻结值。其余（跳空取消、名义上限、2R 跟踪）全部沿用正式策略。

    顺序很关键：先让正式策略完整跑一遍 `decide_entry`（它会做身份解析、
    跳空/跌回取消、风控放行），**通过之后**才把数量替换成冻结值。
    这样「是否成交」仍由正式规则决定，只有「成交多少」被钉死。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fixed_qty = _ACTIVE["qty"]
        self.fixed_qty_applied: Optional[float] = None
        _ACTIVE["policy"] = self

    def decide_entry(self, **kw):
        dec = super().decide_entry(**kw)
        if not dec.accepted or dec.intent is None or not self.fixed_qty:
            return dec
        q = float(self.fixed_qty)
        self.fixed_qty_applied = q
        return EntryDecision(
            True, "", intent=replace(dec.intent, quantity=q),
            risk=dec.risk, phase=dec.phase)


class FixedQtyBreakevenPolicy(FixedQtyMixin, be_core.BreakevenPolicy):
    """MRO：FixedQtyMixin → BreakevenPolicy → BreakevenMixin → EventTrendExecutionPolicy。"""


def install(breakeven_r: Optional[float], fixed_qty: Optional[float]) -> None:
    """装入「保本阈值 + 冻结数量」，并把引擎的默认策略类指过去。

    `breakeven_r=None` 时**仍然**替换策略类 —— 因为固定数量本身也需要它。
    这与 BREAKEVEN-01 的 `install(None)`（还原成正式类）不同，务必注意。
    """
    _ACTIVE["r"] = breakeven_r
    _ACTIVE["qty"] = fixed_qty
    be_core._ACTIVE["r"] = breakeven_r        # 复用 01 的阈值读取路径
    be_core._ACTIVE["policy"] = None
    _ea.EventTrendExecutionPolicy = FixedQtyBreakevenPolicy


def uninstall() -> None:
    """彻底还原（进程结束时用）。"""
    _ACTIVE["r"] = None
    _ACTIVE["qty"] = None
    be_core._ACTIVE["r"] = None
    _ea.EventTrendExecutionPolicy = _ea.__dict__.get(
        "_ORIGINAL_POLICY", be_core.EventTrendExecutionPolicy)


def last_state() -> Dict[str, dict]:
    p = _ACTIVE.get("policy")
    return dict(getattr(p, "_be", {}) or {}) if p is not None else {}


__all__ = ["BE2_CONTRACT_VERSION", "FixedQtyMixin", "FixedQtyBreakevenPolicy",
           "install", "uninstall", "last_state", "be_core"]

# -*- coding: utf-8 -*-
"""拒单分类（PRECISION-09）—— 「执行有效性错误」与「策略/风控取消」的唯一分界。

为什么必须分开
──────────────
引擎里「没成交」有两种完全不同的含义，混在一起就会得出错误结论：

  执行有效性错误（execution_validity）
      撮合层或合约规格层面这笔单**根本不成立**：原价非法/非正、步长非法/非正、
      取整后不可成交（≤0）、数量取整后低于 minQty、名义额低于 minNotional。
      这类拒绝意味着**本次运行的执行链路不可信** ⇒ 必须写 `invalid_reasons`、
      `run_valid=False`、正式放行保持不通过。

  策略/风控取消（strategy_cancel）
      策略自己决定不做：开盘跳空过高、跌回箱体、身份链不可解析、止损非法、
      风控仓位为 0。这是**正常结果**，不得据此把整次运行判为无效。

为什么要有这张表
────────────────
`run_valid = not invalid_reasons`（`engine.py`），所以「往 `invalid_reasons` 里写什么」
直接等价于「什么情况下宣布运行无效」。这个判断原本散落在 6 个 except/if 分支里，
PRECISION-08 复现的缺陷正是入场三处漏写。集中登记一次，engine 只调用
`mark_if_execution_invalid()`，新增原因时必须显式登记。

未登记的原因默认 **不判无效**（fail-open）：新加的策略取消原因不会因为忘记登记
而误杀整次运行。代价是「新加的执行错误可能漏判」—— 由
`test_every_precision_gate_reason_is_registered` 把精度闸门实际会发出的 reason
逐条锁进测试来兜底。
"""
from __future__ import annotations

from typing import Iterable, List

REJECT_CONTRACT_VERSION = "reject_taxonomy_v1.0.0"

# ── 分类标签（写进 `precision.rejects[].class`，便于逐条对账）──
REJECT_CLASS_EXECUTION = "execution_validity"
REJECT_CLASS_STRATEGY = "strategy_cancel"
REJECT_CLASS_UNREGISTERED = "unregistered"

# ── `invalid_reasons` 的阶段前缀：`<阶段>:<原因>` 两段都可追溯 ──
STAGE_ENTRY_FILL = "entry_fill_rejected"
STAGE_ENTRY_QTY = "entry_qty_rejected"
STAGE_ENTRY_NOTIONAL = "entry_notional_rejected"
STAGE_EXIT_GAP = "exit_gap_fill_rejected"
STAGE_EXIT_INTRABAR = "exit_intrabar_fill_rejected"
STAGE_EXIT_EOD = "exit_eod_fill_rejected"

# ── 执行有效性错误（逐条对应真实 raise 点）──
EXECUTION_VALIDITY_REASONS = frozenset({
    # fill.py · SimulatedBarFillAdapter._guard（原价 / 步长）
    "non_finite_or_invalid_price",           # 原价不是可解析的有限数
    "nonpositive_raw_price",                 # 原价 ≤ 0
    "non_finite_or_invalid_price_step",      # 步长不是可解析的有限数
    "invalid_price_step_nonpositive",        # 步长 ≤ 0（含 price_breakdown）
    # fill.py · strict 三段链末端（取整后不可成交）
    "nonpositive_or_nonfinite_entry_fill",
    "nonpositive_or_nonfinite_exit_fill",
    # engine.py · 数量精度闸门（合约规格硬约束）
    "qty_below_min_step",                    # 数量向下取整后 < minQty
    "notional_below_min",                    # 名义额 < minNotional
})

# ── 策略 / 风控取消（**不得**判无效）──
#    用前缀匹配：`decide_entry` 的若干原因带括号实参（如
#    `canceled_fell_back_into_box (open=100.0 < H=101.0)`），精确集合会漏。
STRATEGY_CANCEL_PREFIXES = (
    # event_trend_v0_1.engine_adapter.EventTrendExecutionPolicy.decide_entry
    "candidate_identity_unresolved",
    "candidate_symbol_mismatch",
    "candidate_signal_bar_mismatch",
    "canceled_gap_above_0.5a",
    "canceled_fell_back_into_box",
    "invalid_signal_stop",
    "nonpositive_qty",
    "notional_cap_exceeded_zero_qty",
    # trading_core.risk.check_entry（本金风险 / 并发 / 保证金）
    "duplicate_symbol_active",
    "max_concurrent_trades_reached",
    "open_gapped_beyond_stop",
    "nonpositive_equity",
    "zero_unit_risk",
    "portfolio_risk_cap_exceeded",
    "portfolio_margin_cap_exceeded",
)

# ── 明确未纳入本轮分类的原因（默认 ⇒ 不判无效，保持既有行为）──
#    `participation_volume_unavailable` / `participation_cap_zero_qty`：
#    属可选执行约束（`participation_rate`，默认 None）的数据缺失 fail-closed。
#    它既不是「价格/规格不成立」，也不是「策略主动不做」，本轮不擅自归类，
#    分类结果落在 `unregistered`，是否判无效留给单独工单决定。
UNREGISTERED_KNOWN_REASONS = frozenset({
    "participation_volume_unavailable",
    "participation_cap_zero_qty",
})


def classify_reject(reason: str) -> str:
    """把一条拒单原因归到三类之一。未登记 ⇒ `unregistered`（不判无效）。"""
    r = str(reason or "")
    if r in EXECUTION_VALIDITY_REASONS:
        return REJECT_CLASS_EXECUTION
    for p in STRATEGY_CANCEL_PREFIXES:
        if r.startswith(p):
            return REJECT_CLASS_STRATEGY
    return REJECT_CLASS_UNREGISTERED


def is_execution_validity(reason: str) -> bool:
    return classify_reject(reason) == REJECT_CLASS_EXECUTION


def mark_if_execution_invalid(invalid_reasons: List[str], stage: str,
                              reason: str) -> bool:
    """执行有效性错误 ⇒ 追加 `<stage>:<reason>` 并返回 True；其余一律不写。

    幂等：同一原因只登记一次（坏数据可能在多根 bar 上重复触发，不让列表无限增长）。
    """
    if not is_execution_validity(reason):
        return False
    entry = f"{stage}:{reason}"
    if entry not in invalid_reasons:
        invalid_reasons.append(entry)
    return True


def invalid_reason_stages() -> Iterable[str]:
    """全部阶段前缀（供测试/审计枚举）。"""
    return (STAGE_ENTRY_FILL, STAGE_ENTRY_QTY, STAGE_ENTRY_NOTIONAL,
            STAGE_EXIT_GAP, STAGE_EXIT_INTRABAR, STAGE_EXIT_EOD)


__all__ = [
    "REJECT_CONTRACT_VERSION",
    "REJECT_CLASS_EXECUTION", "REJECT_CLASS_STRATEGY", "REJECT_CLASS_UNREGISTERED",
    "STAGE_ENTRY_FILL", "STAGE_ENTRY_QTY", "STAGE_ENTRY_NOTIONAL",
    "STAGE_EXIT_GAP", "STAGE_EXIT_INTRABAR", "STAGE_EXIT_EOD",
    "EXECUTION_VALIDITY_REASONS", "STRATEGY_CANCEL_PREFIXES",
    "UNREGISTERED_KNOWN_REASONS",
    "classify_reject", "is_execution_validity", "mark_if_execution_invalid",
    "invalid_reason_stages",
]

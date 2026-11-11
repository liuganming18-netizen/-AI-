# -*- coding: utf-8 -*-
"""Shared Trading Core · 资金费结算（整体搬移，无第二实现）。

模型
────
`settlement_events`（默认，fail-closed）：消费真实结算事件 `(time_ms, rate, mark_price)`，
判据 `last_funding_ms < ev_ms <= t_ms`（**毫秒**，不截秒）；缺 rate/mark ⇒ 记 `funding_gap`，
**不补 0**。
`assumption_fixed_8h`：**显式假设模型**（结果恒带 `funding_is_assumption=True`）。

会计口径（GM-FUNDING-ACCOUNTING-REWORK-03）
──────────────────────────────────────────
资金费分两个桶，**永不混算**：

  `confirmed`    —— 进 `pos.cum_funding_fee` 与**现金**。成立条件三条全满足：
                    ① 真实结算事件（不是模型推的）；
                    ② PIT 可见（事件时刻不晚于当前观察时刻）；
                    ③ 能证明结算时点持仓仍在（有精确成交时刻可判先后）。
  `hypothetical` —— 只进 `pos.funding_hypothetical`，**不动现金、不进已实现 PnL、
                    不进累计已确认资金费、不进已确认权益**。来源两类：
                    ① `assumption_fixed_8h` 模型推出来的费用；
                    ② 盘中顺序客观未知（只有 OHLC）时，落在该 bar 内的真实事件。

搬移自 `oi_resonance_v1/execution.py`，算术逐字保留；本版只**增加分桶**，
不改变任何既有算术与判据。
"""
from __future__ import annotations

import math
from typing import List, Optional

from .contracts import (FUNDING_BUCKET_CONFIRMED, FUNDING_BUCKET_HYPOTHETICAL,
                        INTRABAR_ORDER_CONFIRMED)
from .position import Position

FUNDING_CONTRACT_VERSION = "funding_contract_v2.0.0"

FUNDING_MODEL_SETTLEMENT = "settlement_events"
FUNDING_MODEL_ASSUMPTION = "assumption_fixed_8h"


def funding_events_ms(events, default_unit: Optional[str] = None) -> List[dict]:
    """规范化资金费事件 → **毫秒**（保留原始精度，不截秒）。

    接受（按优先级）：
      · `time_ms` / `funding_time_ms`：毫秒，原样保留；
      · `time_sec`：秒 ⇒ ×1000；
      · `funding_time` / `time`：币安口径**毫秒**；仅当事件自带 `time_unit="s"`
        或调用方 `default_unit="s"` 时按秒 ×1000。
    未识别 / 缺时间字段 ⇒ 跳过（**不猜单位、不补 0**）。返回按 `time_ms` 升序。
    """
    out: List[dict] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        unit = ev.get("time_unit") or default_unit
        t_ms: Optional[int] = None
        if ev.get("time_ms") is not None:
            t_ms = int(round(float(ev["time_ms"])))
        elif ev.get("funding_time_ms") is not None:
            t_ms = int(round(float(ev["funding_time_ms"])))
        elif ev.get("time_sec") is not None:
            t_ms = int(round(float(ev["time_sec"]) * 1000.0))
        else:
            for key in ("funding_time", "time"):
                if ev.get(key) is not None:
                    v = float(ev[key])
                    t_ms = int(round(v * 1000.0)) if unit == "s" else int(round(v))
                    break
        if t_ms is None:
            continue
        out.append({"time_ms": t_ms, "time_sec": t_ms // 1000,
                    "rate": ev.get("rate"), "mark_price": ev.get("mark_price")})
    out.sort(key=lambda x: x["time_ms"])
    return out


# 兼容旧名（旧内部调用点已全部更新；保留别名避免既有引用断裂）
_funding_events_ms = funding_events_ms
_funding_events_by_sec = funding_events_ms


def _valid_rate(r) -> bool:
    return (r is not None and isinstance(r, (int, float)) and not isinstance(r, bool)
            and math.isfinite(float(r)))


def _valid_mark(m) -> bool:
    return (m is not None and isinstance(m, (int, float)) and not isinstance(m, bool)
            and math.isfinite(float(m)) and float(m) > 0)


def _book(pos: Position, cost: float, bucket: str, detail: dict) -> None:
    """把一笔资金费记入指定桶。**唯一的记账入口**，防止两条路径各写一份。"""
    if bucket == FUNDING_BUCKET_CONFIRMED:
        pos.cum_funding_fee += cost
        pos.last_funding_confirmed_ms = int(detail.get("time_ms") or 0) or None
    else:
        pos.funding_hypothetical += cost
        pos.funding_hypothetical_detail.append(dict(detail))


def settle_funding(pos: Position, cash: float, *, model: str, t_ms: int,
                   open_px: float, rate, events: List[dict],
                   funding_is_assumption: bool,
                   bucket: Optional[str] = None) -> float:
    """相位 A②：资金费结算。缺 rate/mark ⇒ 记 `funding_gap`，**不补 0**。

    `bucket` 缺省按 `funding_is_assumption` 推导：
      假设模型 ⇒ `hypothetical`（**不动现金**）；
      结算事件 ⇒ `confirmed`（可证明持仓仍在 ⇒ 动现金）。
    调用方可以显式指定 `hypothetical`，用于「事件真实但先后不可证明」的场景。
    """
    if bucket is None:
        bucket = (FUNDING_BUCKET_HYPOTHETICAL if funding_is_assumption
                  else FUNDING_BUCKET_CONFIRMED)

    if model == FUNDING_MODEL_ASSUMPTION:
        # 模型推出来的费用**永远**是假设：即使调用方传了 confirmed 也不认。
        bucket = FUNDING_BUCKET_HYPOTHETICAL
        t_sec = int(t_ms // 1000)
        if t_sec % 28800 != 0 or (t_ms - pos.last_funding_ms) < 28800_000:
            return cash
        if not _valid_rate(rate):
            pos.funding_gaps.append({"time_ms": int(t_ms), "time_sec": t_sec,
                                     "reason": "funding_rate_missing_assumption_model"})
            pos.last_funding_ms = int(t_ms)
            return cash
        cost = (open_px * pos.qty) * float(rate) * pos.direction
        _book(pos, cost, bucket,
              {"time_ms": int(t_ms), "rate": float(rate), "mark_price": float(open_px),
               "reason": "assumption_fixed_8h"})
        pos.last_funding_ms = int(t_ms)
        return cash                                   # 假设费不动现金

    # settlement_events：消费真实结算事件（毫秒）
    for ev in events or []:
        ev_ms = int(ev.get("time_ms"))
        if not (pos.last_funding_ms < ev_ms <= int(t_ms)):
            continue
        r, m = ev.get("rate"), ev.get("mark_price")
        ok_r, ok_m = _valid_rate(r), _valid_mark(m)
        if not (ok_r and ok_m):
            pos.funding_gaps.append({
                "time_ms": ev_ms, "time_sec": ev_ms // 1000,
                "reason": ("funding_event_missing_fields:"
                           + ("rate" if not ok_r else "") + ("mark" if not ok_m else "")),
                "rate": r, "mark_price": m})
            pos.last_funding_ms = ev_ms
            continue
        cost = (float(m) * pos.qty) * float(r) * pos.direction
        _book(pos, cost, bucket,
              {"time_ms": ev_ms, "rate": float(r), "mark_price": float(m),
               "reason": ("settled_event_confirmed"
                          if bucket == FUNDING_BUCKET_CONFIRMED
                          else "settled_event_ordering_unknown")})
        pos.last_funding_ms = ev_ms
        if bucket == FUNDING_BUCKET_CONFIRMED:
            cash -= cost
    return cash


def settle_intrabar_ambiguous(pos: Position, *, events: List[dict],
                              bar_open_ms: int, bar_close_ms: int,
                              execution_time_ms: Optional[int] = None,
                              status: str = "INTRABAR_ORDER_UNKNOWN",
                              ambiguous_out: Optional[dict] = None) -> float:
    """平仓时点结算：把「落在本 bar 内、但先后不可证明」的结算事件计入假设桶。

    返回**新增的假设费用合计**（正数=支出方向，与 `cum_funding_fee` 同符号约定）。

    规则（按可证明性递减）：
      ① `execution_time_ms` 有值且 `status == CONFIRMED`
         ⇒ 事件 `<= execution_time_ms` 可证明「先结算后成交」⇒ **confirmed**。
           注意：本函数**不改现金**，confirmed 的现金扣减由调用方按返回值处理。
      ② 无精确成交时刻（只有 OHLC）
         ⇒ 事件落在 `(bar_open_ms, bar_close_ms]` 内 ⇒ **hypothetical**（先后不可判定）。
      ③ 事件在 bar 之外 ⇒ 不属于本 bar，忽略。
    缺 rate/mark ⇒ 记 gap，不计费、不补 0。

    `ambiguous_out`（REWORK-04 / D3，可选出参）
    ─────────────────────────────────────────
    传入 dict 时回填**本次调用实际看到的顺序可证明性证据**，供调用方据以决定
    `intrabar_order_status`，避免「无条件标 UNKNOWN」：

      · `n_hypothetical` —— 落进不可判定窗口 `(lo, hi]` 的事件数。**与 rate/mark
        是否可用无关**：缺字段的事件同样「先后不可判定」，只是没记账而已。
      · `n_confirmed`    —— 走可证明分支（①）的事件数。
      · `window_open_ms` / `window_close_ms` —— 实际生效的判定窗口。

    该参数不改变返回值，也不改变任何既有调用方的行为（默认 None 即不填）。
    """
    lo = max(int(pos.last_funding_ms), int(bar_open_ms))
    hi = int(bar_close_ms)
    new_hyp = 0.0
    n_confirmed = 0
    n_hypothetical = 0
    confirmed = (status == INTRABAR_ORDER_CONFIRMED and execution_time_ms is not None)
    for ev in events or []:
        ev_ms = int(ev.get("time_ms"))
        if ev_ms <= lo:
            continue
        if confirmed:
            if ev_ms > int(execution_time_ms):
                continue
            bucket = FUNDING_BUCKET_CONFIRMED
            n_confirmed += 1
        else:
            if ev_ms > hi:
                continue
            bucket = FUNDING_BUCKET_HYPOTHETICAL
            # 落进不可判定窗口即计数，**在 rate/mark 校验之前**：
            # 缺字段的事件同样「先后不可判定」，只是没有金额可记。
            n_hypothetical += 1
        r, m = ev.get("rate"), ev.get("mark_price")
        ok_r, ok_m = _valid_rate(r), _valid_mark(m)
        if not (ok_r and ok_m):
            pos.funding_gaps.append({
                "time_ms": ev_ms, "time_sec": ev_ms // 1000,
                "reason": ("funding_event_missing_fields:"
                           + ("rate" if not ok_r else "") + ("mark" if not ok_m else "")),
                "rate": r, "mark_price": m})
            pos.last_funding_ms = ev_ms
            continue
        cost = (float(m) * pos.qty) * float(r) * pos.direction
        _book(pos, cost, bucket,
              {"time_ms": ev_ms, "rate": float(r), "mark_price": float(m),
               "reason": ("intrabar_ordering_unknown"
                          if bucket == FUNDING_BUCKET_HYPOTHETICAL
                          else "intrabar_ordering_confirmed")})
        pos.last_funding_ms = ev_ms
        if bucket == FUNDING_BUCKET_HYPOTHETICAL:
            new_hyp += cost
    if ambiguous_out is not None:
        ambiguous_out.update({"n_hypothetical": n_hypothetical,
                              "n_confirmed": n_confirmed,
                              "window_open_ms": lo,
                              "window_close_ms": hi})
    return float(new_hyp)


# 兼容旧私有名（既有回归测试按 `X._settle_funding` 调用）
_settle_funding = settle_funding

__all__ = ["FUNDING_CONTRACT_VERSION", "FUNDING_MODEL_SETTLEMENT",
           "FUNDING_MODEL_ASSUMPTION", "funding_events_ms", "settle_funding",
           "settle_intrabar_ambiguous",
           "_funding_events_ms", "_funding_events_by_sec", "_settle_funding"]

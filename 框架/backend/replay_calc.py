# -*- coding: utf-8 -*-
"""ACCUM-EARLY-02｜回放计算（**唯一实现**，替代手写算术）。

为什么要有这个模块
──────────────────
ACCUM-EARLY-01 的 `07_事件回放构建.py` 自己手写了一套成交/止损/资金费算术，
复核后发现七处错误（见下）。手写算术最大的问题是**它会和获批实现慢慢分叉**，
而分叉不会报错，只会给出一个看起来合理的数字。

所以本模块不重写算术，而是**调用获批的唯一实现**：
  · 入场价 / 初始止损 / 数量 / 名义额 → `strategies.wx_execution.apply_order_rounding`
  · 合约规格 → `paper_engine.contract_limits`
  · 资金费 → `trading_core.funding.settle_funding`
  · 未实现盈亏 → `trading_core.position.unrealized_pnl` / `unrealized_net_open`

七项修复对应
────────────
① `stop0` 与数量/名义/手续费**统一由最终模拟成交价**计算：
   旧代码 `stop0 = 原始开盘价×0.95`、`qty = 25/(原始开盘价−stop0)`、
   `notional = qty×原始开盘价`，而含滑点成交价在后面才算 ⇒ 三处口径不一致。
   现在整条链由 `apply_order_rounding` 一次算完（它内部就是
   `raw → 滑点 → 取整 → 再由取整后价算止损 → 再由同一止损距离算数量`）。
   两套滑点口径**各自完整跑一遍**（不是只改一个乘数）。
② 止损路径按**持仓生命周期截断**：旧代码把 `snap+30 天`的 4h 全算进跟踪路径，
   导致「最终止损」是退出之后的值。现在只取 `入场时刻 < 4h 收盘 ≤ 退出时刻`。
③ 资金费**逐次真实结算事件**核算：`cost = 结算标记价 × 当时数量 × 费率 × 方向`，
   `>0 支付 / <0 收取`（符号保留）；缺 rate 或 mark ⇒ 记 `funding_gap`，**不补 0**；
   退出时刻与结算时刻相距 ≤15 分钟 ⇒ 标记「顺序不确定」。
④ 参与率**未通过不得计全额成交**：参与率进入 `apply_order_rounding` 的数量上限，
   超限 ⇒ 缩量；缩量后低于最小可下单量 ⇒ 拒单（拒单则无持仓、无盈亏）。
   旧代码只记一个布尔标记，盈亏仍按全额算。
⑤ 期末未平仓**只计未实现估值**，不进已实现净额。
⑥ 初始止损退出与跟踪止损退出**分类准确**；跳空（开盘已越过止损）与
   盘中触及（最低价触及但开盘未越过）**分开核验**。
⑦ 跳空成交模型缺批准依据 ⇒ 提供**三模型敏感性对照** + 推荐理由（不改默认口径）。

纪律
────
· 不读未收盘 bar；不插值；不补零；取不到就写缺口。
· 每个数字都带**来源字段**，让人能回到原始表复算。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

M15 = 900_000
H4 = 14_400_000

#: 退出时刻与资金费结算时刻相距在这个范围内 ⇒ 先后顺序不确定（标记，不擅自定序）
FUNDING_ORDER_UNCERTAIN_MS = M15

#: 两套口径的**完整**参数。注意：它们不只是滑点不同 —— 本金、单名上限、
#: 总敞口上限、组合风险上限、参与率上限全都不同。只改滑点会让读者以为
#: 差异只来自滑点，那是误导。
CALIBERS: Dict[str, Dict[str, Any]] = {
    "生效视图_5bps": {
        "来源": ("数据/研究输入/最新唐奇安威科夫接通与收尾_2026-10-11/"
                 "5000U收尾与回测条件终验6/10_研究参数生效视图_v2_2_user5000_shared.json"),
        "initial_equity": 5000.0,
        "risk_per_trade_fraction": 0.005,
        "fee_each_side": 0.0005,
        "slippage_each_side": 0.0005,
        "single_name_notional_cap": 0.10,
        "gross_long_notional_cap": 0.80,
        "participation_cap": 0.01,
        "leverage": 1.0,
        "cash_reserve_fraction": 0.20,
        "portfolio_risk_cap": 0.05,
    },
    "策略包_3bps": {
        "来源": "策略/WyckoffMaster/event_trend_v0_1/config.py · make_ft_low4h_config()",
        "initial_equity": 2000.0,
        "risk_per_trade_fraction": 0.005,
        "fee_each_side": 0.0005,
        "slippage_each_side": 0.0003,
        "single_name_notional_cap": 0.30,
        "gross_long_notional_cap": 0.90,
        "participation_cap": 0.001,
        "leverage": 1.0,
        "cash_reserve_fraction": 0.20,
        "portfolio_risk_cap": 0.015,
    },
}

#: 冻结规则 FT-LOW4H 的止损/跟踪参数（两套口径共用，属于策略身份而非成本口径）
STOPLOSS_S = 0.05
TRAIL_D = 0.05
TRAIL_LOOKBACK_4H_BARS = 1


# ══════════════════════════ 小工具 ══════════════════════════

def _iso(ms) -> Optional[str]:
    if ms is None:
        return None
    import datetime as dt
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x, nd: int = 12):
    return None if x is None else round(float(x), nd)


def _limits_for(symbol: str) -> Optional[dict]:
    """合约规格（同一份 CSV、同一个函数，不另写）。"""
    import paper_engine as PE
    return PE.contract_limits(symbol)


# ══════════════════════════ ① 入场（唯一实现）══════════════════════════

def plan_entry(*, symbol: str, raw_open: float, prev_quote_volume: Optional[float],
               equity: float, gross_open_notional: float, free_cash: float,
               portfolio_risk_open: float, caliber: dict,
               direction: str = "long") -> dict:
    """入场计划：价 / 止损 / 数量 / 名义额 / 手续费，**一次算完**。

    直接调用获批实现 `apply_order_rounding`。它内部顺序就是工单要求的顺序：
        ① `raw_open × (1 ± 滑点)` → 按步进**保守取整** ⇒ `entry_price`
        ② 由 `entry_price`（**不是**原始开盘价）按 `stoploss_s` 算初始止损 ⇒ `stop_price`
        ③ 由**同一个** `stop_dist = entry_price − stop_price` 算风险数量
        ④ 参与率 / 单名 / 总敞口 / 预留现金各上限取最小 ⇒ 步进向下截断
        ⑤ 最小量、最小金额、单笔与组合风险复核 ⇒ 任一超限**整单拒绝**

    所以「两套滑点各自完整执行」是**结构保证**的：换一个滑点值，止损、数量、
    名义额、手续费会一起变，不可能出现「价变了但数量没变」的分叉。
    """
    from strategies.wx_execution import apply_order_rounding

    limits = _limits_for(symbol)
    if limits is None:
        return {"status": "rejected", "reject": "missing_contract_spec",
                "symbol": symbol,
                "说明": "该币没有合约规格 CSV 行 ⇒ 按获批规则拒单，不设默认步进"}
    rr = apply_order_rounding(
        raw_open=float(raw_open),
        limits=limits,
        risk_per_trade_fraction=float(caliber["risk_per_trade_fraction"]),
        equity=float(equity),
        slippage_each_side=float(caliber["slippage_each_side"]),
        gross_open_notional=float(gross_open_notional),
        free_cash=float(free_cash),
        leverage=float(caliber["leverage"]),
        cash_reserve_fraction=float(caliber["cash_reserve_fraction"]),
        single_name_notional_cap=float(caliber["single_name_notional_cap"]),
        gross_long_notional_cap=float(caliber["gross_long_notional_cap"]),
        prev_quote_volume=prev_quote_volume,
        participation_cap=float(caliber["participation_cap"]),
        portfolio_risk_open=float(portfolio_risk_open),
        portfolio_risk_cap=float(caliber["portfolio_risk_cap"]),
        stoploss_s=STOPLOSS_S,
        strategy_id="FT-LOW4H",
        direction=direction,
    )
    if rr.get("reject"):
        return {"status": "rejected", "reject": str(rr["reject"]), "symbol": symbol,
                "detail": {k: v for k, v in rr.items() if k != "limits"},
                "说明": "获批实现判定的拒单原因；拒单 ⇒ 无持仓、无盈亏，不得按全额计"}
    entry_price = float(rr["entry_price"])
    notional = float(rr["notional"])
    fee = notional * float(caliber["fee_each_side"])
    # 参与率是不是**真正把数量压下来的那个约束**：把参与率上限关掉再算一遍，
    # 两次数量不同才算「参与率生效」。不能用「名义额 < 上限」判断 ——
    # 那只能说明没超限，说明不了是谁在限。
    rr_free = apply_order_rounding(
        raw_open=float(raw_open), limits=limits,
        risk_per_trade_fraction=float(caliber["risk_per_trade_fraction"]),
        equity=float(equity), slippage_each_side=float(caliber["slippage_each_side"]),
        gross_open_notional=float(gross_open_notional), free_cash=float(free_cash),
        leverage=float(caliber["leverage"]),
        cash_reserve_fraction=float(caliber["cash_reserve_fraction"]),
        single_name_notional_cap=float(caliber["single_name_notional_cap"]),
        gross_long_notional_cap=float(caliber["gross_long_notional_cap"]),
        prev_quote_volume=prev_quote_volume, participation_cap=0.0,
        portfolio_risk_open=float(portfolio_risk_open),
        portfolio_risk_cap=float(caliber["portfolio_risk_cap"]),
        stoploss_s=STOPLOSS_S, strategy_id="FT-LOW4H", direction=direction,
    )
    qty_free = (float(rr_free["qty"]) if not rr_free.get("reject") else None)
    capped = bool(qty_free is not None and float(rr["qty"]) < qty_free - 1e-12)
    return {
        "status": "ok", "symbol": symbol,
        "raw_open": float(raw_open),
        "entry_price_raw_after_slip": float(rr["entry_price_raw"]),
        "entry_price": entry_price,
        "price_step_delta": float(rr["price_step_delta"]),
        "initial_stop_price": float(rr["stop_price"]),
        "stop_dist": float(rr["stop_dist"]),
        "qty": float(rr["qty"]),
        "qty_raw": float(rr["qty_raw"]),
        "qty_step_delta": float(rr["qty_step_delta"]),
        "notional": notional,
        "planned_risk": float(rr["planned_risk"]),
        "planned_risk_fraction": float(rr["planned_risk_fraction"]),
        "min_orderable_qty": float(rr["min_orderable_qty"]),
        "entry_fee": fee,
        "fee_rate": float(caliber["fee_each_side"]),
        "slippage_each_side": float(caliber["slippage_each_side"]),
        "limits": dict(limits),
        # 参与率：是否真的缩量了（而不是只看「超没超」）
        "participation": {
            "cap": float(caliber["participation_cap"]),
            "prev_quote_volume": prev_quote_volume,
            "cap_notional": (None if prev_quote_volume is None
                             else float(prev_quote_volume) * float(caliber["participation_cap"])),
            "qty_with_cap": float(rr["qty"]),
            "qty_without_cap": qty_free,
            "qty_without_cap_reject": rr_free.get("reject"),
            "capped": capped,
            "说明": ("把参与率上限关掉再算一遍作对照：`qty_with_cap < qty_without_cap` "
                     "才算参与率真的压了数量；两者相同说明限制来自别的上限。"
                     "参与率进入数量上限 ⇒ 超限时**缩量**；缩量后低于最小可下单量才会整单拒绝"),
        },
        "口径": ("价/止损/数量/名义/手续费全部由获批实现 `apply_order_rounding` "
                 "按**取整后成交价**一次算完"),
    }


# ══════════════════════════ ②⑥ 止损路径 + 退出 ══════════════════════════

def build_stop_path(*, entry_ms: int, initial_stop: float, bars_4h: List[dict],
                    exit_ms: Optional[int] = None) -> List[dict]:
    """跟踪止损路径，**按持仓生命周期截断**。

    规则（冻结规则 FT-LOW4H）：`trailing_trigger_r=0`（入场即跟踪）、
    `trailing_lookback_4h_bars=1`、`trail_d=0.05`
    ⇒ 每根**新闭合** 4h 的低点 × (1−trail_d) 作为候选，**只上移**。

    截断（工单第 ② 项）：只取 `entry_ms < close_time ≤ exit_ms`。
    旧代码取到 `snap+30 天`，于是「最终止损」是退出**之后**的值 —— 那个数
    既不能用来判退出，也不能用来算盈亏，只能误导人。
    """
    path = [{"effective_ms": int(entry_ms), "utc": _iso(entry_ms),
             "confirm_ms": int(entry_ms), "confirm_utc": _iso(entry_ms),
             "stop": float(initial_stop), "kind": "init",
             "来源": "取整后成交价 ×(1−stoploss_s)，再按步进保守取整"}]
    stop = float(initial_stop)
    for b in bars_4h or []:
        ct = int(b["close_time"])
        # 确认时刻：4h 闭合整点（如 11:59:59.999 -> 12:00:00.000）
        confirm_ms = (ct // M15 + 1) * M15 if ct % M15 != 0 else ct
        if confirm_ms <= int(entry_ms):
            continue
        # 最早生效时刻：用户已定时序（ACCUM-EARLY-04-D），确认于 12:00 时，最早 12:15 生效，不得回填 12:00
        effective_ms = confirm_ms + M15
        if exit_ms is not None and effective_ms > int(exit_ms):
            break
        low = _f(b.get("low"))
        if low is None:
            continue
        cand = low * (1.0 - TRAIL_D)
        if cand > stop:
            stop = cand
            path.append({"effective_ms": effective_ms, "confirm_ms": confirm_ms,
                         "utc": _iso(effective_ms), "confirm_utc": _iso(confirm_ms),
                         "stop": float(stop), "kind": "trail",
                         "来源": f"4h 低点 {low:.12g} ×(1−{TRAIL_D})，确认于 {_iso(confirm_ms)}，最早生效于 {_iso(effective_ms)}"})
    return path


def scan_exit(*, bars_15m: List[dict], entry_ms: int, entry_bar_ms: int,
              stop_path: List[dict], initial_stop: float,
              gap_model: str = "stop_price") -> dict:
    """逐 15m 判定退出，**跳空与盘中分开**，**初始止损与跟踪止损分开**。

    相位顺序（与共享引擎一致，不可换）：
      先按「4h 收盘后下一根 15m 起生效」更新止损，再判本根是否触及。

    触及判据（多头）：
      · **跳空**：`bar.open ≤ 当前止损` ⇒ 开盘就已经在止损之下。
        成交价取决于跳空模型（见 `gap_model`）。
      · **盘中触及**：`bar.open > 当前止损 且 bar.low ≤ 当前止损` ⇒ 盘中打穿。
        成交价 = 当前止损价（保守，不给更优的价）。

    `gap_model`（工单第 ⑦ 项，默认口径未改，只做敏感性）：
      · `stop_price`：按止损价成交（**现行默认**，缺批准依据，偏乐观）
      · `bar_open`  ：按该根开盘价成交（跳空跳多远就亏多远，偏保守）
      · `midpoint`  ：两者中点（折中）
    """
    if gap_model not in ("stop_price", "bar_open", "midpoint"):
        raise ValueError(f"unknown_gap_model:{gap_model}")
    path = sorted(stop_path, key=lambda x: x["effective_ms"])
    pi = 0
    cur_stop = float(initial_stop)
    for b in bars_15m or []:
        ot = int(b["open_time"])
        if ot < int(entry_bar_ms):
            continue
        while pi < len(path) and path[pi]["effective_ms"] <= ot:
            cur_stop = float(path[pi]["stop"])
            pi += 1
        o, lo = _f(b.get("open")), _f(b.get("low"))
        if o is None or lo is None:
            continue
        if o <= cur_stop:
            if gap_model == "bar_open":
                fill = o
            elif gap_model == "midpoint":
                fill = (o + cur_stop) / 2.0
            else:
                fill = cur_stop
            return {"hit": True, "kind": "gap",
                    "bar_open_ms": ot, "bar_open_utc": _iso(ot),
                    "bar_open": o, "bar_low": lo,
                    "stop_used": cur_stop,
                    "stop_kind": ("init" if abs(cur_stop - initial_stop) < 1e-15 else "trail"),
                    "exit_fill": float(fill),
                    "gap_model": gap_model,
                    "gap_size": float(cur_stop - o),
                    "note": ("开盘已越过止损 ⇒ 跳空；成交价由跳空模型决定"
                             if gap_model != "stop_price"
                             else "开盘已越过止损，但按止损价成交（默认口径，偏乐观）")}
        if lo <= cur_stop:
            return {"hit": True, "kind": "intrabar",
                    "bar_open_ms": ot, "bar_open_utc": _iso(ot),
                    "bar_open": o, "bar_low": lo,
                    "stop_used": cur_stop,
                    "stop_kind": ("init" if abs(cur_stop - initial_stop) < 1e-15 else "trail"),
                    "exit_fill": float(cur_stop),
                    "gap_model": gap_model,
                    "gap_size": 0.0,
                    "note": "盘中触及 ⇒ 按止损价成交"}
    return {"hit": False, "kind": "no_exit_in_window",
            "note": "窗口内未触及止损 ⇒ 期末未平仓，只计未实现估值"}


def classify_exit_reason(*, kind: str, stop_kind: str) -> str:
    """工单第 ⑥ 项：初始止损退出 / 跟踪止损退出 / 期末未平仓，**分类不混**。"""
    if kind == "no_exit_in_window":
        return "open_at_window_end"
    if stop_kind == "init":
        return "initial_stop_loss" if kind == "intrabar" else "initial_stop_gap"
    return "trailing_stop_loss" if kind == "intrabar" else "trailing_stop_gap"


# ══════════════════════════ ③ 资金费逐笔 ══════════════════════════

def settle_funding_events(*, symbol: str, qty: float, direction: int,
                          entry_ms: int, exit_ms: int,
                          funding_events: List[dict]) -> dict:
    """逐次真实结算事件核算资金费。

    用获批实现 `trading_core.funding.settle_funding`（`settlement_events` 模型）：
      · 判据 `entry_ms < 结算时刻 ≤ exit_ms`（毫秒，不截秒）；
      · `cost = 结算标记价 × 当时数量 × 费率 × 方向`；
      · `cost > 0` ⇒ 多头**支付**；`cost < 0` ⇒ **收取**（符号保留，不取绝对值）；
      · 缺 rate 或 mark ⇒ 记 `funding_gap`，**不补 0**、不计入成本。

    与旧代码的差别：旧代码 `sum(rates) × 入场时名义额`，
    ① 用的是**入场时**名义额而不是结算时的数量 × 标记价；
    ② 把费率直接求和 ⇒ 丢了逐笔的符号与量级差异；
    ③ 缺字段时静默跳过，看起来像「没有资金费」。
    """
    from 策略.WyckoffMaster.trading_core.funding import funding_events_ms, settle_funding
    from 策略.WyckoffMaster.trading_core.position import Position

    pos = Position(
        symbol=symbol, direction=int(direction), signal_type=0,
        entry_time=int(entry_ms), entry_time_sec=int(entry_ms) // 1000,
        entry_price=0.0, qty=float(qty), initial_stop=0.0, current_stop=0.0,
        entry_fee=0.0, last_mark_price=0.0,
        last_funding_ms=int(entry_ms),
    )
    evs = funding_events_ms(funding_events or [])
    # **只保留持仓区间内的结算事件**（`entry_ms < 结算时刻 ≤ exit_ms`）。
    # 不过滤会把区间外的事件也记成「已结算、金额 0」，看起来像「这些时段没有资金费」，
    # 实际是「这些时段不持仓」—— 两件事必须分开。
    in_window = [e for e in evs if int(entry_ms) < int(e["time_ms"]) <= int(exit_ms)]
    out_window = len(evs) - len(in_window)
    cash = 0.0
    rows: List[dict] = []
    for ev in in_window:
        before = pos.cum_funding_fee
        gaps_before = len(pos.funding_gaps)
        cash = settle_funding(pos, cash, model="settlement_events",
                             t_ms=int(exit_ms), open_px=0.0,
                             rate=ev.get("rate"), events=[ev],
                             funding_is_assumption=False)
        amt = pos.cum_funding_fee - before
        uncertain = abs(int(exit_ms) - int(ev["time_ms"])) <= FUNDING_ORDER_UNCERTAIN_MS
        if len(pos.funding_gaps) > gaps_before:
            rows.append({"结算时刻_utc": _iso(ev["time_ms"]),
                         "status": "gap",
                         "原因": pos.funding_gaps[-1].get("reason"),
                         "rate": ev.get("rate"), "mark_price": ev.get("mark_price"),
                         "顺序不确定": bool(uncertain),
                         "计入成本": False})
            continue
        rows.append({"结算时刻_utc": _iso(ev["time_ms"]),
                     "status": "settled",
                     "rate": ev.get("rate"),
                     "mark_price": ev.get("mark_price"),
                     "qty": float(qty),
                     "金额": float(amt),
                     "方向": ("支付" if amt > 0 else ("收取" if amt < 0 else "零")),
                     "顺序不确定": bool(uncertain),
                     "计入成本": True})
    return {
        "模型": "settlement_events（逐次真实结算，符号保留）",
        "公式": "结算标记价 × 当时数量 × 费率 × 方向；>0 支付 / <0 收取",
        "持仓区间": {"from_utc": _iso(entry_ms), "to_utc": _iso(exit_ms)},
        "事件数": len(rows),
        "区间外事件数": int(out_window),
        "已结算": sum(1 for r in rows if r["status"] == "settled"),
        "缺口数": sum(1 for r in rows if r["status"] == "gap"),
        "成本合计": float(pos.cum_funding_fee),
        "明细": rows,
        "顺序不确定事件数": sum(1 for r in rows if r["顺序不确定"]),
        "说明": ("`成本合计 > 0` 表示净支付；缺口不计入、不补零；"
                 "`顺序不确定=True` 表示退出与结算相距 ≤15 分钟，先后无法从日线级数据判定；"
                 "`区间外事件数` 是同一窗口内但不在持仓区间的事件，**不计入**"),
    }


# ══════════════════════════ ⑤ 盈亏口径 ══════════════════════════

def pnl_breakdown(*, entry_price: float, entry_fee: float, qty: float,
                  direction: int, fee_rate: float,
                  exit_fill: Optional[float], funding_cost: float,
                  mark_price_at_end: Optional[float] = None) -> dict:
    """已实现净额与未实现估值**分开**，期末未平仓不得混进已实现。

    · 已平仓：`gross = (exit_fill − entry_price) × qty × dir`，
      `net = gross − entry_fee − exit_fee − funding_cost`；
    · 未平仓：`realized_net = None`，只给 `unrealized_*`；
      `unrealized_gross = (mark − entry) × qty × dir`；
      `unrealized_net = gross − entry_fee − cum_funding − 预估出场费`
      （与 `trading_core.position.unrealized_net_open` 同口径）。
    """
    if exit_fill is None:
        mark = mark_price_at_end
        if mark is None:
            return {"已平仓": False, "realized_net": None,
                    "unrealized_gross": None, "unrealized_net": None,
                    "缺失": "期末未平仓且没有可用的收盘价估值"}
        gross_u = (float(mark) - float(entry_price)) * float(qty) * int(direction)
        est_exit_fee = float(mark) * float(qty) * float(fee_rate)
        return {
            "已平仓": False,
            "realized_net": None,
            "realized_说明": "期末未平仓 ⇒ **不计已实现**，只给未实现估值",
            "mark_price_at_end": float(mark),
            "unrealized_gross": float(gross_u),
            "unrealized_exit_fee_est": float(est_exit_fee),
            "unrealized_net": float(gross_u - float(entry_fee)
                                    - float(funding_cost) - est_exit_fee),
            "unrealized_口径": "毛额 − 入场费 − 已结算资金费 − 预估出场费（同 unrealized_net_open）",
        }
    exit_fee = float(exit_fill) * float(qty) * float(fee_rate)
    gross = (float(exit_fill) - float(entry_price)) * float(qty) * int(direction)
    return {
        "已平仓": True,
        "exit_fee": float(exit_fee),
        "gross": float(gross),
        "entry_fee": float(entry_fee),
        "funding_cost": float(funding_cost),
        "realized_net": float(gross - float(entry_fee) - exit_fee - float(funding_cost)),
        "realized_口径": "毛额 − 入场费 − 出场费 − 已结算资金费",
    }


# ══════════════════════════ ⑦ 跳空敏感性 ══════════════════════════

def gap_sensitivity(*, entry_price: float, entry_fee: float, qty: float,
                    direction: int, fee_rate: float, funding_cost: float,
                    bars_15m: List[dict], entry_bar_ms: int,
                    stop_path: List[dict], initial_stop: float) -> dict:
    """三种跳空成交模型的净额对照 + 推荐理由（**不改默认口径**）。

    为什么不能自己挑一个：跳空按什么价成交，直接改盈亏，属于口径变更，
    必须由裁决方定。这里只把三个数摆出来，并说明为什么推荐其中一个。
    """
    out: Dict[str, Any] = {}
    for m in ("stop_price", "bar_open", "midpoint"):
        ex = scan_exit(bars_15m=bars_15m, entry_ms=entry_bar_ms,
                       entry_bar_ms=entry_bar_ms, stop_path=stop_path,
                       initial_stop=initial_stop, gap_model=m)
        if not ex.get("hit"):
            out[m] = {"hit": False, "note": "该模型下窗口内未触及止损"}
            continue
        p = pnl_breakdown(entry_price=entry_price, entry_fee=entry_fee, qty=qty,
                          direction=direction, fee_rate=fee_rate,
                          exit_fill=ex["exit_fill"], funding_cost=funding_cost)
        out[m] = {"hit": True, "kind": ex["kind"],
                  "exit_fill": ex["exit_fill"], "stop_used": ex["stop_used"],
                  "bar_open": ex["bar_open"], "gap_size": ex["gap_size"],
                  "realized_net": p.get("realized_net")}
    nets = {k: v.get("realized_net") for k, v in out.items() if v.get("hit")}
    return {
        "模型": {
            "stop_price": "跳空也按止损价成交（现行默认，缺批准依据）",
            "bar_open": "按该根 15m 开盘价成交（跳多远亏多远）",
            "midpoint": "开盘价与止损价的中点",
        },
        "结果": out,
        "净额极差": (None if len(nets) < 2 else
                     round(max(nets.values()) - min(nets.values()), 6)),
        "推荐": "bar_open",
        "推荐理由": (
            "① 交易所撮合的是**真实成交价**，跳空时止损单是市价单，成交在开盘价附近，"
            "不存在「价格跳过了止损却还能按止损价成交」；"
            "② `stop_price` 模型系统性高估成交价 ⇒ 系统性低估亏损，方向上永远偏乐观；"
            "③ `midpoint` 是人为插值，没有任何机制依据，比 `stop_price` 更不可辩护；"
            "④ 推荐只影响**敏感性对照**，默认口径未改 —— 改默认需裁决方批准。"),
        "未改默认": True,
    }


# ══════════════════════════ 总装：一条完整回放 ══════════════════════════

def replay_one(*, symbol: str, entry_bar: dict, prev_bar: Optional[dict],
               bars_15m: List[dict], bars_4h: List[dict],
               funding_events: List[dict], caliber_name: str,
               equity: Optional[float] = None,
               gross_open_notional: float = 0.0,
               portfolio_risk_open: float = 0.0,
               direction: int = 1) -> dict:
    """按指定口径完整回放一条事件（入场 → 止损路径 → 退出 → 成本 → 净额）。"""
    cal = CALIBERS[caliber_name]
    eq = float(cal["initial_equity"] if equity is None else equity)
    entry_ms = int(entry_bar["open_time"])
    pqv = _f(prev_bar.get("quote_volume")) if prev_bar else None

    entry = plan_entry(symbol=symbol, raw_open=float(entry_bar["open"]),
                       prev_quote_volume=pqv, equity=eq,
                       gross_open_notional=gross_open_notional,
                       free_cash=eq, portfolio_risk_open=portfolio_risk_open,
                       caliber=cal,
                       direction=("long" if int(direction) == 1 else "short"))
    if entry.get("status") != "ok":
        return {"caliber": caliber_name, "caliber_params": cal,
                "entry": entry, "status": "rejected",
                "已实现净额": None, "未实现净额": None,
                "说明": "入场即被拒 ⇒ 无持仓、无盈亏；不得按全额成交另算一套"}

    entry_price = entry["entry_price"]
    initial_stop = entry["initial_stop_price"]
    qty = entry["qty"]
    entry_fee = entry["entry_fee"]

    # 先扫一遍退出（生命周期上界未知 ⇒ 用窗口末作为上界，但止损路径要截断到退出时刻）
    window_end_ms = int(bars_15m[-1]["open_time"]) if bars_15m else entry_ms
    path_probe = build_stop_path(entry_ms=entry_ms, initial_stop=initial_stop,
                                 bars_4h=bars_4h, exit_ms=window_end_ms)
    ex = scan_exit(bars_15m=bars_15m, entry_ms=entry_ms,
                   entry_bar_ms=entry_ms, stop_path=path_probe,
                   initial_stop=initial_stop)
    exit_ms = int(ex["bar_open_ms"]) if ex.get("hit") else window_end_ms
    # ② 用**真实退出时刻**重建一次止损路径（截断），保证「最终止损」是持仓期内的值
    stop_path = build_stop_path(entry_ms=entry_ms, initial_stop=initial_stop,
                                bars_4h=bars_4h, exit_ms=exit_ms)

    fund = settle_funding_events(symbol=symbol, qty=qty, direction=int(direction),
                                 entry_ms=entry_ms, exit_ms=exit_ms,
                                 funding_events=funding_events)
    fee_rate = float(cal["fee_each_side"])
    mark_end = _f(bars_15m[-1].get("close")) if (bars_15m and not ex.get("hit")) else None

    pnl = pnl_breakdown(entry_price=entry_price, entry_fee=entry_fee, qty=qty,
                        direction=int(direction), fee_rate=fee_rate,
                        exit_fill=(ex["exit_fill"] if ex.get("hit") else None),
                        funding_cost=fund["成本合计"],
                        mark_price_at_end=mark_end)

    reason = classify_exit_reason(kind=ex.get("kind"), stop_kind=ex.get("stop_kind"))
    sens = gap_sensitivity(entry_price=entry_price, entry_fee=entry_fee, qty=qty,
                           direction=int(direction), fee_rate=fee_rate,
                           funding_cost=fund["成本合计"],
                           bars_15m=bars_15m, entry_bar_ms=entry_ms,
                           stop_path=stop_path, initial_stop=initial_stop)

    return {
        "status": "ok", "caliber": caliber_name, "caliber_params": cal,
        "entry": entry,
        "exit": {**ex, "exit_reason": reason,
                 "exit_utc": _iso(exit_ms)},
        "止损路径": [{k: v for k, v in p.items() if k != "effective_ms"} for p in stop_path],
        "止损路径_条数": len(stop_path),
        "止损路径_截断到": _iso(exit_ms),
        "最终止损_持仓期内": float(stop_path[-1]["stop"]),
        "初始止损": float(initial_stop),
        "止损上移过": bool(len(stop_path) > 1),
        "资金费": fund,
        "盈亏": pnl,
        "已实现净额": pnl.get("realized_net"),
        "未实现净额": pnl.get("unrealized_net"),
        "跳空敏感性": sens,
        "数据边界": {
            "entry_bar_utc": _iso(entry_ms),
            "exit_utc": _iso(exit_ms),
            "bars_15m_used": sum(1 for b in (bars_15m or [])
                                 if int(b["open_time"]) >= entry_ms),
            "bars_4h_used": sum(1 for b in (bars_4h or [])
                                if entry_ms < int(b["close_time"]) <= exit_ms),
            "funding_events_in_window": fund["事件数"],
        },
    }


__all__ = [
    "CALIBERS", "STOPLOSS_S", "TRAIL_D", "TRAIL_LOOKBACK_4H_BARS",
    "FUNDING_ORDER_UNCERTAIN_MS",
    "plan_entry", "build_stop_path", "scan_exit", "classify_exit_reason",
    "settle_funding_events", "pnl_breakdown", "gap_sensitivity", "replay_one",
]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-09 工具 20：拒单原因映射表（从代码反查，不手抄）。

为什么要「反查」
────────────────
映射表如果是手写的，它和代码迟早会分家。这里做三件事：
  1. 从 `fill.py` 里正则抓出每个 `FillRejected("<reason>")` 字面量；
  2. 从 `engine.py` 里抓出两处数量闸门的 reason 字面量；
  3. 逐条问分类器「这属于哪一类」，再与人工判断表对账 —— 任何一条不一致就报错。

输出：映射表_拒单分类.csv / 映射表_拒单分类.md
"""
from __future__ import annotations

import csv
import inspect
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (ROOT, os.path.join(ROOT, "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)

from WyckoffMaster.trading_core import engine as engine_mod          # noqa: E402
from WyckoffMaster.trading_core import fill as fill_mod              # noqa: E402
from WyckoffMaster.trading_core.rejects import (                     # noqa: E402
    REJECT_CLASS_EXECUTION, REJECT_CLASS_STRATEGY, REJECT_CLASS_UNREGISTERED,
    STRATEGY_CANCEL_PREFIXES, UNREGISTERED_KNOWN_REASONS, classify_reject,
)

# 人工判断表（独立于代码，用来对账）：reason -> (位置, 是否应判无效, 依据)
EXPECT = {
    # ── 撮合层：原价 / 步长 ──
    "non_finite_or_invalid_price": (
        "fill.py `_guard`", True, "原价不是可解析的有限数 ⇒ 这笔单的价格不可信"),
    "nonpositive_raw_price": (
        "fill.py `_guard`", True, "原价 ≤ 0 ⇒ 价格源坏了，不是策略决定"),
    "non_finite_or_invalid_price_step": (
        "fill.py `_guard`", True, "步长不可解析 ⇒ 规格链路断了"),
    "invalid_price_step_nonpositive": (
        "fill.py `_guard` / `price_breakdown`", True, "步长 ≤ 0 ⇒ 无法取整，等于没有精度约束"),
    # ── 撮合层：三段链末端 ──
    "nonpositive_or_nonfinite_entry_fill": (
        "fill.py strict 取整后", True, "取整后不可成交（≤0）⇒ 成交价非法"),
    "nonpositive_or_nonfinite_exit_fill": (
        "fill.py strict 取整后", True, "同上，出场侧（P06 已按此处理）"),
    # ── 数量精度闸门 ──
    "qty_below_min_step": (
        "engine.py 相位 C 数量闸门", True, "数量向下取整后 < minQty ⇒ 交易所根本不收这张单"),
    "notional_below_min": (
        "engine.py 相位 C 名义额闸门", True, "名义额 < minNotional ⇒ 同上"),
}

# 策略/风控取消（不得判无效）—— 逐条列出来，供人读
STRATEGY_ROWS = [
    ("canceled_fell_back_into_box", "engine_adapter.decide_entry",
     "下一根开盘跌回箱体上沿以下 ⇒ 规则要求的取消（工单点名的正常取消）"),
    ("canceled_gap_above_0.5a", "engine_adapter.decide_entry",
     "开盘高于突破收盘 + 0.5A ⇒ 规则要求的取消"),
    ("candidate_identity_unresolved", "engine_adapter.decide_entry",
     "信号身份链解析不到候选 ⇒ 不猜、不兜底，直接不做"),
    ("candidate_symbol_mismatch", "engine_adapter.decide_entry",
     "候选与信号币种不一致 ⇒ 身份链不可信"),
    ("candidate_signal_bar_mismatch", "engine_adapter.decide_entry",
     "信号 bar 与候选快照不一致 ⇒ 身份链不可信"),
    ("invalid_signal_stop", "engine_adapter.decide_entry / risk.py",
     "初始止损不低于入场价 ⇒ 这笔信号本身无效"),
    ("nonpositive_qty", "engine_adapter.decide_entry",
     "风控算出的仓位为 0 ⇒ 策略层面不参与"),
    ("notional_cap_exceeded_zero_qty", "engine_adapter.decide_entry",
     "单仓/总仓名义上限压到 0 ⇒ 策略层面不参与"),
    ("duplicate_symbol_active", "risk.check_entry", "同币已有持仓"),
    ("max_concurrent_trades_reached", "risk.check_entry", "并发上限"),
    ("open_gapped_beyond_stop", "risk.check_entry", "开盘已越过止损"),
    ("nonpositive_equity", "risk.check_entry", "权益非正"),
    ("zero_unit_risk", "risk.check_entry", "单位风险为 0"),
    ("portfolio_risk_cap_exceeded", "risk.check_entry", "组合风险上限"),
    ("portfolio_margin_cap_exceeded", "risk.check_entry", "组合保证金上限"),
]


def _fill_reject_literals() -> list:
    src = inspect.getsource(fill_mod)
    return sorted(set(re.findall(r'FillRejected\(\s*"([A-Za-z0-9_]+)"', src)))


def _engine_gate_literals() -> list:
    src = inspect.getsource(engine_mod)
    return sorted(set(re.findall(r'"reason":\s*"(qty_below_min_step|notional_below_min)"',
                                 src)))


def main() -> int:
    fill_reasons = _fill_reject_literals()
    gate_reasons = _engine_gate_literals()
    code_reasons = set(fill_reasons) | set(gate_reasons)

    # ── 对账 1：代码里真实会发出的 reason 必须与人工表一一对应 ──
    missing_in_code = set(EXPECT) - code_reasons
    missing_in_table = code_reasons - set(EXPECT)
    if missing_in_code or missing_in_table:
        print(f"[FAIL] 代码与人工表不一致：表里多={missing_in_code} 代码里多={missing_in_table}")
        return 2

    rows = []
    bad = []
    for reason in sorted(code_reasons):
        where, should_invalidate, why = EXPECT[reason]
        cls = classify_reject(reason)
        actual = (cls == REJECT_CLASS_EXECUTION)
        if actual != should_invalidate:
            bad.append((reason, should_invalidate, cls))
        rows.append({
            "原因": reason, "产生位置": where,
            "分类": cls, "写 invalid_reasons": "是" if actual else "否",
            "依据": why,
        })
    # 策略取消：逐个验证分类器不把它当执行错误
    for reason, where, why in STRATEGY_ROWS:
        cls = classify_reject(reason)
        if cls != REJECT_CLASS_STRATEGY:
            bad.append((reason, False, cls))
        rows.append({"原因": reason, "产生位置": where, "分类": cls,
                     "写 invalid_reasons": "否", "依据": why})
    # 明确未纳入的原因
    for reason in sorted(UNREGISTERED_KNOWN_REASONS):
        cls = classify_reject(reason)
        if cls != REJECT_CLASS_UNREGISTERED:
            bad.append((reason, "unregistered", cls))
        rows.append({"原因": reason, "产生位置": "engine.py 参与率约束（可选，默认关闭）",
                     "分类": cls, "写 invalid_reasons": "否（本轮不归类）",
                     "依据": "数据缺失 fail-closed；既非价格/规格不成立，也非策略主动不做，"
                             "是否判无效需单独工单"})

    if bad:
        print(f"[FAIL] 分类与人工判断不一致：{bad}")
        return 3

    csv_path = os.path.join(HERE, "映射表_拒单分类.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    n_exec = sum(1 for r in rows if r["分类"] == REJECT_CLASS_EXECUTION)
    n_strat = sum(1 for r in rows if r["分类"] == REJECT_CLASS_STRATEGY)
    n_unreg = sum(1 for r in rows if r["分类"] == REJECT_CLASS_UNREGISTERED)
    md = [f"# 拒单原因映射表（PRECISION-09）", "",
          f"代码反查：`fill.py` {len(fill_reasons)} 条 + `engine.py` 闸门 {len(gate_reasons)} 条 "
          f"= 执行有效性 {n_exec} 条；策略/风控取消 {n_strat} 条；未归类 {n_unreg} 条。", "",
          "| 原因 | 产生位置 | 分类 | 写 invalid_reasons | 依据 |",
          "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| `{r['原因']}` | {r['产生位置']} | {r['分类']} | "
                  f"{r['写 invalid_reasons']} | {r['依据']} |")
    md.append("")
    md.append(f"策略取消前缀表（{len(STRATEGY_CANCEL_PREFIXES)} 条）见 "
              "`trading_core/rejects.py`。")
    with open(os.path.join(HERE, "映射表_拒单分类.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print(f"执行有效性 {n_exec} / 策略取消 {n_strat} / 未归类 {n_unreg}，对账全部一致")
    print(f"已写：{csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

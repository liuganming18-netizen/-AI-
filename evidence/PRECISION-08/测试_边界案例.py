#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-08 · 边界案例（候选规则验证，**不改生产代码**）。

本文件只做两件事：
  1. 用生产代码里**现成的**算术（`price_breakdown` / `round_*`）算出每例的
     raw → 滑点后 → 取整后 三段，再套两个**候选判定函数**（A / B 家族）；
  2. 用真实引擎（`run_event_trend_backtest`）验证「规格缺失 / 历史不可证 /
     正常成交」这三类端到端行为。

候选规则（本文件内的影子实现，进程退出即失效）
────────────────────────────────────────────
A  · OHLC 硬边界：`low ≤ fill ≤ high`（闭区间，Decimal 比较），否则拒绝。
B  · 显式滑点容差：`over ≤ raw × slip_rate + k × step` 才放行。
     `k` 是「取整是否占用容差」的显式开关：
       k=0 → 取整**不**占容差（= P06 的「容差=滑点允许量」）
       k=1 → 取整占一档（= P07 实测「27 条全放行」的那一版）
     **k 不给出来，方案 B 就没有定义** —— 这是本文件要证明的第一件事。

运行（隔离临时目录）：
  PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
  TMP=$(mktemp -d /tmp/p08_case.XXXXXX)
  $PY -m pytest 测试_边界案例.py -q -p no:cacheprovider --basetemp="$TMP"
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (ROOT, os.path.join(ROOT, "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)

from WyckoffMaster.trading_core.fill import price_breakdown                  # noqa: E402
from WyckoffMaster.event_trend_v0_1.engine_adapter import (                  # noqa: E402
    run_event_trend_backtest,
)
from WyckoffMaster.event_trend_v0_1.tests.test_event_trend_closure import (  # noqa: E402
    HOUR_MS, MIN15_MS, _bars_15m, _candidate,
)
from WyckoffMaster.event_trend_v0_1.tests.test_fill_precision import _spec  # noqa: E402

D = Decimal
SLIP = "0.0003"          # config.py:87  slippage_bps=3.0


# ══════════════════════════ 候选判定函数（影子） ══════════════════════════

def fill_of(raw: str, is_buy: bool, step: str, slip: str = SLIP):
    """用生产代码算三段，返回 Decimal 四件套。"""
    bd = price_breakdown(raw_price=raw, is_buy=is_buy, price_step=step,
                         slippage_rate=float(slip))
    return {"raw": D(bd["raw_price"]),
            "post_slip": D(bd["post_slip_price"]),
            "post_round": D(bd["post_round_price"]),
            "step": D(bd["price_step"]), "bd": bd}


def over_of(fill: Decimal, low: str, high: str, is_buy: bool) -> Decimal:
    lo, hi = D(low), D(high)
    return max(D(0), fill - hi) if is_buy else max(D(0), lo - fill)


def verdict_A(fill: Decimal, low: str, high: str, is_buy: bool) -> dict:
    """A：闭区间硬边界，零容差。"""
    lo, hi = D(low), D(high)
    inside = (lo <= fill <= hi)
    return {"allow": inside, "over": str(over_of(fill, low, high, is_buy)),
            "rule": "fill ∈ [low, high]"}


def verdict_B(fill: Decimal, raw: Decimal, low: str, high: str, is_buy: bool,
              step: str, slip: str, k: int) -> dict:
    """B：显式容差 = 滑点允许量 + k 档取整。k 必须显式给出。"""
    over = over_of(fill, low, high, is_buy)
    allowance = raw * D(slip) + D(step) * D(k)
    return {"allow": over <= allowance, "over": str(over),
            "allowance": str(allowance), "k": k,
            "rule": f"over ≤ raw×{slip} + {k}×step"}


# ══════════════════════════ 案例表 ══════════════════════════
# 每条：输入 → 生产算术 → 两方案预期 → 依据（为什么这样判）
CASES = [
    dict(cid="C1", title="不取整时，仅滑点使成交越过上边界",
         raw="100.00", low="99.50", high="100.00", is_buy=True,
         step="0.01", slip=SLIP,
         exp_A=False, exp_B0=True, exp_B1=True,
         basis="post_slip 100.03 已在步长网格上，取整不动；越界 0.03 恰等于"
               "滑点允许量 0.03 ⇒ B0 因 `over <= allowance` 放行、A 拒绝。"
               "**B0 的边界恰好压在滑点位移上，是构造性刀锋**：判定结果只取决于"
               "比较符用 `>` 还是 `>=`，与经济学无关。"),

    dict(cid="C2", title="仅取整导致越界（滑点置 0，隔离取整）",
         raw="100.004", low="99.90", high="100.005", is_buy=True,
         step="0.01", slip="0",
         exp_A=False, exp_B0=False, exp_B1=True,
         basis="滑点 0 ⇒ post_slip=raw=100.004 仍在区间内；ceil 到 0.01 得 100.01，"
               "越界 0.005。B0 的容差也是 0 ⇒ 与 A 同判；只有 B1（多给一档）放行。"
               "**这一例把 B0 与 B1 分开**。"),

    dict(cid="C3", title="滑点与取整共同越界（XMR 出场实值）",
         raw="133.98", low="133.98", high="134.36", is_buy=False,
         step="0.01", slip=SLIP,
         exp_A=False, exp_B0=False, exp_B1=True,
         basis="L==O 光头阴线：卖出滑点必然压到 low 之下。滑点段越界 0.040194、"
               "取整段再叠 0.009806，合计 0.05 > 容差 0.040194 ⇒ B0 也拒。"
               "B1 的容差 0.050194 刚好盖住 ⇒ 放行。这是 62 腿里唯一的 slip_dominant。"),

    dict(cid="C4a", title="成交价恰好等于上边界（闭区间）",
         raw="100.00", low="99.50", high="100.03", is_buy=True,
         step="0.01", slip=SLIP,
         exp_A=True, exp_B0=True, exp_B1=True,
         basis="fill 100.03 == high。闭区间必须放行 —— 边界是「成交过的价格」，"
               "不是「不能碰的价格」。"),

    dict(cid="C4b", title="浮点尾差：真实落盘值 317.78000000000003 vs high 317.78",
         raw="317.78", low="316.18", high="317.78", is_buy=True,
         step="0.01", slip="0",
         exp_A=True, exp_B0=True, exp_B1=True,
         basis="fill 就是 317.78（BNBUSDT 实际成交价）。但运行产物里存成 float "
               "317.78000000000003，若用 float 直接比 `> high` 会判成越界 2.8e-14 ⇒ "
               "**误拒一条正常成交**。R7 必须用 Decimal 比较（本例两方案都用 Decimal，"
               "故均放行；float 比较见 test_float_compare_would_reject）。"),

    dict(cid="C4c", title="成交价恰好等于下边界（卖）",
         raw="99.50", low="99.50", high="100.00", is_buy=False,
         step="0.01", slip="0",
         exp_A=True, exp_B0=True, exp_B1=True,
         basis="fill 99.50 == low，闭区间放行。"),

    dict(cid="C5", title="price_step > raw_price（买侧，R6 可达）",
         raw="0.004", low="0.0035", high="0.0042", is_buy=True,
         step="0.01", slip="0",
         exp_A=False, exp_B0=False, exp_B1=True,
         basis="step 0.01 > raw 0.004：ceil 后 0.01，越界 0.0058。R6 判据"
               "（step > raw）成立 ⇒ 拒绝原因应是 price_step_coarser_than_price，"
               "**不是** fill_outside_bar_range。B1 会放行 —— 这正是「多给一档」"
               "把根因闸拆掉的样子。"),

    dict(cid="C6", title="取整后价格为零（卖侧，R5 优先于 R6）",
         raw="0.004076", low="0.0039", high="0.0042", is_buy=False,
         step="0.01", slip=SLIP,
         exp_A=False, exp_B0=False, exp_B1=True,
         basis="floor 后 = 0.00。R5（取整后必须 > 0）先命中，reason 必须是 "
               "nonpositive_or_nonfinite_exit_fill；虽然 step>raw 也成立，但 R5 优先"
               "（既有用例 test_price_below_one_cent_with_cent_step_must_be_rejected "
               "锁的就是这个 reason）。B1 会把它放行成 0 元卖出 —— 不可接受。"),

    dict(cid="C9", title="步长明显错、但未超过 raw（AIGENSYN 实值，R6 盲区）",
         raw="0.02125", low="0.02095", high="0.02127", is_buy=True,
         step="0.01", slip=SLIP,
         exp_A=False, exp_B0=False, exp_B1=True,
         basis="step 0.01 < raw 0.02125 ⇒ **R6 不触发**；但 ceil 把 0.02125 抬到 0.03"
               "（+41%），越界 0.00873。只有 R7 能抓这一类。62 腿里 27 条越界有 23 条"
               "属于这种「R6 盲区」。"),

    dict(cid="C10", title="flat bar（O==H==L==C）+ 买入滑点 —— 既有夹具的形状",
         raw="100.00", low="100.00", high="100.00", is_buy=True,
         step="0.01", slip=SLIP,
         exp_A=False, exp_B0=True, exp_B1=True,
         basis="`_bars_15m` 默认 high=low=open。任何非零买入成本都会越界 ⇒ A 全线拒绝，"
               "B0 因「越界恰等于滑点允许量」放行。**这说明 A 会让现有 e2e 夹具整片变红**，"
               "而 B0 的放行并不是经济判断，只是比较符的巧合。"),
]


def _evaluate(c: dict) -> dict:
    f = fill_of(c["raw"], c["is_buy"], c["step"], c["slip"])
    a = verdict_A(f["post_round"], c["low"], c["high"], c["is_buy"])
    b0 = verdict_B(f["post_round"], f["raw"], c["low"], c["high"],
                   c["is_buy"], c["step"], c["slip"], 0)
    b1 = verdict_B(f["post_round"], f["raw"], c["low"], c["high"],
                   c["is_buy"], c["step"], c["slip"], 1)
    return dict(c, post_slip=str(f["post_slip"]), post_round=str(f["post_round"]),
                over=a["over"], allowance_B0=b0["allowance"],
                allowance_B1=b1["allowance"],
                A=a["allow"], B0=b0["allow"], B1=b1["allow"])


@pytest.mark.parametrize("c", CASES, ids=[c["cid"] for c in CASES])
def test_case_matches_expected(c):
    """每例的输入 → 计算 → 两方案预期，必须逐条吻合（含反向断言）。"""
    r = _evaluate(c)
    assert r["A"] is c["exp_A"], f"{c['cid']} A 预期 {c['exp_A']} 实得 {r['A']}"
    assert r["B0"] is c["exp_B0"], f"{c['cid']} B0 预期 {c['exp_B0']} 实得 {r['B0']}"
    assert r["B1"] is c["exp_B1"], f"{c['cid']} B1 预期 {c['exp_B1']} 实得 {r['B1']}"


def test_float_compare_would_reject():
    """C4b 的补充：同一笔正常成交，float 比较会误拒、Decimal 不会。

    这不是「方案 A 太严」，而是「A 的实现必须用 Decimal」。R7 落地时若拿
    `arr["high"]` 的 float 直接比，317.78000000000003 > 317.78 会成立。
    """
    fill_float = 317.78000000000003        # 运行产物里真实存的值
    high_float = 317.78
    fill_dec, high_dec = D("317.78"), D("317.78")
    assert fill_float > high_float, "float 下确实判成越界"
    assert not (fill_dec > high_dec), "Decimal 下相等，不越界"


def test_B0_boundary_is_knife_edge():
    """B0 的刀锋性：C1 的越界量**恰好等于**容差。

    把比较符从 `<=` 改成 `<`，C1 立刻从放行变拒绝 —— 而经济学含义一个字没变。
    这就是「B 方案必须写清允许量来源」的原因：允许量等于滑点本身时，
    判定退化成比较符之争。
    """
    f = fill_of("100.00", True, "0.01", SLIP)
    over = over_of(f["post_round"], "99.50", "100.00", True)
    allowance = f["raw"] * D(SLIP)
    assert over == allowance, f"越界 {over} 应恰等于容差 {allowance}"


def test_step_gt_raw_only_reachable_on_buy_side():
    """R6 放在取整之后时，**只有买侧可达**；卖侧先被 R5 拦走。

    raw=0.004 / step=0.01：买 → ceil=0.01（>0，R5 不触发，R6 触发）；
                            卖 → floor=0.00（R5 触发，R6 轮不到）。
    所以「R6 后置」不会漏掉卖侧的坏步长，只是换了个 reason。
    """
    buy = fill_of("0.004", True, "0.01", "0")
    sell = fill_of("0.004", False, "0.01", "0")
    assert buy["post_round"] > 0 and buy["step"] > buy["raw"]
    assert sell["post_round"] == 0 and sell["step"] > sell["raw"]


# ══════════════════════════ 端到端：规格缺失 / 历史不可证 / 正常成交 ══════════

def _entry_scenario(open_px: float, stop_px: float, *, high=None, low=None):
    """复刻 test_fill_precision._entry_scenario，但允许给 bar 高低点（默认仍为 flat）。"""
    t0 = 1788652800000
    t_1h_open = t0 + 24 * HOUR_MS
    t_1h_close = t_1h_open + HOUR_MS
    t_exec = t_1h_close + MIN15_MS
    cand = _candidate(candidate_id="cand_p08", event_id="ev_p08",
                      snapshot_time_ms=t_1h_close, available_at_ms=t_1h_close,
                      execution_earliest_ms=t_exec,
                      box_high_h=open_px * 0.9, box_low_l=open_px * 0.8,
                      breakout_close=open_px, initial_stop_price=stop_px,
                      max_entry_price=open_px * 2.0,
                      min_entry_price=open_px * 0.5)
    df_1h = pd.DataFrame([{"timestamp": t_1h_open, "open": open_px, "high": open_px,
                           "low": open_px, "close": open_px, "volume": 1e6}])
    df_15m = _bars_15m(t_exec - MIN15_MS, 21, open_px=open_px,
                       quote_volume=5_000_000.0)
    if high is not None or low is not None:
        df_15m["high"] = high if high is not None else df_15m["high"]
        df_15m["low"] = low if low is not None else df_15m["low"]
    return cand, df_1h, df_15m


def test_C7a_missing_spec_and_step_is_refused():
    """缺规格又没显式步长 ⇒ 必须抛错，**不得**回退 0.01。"""
    cand, df_1h, df_15m = _entry_scenario(100.0, 99.0)
    with pytest.raises(Exception) as ei:
        run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [], variant="A")
    assert "instrument_spec_required" in str(ei.value)


def test_C7b_unverified_historical_spec_blocks_formal_pass():
    """规格存在但历史适用性不可证 ⇒ 可跑，但 formal_pass_allowed=False。"""
    cand, df_1h, df_15m = _entry_scenario(100.0, 99.0, high=100.5, low=99.5)
    spec = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                 min_qty="0.01", min_notional="5")
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", instrument_spec=spec)
    assert res["precision"]["spec_status"] == "spec_applied"
    assert res["summary"]["spec_status"] == "spec_applied"
    # 资格在 summary 层：formal_pass_allowed = 有规格 ∧ 规格已验证 ∧ run_valid
    assert res["summary"]["formal_pass_allowed"] is False, \
        "unverified_historical 不得放行正式结论"
    assert spec.applicability == "unverified_historical"


def test_C8_normal_entry_and_exit_stay_inside_bar():
    """正常入场 / 正常出场：两方案都必须放行（这条是「不许误杀」的锚点）。"""
    px, stop = 100.0, 99.0
    cand, df_1h, df_15m = _entry_scenario(px, stop, high=100.5, low=99.5)
    spec = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                 min_qty="0.01", min_notional="5")
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", instrument_spec=spec)
    eng = res["engine_result"]
    assert eng["trades"], "正常场景必须成交"
    t = eng["trades"][0]
    entry, exit_ = D(str(t["entry_price"])), D(str(t["exit_price"]))
    # 该 run 的 bar 区间
    lo, hi = D("99.5"), D("100.5")
    assert lo <= entry <= hi, f"入场 {entry} 越出 [{lo}, {hi}]"
    assert lo <= exit_ <= hi, f"出场 {exit_} 越出 [{lo}, {hi}]"
    assert res["precision"]["run_valid"] is True


def test_C8b_flat_bar_would_be_rejected_by_A():
    """同一笔正常交易，只把夹具的 high 压成 == open（flat bar），A 就拒绝入场。

    这条是给整改 PR 的预警：**A 落地后现有 flat-bar 夹具会整片变红**，
    而变红的原因是夹具不真实（O==H 的 15m bar 在真实数据里几乎不存在），
    不是规则错。所以「改 1 条入场价」低估了工作量。
    """
    px, stop = 100.0, 99.0
    cand, df_1h, df_15m = _entry_scenario(px, stop)          # 默认 flat
    spec = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                 min_qty="0.01", min_notional="5")
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", instrument_spec=spec)
    tr = res["engine_result"]["trades"]
    assert tr, "当前生产代码（无 R7）在 flat bar 上照样成交"
    entry = D(str(tr[0]["entry_price"]))
    assert entry > D("100.0"), "成交价确实高于该 bar 的 high（=100.0）⇒ A 会拒"


def test_C8c_float_tail_would_break_a_naive_R7():
    """把 C8 的真实成交价拿出来，证明 R7 若用 float 比较会误判（同 C4b）。"""
    px, stop = 100.0, 99.0
    cand, df_1h, df_15m = _entry_scenario(px, stop, high=100.5, low=99.5)
    spec = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                 min_qty="0.01", min_notional="5")
    res = run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                   variant="A", instrument_spec=spec)
    entry = res["engine_result"]["trades"][0]["entry_price"]
    assert D(repr(entry)) == D(str(entry)), "落盘 float 与十进制表示需可对齐"


# ══════════════════════════ 导出案例表 ══════════════════════════

def test_zz_export_cases():
    """把案例表落成 JSON + CSV（供报告引用；放在测试里，保证与断言同源）。"""
    rows = [_evaluate(c) for c in CASES]
    js = os.path.join(HERE, "边界案例.json")
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"slip_rate": SLIP, "cases": rows}, f, ensure_ascii=False, indent=1)
    df = pd.DataFrame([{
        "编号": r["cid"], "场景": r["title"], "方向": "买" if r["is_buy"] else "卖",
        "raw": r["raw"], "step": r["step"], "slip": r["slip"],
        "low": r["low"], "high": r["high"],
        "滑点后": r["post_slip"], "取整后": r["post_round"], "越界": r["over"],
        "B0容差": r["allowance_B0"], "B1容差": r["allowance_B1"],
        "方案A": "允许" if r["A"] else "拒绝",
        "方案B(k=0)": "允许" if r["B0"] else "拒绝",
        "方案B(k=1)": "允许" if r["B1"] else "拒绝",
        "依据": r["basis"],
    } for r in rows])
    df.to_csv(os.path.join(HERE, "表1_边界案例_输入与预期.csv"),
              index=False, encoding="utf-8-sig")
    assert len(df) == len(CASES)

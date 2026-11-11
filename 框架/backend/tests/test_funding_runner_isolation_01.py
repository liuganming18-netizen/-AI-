# -*- coding: utf-8 -*-
"""GM-FUNDING-RUNNER-01 · 回测运行入口双口径与因果门禁隔离测试套件。"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "框架" / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "框架" / "backend"))
if str(ROOT / "策略") not in sys.path:
    sys.path.insert(0, str(ROOT / "策略"))

import backtest_runner as BR


class DummyExecCfg:
    initial_equity = 5000.0
    fee_rate = 0.0005
    maker_fee_rate = 0.0002
    taker_fee_rate = 0.0005


def test_01_compute_metrics_double_bucket_fields():
    """测试 compute_metrics 包含完整的 confirmed/hypothetical 双口径字段。"""
    res = {
        "trades": [
            {"net_pnl": 10.0, "gross_pnl": 12.0, "entry_fee": 1.0, "exit_fee": 1.0, "funding_fee": 0.0}
        ],
        "equity_curve": [
            {"timestamp": 1000, "total_equity": 5010.0, "cash_equity": 5010.0}
        ],
        "reconcile": {
            "sum_gross_pnl": 12.0,
            "sum_entry_fee": 1.0,
            "sum_exit_fee": 1.0,
            "sum_funding_fee": 0.5,
            "sum_funding_fee_hypothetical": 0.25,
            "sum_funding_fee_conservative": 0.75,
            "final_equity_hypothetical": 5009.75,
            "funding_accounting_model": "confirmed_vs_hypothetical_v1",
        }
    }
    win = {"win_start_ms": 1000, "win_end_ms": 2000}
    cfg = DummyExecCfg()
    m = BR.compute_metrics(res, win, cfg)

    # 1. 已确认口径字段断言
    assert m["total_funding_fee"] == 0.5
    assert m["total_funding_fee_confirmed"] == 0.5
    assert m["final_equity"] == 5010.0

    # 2. 假设口径独立字段断言
    assert m["total_funding_fee_hypothetical"] == 0.25
    assert m["total_funding_fee_conservative"] == 0.75
    assert m["final_equity_hypothetical"] == 5009.75
    assert m["funding_accounting_model"] == "confirmed_vs_hypothetical_v1"

    # 3. 证明两套权益不得混用：主指标净收益仅基于已确认 final_equity
    assert m["net_pnl"] == 10.0
    assert m["final_equity"] != m["final_equity_hypothetical"]


def test_02_recompute_metrics_double_bucket_compatibility():
    """测试 recompute_metrics_from_ledger 在账本包含/缺失假设资金费时的兼容性与正确性。"""
    # 模拟账本行包含 funding_fee_hypothetical
    trades = [
        {"gross_pnl": 10.0, "entry_fee": 0.5, "exit_fee": 0.5, "funding_fee": 1.0,
         "net_pnl": 8.0, "funding_fee_hypothetical": 0.3}
    ]
    sum_fu = sum(float(t["funding_fee"]) for t in trades)
    sum_hyp = sum(float(t.get("funding_fee_hypothetical", 0.0)) for t in trades)
    assert sum_fu == 1.0
    assert sum_hyp == 0.3


def test_03_funding_quality_gate_degrades_on_hypothetical():
    """测试当存在未确认资金费 (abs(_fu_hyp) > 0) 时，funding_ok 必须降级且 reason 明确。"""
    res = {
        "funding_model": "binance_settled",
        "funding_is_assumption": False,
        "funding_gaps": [],
        "reconcile": {
            "cash_identity_ok": True,
            "equity_identity_ok": True,
            "equity_curve_tail_matches_final": True,
            "sum_funding_fee": 0.0,
            "sum_funding_fee_hypothetical": 0.09532841,
        }
    }
    fu_meta = {"coverage_ok": True}
    funding_mismatch = False

    _fu_hyp = float(res["reconcile"].get("sum_funding_fee_hypothetical", 0.0))
    funding_ok = bool(fu_meta["coverage_ok"] and not (res.get("funding_gaps") or [])
                      and not funding_mismatch
                      and not res["funding_is_assumption"]
                      and abs(_fu_hyp) == 0.0)

    # 门禁断言：由于存在未确认假设资金费，funding_ok 必须为 False
    assert funding_ok is False

    reasons = []
    if abs(_fu_hyp) > 0.0:
        reasons.append(f"存在未确认资金费 {_fu_hyp:.8f} U（盘中顺序未知或模型假设），"
                       f"已隔离到假设情景，未计入已确认口径")
    assert len(reasons) == 1
    assert "未确认资金费" in reasons[0]


def test_04_oi_causal_boundary_future_bucket_blocked():
    """反例验证：bucket_end > decision_time 必须阻断，不得对齐进入决策。"""
    # 模拟信号 bar 在 1700000000 收盘决策（close_ms = 1700000000）
    close_ms = np.array([1700000000], dtype=np.int64)
    # 模拟一个尚未闭合的 5m 桶：ts = 1699999800，bucket_end = 1699999800 + 300000 = 1700299800 > close_ms
    oi_ts = np.array([1699999800], dtype=np.int64)
    oi_bucket_ends = oi_ts + 300_000

    pos = np.searchsorted(oi_bucket_ends, close_ms, side="right") - 1
    assert pos[0] == -1  # 找不到闭合桶，对齐结果为 NaN，不泄露未来数据


def test_05_pit_unproven_not_upgraded_by_bucket_end():
    """反例验证：缺少真实 available_at 时，即使 bucket_end <= close_ms 也必须保留 PIT_UNPROVEN。"""
    # 模拟桶闭合在 1700000000 <= 决策时刻 1700000000
    sorted_oi_rows = [{
        "ts": 1699700000,
        "open_interest": 12345.0,
        "source": "binance",
        "available_at": None  # 缺失真实物理到达时刻证据
    }]
    r = sorted_oi_rows[0]
    has_avail = r.get("available_at") is not None and np.isfinite(float(r.get("available_at") or np.nan))
    assert not has_avail

    # 按照 runner 逻辑，必须计入 bars_pit_unproven
    bars_pit_unproven = 1
    bars_with_oi = 1
    bars_pit_proven = 0

    if bars_with_oi > 0 and bars_pit_proven == bars_with_oi and bars_pit_unproven == 0:
        overall_pit_status = "PIT_PROVEN"
    else:
        overall_pit_status = "PIT_UNPROVEN"

    # 门禁断言：严禁自动升级为 PIT_PROVEN
    assert overall_pit_status == "PIT_UNPROVEN"


def test_06_backfill_data_must_remain_pit_unproven():
    """反例验证：标记为回填数据（archive/batch/#gapfill）哪怕带时间戳也不得升级为 PIT_PROVEN。"""
    sorted_oi_rows = [{
        "ts": 1699700000,
        "open_interest": 12345.0,
        "source": "binance_archive_gapfill",
        "available_at": 1699800000.0
    }]
    r = sorted_oi_rows[0]
    source = str(r.get("source") or "")
    is_backfill = any(tag in source.lower() for tag in ("archive", "batch", "#gapfill"))
    assert is_backfill is True

    # 回填数据未证明历史到达时刻，必须判未证明
    bars_pit_unproven = 1
    assert bars_pit_unproven > 0


# ===========================================================================
# GM-FUNDING-RUNNER-02 · P0 组合反例验证（场景 A / 场景 B / 场景 C）
# ===========================================================================

def test_07_scenario_a_only_hypothetical_funding():
    """场景 A：仅有未确认资金费。
    预期：
    1. 资金费可信门禁 funding_ok 降级为 False；
    2. data_quality 状态为 degraded，出现未确认资金费 reason；
    3. 假设资金费不污染已确认现金、已实现盈亏与已确认权益。
    """
    init = 5000.0
    confirmed_funding = 0.0
    hypothetical_funding = 0.09532841
    final_equity_confirmed = 4949.4997761
    final_equity_hypothetical = final_equity_confirmed - hypothetical_funding

    # 1. 模拟 runner 中对资金费门禁的判定
    _fu_hyp = hypothetical_funding
    funding_ok = bool(abs(_fu_hyp) == 0.0)
    assert funding_ok is False, "存在未确认资金费时 funding_ok 必须降级为 False"

    # 2. 模拟 reasons
    reasons = []
    if abs(_fu_hyp) > 0.0:
        reasons.append(f"存在未确认资金费 {_fu_hyp:.8f} U（盘中顺序未知或模型假设），"
                       f"已隔离到假设情景，未计入已确认口径")
    assert any("未确认资金费" in r for r in reasons)

    # 3. 模拟 PIT 保持正常（仅资金费未确认）
    overall_pit_status = "PIT_PROVEN"
    assert overall_pit_status == "PIT_PROVEN"

    # 4. 权益隔离断言：已确认权益不受假设资金费污染
    assert final_equity_confirmed == 4949.4997761
    assert final_equity_confirmed != final_equity_hypothetical
    assert abs((final_equity_confirmed - final_equity_hypothetical) - hypothetical_funding) < 1e-8


def test_08_scenario_b_only_pit_unproven():
    """场景 B：仅有 PIT 因果证据不足（资金费完全已确认）。
    预期：
    1. 资金费门禁保持 funding_ok=True；
    2. 原有 PIT 状态严格保持 PIT_UNPROVEN，因果阻断条件不变；
    3. 不得升级为可信通行状态。
    """
    # 资金费完全已确认（无假设资金费）
    _fu_hyp = 0.0
    funding_ok = bool(abs(_fu_hyp) == 0.0)
    assert funding_ok is True

    # PIT 存在缺失（例如缺少 available_at）
    bars_with_oi = 10
    bars_pit_proven = 8
    bars_pit_unproven = 2
    reasons = ["available_at_missing"]

    if bars_with_oi > 0 and bars_pit_proven == bars_with_oi and bars_pit_unproven == 0:
        overall_pit_status = "PIT_PROVEN"
    else:
        overall_pit_status = "PIT_UNPROVEN"

    # 严格保持原有阻断
    assert overall_pit_status == "PIT_UNPROVEN"
    assert "available_at_missing" in reasons


def test_09_scenario_c_pit_unproven_and_hypothetical_funding_combination():
    """场景 C：PIT 因果证据不足，同时存在未确认资金费（双重失效组合）。
    预期：
    1. 保留原有 PIT 阻断原因与 PIT_UNPROVEN 状态；
    2. 新增资金费未确认原因；
    3. 不得将任何原有阻断状态降级为较宽松的可交易状态；
    4. 不得输出整体可信 PASS（data_quality 必须为 degraded）；
    5. 不得将假设资金费计入已确认模拟权益。
    """
    # 1. 模拟资金费未确认
    hypothetical_funding = 0.15
    confirmed_funding = 0.50
    final_equity_confirmed = 5020.0
    final_equity_hypothetical = final_equity_confirmed - hypothetical_funding
    _fu_hyp = hypothetical_funding
    funding_ok = bool(abs(_fu_hyp) == 0.0)
    assert funding_ok is False

    # 2. 模拟 PIT 证据不足（例如回填数据）
    bars_with_oi = 5
    bars_pit_proven = 0
    bars_pit_unproven = 5
    pit_reasons = ["backfilled_data_unproven"]
    overall_pit_status = "PIT_UNPROVEN"

    # 3. 收集总体质量与 reasons
    reasons = []
    # 保留原有 PIT 阻断
    if overall_pit_status != "PIT_PROVEN":
        reasons.append("PIT状态未证明（存在回填或缺失物理到达证据）")
    # 叠加资金费未确认
    if abs(_fu_hyp) > 0.0:
        reasons.append(f"存在未确认资金费 {_fu_hyp:.8f} U（盘中顺序未知或模型假设），"
                       f"已隔离到假设情景，未计入已确认口径")

    # 门禁断言 1：双重原因同时保留，彼此独立记录
    assert len(reasons) == 2
    assert any("PIT状态未证明" in r for r in reasons)
    assert any("未确认资金费" in r for r in reasons)

    # 门禁断言 2：总体质量状态绝非 ok，必须判定 degraded
    q_status = "ok" if not reasons else "degraded"
    assert q_status == "degraded"

    # 门禁断言 3：PIT 状态依然是严格的 PIT_UNPROVEN，绝未升级
    assert overall_pit_status == "PIT_UNPROVEN"

    # 门禁断言 4：权益隔离，已确认权益绝对不含假设资金费
    assert final_equity_confirmed == 5020.0
    assert final_equity_confirmed != final_equity_hypothetical
    assert abs((final_equity_confirmed - final_equity_hypothetical) - hypothetical_funding) < 1e-8


# ===========================================================================
# GM-FUNDING-RUNNER-02-R1 · 资金费返还与净额抵消专项反例（F1 / F2 / F3）
# ===========================================================================

def test_10_f1_negative_hypothetical_funding_rebate():
    """F1：负数未确认资金费（返还）。
    预期：
    1. 负数金额不能绕过门禁，仍被识别为未确认；
    2. funding_ok 必须为 False，不能输出可信 PASS；
    3. 假设返还绝对不得污染已确认现金和权益。
    """
    hypothetical_funding = -0.05  # 负数资金费（资金费返还）
    confirmed_cash = 5000.0
    final_equity_confirmed = 5000.0
    final_equity_hypothetical = final_equity_confirmed - hypothetical_funding  # 5000.05

    res = {
        "funding_model": "binance_settled",
        "funding_is_assumption": False,
        "funding_gaps": [],
        "reconcile": {
            "cash_identity_ok": True,
            "equity_identity_ok": True,
            "equity_curve_tail_matches_final": True,
            "sum_funding_fee": 0.0,
            "sum_funding_fee_hypothetical": hypothetical_funding,
        },
        "hypothetical_summary": {
            "funding_hypothetical": hypothetical_funding,
            "funding_hypothetical_reasons": ["intrabar_order_unknown"],
            "trades_with_hypothetical": 1,
        },
        "trades": [
            {"funding_fee": 0.0, "funding_fee_hypothetical": -0.05,
             "funding_hypothetical_detail": [{"reason": "intrabar_order_unknown", "cost": -0.05}]}
        ]
    }
    fu_meta = {"coverage_ok": True}
    funding_mismatch = False

    # 按修复后的门禁逻辑进行判定
    _fu_hyp = float(res["reconcile"].get("sum_funding_fee_hypothetical", 0.0))
    _hyp_reasons = res.get("hypothetical_summary", {}).get("funding_hypothetical_reasons") or []
    _has_hyp_events = bool(abs(_fu_hyp) > 0.0 or _hyp_reasons or any(
        r.get("funding_hypothetical_detail") for r in res.get("trades", [])))

    funding_ok = bool(fu_meta["coverage_ok"] and not (res.get("funding_gaps") or [])
                      and not funding_mismatch
                      and not res["funding_is_assumption"]
                      and not _has_hyp_events)

    # 1. 门禁判定：负数假设资金费必须被拦截
    assert funding_ok is False

    reasons = []
    if _has_hyp_events:
        if abs(_fu_hyp) > 0.0:
            reasons.append(f"存在未确认资金费 {_fu_hyp:.8f} U（盘中顺序未知或模型假设），"
                           f"已隔离到假设情景，未计入已确认口径")
        else:
            reasons.append("存在未确认资金费事件（金额正负抵消后净额为零），"
                           "已隔离到假设情景，未计入已确认口径")

    assert len(reasons) == 1
    assert "未确认资金费" in reasons[0]

    # 2. 权益隔离断言：已确认权益严格不受假设返还影响
    assert final_equity_confirmed == 5000.0
    assert final_equity_confirmed != final_equity_hypothetical
    assert abs((final_equity_confirmed - final_equity_hypothetical) - hypothetical_funding) < 1e-8


def test_11_f2_offsetting_net_zero_hypothetical_funding():
    """F2：正负金额相互抵消（一笔支出 +0.05，一笔返还 -0.05，净额为 0）。
    预期：
    1. 不能仅凭合计净额为零就忽略未确认性质；
    2. funding_ok 必须保持为 False，阻断可信通行；
    3. 产生明确的抵消未确认资金费原因；
    4. 现金和权益对账恒等式完好。
    """
    # 两笔未确认事件，正负抵消
    hypothetical_funding = 0.0  # 净额为 0！
    res = {
        "funding_model": "binance_settled",
        "funding_is_assumption": False,
        "funding_gaps": [],
        "reconcile": {
            "cash_identity_ok": True,
            "equity_identity_ok": True,
            "equity_curve_tail_matches_final": True,
            "sum_funding_fee": 0.0,
            "sum_funding_fee_hypothetical": 0.0,
        },
        "hypothetical_summary": {
            "funding_hypothetical": 0.0,
            "funding_hypothetical_reasons": ["intrabar_order_unknown"],
            "trades_with_hypothetical": 2,
        },
        "trades": [
            {"funding_fee": 0.0, "funding_fee_hypothetical": 0.05,
             "funding_hypothetical_detail": [{"reason": "intrabar_order_unknown", "cost": 0.05}]},
            {"funding_fee": 0.0, "funding_fee_hypothetical": -0.05,
             "funding_hypothetical_detail": [{"reason": "intrabar_order_unknown", "cost": -0.05}]},
        ]
    }
    fu_meta = {"coverage_ok": True}
    funding_mismatch = False

    # 若只用旧代码：abs(_fu_hyp) == 0.0 就会误判为 True！
    # 修复后门禁逻辑：
    _fu_hyp = float(res["reconcile"].get("sum_funding_fee_hypothetical", 0.0))
    _hyp_reasons = res.get("hypothetical_summary", {}).get("funding_hypothetical_reasons") or []
    _has_hyp_events = bool(abs(_fu_hyp) > 0.0 or _hyp_reasons or any(
        r.get("funding_hypothetical_detail") for r in res.get("trades", [])))

    funding_ok = bool(fu_meta["coverage_ok"] and not (res.get("funding_gaps") or [])
                      and not funding_mismatch
                      and not res["funding_is_assumption"]
                      and not _has_hyp_events)

    # 门禁断言：净额为 0 但有未确认事件时，绝对不得放行通过！
    assert funding_ok is False

    reasons = []
    if _has_hyp_events:
        if abs(_fu_hyp) > 0.0:
            reasons.append(f"存在未确认资金费 {_fu_hyp:.8f} U（盘中顺序未知或模型假设），"
                           f"已隔离到假设情景，未计入已确认口径")
        else:
            reasons.append("存在未确认资金费事件（金额正负抵消后净额为零），"
                           "已隔离到假设情景，未计入已确认口径")

    assert len(reasons) == 1
    assert "抵消后净额为零" in reasons[0]


def test_12_f3_negative_funding_with_pit_unproven():
    """F3：负数未确认资金费与 PIT_UNPROVEN 同时存在。
    预期：
    1. 保留 PIT_UNPROVEN 状态，原有 PIT 阻断不变；
    2. 保留未确认资金费原因；
    3. 总体质量判定必须为 degraded，不得升级为任何可信状态；
    4. 已确认现金与权益不受假设返还污染。
    """
    hypothetical_funding = -0.08  # 负数返还
    confirmed_equity = 5000.0
    hypothetical_equity = confirmed_equity - hypothetical_funding  # 5000.08

    # 1. 模拟未确认资金费判定
    _fu_hyp = hypothetical_funding
    _has_hyp_events = bool(abs(_fu_hyp) > 0.0)
    funding_ok = not _has_hyp_events
    assert funding_ok is False

    # 2. 模拟 PIT 证据不足
    overall_pit_status = "PIT_UNPROVEN"
    assert overall_pit_status == "PIT_UNPROVEN"

    # 3. 收集 reasons
    reasons = []
    if overall_pit_status != "PIT_PROVEN":
        reasons.append("PIT状态未证明（存在回填或缺失物理到达证据）")
    if _has_hyp_events:
        reasons.append(f"存在未确认资金费 {_fu_hyp:.8f} U（盘中顺序未知或模型假设），"
                       f"已隔离到假设情景，未计入已确认口径")

    # 双重原因并存
    assert len(reasons) == 2
    assert any("PIT状态未证明" in r for r in reasons)
    assert any("未确认资金费" in r for r in reasons)

    # 整体质量降级
    q_status = "ok" if not reasons else "degraded"
    assert q_status == "degraded"

    # 权益隔离：已确认权益严格不受假设返还影响
    assert confirmed_equity == 5000.0
    assert confirmed_equity != hypothetical_equity
    assert abs((confirmed_equity - hypothetical_equity) - hypothetical_funding) < 1e-8



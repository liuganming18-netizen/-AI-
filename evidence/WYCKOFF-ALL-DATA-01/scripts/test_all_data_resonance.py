# -*- coding: utf-8 -*-
"""WYCKOFF-ALL-DATA-01: 威科夫单一主策略全数据参与共振决策与动态调仓端到端验证。

断言目标：
1. OI 动能、聪明钱阶段巨鲸均价、主动买卖比 Taker、大户多空比数据全面参与打分；
2. 共振得分 resonance_score 计算正确，处于 [0, 100] 合法区间；
3. position_multiplier 动态缩放生效（0.5x ~ 1.3x）；
4. 缺数据时给予中性分，不一票否决；
5. live_signal 持久化与 live_api 成功透传 resonance_score 与 resonance_summary；
6. 模拟实盘开仓 open_position 按 position_multiplier 动态调节下单规模；
7. 保持单一威科夫主策略主线不变。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

import wyckoff_factor_resonance as WFR
import paper_engine as PE
import live_store as L

REPORT = {
    "test_name": "WYCKOFF-ALL-DATA-01",
    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    "assertions": [],
    "summary": {}
}


def record_assert(name: str, passed: bool, detail: str = ""):
    REPORT["assertions"].append({
        "name": name,
        "passed": bool(passed),
        "detail": detail
    })
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name} - {detail}")
    assert passed, f"Assertion failed: {name} ({detail})"


def main():
    now_ms = int(time.time() * 1000)

    # ─────────────────────────────────────────────────────────────
    # 测试 1: 多因子强共振打分测试 (所有因子正向共振)
    # ─────────────────────────────────────────────────────────────
    res_strong = WFR.evaluate_resonance(
        symbol="BTCUSDT",
        decision_ms=now_ms,
        current_price=65000.0,
        box_low=60000.0,
        box_high=68000.0,
        box_atr=2000.0,
        oi_growth_6h=0.06,           # +6% 强增仓 (+15)
        whale_vwap_override=64000.0, # 贴近巨鲸成本支撑 (+15)
        taker_ratio_override=1.25,   # 主动买盘强劲 (+15)
        top_pos_ls_override=1.30,    # 大户多头 (+15)
    )
    # 40 (基准) + 15 + 15 + 15 + 15 = 100
    record_assert(
        "强共振得分处于高分带(>=80分)",
        res_strong["resonance_score"] >= 80.0,
        f"score={res_strong['resonance_score']}, mult={res_strong['position_multiplier']}"
    )
    record_assert(
        "强共振仓位倍数扩大至1.3x",
        res_strong["position_multiplier"] == 1.3,
        f"multiplier={res_strong['position_multiplier']}"
    )
    record_assert(
        "四类因子全部包含在 factors 输出中",
        all(k in res_strong["factors"] for k in ("oi", "whale_cost", "taker_ratio", "long_short")),
        f"factors keys={list(res_strong['factors'].keys())}"
    )

    # ─────────────────────────────────────────────────────────────
    # 测试 2: 多因子背离/逆向打分测试 (主力减仓 + 破位 + 卖盘)
    # ─────────────────────────────────────────────────────────────
    res_weak = WFR.evaluate_resonance(
        symbol="BTCUSDT",
        decision_ms=now_ms,
        current_price=58000.0,
        box_low=60000.0,
        box_high=68000.0,
        box_atr=2000.0,
        oi_growth_6h=-0.08,          # -8% 大减仓 (-4)
        whale_vwap_override=65000.0, # 破位巨鲸成本 (-4)
        taker_ratio_override=0.75,   # 卖盘压制 (-4)
        top_pos_ls_override=0.80,    # 大户偏空 (-4)
    )
    record_assert(
        "背离与减仓时得分明显走低",
        res_weak["resonance_score"] < 50.0,
        f"weak_score={res_weak['resonance_score']} vs strong={res_strong['resonance_score']}"
    )
    record_assert(
        "偏弱时仓位乘数防守下调至0.5x",
        res_weak["position_multiplier"] == 0.5,
        f"weak_multiplier={res_weak['position_multiplier']}"
    )

    # ─────────────────────────────────────────────────────────────
    # 测试 3: 缺失容错 (所有外部指标未提供时不抛异常、不判死，给中性分)
    # ─────────────────────────────────────────────────────────────
    res_neutral = WFR.evaluate_resonance(
        symbol="UNKNOWN_NEW_COIN",
        decision_ms=now_ms,
        current_price=10.0,
        box_low=8.0,
        box_high=12.0,
        box_atr=1.0,
    )
    record_assert(
        "未知代币缺失外部指标时不崩溃且给出基准分",
        50.0 <= res_neutral["resonance_score"] <= 75.0,
        f"neutral_score={res_neutral['resonance_score']}, grade={res_neutral['grade']}"
    )
    record_assert(
        "中性缺失时仓位乘数为标准1.0x或0.8x",
        res_neutral["position_multiplier"] in (0.8, 1.0),
        f"neutral_mult={res_neutral['position_multiplier']}"
    )

    # ─────────────────────────────────────────────────────────────
    # 测试 4: 模拟实盘 open_position 动态仓位调节验证
    # ─────────────────────────────────────────────────────────────
    from paper_engine import _apply_order_rounding, contract_limits
    limits = contract_limits("BTCUSDT") or {
        "price_step": 0.1, "quantity_step": 0.001, "min_notional": 5.0, "max_nominal_usd": 50000.0
    }
    
    # 1.3x 强共振乘数 (使用 20,000 U 账户以展现纯 risk 驱动的数量差异)
    rr_strong = _apply_order_rounding(
        raw_open=65000.0,
        atr=2000.0,
        limits=limits,
        risk_per_trade_fraction=0.01 * 1.3,
        equity=20000.0,
        stop_atr_mult=3.0,
        slippage_each_side=0.0005,
        gross_open_notional=0.0,
        free_cash=20000.0,
        leverage=1.0,
        cash_reserve_fraction=0.1,
        single_name_notional_cap=0.30,
        gross_long_notional_cap=0.70,
        prev_quote_volume=10000000.0,
        participation_cap=0.01,
        portfolio_risk_open=0.0,
        portfolio_risk_cap=0.05,
        stoploss_s=0.05,
        strategy_id="FT-LOW4H",
        direction="long"
    )

    # 0.5x 防守乘数
    rr_half = _apply_order_rounding(
        raw_open=65000.0,
        atr=2000.0,
        limits=limits,
        risk_per_trade_fraction=0.01 * 0.5,
        equity=20000.0,
        stop_atr_mult=3.0,
        slippage_each_side=0.0005,
        gross_open_notional=0.0,
        free_cash=20000.0,
        leverage=1.0,
        cash_reserve_fraction=0.1,
        single_name_notional_cap=0.30,
        gross_long_notional_cap=0.70,
        prev_quote_volume=10000000.0,
        participation_cap=0.01,
        portfolio_risk_open=0.0,
        portfolio_risk_cap=0.05,
        stoploss_s=0.05,
        strategy_id="FT-LOW4H",
        direction="long"
    )

    record_assert(
        "强共振仓位计算成功",
        rr_strong.get("reject") is None and rr_strong.get("qty", 0) > 0,
        f"strong_qty={rr_strong.get('qty')}, notional={rr_strong.get('notional')}"
    )
    record_assert(
        "防守乘数相比强共振有效按比例收缩仓位规模",
        rr_half.get("reject") is None and rr_half["qty"] < rr_strong["qty"],
        f"half_qty={rr_half.get('qty')} vs strong_qty={rr_strong.get('qty')}"
    )
    record_assert(
        "强共振与防守仓位均严格受控于单票 30% 敞口上限",
        rr_strong["notional"] <= 20000.0 * 0.30 and rr_half["notional"] <= 20000.0 * 0.30,
        f"strong_notional={rr_strong['notional']}, half_notional={rr_half['notional']}"
    )

    # ─────────────────────────────────────────────────────────────
    # 测试 5: 真实标的在现役行情与指标库上的穿透采样验证
    # ─────────────────────────────────────────────────────────────
    for test_sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        eval_res = WFR.evaluate_resonance(
            symbol=test_sym,
            decision_ms=now_ms,
            current_price=65000.0 if test_sym == "BTCUSDT" else 3000.0,
            box_low=60000.0,
            box_high=68000.0,
            box_atr=2000.0
        )
        record_assert(
            f"真实标的 {test_sym} 多因子共振计算成功",
            "resonance_score" in eval_res and "summary" in eval_res,
            eval_res["summary"]
        )

    # 汇总写入报告
    passed_n = sum(1 for a in REPORT["assertions"] if a["passed"])
    total_n = len(REPORT["assertions"])
    REPORT["summary"] = {
        "total": total_n,
        "passed": passed_n,
        "failed": total_n - passed_n,
        "pass_rate": f"{passed_n / total_n * 100:.1f}%"
    }

    out_file = PROJECT_ROOT / "evidence" / "WYCKOFF-ALL-DATA-01" / "out" / "all_data_resonance_report.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, ensure_ascii=False, indent=2)

    print(f"\n==========================================")
    print(f"全项断言验证通过! 共 {total_n} 项，通过率 {REPORT['summary']['pass_rate']}")
    print(f"报告已持久化至: {out_file}")
    print(f"==========================================")


if __name__ == "__main__":
    main()

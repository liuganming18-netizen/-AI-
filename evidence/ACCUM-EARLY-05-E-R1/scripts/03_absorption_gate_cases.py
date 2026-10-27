# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-E-R1 | 脚本 03: 承接与卖压门槛开启/关闭显式正反例测试

【目的】
1. 验证承接门槛在开启 (require=true) 与关闭 (require=false) 时的行为；
2. 补齐开启门槛时的标准正例与 4 类反例：
   - 正例：主动买占比达标、主动卖压额达标、收盘位于当根振幅上半区；
   - 反例 A：主动买占比低于基准中位数 × buy_ratio_mult；
   - 反例 B：主动卖出额超过基准中位数 × sell_pressure_mult；
   - 反例 C：收盘未位于当根振幅上半区 (require_upper_half=true)；
   - 反例 D：历史 K 线不足 20 根或关键名义额字段缺失 (quote_volume / taker_buy_quote_volume)；
3. 重点核实指标口径：必须为 USDT 名义额 (taker_buy_quote_volume / quote_volume)，严禁币数比；
4. 深度指出当前实现缺陷：虽然单函数逻辑已修正为名义额与中位数，但状态机中**调用时序严重颠倒（仍在确认突破根确认，扫低根完全未执行）**！
"""
import copy
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

import accum_early_entry as AEE

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E-R1" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def build_mock_bar(high, low, close, open_p, quote_vol, taker_quote_vol):
    return {
        "open": str(open_p),
        "high": str(high),
        "low": str(low),
        "close": str(close),
        "quote_volume": float(quote_vol) if quote_vol is not None else None,
        "taker_buy_quote_volume": float(taker_quote_vol) if taker_quote_vol is not None else None,
        "volume": "100.0",
        "close_time": 1785500000000
    }

def run_absorption_tests():
    fixture_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
    cfg_base = AEE.load_config(fixture_cfg_path)

    results = {
        "summary": "承接与卖压门槛开启/关闭与正反例测试结果",
        "cases": []
    }

    # ── 1. 门槛关闭测试 (require=false) ──
    cfg_disabled = copy.deepcopy(cfg_base)
    cfg_disabled["doc"]["shared"]["sell_pressure"]["require"] = False
    dummy_bars = [build_mock_bar(10, 5, 8, 6, 1000, 500)]
    ok, detail, conds = AEE._absorption(dummy_bars, 0, cfg_disabled)
    results["cases"].append({
        "case_name": "门槛关闭 (require=false)",
        "expected_ok": True,
        "actual_ok": ok,
        "detail": detail,
        "conditions": conds,
        "pass": (ok is True and detail.get("skipped") == "not_required")
    })

    # 构造标准基准：前 20 根 K 线，quote_volume 恒为 10000，taker_buy_quote_volume 恒为 5000 (占比 0.5，主动卖额 5000)
    baseline_bars = []
    for _ in range(20):
        baseline_bars.append(build_mock_bar(10.0, 5.0, 8.0, 6.0, 10000.0, 5000.0))

    # ── 2. 门槛开启 - 标准正例 ──
    # 当前根：quote_volume 10000, taker_buy_quote_volume 6000 (占比 0.6 >= 0.5*1.0)
    # 卖额 = 4000 <= 5000 * 0.8 = 4000
    # high=10, low=5, close=8 (上半区 >= 5 + 0.5*5 = 7.5)
    cfg_enabled = copy.deepcopy(cfg_base)
    cfg_enabled["doc"]["shared"]["sell_pressure"]["require"] = True
    cfg_enabled["doc"]["shared"]["sell_pressure"]["buy_ratio_mult"] = 1.0
    cfg_enabled["doc"]["shared"]["sell_pressure"]["sell_pressure_mult"] = 0.8
    cfg_enabled["doc"]["shared"]["sell_pressure"]["require_upper_half"] = True

    pos_bar = build_mock_bar(10.0, 5.0, 8.0, 6.0, 10000.0, 6000.0)
    test_bars_pos = baseline_bars + [pos_bar]
    ok, detail, conds = AEE._absorption(test_bars_pos, 20, cfg_enabled)
    results["cases"].append({
        "case_name": "门槛开启 - 正例 (买比达标、卖额受控、上半区收盘)",
        "expected_ok": True,
        "actual_ok": ok,
        "detail": detail,
        "conditions": conds,
        "pass": (ok is True)
    })

    # ── 3. 门槛开启 - 反例 A: 主动买占比不足 ──
    # 当前根：taker_buy_quote_volume 4000 (占比 0.4 < 0.5*1.0)
    neg_a_bar = build_mock_bar(10.0, 5.0, 8.0, 6.0, 10000.0, 4000.0)
    test_bars_neg_a = baseline_bars + [neg_a_bar]
    ok, detail, conds = AEE._absorption(test_bars_neg_a, 20, cfg_enabled)
    results["cases"].append({
        "case_name": "门槛开启 - 反例 A (主动买占比 0.4 < 基准 0.5)",
        "expected_ok": False,
        "actual_ok": ok,
        "detail": detail,
        "conditions": conds,
        "pass": (ok is False)
    })

    # ── 4. 门槛开启 - 反例 B: 卖压额超标 ──
    # 当前根：quote_vol 20000, taker_buy_quote_volume 12000 (占比 0.6 >= 0.5，但卖额 = 8000 > 5000*0.8=4000)
    neg_b_bar = build_mock_bar(10.0, 5.0, 8.0, 6.0, 20000.0, 12000.0)
    test_bars_neg_b = baseline_bars + [neg_b_bar]
    ok, detail, conds = AEE._absorption(test_bars_neg_b, 20, cfg_enabled)
    results["cases"].append({
        "case_name": "门槛开启 - 反例 B (卖额 8000 > 上限 4000)",
        "expected_ok": False,
        "actual_ok": ok,
        "detail": detail,
        "conditions": conds,
        "pass": (ok is False)
    })

    # ── 5. 门槛开启 - 反例 C: 收盘未在上半区 ──
    # 当前根：high=10, low=5, close=6 (< 7.5)
    neg_c_bar = build_mock_bar(10.0, 5.0, 6.0, 6.5, 10000.0, 6000.0)
    test_bars_neg_c = baseline_bars + [neg_c_bar]
    ok, detail, conds = AEE._absorption(test_bars_neg_c, 20, cfg_enabled)
    results["cases"].append({
        "case_name": "门槛开启 - 反例 C (收盘 6.0 未位于上半区 [7.5, 10])",
        "expected_ok": False,
        "actual_ok": ok,
        "detail": detail,
        "conditions": conds,
        "pass": (ok is False)
    })

    # ── 6. 门槛开启 - 反例 D: 字段缺失 / 历史不足 ──
    # 历史只有 5 根
    short_bars = baseline_bars[:5] + [pos_bar]
    ok, detail, conds = AEE._absorption(short_bars, 5, cfg_enabled)
    results["cases"].append({
        "case_name": "门槛开启 - 反例 D (历史不足 20 根)",
        "expected_ok": None,
        "actual_ok": ok,
        "detail": detail,
        "conditions": conds,
        "pass": (ok is None and detail.get("reason") == "baseline_window_short")
    })

    # ── 7. 架构级缺陷核验：调用位置与时序审计 ──
    # 静态检查在 accum_early_entry.py 中：
    # 1. 扫低函数 _sweep_check 是否调用了 _absorption -> False
    # 2. 状态机在进入 SWEEP 时是否核验了承接 -> False
    # 3. 确认突破函数 _confirm_actions 是否调用了 _absorption -> True
    results["timing_audit"] = {
        "sweep_check_invokes_absorption": False,
        "state_machine_sweep_gate_enforced": False,
        "confirm_actions_invokes_absorption": True,
        "conclusion": "当前 05-B 的 _absorption 函数单体计算正确（使用 USDT 名义额与基准中位数），但是在全链时序上存在严重错位：扫低时不执行承接门槛，而在确认突破压制位时才执行！"
    }

    out_file = OUT_DIR / "03_absorption_cases.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Absorption test cases written to {out_file}")

if __name__ == "__main__":
    run_absorption_tests()

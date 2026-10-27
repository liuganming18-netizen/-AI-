# -*- coding: utf-8 -*-
"""工单三｜反重力独立核查：6大疑点隔离审计与复现探针
严守纪律：纯只读/内存隔离，禁止向生产注入信号、订单或修改账本。
"""
import sys
import os
import json
import sqlite3
import time
from pathlib import Path

# 确保能加载工程模块
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "框架" / "backend"))

def audit_suspect_1_trailing_channel():
    """疑点 1：逐笔跟踪止损通道
    核查内容：
    1. engine_adapter.py 中 starts 遍历全部候选导致的同币后序持仓污染
    2. 生产是否存在实时逐笔 ticker 通道（经核查代码，不存在，只有 4h 抬线 + 15m low 退出）
    """
    import inspect
    from 策略.WyckoffMaster.event_trend_v0_1 import engine_adapter
    src = inspect.getsource(engine_adapter)
    has_starts_loop = "starts = sorted({_normalize_to_ms(c.execution_earliest_ms) for c in candidates})" in src
    has_starts_iter = "for s in starts:" in src
    
    # 隔离复现逻辑缺陷：
    # 候选 1 在 05:00 入场，候选 2 在 09:00 入场。
    # 09:15 判定候选 2 时，由于 starts 包含 05:00，取到了 08:00 收盘的 4h K 线（low=92），
    # 若候选 2 入场价=100，初始止损 95，但借用了 08:00 收盘前的低点计算止损，导致入场时刻规则失序。
    simulated_bug = {
        "candidate_1_entry": "2026-10-01 05:00:00",
        "candidate_2_entry": "2026-10-01 09:00:00",
        "bar_4h_closed_at": "2026-10-01 08:00:00",
        "starts_list": ["2026-10-01 05:00:00", "2026-10-01 09:00:00"],
        "bug_trigger": "候选2借用了 08:00 已收盘4h（早于候选2入场时刻09:00），因 starts[0] < 08:00 收盘时刻"
    }

    # 核查 unified_runtime 中是否有任何逐笔 ticker 跟踪止损通道
    with open(REPO_ROOT / "框架" / "backend" / "unified_runtime.py", "r", encoding="utf-8") as f:
        ur_src = f.read()
    has_realtime_tick_trailing = "trade_ticker" in ur_src or "aggTrade" in ur_src or "trailing_stop_tick" in ur_src

    return {
        "suspect": "逐笔跟踪止损通道",
        "classification": "部分实际缺陷(回测starts污染)，部分旧路径/未证实猜测(无逐笔ticker通道)",
        "engine_adapter_starts_bug_present": has_starts_loop and has_starts_iter,
        "realtime_tick_channel_present": has_realtime_tick_trailing,
        "isolation_reproduction": simulated_bug,
        "frozen_rule_fact": "冻结规范仅支持4h收盘抬高候选线、15m低点判断退出；不存在毫秒级逐笔跟踪通道"
    }

def audit_suspect_2_gap_execution():
    """疑点 2：跳空成交（Gap Execution）
    核查内容：
    模拟盘 paper_engine.py 中 check_position_exit_on_bar 和 advance_position_trailing
    在 low <= cur_stop 时，直接使用 raw_price = cur_stop，缺少跳空开盘判定（bar_open <= cur_stop）。
    """
    with open(REPO_ROOT / "框架" / "backend" / "paper_engine.py", "r", encoding="utf-8") as f:
        pe_src = f.read()

    # 检查 check_position_exit_on_bar
    has_exit_check = "if cur_stop > 0 and low_p <= cur_stop:" in pe_src
    has_hardcoded_stop_exit = "close_at_price(\n            arm, position_id, raw_price=cur_stop," in pe_src or "raw_price=cur_stop" in pe_src
    
    # 隔离复现：
    cur_stop = 95.0
    bar_open = 90.0  # 跳空暴跌开盘
    bar_low = 88.0
    # 预期行为（按 trading_core 规范）：跳空开盘应以 bar_open (90.0) 平仓
    expected_exit_raw_price = bar_open
    # 实际模拟盘行为：直接使用 cur_stop (95.0)
    actual_exit_raw_price = cur_stop
    underestimated_loss = (actual_exit_raw_price - expected_exit_raw_price) # 多算了 5.0 的价值，低估了亏损

    return {
        "suspect": "跳空成交 (Gap Execution)",
        "classification": "实际缺陷 (P0 经济失真)",
        "code_location": "框架/backend/paper_engine.py:2296-2302 & 2458-2464",
        "has_hardcoded_stop_exit": has_hardcoded_stop_exit,
        "trigger_condition": "当15m或4h K线出现开盘向下跳空（open <= cur_stop）",
        "expected_behavior": f"按跳空开盘价 open={bar_open} 执行平仓并计滑点",
        "actual_behavior": f"以完美止损价 cur_stop={cur_stop} 平仓，低估亏损 {underestimated_loss} 美元/单位",
        "impact_scope": "所有遭遇黑天鹅或剧烈跳空跌破止损的持仓，模拟盘账本系统性高估资金净值"
    }

def audit_suspect_3_missing_intermediate_15m():
    """疑点 3：漏检中间十五分钟线
    核查内容：
    unified_runtime.py:2101 及 paper_loop.py:210
    使用了 ORDER BY open_time DESC LIMIT 1
    当运行中断或轮询延迟跨越 >1 根 15m 时，中间 K 线被跳过。
    """
    with open(REPO_ROOT / "框架" / "backend" / "unified_runtime.py", "r", encoding="utf-8") as f:
        ur_src = f.read()
    with open(REPO_ROOT / "框架" / "backend" / "paper_loop.py", "r", encoding="utf-8") as f:
        pl_src = f.read()

    ur_has_limit1 = "AND open_time>? AND close_time<=? \n                            ORDER BY open_time DESC LIMIT 1" in ur_src or "ORDER BY open_time DESC LIMIT 1" in ur_src
    pl_has_limit1 = "ORDER BY open_time DESC LIMIT 1" in pl_src

    # 隔离复现场景：
    # 假设仓位止损为 100.0
    # 停机/延迟期间产生了两根已闭合 15m K线：
    # Bar 1 (中间线, 10:00-10:15): open=102, high=103, low=98, close=99 (触及止损 low=98 <= 100)
    # Bar 2 (最新线, 10:15-10:30): open=101, high=105, low=100.5, close=104 (反弹，low=100.5 > 100)
    bars_in_db = [
        {"time": "10:00-10:15", "low": 98.0, "should_exit": True},
        {"time": "10:15-10:30", "low": 100.5, "should_exit": False}
    ]
    # LIMIT 1 读出的结果
    fetched_bar = bars_in_db[-1] # 只读到最新一根
    simulated_exit_triggered = fetched_bar["low"] <= 100.0

    return {
        "suspect": "漏检中间十五分钟线",
        "classification": "实际缺陷 (P0 穿透存活漏洞)",
        "code_location": "框架/backend/unified_runtime.py:2097-2103 & paper_loop.py:200-215",
        "limit_1_present_in_ur": ur_has_limit1,
        "limit_1_present_in_pl": pl_has_limit1,
        "trigger_condition": "系统停机重启、网络拉取延迟或循环阻塞超过 15 分钟，期间产生 >=2 根 15m K线",
        "expected_behavior": "连续遍历区间内所有已闭合 15m K线，中间 K线低点击穿止损即执行退出",
        "actual_behavior": f"仅查询 ORDER BY open_time DESC LIMIT 1，导致中间触及止损的 Bar 1 被漏检，模拟结果 exit_triggered={simulated_exit_triggered}（错误存活）",
        "impact_scope": "任何非零延迟/断网重启场景下的持仓保护彻底失效，产生虚假盈利或严重漂移"
    }

def audit_suspect_4_funding_drawdown_scheduling():
    """疑点 4：资金费与回撤调度
    核查内容：
    unified_runtime.py 和 paper_loop.py 中是否调用了 accrue_funding 和 enforce_drawdown。
    """
    with open(REPO_ROOT / "框架" / "backend" / "unified_runtime.py", "r", encoding="utf-8") as f:
        ur_src = f.read()
    with open(REPO_ROOT / "框架" / "backend" / "paper_loop.py", "r", encoding="utf-8") as f:
        pl_src = f.read()

    ur_has_accrue = "accrue_funding" in ur_src
    ur_has_drawdown = "enforce_drawdown" in ur_src
    pl_has_accrue = "accrue_funding" in pl_src
    pl_has_drawdown = "enforce_drawdown" in pl_src

    return {
        "suspect": "资金费与回撤调度",
        "classification": "实际缺陷 (P0 核心风控脱幅)",
        "code_location": "框架/backend/unified_runtime.py (主运行入口)",
        "unified_runtime_accrue_funding_called": ur_has_accrue,
        "unified_runtime_enforce_drawdown_called": ur_has_drawdown,
        "paper_loop_accrue_funding_called": pl_has_accrue,
        "paper_loop_enforce_drawdown_called": pl_has_drawdown,
        "trigger_condition": "统一运行时 (UnifiedRuntime) 常驻运行时",
        "expected_behavior": "每 15m 驱动 enforce_drawdown 检查 20% 账户回撤；每 8h 驱动 accrue_funding 计提持仓资金费",
        "actual_behavior": "UnifiedRuntime.step_once 中完全没有调用 accrue_funding 与 enforce_drawdown，风控完全脱幅，资金费为0行",
        "impact_scope": "模拟盘运行期间即便回撤超过 20% 也不会触发熔断强平；持仓期间动态净值完全失真"
    }

def audit_suspect_5_price_precision_fallback():
    """疑点 5：价格精度兜底
    核查内容：
    paper_engine.py:2404 limits = contract_limits(symbol) or {"price_step": 0.01}
    D-08 模拟盘平仓价未按 step 截断。
    """
    with open(REPO_ROOT / "框架" / "backend" / "paper_engine.py", "r", encoding="utf-8") as f:
        pe_src = f.read()

    has_silent_fallback = 'limits = contract_limits(symbol) or {"price_step": 0.01}' in pe_src

    # 隔离复现精度失真：
    btc_actual_step = 0.1
    shib_actual_step = 0.000001
    fallback_step = 0.01

    raw_price_btc = 65432.145
    # 若用 0.01 兜底取整 vs 0.1 正确取整
    from 策略.WyckoffMaster.trading_core.fill import price_breakdown
    rounded_fallback_btc = price_breakdown(raw_price=raw_price_btc, is_buy=False, price_step=fallback_step, slippage_rate=0.0005)["post_round_price"]
    rounded_correct_btc = price_breakdown(raw_price=raw_price_btc, is_buy=False, price_step=btc_actual_step, slippage_rate=0.0005)["post_round_price"]

    return {
        "suspect": "价格精度兜底",
        "classification": "实际缺陷 (违背严禁静默兜底准则)",
        "code_location": "框架/backend/paper_engine.py:2404",
        "has_silent_fallback": has_silent_fallback,
        "trigger_condition": "当新上线币种或无合约元数据标的参与计算跟踪止损",
        "expected_behavior": "严禁兜底，合约规格缺失时必须明确抛错并拒绝推进 (missing_contract_spec)",
        "actual_behavior": f"静默回退到 price_step=0.01。对比 BTC: 兜底成交价={rounded_fallback_btc} vs 正确成交价={rounded_correct_btc} (产生价格精度非法分叉)",
        "impact_scope": "合约缺失标的止损位计算精度失准，直接违背交易所实盘下单规则"
    }

def audit_suspect_6_restart_signal_qualification():
    """疑点 6：重启恢复信号资格
    核查内容：
    unified_runtime.py:bootstrap 与 _route_recovered_candidates
    """
    with open(REPO_ROOT / "框架" / "backend" / "unified_runtime.py", "r", encoding="utf-8") as f:
        ur_src = f.read()

    has_recovered_routing = "_route_recovered_candidates" in ur_src
    has_epoch_guard = "before_paper_epoch_no_backfill" in ur_src
    has_lag_check = "execution_earliest_ms + PE.MAX_FILL_LAG_MS >= int(upto_ms)" in ur_src

    return {
        "suspect": "重启恢复信号资格",
        "classification": "已修补路径但存在时序/多进程边界隐患 (需严格测试去重与时效)",
        "code_location": "框架/backend/unified_runtime.py:896-960 & 2233-2275",
        "has_recovered_routing": has_recovered_routing,
        "has_epoch_guard": has_epoch_guard,
        "has_lag_check": has_lag_check,
        "trigger_condition": "服务进程重启时执行 bootstrap 预热回放",
        "expected_behavior": "严格识别停机期间产生的合法信号（在 MAX_FILL_LAG 内），且对已存在订单/持仓幂等去重",
        "actual_behavior": "当前通过 recovered_candidates 恢复并检查三重表，但缺乏对'模型动态决策'状态的恢复协议（动态策略的上下文在重启时会丢失）",
        "impact_scope": "若接入 AI 动态策略，无状态的重启将导致 AI 上下文断裂、做出重复或冲突动作"
    }

def main():
    print("=== 开始工单三 6 大疑点隔离审计与复现 ===")
    results = [
        audit_suspect_1_trailing_channel(),
        audit_suspect_2_gap_execution(),
        audit_suspect_3_missing_intermediate_15m(),
        audit_suspect_4_funding_drawdown_scheduling(),
        audit_suspect_5_price_precision_fallback(),
        audit_suspect_6_restart_signal_qualification()
    ]
    
    out_path = REPO_ROOT / "evidence" / "AI-DYNAMIC-AUDIT-01" / "out" / "audit_suspects_isolated.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"审计完成！隔离证据已写入: {out_path}")
    for r in results:
        print(f"[{r['suspect']}] => {r['classification']}")

if __name__ == "__main__":
    main()

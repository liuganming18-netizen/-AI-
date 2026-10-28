# -*- coding: utf-8 -*-
"""NATURAL-CANDIDATE-01｜自然零候选穿透排查探针（全池 526 标的 1220 次 RETEST 根因分析）。

运行目的：
  1. 重建 48 天预热回放中的全部状态迁移；
  2. 提取全池 1220 次 RETEST 事件的逐笔明细；
  3. 统计 1220 次 RETEST 的最终终结去向：
     - INVALIDATED（细分：跌破 Lr-0.1A vs 连续 2 根破 L）；
     - EXPIRED（7 天超时未突破）；
     - 突破尝试（穿越 H+0.1A）：VOLUME_REJECTED / OI_UNAVAILABLE / OI_REJECTED / CANDIDATE；
  4. 测算 RETEST 存活时长分布与最高反弹幅度（相对箱体高点 H 与宽度的比例）；
  5. 对照测算：在 3b 方案（关闭 OI 门槛）下，这 1220 次 RETEST 能转化出多少 CANDIDATE；
  6. 严格只读，零写入生产库。
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

# 项目根与 backend 导入
BACKEND = Path("/Users/boos/Desktop/威科夫因子共振趋势/框架/backend")
sys.path.insert(0, str(BACKEND))

import live_store as L
import project_paths as PP
import history_loader as HL
from unified_runtime import MultiSymbolSignalEngine, _default_universe
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventState


def iso(ms) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000)) if ms else "—"


def main():
    out_dir = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/NATURAL-CANDIDATE-01/out")
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    now_ms = int(time.time() * 1000)

    # 1. 获取全池标的
    symbols = _default_universe()
    print(f"[*] 全池标的数: {len(symbols)}")

    # 2. 初始化引擎（使用标准生产 3a 配置：enable_oi_filter=True）
    eng = MultiSymbolSignalEngine(symbols=symbols, enable_oi_filter=True)
    eng.history_provider = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=lambda *args: None)
    
    print("[*] 正在装载冷热合并历史数据...")
    eng.prepare_history(now_ms, log=lambda *args: None)
    
    print("[*] 正在执行全池 48 天预热回放 (bootstrap)...")
    b_stat = eng.bootstrap(now_ms, log=lambda *args: None)
    print(f"[*] Bootstrap 完成！耗时: {time.time() - t0:.1f} 秒")

    funnel = eng.funnel()
    print("[*] 历史漏斗:", json.dumps(funnel["history"], indent=2, ensure_ascii=False))

    # 3. 提取所有经历过 RETEST 的事件
    retest_events: List[Dict[str, Any]] = []
    
    for sym in symbols:
        engine = eng.engines.get(sym)
        if not engine:
            continue
        sm = engine.sm
        all_events = list(sm.completed_events)
        if sm.active_event:
            all_events.append(sm.active_event)
            
        for ev in all_events:
            states = [t.to_state for t in ev.history]
            if "RETEST" not in states:
                continue
                
            retest_idx = states.index("RETEST")
            retest_trans = ev.history[retest_idx]
            retest_ms = retest_trans.trigger_bar_close_ms
            
            box = ev.box
            H = box.high_h
            L_val = box.low_l
            A = box.atr_a
            width = H - L_val
            width_atr = width / A if A > 0 else 0
            
            lr = ev.retest_low_lr
            lr_limit = lr - sm.config.initial_stop_buffer_atr * A if lr is not None else None
            breakout_level = H + sm.config.breakout_buffer_atr * A
            
            # 分析 RETEST 之后的轨迹
            after_retest = ev.history[retest_idx + 1:]
            term_state = after_retest[-1].to_state if after_retest else ev.current_state.value
            term_reason = after_retest[-1].reason if after_retest else (ev.termination_reason or "ACTIVE")
            term_ms = after_retest[-1].trigger_bar_close_ms if after_retest else (ev.ended_at_ms or now_ms)
            duration_hours = (term_ms - retest_ms) / 3600000.0
            
            # 判断终结子原因
            sub_cause = "UNKNOWN"
            if term_state == "INVALIDATED":
                if "跌破Lr-0.1A" in term_reason:
                    # 检查指标判断究竟是跌破 Lr 还是连续 2 根破 L
                    metrics = after_retest[-1].metrics_snapshot or {}
                    low_val = metrics.get("low")
                    lr_lim = metrics.get("lr_limit")
                    if low_val is not None and lr_lim is not None and low_val < lr_lim:
                        sub_cause = "BREAK_LR_STOP"
                    else:
                        sub_cause = "CONSECUTIVE_BELOW_L"
                elif "连续2根" in term_reason:
                    sub_cause = "CONSECUTIVE_BELOW_L"
                else:
                    sub_cause = "OTHER_INVALIDATED"
            elif term_state == "EXPIRED":
                sub_cause = "7_DAYS_TIMEOUT"
            elif term_state == "VOLUME_REJECTED":
                sub_cause = "VOLUME_LT_1_5_V20"
            elif term_state == "OI_UNAVAILABLE":
                sub_cause = "OI_DATA_UNAVAILABLE"
            elif term_state == "OI_REJECTED":
                sub_cause = "OI_GROWTH_LT_2_PCT"
            elif term_state == "CANDIDATE":
                sub_cause = "CANDIDATE_GENERATED"
            elif term_state == "RETEST":
                sub_cause = "STILL_IN_RETEST"
                
            # 统计 RETEST 期间价格反弹情况（从 1h 收盘序列中分析最高价）
            # 找到在 retest_ms 到 term_ms 之间的 1h K 线
            w = engine.sm  # 可以通过 traces 或历史记录
            # 我们从 raw traces 中获取
            retest_traces = [tr for tr in ev.traces if tr.bar_close_ms >= retest_ms and tr.bar_close_ms <= term_ms]
            max_high = max([tr.price_high for tr in retest_traces], default=H) if retest_traces else H
            rebound_from_l = (max_high - L_val)
            rebound_pct_of_box = rebound_from_l / width if width > 0 else 0.0
            dist_to_breakout = breakout_level - max_high
            
            retest_events.append({
                "symbol": sym,
                "event_id": ev.event_id,
                "box_h": round(H, 6),
                "box_l": round(L_val, 6),
                "box_atr": round(A, 6),
                "box_width_atr": round(width_atr, 2),
                "retest_utc": iso(retest_ms),
                "retest_ms": retest_ms,
                "retest_low_lr": round(lr, 6) if lr else None,
                "lr_stop_limit": round(lr_limit, 6) if lr_limit else None,
                "breakout_level": round(breakout_level, 6),
                "term_state": term_state,
                "sub_cause": sub_cause,
                "term_reason": term_reason,
                "term_utc": iso(term_ms),
                "duration_hours": round(duration_hours, 2),
                "rebound_pct_of_box": round(rebound_pct_of_box * 100, 1),
                "dist_to_breakout": round(dist_to_breakout, 6),
            })

    print(f"[*] 成功提取经历 RETEST 的事件样本数: {len(retest_events)}")

    # 4. 统计分析
    term_counter = Counter([e["term_state"] for e in retest_events])
    sub_counter = Counter([e["sub_cause"] for e in retest_events])
    
    print("\n=== RETEST 最终终结状态分布 ===")
    for k, v in term_counter.most_common():
        print(f"  {k:20s}: {v:5d} ({v / len(retest_events) * 100:.1f}%)")
        
    print("\n=== RETEST 细分子原因分布 ===")
    for k, v in sub_counter.most_common():
        print(f"  {k:25s}: {v:5d} ({v / len(retest_events) * 100:.1f}%)")

    # 存活时间统计
    durations = [e["duration_hours"] for e in retest_events]
    durations.sort()
    avg_dur = sum(durations) / len(durations) if durations else 0
    med_dur = durations[len(durations) // 2] if durations else 0
    p90_dur = durations[int(len(durations) * 0.9)] if durations else 0

    # 突破尝试统计
    attempted_breakout = [e for e in retest_events if e["term_state"] in ("VOLUME_REJECTED", "OI_UNAVAILABLE", "OI_REJECTED", "CANDIDATE")]
    
    summary = {
        "生成时间": iso(now_ms),
        "全池标的数": len(symbols),
        "48天回放总RETEST事件数": len(retest_events),
        "漏斗历史迁移数": funnel["history"],
        "最终终态分布": dict(term_counter),
        "细分子原因分布": dict(sub_counter),
        "存活时长_小时": {
            "平均值": round(avg_dur, 2),
            "中位数": round(med_dur, 2),
            "P90": round(p90_dur, 2),
            "min": round(durations[0], 2) if durations else 0,
            "max": round(durations[-1], 2) if durations else 0,
        },
        "突破尝试数": len(attempted_breakout),
        "突破尝试占比": f"{len(attempted_breakout) / len(retest_events) * 100:.2f}%" if retest_events else "0%",
        "3b方案潜在候选数": term_counter.get("OI_UNAVAILABLE", 0) + term_counter.get("CANDIDATE", 0),
        "3b方案潜在候选说明": "在 3b 方案中关闭了 OI 门槛，被 OI_UNAVAILABLE 拦截的突破尝试如果量能达标将直接转为候选",
    }

    # 写入 JSON
    summary_path = out_dir / "retest_funnel_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[*] 汇总已保存至: {summary_path}")

    # 写入 CSV 明细表
    csv_path = out_dir / "retest_events_detail.csv"
    if retest_events:
        fields = list(retest_events[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(retest_events)
        print(f"[*] 逐笔明细已保存至: {csv_path} (共 {len(retest_events)} 行)")

    print(f"\n[*] 整体分析完成，耗时: {time.time() - t0:.1f} 秒")


if __name__ == "__main__":
    main()

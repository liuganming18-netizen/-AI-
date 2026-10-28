# -*- coding: utf-8 -*-
"""PARALLEL-STRATEGY-01 反重力A：策略状态迁移独立逐bar复算脚本。
选定标的：ORDIUSDT
区间涵盖：
  - 支持样例：ev_WX_EVENT_TREND_V0_1_binance_ORDIUSDT_1716364800000（2024-05-22 至 2024-05-30）
  - 反对/失败样例：ev_WX_EVENT_TREND_V0_1_binance_ORDIUSDT_1705492800000（2024-01-17 至 2024-01-22，Lr-0.1A失效）
  - 未触发样例：ev_WX_EVENT_TREND_V0_1_binance_ORDIUSDT_1704081600000（2024-01-01 至 2024-01-08，7天无扫低超时）
"""
import os
import sys
import glob
import json
import hashlib
import numpy as np
import pandas as pd

# 加入项目根目录
BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
sys.path.insert(0, BASE_DIR)

from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from 策略.WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series, compute_efficiency_ratio, compute_v20_median_volume
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine
from 策略.WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner

def load_data(symbol="ORDIUSDT", start_date="2024-01-01", end_date="2024-06-30"):
    runner = EventTrendRunner(base_dir=BASE_DIR)
    df_4h, df_1h, df_15m, oi_ts, oi_vals, funding_events = runner.load_symbol_data(
        symbol=symbol, start_date=start_date, end_date=end_date
    )
    return df_4h, df_1h, df_15m, oi_ts, oi_vals, funding_events

def run_step_by_step_audit(symbol="ORDIUSDT"):
    print(f"[*] 开始加载 {symbol} 真实市场数据...")
    df_4h, df_1h, df_15m, oi_ts, oi_vals, funding_events = load_data(symbol)
    print(f"    4h K线: {len(df_4h)} 根, 1h K线: {len(df_1h)} 根, 15m K线: {len(df_15m)} 根, OI记录: {len(oi_ts)} 条")

    # 初始化状态机
    cfg = EventTrendConfig()
    sm = EventStateMachine(config=cfg, symbol=symbol, enable_oi_filter=True, pit_mode="declared_assumption")
    sm.pit.register_oi(oi_ts, oi_vals)

    records_1h = df_1h.to_dict("records")

    audit_rows = []
    
    # 关注的特定样例事件
    target_event_id = "ev_WX_EVENT_TREND_V0_1_binance_ORDIUSDT_1716364800000"
    
    for r in records_1h:
        t_open = int(r["timestamp"])
        t_close = t_open + 3600 * 1000
        
        # 4h 建档检查
        box_formed = None
        if t_close % (4 * 3600 * 1000) == 0:
            sub_4h = df_4h[df_4h["close_time"] <= t_close]
            if len(sub_4h) >= 42:
                last_42 = sub_4h.tail(42)
                atr_val = float(sub_4h["atr"].iloc[-1])
                h_arr = last_42["high"].to_numpy(np.float64)
                l_arr = last_42["low"].to_numpy(np.float64)
                c_arr = last_42["close"].to_numpy(np.float64)
                
                # 在调用前记录当前状态
                pre_active = sm.active_event.event_id if sm.active_event else None
                box_res = sm.check_and_form_box(
                    bar_4h_close_ms=t_close,
                    history_42_highs=h_arr,
                    history_42_lows=l_arr,
                    history_42_closes=c_arr,
                    current_atr_a=atr_val
                )
                if box_res:
                    box_formed = box_res
        
        # 1h 推动
        state_before = sm.active_event.current_state.value if sm.active_event else "IDLE"
        event_id_before = sm.active_event.event_id if sm.active_event else "NONE"
        consecutive_before = sm.consecutive_closes_below_l
        
        # 记录 1h 详细指标
        cand = sm.process_1h_bar(
            open_time_ms=t_open,
            open_p=float(r["open"]),
            high_p=float(r["high"]),
            low_p=float(r["low"]),
            close_p=float(r["close"]),
            volume_v=float(r["volume"]),
            oi_timestamps=oi_ts,
            oi_values=oi_vals
        )
        
        state_after = sm.active_event.current_state.value if sm.active_event else "IDLE"
        event_id_after = sm.active_event.event_id if sm.active_event else "NONE"
        
        # 如果是目标事件期间的 bar，或者刚刚完成终止、生成候选，记录审计
        is_relevant = False
        if event_id_before == target_event_id or event_id_after == target_event_id:
            is_relevant = True
        elif box_formed and box_formed.event_id == target_event_id:
            is_relevant = True
        elif sm.transitions and sm.transitions[-1].trigger_bar_close_ms == t_close:
            is_relevant = True

        if is_relevant:
            last_trans = sm.transitions[-1] if (sm.transitions and sm.transitions[-1].trigger_bar_close_ms == t_close) else None
            trans_desc = f"{last_trans.from_state}->{last_trans.to_state} ({last_trans.reason})" if last_trans else "无迁移"
            
            # 记录当前事件的参数
            ev = sm.active_event
            if not ev and sm.completed_events and sm.completed_events[-1].ended_at_ms == t_close:
                ev = sm.completed_events[-1]
            
            H = ev.box.high_h if ev else np.nan
            L = ev.box.low_l if ev else np.nan
            A = ev.box.atr_a if ev else np.nan
            S = ev.sweep_low_s if ev else np.nan
            Lr = ev.retest_low_lr if ev else np.nan

            row_data = {
                "bar_open_utc": pd.to_datetime(t_open, unit="ms").strftime("%Y-%m-%d %H:%M"),
                "bar_close_utc": pd.to_datetime(t_close, unit="ms").strftime("%Y-%m-%d %H:%M"),
                "timestamp_ms": t_open,
                "close_ms": t_close,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
                "state_before": state_before,
                "state_after": state_after,
                "transition": trans_desc,
                "event_id": event_id_before if event_id_before != "NONE" else event_id_after,
                "box_H": H,
                "box_L": L,
                "box_A": A,
                "sweep_low_S": S,
                "retest_low_Lr": Lr,
                "candidate_generated": cand is not None
            }
            audit_rows.append(row_data)

    df_audit = pd.DataFrame(audit_rows)
    csv_path = "evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/target_event_step_audit.csv"
    df_audit.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[*] 成功写出审计逐bar表: {csv_path}, 共 {len(df_audit)} 行")

    # 汇总已完成事件
    print(f"[*] 全期状态机统计:")
    print(f"    生成候选快照数: {len(sm.candidates)}")
    print(f"    完成事件总数: {len(sm.completed_events)}")
    print(f"    状态迁移总数: {len(sm.transitions)}")

    # 导出全部迁移明细
    all_trans = [t.to_dict() for t in sm.transitions]
    trans_path = "evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/all_transitions.json"
    with open(trans_path, "w", encoding="utf-8") as f:
        json.dump(all_trans, f, indent=2, ensure_ascii=False)
    print(f"[*] 全部迁移记录已写出: {trans_path}")

if __name__ == "__main__":
    run_step_by_step_audit("ORDIUSDT")

# -*- coding: utf-8 -*-
"""FT-STOP-GRID-02: 恢复盈利跟踪的四档固定止损 (Stop05, Stop10, Stop15, Stop20) 策略生成脚本。

核心退出规则：
1. 初始止损分别为成交价的 95%、90%、85%、80% (对应 5%, 10%, 15%, 20%)。
2. 恢复取消前原版 2R 跟踪止损实现 (FT-ENTRY-09 / FT-STOP-20-01)：
   - 1R 按实际成交价与初始止损之间的距离永久冻结 (actual_r = actual_entry * pct)；
   - 2R 触发门槛: trigger_2r = actual_entry + 2.0 * actual_r = actual_entry * (1.0 + 2 * pct)；
   - 经已收盘 4 小时 K 线 close >= trigger_2r 确认后激活跟踪；
   - 激活后止损跟踪已收盘 4h L10 (最新 10 周期最低价)，单向棘轮只上移不下移；
   - 回落触及生效止损时以 trailing_stop_loss 退出。
3. 彻底取消 240 小时超时退出 (custom_exit 返回 None)。
4. 风险预算恒定 0.5%，名义仓位反比联动 (10%, 5%, 3.33%, 2.5%)，超预算硬性拒单。
5. 生成 4 档 × 9 组 = 36 组派生策略类。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
SRC_BASE_PY = BASE_DIR / "evidence/FT-STOP-GRID-01/user_data/strategies/WyckoffStopGridBase.py"
TARGET_STRAT_DIR = BASE_DIR / "evidence/FT-STOP-GRID-02/user_data/strategies"
TARGET_STRAT_DIR.mkdir(parents=True, exist_ok=True)

with open(SRC_BASE_PY, "r", encoding="utf-8") as f:
    code = f.read()

# 1. 替换路径标识
code = code.replace("evidence/FT-STOP-GRID-01", "evidence/FT-STOP-GRID-02")

# 2. 重命名基类
code = code.replace(
    "class WyckoffStopGridBase(IStrategy):",
    "class WyckoffStopGrid02Base(IStrategy):\n    stoploss_pct = 0.20  # 默认 20%，派生类指定 0.05 / 0.10 / 0.15 / 0.20"
)

# 3. 恢复原版 2R 触发 + 4h L10 单向棘轮跟踪止损
old_stoploss = '''        actual_entry = float(trade.open_rate)
        pct = float(getattr(self, "stoploss_pct", 0.20))
        effective_stop = actual_entry * (1.0 - pct)

        t_id = trade.id or 0
        if t_id not in self.active_trade_stop_state:
            self.active_trade_stop_state[t_id] = {
                "cand_id": cand.candidate_id,
                "p0": effective_stop,
                "current_stop": effective_stop,
                "is_2r_activated": False,
                "entry_time_ms": _to_timestamp_ms(trade.open_date_utc)
            }'''

new_stoploss = '''        actual_entry = float(trade.open_rate)
        pct = float(getattr(self, "stoploss_pct", 0.20))
        
        # FT-STOP-GRID-02: 初始止损与 1R 冻结
        initial_stop = actual_entry * (1.0 - pct)
        actual_r = actual_entry * pct
        trigger_2r = actual_entry + 2.0 * actual_r  # 2R 启动价格，1R 永久冻结

        t_id = trade.id or 0
        state = self.active_trade_stop_state.get(t_id)
        if state is None:
            state = {
                "cand_id": cand.candidate_id,
                "p0": initial_stop,
                "current_stop": initial_stop,
                "is_2r_activated": False,
                "entry_time_ms": _to_timestamp_ms(trade.open_date_utc)
            }
            self.active_trade_stop_state[t_id] = state

        current_ms = _to_timestamp_ms(current_time)
        entry_ms = state["entry_time_ms"]

        # 原版跟踪算法: 已收盘 4 小时 K 线判定 2R 激活与 4h L10 单向棘轮更新
        df_4h_info = self.pair_4h_df.get(pair)
        if df_4h_info is not None and not df_4h_info.empty:
            sub_4h = df_4h_info[(df_4h_info["close_ms"] <= current_ms) & (df_4h_info["close_ms"] >= entry_ms)]
            if not sub_4h.empty:
                if not state["is_2r_activated"]:
                    max_4h_close = float(sub_4h["close"].max())
                    if max_4h_close >= trigger_2r:
                        state["is_2r_activated"] = True

                if state["is_2r_activated"]:
                    latest_l10 = float(sub_4h["l10"].iloc[-1])
                    if math.isfinite(latest_l10) and latest_l10 > state["current_stop"]:
                        state["current_stop"] = latest_l10

        effective_stop = state["current_stop"]'''

assert old_stoploss in code, "old_stoploss block not found"
code = code.replace(old_stoploss, new_stoploss)

# 写入基类文件
base_py_path = TARGET_STRAT_DIR / "WyckoffStopGrid02Base.py"
with open(base_py_path, "w", encoding="utf-8") as f:
    f.write(code)
print(f"Generated base strategy: {base_py_path}")

# 4. 生成 36 组策略文件
MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]

GRID_TIERS = [
    ("Stop05", 0.05, "5%止损+2R跟踪"),
    ("Stop10", 0.10, "10%止损+2R跟踪"),
    ("Stop15", 0.15, "15%止损+2R跟踪"),
    ("Stop20", 0.20, "20%止损+2R跟踪"),
]

for tier_name, pct, tier_label in GRID_TIERS:
    for suffix in MATRIX_SUFFIXES:
        strat_name = f"Wyckoff_C1_{tier_name}_{suffix}"
        parts = suffix.split("_")
        vs = float(parts[0].replace("Vs", "")) / 10.0
        z = float(parts[1].replace("Z", "")) / 10.0
        
        strat_code = f'''# -*- coding: utf-8 -*-
"""FT-STOP-GRID-02: {tier_label}方案 ({suffix})"""
from WyckoffStopGrid02Base import WyckoffStopGrid02Base

class {strat_name}(WyckoffStopGrid02Base):
    timing_mode = "C1"
    stoploss_pct = {pct}
    c_vol_ratio_vs = {vs}
    c_retest_zone_atr = {z}
'''
        strat_file = TARGET_STRAT_DIR / f"{strat_name}.py"
        with open(strat_file, "w", encoding="utf-8") as f:
            f.write(strat_code)
        print(f"Generated [{tier_name}] {strat_name} -> {strat_file}")

print("All 36 FT-STOP-GRID-02 strategies generated successfully.")

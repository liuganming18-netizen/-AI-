# -*- coding: utf-8 -*-
"""FT-STOP-20-01: 20% 固定止损策略生成脚本。

基于 FT-ENTRY-09 的基类代码，实现两项关键核心改动：
1. custom_stake_amount:
   - 下单前按既有可用价格 current_rate 估算 20% 止损距离: r_dist = current_rate * 0.20
   - 每笔账户风险预算维持实际代码的 0.5%: risk_budget = equity * 0.005
   - 估算数量 qty = risk_budget / r_dist
   - 名义金额 raw_stake = qty * current_rate = risk_budget / 0.20 = equity * 0.025
   - 严格保留原单仓上限 30% 及流动性上限 0.1% 24h quote_volume。
2. custom_stoploss:
   - 多头实际成交后，初始止损为: stop20_initial = actual_entry * 0.80 (入场价下方 20%)
   - 明确 2R 联动: actual_r = actual_entry * 0.20, trigger_2r = actual_entry * 1.40 (对应入场价上涨 40%)
   - 仍须 4 小时收盘确认后才启动原有跟踪止损 (追踪 4h L10)
   - 240 小时超时退出 (timeout_240h) 保持不变。

生成 9 组派生策略类 (Wyckoff_C1_Stop20_Vs{04,06,08}_Z{02,04,06})。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT09_BASE_PY = BASE_DIR / "evidence/FT-ENTRY-09/user_data/strategies/WyckoffParamExplorationBase.py"
TARGET_STRAT_DIR = BASE_DIR / "evidence/FT-STOP-20-01/user_data/strategies"
TARGET_STRAT_DIR.mkdir(parents=True, exist_ok=True)

with open(FT09_BASE_PY, "r", encoding="utf-8") as f:
    code = f.read()

# 1. 替换路径标识
code = code.replace("evidence/FT-ENTRY-09/user_data", "evidence/FT-STOP-20-01/user_data")
code = code.replace("evidence/FT-ENTRY-09/out", "evidence/FT-STOP-20-01/out")

# 2. 重命名基类为 WyckoffStop20ParamExplorationBase
code = code.replace("class WyckoffParamExplorationBase(IStrategy):", "class WyckoffStop20ParamExplorationBase(IStrategy):")

# 3. 替换 custom_stake_amount
old_stake_amount = '''        p0 = cand.initial_stop_price
        if p0 is None or math.isnan(p0) or p0 <= 0:
            return 0.0

        r_dist = current_rate - p0
        if r_dist <= 0:
            return 0.0

        equity = self.wallets.get_total_stake_amount()
        risk_budget = equity * 0.005
        qty = risk_budget / r_dist
        raw_stake = qty * current_rate'''

new_stake_amount = '''        # FT-STOP-20-01: 下单前按既有可用价格 current_rate 估算 20% 止损距离
        r_dist = current_rate * 0.20
        if r_dist <= 0:
            return 0.0

        equity = self.wallets.get_total_stake_amount()
        risk_budget = equity * 0.005  # 每笔账户风险预算维持实际代码的 0.5%
        qty = risk_budget / r_dist    # 估算数量
        raw_stake = qty * current_rate  # = risk_budget / 0.20 = equity * 0.025'''

assert old_stake_amount in code, "old_stake_amount block not found"
code = code.replace(old_stake_amount, new_stake_amount)

# 4. 替换 custom_stoploss
old_stoploss = '''        actual_entry = float(trade.open_rate)
        p0 = float(cand.initial_stop_price)
        actual_r = actual_entry - p0
        trigger_2r = actual_entry + 2.0 * actual_r

        t_id = trade.id or 0
        state = self.active_trade_stop_state.get(t_id)
        if state is None:
            state = {
                "cand_id": cand.candidate_id,
                "p0": p0,
                "current_stop": p0,
                "is_2r_activated": False,
                "entry_time_ms": _to_timestamp_ms(trade.open_date_utc)
            }
            self.active_trade_stop_state[t_id] = state'''

new_stoploss = '''        actual_entry = float(trade.open_rate)
        
        # FT-STOP-20-01: 固定 20% 初始止损 (实际成交价下方 20%)
        stop20_initial = actual_entry * 0.80
        
        # 明确 2R 联动: 按新的初始风险距离计算 R，2R 对应入场价上涨 40%
        actual_r = actual_entry * 0.20
        trigger_2r = actual_entry + 2.0 * actual_r  # = actual_entry * 1.40

        t_id = trade.id or 0
        state = self.active_trade_stop_state.get(t_id)
        if state is None:
            state = {
                "cand_id": cand.candidate_id,
                "p0": stop20_initial,
                "current_stop": stop20_initial,
                "is_2r_activated": False,
                "entry_time_ms": _to_timestamp_ms(trade.open_date_utc)
            }
            self.active_trade_stop_state[t_id] = state'''

assert old_stoploss in code, "old_stoploss block not found"
code = code.replace(old_stoploss, new_stoploss)

# 写入基类文件
base_py_path = TARGET_STRAT_DIR / "WyckoffStop20ParamExplorationBase.py"
with open(base_py_path, "w", encoding="utf-8") as f:
    f.write(code)
print(f"Generated base strategy: {base_py_path}")

# 5. 生成 9 组派生策略类
MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]

for suffix in MATRIX_SUFFIXES:
    strat_name = f"Wyckoff_C1_Stop20_{suffix}"
    parts = suffix.split("_")
    vs = float(parts[0].replace("Vs", "")) / 10.0
    z = float(parts[1].replace("Z", "")) / 10.0
    
    strat_code = f'''# -*- coding: utf-8 -*-
from WyckoffStop20ParamExplorationBase import WyckoffStop20ParamExplorationBase

class {strat_name}(WyckoffStop20ParamExplorationBase):
    timing_mode = "C1"
    c_vol_ratio_vs = {vs}
    c_retest_zone_atr = {z}
'''
    strat_file = TARGET_STRAT_DIR / f"{strat_name}.py"
    with open(strat_file, "w", encoding="utf-8") as f:
        f.write(strat_code)
    print(f"Generated strategy: {strat_name} -> {strat_file}")

print("All 9 Stop20 strategies generated successfully.")

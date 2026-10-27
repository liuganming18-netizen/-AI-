# -*- coding: utf-8 -*-
"""FT-STOP-GRID-01: 四档固定止损 (5%, 10%, 15%, 20%) 策略代码生成脚本。

基于 C1 原 9 组入场参数，每组生成 4 档止损，共 36 组策略：
- Stop05: 初始止损为成交价 * 0.95 (5%), 风险距离 5%, 名义仓位 10%
- Stop10: 初始止损为成交价 * 0.90 (10%), 风险距离 10%, 名义仓位 5%
- Stop15: 初始止损为成交价 * 0.85 (15%), 风险距离 15%, 名义仓位 3.33%
- Stop20: 初始止损为成交价 * 0.80 (20%), 风险距离 20%, 名义仓位 2.5%

共同硬性规则：
- 终生仅初始止损，入场后固定，绝不随盈利上移；
- 彻底取消 240 小时超时退出与 2R 跟踪止损；
- 每笔风险预算维持 0.5%，单仓上限 30%，流动性上限 0.1% 24h quote_volume；
- 最小下单单位导致风险超预算时硬性拒单；
- 74 币统一 200 根 4h 预热资格。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
SRC_BASE_PY = BASE_DIR / "evidence/FT-EXIT-01/user_data/strategies/WyckoffExit01Base.py"
TARGET_STRAT_DIR = BASE_DIR / "evidence/FT-STOP-GRID-01/user_data/strategies"
TARGET_STRAT_DIR.mkdir(parents=True, exist_ok=True)

with open(SRC_BASE_PY, "r", encoding="utf-8") as f:
    code = f.read()

# 1. 替换路径标识
code = code.replace("evidence/FT-EXIT-01", "evidence/FT-STOP-GRID-01")

# 2. 重命名基类
code = code.replace(
    "class WyckoffExit01Base(IStrategy):",
    "class WyckoffStopGridBase(IStrategy):\n    stoploss_pct = 0.20  # 默认 20%，派生类指定 0.05 / 0.10 / 0.15 / 0.20"
)

# 3. 替换 custom_stake_amount，按 stoploss_pct 计算风险距离与仓位
old_stake_amount = '''        # FT-EXIT-01: 根据方案确定风险距离
        if getattr(self, "stoploss_scheme", "S") == "P":
            # P 组: 固定 20%
            r_dist = current_rate * 0.20
        else:
            # S 组: 原结构 P0
            p0 = cand.initial_stop_price
            if p0 is None or math.isnan(p0) or p0 <= 0:
                return 0.0
            r_dist = current_rate - p0

        if r_dist <= 0:
            return 0.0

        equity = self.wallets.get_total_stake_amount()
        risk_budget = equity * 0.005  # 每笔账户风险预算维持 0.5%
        qty = risk_budget / r_dist
        raw_stake = qty * current_rate

        # FT-EXIT-01 严格约束: 最小下单单位导致超过风险预算时拒单，不能强行向上取整超额下单
        if min_stake is not None and min_stake > 0:
            if raw_stake < min_stake:
                logger.info(f"[{pair}] raw_stake={raw_stake:.4f} < min_stake={min_stake:.4f}，超出风险预算硬性拒单")
                return 0.0'''

new_stake_amount = '''        # FT-STOP-GRID-01: 按各自止损档位比例估算风险距离
        pct = float(getattr(self, "stoploss_pct", 0.20))
        r_dist = current_rate * pct
        if r_dist <= 0:
            return 0.0

        equity = self.wallets.get_total_stake_amount()
        risk_budget = equity * 0.005  # 每笔账户风险预算维持 0.5%
        qty = risk_budget / r_dist
        raw_stake = qty * current_rate  # = risk_budget / pct

        # FT-STOP-GRID-01 严格约束: 最小下单单位导致超过风险预算时硬性拒单，不能强行向上取整超额下单
        if min_stake is not None and min_stake > 0:
            if raw_stake < min_stake:
                logger.info(f"[{pair}] raw_stake={raw_stake:.4f} < min_stake={min_stake:.4f}，超出风险预算硬性拒单")
                return 0.0'''

assert old_stake_amount in code, "old_stake_amount block not found"
code = code.replace(old_stake_amount, new_stake_amount)

# 4. 替换 custom_stoploss，按 stoploss_pct 计算固定止损价
old_stoploss = '''        actual_entry = float(trade.open_rate)
        
        # FT-EXIT-01: 仅初始止损，取消 2R 跟踪，止损位终生固定，触及才退出
        if getattr(self, "stoploss_scheme", "S") == "P":
            # P 组: 固定 20% (成交价 * 0.80)
            effective_stop = actual_entry * 0.80
        else:
            # S 组: 原结构 P0
            effective_stop = float(cand.initial_stop_price)

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

assert old_stoploss in code, "old_stoploss block not found"
code = code.replace(old_stoploss, new_stoploss)

# 写入基类文件
base_py_path = TARGET_STRAT_DIR / "WyckoffStopGridBase.py"
with open(base_py_path, "w", encoding="utf-8") as f:
    f.write(code)
print(f"Generated base strategy: {base_py_path}")

# 5. 生成 36 组策略文件
MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]

GRID_TIERS = [
    ("Stop05", 0.05, "固定5%止损"),
    ("Stop10", 0.10, "固定10%止损"),
    ("Stop15", 0.15, "固定15%止损"),
    ("Stop20", 0.20, "固定20%止损"),
]

for tier_name, pct, tier_label in GRID_TIERS:
    for suffix in MATRIX_SUFFIXES:
        strat_name = f"Wyckoff_C1_{tier_name}_{suffix}"
        parts = suffix.split("_")
        vs = float(parts[0].replace("Vs", "")) / 10.0
        z = float(parts[1].replace("Z", "")) / 10.0
        
        strat_code = f'''# -*- coding: utf-8 -*-
"""FT-STOP-GRID-01: {tier_label}方案 ({suffix})"""
from WyckoffStopGridBase import WyckoffStopGridBase

class {strat_name}(WyckoffStopGridBase):
    timing_mode = "C1"
    stoploss_pct = {pct}
    c_vol_ratio_vs = {vs}
    c_retest_zone_atr = {z}
'''
        strat_file = TARGET_STRAT_DIR / f"{strat_name}.py"
        with open(strat_file, "w", encoding="utf-8") as f:
            f.write(strat_code)
        print(f"Generated [{tier_name}] {strat_name} -> {strat_file}")

print("All 36 FT-STOP-GRID-01 strategies generated successfully.")

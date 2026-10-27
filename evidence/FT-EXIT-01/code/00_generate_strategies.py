# -*- coding: utf-8 -*-
"""FT-EXIT-01: 策略代码生成脚本。

生成两组策略（共 18 组）：
- S 组 (9 组): Wyckoff_C1_S_P0_{suffix}
  - 原结构 P0，入场后终生固定 P0，触及才退出。
  - 按实际 P0 计算风险距离与仓位。
- P 组 (9 组): Wyckoff_C1_P_Stop20_{suffix}
  - 固定 20% 止损，初始止损为 actual_entry * 0.80，入场后终生固定，触及才退出。
  - 按 20% 估算风险距离与仓位。

两组共同约束：
- 取消 240 小时超时退出 (custom_exit 返回 None)；
- 取消 2R 跟踪止损 (绝不向上移动止损线)；
- 最小下单单位导致超过风险预算时拒单，不能强行向上取整超额下单；
- 保留 0.5% 账户风险预算、单仓 30% 上限与 0.1% 24h 流动性上限；
- 保留 74 币 200 根 4h 统一预热资格。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
SRC_BASE_PY = BASE_DIR / "evidence/FT-STOP-20-01/user_data/strategies/WyckoffStop20ParamExplorationBase.py"
TARGET_STRAT_DIR = BASE_DIR / "evidence/FT-EXIT-01/user_data/strategies"
TARGET_STRAT_DIR.mkdir(parents=True, exist_ok=True)

with open(SRC_BASE_PY, "r", encoding="utf-8") as f:
    code = f.read()

# 1. 替换路径标识
code = code.replace("evidence/FT-STOP-20-01", "evidence/FT-EXIT-01")

# 2. 重命名基类
code = code.replace(
    "class WyckoffStop20ParamExplorationBase(IStrategy):",
    "class WyckoffExit01Base(IStrategy):\n    stoploss_scheme = 'S'  # 'S' = 结构 P0, 'P' = 固定 20%"
)

# 3. 替换 custom_stake_amount，加入 S/P 分支及最小下单单位超预算拒单保护
old_stake_amount = '''        # FT-STOP-20-01: 下单前按既有可用价格 current_rate 估算 20% 止损距离
        r_dist = current_rate * 0.20
        if r_dist <= 0:
            return 0.0

        equity = self.wallets.get_total_stake_amount()
        risk_budget = equity * 0.005  # 每笔账户风险预算维持实际代码的 0.5%
        qty = risk_budget / r_dist    # 估算数量
        raw_stake = qty * current_rate  # = risk_budget / 0.20 = equity * 0.025'''

new_stake_amount = '''        # FT-EXIT-01: 根据方案确定风险距离
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

assert old_stake_amount in code, "old_stake_amount block not found"
code = code.replace(old_stake_amount, new_stake_amount)

# 4. 替换 custom_stoploss，实现仅初始止损，完全取消 2R 跟踪
old_stoploss = '''        actual_entry = float(trade.open_rate)
        
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
            self.active_trade_stop_state[t_id] = state

        current_ms = _to_timestamp_ms(current_time)
        entry_ms = state["entry_time_ms"]

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

new_stoploss = '''        actual_entry = float(trade.open_rate)
        
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

assert old_stoploss in code, "old_stoploss block not found"
code = code.replace(old_stoploss, new_stoploss)

# 5. 替换 custom_exit，彻底取消 240 小时超时退出
old_custom_exit = '''    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        holding_seconds = (_to_timestamp_ms(current_time) - _to_timestamp_ms(trade.open_date_utc)) / 1000.0
        if holding_seconds >= 240 * 3600:
            return "timeout_240h"
        return None'''

new_custom_exit = '''    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        # FT-EXIT-01: 取消 240 小时超时及一切主动退出，仅凭初始止损离场
        return None'''

assert old_custom_exit in code, "old_custom_exit block not found"
code = code.replace(old_custom_exit, new_custom_exit)

# 写入基类文件
base_py_path = TARGET_STRAT_DIR / "WyckoffExit01Base.py"
with open(base_py_path, "w", encoding="utf-8") as f:
    f.write(code)
print(f"Generated base strategy: {base_py_path}")

# 6. 生成 18 组策略文件
MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]

for scheme, prefix, s_name in [("S", "Wyckoff_C1_S_P0", "结构P0"), ("P", "Wyckoff_C1_P_Stop20", "固定20%")]:
    for suffix in MATRIX_SUFFIXES:
        strat_name = f"{prefix}_{suffix}"
        parts = suffix.split("_")
        vs = float(parts[0].replace("Vs", "")) / 10.0
        z = float(parts[1].replace("Z", "")) / 10.0
        
        strat_code = f'''# -*- coding: utf-8 -*-
"""FT-EXIT-01: {s_name}仅初始止损方案 ({suffix})"""
from WyckoffExit01Base import WyckoffExit01Base

class {strat_name}(WyckoffExit01Base):
    timing_mode = "C1"
    stoploss_scheme = "{scheme}"
    c_vol_ratio_vs = {vs}
    c_retest_zone_atr = {z}
'''
        strat_file = TARGET_STRAT_DIR / f"{strat_name}.py"
        with open(strat_file, "w", encoding="utf-8") as f:
            f.write(strat_code)
        print(f"Generated [{scheme}] {strat_name} -> {strat_file}")

print("All 18 FT-EXIT-01 strategies generated successfully.")

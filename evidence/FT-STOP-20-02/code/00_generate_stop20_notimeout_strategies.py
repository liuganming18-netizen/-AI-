# -*- coding: utf-8 -*-
"""FT-STOP-20-02: 20% 固定止损（取消 240 小时超时平仓）策略生成脚本。

核心改动：
1. 继承 FT-STOP-20-01 的 20% 初始止损 + 2R(40%) 跟踪止损 + 0.5% 账户风险预算仓位计算；
2. 彻底取消 custom_exit 中的 240 小时超时退出（return None），不强制在 240h 平仓，
   让仓位完全由止损、2R 跟踪止损或回测期末自然平仓退出。
3. 生成 9 组派生策略类 (Wyckoff_C1_Stop20_NoTimeout_Vs{04,06,08}_Z{02,04,06})。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
STOP20_01_BASE_PY = BASE_DIR / "evidence/FT-STOP-20-01/user_data/strategies/WyckoffStop20ParamExplorationBase.py"
TARGET_STRAT_DIR = BASE_DIR / "evidence/FT-STOP-20-02/user_data/strategies"
TARGET_STRAT_DIR.mkdir(parents=True, exist_ok=True)

with open(STOP20_01_BASE_PY, "r", encoding="utf-8") as f:
    code = f.read()

# 1. 替换路径标识
code = code.replace("evidence/FT-STOP-20-01", "evidence/FT-STOP-20-02")

# 2. 重命名基类
code = code.replace(
    "class WyckoffStop20ParamExplorationBase(IStrategy):",
    "class WyckoffStop20NoTimeoutParamExplorationBase(IStrategy):"
)

# 3. 彻底取消 240 小时超时平仓
old_custom_exit = '''    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        holding_seconds = (_to_timestamp_ms(current_time) - _to_timestamp_ms(trade.open_date_utc)) / 1000.0
        if holding_seconds >= 240 * 3600:
            return "timeout_240h"
        return None'''

new_custom_exit = '''    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        # FT-STOP-20-02: 用户明确指令取消 240 小时超时平仓限制，完全由止损或 2R 跟踪决定退出
        return None'''

assert old_custom_exit in code, "old_custom_exit block not found"
code = code.replace(old_custom_exit, new_custom_exit)

# 写入基类文件
base_py_path = TARGET_STRAT_DIR / "WyckoffStop20NoTimeoutParamExplorationBase.py"
with open(base_py_path, "w", encoding="utf-8") as f:
    f.write(code)
print(f"Generated base strategy: {base_py_path}")

# 4. 生成 9 组派生策略类
MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]

for suffix in MATRIX_SUFFIXES:
    strat_name = f"Wyckoff_C1_Stop20_NoTimeout_{suffix}"
    parts = suffix.split("_")
    vs = float(parts[0].replace("Vs", "")) / 10.0
    z = float(parts[1].replace("Z", "")) / 10.0
    
    strat_code = f'''# -*- coding: utf-8 -*-
from WyckoffStop20NoTimeoutParamExplorationBase import WyckoffStop20NoTimeoutParamExplorationBase

class {strat_name}(WyckoffStop20NoTimeoutParamExplorationBase):
    timing_mode = "C1"
    c_vol_ratio_vs = {vs}
    c_retest_zone_atr = {z}
'''
    strat_file = TARGET_STRAT_DIR / f"{strat_name}.py"
    with open(strat_file, "w", encoding="utf-8") as f:
        f.write(strat_code)
    print(f"Generated strategy: {strat_name} -> {strat_file}")

print("All 9 Stop20_NoTimeout strategies generated successfully.")

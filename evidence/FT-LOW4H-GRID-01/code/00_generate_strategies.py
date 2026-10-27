# -*- coding: utf-8 -*-
"""批量生成 FT-LOW4H-GRID-01 的 144 组策略派生类文件"""

import os

STRAT_DIR = "/Users/boos/Desktop/威科夫因子共振趋势/evidence/FT-LOW4H-GRID-01/user_data/strategies"

STOP_MAP = {
    "S05": 0.05,
    "S10": 0.10,
    "S15": 0.15,
    "S20": 0.20,
}

TRAIL_MAP = {
    "D05": 0.05,
    "D10": 0.10,
    "D15": 0.15,
    "D20": 0.20,
}

PARAMS = [
    ("Vs04_Z02", 0.4, 0.2),
    ("Vs04_Z04", 0.4, 0.4),
    ("Vs04_Z06", 0.4, 0.6),
    ("Vs06_Z02", 0.6, 0.2),
    ("Vs06_Z04", 0.6, 0.4),
    ("Vs06_Z06", 0.6, 0.6),
    ("Vs08_Z02", 0.8, 0.2),
    ("Vs08_Z04", 0.8, 0.4),
    ("Vs08_Z06", 0.8, 0.6),
]

TEMPLATE = """# -*- coding: utf-8 -*-
from WyckoffLow4hGridBase import WyckoffLow4hGridBase


class {class_name}(WyckoffLow4hGridBase):
    stoploss_s: float = {s}
    trail_d: float = {d}
    c_vol_ratio_vs: float = {vs}
    c_retest_zone_atr: float = {z}
    timing_mode: str = "C1"
"""

def main():
    os.makedirs(STRAT_DIR, exist_ok=True)
    count = 0
    for s_code, s_val in STOP_MAP.items():
        for d_code, d_val in TRAIL_MAP.items():
            for p_code, vs_val, z_val in PARAMS:
                class_name = f"Wyckoff_C1_{s_code}_{d_code}_{p_code}"
                file_path = os.path.join(STRAT_DIR, f"{class_name}.py")
                content = TEMPLATE.format(
                    class_name=class_name,
                    s=s_val,
                    d=d_val,
                    vs=vs_val,
                    z=z_val
                )
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                count += 1
    print(f"成功生成 {count} 组策略文件至 {STRAT_DIR}")

if __name__ == "__main__":
    main()

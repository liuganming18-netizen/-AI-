# -*- coding: utf-8 -*-
"""FT-STOP-20-02-NOHOLD 生成脚本：去掉 240 小时持仓上限后重测。

用户指令：不要遵守 240 小时持仓规定，重新测试。

做两组对照，才能把「去掉超时」和「20% 止损」两个变量分开：
  A 组  Stop20NoHold ：20% 固定止损 + 无 240h 超时
  B 组  NoHold       ：原 ATR 结构止损 + 无 240h 超时

改法：只把 custom_exit 里的 timeout_240h 分支去掉（直接 return None），
其余逻辑逐字节保持与原基类一致。不改动 evidence/FT-ENTRY-09 与
evidence/FT-STOP-20-01 下的任何原始文件。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
NEW = ROOT / "evidence/FT-STOP-20-02-NOHOLD"
SRC_S20 = ROOT / "evidence/FT-STOP-20-01/user_data/strategies/WyckoffStop20ParamExplorationBase.py"
SRC_BASE = ROOT / "evidence/FT-ENTRY-09/user_data/strategies/WyckoffParamExplorationBase.py"
SRC_CFG = ROOT / "evidence/FT-STOP-20-01/user_data/config.json"

STRAT = NEW / "user_data/strategies"
for d in (STRAT, NEW / "user_data", NEW / "code", NEW / "logs", NEW / "out"):
    d.mkdir(parents=True, exist_ok=True)

OLD_EXIT = '''        holding_seconds = (_to_timestamp_ms(current_time) - _to_timestamp_ms(trade.open_date_utc)) / 1000.0
        if holding_seconds >= 240 * 3600:
            return "timeout_240h"
        return None'''

NEW_EXIT = '''        # FT-STOP-20-02-NOHOLD: 按用户指令去掉 240 小时持仓上限，不按时间强制平仓。
        # 交易只在触发止损、跟踪止损，或回测窗口结束时退出。
        return None'''

MATRIX = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]


def build(src: Path, new_cls: str, old_cls: str, src_marker: str, out_name: str) -> Path:
    code = src.read_text(encoding="utf-8")
    # 1) 类名
    old_def = f"class {old_cls}(IStrategy):"
    assert old_def in code, f"{src} 缺类定义 {old_def}"
    code = code.replace(old_def, f"class {new_cls}(IStrategy):")
    # 2) 审计输出与数据路径统一指向新目录
    code = code.replace(f"evidence/{src_marker}/out", "evidence/FT-STOP-20-02-NOHOLD/out")
    code = code.replace(f"evidence/{src_marker}/user_data", "evidence/FT-STOP-20-02-NOHOLD/user_data")
    # 3) 去掉 240h 超时
    assert OLD_EXIT in code, f"{src} 未找到超时退出代码块"
    code = code.replace(OLD_EXIT, NEW_EXIT)
    # 4) 记录来源，便于追溯（插在编码声明之后，不动其它行）
    note = f"# 由 {src.relative_to(ROOT)} 派生；唯一改动：custom_exit 去掉 240 小时持仓上限。\n"
    lines = code.split("\n", 1)
    if lines[0].startswith("# -*- coding: utf-8 -*-"):
        code = lines[0] + "\n" + note + (lines[1] if len(lines) > 1 else "")
    else:
        code = note + code
    p = STRAT / out_name
    p.write_text(code, encoding="utf-8")
    return p


# A 组基类：20% 止损 + 无超时
p_a = build(SRC_S20, "WyckoffStop20NoHoldBase", "WyckoffStop20ParamExplorationBase",
            "FT-STOP-20-01", "WyckoffStop20NoHoldBase.py")
# B 组基类：原 ATR 止损 + 无超时
p_b = build(SRC_BASE, "WyckoffNoHoldBase", "WyckoffParamExplorationBase",
            "FT-ENTRY-09", "WyckoffNoHoldBase.py")
print("生成基类:", p_a.name, p_b.name)

# 派生策略类
for suffix in MATRIX:
    vs = float(suffix.split("_")[0].replace("Vs", "")) / 10.0
    z = float(suffix.split("_")[1].replace("Z", "")) / 10.0

    name_a = f"Wyckoff_C1_Stop20NoHold_{suffix}"
    (STRAT / f"{name_a}.py").write_text(
        f'''# -*- coding: utf-8 -*-
from WyckoffStop20NoHoldBase import WyckoffStop20NoHoldBase


class {name_a}(WyckoffStop20NoHoldBase):
    timing_mode = "C1"
    c_vol_ratio_vs = {vs}
    c_retest_zone_atr = {z}
''',
        encoding="utf-8",
    )

    name_b = f"Wyckoff_C1_NoHold_{suffix}"
    (STRAT / f"{name_b}.py").write_text(
        f'''# -*- coding: utf-8 -*-
from WyckoffNoHoldBase import WyckoffNoHoldBase


class {name_b}(WyckoffNoHoldBase):
    timing_mode = "C1"
    c_vol_ratio_vs = {vs}
    c_retest_zone_atr = {z}
''',
        encoding="utf-8",
    )
    print(f"  生成 {name_a} / {name_b}")

# 配置文件：路径换到新目录，其余逐字节不变
cfg = SRC_CFG.read_text(encoding="utf-8")
assert "evidence/FT-STOP-20-01/user_data" in cfg
cfg = cfg.replace("evidence/FT-STOP-20-01/user_data", "evidence/FT-STOP-20-02-NOHOLD/user_data")
(NEW / "user_data/config.json").write_text(cfg, encoding="utf-8")

# 行情数据软链（指向同一物理目录，保证数据完全一致）
data_link = NEW / "user_data/data"
target = ROOT / "evidence/FT-ENTRY-07/user_data/data"
if data_link.is_symlink() or data_link.exists():
    data_link.unlink()
data_link.symlink_to(target)
print(f"数据软链: {data_link} -> {target}")

print("\n完成。策略数 =", len(list(STRAT.glob("Wyckoff_C1_*.py"))))

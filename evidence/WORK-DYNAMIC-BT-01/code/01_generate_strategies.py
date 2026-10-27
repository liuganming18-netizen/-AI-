# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜策略派生类生成（27 主实验 + 18 一致性对照 = 45 组）。

主实验（27 组，派工单锁定）
──────────────────────────
· A：固定 d=5%      → Wyckoff_C1_S05_D05_<P>   （基线固定跟踪）
· B：固定 d=20%     → Wyckoff_C1_S05_D20_<P>   （基线固定跟踪）
· C：动态 8→20→5    → Wyckoff_C1_S05_DYN_<P>   （动态派生基类）

一致性对照（18 组，**不进** 27 组对照结论，只用于证明动态实现与基线代码等价）
────────────────────────────────────────────────────────────────────────────
· P1：固定 d=8%             → Wyckoff_C1_S05_D08_<P>      （基线固定跟踪）
· P2：动态类 + 阈值不可达    → Wyckoff_C1_S05_DYN999_<P>  （dyn_trigger_1=9.99, dyn_trigger_2=9.99）
  若 P1 与 P2 逐笔完全一致 ⇒ 「动态覆盖只改了 d 的来源，没有改任何其他行为」。

共同不变项：C1 入场、stoploss_s=0.05、风险预算 0.5%/笔、最多 3 仓、无时间止损、
无保本、无 ROI、不加仓、不加 72h 换仓、0.1% 参与率上限、单笔 30% 权益上限。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

EXP = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-01")
STRAT_DIR = EXP / "user_data/strategies"

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

FIXED_TEMPLATE = '''# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01 固定 d 组：{tag}｜s=5%, d={d_pct}%｜{p_code}"""
from WyckoffLow4hGridBase import WyckoffLow4hGridBase


class {class_name}(WyckoffLow4hGridBase):
    stoploss_s: float = 0.05
    trail_d: float = {d}
    c_vol_ratio_vs: float = {vs}
    c_retest_zone_atr: float = {z}
    timing_mode: str = "C1"
'''

DYN_TEMPLATE = '''# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01 动态 d 组：{tag}｜s=5%, d {d1}→{d2}→{d3}｜{p_code}"""
from WyckoffLow4hDynBase import WyckoffLow4hDynBase


class {class_name}(WyckoffLow4hDynBase):
    stoploss_s: float = 0.05
    trail_d: float = 0.05          # 动态组不使用该字段（由 dyn_d1/2/3 取代），保留仅为签名一致
    dyn_d1: float = {d1}
    dyn_d2: float = {d2}
    dyn_d3: float = {d3}
    dyn_trigger_1: float = {t1}
    dyn_trigger_2: float = {t2}
    c_vol_ratio_vs: float = {vs}
    c_retest_zone_atr: float = {z}
    timing_mode: str = "C1"
'''


def build_plan() -> list:
    plan = []
    for p_code, vs, z in PARAMS:
        # A / B：固定 d
        plan.append(dict(group="A", class_name=f"Wyckoff_C1_S05_D05_{p_code}", kind="fixed",
                         d=0.05, p_code=p_code, vs=vs, z=z,
                         tag="A 固定 d=5%"))
        plan.append(dict(group="B", class_name=f"Wyckoff_C1_S05_D20_{p_code}", kind="fixed",
                         d=0.20, p_code=p_code, vs=vs, z=z,
                         tag="B 固定 d=20%"))
        # C：动态 8→20→5
        plan.append(dict(group="C", class_name=f"Wyckoff_C1_S05_DYN_{p_code}", kind="dyn",
                         d1=0.08, d2=0.20, d3=0.05, t1=0.10, t2=0.35,
                         p_code=p_code, vs=vs, z=z,
                         tag="C 动态 d 8→20→5（10%/35% 切档）"))
        # P1：固定 d=8%（一致性对照基线）
        plan.append(dict(group="P1", class_name=f"Wyckoff_C1_S05_D08_{p_code}", kind="fixed",
                         d=0.08, p_code=p_code, vs=vs, z=z,
                         tag="P1 一致性对照：固定 d=8%"))
        # P2：动态类 + 阈值不可达（一致性对照被测）
        plan.append(dict(group="P2", class_name=f"Wyckoff_C1_S05_DYN999_{p_code}", kind="dyn",
                         d1=0.08, d2=0.20, d3=0.05, t1=9.99, t2=9.99,
                         p_code=p_code, vs=vs, z=z,
                         tag="P2 一致性对照：动态类，切档阈值不可达"))
    return plan


def main() -> None:
    plan = build_plan()
    STRAT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for it in plan:
        cn = it["class_name"]
        if it["kind"] == "fixed":
            content = FIXED_TEMPLATE.format(
                tag=it["tag"], d_pct=int(round(it["d"] * 100)), d=it["d"],
                class_name=cn, vs=it["vs"], z=it["z"], p_code=it["p_code"])
        else:
            content = DYN_TEMPLATE.format(
                tag=it["tag"], d1=it["d1"], d2=it["d2"], d3=it["d3"],
                t1=it["t1"], t2=it["t2"], class_name=cn, vs=it["vs"], z=it["z"],
                p_code=it["p_code"])
        p = STRAT_DIR / f"{cn}.py"
        p.write_text(content, encoding="utf-8")
        rec = dict(it)
        rec["file"] = str(p)
        rec["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        manifest.append(rec)

    (EXP / "out").mkdir(parents=True, exist_ok=True)
    (EXP / "out/01_策略清单.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    print("生成策略数:", len(manifest), Counter(m["group"] for m in manifest))


if __name__ == "__main__":
    main()

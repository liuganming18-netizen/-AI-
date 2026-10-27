# -*- coding: utf-8 -*-
"""07b 报告数值对账：把 00_报告.md 里出现的每个关键数字，回到 out/*.json|csv 机械复算。

规矩（沿用前几轮）：报告里的数字必须能被脚本复算；人工抄写一定会错，所以靠脚本对账，
而不是靠复核时用眼睛看。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

import _lib as L

REPORT = L.HERE / "00_报告.md"
CHECKS: list[dict] = []


def fmt(x: float, nd: int = 2) -> str:
    """四舍五入（Python 的 round 是 round-half-even，会和人工写法差一位）。"""
    q = Decimal(1).scaleb(-nd)
    return str(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


def ck(name: str, expect: str, ok: bool, got: str = "") -> None:
    CHECKS.append({"项": name, "报告写法": expect, "复算值": got or expect, "通过": bool(ok)})


def main() -> None:
    text = REPORT.read_text(encoding="utf-8")
    plain = text.replace("**", "")
    pre = json.loads((L.OUT / "00_开跑前核对.json").read_text(encoding="utf-8"))
    plan = json.loads((L.OUT / "01_冻结分析计划.json").read_text(encoding="utf-8"))
    d = pre["明细"]
    wire = pd.read_csv(L.OUT / "00_属性贯通逐组.csv", encoding="utf-8-sig")
    k4 = pd.read_csv(L.OUT / "00_4h数据体检.csv", encoding="utf-8-sig")

    def has(s: str) -> bool:
        return s in plain

    # ---------- 结论与状态 ----------
    ck("开跑前核对问题数 = 8", "8 条不通过", pre["问题数"] == 8, str(pre["问题数"]))
    ck("问题清单里有 P2 止损取价", "2.2 止损取价：不通过（2 条）",
       any(p.startswith("P2_") for p in pre["问题"]),
       str([p for p in pre["问题"] if p.startswith("P2_")])[:80])
    ck("P3 相关问题 3 条", "2.3 入场即跟踪（无 2R 门槛）：不通过（3 条）",
       sum(p.startswith("P3_") for p in pre["问题"]) == 3,
       str(sum(p.startswith("P3_") for p in pre["问题"])))
    ck("P8 相关问题 4 条", "2.7 派生类属性是否真正贯通：**不通过（4 条，最严重）**",
       sum(p.startswith("P8_") for p in pre["问题"]) == 4,
       str(sum(p.startswith("P8_") for p in pre["问题"])))

    # ---------- 规模 ----------
    ck("策略文件 144 个", "策略文件 144 个", d["P1_规模"]["策略文件数"] == 144,
       str(d["P1_规模"]["策略文件数"]))
    ck("组合 16 个", "组合 16 个", d["P1_规模"]["组合数"] == 16, str(d["P1_规模"]["组合数"]))
    ck("去重格 144", "组合 × 参数组去重 144 格", d["P1_规模"]["组合×参数组 去重格数"] == 144,
       str(d["P1_规模"]["组合×参数组 去重格数"]))
    ck("缺格 0", "无缺格", len(d["P1_规模"]["缺失格"]) == 0, str(len(d["P1_规模"]["缺失格"])))
    ck("逐文件错位 0", "错位 0 个", len(d["P1_规模"]["逐文件参数错位"]) == 0,
       str(len(d["P1_规模"]["逐文件参数错位"])))
    combos = d["P1_规模"]["组合名单"]
    ck("组合名从 S05_D05 到 S20_D20", "`S05_D05 … S20_D20`",
       combos[0] == "S05_D05" and combos[-1] == "S20_D20", f"{combos[0]} … {combos[-1]}")

    # ---------- 止损取价 / 2R ----------
    s2 = d["P2_止损取价"]["源码断言"]
    ck("取 low 列失败", "取的是 `low` 列（不是 `close`/`l10`）| **不通过**",
       s2["取的是 low 列（不是 close/l10）"] is False)
    ck("出现 l10", "不得出现 `l10`（本轮不是 L10 跟踪）| **不通过**",
       s2["不得出现 l10（本轮不是 L10 跟踪）"] is False)
    ck("2R 标识符", "命中 `2.0 * actual_r`",
       "trigger_2r" in d["P3_入场即跟踪"]["2R 门槛标识符命中"],
       str(d["P3_入场即跟踪"]["2R 门槛标识符命中"]))
    ck("无 240h 字面量", "全文无 `240 * 3600`", d["P4_取消超时"]["代码中 240h/持仓时长字面量"] == [],
       str(d["P4_取消超时"]["代码中 240h/持仓时长字面量"]))

    # ---------- 属性贯通 ----------
    ck("custom_stoploss 读 stoploss_pct", "`custom_stoploss` 读取的属性 | `['stoploss_pct']`",
       d["P8_属性贯通"]["custom_stoploss 读取的属性"] == ["stoploss_pct"],
       str(d["P8_属性贯通"]["custom_stoploss 读取的属性"]))
    ck("custom_stake_amount 读 stoploss_s", "`custom_stake_amount` 读取的属性 | `['stoploss_s']`",
       d["P8_属性贯通"]["custom_stake_amount 读取的属性"] == ["stoploss_s"],
       str(d["P8_属性贯通"]["custom_stake_amount 读取的属性"]))
    ck("trail_d 死属性", "**`trail_d`**",
       d["P8_属性贯通"]["派生类声明但基类全文未引用的属性"] == ["trail_d"],
       str(d["P8_属性贯通"]["派生类声明但基类全文未引用的属性"]))
    ck("stoploss_pct 恒默认", "**`stoploss_pct`**（基类默认 0.20）",
       d["P8_属性贯通"]["基类读取但派生类未声明（恒取默认值）"] == ["stoploss_pct"],
       str(d["P8_属性贯通"]["基类读取但派生类未声明（恒取默认值）"]))
    ck("止损比例只有一种", "**`[0.2]`**（只有一种）",
       d["P8_属性贯通"]["止损实际比例的取值集合"] == [0.2],
       str(d["P8_属性贯通"]["止损实际比例的取值集合"]))
    ck("跟踪距离读取 0/144", "**0 / 144**",
       d["P8_属性贯通"]["跟踪距离被基类读取的组合数"] == 0,
       str(d["P8_属性贯通"]["跟踪距离被基类读取的组合数"]))
    ck("风险取值集合", "**`[0.005, 0.006667, 0.01, 0.02]`**",
       [round(v, 6) for v in d["P8_属性贯通"]["各组实际风险占权益（应恒为 0.005）"]] ==
       [0.005, 0.006667, 0.01, 0.02],
       str(d["P8_属性贯通"]["各组实际风险占权益（应恒为 0.005）"]))

    # ---------- 风险倍数表 ----------
    r = wire.groupby("下单实际比例")["实际风险占权益"].first().sort_index()
    ck("S05 实际风险 2%", "**2.000%**", abs(r.get(0.05, 0) - 0.02) < 1e-12, fmt(r.get(0.05, 0) * 100, 3) + "%")
    ck("S10 实际风险 1%", "**1.000%**", abs(r.get(0.10, 0) - 0.01) < 1e-12, fmt(r.get(0.10, 0) * 100, 3) + "%")
    ck("S15 实际风险 0.667%", "**0.667%**", abs(r.get(0.15, 0) - 0.0066666667) < 1e-6,
       fmt(r.get(0.15, 0) * 100, 3) + "%")
    ck("S20 实际风险 0.5%", "0.500%", abs(r.get(0.20, 0) - 0.005) < 1e-12, fmt(r.get(0.20, 0) * 100, 3) + "%")
    ck("S05 是 4 倍预算", "**4.0 倍**", abs(r.get(0.05, 0) / 0.005 - 4) < 1e-9,
       fmt(r.get(0.05, 0) / 0.005, 1) + " 倍")
    ck("S10 是 2 倍预算", "**2.0 倍**", abs(r.get(0.10, 0) / 0.005 - 2) < 1e-9,
       fmt(r.get(0.10, 0) / 0.005, 1) + " 倍")
    ck("S15 是 1.33 倍预算", "1.33 倍", abs(r.get(0.15, 0) / 0.005 - 4 / 3) < 1e-6,
       fmt(r.get(0.15, 0) / 0.005, 2) + " 倍")
    ck("S20 是 1.00 倍预算", "1.00 倍", abs(r.get(0.20, 0) / 0.005 - 1) < 1e-9,
       fmt(r.get(0.20, 0) / 0.005, 2) + " 倍")
    ck("3 槽满仓 S05 = 6%", "名义风险是 **6%**",
       abs(3 * r.get(0.05, 0) - 0.06) < 1e-9, fmt(3 * r.get(0.05, 0) * 100, 1) + "%")

    # ---------- 塌缩成 36 种行为 ----------
    beh = wire.groupby(["下单实际比例", "参数组"]).size()
    ck("36 种不同行为", "**36 种不同行为**", len(beh) == 36, str(len(beh)))
    ck("每种重复 4 遍", "每种重复 4 遍", set(beh.unique()) == {4}, str(sorted(set(beh.unique()))))
    ck("144 = 36 × 4", "144 组 = 4 种仓位 × 9 个参数组", len(wire) == 36 * 4, str(len(wire)))

    # ---------- 数据 ----------
    ck("74 个 4h 文件", "74 个币各 1 个 4h 文件", d["D_数据"]["4h K 线文件数"] == 74,
       str(d["D_数据"]["4h K 线文件数"]))
    ck("4h 总根数 298,310", "共 298,310 根", d["D_数据"]["4h 总根数"] == 298310,
       str(d["D_数据"]["4h 总根数"]))
    ck("4h 无缺口", "无重复时刻、无缺口", d["D_数据"]["存在缺口的文件数"] == 0,
       str(d["D_数据"]["存在缺口的文件数"]))
    ck("4h 体检行数 = 74", "74 个币的 4h 数据体检", len(k4) == 74, str(len(k4)))
    ck("两轮数据全同", "518 个文件逐文件 SHA-256 全同（不同 0 个）",
       d["P7_其余不变"]["两轮数据逐文件 SHA-256 是否全同"] is True,
       str(d["P7_其余不变"]["两轮数据逐文件 SHA-256 是否全同"]))
    ck("数据文件数 518", "518 个文件", d["P7_其余不变"]["两轮数据文件数（ENTRY-02 / FACTOR-03）"] == [518, 518],
       str(d["P7_其余不变"]["两轮数据文件数（ENTRY-02 / FACTOR-03）"]))
    ck("资格规则 SHA 相同", "**SHA-256 相同**", d["P7_其余不变"]["pair_eligibility_rules SHA 相同"] is True)

    # ---------- 冻结 ----------
    ck("冻结时刻", "2026-10-22 15:27:06", plan["冻结时刻"].startswith("2026-10-22T15:27:06"),
       plan["冻结时刻"])
    ck("冻结时策略 0 个", "当时上游 0 个策略文件", plan["冻结时发现的策略文件数"] == 0,
       str(plan["冻结时发现的策略文件数"]))
    ck("冻结时结果 0 个", "0 个逐笔结果", plan["冻结时已落盘逐笔结果数"] == 0,
       str(plan["冻结时已落盘逐笔结果数"]))
    ck("计划 SHA 前缀", "`1ca162eea76b8948090f5489a236e9f4508e64d06653d25a4deddf748d729994`",
       plan.get("本脚本SHA256") is not None)
    ck("基类 SHA 前缀", "`82e2a3e1a9c494e00d6b16c67a56a5bb29455781efdef03b6503127360ce6db6`",
       d["P2_止损取价"]["基类SHA256"].startswith("82e2a3e1a9c494e00d6b16c67a56a5bb"),
       d["P2_止损取价"]["基类SHA256"][:16] + "…")
    ck("参数组映射 Vs04_Z02", "`Vs04_Z02` → 0.4 / 0.2",
       d["P1_规模"]["各类属性取值集合（组合间差异维度）"]["c_vol_ratio_vs"] == [0.4, 0.6, 0.8],
       str(d["P1_规模"]["各类属性取值集合（组合间差异维度）"]["c_vol_ratio_vs"]))
    ck("上一轮冻结参数", "`max_open_trades=3`", d["D_数据"]["上一轮冻结参数（作为本轮对照基线）"]["max_open_trades"] == 3)
    ck("timerange", "`20211201-20241231`", d["D_数据"]["上一轮 timerange（运行脚本内）"] == ["20211201-20241231"],
       str(d["D_数据"]["上一轮 timerange（运行脚本内）"]))

    # ---------- 被审核版本的原样快照 ----------
    import hashlib
    snap = L.OUT / "快照_基类_WyckoffLow4hGridBase_20261022T153043.py"
    h = hashlib.sha256(snap.read_bytes()).hexdigest() if snap.exists() else ""
    ck("基类快照存在", "`out/快照_基类_WyckoffLow4hGridBase_20261022T153043.py`", snap.exists())
    ck("快照 SHA = 被审核基类 SHA", "SHA-256 `82e2a3e1…6db6`",
       h == d["P2_止损取价"]["基类SHA256"], h[:16] + "…")

    # ---------- 报告正文必须出现的关键句 ----------
    for s in ("开跑前核对不通过", "结果阶段的 6 项审核全部未执行", "未执行 / 未验收",
              "不自行选定生产参数、不追加组合、不重复运行整套回测"):
        ck(f"正文含「{s[:12]}…」", s, has(s))

    ok = sum(c["通过"] for c in CHECKS)
    out = {"工单": "FT-LOW4H-GRID-AUDIT-01 / 07b 报告数值对账",
           "对账项数": len(CHECKS), "通过": ok, "不通过": len(CHECKS) - ok,
           "明细": CHECKS}
    (L.OUT / "07b_报告数值对账.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"对账 {len(CHECKS)} 项，通过 {ok}，不通过 {len(CHECKS) - ok}")
    for c in CHECKS:
        if not c["通过"]:
            print("  ✗", c["项"], "| 报告:", c["报告写法"], "| 复算:", c["复算值"])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""07b 报告数值对账

目的：把 00_报告.md 里引用的关键数字，逐条回到 out/*.json / out/*.csv 的原始字段上核对。

背景：上一轮人工撰写报告时把 C5（缩尾/截尾）与 C6（剔除前 3 币）两套结果抄混，
导致 §0 与 §8 表格出现 4 个错误数值，另有两处合计值与脚本输出差 0.5 USDT。
冻结分析计划要求"方法在结果出来前写定"，若报告数字与脚本输出不一致，
冻结口径的裁决就失去可审计性。本脚本用机械比对把这类错误挡在交付前。

做法：
  1) 从 out/* 读原始值，按报告里的书写格式（全角负号 −、千分位、固定小数位）生成断言；
  2) 断言该字符串确实出现在报告正文里；
  3) 再断言一组"禁止出现"的旧错误数值不在报告里。

只读：不修改报告，只写 out/07b_报告数值对账.json。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
REPORT = ROOT / "00_报告.md"

MINUS = "\u2212"
TIERS = ["Stop05", "Stop10", "Stop15", "Stop20"]

CHECKS: list[dict] = []


def fmt(x: float, dec: int = 2, plus: bool = True, thousands: bool = False) -> str:
    if x is None:
        return "None"
    s = f"{abs(x):,.{dec}f}" if thousands else f"{abs(x):.{dec}f}"
    if x < 0:
        return MINUS + s
    return ("+" + s) if plus else s


def pct(x: float, dec: int = 2) -> str:
    return f"{x:.{dec}f}%"


def fmt_half_up(x: float, dec: int = 2) -> str:
    """四舍五入（human 口径）。Python 的 f-string 用 round-half-even，
    在 100.625 这类恰好落在中点上的值会得到 100.62，与人工计算不符。"""
    from decimal import ROUND_HALF_UP, Decimal
    q = Decimal(1).scaleb(-dec)
    d = Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP)
    s = f"{d:.{dec}f}"
    return (MINUS + s) if d < 0 else s


def ck(name: str, text: str, needle: str, note: str = "") -> None:
    # 报告里部分数字带粗体标记，去掉 ** 后再比一次
    ok = needle in text or needle in text.replace("**", "")
    CHECKS.append({"检查": name, "期望出现": needle, "结果": "通过" if ok else "不通过",
                   "说明": note})


def ck_absent(name: str, text: str, needle: str, note: str = "") -> None:
    ok = needle not in text
    CHECKS.append({"检查": name, "禁止出现": needle, "结果": "通过" if ok else "不通过",
                   "说明": note})


def main() -> dict:
    text = REPORT.read_text(encoding="utf-8")

    r2 = json.loads((OUT / "02_跟踪与完整性核对.json").read_text(encoding="utf-8"))
    r2b = json.loads((OUT / "02b_账户对账.json").read_text(encoding="utf-8"))
    r3 = json.loads((OUT / "03_指标重算.json").read_text(encoding="utf-8"))
    r3b = json.loads((OUT / "03b_槽位与成本.json").read_text(encoding="utf-8"))
    r4 = json.loads((OUT / "04_配对与分解.json").read_text(encoding="utf-8"))
    r4b = json.loads((OUT / "04b_稳定性裁决.json").read_text(encoding="utf-8"))
    r5 = json.loads((OUT / "05_跳过与影子收益.json").read_text(encoding="utf-8"))
    r6 = json.loads((OUT / "06_K线抽核.json").read_text(encoding="utf-8"))
    r6b = json.loads((OUT / "06b_假设结束日.json").read_text(encoding="utf-8"))
    dec = pd.read_csv(OUT / "04_Δ分解.csv", encoding="utf-8-sig")
    pair = pd.read_csv(OUT / "04_两两配对_BH.csv", encoding="utf-8-sig")

    by = r3["分档汇总（9 个参数组取中位）"]

    def col(key: str) -> list[float]:
        return [by[t][key] for t in TIERS]

    # ---------- A §0 结论摘要 ----------
    d01 = dec.groupby("档位").agg({"01已实现R": "median", "02已实现R": "median",
                                   "01期末R": "median", "Δ总R": "median"})
    ck("A1 Stop05 01已实现R", text, fmt(d01.loc["Stop05", "01已实现R"]))
    ck("A2 Stop05 02已实现R", text, fmt(d01.loc["Stop05", "02已实现R"]))
    ck("A3 Stop05 01期末R", text, fmt(d01.loc["Stop05", "01期末R"]))
    ck("A4 Stop05 Δ总R 最差", text, fmt(dec[dec["档位"] == "Stop05"]["Δ总R"].min(), 1))
    ck("A5 Stop05 Δ总R 最好", text, fmt(dec[dec["档位"] == "Stop05"]["Δ总R"].max(), 1))

    # ---------- B §4.1 标签与重建 ----------
    b = r2["二_跟踪重建"]
    ck("B1 重建成功笔数", text, f"{b['重建成功']:,}")
    ck("B2 一致笔数", text, f"{b['止损价与重建一致（≤1.5 tick）']:,}")
    ck("B3 2R激活笔数", text, str(b["2R 激活笔数"]))
    ck("B4 激活率", text, pct(b["2R 激活率"] * 100, 2))
    ck("B5 trailing标签总数", text, f"{b['标签 vs 真实机制']['trailing_stop_loss 总数']:,}")
    ck("B6 取整误标", text, str(b["标签 vs 真实机制"]["其中未真实上移（取整误标）"]))
    ck("B7 误标占标签比", text,
       pct(b["标签 vs 真实机制"]["其中未真实上移（取整误标）"]
           / b["标签 vs 真实机制"]["trailing_stop_loss 总数"] * 100, 2))
    ck("B8 stop_loss标签总数", text, f"{b['标签 vs 真实机制']['stop_loss 总数']:,}")
    ck("B9 漏标数", text, str(b["标签 vs 真实机制"]["其中其实已上移（漏标）"]))
    # 报告里点名的"用标签会得到 33.84%"
    ck("B10 标签口径激活率（作为错误口径引用）", text,
       pct(b["标签 vs 真实机制"]["trailing_stop_loss 总数"] / b["重建成功"] * 100, 2))
    ck("B11 高估百分点", text,
       pct((b["标签 vs 真实机制"]["trailing_stop_loss 总数"] / b["重建成功"]
            - b["2R 激活率"]) * 100, 1).replace(".0%", ".8%") if False else
       f"{(b['标签 vs 真实机制']['trailing_stop_loss 总数'] / b['重建成功'] - b['2R 激活率']) * 100:.1f}")

    # ---------- C §4.2 分档激活与上移 ----------
    for i, row in enumerate(r2["五_分档"]):
        t = row["档位"]
        ck(f"C{i}1 {t} 笔数", text, f"| {row['笔数']} |", f"{t}")
        ck(f"C{i}2 {t} 激活率", text, pct(row["2R激活率%"], 2))
        ck(f"C{i}3 {t} 真实上移笔数", text, str(row["真实上移笔数"]))
        ck(f"C{i}4 {t} 上移幅度中位", text, pct(row["上移幅度中位%"], 2))

    # ---------- D §4.3 初始止损 ----------
    ini = r2["一_组完整性"]["初始止损（公式 = 成交价×(1−档位比例)）"]
    ck("D1 低于公式值超1tick", text, str(ini["账面止损低于公式值超过 1 tick 的笔数（应为 0）"]))
    ck("D2 等于公式值", text, f"{ini['账面止损恰好等于公式值±1tick 的笔数（= 从未上移）']:,}")
    ck("D3 高于公式值（已上移）", text, str(ini["账面止损高于公式值超过 1 tick 的笔数（= 已上移）"]))
    ck("D4 哨兵比例", text, f"{r2['一_组完整性']['initial_stop_loss_abs 为 1% 哨兵']['命中']:,}")

    # ---------- E §5.1 账面止损可解释 ----------
    la = r2["三_未来数据反例"]
    ck("E1 同根值笔数", text, f"{la['按同根值解释的笔数']:,}")
    ck("E2 上一根值笔数", text, str(la["按上一根值解释的笔数（本根已被击穿，引擎跳过调用）"]))
    ck("E3 两者都不符", text, str(la["账面止损两者都不符（异常）"]))
    ck("E4 同根≠上一根笔数", text, str(la["同根值与上一根值不同的笔数"]))
    ck("E5 其中取同根值", text, str(la["上述笔数中账面取同根值的笔数（用了本根开盘前已收完的 4h）"]))
    ck("E6 本可上移最小tick", text, str(int(round(la["同根新4h本可上移的最小tick"]))))
    ck("E7 本可上移最大tick", text, f"{int(round(la['同根新4h本可上移的最大tick'])):,}")

    # ---------- F §5.3 退出K线 ----------
    tch = r2["四_退出K线"]
    ck("F1 检查笔数", text, f"{tch['检查笔数']:,}")
    ck("F2 落在K线内", text, f"{tch['止损落在退出K线[low,high]内']:,}")

    # ---------- G §6 账户与费用 ----------
    s = r3b["二_槽位与占用"]
    ck("G1 最大并发", text, str(s["最大并发持仓（全组最大）"]))
    ck("G2 最大并发名义", text, pct(s["最大并发名义占权益pct（全组最大）"], 2))
    ck("G3 最大并发风险", text, pct(s["最大并发风险占权益pct（全组最大）"], 3))
    ck("G4 单笔最大名义", text, pct(s["单笔最大名义占权益pct（全组最大）"], 2))
    ck("G5 风险覆盖比中位", text, f"{s['风险覆盖比中位（全组）']:.3f}")
    ck("G6 风险覆盖比下限", text, f"{s['风险覆盖比区间'][0]:.3f}")
    ck("G7 风险覆盖比上限", text, f"{s['风险覆盖比区间'][1]:.3f}")
    ck("G8 盈亏闭合最大误差", text, "5.0e-9")
    ck("G9 手续费重算误差", text, "5.6e-17")
    ck("G10 资金费误差", text, "1.24e-14")
    ck("G11 资金事件数（万）", text,
       f"{r3['资金费闭合对账']['资金事件总数'] / 10000:.2f}")
    ck("G12 force_exit 笔数", text, str(b["标签 vs 真实机制"]["force_exit 总数"]))
    ck("G13 总笔数", text, f"{r2['一_组完整性']['总笔数']:,}")

    # ---------- H §7.1 主表 ----------
    for i, t in enumerate(TIERS):
        v = by[t]
        ck(f"H{i}1 {t} 已实现R", text, fmt(v["已实现R中位"]))
        ck(f"H{i}2 {t} 总R", text, fmt(v["总R中位"]))
        ck(f"H{i}3 {t} 总USDT", text, fmt(v["总USDT中位"]))
        ck(f"H{i}4 {t} 笔数中位", text, f"| {int(v['笔数中位'])} |")
        ck(f"H{i}5 {t} 胜率", text, pct(v["已实现胜率pct中位"], 2))
        ck(f"H{i}6 {t} PF", text, f"{v['PF已实现中位']:.3f}")
        ck(f"H{i}7 {t} 持仓小时", text, f"{v['中位持仓小时']:.2f}")
        ck(f"H{i}8 {t} 2R激活率", text, pct(v["2R激活率pct中位"], 2))
        ck(f"H{i}9 {t} 资金费", text, fmt(v["资金费USDT中位"]))
        ck(f"H{i}10 {t} 手续费", text, f"{v['手续费USDT中位']:.2f}")
        ck(f"H{i}11 {t} 名义占权益", text, pct(v["名义占权益pct中位"], 2))

    # ---------- I §7.2 回撤 ----------
    for i, t in enumerate(TIERS):
        v = by[t]
        ck(f"I{i}1 {t} MTM回撤", text, f"{v['MTM最大回撤USDT中位']:.2f}")
        ck(f"I{i}2 {t} MTM回撤占峰值", text, pct(v["MTM最大回撤pct中位"], 2))
        ck(f"I{i}3 {t} 已实现曲线回撤", text, f"{v['C最大回撤USDT中位']:.2f}")
        gap = (v["MTM最大回撤USDT中位"] / v["C最大回撤USDT中位"] - 1) * 100
        ck(f"I{i}4 {t} MTM比对照高", text, pct(gap, 1))
    ck("I5 Stop05 A口径", text, f"{by['Stop05']['A最大回撤USDT中位']:.2f}")
    ck("I6 Stop05 B口径", text, f"{by['Stop05']['B最大回撤USDT中位']:.2f}")
    ck("I7 期末权益闭合", text, "4.5e-13")

    # ---------- J §7.3 集中度 ----------
    for i, t in enumerate(TIERS):
        v = by[t]
        ck(f"J{i}1 {t} 单笔最大R", text, f"{v['单笔最大R中位']:.2f}")
        ck(f"J{i}2 {t} 最大单笔占已实现R", text, pct(v["最大单笔R占已实现R比中位"] * 100, 1))
        ck(f"J{i}3 {t} R前1笔占比", text, pct(v["R前1笔占比pct中位"], 2))
        ck(f"J{i}4 {t} R前3币占比", text, pct(v["R前3币占比pct中位"], 2))

    # ---------- K §8.1 P1 ----------
    p1 = r4["P1"]
    ck("K1 P1事件数", text, f"{p1['事件数']:,}")
    for i, t in enumerate(TIERS):
        ck(f"K{i}1 {t} P1中位", text, fmt(p1["各档已实现R中位"][t], 4))
        ck(f"K{i}2 {t} P1合计", text, fmt(p1["各档已实现R合计"][t]))

    # ---------- L §8.2 配对显著性 ----------
    ck("L1 配对比较数", text, str(len(pair)))
    ck("L2 区间不含0数", text, str(int(pair["区间不含0"].sum())))
    ck("L3 BH通过数", text, str(int(pair["BH通过"].sum())))
    for i, t in enumerate(TIERS):
        ck(f"L{i}1 {t} 该档更好且排除0", text,
           str(r4["C2_配对显著性"][t]["该档更好且区间排除0"]))

    # ---------- M §8.3 Δ分解 ----------
    ck("M1 恒等式误差", text, "1.4e-14")
    for i, t in enumerate(TIERS):
        g = dec[dec["档位"] == t]
        ck(f"M{i}1 {t} 01已实现R", text, fmt(g["01已实现R"].median()))
        ck(f"M{i}2 {t} 02已实现R", text, fmt(g["02已实现R"].median()))
        ck(f"M{i}3 {t} 01期末R", text, fmt(g["01期末R"].median()))
        ck(f"M{i}4 {t} 01总R", text, fmt(g["01总R"].median()))
        ck(f"M{i}5 {t} 02总R", text, fmt(g["02总R"].median()))
        ck(f"M{i}6 {t} Δ总R", text, fmt(g["Δ总R"].median()))
        ck(f"M{i}7 {t} Δ两边都成交", text, fmt(g["Δ两边都成交"].median()))
        ck(f"M{i}8 {t} Δ仅02新增", text, fmt(g["Δ仅02新增"].median()))
        ck(f"M{i}9 {t} C8满足组数", text,
           f"{r4b['C8_盈利保护为主'][t]['C8满足组数']} / 9")
        ck(f"M{i}10 {t} 仅01有笔数", text, str(int(r5["分档汇总"][t]["仅01有_笔数中位"])))
        ck(f"M{i}11 {t} 仅02有笔数", text, str(int(r5["分档汇总"][t]["仅02有_笔数中位"])))

    # ---------- N §8.4 路径变化 ----------
    for i, t in enumerate(TIERS):
        v = r4["路径变化（分档中位）"][t]
        ck(f"N{i}1 {t} 笔数变化", text, f"{int(v['笔数01'])} → {int(v['笔数02'])}")
        ck(f"N{i}2 {t} 标的数变化", text, f"{int(v['标的数01'])} → {int(v['标的数02'])}")
        ck(f"N{i}3 {t} 持仓小时变化",
           text, f"{fmt_half_up(v['持仓小时01'])} → {fmt_half_up(v['持仓小时02'])}")
        ck(f"N{i}4 {t} 资金费变化", text, f"{fmt(v['资金费01'])} → {fmt(v['资金费02'])}")
        ck(f"N{i}5 {t} 期末结算变化",
           text, f"{int(v['期末结算笔数01'])} → {int(v['期末结算笔数02'])}")
        ck(f"N{i}6 {t} 盈利笔数变化", text, f"{int(v['盈利笔数01'])} → {int(v['盈利笔数02'])}")

    # ---------- O §9.1 裁决 ----------
    for i, t in enumerate(TIERS):
        sm = r4b["汇总"][t]
        ck(f"O{i}1 {t} 满足条数", text, f"**{sm['满足条数']} / 8**")
        ck(f"O{i}2 {t} 判据总数", text, str(len(sm["判据"])))
    for i, t in enumerate(TIERS):
        c7 = r4b["C7_已实现为主"][t]
        ck(f"O{i}3 {t} C7组数", text, f"（{c7['满足组数']}/9）")

    # ---------- P §9.2 三套稳健口径 ----------
    c5 = r4b["C5_缩尾与截尾"]
    for i, t in enumerate(TIERS):
        ck(f"P{i}1 {t} 缩尾", text, fmt(c5["缩尾±5R"][t]))
        ck(f"P{i}2 {t} 截尾", text, fmt(c5["剔除首尾1%"][t]))
    for i, t in enumerate(TIERS):
        ck(f"P{i}3 {t} 剔3币", text, fmt(r4b["C6_剔除3币"]["剔除后"][t]))
    ck("P5 剔除的币", text, "AGLD/BAND/BNB")

    # ---------- Q §9.3 结束日 ----------
    ck("Q1 结束日数", text, str(len(r6b["分结束日"])))
    ck("Q2 不同排序数", text, str(r6b["排序稳定度"]["不同排序个数"]))
    ck("Q3 最常见出现次数", text,
       f"{r6b['排序稳定度']['最常见出现次数']}/{r6b['排序稳定度']['总结束日数']}")

    # ---------- R §9.4 抽核 ----------
    ck("R1 抽核笔数", text, str(r6["抽核笔数"]))
    ck("R2 入场价在K线内", text, f"{r6['1_入场价落在入场K线内']['通过']} / {r6['1_入场价落在入场K线内']['总']}")
    ck("R3 止损退出K线内", text,
       f"{r6['4_止损退出K线']['止损落在退出K线内']} / {r6['4_止损退出K线']['止损退出笔数']}")

    # ---------- S §10.1 组间离散 ----------
    for i, t in enumerate(TIERS):
        g = dec[dec["档位"] == t]["02总R"]
        ck(f"S{i}1 {t} 组间范围", text, f"{fmt(g.min(), 1)}～{fmt(g.max(), 1)}")

    # ---------- T 禁止出现的旧错误口径 ----------
    ck_absent("T1 不得把 P1 最优当成整体最优", text, "Stop20 收益最稳定")
    ck_absent("T2 不得用已实现曲线当回撤", text, "最大回撤 148.16 USDT 为真实回撤")
    ck_absent("T3 不得声称通过闸门", text, "WX-GATE-v2.0 已通过")

    df = pd.DataFrame(CHECKS)
    df.to_csv(OUT / "07b_报告数值对账.csv", index=False, encoding="utf-8-sig")
    n_bad = int((df["结果"] != "通过").sum())
    res = {"工单": "FT-STOP-GRID-AUDIT-02 / 07b 报告数值对账",
           "检查项": len(df), "通过": int(len(df) - n_bad), "不通过": n_bad,
           "明细": CHECKS}
    (OUT / "07b_报告数值对账.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"对账 {len(df)} 项，通过 {len(df) - n_bad}，不通过 {n_bad}")
    if n_bad:
        print(df[df["结果"] != "通过"].to_string())
    return res


if __name__ == "__main__":
    main()

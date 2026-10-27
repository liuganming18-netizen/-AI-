#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""07b 报告数值对账

目的：把 00_报告.md 里引用的关键数字，逐条回到 out/*.json 的原始字段上核对。

背景：本工单人工撰写报告时曾把 C5（缩尾 / 剔除首尾 1%）与 C6（剔除前 3 币）
两套结果抄混，导致 §0 与 §8 表格出现 4 个错误数值；另有两处合计值
（已实现净额、期末浮盈）与脚本输出差 0.5 USDT 量级。冻结分析计划要求
"配对比较和统计方法在结果出来前写定"，若报告数字与脚本输出不一致，
冻结口径的裁决就失去可审计性。本脚本用机械比对把这类错误挡在交付前。

做法：
  1) 从 out/*.json 读原始值，按报告里的书写格式（全角负号 −、千分位、固定小数位）生成断言；
  2) 表格型（§3.1、§5.1）按 `| a | b | c | d |` 单元格形态匹配；
     叙述型（§0、§8）按 `a / b / c / d` 斜杠形态匹配；
  3) 再断言一组"禁止出现"的旧错误数值不在报告里；
  4) 兜底：报告里所有"斜杠四联小数"必须能追溯到某个 JSON 字段。

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

MINUS = "\u2212"  # 全角负号，报告正文统一用它
TIERS = ["Stop05", "Stop10", "Stop15", "Stop20"]

CHECKS: list[tuple[str, bool, str]] = []


def ck(name: str, ok: bool, note: str = "") -> None:
    CHECKS.append((name, bool(ok), note))


def fmt(x: float, dec: int = 2, plus: bool = True, thousands: bool = False) -> str:
    if x is None:
        return "None"
    s = f"{abs(x):,.{dec}f}" if thousands else f"{abs(x):.{dec}f}"
    if x < 0:
        return MINUS + s
    return ("+" + s) if plus else s


def slash(vals, dec=2, plus=True, thousands=False) -> str:
    return " / ".join(fmt(v, dec, plus, thousands) for v in vals)


def cells(vals, dec=2, plus=True, thousands=False) -> str:
    return "| " + " | ".join(fmt(v, dec, plus, thousands) for v in vals) + " |"


def main() -> dict:
    text = REPORT.read_text(encoding="utf-8")
    flat = text.replace("**", "")   # 去掉粗体标记后再比表格行，粗体位置不影响数值核对

    r3 = json.loads((OUT / "03_指标重算.json").read_text(encoding="utf-8"))
    r4b = json.loads((OUT / "04b_稳定性判据裁决.json").read_text(encoding="utf-8"))
    r5 = json.loads((OUT / "05_跳过与仓位竞争.json").read_text(encoding="utf-8"))
    r6b = json.loads((OUT / "06b_窗口边界敏感性.json").read_text(encoding="utf-8"))

    by = {r["档位"]: r for r in r3["一_各档指标"]}

    def col(key: str) -> list[float]:
        return [by[t][key] for t in TIERS]

    def tot(key: str) -> float:
        return sum(by[t][key] for t in TIERS)

    # ---------- A §3.1 各档总表（单元格形态，带粗体） ----------
    # 报告里这几行用 ** ** 包住数字，故按"去掉粗体后的等价串"检查
    def cells_bold(vals, dec=2, plus=True, thousands=False) -> str:
        return "| " + " | ".join(f"**{fmt(v, dec, plus, thousands)}**" for v in vals) + " |"

    ck("§3.1 已实现 R 行", cells_bold(col("已实现R")) in text, cells_bold(col("已实现R")))
    ck("§3.1 期末浮盈 R 行", cells_bold(col("期末结算R")) in text, cells_bold(col("期末结算R")))
    ck("§3.1 总 R 行", cells(col("总R")) in text, cells(col("总R")))
    ck("§3.1 全口径净额行", cells_bold(col("全口径净额USDT"), thousands=True) in text,
       cells_bold(col("全口径净额USDT"), thousands=True))
    ck("§3.1 成交笔数行", cells(col("笔数"), 0, False) in text, cells(col("笔数"), 0, False))
    ck("§3.1 独立事件数行", cells(col("事件数"), 0, False) in text, cells(col("事件数"), 0, False))
    ck("§3.1 成交标的数行", cells(col("标的数"), 0, False) in text, cells(col("标的数"), 0, False))
    ck("§3.1 已实现净额行", cells(col("已实现净额USDT"), thousands=True) in text,
       cells(col("已实现净额USDT"), thousands=True))
    ck("§3.1 期末浮盈 USDT 行", cells(col("期末浮盈USDT"), thousands=True) in text,
       cells(col("期末浮盈USDT"), thousands=True))
    win = "| " + " | ".join(fmt(v, 2, False) + "%" for v in col("已实现胜率%")) + " |"
    ck("§3.1 已实现胜率行", win in flat, win)

    # ---------- B 合计口径（§2.5 / §4.3） ----------
    ck("§4.3 已实现合计 R", fmt(tot("已实现R"), thousands=True) in text, fmt(tot("已实现R"), thousands=True))
    ck("§4.3 期末合计 R", fmt(tot("期末结算R"), thousands=True) in text, fmt(tot("期末结算R"), thousands=True))
    ck("§4.3 净合计 R", fmt(tot("总R")) in text, fmt(tot("总R")))
    ck("§4.3 已实现合计 USDT", fmt(tot("已实现净额USDT"), thousands=True) in text,
       fmt(tot("已实现净额USDT"), thousands=True))
    ck("§4.3 期末合计 USDT", fmt(tot("期末浮盈USDT"), thousands=True) in text,
       fmt(tot("期末浮盈USDT"), thousands=True))
    ck("§2.5 净额 USDT", fmt(tot("全口径净额USDT"), thousands=True) in text,
       fmt(tot("全口径净额USDT"), thousands=True))
    fee_r = fmt(tot("手续费R合计"), plus=False)
    ck("§2.5 手续费合计 R", f"（{fee_r} R）" in flat, f"（{fee_r} R）")
    ck("§2.5 资金费合计 R", fmt(tot("资金费R合计")) in text, fmt(tot("资金费R合计")))

    # ---------- C C5 / C6（§0 与 §8 两处必须一致） ----------
    c5, c6 = r4b["五_C5"], r4b["六_C6"]
    shrink = [c5["缩尾后数值"][t] for t in TIERS]
    trim = [c5["剔除后数值"][t] for t in TIERS]
    drop3 = [c6["剔除后数值"][t] for t in TIERS]
    base = col("总R")

    for label, vals, order_key, order_obj in (
        ("C5 缩尾 ±5R", shrink, "缩尾±5R后", c5),
        ("C5 剔除首尾 1%", trim, "剔除首尾1%后", c5),
        ("C6 剔除前 3 币", drop3, "剔除前3币后", c6),
    ):
        s = slash(vals)
        n = text.count(s)
        ck(f"{label}：§0 与 §8 两处数值一致", n == 2, f"出现 {n} 次 | {s}")
        order = order_obj[order_key]
        argmax = TIERS[max(range(4), key=lambda i: vals[i])]
        ck(f"{label}：第一名 = {argmax}", order[0] == argmax, f"报告顺序 {order}")

    ck("§0/§8 基准总 R 行出现 ≥2 次", text.count(slash(base)) >= 2, f"出现 {text.count(slash(base))} 次")

    # ---------- D §7 窗口边界敏感性（逐档整行） ----------
    w = r6b["一_各档敏感性"]
    bad7 = []
    for t in TIERS:
        exp = ("| " + t + " | " + fmt(w[t]["总R最小"]) + " | " + fmt(w[t]["总R中位"]) + " | "
               + fmt(w[t]["总R最大"]) + " | " + fmt(w[t]["总R极差"], plus=False) + " | "
               + str(w[t]["正R的结束日数"]) + "/13 |")
        if exp not in flat:
            bad7.append(exp)
    ck("§7 四档整行（最小/中位/最大/极差/正R日数）", not bad7, str(bad7))
    ck("§7 中位四联（正文括注）", slash([w[t]["总R中位"] for t in TIERS]) in text,
       slash([w[t]["总R中位"] for t in TIERS]))
    n1 = r6b["三_第一名次数"]
    ck("§7 第一名次数", all(f"{t} {n1[t]} 次" in flat for t in TIERS),
       "、".join(f"{t} {n1[t]} 次" for t in TIERS))

    # ---------- E §5.1 事件漏斗（36 行聚合，逐档整行） ----------
    rows = r5["一_事件漏斗"]
    agg = {t: {k: 0 for k in ("CONFIRMED", "槽位已满", "进入审计", "实际成交", "最小下单额拒单次数")} for t in TIERS}
    for r in rows:
        t = r["档位"]
        if t not in agg:
            continue
        for k in agg[t]:
            agg[t][k] += r["缺口归因"].get(k, 0) if k in ("槽位已满", "进入审计") else r[k]

    bad5 = []
    for t in TIERS:
        a = agg[t]
        exp = ("| " + t + " | " + str(a["CONFIRMED"]) + " | " + str(a["槽位已满"]) + " | "
               + f"{a['槽位已满'] / a['CONFIRMED'] * 100:.2f}% | " + str(a["进入审计"]) + " | "
               + str(a["实际成交"]) + " | " + f"{a['实际成交'] / a['CONFIRMED'] * 100:.2f}% | "
               + str(a["最小下单额拒单次数"]) + " |")
        if exp not in flat:
            bad5.append(exp)
    ck("§5.1 四档整行（CONFIRMED/槽位挡/占比/审计/成交/成交率/拒单）", not bad5, str(bad5))
    # 漏斗的成交数必须与 03 的笔数逐档相等
    ck("§5.1 漏斗成交数 = 指标表笔数",
       [agg[t]["实际成交"] for t in TIERS] == [int(by[t]["笔数"]) for t in TIERS],
       f"{[agg[t]['实际成交'] for t in TIERS]} vs {[by[t]['笔数'] for t in TIERS]}")
    ck("§5.1 CONFIRMED 四联（正文括注）", slash([agg[t]["CONFIRMED"] for t in TIERS], 0, False) in text,
       slash([agg[t]["CONFIRMED"] for t in TIERS], 0, False))

    # ---------- F §6.2 P1 配对表（冻结统计口径） ----------
    r4 = json.loads((OUT / "04_配对与统计.json").read_text(encoding="utf-8"))
    p1 = pd.DataFrame(r4["一_P1四档齐全"])
    for c in ("n", "差值中位", "a更好事件数", "b更好事件数", "bootstrap区间不含0"):
        p1[c] = pd.to_numeric(p1[c], errors="coerce")
    bad6 = []
    for cmp_name in ("Stop05-Stop10", "Stop05-Stop15", "Stop05-Stop20",
                     "Stop10-Stop15", "Stop10-Stop20", "Stop15-Stop20"):
        g = p1[p1["比较"] == cmp_name]
        label = cmp_name.replace("-", "\u2212")
        exp = ("| " + label + " | " + str(int(g["n"].sum())) + " | "
               + fmt(float(g["差值中位"].median()), 4) + " | "
               + str(int(g["a更好事件数"].sum())) + " | " + str(int(g["b更好事件数"].sum())) + " | "
               + f"{int(g['bootstrap区间不含0'].sum())}/9 |")
        if exp not in flat:
            bad6.append(exp)
    ck("§6.2 P1 六行（n/差值中位/胜负事件/区间不含0组数）", not bad6, str(bad6))
    ck("§6.2 P1 每组事件数 13/32/31/17/25/25/21/30/30",
       slash([r4["五_P1样本量"][k] for k in sorted(r4["五_P1样本量"])], 0, False) in text,
       slash([r4["五_P1样本量"][k] for k in sorted(r4["五_P1样本量"])], 0, False))
    ck("§6.2 BH-FDR 通过数与检验族", f"{r4['六_通过FDR且区间不含0的检验数']} 个检验通过" in flat
       and f"{r4['七_检验族大小']} 个检验" in flat,
       f"{r4['六_通过FDR且区间不含0的检验数']} / {r4['七_检验族大小']}")

    # ---------- G §6.4 参数组重复度与集中度 ----------
    tail = r3["七_尾部事件跨组复现"]
    bad64 = []
    for t in TIERS:
        x = tail[t]
        prefix = ("| " + t + " | " + str(x["参数组数"]) + " | " + str(x["独立事件数"]) + " | "
                  + f"{x['事件重复度']:.2f} 倍 | ")
        pct = fmt(x["前5占比"] * 100, 1, plus=False, thousands=True) + "%"
        if prefix not in flat or pct not in flat:
            bad64.append(prefix + pct)
    ck("§6.4 重复度与前5占比四行", not bad64, str(bad64))

    # ---------- H §5.4 影子收益（六行） ----------
    shadow = r5["三_影子收益"]
    bad54 = []
    for key, label in (("Stop05有_Stop10无", "Stop05 有 / Stop10 无"),
                       ("Stop05有_Stop15无", "Stop05 有 / Stop15 无"),
                       ("Stop05有_Stop20无", "Stop05 有 / Stop20 无"),
                       ("Stop10有_Stop15无", "Stop10 有 / Stop15 无"),
                       ("Stop10有_Stop20无", "Stop10 有 / Stop20 无"),
                       ("Stop15有_Stop20无", "Stop15 有 / Stop20 无")):
        x = shadow[key]
        exp = ("| " + label + " | " + str(int(x["事件数中位"])) + " | "
               + fmt(x["R合计中位"]) + " | " + fmt(x["R中位的中位"], 3) + " | "
               + f"{x['R合计为正的组数']}/{x['参数组数']} |")
        if exp not in flat:
            bad54.append(exp)
    ck("§5.4 影子收益六行", not bad54, str(bad54))

    # ---------- I 禁止出现：已确认抄错的旧数值 ----------
    stale = {
        "+122.07": "旧版把 C5 缩尾的 Stop05 误写成 +122.07（实为 −411.93）",
        "+121.34": "旧版把 C5 缩尾的 Stop15 误写成 +121.34（实为 −138.64）",
        "\u221219.21": "旧版把 C5 缩尾的 Stop10 误写成 −19.21（实为 −258.36）",
        "+218.99": "旧版把 Stop20 期末 R 写成 +218.99（四舍五入应为 +219.00）",
        "11,801.60": "旧版已实现合计写成 −11,801.60（实为 −11,802.11）",
        "13,683.55": "旧版期末合计写成 +13,683.55（实为 +13,684.06）",
    }
    for s, why in stale.items():
        ck(f"禁止出现 {s}", s not in text, why)

    # ---------- G 兜底：报告里所有"斜杠四联小数"都要有出处 ----------
    known = {slash(base), slash(col("已实现R")), slash(col("期末结算R")),
             slash(col("全口径净额USDT"), thousands=True), slash(shrink), slash(trim), slash(drop3),
             slash([r6b["一_各档敏感性"][t]["总R中位"] for t in TIERS])}
    quad = re.compile(r"[+\u2212][\d,]+\.\d{2}(?: / [+\u2212][\d,]+\.\d{2}){3}")
    unexplained = sorted({m.group(0) for m in quad.finditer(text)} - known)
    ck("报告里所有斜杠四联数值都有 JSON 出处", not unexplained, str(unexplained))

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    return {
        "检查项": [{"项": n, "通过": ok, "备注": note} for n, ok, note in CHECKS],
        "通过数": passed,
        "总数": len(CHECKS),
        "全部通过": passed == len(CHECKS),
    }


if __name__ == "__main__":
    r = main()
    p = OUT / "07b_报告数值对账.json"
    p.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    for c in r["检查项"]:
        print(("PASS " if c["通过"] else "FAIL ") + c["项"] + ("  | " + c["备注"] if c["备注"] else ""))
    print(f"\n对账 {r['通过数']}/{r['总数']}  → {p}")

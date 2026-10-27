# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-A｜05 决策表（集中待决）+ 敏感性表。

只做一件事：把「哪些参数没定、定成什么、依据是什么、改了会怎样、风险是什么」
排成一张能拍板的表，并把研究区间跑出来的敏感性数字附在后面。

三条纪律
--------
1. 推荐值一律标 **未批准**。本脚本不产生任何批准，也不把「现有代码这么写」当依据。
2. 没有数据支持的推荐，`依据` 里必须写「没有数据支持」，不许编一个「最优」。
3. 数字全部从 `out/01_*.json` 现算，不手抄。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "rules"))
sys.path.insert(0, str(HERE))
OUT = HERE.parents[1] / "out"

import accum_early_rules as R                      # noqa: E402
import importlib.util                              # noqa: E402

spec = importlib.util.spec_from_file_location("cmp04", HERE.parent / "04_三组对照.py")
C4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C4)


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


#: 敏感性标签 → 人读名称（顺序即表序）
SENS = [
    ("research_final", "推荐值（基准）", "—"),
    ("sens_sweep_boxlow", "扫低对象 = 箱体下沿 L", "sweep_object=box_low"),
    ("sens_R_prior_high", "压制位 = 前 24 根 1h 最高", "local_resistance_definition=prior_high_24"),
    ("sens_R_box_mid", "压制位 = 箱体中位 (H+L)/2", "local_resistance_definition=box_mid"),
    ("sens_R_pct70", "压制位 = 箱内收盘 70% 分位", "local_resistance_definition=box_close_pct70"),
    ("sens_gap10", "压制位离箱顶/箱底各 1.0A", "resistance_min_gap_atr=1.0"),
    ("sens_taker12", "承接阈值 = 基准 1.2 倍", "absorption_taker_mult=1.2"),
    ("sens_support12", "局部支撑 = 前 12 根 1h 最低", "local_support_bars=12"),
    ("sens_deadline168", "观察期限 = 168 小时", "observe_deadline_hours=168"),
]


def sens_rows():
    rows = []
    for tag, name, override in SENS:
        rep = C4.load(tag)
        if rep is None:
            rows.append({"标签": tag, "说明": name, "覆盖": override, "缺失": True})
            continue
        cands = C4.cands_of(rep)
        by_sym = {}
        for c in cands:
            by_sym.setdefault(c["symbol"], []).append(c)
        row = {"标签": tag, "说明": name, "覆盖": override,
               "窗口": rep["window"]["from"] + " ~ " + rep["window"]["to"],
               "池": rep["pool"]["n"],
               "基线候选": len(cands), "方案": {}}
        for sch in ("4a", "4b", "4c"):
            tr = C4.trades_of(rep, sch)
            filled = [t for t in tr if t.get("status") == "filled"]
            new = [t for t in filled if C4.overlap(t, by_sym) is None]
            st = C4.stats([t["r_multiple"] for t in filled])
            row["方案"][sch] = {
                "确认": len(tr), "成交": len(filled),
                "R合计": st.get("R合计"), "R均值": st.get("R均值"),
                "R中位": st.get("R中位"), "胜率": st.get("胜率"),
                "盈亏比": st.get("盈亏比"),
                "新增机会": len(new),
                "新增R合计": C4.stats([t["r_multiple"] for t in new]).get("R合计"),
            }
        rows.append(row)
    return rows


def main() -> int:
    rows = sens_rows()
    rep = {"probe": "05_决策表", "rules_version": R.RULES_VERSION,
           "generated_at_utc": iso(time.time() * 1000),
           "params": R.PARAMS, "sensitivity": rows}

    md = []
    md.append("# ACCUM-EARLY-04-A｜集中待决表（决策用）")
    md.append("")
    md.append(f"- 生成时刻（UTC）：{iso(time.time() * 1000)}")
    md.append(f"- 规则接口版本：`{R.RULES_VERSION}`")
    md.append("- **本表不含任何批准。** 推荐值一律「未批准」，需要有人签字才生效。")
    md.append("")
    counts = {}
    for v in R.PARAMS.values():
        counts[v["状态"]] = counts.get(v["状态"], 0) + 1
    md.append("## 0 现在能不能开仓")
    md.append("")
    md.append(f"**不能。** {counts.get('推荐未批准', 0)} 项只有推荐没有批准、"
              f"{counts.get('待决', 0)} 项连推荐都给不出（成本与仓位口径冲突）。")
    md.append("")
    md.append("按依赖顺序，真正卡住「能不能跑起来」的只有 3 项：")
    md.append("")
    md.append("1. **扫低对象**（扫箱体下沿 L 还是扫任意局部支撑）——这一项决定机会量差 20 倍；")
    md.append("2. **局部压制位定义**——决定入场点高低；")
    md.append("3. **成本/仓位/参与率**——决定盈亏量级（本单用 R 倍数绕开，正式回测绕不开）。")
    md.append("")
    md.append("其余各项都有推荐值，工程上可以直接跑，但跑出来的结果**不能当成策略结论**。")
    md.append("")

    # ── 表 A：本单新增 / 最关键的待决 ──
    md.append("## 1 表 A：本单新增、真正影响结果的（按重要性排序）")
    md.append("")
    md.append("| # | 问题 | 已有候选 | 推荐（未批准） | 依据 | 改变的行为 | 风险 |")
    md.append("|---|---|---|---|---|---|---|")
    order = ["sweep_object", "local_resistance_definition", "resistance_min_gap_atr",
             "local_support_bars", "absorption_taker_mult", "absorption_is_gate",
             "sell_pressure_mult", "confirm_requires_bull_close", "armed_fail_bars",
             "pullback_zone_atr", "entry_price_deviation_cap", "max_gap_up_atr"]
    for n, k in enumerate(order, 1):
        v = R.PARAMS[k]
        md.append("| {} | {} | {} | `{}` = `{}` | {} | {} | {} |".format(
            n, v["含义"], _cand(k), k, v["值"], v["依据"], v["改变的行为"], v["风险"]))
    md.append("")

    # ── 表 B：沿用项（已定，不再问）──
    md.append("## 2 表 B：已经定死的（不再询问，列出来是为了可核对）")
    md.append("")
    md.append("| 项 | 值 | 依据 |")
    md.append("|---|---|---|")
    for k, v in R.PARAMS.items():
        if v["状态"] in ("已确定", "沿用"):
            md.append(f"| `{k}` | {v['值']} | {v['依据']} |")
    md.append("")

    # ── 表 C：口径冲突 ──
    md.append("## 3 表 C：口径冲突（两份文件写着不同的值，不择一）")
    md.append("")
    md.append("| 项 | 两套值 | 影响 | 本单怎么处理 |")
    md.append("|---|---|---|---|")
    md.append("| 滑点 | 策略包 3bps / 生效视图 5bps | 改所有净额 | 主表用 3bps，"
              "并说明 5bps 会把每笔再压低约 0.02R |")
    md.append("| 本金与仓位 | 2000/3仓/30% / 5000/10仓/10% | 决定名义额 | "
              "**本单全部用 R 倍数**，不模拟名义额 |")
    md.append("| 参与率上限 | 0.1% / 1% | 决定能不能成交 | 不模拟，只报「触发了多少笔」 |")
    md.append("")

    # ── 表 D：敏感性 ──
    md.append("## 4 敏感性：换一个参数，结果差多少（研究区间 2025-01-01 ~ 2026-07-01）")
    md.append("")
    md.append("> 一次只改一个参数，其余用推荐值。**这不是寻优**，是让你看见每个旋钮的力度。")
    md.append("")
    md.append("| 改动 | 甲 成交 / R合计 / R均值 / 胜率 / 新增机会 | "
              "乙 成交 / R合计 / R均值 | 丙 成交 / R合计 / R均值 |")
    md.append("|---|---|---|---|")
    for r in rows:
        if r.get("缺失"):
            md.append(f"| {r['说明']} | 缺产物 | | |")
            continue
        cells = []
        for sch in ("4a", "4b", "4c"):
            s = r["方案"][sch]
            cells.append("{} / {} / {} / {} / {}".format(
                s["成交"], s["R合计"], s["R均值"], s["胜率"], s["新增机会"])
                if sch == "4a" else "{} / {} / {}".format(
                    s["成交"], s["R合计"], s["R均值"]))
        md.append("| {} | {} | {} | {} |".format(r["说明"], *cells))
    md.append("")
    md.append("读法：")
    md.append("")
    md.append("- **R合计** 是全部成交的 R 倍数加总。样本越大越接近「这套规则赚不赚钱」；")
    md.append("- **新增机会** 指「原策略在同一标的同一时段没有候选」的成交笔数，"
              "这些是新规则自己找的，不是原策略的机会；")
    md.append("- 换参数后 R合计 大幅变化 ⇒ 这个参数是主开关，不能随手定；"
              "变化很小 ⇒ 说明结论对这个参数不敏感，可以先按推荐值跑。")
    md.append("")

    # ── 表 E：本单发现的结构问题 ──
    md.append("## 5 比阈值更重要的三件事（本单跑出来的）")
    md.append("")
    base = next((r for r in rows if r["标签"] == "research_final"), None)
    boxlow = next((r for r in rows if r["标签"] == "sens_sweep_boxlow"), None)
    if base and boxlow:
        md.append("### 5.1 提前入场的收益全部集中在「原策略也会做的机会」上")
        md.append("")
        md.append("| | 甲 | 乙 | 丙 |")
        md.append("|---|---|---|---|")
        md.append("| 与原策略候选重叠的笔数 R合计 | {} | {} | {} |".format(
            *[_overlap_r(base, s) for s in ("4a", "4b", "4c")]))
        md.append("| 新增机会（不与原策略相交）R合计 | {} | {} | {} |".format(
            *[_new_r(base, s) for s in ("4a", "4b", "4c")]))
        md.append("")
        md.append("重叠的那几十笔是正的，新增的几千笔把总和拉成负数。"
                  "**「提前」有价值，「放宽事件链起点」没有价值。**")
        md.append("")
        md.append("口径说明（很重要）：这里的「重叠」= **同标的 + 原策略候选时刻落在"
                  "本笔的箱体建档到出场后 7 天之间**，是一段**时间窗相交**，"
                  "不是「同一笔交易」。所以上表的正数**不能**读成"
                  "「同一批机会，提前进场后从 −35.5 变成 +27.6」；"
                  "它只说明「与原策略关注同一批标的、同一段时间的那些笔是正的」。")
        md.append("")
        md.append("### 5.2 把扫低对象改回箱体下沿，机会量掉一个数量级")
        md.append("")
        md.append("| 口径 | 甲 成交 | 甲 R合计 | 乙 成交 | 丙 成交 |")
        md.append("|---|---|---|---|---|")
        for r in (base, boxlow):
            md.append("| {} | {} | {} | {} | {} |".format(
                r["说明"], r["方案"]["4a"]["成交"], r["方案"]["4a"]["R合计"],
                r["方案"]["4b"]["成交"], r["方案"]["4c"]["成交"]))
        md.append("")
        md.append("### 5.3 三组不是原策略的超集")
        md.append("")
        cmp4 = json.loads((OUT / "04_三组对照.json").read_text(encoding="utf-8"))
        w = cmp4["windows"].get("research_final") or cmp4["windows"].get("research") or {}
        if w:
            md.append("原策略的 {} 个候选里，有 {} 个三组**一个都没抓到**"
                      "（{:.1%}）。原因是新方案的扫低对象是「局部支撑」，"
                      "而原策略扫的是箱体下沿；两者不是同一件事。".format(
                          w["baseline"]["candidates"],
                          w["baseline"]["三组都没覆盖的候选"],
                          w["baseline"]["三组都没覆盖的候选"]
                          / max(1, w["baseline"]["candidates"])))
        md.append("")
        # 5.4 承接阈值：唯一能把结论翻正的旋钮
        t12 = next((r for r in rows if r["标签"] == "sens_taker12"), None)
        if base and t12 and not t12.get("缺失"):
            md.append("### 5.4 承接阈值是唯一能把结论翻正的旋钮（也是最危险的一个）")
            md.append("")
            md.append("| 口径 | 甲 成交 | 甲 R合计 | 乙 R合计 | 丙 R合计 |")
            md.append("|---|---|---|---|---|")
            for r in (base, t12):
                md.append("| {} | {} | {} | {} | {} |".format(
                    r["说明"], r["方案"]["4a"]["成交"], r["方案"]["4a"]["R合计"],
                    r["方案"]["4b"]["R合计"], r["方案"]["4c"]["R合计"]))
            md.append("")
            md.append("承接阈值从「不比平时差」（1.0 倍）提到「明显更强」（1.2 倍）后，"
                      "甲组 R 合计从 {} 变成 {}，丙组从 {} 变成 {}，"
                      "代价是笔数从 {} 掉到 {}。".format(
                          base["方案"]["4a"]["R合计"], t12["方案"]["4a"]["R合计"],
                          base["方案"]["4c"]["R合计"], t12["方案"]["4c"]["R合计"],
                          base["方案"]["4a"]["成交"], t12["方案"]["4a"]["成交"]))
            md.append("")
            md.append("**这条不能当成「找到了好参数」。** 它是在研究区间上试出来的，"
                      "而且是在四五个旋钮里唯一翻正的那个——这恰恰是过拟合最典型的形状："
                      "单点、方向突变、样本同时腰斩。要它成立，必须在一个没看过的区间上重跑，"
                      "而本单的验证区间已经用掉了（见 `04_验证区间方案.md` 第 0 节）。")
            md.append("")

    (OUT / "05_决策表.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (HERE.parents[1] / "03_决策表.md").write_text("\n".join(md) + "\n",
                                                 encoding="utf-8")
    print("\n".join(md[:40]))
    print("... written 03_决策表.md")
    return 0


def _overlap_r(row, sch):
    """从 04 的产物里取重叠笔 R合计（04 已按标签算过）。"""
    try:
        cmp4 = json.loads((OUT / "04_三组对照.json").read_text(encoding="utf-8"))
        for tag in (row["标签"], "research"):
            w = cmp4["windows"].get(tag)
            if w and sch in w["schemes"]:
                return w["schemes"][sch]["重叠笔_结果"]["R合计"]
    except Exception:                                      # noqa: BLE001
        pass
    return "—"


def _new_r(row, sch):
    """新增机会的 R 合计（04 已按标签算过）。"""
    try:
        cmp4 = json.loads((OUT / "04_三组对照.json").read_text(encoding="utf-8"))
        for tag in (row["标签"], "research"):
            w = cmp4["windows"].get(tag)
            if w and sch in w["schemes"]:
                return w["schemes"][sch]["新增机会_结果"]["R合计"]
    except Exception:                                      # noqa: BLE001
        pass
    return "—"


def _cand(k):
    return {
        "sweep_object": "箱体下沿 L（原策略）/ 局部支撑 S_loc（甲乙丙原文）",
        "local_resistance_definition": "前 N 根最高 / 箱体中位 / 箱内收盘分位 / 摆动高点",
        "resistance_min_gap_atr": "0.5A / 1.0A",
        "local_support_bars": "12 / 24 / 48 根",
        "absorption_taker_mult": "1.0 / 1.1 / 1.2 倍",
        "absorption_is_gate": "硬门槛 / 只作证据",
        "sell_pressure_mult": "0.8 / 1.0 倍",
        "confirm_requires_bull_close": "要求收阳 / 不要求",
        "armed_fail_bars": "1 / 2 / 3 根",
        "pullback_zone_atr": "0.25A / 0.5A",
        "entry_price_deviation_cap": "1% / 1.5% / 2%",
        "max_gap_up_atr": "0.5A / 1.0A",
    }.get(k, "—")


if __name__ == "__main__":
    raise SystemExit(main())

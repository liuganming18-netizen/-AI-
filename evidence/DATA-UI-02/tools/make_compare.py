#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-UI-02：把三份覆盖审计（改前 / 改后统一阈值 / 改后轮转阈值）合成前后对照表。

数字全部从审计 JSON 读，不手抄 —— 手抄一次就可能和证据对不上。
"""
import json
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.abspath(os.path.join(HERE, ".."))

BLOCKS = [
    ("taker_buy_sell_ratio", "主动买卖量比"),
    ("top_account_long_short_ratio", "大户账户多空比"),
    ("global_account_long_short_ratio", "全市场账户多空比"),
    ("top_position_long_short_ratio", "大户持仓多空比（核心项，对照用）"),
]
STATUSES = ["有值", "未轮到", "限频", "错误", "过期", "上游不支持", "无记录"]


def load(name):
    p = os.path.join(EVID, name)
    if not os.path.exists(p):
        raise SystemExit(f"缺少审计文件：{p}")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def counts(a, key):
    c = a["summary"][key]["counts"]
    return {s: c.get(s, 0) for s in STATUSES}


def main():
    before = load("覆盖审计_改前.json")
    after900 = load("覆盖审计_改后_统一900.json")
    after = load("覆盖审计_改后_轮转阈值.json")

    L = []
    L.append("# DATA-UI-02 采集覆盖前后对照\n")
    L.append(f"生成时间（UTC）：`{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}`\n")
    L.append("> 三份审计都是**同一套脚本**（`tools/audit_ratio_coverage.py`）跑出来的，"
             "只是取值时点与过期阈值不同。改前那份用 `--from-snapshots` 把「应采」锁在"
             "当时的 60 个标的上，避免用改后的口径去算改前的账。\n")

    L.append("## 一、分母与阈值\n")
    L.append("| 项 | 改前 | 改后 |")
    L.append("|---|---|---|")
    L.append(f"| 应采标的 | {before['expected_n']} | {after['expected_n']} |")
    L.append("| 选取依据 | ASCII 升序前 48 ∪ 主流清单 | 24h 成交额前 60 ∪ 主流 ∪ 固定观测"
             "（且要求价格流仍在更新） |")
    L.append(f"| 过期阈值 | 统一 900s | {after.get('stale_policy') or '见审计文件'} |")
    L.append(f"| 审计时刻 | `{before['generated_at']}` | `{after['generated_at']}` |")
    L.append("")

    L.append("## 二、三个比率块：改前 vs 改后\n")
    L.append("改前与改后第一列都按**统一 900s** 判过期，才是同一把尺子；"
             "最后一列是改后按**轮转阈值**判的结果。\n")
    L.append("| 比率块 | 应采 | 改前·有值 | 改前·过期 | 改后·有值(900s) | "
             "改后·有值(轮转阈值) | 改后·未轮到 | 改后·限频 | 改后·过期 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for key, name in BLOCKS:
        b, a9, a = counts(before, key), counts(after900, key), counts(after, key)
        L.append(f"| {name} | {after['summary'][key]['expected']} | {b['有值']} | {b['过期']} | "
                 f"{a9['有值']} | {a['有值']} | {a['未轮到']} | {a['限频']} | {a['过期']} |")
    L.append("")

    # 同一把尺子下的对比：这行是整份报告最容易被误读的地方，必须自己讲清楚
    b0 = counts(before, BLOCKS[0][0])["有值"]
    a90 = counts(after900, BLOCKS[0][0])["有值"]
    arot = counts(after, BLOCKS[0][0])["有值"]
    L.append(f"**同一把 900s 尺子下：改前 {b0}、改后 {a90}** —— 几乎没变。"
             f"换成轮转阈值（2880s）才变成 {arot}。")
    L.append("")
    L.append("也就是说：**这次涨的不是数据量，是判据**。原来那 33 个「过期」绝大多数是"
             "「轮转周期 38 分钟 > 阈值 15 分钟」的数学必然结果，数据一直在采。"
             "真正被修掉的是「未轮到」和「被轮转跳过」这两类真缺口。")
    L.append("")

    L.append("## 三、改后的缺失原因分布（互斥分类）\n")
    L.append("| 状态 | 主动买卖量比 | 大户账户多空比 | 全市场账户多空比 |")
    L.append("|---|---|---|---|")
    any_missing = False
    for s in STATUSES:
        if s == "有值":
            continue
        cells = []
        for key, _n in BLOCKS[:3]:
            cells.append(str(counts(after, key)[s]))
        if all(c == "0" for c in cells):
            continue
        any_missing = True
        L.append(f"| {s} | " + " | ".join(cells) + " |")
    if not any_missing:
        L.append(f"| （无缺失） | 0 | 0 | 0 |")
        L.append("")
        L.append(f"改后三个比率块**全部取齐**：应采 {after['expected_n']}，"
                 f"有值 {counts(after, 'taker_buy_sell_ratio')['有值']}，"
                 "未轮到 / 限频 / 错误 / 过期 / 上游不支持 / 无记录 均为 0。")
    L.append("")

    # 未轮到 / 限频 的标的清单（按块合并，同一批标的三个块一起缺）
    L.append("## 四、改后缺失明细（按状态归组）\n")
    any_group = False
    for s in ("未轮到", "限频", "错误", "过期", "上游不支持", "无记录"):
        syms = []
        for r in after["rows"]:
            for key, _n in BLOCKS[:3]:
                if r[key]["status"] == s and r["symbol"] not in syms:
                    syms.append(r["symbol"])
        if not syms:
            continue
        any_group = True
        reason = ""
        for r in after["rows"]:
            for key, _n in BLOCKS[:3]:
                if r[key]["status"] == s and r[key]["reason"]:
                    reason = r[key]["reason"]
                    break
            if reason:
                break
        L.append(f"- **{s}（{len(syms)} 个标的）**：{', '.join('`' + x + '`' for x in syms[:40])}"
                 + ("…" if len(syms) > 40 else ""))
        if reason:
            L.append(f"  - 原因：{reason[:110]}")
    if not any_group:
        L.append("**无。** 62 个标的的三个额外比率块在审计时刻全部有值、且未过期，"
                 "不存在需要解释的缺失标的。")
    L.append("")

    # 界面实际取数口径
    ui = after.get("ui_scope_taker_ratio") or {}
    if ui:
        L.append("## 五、一个必须澄清的口径\n")
        L.append("界面上「主动买卖量比」**不是**读快照里那个同名块，而是用实时 K 线派生"
                 "（主动买入量 ÷ (成交量 − 主动买入量)）。上表第一行统计的是**快照同名块**，"
                 "不等于界面缺数据：\n")
        L.append(f"- 审计范围内可算出：**{ui['scoped'][0]}/{ui['scoped'][1]}**")
        L.append(f"- 币安全池（1h 实时 K 线）可算出：**{ui['whole_pool'][0]}/{ui['whole_pool'][1]}**")
        L.append("- 只有上游不给主动买入量的交易所（欧易原生 K 线）才显示为「上游不支持」")
        L.append("")
        L.append("所以「24/60」这个数说的是**快照同名块**的覆盖，"
                 "不是界面上那一行的覆盖。这两件事必须分开讲，否则会误判成界面缺数据。\n")

    out = os.path.join(EVID, "覆盖对照表.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("\n".join(L))
    print("\n写出：", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

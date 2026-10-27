# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-A｜08 验证区间方案（生成 04_验证区间方案.md）。

为什么要有这一步
----------------
「研究区间挑参数、验证区间看结果」只有在**验证区间没被看过**时才成立。
本单的验证区间已经跑过一次（结果见正文第 5 节），所以它对本轮**不再算盲测**。
这一点必须写在文档最前面，否则后面任何「验证通过」都是假的。

本脚本只做两件事：
  1. 把区间怎么划、边界在哪、判据是什么写清楚；
  2. 把两个区间的数字从 out/*.json 现算出来并列，不手抄。

输出：04_验证区间方案.md + out/08_验证区间方案.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "rules"))
OUT = HERE.parents[1] / "out"

import accum_early_rules as R                      # noqa: E402

SCHEMES = (("baseline", "基线（原策略）"), ("4a", "甲 4a"), ("4b", "乙 4b"), ("4c", "丙 4c"))


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def load(name):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def pick(*names):
    for n in names:
        d = load(n)
        if d is not None:
            return d, n
    return None, None


def fmt(x, nd=4):
    return "—" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def main() -> int:
    res, res_src = pick("01_共同观察范围_research_final.json",
                        "01_共同观察范围_research.json")
    hol, hol_src = pick("01_共同观察范围_holdout_final.json",
                        "01_共同观察范围_holdout.json")
    c_res, _ = pick("07_仓位约束对照_research_final.json", "07_仓位约束对照_research.json")
    c_hol, _ = pick("07_仓位约束对照_holdout_final.json", "07_仓位约束对照_holdout.json")
    cmp4 = load("04_三组对照.json")

    rep = {"probe": "08_验证区间方案", "generated_at_utc": iso(time.time() * 1000),
           "rules_version": R.RULES_VERSION,
           "sources": {"research": res_src, "holdout": hol_src}}

    md = ["# ACCUM-EARLY-04-A｜验证区间方案", "",
          f"- 生成时刻（UTC）：{iso(time.time() * 1000)}",
          f"- 规则接口版本：`{R.RULES_VERSION}`",
          f"- 数据来源：`{res_src}`、`{hol_src}`", "",
          "## 0 先说最要紧的一句", "",
          "**这个验证区间已经被跑过一次了，所以它对本轮不再是盲测。**",
          "",
          "本单在定稿过程中已经把 2026-07-01 ~ 2026-10-12 跑完并登记（第 5 节）。",
          "按照「不许用验证区间挑参数」的纪律，这意味着：",
          "",
          "- 本轮任何参数调整**都不能**再拿这段数据当依据，也不能宣称「验证通过」；",
          "- 要真正验证，必须等**新的、没看过的**数据（2026-10-12 之后）；",
          "- 本文件的作用是**把规则和判据先钉死**，等新数据到了照着跑，而不是事后解释。",
          ""]

    # ── 1 区间划分 ──
    md += ["## 1 区间怎么划的", ""]
    if res and hol:
        md += ["| 区间 | 起 | 止 | 用途 | 长度 |", "|---|---|---|---|---|",
               f"| 研究区间 | {res['window']['from']} | {res['window']['to']} | "
               f"**所有阈值都只在这里挑** | "
               f"{(res['window']['to_ms'] - res['window']['from_ms']) / 86400000:.0f} 天 |",
               f"| 验证区间 | {hol['window']['from']} | {hol['window']['to']} | "
               f"只用于「照规则跑一遍、如实登记」，不回头改参数 | "
               f"{(hol['window']['to_ms'] - hol['window']['from_ms']) / 86400000:.0f} 天 |",
               ""]
    md += ["划分原则：", "",
           "1. **两段不重叠**，中间不设过渡带（过渡带等于给了调参的空间）；",
           "2. 验证区间取**最近一段**，因为越近的数据越接近当下市场形态；",
           "3. 两段的标的池完全相同（同一批 526 个），所以差异只来自行情，不来自换池子。",
           ""]

    # ── 2 能用不能用 ──
    md += ["## 2 验证区间能用吗：三条边界", "",
           "### 2.1 数据覆盖：够用", "",
           "15m 与 1h/4h 全部走冷热合并读取器（`research_15m.Merged15m`）。",
           "验证区间结束于 2026-10-12，而热库数据已覆盖到 2026-10-26，"
           "所以**不存在尾部截断**——不会出现「最后一笔因为没数据被强行平仓」。",
           "",
           "标的有数据率："]
    if hol:
        a = hol["aggregate"]
        md.append(f"- 池内 {a['symbols_scanned']} 个，有数据 {a['symbols_with_data']} 个，"
                  f"跳过 {a['symbols_skipped']} 个（`CTUSDT`，该段无 K 线）。")
    md += ["", "### 2.2 时点语义（PIT）：本单不依赖持仓量，所以不被它卡住", "",
           "原策略的正式回测要过 OI 门槛，而 OI 的「可得时点」没有发布证据，"
           "所以 OI 相关结论一律只能声明「计算不超前」。",
           "",
           "本单的基线价格链**不含 OI 门槛**（`discipline` 里已写明），"
           "三组提前方案也只用 K 线自带的主动买占比，不用外部指标。"
           "因此这段验证区间的结论**不受 OI 时点问题影响**——这是本单少数能干净说话的地方。",
           "",
           "但仍然有一处不可证：15m 冷档的 `available_at` 填充率是 0%（见 ACCUM-EARLY-03-B）。"
           "本单按「K 线开盘时刻 = 可得时刻」处理，属于**假设**，不是已证事实。",
           "",
           "### 2.3 幸存者偏差：存在，且方向是让结果偏好看", "",
           "标的池 = 当前 `parquet/candles/binance/um` 下的 526 个目录，"
           "也就是**今天还活着的币**。研究区间和验证区间里已经下架的币不在池内。",
           "",
           "对一个只做多的突破策略，这会**高估**收益。"
           "所以本单的负结论可以放心用（真实情况只会更差），"
           "但任何正结论都要打折看。",
           ""]

    # ── 3 一次性使用规则 ──
    md += ["## 3 怎么用这段数据（一次性规则）", "",
           "1. 参数在研究区间定稿 → **冻结**（写进 `PARAMS`，标「已定」）；",
           "2. 冻结后**只跑一次**验证区间；",
           "3. 跑完不管好坏，结果原样登记，**不回改参数再跑**；",
           "4. 如果确实要改参数，改完后必须用**更新的数据**做新验证，"
           "不能把这段数据再用一次。",
           "",
           "这条规则的作用是防止「跑十次挑一次好看的」。本单第 5 节的结果就是按这个规则原样登记的，"
           "包括对它不利的部分（符号翻转）。",
           ""]

    # ── 4 判据 ──
    md += ["## 4 什么算通过（判据先定，免得事后解释）", "",
           "只有三态：**通过 / 不通过 / 未验证**。未验证不计入通过。", "",
           "| 判据 | 通过的条件 | 为什么这么定 |", "|---|---|---|",
           "| 方向一致性 | 研究区间与验证区间的 R 合计**同号**，且都为负时也要同号 | "
           "符号翻转说明差异落在噪声里，不能据此选任何一组 |",
           "| 优于基线 | 同区间内该组 R 合计 > 基线 R 合计，**且**在仓位约束下也 > 基线 | "
           "无约束的 R 合计会被笔数放大，必须两个口径都对 |",
           "| 中位数为正 | R 中位 > 0 | 中位为负说明靠少数大赢单撑总账，不可复制 |",
           "| 样本量 | 验证区间成交 ≥ 30 笔 | 少于 30 笔，胜率和均值都不可信 |",
           "| 可复现 | 同一参数重跑结果逐位相同 | 确定性是可复算的前提 |",
           ""]

    # ── 5 已知结果 ──
    md += ["## 5 已经跑出来的结果（原样登记，包括不利的）", "",
           "### 5.1 无仓位约束（信号全接）", "",
           "| 组 | 研究区间 成交 | 研究 R合计 | 研究 R均值 | 研究 R中位 | "
           "验证 成交 | 验证 R合计 | 验证 R均值 | 验证 R中位 |",
           "|---|---|---|---|---|---|---|---|---|"]
    tbl = {}
    for key, label in SCHEMES:
        r = (res["aggregate"]["schemes"].get(key) or {}).get("trade_summary") if res else None
        h = (hol["aggregate"]["schemes"].get(key) or {}).get("trade_summary") if hol else None
        tbl[key] = {"research": r, "holdout": h}
        md.append(f"| {label} | {fmt(r and r['成交'])} | {fmt(r and r['R合计'])} | "
                  f"{fmt(r and r['R均值'])} | {fmt(r and r['R中位'])} | "
                  f"{fmt(h and h['成交'])} | {fmt(h and h['R合计'])} | "
                  f"{fmt(h and h['R均值'])} | {fmt(h and h['R中位'])} |")
    md.append("")
    if res and hol:
        flips = []
        for key, label in SCHEMES:
            r = tbl[key]["research"]
            h = tbl[key]["holdout"]
            if r and h and (r["R合计"] > 0) != (h["R合计"] > 0):
                flips.append(f"{label}（研究 {r['R合计']:.2f} → 验证 {h['R合计']:.2f}）")
        md.append("**符号翻转的组：" + ("、".join(flips) if flips else "无") + "**")
        md.append("")
        md.append("符号翻转的含义很直接：同一套规则，换一段行情，赚亏方向就变了。"
                  "这不叫「验证区间表现更好」，这叫**没有稳定优势**。")
    rep["unconstrained"] = tbl

    # 5.2 仓位约束
    if c_res and c_hol:
        md += ["", "### 5.2 3 仓上限下（更接近真实能做的）", "",
               "冻结参数里同时最多 3 个仓位（`max_active_positions=3`，"
               "每笔风险 0.5%，合计 1.5%）。无约束的 R 合计等于假设无限仓位，"
               "而四组笔数相差 20 倍，直接横比没有意义。", "",
               "| 组 | 研究 可执行笔 | 研究 R合计 | 研究 R均值 | 验证 可执行笔 | 验证 R合计 | 验证 R均值 |",
               "|---|---|---|---|---|---|---|"]
        for key, label in SCHEMES:
            a = (c_res["schemes"].get(key) or {}).get("3仓约束下可执行") or {}
            b = (c_hol["schemes"].get(key) or {}).get("3仓约束下可执行") or {}
            md.append(f"| {label} | {fmt(a.get('笔数'))} | {fmt(a.get('R合计'))} | "
                      f"{fmt(a.get('R均值'))} | {fmt(b.get('笔数'))} | "
                      f"{fmt(b.get('R合计'))} | {fmt(b.get('R均值'))} |")
        md += ["", "分配规则是**先到先得**（按入场时刻占仓，不按 R 大小挑），"
               "对四组完全相同。它是**一种**合法占用顺序，不是真实可实现收益。"]
        rep["constrained"] = {k: {"research": (c_res["schemes"].get(k) or {}).get("3仓约束下可执行"),
                                  "holdout": (c_hol["schemes"].get(k) or {}).get("3仓约束下可执行")}
                              for k, _ in SCHEMES}

    # 5.3 重叠
    if cmp4:
        md += ["", "### 5.3 提前入场的收益来自哪里", ""]
        w = cmp4["windows"].get("research_final") or cmp4["windows"].get("research")
        if w:
            md += ["| 组 | 与原策略重叠的笔数 | 重叠 R合计 | 新增机会笔数 | 新增 R合计 |",
                   "|---|---|---|---|---|"]
            for key in ("4a", "4b", "4c"):
                s = w["schemes"].get(key) or {}
                md.append(f"| {key} | {fmt(s.get('与原策略候选重叠'))} | "
                          f"{fmt((s.get('重叠笔_结果') or {}).get('R合计'))} | "
                          f"{fmt(s.get('新增机会'))} | "
                          f"{fmt((s.get('新增机会_结果') or {}).get('R合计'))} |")
            md += ["", "重叠的那几十笔是正的，新增的几千笔把总和拉成负数。"
                   "**「提前」有价值，「放宽事件链起点」没有价值。**",
                   "",
                   f"另外，原策略的 {w['baseline']['candidates']} 个候选里，"
                   f"{w['baseline']['三组都没覆盖的候选']} 个三组一个都没抓到——"
                   "三组不是原策略的超集。"]
            rep["overlap"] = w["schemes"]

    # ── 6 结论 ──
    md += ["", "## 6 按第 4 节判据，现在是什么结论", ""]
    if res and hol:
        rows = []
        for key, label in SCHEMES:
            r, h = tbl[key]["research"], tbl[key]["holdout"]
            if not (r and h):
                continue
            same_sign = (r["R合计"] > 0) == (h["R合计"] > 0)
            beat = h["R合计"] > (tbl["baseline"]["holdout"]["R合计"])
            med_pos = h["R中位"] is not None and h["R中位"] > 0
            n_ok = h["成交"] >= 30
            rows.append((label, same_sign, beat, med_pos, n_ok))
        md += ["| 组 | 方向一致 | 优于基线（验证） | 验证中位为正 | 验证样本≥30 | 判定 |",
               "|---|---|---|---|---|---|"]
        for label, same_sign, beat, med_pos, n_ok in rows:
            ok = same_sign and beat and med_pos and n_ok
            verdict = "通过" if ok else "**不通过**"
            md.append(f"| {label} | {'是' if same_sign else '**否**'} | "
                      f"{'是' if beat else '否'} | {'是' if med_pos else '否'} | "
                      f"{'是' if n_ok else '否'} | {verdict} |")
        md += ["", "**没有任何一组通过。** 三组提前方案都在「方向一致」这一关就挂了："
               "研究区间全负、验证区间有的转正，符号对不上。",
               "",
               "所以现在的正确说法是：**提前入场在这两段数据上都没有稳定优势**，"
               "而不是「验证区间乙组赚了 67 个 R」。"]
    md += ["", "## 7 下一步（按依赖顺序）", "",
           "1. 先裁决第 3 节的一次性规则是否认可——如果不认可，后面都没意义；",
           "2. 把第 4 节判据写进冻结文档，**在跑新数据之前**；",
           "3. 等 2026-10-12 之后的新数据积累到至少 90 天，再做一次真正的盲测；",
           "4. 在那之前，不要用本单任何一组参数去开仓。",
           ""]

    (OUT / "08_验证区间方案.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (HERE.parents[1] / "04_验证区间方案.md").write_text("\n".join(md) + "\n",
                                                     encoding="utf-8")
    print("\n".join(md[-30:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

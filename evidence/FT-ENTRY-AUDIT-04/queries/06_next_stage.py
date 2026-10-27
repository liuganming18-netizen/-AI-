# -*- coding: utf-8 -*-
"""06 下一阶段准备（只出方案，不执行）。

工单第 4 条：若发现值得验证的区域，提出有限候选及选择依据，并列明拟用验证区间
是否曾被查看或用于调参。本轮不打开 2026 留出期，不修改参数。

本脚本只读已交付的元数据表（不读 2026 行情数据）：
- evidence/OOS-READY-01/out/02_验证数据资格表.csv（S0~S7 日历段资格）
- evidence/OOS-READY-01/out/01_已观察范围_批次.csv（窗口与是否用于调参）
- 本目录 out/03_稳定区判据_终版_v1.csv、04_时间块与多重比较_v1.csv、05_收益来源分解_v1.csv

输出：out/06_*.csv
"""
from __future__ import annotations

import json
import math
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import OUT, ROOT, jdump, write_csv  # noqa: E402

OOS_OUT = os.path.join(ROOT, "evidence/OOS-READY-01/out")

Z_ALPHA = 1.959964   # 双侧 5%
Z_POWER = 0.841621   # 80%


def main() -> None:
    crit = pd.read_csv(os.path.join(OUT, "03_稳定区判据_终版_v1.csv"), encoding="utf-8-sig")
    stat = pd.read_csv(os.path.join(OUT, "04_时间块与多重比较_v1.csv"), encoding="utf-8-sig")
    dec = pd.read_csv(os.path.join(OUT, "05_收益来源分解_v1.csv"), encoding="utf-8-sig")
    met = pd.read_csv(os.path.join(OUT, "02_独立重算指标_v1.csv"), encoding="utf-8-sig")
    m = crit.merge(stat[["code", "block_sd_u", "block_mean_u"]], on="code")
    m = m.merge(dec[["code", "win_rate_pp_vs_base", "avg_win_ratio_vs_base", "payoff_ratio"]], on="code")
    m = m.merge(met[["code", "net_pnl", "expectancy", "trades", "max_dd_realized"]], on="code")

    # ── 6.1 有限候选：只取 Δ=0.6A 整行（不取单点最高） ──────────────────
    cand = m[m["delta"] == 0.6].copy()
    cand_rows = []
    for _, r in cand.iterrows():
        # 80% power、双侧 5%，按块级均值与块级 SD 折算所需 30 天块数
        effect = float(r["block_mean_u"])
        sd = float(r["block_sd_u"])
        nb_req = ((Z_ALPHA + Z_POWER) ** 2) * (sd / effect) ** 2 if effect > 0 else float("inf")
        cand_rows.append({
            "code": r["code"], "strategy": r["strategy"], "delta": r["delta"], "k_vol": r["k_vol"],
            "trades": int(r["trades"]), "pass_count": int(r["pass_count"]),
            "net_pnl": round(float(r["net_pnl"]), 4), "expectancy": round(float(r["expectancy"]), 4),
            "max_dd_realized": round(float(r["max_dd_realized"]), 4),
            "block_mean_u": round(effect, 4), "block_sd_u": round(sd, 4),
            "boot_ci_lo": r["boot_ci_lo"], "boot_ci_hi": r["boot_ci_hi"],
            "maxt_fwer_p": r["maxt_fwer_p"],
            "win_rate_pp_vs_base": r["win_rate_pp_vs_base"],
            "avg_win_ratio_vs_base": r["avg_win_ratio_vs_base"],
            "payoff_ratio": r["payoff_ratio"],
            "blocks_needed_80pct_power": round(nb_req, 1) if math.isfinite(nb_req) else None,
            "years_needed_80pct_power": round(nb_req * 30 / 365.25, 1) if math.isfinite(nb_req) else None,
        })
    cand_df = pd.DataFrame(cand_rows)
    write_csv(cand_df, "06_候选区域_v1.csv")
    print("候选区域（Δ=0.6A 整行，非单点）")
    print(cand_df.to_string(index=False))

    # ── 6.2 拟用验证区间：是否曾被查看 / 用于调参 ────────────────────────
    seg = pd.read_csv(os.path.join(OOS_OUT, "02_验证数据资格表.csv"), encoding="utf-8-sig")
    batch = pd.read_csv(os.path.join(OOS_OUT, "01_已观察范围_批次.csv"), encoding="utf-8-sig")
    tuned_batches = batch[batch["是否用于调参/筛选"].astype(str).str.startswith("是")]
    print(f"\nOOS-READY-01 已记录批次 {len(batch)} 个，其中标注『用于调参/筛选=是』的 {len(tuned_batches)} 个")

    seg_rows = []
    for _, r in seg.iterrows():
        start, end = str(r["起"]), str(r["止"])
        # 与本轮研究期（2021-12-01~2024-12-31）的重叠判定
        overlaps_research = not (end < "2021-12-01" or start > "2024-12-31")
        used_this_round = "是（FT-ENTRY-03 回测期）" if overlaps_research else "否"
        verdict = str(r["综合判定"])
        if overlaps_research:
            usable, why = "否", "已被本轮回测使用"
        elif verdict.startswith("不可用"):
            usable, why = "否", "数据资格不满足"
        elif verdict.startswith("已观察"):
            usable, why = "否", "已观察/已污染"
        else:
            usable, why = "否", "无数据"
        seg_rows.append({
            "段": r["段"], "起": start, "止": end, "日历天数": int(r["日历天数"]),
            "是否被既有回测窗口覆盖": r["是否被既有回测窗口覆盖"],
            "是否已用于调参": "是（9 格 A 网格在此段内选定）" if overlaps_research else
                              ("是（FULL_SWEEP 样本母集）" if r["是否被既有回测窗口覆盖"] == "已覆盖" else "否（未进入任何回测窗口）"),
            "本轮是否使用": used_this_round,
            "综合判定": verdict,
            "资格判定": r["资格判定"],
            "能否作为下一阶段验证区间": usable,
            "不可用原因": why,
            "依据": "evidence/OOS-READY-01/out/02_验证数据资格表.csv",
        })
    seg_df = pd.DataFrame(seg_rows)
    write_csv(seg_df, "06_拟用验证区间_接触状态_v1.csv")
    print("\n拟用验证区间（沿用 OOS-READY-01 的 S0~S7 分段，本轮未读取任何 2026 行情）")
    print(seg_df[["段", "起", "止", "是否被既有回测窗口覆盖", "是否已用于调参",
                  "本轮是否使用", "能否作为下一阶段验证区间", "不可用原因"]].to_string(index=False))

    usable = seg_df[seg_df["能否作为下一阶段验证区间"] == "是"]
    print(f"\n可直接作为下一阶段验证区间的段数 = {len(usable)}")

    # ── 6.3 前置条件清单 ───────────────────────────────────────────────
    pre = pd.DataFrame([
        {"前置条件": "成交价严格 < H",
         "现状": "信号收盘价 0 违反；但成交价仍有 8.3%~64.3% 落在 H 之上（最高 +0.357A），Δ 越小越严重",
         "证据": "out/01_A严格低于H_汇总_v1.csv",
         "是否阻断": "是（决定『严格突破前』是否名副其实）"},
        {"前置条件": "有效独立格数",
         "现状": "名义 9 格，实际 8 格（A07 与 A08 是同一批 14 笔成交）",
         "证据": "out/07_组间逐笔同一性_v1.csv",
         "是否阻断": "否，但多重比较的分母要改成 8"},
        {"前置条件": "统计分辨率",
         "现状": "按 Δ=0.6A 的块级效应 1.96~2.37 U/块、块级 SD 13.5~14.1，80% 功效需 ~280 个 30 天块 ≈ 23 年",
         "证据": "out/06_候选区域_v1.csv",
         "是否阻断": "是（现有 3 年研究期无法给出显著性）"},
        {"前置条件": "样本外区间",
         "现状": "无可用段：S0/S1/S6/S7 数据不合格，S2~S5 已被观察，S5 旧 Holdout 已被消耗",
         "证据": "evidence/OOS-READY-01/out/02_验证数据资格表.csv",
         "是否阻断": "是（只能靠前向采集，约 18~24 个月攒 100 笔）"},
        {"前置条件": "OI 发布时点",
         "现状": "全程 PitMode.DECLARED，桶结束即当可用，无 fetched_at 证据",
         "证据": "策略/pit_bridge.py register_oi；本目录 out/01_OI门槛行为汇总_v1.csv",
         "是否阻断": "是（PIT 未证 ⇒ 只能算探索性）"},
        {"前置条件": "回撤口径",
         "现状": "只有已实现权益回撤，不含浮亏，是下界",
         "证据": "out/02_独立重算指标_v1.csv 的 max_dd_realized",
         "是否阻断": "否，但不得用于风险预算结论"},
    ])
    write_csv(pre, "06_前置条件_v1.csv")
    print("\n前置条件\n", pre[["前置条件", "是否阻断"]].to_string(index=False))

    summary = {
        "candidates": cand_df.to_dict("records"),
        "n_usable_validation_segments": int(len(usable)),
        "segments": seg_df.to_dict("records"),
        "preconditions": pre.to_dict("records"),
        "round_used_2026_data": False,
        "round_note": "本轮所有脚本的输入仅限 2021-12-01~2024-12-31 的逐笔结果与 4h/1h/5m OI 数据；未读取 2026 行情",
        "recommendation": "不建议立即启动验证；先解决『成交价 < H』与『样本外区间不存在』两项阻断条件",
    }
    jdump(os.path.join(OUT, "06_下一阶段摘要_v1.json"), summary)
    print("\n完成")


if __name__ == "__main__":
    main()

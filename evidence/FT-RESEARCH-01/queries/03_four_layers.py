# -*- coding: utf-8 -*-
"""03_四分层｜按工单四类划分样本，并产出可扩展样本表。

四类（工单硬要求，逐类独立、口径不混）：
① 当前 70 币已研究样本（A 路线 70 白名单，冻结研究期 9 格正式研究）
② 241 基础候选中尚未纳入、且满足现有策略所需条件的可扩展区间
③ 过去已被其他实验使用的区间（按币×时间×接触深度）
④ 仍封存、不可自动启用的留出区间

关键口径：
- ②与③是不同维度（币成员资格 vs 时间区间接触），必须同时成立才可宣称「干净」；
- 「本轮没跑过」≠「从未用于研究」：②的币在时间维度上多数已落入③；
- L1 数据候选 ≠ L2 完整策略资格（L2 全池 0）。

产出：out/03_四分层样本_v1.csv、out/03_可扩展样本表_v1.csv、out/03_四分层摘要_v1.json
不下载、不改时间戳、不跑回测、不打开 2026 留出。
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (HOLDOUT_END, HOLDOUT_START, OOS_BATCH, OOS_COINS,  # noqa: E402
                     OOS_SEG, OUT, RESEARCH_END, RESEARCH_START,
                     jdump, load_fullsweep_runs, load_l1, load_whitelist_70,
                     read_csv, write_csv)

RS, RE = RESEARCH_START, RESEARCH_END


def main() -> None:
    l1 = load_l1()
    w70 = load_whitelist_70()
    fs = load_fullsweep_runs()
    ledger = read_csv(os.path.join(OUT, "02_接触台账_逐币_v1.csv"))
    oos_seg = read_csv(OOS_SEG)
    oos_coins = read_csv(OOS_COINS)
    oos_batch = read_csv(OOS_BATCH)

    s1 = set(l1["symbol"])
    w70set = set(w70)

    # 研究期内 FULL_SWEEP 每币候选/成交（信号可用性）
    inp = fs[(fs["s"] <= RE) & (fs["e"] >= RS)]
    res = inp[inp["symbol"].isin(s1)].groupby("symbol").agg(
        cand=("candidates", "sum"), trad=("trades", "sum"), net=("net", "sum"),
        runs=("symbol", "size"))
    # 全期（含 2025~2026）
    allg = fs.groupby("symbol").agg(cand_all=("candidates", "sum"),
                                    trad_all=("trades", "sum"))

    rows = []
    for sym in sorted(s1):
        is70 = sym in w70set
        r = res.loc[sym] if sym in res.index else None
        a = allg.loc[sym] if sym in allg.index else None
        o = oos_coins[oos_coins["symbol"] == sym]
        o = o.iloc[0] if len(o) else None
        led = ledger[ledger["symbol"] == sym]
        led = led.iloc[0] if len(led) else {}

        # ①/② 分类
        cls = "①已研究样本(A路线70白名单)" if is70 else "②可扩展候选(未入70白名单)"
        # ③ 时间维度接触
        time_touched = (a is not None) or (o is not None)
        depth = led.get("接触深度", "无接触记录")
        # ④ 留出污染
        hold_net = None
        if o is not None and "净额U（含重复）" in o:
            try:
                hold_net = float(o["净额U（含重复）"])
            except Exception:
                hold_net = None

        rows.append({
            "symbol": sym,
            "分层": cls,
            "L1资格": True,
            "L2完整策略资格": False,   # 全池 L2=0（1h OI 因子资格 0）
            "是否已接触_时间维度": bool(time_touched),
            "接触深度": depth,
            "研究期_候选": int(r["cand"]) if r is not None else 0,
            "研究期_成交": int(r["trad"]) if r is not None else 0,
            "研究期_净额运行级": round(float(r["net"]), 4) if r is not None else 0.0,
            "全期_候选": int(a["cand_all"]) if a is not None else 0,
            "全期_成交": int(a["trad_all"]) if a is not None else 0,
            "有真实信号_研究期": bool((r is not None and (r["cand"] > 0 or r["trad"] > 0))),
            "OOS最早窗口起": (o["最早窗口起"] if o is not None and "最早窗口起" in o else ""),
            "OOS最晚窗口止": (o["最晚窗口止"] if o is not None and "最晚窗口止" in o else ""),
            "OOS覆盖窗口数": int(o["覆盖窗口数"]) if o is not None and "覆盖窗口数" in o else 0,
        })
    four = pd.DataFrame(rows)
    write_csv(four, "03_四分层样本_v1.csv")

    # 可扩展样本表（②，且排除「无数据资格」）
    exp = four[four["分层"] == "②可扩展候选(未入70白名单)"].copy()
    exp["信号可用性"] = exp.apply(
        lambda r: "研究期内有成交" if r["研究期_成交"] > 0
        else ("研究期内有候选无成交" if r["研究期_候选"] > 0 else "研究期内无信号"), axis=1)
    exp["可否做最小因果验证"] = exp["有真实信号_研究期"]
    exp = exp.sort_values(["信号可用性", "symbol"], ascending=[True, True])
    write_csv(exp[["symbol", "L1资格", "L2完整策略资格", "接触深度",
                   "研究期_候选", "研究期_成交", "研究期_净额运行级",
                   "全期_候选", "全期_成交", "信号可用性",
                   "是否已接触_时间维度", "OOS最早窗口起", "OOS最晚窗口止",
                   "OOS覆盖窗口数", "可否做最小因果验证"]],
               "03_可扩展样本表_v1.csv")

    # 摘要
    n70 = int((four["分层"] == "①已研究样本(A路线70白名单)").sum())
    n_exp = int((four["分层"] == "②可扩展候选(未入70白名单)").sum())
    exp_sig = exp[exp["可否做最小因果验证"]]
    sig_dist = exp["信号可用性"].value_counts().to_dict()
    all_sig = four[four["有真实信号_研究期"]]

    # ③ 汇总（批次口径照录）
    touched_time_coins = int(four["是否已接触_时间维度"].sum())

    summary = {
        "工单": "FT-RESEARCH-01｜四分层样本",
        "四类定义与规模": {
            "①_当前70币已研究样本": {
                "规模": n70,
                "口径": "A 路线 70 白名单；冻结研究期 2021-12-01~2024-12-31；Δ∈{0.6,0.4,0.2}A × k_vol∈{0.8,1.0,1.2} 共 9 格 + Baseline",
                "注意": "9 格实为 8 个独立实验（A07=A08 逐笔 SHA 相同）；70 币中位覆盖仅约 1.6 年",
            },
            "②_241候选中未纳入且满足现有策略条件的可扩展区间": {
                "规模": n_exp,
                "条件": "L1 资格成立（4h 200 预热 ∩ 1h 候选 ∩ 15m 有效 ∩ 冻结研究期非空）",
                "关键限定": (
                    "L1 仅代表价量数据可算，不代表信号会出现，也不代表正式 PIT 资格；"
                    "1h OI 因子正式资格全池为 0（L2=0），故②是『数据候选可扩展』，"
                    "不是『完整策略资格可扩展』。"
                ),
                "其中_研究期内有真实信号": int(len(exp_sig)),
                "信号可用性分布": sig_dist,
            },
            "③_过去已被其他实验使用的区间": {
                "按币×时间": f"L1 241 个币中 {touched_time_coins} 个有时间维度接触记录（FULL_SWEEP 473 标的覆盖全部 241）",
                "按批次": "17 个批次，照录 out/02_接触台账_逐段_v1.csv 与 OOS-READY-01 批次表",
                "按日历段": "S2(2021-01-01~2021-11-30, OI 全缺 0 事件)、S3(2021-12, 6 事件)、S4(2022-01-01~2026-04-14, 主样本 253 事件/173 候选)",
                "冒烟集": "FT-BASELINE-02~05 / FT-FACTOR-01 的 5 币（AR/BANANA/BAT/CTSI/PUMPBTC）",
                "36币集": "FT-FACTOR-02（34 币在 L1 内，且全部 ⊆ 70 白名单）",
            },
            "④_仍封存不可自动启用的留出区间": {
                "旧Holdout": f"{HOLDOUT_START}~{HOLDOUT_END}",
                "旧Holdout状态": "已被 FULL_SWEEP 触碰并产生 37 笔唯一交易、净额 +234.38U ⇒ 已消耗/已污染，不可改名复用",
                "S0/S1": "未覆盖但数据资格不合格（OI/mark 缺失，单币或样本量不足）",
                "S6": "仅约 5 天，样本量不足",
                "S7": "无数据（需前向采集，约 18~24 个月才能攒到 100 笔）",
                "结论": "当前不存在『既未接触又数据合格』的可自动启用留出区间；可称样本外的只有以后新产生的数据",
            },
        },
        "维度重叠说明": (
            "②与③是不同维度：②按币成员资格划分，③按时间区间接触划分。"
            "L1 全部 241 个币都已有 FULL_SWEEP 时间维度接触记录，"
            "因此②的 171 个币在时间维度上多数已落入③。"
            "任何「用②做新研究」的表述必须同时说明：币未被正式 9 格研究过、"
            "但时间窗口已被扫描过。不得把「本轮没跑过」说成「从未用于研究」。"
        ),
        "可扩展币_有真实信号清单_前30": sorted(exp_sig["symbol"].tolist())[:30],
        "不含": ["不新增下载", "不改时间戳", "不打开 2026 留出数据", "不跑任何回测"],
    }
    jdump(os.path.join(OUT, "03_四分层摘要_v1.json"), summary)

    print("== 四分层 ==")
    print(f"  ① 70 币已研究样本: {n70}")
    print(f"  ② 可扩展候选(未入70白名单): {n_exp}｜其中研究期内有真实信号 {len(exp_sig)}")
    print(f"     信号可用性: {sig_dist}")
    print(f"  ③ 已接触区间: L1 {touched_time_coins}/{len(four)} 币有时间接触记录；17 批次；S2~S5")
    print(f"  ④ 封存留出: 旧 Holdout 已污染；S0/S1/S6/S7 数据不合格 ⇒ 可用留出区间 = 0")
    print("  out/03_四分层样本_v1.csv、out/03_可扩展样本表_v1.csv")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""05 三问判定：把第 1–3 项的可复核数字固化为简短结论。

判定纪律：
- 指标重算一致 ≠ 策略有效；本文件只引用 AUDIT-08 自己算出的区间/置换/稳健性结果；
- 达不到多重比较显著性的，写"证据不足"，不写"有效"；
- C2 的"无增益"是点估计与分解一致为负，且 6/9 网格为负，判"不支持"。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _common import OUT, jdump, write_csv  # noqa: E402
import pandas as pd  # noqa: E402


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    s2 = load("02_统计摘要_v1.json")
    s3 = load("03_入场下限影响摘要_v1.json")
    s4 = load("04_样本有效区间摘要_v1.json")
    pooled = {r["contrast"]: r for r in s2["pooled"]}
    c1 = pooled["C1−C0"]
    c2 = pooled["C2−C0"]
    c21 = pooled["C2−C1"]
    grids = pd.read_csv(os.path.join(OUT, "02_逐格配对统计_全对照_v1.csv"), encoding="utf-8-sig")
    prim = grids[grids.family == "主族"]
    c1g = prim[prim.contrast == "C1−C0"]
    c2g = prim[prim.contrast == "C2−C0"]

    verdict = {
        "Q1_C1是否提高胜率及净期望且稳定": {
            "answer": "净期望方向性提高、胜率只在'少交易'意义上提高；稳定性跨网格/跨年/跨币方向一致，"
                      "但多重比较后不显著。判：支持继续验证（不等于已证明有效）。",
            "net_expectancy": {
                "aggregate_u": {"C0": -4243.2554, "C1": 239.3019, "diff": 4482.5573},
                "event_level_mean_u": {"C0": c1["pnl_mean_C"], "C1": c1["pnl_mean_C1"],
                                       "diff": c1["pnl_diff"],
                                       "ci95": [c1["pnl_ci_lo"], c1["pnl_ci_hi"]],
                                       "raw_p": c1["pnl_p"]},
                "grids_positive": int((c1g.pnl_diff > 0).sum()),
                "grids_total": int(len(c1g)),
                "holm_lt_005": int((c1g.pnl_holm < 0.05).sum()),
                "maxt_fwer_p": float(c1g.pnl_maxt_fwer_p.iloc[0]),
            },
            "win_rate": {
                "trade_level_pct": {"C0": 18.7277, "C1": 22.7815, "diff_pp": 4.0538},
                "event_level_conditional_pct": {"C0": c1["win_rate_C"], "C1": c1["win_rate_C1"]},
                "matched_same_events_diff_pp": c1["win_diff_matched"],
                "matched_p": c1["win_p"],
                "interpretation": "成交口径胜率提高来自 C1 过滤掉 394 个 C0 事件（多为亏损），"
                                  "不是同批交易变好；219 个共同成交事件上胜率差为负且不显著。",
            },
            "quick_stop": {
                "trade_level_pct": {"C0": 47.2574, "C1": 40.2649, "diff_pp": -6.9925},
                "matched_same_events_diff_pp": c1["qs_diff_matched"],
                "matched_p": c1["qs_p"],
                "interpretation": "快速止损率下降同样来自不交易；共同成交事件上 C1 反而高 2.26pp（p=0.036）。",
            },
            "decomposition_grid_mean_u": {
                "avoided_c0_losses": 727.8601, "delay_effect": -215.8502,
                "new_slot_entries": -13.9480, "total": 498.0619,
                "share_from_avoidance_pct": 81.6,
            },
            "stability": {
                "cross_coin_drop_top1_p": 0.0508, "cross_coin_drop_top3_p": 0.1136,
                "cross_coin_leave_one_out_range_u": [0.7302, 0.9130],
                "cross_time_diff_by_year_u": {"2021": -0.6874, "2022": 1.1929,
                                              "2023": 0.4631, "2024": 0.8009},
                "extreme_profit_drop_top5_mean_diff_u": 1.1707,
                "block_positive_share_range_pct": [55.26, 76.32],
            },
            "grade": "支持继续验证",
        },
        "Q2_C2相对C1是否有增益": {
            "answer": "无增益，点估计与分解一致为负；C2 额外过滤掉的是 C1 的净盈利交易。判：不支持。",
            "aggregate_u": {"C1": 239.3019, "C2": -146.9014, "C2_minus_C1": -386.2033},
            "event_level": {"diff_u": c21["pnl_diff"], "ci95": [c21["pnl_ci_lo"], c21["pnl_ci_hi"]],
                            "raw_p": c21["pnl_p"]},
            "grids_positive": int((c2g.pnl_diff > 0).sum()), "grids_total": int(len(c2g)),
            "win_matched_diff_pp": c21["win_diff_matched"], "win_p": c21["win_p"],
            "qs_matched_diff_pp": c21["qs_diff_matched"], "qs_p": c21["qs_p"],
            "decomposition_grid_mean_u": {"avoided_c1_pnl": -56.2658, "delay_effect": 13.3543,
                                          "filled_only_in_c2": 0.0, "total": -42.9115},
            "grid_sign": {"positive_grids": int((grids[(grids.family == "次族")
                                                       & (grids.pnl_diff > 0)]).shape[0]),
                          "negative_grids": int((grids[(grids.family == "次族")
                                                       & (grids.pnl_diff < 0)]).shape[0])},
            "grade": "不支持",
        },
        "Q3_是否值得进入下一项实验与证据缺口": {
            "answer": "C1 值得进入下一项'独立样本/留出期 + 执行成本'验证；C2 不建议。"
                      "当前不能宣称策略有效：多重比较不显著、样本仅 613 个物理事件、"
                      "胜率/快速止损改善是同批交易之外的选择效应。",
            "min_entry_change": {
                "affected_candidates": s3["affected_candidates"],
                "affected_trades": s3["affected_traded"],
                "confirmation_candidates": s3["confirmation_candidates"],
                "fill_below_old_min": s3["all_fills_below_old_min"],
                "judgement": s3["judgement"],
                "note": "AUDIT-07 的 T6 只检查 audit_orders（信号生成后）；本轮补查 "
                        "populate_entry_trend 的 open>=min_entry（信号生成前）。结论相同但理由不同："
                        "全部 1397 个确认候选在旧/新下限下都在同一根 15m 触发。",
            },
            "sample_validity": {
                "universe": s4["universe"],
                "qualified_from_start": s4["qualified_from_start_200bars"],
                "qualified_late": s4["qualified_late_in_period"],
                "unqualified_whole_period": s4["unqualified_whole_period"],
                "trades_before_200bar_start": s4["trades_before_L1_q_start"],
                "trades_before_42bar_box": s4["trades_before_box_ready_42bars"],
                "affected_by_mode": s4["affected_by_mode"],
                "note": "4 个全程不合格币 0 成交；115 笔发生在 200 根 4h 资格起点之前，"
                        "但均晚于策略实际箱体需求（42 根 4h），未删除、未清洗重算。",
            },
            "evidence_gaps": [
                "多重比较后不显著：18 个主族对照 maxT p=0.189，Holm 无 <0.05；剔除贡献前 3 币后 p=0.114。",
                "样本内、已看样本：2021-12~2024-12；2025+ 留出期未打开，无独立验证。",
                "物理事件仅 613 个（C1 228 / C2 197），跨参数重复不能增加独立样本；块级检验功效有限。",
                "胜率/快速止损的改善是过滤/选择效应；共同成交事件上并无改善（胜率 -2.15pp，快速止损 +2.26pp）。",
                "执行模型只含手续费/资金费，未含滑点与冲击成本；延迟入场的 -215.85U（网格均值）可能被低估。",
                "3 槽位组合/仓位竞争效应只用 ITT=0 近似，未做账户级复利与资金占用模拟。",
                "115 笔交易早于 200 根 4h 资格起点（但晚于 42 根箱体），绝对收益口径需注明。",
            ],
            "next_experiment": "预注册的独立留出期 + 含滑点的执行模型 + 事件级（非网格加总）样本量/功效计算；"
                               "C1 作为唯一候选，C2 不作为候选。",
            "grade": "支持继续验证（仅 C1）",
        },
        "boundary": {
            "inputs_locked": "166/166 与 AUDIT-07 指纹一致",
            "no_rerun": "未调用 freqtrade、未重跑回测、未改策略/参数",
            "holdout": "未打开 2025+ 留出期",
            "account_result": "所有净额为网格/事件算术量，不是单一账户复利结果；未删除交易生成清洗后账户",
        },
    }
    jdump(os.path.join(OUT, "05_三问判定_v1.json"), verdict)

    rows = [
        {"问题": "C1 是否提高胜率及净期望且稳定",
         "结论": "净期望方向性提高；胜率仅'少交易'口径提高；稳定性方向一致但多重比较不显著",
         "档位": "支持继续验证",
         "关键数字": f"事件级 +{c1['pnl_diff']}U/候选 (95%CI {c1['pnl_ci_lo']}~{c1['pnl_ci_hi']}, p={c1['pnl_p']}); "
                     f"C1 {int((c1g.pnl_diff > 0).sum())}/{len(c1g)} 网格为正（主族 18/18）; "
                     f"maxT p={float(c1g.pnl_maxt_fwer_p.iloc[0])}"},
        {"问题": "C2 相对 C1 是否有增益",
         "结论": "无增益；额外过滤掉 C1 的净盈利交易，6/9 网格为负",
         "档位": "不支持",
         "关键数字": f"事件级 {c21['pnl_diff']}U/候选 (p={c21['pnl_p']}); "
                     f"9 组汇总 {verdict['Q2_C2相对C1是否有增益']['aggregate_u']['C2_minus_C1']}U; "
                     f"网格正/负 {verdict['Q2_C2相对C1是否有增益']['grid_sign']['positive_grids']}/"
                     f"{verdict['Q2_C2相对C1是否有增益']['grid_sign']['negative_grids']}"},
        {"问题": "入场下限 L−0.2A → P0 的实际影响",
         "结论": "0 个确认候选受影响；0 笔成交价低于旧/新下限",
         "档位": "无影响（已核对全部候选与拒单）",
         "关键数字": "C1 778 / C2 619 个确认候选，旧/新下限同一触发根；4 笔拒单均为高于 max_entry"},
        {"问题": "74 币逐币有效区间",
         "结论": "26 币起点合格、44 币期内后段合格、4 币全程不合格（0 成交）；115 笔早于 200 根 4h 起点但晚于 42 根箱体",
         "档位": "已标注，未删除",
         "关键数字": "受影响 C0 88 笔/-380.77U、C1 15 笔/-82.85U、C2 12 笔/-54.47U（算术标注）"},
    ]
    write_csv(pd.DataFrame(rows), "05_三问结论_v1.csv")
    print(json.dumps({k: v.get("grade") for k, v in verdict.items() if isinstance(v, dict)},
                     ensure_ascii=False, indent=1))
    print("完成 05")


if __name__ == "__main__":
    main()

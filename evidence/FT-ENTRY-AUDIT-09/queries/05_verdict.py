# -*- coding: utf-8 -*-
"""05 三问判定：把预注册规则套到 03/04 的结果上，输出唯一结论。

不在此处改阈值、不改口径；只读 03/04 的落盘摘要。
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
    s3 = load("03_重算与资格核对摘要_v1.json")
    s4 = load("04_统计摘要_v1.json")
    dec = s4["decisions"]
    c1, c2 = dec["C1"], dec["C2_vs_C1"]
    pooled = {r["contrast"]: r for r in s4["pooled"]}
    ms = pd.read_csv(os.path.join(OUT, "03_模式汇总独立重算_v1.csv"), encoding="utf-8-sig").set_index("mode")

    def mval(mode, col):
        return float(ms.loc[mode, col]) if mode in ms.index else None
    p1, p2, p21 = pooled["C1−C0"], pooled["C2−C0"], pooled["C2−C1"]

    overall = c1["grade"]
    verdict = {
        "Q1_统一资格后C1胜率与净期望改善是否仍成立": {
            "grade": c1["grade"],
            "net_expectancy": {
                "event_level_diff_u": p1["pnl_diff"],
                "ci95": [p1["pnl_ci_lo"], p1["pnl_ci_hi"]],
                "p": p1["pnl_p"],
                "grids_positive": f"{c1['grids_positive']}/9",
                "maxt_fwer_p": c1["maxt_p"],
                "holm_lt_005": c1["holm_lt_005"],
                "top1_coin_share_pct": c1["top1_coin_share_pct"],
                "drop_top5_mean_diff_u": c1["drop_top5_mean_diff_u"],
            },
            "win_rate": {
                "trade_level_C0": mval("C", "win_rate_pct"),
                "trade_level_C1": mval("C1", "win_rate_pct"),
                "event_level_conditional_C0": p1["win_rate_C"],
                "event_level_conditional_C1": p1["win_rate_C1"],
                "matched_diff_pp": p1["win_diff_matched"],
                "matched_p": p1["win_p"],
                "note": "成交口径胜率提高来自 C1 过滤；共同成交事件上匹配差为负（不显著）。",
            },
            "quick_stop": {
                "trade_level_C0": mval("C", "quick_stop_rate_pct"),
                "trade_level_C1": mval("C1", "quick_stop_rate_pct"),
                "event_level_conditional_C0": p1["qs_rate_C"],
                "event_level_conditional_C1": p1["qs_rate_C1"],
                "matched_diff_pp": p1["qs_diff_matched"],
                "matched_p": p1["qs_p"],
                "note": "匹配口径 C1 反而高 2.14pp（p=0.044）；下降来自不交易，非共同交易变好。",
            },
            "significance_caveat": ("主族 18 项 maxT p=0.2064、Holm 0/18；"
                                    "本档位依据预注册的池化 CI+网格方向+跨币/极值稳健性，"
                                    "含义是'值得进入独立验证'，不是'已证明族级显著'。"),
            "criteria": c1.get("criteria", {}),
        },
        "Q2_C2相对C1是否有稳定增益": {
            "grade": c2["grade"],
            "event_level_diff_u": p21["pnl_diff"],
            "ci95": [p21["pnl_ci_lo"], p21["pnl_ci_hi"]],
            "p": p21["pnl_p"],
            "grids_positive": f"{c2['grids_positive']}/9",
            "grids_negative": f"{c2['grids_negative']}/9",
            "maxt_fwer_p": c2.get("maxt_p"),
            "holm_lt_005": c2.get("holm_lt_005"),
            "decomposition": {"avoided_c1_pnl_u": c2["avoided_c1_pnl_u"],
                              "delay_effect_u": c2["delay_effect_u"]},
            "win_matched_diff_pp": p21["win_diff_matched"],
            "qs_matched_diff_pp": p21["qs_diff_matched"],
            "note": ("C2 在 Vs04 三格为正（2 格 Holm 显著），Vs06/Vs08 六格为负；"
                     "整体点估计为负、pooled 不显著，判不支持作为统一附加规则。"),
        },
        "Q3_是否值得进入独立验证": {
            "grade": overall,
            "answer": ("C1 达到预注册门槛，建议进入独立验证；C2 不作为候选。"
                       if overall == "支持进入独立验证" else
                       ("C1 方向性证据存在但未达预注册门槛，判证据不足；C2 不作为候选。"
                        if overall == "证据不足" else
                        "C1 未达预注册门槛，判不支持；C2 不作为候选。")),
            "qualification": s3,
            "limitations": [
                "样本内、已看样本；2025+ 留出期未打开，不构成独立验证。",
                "OI 5m→1h 映射与发布时点未证实（FT-ENTRY-03 L2=0）。",
                "执行模型只含手续费/资金费，未含滑点/冲击。",
                "块级重采样只处理时间聚集，未处理同期跨币相关。",
                "3 槽位组合/资金占用未做账户级复利；所有净额为网格/事件算术量。",
                "统一准入改变样本；与 FT-ENTRY-07 的差异不能直接归因于资格修正。",
            ],
        },
        "boundary": {
            "no_rerun": "未替反重力重跑回测；只读 FT-ENTRY-09 落盘结果",
            "no_new_params": "未追加参数；9 组 Vs/Z 网格冻结",
            "holdout": "未打开 2025+ 留出期",
            "account_result": "27 组净收益不相加作为单一账户表现",
        },
    }
    jdump(os.path.join(OUT, "05_三问判定_v1.json"), verdict)

    rows = [
        {"问题": "统一资格后 C1 的胜率/净期望改善是否仍成立",
         "档位": c1["grade"],
         "关键数字": f"事件级 {p1['pnl_diff']}U/候选 (95%CI {p1['pnl_ci_lo']}~{p1['pnl_ci_hi']}, "
                     f"p={p1['pnl_p']}); 网格 {c1['grids_positive']}/9 为正; maxT p={c1['maxt_p']}",
         "胜率/快速止损": (f"成交口径 {mval('C','win_rate_pct'):.2f}%→{mval('C1','win_rate_pct'):.2f}%；"
                          f"匹配差 {p1['win_diff_matched']}pp；快速止损 "
                          f"{mval('C','quick_stop_rate_pct'):.2f}%→{mval('C1','quick_stop_rate_pct'):.2f}%；"
                          f"匹配快速止损差 {p1['qs_diff_matched']}pp")},
        {"问题": "C2 相对 C1 是否有稳定增益",
         "档位": c2["grade"],
         "关键数字": f"事件级 {p21['pnl_diff']}U/候选 (p={p21['pnl_p']}); "
                     f"网格正/负 {c2['grids_positive']}/{c2['grids_negative']}",
         "机制": f"被 C2 过滤的 C1 交易净额 {c2['avoided_c1_pnl_u']}U；延迟效应 {c2['delay_effect_u']}U"},
        {"问题": "是否值得进入独立验证",
         "档位": overall,
         "关键数字": f"资格违规 {s3['qualification_violations']}；27 组完整 {s3['cells_found']}/27",
         "说明": "C1 为唯一候选；C2 不作为候选。保留样本内/数据时点/防泄漏限制。"},
    ]
    write_csv(pd.DataFrame(rows), "05_三问结论_v1.csv")
    print(json.dumps({"C1": c1["grade"], "C2": c2["grade"], "overall": overall},
                     ensure_ascii=False, indent=1))
    print("完成 05")


if __name__ == "__main__":
    main()

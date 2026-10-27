# -*- coding: utf-8 -*-
"""06 最终判定汇总：把工单四问转成可核验的判据表。

四问：
Q1 是否真正实现突破前成交？
Q2 是否提高准确率？
Q3 是否改善净期望，还是仅减少交易？
Q4 是否值得保留该约束，证据有什么限制？

判据（写死，不按结果调整）：
Q1  PASS = 04 的 A 路线全部成交严格 < 冻结 H（fills_at_or_above_H == 0）
            且 H 来源于已闭合 4h 箱体、成交时刻晚于信号时刻（causal_violations == 0）
            且被拒候选未出现在逐笔成交里（拒单不伪装成交）
Q2  判定为「提高」需同时满足：全部 9 格扣费后胜率 >= 03 对应格，
    且加权合计胜率上升；否则判「未提高」（并列出上升/下降的格数）
Q3  「改善净期望」需：全部 9 格净期望 >= 03 对应格，且块级 CI 下界 > 0 的格数 >= 1，
    且 Holm/maxT 校正后仍有显著格；「仅减少交易」= 笔数下降但净期望未全部改善
Q4  综合：PASS 需 Q1 PASS 且 Q2/Q3 同时成立；任何一环不成立则判
    「不值得保留」或「证据不足以支持保留」

只读，不重跑回测。
"""
from __future__ import annotations

import os
import sys
from typing import List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import OUT, jdump, write_csv  # noqa: E402


def read(name: str) -> pd.DataFrame:
    p = os.path.join(OUT, name)
    if not os.path.isfile(p):
        return pd.DataFrame()
    return pd.read_csv(p, encoding="utf-8-sig")


def main() -> None:
    aud = read("03_成交约束真实性核验_v1.csv")
    mm = read("03_独立重算指标_v1.csv")
    att = read("04_拒单归因与净差异分解_v1.csv")
    ident = read("04_净差异恒等式核验_v1.csv")
    comp = read("04_仓位竞争双向检查_v1.csv")
    st = read("05_成交约束块级统计_v1.csv")
    lad = read("05_极端盈利单剔除阶梯_v1.csv")
    conc = read("05_跨币集中度_v1.csv")

    verdicts: List[dict] = []

    # ── Q1：是否真正实现突破前成交（权威核验源 = 07 审计日志 × trades CSV 交叉） ──
    q1_auth = read("07_Q1审计日志权威核验_v1.csv")
    q1_rows = []
    for _, r in q1_auth.iterrows():
        if r.get("route") != "A":
            continue
        q1_rows.append({
            "code": r["code"], "strategy": r["strategy"],
            "trades_04": int(r["trades"]),
            "audit_passed": int(r["audit_passed"]),
            "rej_fill_ge_H": int(r["rej_fill_ge_H"]),
            "trade_in_audit": int(r["trade_in_audit"]),
            "trade_pass_lt_H": int(r["trade_pass_lt_H"]),
            "trade_fill_mismatch": int(r["trade_fill_mismatch"]),
            "rejected_in_trades": int(r["rejected_in_trades"]),
            "Q1_pass": bool(r["Q1_pass"]),
        })
    q1 = pd.DataFrame(q1_rows)
    write_csv(q1, "06_Q1_突破前成交实现核验_v1.csv")
    q1_pass = bool(q1["Q1_pass"].all()) if not q1.empty else None

    # 拒单伪装成交（来自审计日志交叉核验的 rejected_in_trades 汇总）
    n_leak = int(q1_auth[q1_auth["route"] == "A"]["rejected_in_trades"].sum()) if not q1_auth.empty else None

    # ── Q2 / Q3：胜率与净期望 ──
    # 03 对照（旧版无约束）的独立重算指标取自 02 静态反事实预测表（内含 03 重算值 *_03 列）；
    # 04 实际（新约束）取自 03 独立重算指标表（mm）。
    pred = read("02_静态反事实预测_v1.csv")
    if not mm.empty and not pred.empty:
        cmp = mm.merge(pred, on="code", how="left", suffixes=("", "_pred"))
        cmp = cmp.merge(att[["code", "n_rejected", "n_added", "n_kept"]]
                        if not att.empty else pd.DataFrame({"code": []}),
                        on="code", how="left")
        cmp["win_rate_delta_pp"] = (cmp["win_rate_pct"] - cmp["win_rate_pct_03"]).round(4)
        cmp["expectancy_delta_u"] = (cmp["expectancy_u"] - cmp["expectancy_u_03"]).round(4)
        cmp["net_delta_u"] = (cmp["net_pnl_u"] - cmp["net_pnl_u_03"]).round(4)
        cmp["trades_delta"] = (cmp["trades"] - cmp["trades_03"]).astype("Int64")
        cmp["wins_03"] = cmp["wins_03"]
        cmp["trades_03"] = cmp["trades_03"]
        write_csv(cmp, "06_新旧对比全指标_v1.csv")

        a_only = cmp[cmp["route"] == "A"]
        n_up = int((a_only["win_rate_delta_pp"] > 0).sum())
        n_down = int((a_only["win_rate_delta_pp"] < 0).sum())
        n_flat = int((a_only["win_rate_delta_pp"] == 0).sum())
        w3 = a_only["wins_03"].sum() if "wins_03" in a_only else None
        t3 = a_only["trades_03"].sum() if "trades_03" in a_only else None
        # 合计口径：按逐笔汇总
        sum3 = float(a_only["net_pnl_u_03"].sum())
        sum4 = float(a_only["net_pnl_u"].sum())
        tot3 = int(a_only["trades_03"].sum())
        tot4 = int(a_only["trades"].sum())
        wsum3 = int(a_only["wins_03"].sum()) if "wins_03" in a_only else None
        wsum4 = int(a_only["wins"].sum()) if "wins" in a_only else None
        wr3 = round(100 * wsum3 / tot3, 4) if wsum3 is not None and tot3 else None
        wr4 = round(100 * wsum4 / tot4, 4) if wsum4 is not None and tot4 else None
        exp3 = round(sum3 / tot3, 4) if tot3 else None
        exp4 = round(sum4 / tot4, 4) if tot4 else None

        q2_pass = bool(n_up == len(a_only) and n_down == 0 and
                       (wr4 is not None and wr3 is not None and wr4 > wr3))
        q3_all_improve = bool((a_only["expectancy_delta_u"] >= 0).all())
        n_sig = int(st["boot_ci_excludes_0"].sum()) if not st.empty else None
        n_holm = int((st["holm_p"] < 0.05).sum()) if not st.empty else None
        maxt = float(st["maxt_fwer_p"].iloc[0]) if not st.empty and "maxt_fwer_p" in st else None
        q3_pass = bool(q3_all_improve and (n_sig or 0) >= 1 and (n_holm or 0) >= 1)
        only_reduced = bool((tot4 < tot3) and not q3_all_improve)
    else:
        q2_pass = q3_pass = only_reduced = None
        n_up = n_down = n_flat = None
        wr3 = wr4 = exp3 = exp4 = None
        tot3 = tot4 = None
        sum3 = sum4 = None
        n_sig = n_holm = None
        maxt = None
        cmp = pd.DataFrame()

    # ── Q4：证据限制清单 ──
    limits = [
        "OI 发布时点全程 PitMode.DECLARED（桶结束即当可用），无 fetched_at 证据 ⇒ PIT 未证，只能算探索性",
        "历史规格未满足：官方无历史规格归档，576 次运行 0 verified ⇒ 正式历史验证不可得",
        "研究期 2021-12-01~2024-12-31 已被 FT-ENTRY-02/03 反复观察，样本外区间不存在（OOS-READY-01）",
        "回撤口径只有已实现权益回撤，不含浮亏，是下界，不得用于风险预算结论",
        "L2 完整策略资格为 0：全池 OI 1h 因子无合格区间，241 个 L1 候选均非完整策略合格",
        "本轮样本池 70 币，其中仅 26 个覆盖研究期全期；54 个币在 9 格 A 里 0 笔",
        "本审核为只读审核，不重跑回测；04 的实际结果由反重力产出，审核只做独立重算与对账",
    ]
    if not ident.empty:
        limits.append(f"净差异恒等式全部成立（残差恒 0）：{bool(ident['identity_holds'].all())}")
    if not comp.empty:
        n_comp = int(len(comp[comp.get("was_slot_full_03") == True])) if "was_slot_full_03" in comp else 0
        limits.append(f"仓位竞争双向检查记录 {len(comp)} 条，其中 03 侧满仓相关 {n_comp} 条")

    q4_pass = bool(q1_pass and q2_pass and q3_pass)
    if q4_pass:
        q4_text = "值得保留：突破前成交真实实现，准确率与净期望同时改善且统计显著"
    elif q1_pass and not q2_pass and not q3_pass:
        q4_text = "不值得保留：约束实现属实，但未提高准确率、未改善净期望（部分格显著变差）"
    elif q1_pass and only_reduced:
        q4_text = "不值得保留：仅减少交易，净期望未改善"
    elif q1_pass:
        q4_text = "证据不足以支持保留：约束实现属实，但改善不完整或统计不显著"
    else:
        q4_text = "不予采信：突破前成交未真实实现"

    summary = {
        "Q1_是否真正实现突破前成交": {
            "判定": ("PASS" if q1_pass else "FAIL"),
            "依据": {
                "A路线全部成交严格小于冻结H": q1.to_dict("records") if not q1.empty else None,
                "被拒候选出现在逐笔成交中的条数": n_leak,
                "H来源": "cand.box_high_h（回测运行时由箱体形成逻辑冻结，成交时刻晚于信号时刻）；权威核验 = FT-ENTRY-04 运行期写出的 audit_orders_*.csv 与 freqtrade trades CSV 交叉对账",
            },
        },
        "Q2_是否提高准确率": {
            "判定": ("是" if q2_pass else "否"),
            "上升格数": n_up, "下降格数": n_down, "持平格数": n_flat,
            "合计胜率_03": wr3, "合计胜率_04": wr4,
        },
        "Q3_是否改善净期望还是仅减少交易": {
            "判定": ("改善净期望" if q3_pass else ("仅减少交易" if only_reduced else "未改善")),
            "全部9格净期望均不劣": q3_all_improve,
            "块级CI排除0的格数": n_sig, "Holm<0.05的格数": n_holm, "maxT族错误率p": maxt,
            "合计净期望_03": exp3, "合计净期望_04": exp4,
            "合计净收益_03": sum3, "合计净收益_04": sum4,
            "合计笔数_03": tot3, "合计笔数_04": tot4,
        },
        "Q4_是否值得保留": {
            "判定": ("保留" if q4_pass else "不保留"),
            "结论": q4_text,
            "证据限制": limits,
        },
    }
    jdump(os.path.join(OUT, "06_最终判定_v1.json"), summary)

    verdicts.append({"问题": "Q1 是否真正实现突破前成交？", "判定": summary["Q1_是否真正实现突破前成交"]["判定"]})
    verdicts.append({"问题": "Q2 是否提高准确率？", "判定": summary["Q2_是否提高准确率"]["判定"]})
    verdicts.append({"问题": "Q3 是否改善净期望，还是仅减少交易？", "判定": summary["Q3_是否改善净期望还是仅减少交易"]["判定"]})
    verdicts.append({"问题": "Q4 是否值得保留该约束？", "判定": summary["Q4_是否值得保留"]["判定"]})
    vdf = pd.DataFrame(verdicts)
    write_csv(vdf, "06_四问判定_v1.csv")

    print("== Q1 突破前成交实现核验 ==")
    print(q1.to_string(index=False) if not q1.empty else "（无数据）")
    print(f"被拒候选出现在逐笔成交中的条数 = {n_leak}")
    print("\n== 新旧对比 ==")
    if not cmp.empty:
        print(cmp[["code", "trades_03", "trades", "win_rate_pct_03", "win_rate_pct",
                   "win_rate_delta_pp", "expectancy_u_03", "expectancy_u",
                   "expectancy_delta_u", "net_pnl_u_03", "net_pnl_u", "net_delta_u",
                   "n_rejected"]].to_string(index=False))
    print("\n== 四问判定 ==")
    for _, r in vdf.iterrows():
        print(f"  {r['问题']} => {r['判定']}")
    print(f"\nQ4 结论：{q4_text}")
    print("\n完成")


if __name__ == "__main__":
    main()

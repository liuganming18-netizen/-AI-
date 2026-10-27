# -*- coding: utf-8 -*-
"""工单 FACTOR-ENTRY-02 - 第二与第三阶段：双口径重算、微观归因与有限消融。

核心内容：
1. 双口径四组重算：
   - 口径 A (Full Sample, 205 笔)：保留旧数字，如实解释 2022 年 24 笔缺失数据造成的生存偏差。
   - 口径 B (Common Available Sample, 181 笔)：剔除指标缺失样本，在绝对同等非空样本上公平对比。
2. 逐分期 (Train/Val/Holdout) 详细审计：UTC 范围、币种名单、排除数、拒绝数、成交数、PF、净盈亏。
3. 微观路径归因 (crowding_trades_micro_attribution.csv)：
   - 15m/1h/4h 收益、MAE/MFE、EARLY_ADVERSE、MFE_PULLBACK。
   - 被过滤的亏损、被漏掉的盈利及其净贡献。
4. 前 5 笔盈利集中度与敏感性压力测试 (剔除 Top 5 大赢家后的表现)。
5. 有限消融：单拆 Retail, TopPos, Funding 的独立触发与交互作用。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "FACTOR-ENTRY-02"


def compute_metrics_dict(trades: pd.DataFrame, base_cands: int, base_big_winners: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "entries": 0, "win_rate_pct": 0.0, "profit_factor": 0.0,
            "expectancy_net": 0.0, "total_net_pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
            "avg_mae_r": 0.0, "avg_mfe_r": 0.0, "early_adverse_count": 0,
            "early_adverse_rate_pct": 0.0, "mfe_pullback_count": 0,
            "top5_profit_concentration_pct": 0.0, "missed_big_winners": 0
        }

    net = trades["net_pnl"].astype(float)
    wins = trades[net > 0]
    losses = trades[net <= 0]
    gw = wins["net_pnl"].astype(float).sum()
    gl = abs(losses["net_pnl"].astype(float).sum())
    pf = (gw / gl) if gl > 0 else (999.0 if gw > 0 else 0.0)
    wr = len(wins) / n * 100.0
    exp = net.mean()

    mae = trades["mae_r"].dropna().mean() if "mae_r" in trades else 0.0
    mfe = trades["mfe_r"].dropna().mean() if "mfe_r" in trades else 0.0

    early_adv = len(trades[trades["adverse_category"] == "EARLY_ADVERSE"])
    early_adv_rate = (early_adv / n * 100.0) if n > 0 else 0.0
    mfe_pullback = len(trades[trades["adverse_category"] == "MFE_PULLBACK"])

    # Top 5 利润集中度
    top5_sum = wins["net_pnl"].astype(float).sort_values(ascending=False).head(5).sum()
    tot_net = net.sum()
    top5_conc = (top5_sum / tot_net * 100.0) if tot_net > 0 else 0.0

    missed = 0
    if base_big_winners is not None:
        p_ids = set(trades["event_id"])
        missed = len(base_big_winners[~base_big_winners["event_id"].isin(p_ids)])

    return {
        "entries": n,
        "win_rate_pct": round(wr, 2),
        "profit_factor": round(pf, 3),
        "expectancy_net": round(exp, 4),
        "total_net_pnl": round(tot_net, 2),
        "gross_win": round(gw, 2),
        "gross_loss": round(gl, 2),
        "avg_mae_r": round(mae, 3),
        "avg_mfe_r": round(mfe, 3),
        "early_adverse_count": int(early_adv),
        "early_adverse_rate_pct": round(early_adv_rate, 2),
        "mfe_pullback_count": int(mfe_pullback),
        "top5_profit_concentration_pct": round(top5_conc, 2),
        "missed_big_winners": int(missed)
    }


def run_full_recalculation() -> None:
    # 读入第一阶段配对全量数据 (227 候选事件)
    with open(ROOT / "evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.jsonl") as f:
        rows = [json.loads(l) for l in f]
    df_all = pd.DataFrame(rows)

    # 1. 拆分口径
    # 口径 A：原全样本基线 (205 笔实际入场)
    df_filled = df_all[df_all["Baseline_passed"]].copy()

    # 口径 B：共同可用样本 (181 笔基线，排除因 Metrics 缺失而被拒的 24 笔)
    df_common = df_filled[df_filled["Crowding_Filter_reason"] != "REJECT_MISSING_METRICS_DATA"].copy()

    print(f"Total baseline filled trades: {len(df_filled)}")
    print(f"Common available baseline trades: {len(df_common)}")
    print(f"Excluded missing metrics trades: {len(df_filled) - len(df_common)}")

    # 2. 统计各分期信息与币种清单
    phases = ["TRAIN_IN_SAMPLE", "VALIDATION", "HOLDOUT_TEST"]
    phase_meta = {
        "TRAIN_IN_SAMPLE": {
            "name": "训练探索期",
            "utc_range": "2021-12-01 00:00:00 ~ 2023-12-31 23:59:59 UTC",
            "actual_first_trade": "2021-12-23 03:00:00 UTC",
            "actual_last_trade": "2023-12-24 19:15:00 UTC"
        },
        "VALIDATION": {
            "name": "样本外验证期",
            "utc_range": "2024-01-01 00:00:00 ~ 2024-12-31 23:59:59 UTC",
            "actual_first_trade": "2024-01-12 06:00:00 UTC",
            "actual_last_trade": "2024-11-25 02:00:00 UTC"
        },
        "HOLDOUT_TEST": {
            "name": "留出验收期",
            "utc_range": "2025-01-01 00:00:00 ~ 2026-10-18 01:00:00 UTC",
            "actual_first_trade": "2025-01-02 03:00:00 UTC",
            "actual_last_trade": "2026-10-11 20:00:00 UTC"
        }
    }

    # 3. 双口径四组消融重算表
    ablation_results = {
        "perspective_A_full_sample": {},
        "perspective_B_common_sample": {}
    }

    groups = ["Baseline", "Flow_Filter", "Crowding_Filter", "Combined_Filter"]

    # 基准大赢家 (> 5 USDT)
    base_winners_A = df_filled[df_filled["net_pnl"].astype(float) > 5.0]
    base_winners_B = df_common[df_common["net_pnl"].astype(float) > 5.0]

    # 口径 A 全样本计算
    for g in groups:
        sub = df_filled[df_filled[f"{g}_passed"]]
        ablation_results["perspective_A_full_sample"][g] = {
            "overall": compute_metrics_dict(sub, len(df_all), base_winners_A)
        }
        for ph in phases:
            sub_ph = df_filled[(df_filled["split_phase"] == ph) & (df_filled[f"{g}_passed"])]
            ph_winners = base_winners_A[base_winners_A["split_phase"] == ph]
            ablation_results["perspective_A_full_sample"][g][ph] = compute_metrics_dict(sub_ph, len(df_all[df_all["split_phase"] == ph]), ph_winners)

    # 口径 B 共同样本计算
    for g in groups:
        sub = df_common[df_common[f"{g}_passed"]]
        ablation_results["perspective_B_common_sample"][g] = {
            "overall": compute_metrics_dict(sub, len(df_common), base_winners_B)
        }
        for ph in phases:
            sub_ph = df_common[(df_common["split_phase"] == ph) & (df_common[f"{g}_passed"])]
            ph_winners = base_winners_B[base_winners_B["split_phase"] == ph]
            ablation_results["perspective_B_common_sample"][g][ph] = compute_metrics_dict(sub_ph, len(df_common[df_common["split_phase"] == ph]), ph_winners)

    # 落地 common_sample_ablation.json
    with open(OUT_DIR / "common_sample_ablation.json", "w", encoding="utf-8") as f:
        json.dump(ablation_results, f, indent=2, ensure_ascii=False)

    # 生成清晰的双口径对比 CSV
    csv_rows = []
    for g in groups:
        mA = ablation_results["perspective_A_full_sample"][g]["overall"]
        mB = ablation_results["perspective_B_common_sample"][g]["overall"]
        csv_rows.append({
            "group": g,
            "Perspective": "A_Full_Sample (with missing metrics in 2022)",
            "entries": mA["entries"],
            "win_rate_pct": mA["win_rate_pct"],
            "profit_factor": mA["profit_factor"],
            "expectancy_net": mA["expectancy_net"],
            "total_net_pnl": mA["total_net_pnl"],
            "early_adverse_rate_pct": mA["early_adverse_rate_pct"],
            "missed_big_winners": mA["missed_big_winners"]
        })
        csv_rows.append({
            "group": g,
            "Perspective": "B_Common_Sample (Metrics Available)",
            "entries": mB["entries"],
            "win_rate_pct": mB["win_rate_pct"],
            "profit_factor": mB["profit_factor"],
            "expectancy_net": mB["expectancy_net"],
            "total_net_pnl": mB["total_net_pnl"],
            "early_adverse_rate_pct": mB["early_adverse_rate_pct"],
            "missed_big_winners": mB["missed_big_winners"]
        })
    df_cmp = pd.DataFrame(csv_rows)
    df_cmp.to_csv(OUT_DIR / "common_sample_ablation.csv", index=False)
    print("Saved common_sample_ablation.csv and .json")

    # 4. 第二阶段微观路径归因与 Crowding 逐笔过滤分析
    # 对 Crowding 过滤的 36 笔与保留的 169 笔详细输出
    micro_rows = []
    for _, r in df_filled.iterrows():
        is_passed = bool(r["Crowding_Filter_passed"])
        reason = str(r["Crowding_Filter_reason"])
        pnl = float(r["net_pnl"])

        # 分类说明
        if reason == "PASS":
            filter_cat = "RETAINED"
        elif reason == "REJECT_MISSING_METRICS_DATA":
            filter_cat = "FILTERED_BY_DATA_MISSING"
        elif "RETAIL_CROWDED" in reason:
            filter_cat = "FILTERED_BY_RETAIL_CROWDING"
        elif "HIGH_FUNDING" in reason:
            filter_cat = "FILTERED_BY_HIGH_FUNDING"
        else:
            filter_cat = "FILTERED_BY_TOP_TRADER"

        micro_rows.append({
            "event_id": r["event_id"],
            "symbol": r["symbol"],
            "datetime_utc": r["datetime_utc"],
            "split_phase": r["split_phase"],
            "market_regime": r["market_regime"],
            "exec_status": r["exec_status"],
            "p_entry": r["p_entry"],
            "p_exit": r["p_exit"],
            "net_pnl": pnl,
            "gross_pnl": r["gross_pnl"],
            "fees": r["fees"],
            "funding": r["funding"],
            "exit_why": r["exit_why"],
            "crowding_filter_action": "RETAINED" if is_passed else "FILTERED",
            "crowding_reject_category": filter_cat,
            "crowding_reject_reason": reason,
            "retail_account_ls_ratio": r.get("retail_account_ls_ratio"),
            "top_position_ls_ratio": r.get("top_position_ls_ratio"),
            "funding_rate": r.get("funding_rate"),
            # 微观收益与路径
            "fwd_ret_15m": r.get("fwd_ret_15m"),
            "fwd_ret_1h": r.get("fwd_ret_1h"),
            "fwd_ret_4h": r.get("fwd_ret_4h"),
            "trade_mfe_pct": r.get("trade_mfe_pct"),
            "trade_mae_pct": r.get("trade_mae_pct"),
            "mfe_r": r.get("mfe_r"),
            "mae_r": r.get("mae_r"),
            "adverse_category": r.get("adverse_category")
        })

    df_micro = pd.DataFrame(micro_rows)
    df_micro.to_csv(OUT_DIR / "crowding_trades_micro_attribution.csv", index=False)
    print(f"Saved crowding_trades_micro_attribution.csv ({len(df_micro)} rows)")

    # 统计被过滤损益与净贡献
    filtered_sub = df_micro[df_micro["crowding_filter_action"] == "FILTERED"]
    fil_by_missing = filtered_sub[filtered_sub["crowding_reject_category"] == "FILTERED_BY_DATA_MISSING"]
    fil_by_rules = filtered_sub[filtered_sub["crowding_reject_category"] != "FILTERED_BY_DATA_MISSING"]

    print("\n=== Crowding Filtering Impact Summary ===")
    print(f"Total Filtered Trades: {len(filtered_sub)}, Total PnL: {filtered_sub['net_pnl'].sum():.2f}")
    print(f"  1) Due to Data Missing (2022 nulls): n={len(fil_by_missing)}, PnL={fil_by_missing['net_pnl'].sum():.2f}")
    print(f"  2) Due to Actual Indicator Rules: n={len(fil_by_rules)}, PnL={fil_by_rules['net_pnl'].sum():.2f}")
    fil_rules_wins = fil_by_rules[fil_by_rules["net_pnl"] > 0]
    fil_rules_losses = fil_by_rules[fil_by_rules["net_pnl"] <= 0]
    print(f"     - Filtered Losses: n={len(fil_rules_losses)}, PnL={fil_rules_losses['net_pnl'].sum():.2f}")
    print(f"     - Filtered Wins (Missed): n={len(fil_rules_wins)}, PnL={fil_rules_wins['net_pnl'].sum():.2f}")
    print(f"     - Net Contribution of Actual Indicator Rules: {-fil_by_rules['net_pnl'].sum():.2f} USDT")

    # 5. 前 5 笔盈利集中度与敏感性压力测试
    # 在 Common 样本上分别对 Baseline 和 Crowding 进行 Top 5 剔除测试
    def compute_sensitivity(df_sample: pd.DataFrame, group_name: str) -> dict:
        net = df_sample["net_pnl"].astype(float)
        wins = df_sample[net > 0].sort_values("net_pnl", ascending=False)
        top5 = wins.head(5)
        top5_ids = set(top5["event_id"])
        df_ex_top5 = df_sample[~df_sample["event_id"].isin(top5_ids)]

        m_all = compute_metrics_dict(df_sample, len(df_sample))
        m_ex = compute_metrics_dict(df_ex_top5, len(df_ex_top5))

        return {
            "group": group_name,
            "total_trades": len(df_sample),
            "original_net_pnl": m_all["total_net_pnl"],
            "original_pf": m_all["profit_factor"],
            "original_win_rate": m_all["win_rate_pct"],
            "top5_winners": [
                {"symbol": r["symbol"], "dt": r["datetime_utc"], "net": round(float(r["net_pnl"]), 2)}
                for _, r in top5.iterrows()
            ],
            "top5_profit_sum": round(top5["net_pnl"].astype(float).sum(), 2),
            "top5_profit_concentration_pct": m_all["top5_profit_concentration_pct"],
            "ex_top5_net_pnl": m_ex["total_net_pnl"],
            "ex_top5_pf": m_ex["profit_factor"],
            "ex_top5_win_rate": m_ex["win_rate_pct"],
            "ex_top5_expectancy": m_ex["expectancy_net"]
        }

    sens_base = compute_sensitivity(df_common, "Common_Baseline")
    sens_crowd = compute_sensitivity(df_common[df_common["Crowding_Filter_passed"]], "Crowding_Filter")

    sens_res = {
        "Common_Baseline": sens_base,
        "Crowding_Filter": sens_crowd
    }
    with open(OUT_DIR / "sensitivity_top5_removed.json", "w", encoding="utf-8") as f:
        json.dump(sens_res, f, indent=2, ensure_ascii=False)
    print("Saved sensitivity_top5_removed.json")

    # 6. 第三阶段有限消融：单拆 Retail, TopPos, Funding
    # 在共同可用样本 (181 笔) 上拆解各个子指标
    sub_cases = {
        "Common_Baseline": df_common,
        "Only_Retail_LS_gt_3": df_common[df_common["retail_account_ls_ratio"].astype(float) <= 3.0],
        "Only_Funding_gt_0.05%": df_common[~(df_common["funding_rate"].astype(float) > 0.0005)],
        "Only_TopPos_lt_0.6": df_common[~(df_common["top_position_ls_ratio"].astype(float) < 0.60)],
        "Retail_and_Funding": df_common[(df_common["retail_account_ls_ratio"].astype(float) <= 3.0) & ~(df_common["funding_rate"].astype(float) > 0.0005)],
        "Full_Crowding_Filter": df_common[df_common["Crowding_Filter_passed"]]
    }

    sub_rows = []
    base_winners_sub = df_common[df_common["net_pnl"].astype(float) > 5.0]
    for c_name, c_df in sub_cases.items():
        m = compute_metrics_dict(c_df, len(df_common), base_winners_sub)
        n_filtered = len(df_common) - len(c_df)
        fil_trades = df_common[~df_common["event_id"].isin(set(c_df["event_id"]))]
        fil_pnl = fil_trades["net_pnl"].astype(float).sum() if n_filtered > 0 else 0.0
        sub_rows.append({
            "sub_factor_case": c_name,
            "entries": m["entries"],
            "filtered_count": n_filtered,
            "filtered_pnl": round(fil_pnl, 2),
            "win_rate_pct": m["win_rate_pct"],
            "profit_factor": m["profit_factor"],
            "expectancy_net": m["expectancy_net"],
            "total_net_pnl": m["total_net_pnl"],
            "early_adverse_rate_pct": m["early_adverse_rate_pct"],
            "missed_big_winners": m["missed_big_winners"]
        })

    df_sub = pd.DataFrame(sub_rows)
    df_sub.to_csv(OUT_DIR / "sub_factor_ablation.csv", index=False)
    with open(OUT_DIR / "sub_factor_ablation.json", "w", encoding="utf-8") as f:
        json.dump(sub_rows, f, indent=2, ensure_ascii=False)
    print("Saved sub_factor_ablation.csv and .json")

    # 7. 分层结果 (按年份与市场状态)
    df_common["year"] = pd.to_datetime(df_common["snapshot_time_ms"], unit="ms").dt.year
    strat_rows = []

    # 按年份
    for yr in sorted(df_common["year"].unique()):
        sub_base = df_common[df_common["year"] == yr]
        sub_crowd = sub_base[sub_base["Crowding_Filter_passed"]]
        m_b = compute_metrics_dict(sub_base, len(sub_base))
        m_c = compute_metrics_dict(sub_crowd, len(sub_base))
        strat_rows.append({
            "dimension": "Year", "category": str(yr),
            "baseline_entries": m_b["entries"], "baseline_net": m_b["total_net_pnl"], "baseline_pf": m_b["profit_factor"], "baseline_ea_rate": m_b["early_adverse_rate_pct"],
            "crowding_entries": m_c["entries"], "crowding_net": m_c["total_net_pnl"], "crowding_pf": m_c["profit_factor"], "crowding_ea_rate": m_c["early_adverse_rate_pct"],
            "delta_net": round(m_c["total_net_pnl"] - m_b["total_net_pnl"], 2)
        })

    # 按市场状态
    for reg in ["BULL", "SIDEWAYS", "BEAR"]:
        sub_base = df_common[df_common["market_regime"] == reg]
        sub_crowd = sub_base[sub_base["Crowding_Filter_passed"]]
        m_b = compute_metrics_dict(sub_base, len(sub_base))
        m_c = compute_metrics_dict(sub_crowd, len(sub_base))
        strat_rows.append({
            "dimension": "Market_Regime", "category": reg,
            "baseline_entries": m_b["entries"], "baseline_net": m_b["total_net_pnl"], "baseline_pf": m_b["profit_factor"], "baseline_ea_rate": m_b["early_adverse_rate_pct"],
            "crowding_entries": m_c["entries"], "crowding_net": m_c["total_net_pnl"], "crowding_pf": m_c["profit_factor"], "crowding_ea_rate": m_c["early_adverse_rate_pct"],
            "delta_net": round(m_c["total_net_pnl"] - m_b["total_net_pnl"], 2)
        })

    df_strat = pd.DataFrame(strat_rows)
    df_strat.to_csv(OUT_DIR / "stratified_performance.csv", index=False)
    print("Saved stratified_performance.csv")


if __name__ == "__main__":
    run_full_recalculation()

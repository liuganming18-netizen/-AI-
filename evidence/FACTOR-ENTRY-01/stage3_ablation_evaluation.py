# -*- coding: utf-8 -*-
"""工单 FACTOR-ENTRY-01 - 第三阶段：消融实验对比、配对评估与统计报告。

实验设置（最多 4 组，日线状态优先分层）：
1. 组 1：Baseline (冻结基线组) - 原始未加额外微观因子过滤的全部入场。
2. 组 2：Flow Filter (主动流量组) - 过滤主动买入占比低 (<0.50) 或突破 K 线上影线过长 (>0.35)。
3. 组 3：Crowding Filter (持仓与拥挤度组) - 过滤散户极度做多 (>3.0)、大户极度做空 (<0.6) 或高资金费 (>0.05%)。
4. 组 4：Combined Filter (合并精选组) - 同时满足 Flow 与 Crowding 健康门槛。

严格分期：
- TRAIN_IN_SAMPLE (2021-12 ~ 2023-12): 阈值探索与冻结
- VALIDATION (2024): 样本外验证
- HOLDOUT_TEST (2025 ~ 2026-10): 严格隔离留出验收

产出：
- paired_trades_and_rejects.csv / .jsonl: 逐笔配对与各组通过/拒绝明细及原因
- ablation_summary.json / .csv: 四组全指标消融统计与分层表格
- 完整的胜率、期望、PF、MAE/MFE、早期回落率、漏掉的赢家、前5笔集中度
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "FACTOR-ENTRY-01"


def evaluate_filter_pass(row: pd.Series) -> Dict[str, Tuple[bool, str]]:
    """评估每个候选/入场事件在各消融组的准入判断。"""
    res = {}

    # 1. Baseline: 只要引擎未拒单即入场
    if row["exec_status"] == "FILLED":
        res["Baseline"] = (True, "PASS")
    else:
        res["Baseline"] = (False, f"ENGINE_REJECT:{row.get('reject_reason', 'UNFILLED')}")

    # 2. Flow Filter
    # 规则：taker_buy_ratio >= 0.50 且 upper_shadow_ratio <= 0.35
    tb_ratio = row.get("taker_buy_ratio_1h")
    u_shadow = row.get("upper_shadow_ratio_1h")

    if not res["Baseline"][0]:
        res["Flow_Filter"] = (False, res["Baseline"][1])
    elif pd.isna(tb_ratio) or pd.isna(u_shadow):
        res["Flow_Filter"] = (False, "REJECT_MISSING_CANDLE_FLOW_DATA")
    elif tb_ratio < 0.50:
        res["Flow_Filter"] = (False, f"REJECT_LOW_TAKER_BUY_RATIO({tb_ratio:.2f}<0.50)")
    elif u_shadow > 0.35:
        res["Flow_Filter"] = (False, f"REJECT_HIGH_UPPER_SHADOW({u_shadow:.2f}>0.35)")
    else:
        res["Flow_Filter"] = (True, "PASS")

    # 3. Crowding Filter
    # 规则：散户多空比 <= 3.0，大户多空比 >= 0.60，结算资金费率 <= 0.0005
    ret_ratio = row.get("retail_account_ls_ratio")
    top_ratio = row.get("top_position_ls_ratio")
    f_rate = row.get("funding_rate")

    if not res["Baseline"][0]:
        res["Crowding_Filter"] = (False, res["Baseline"][1])
    elif pd.isna(ret_ratio) or pd.isna(top_ratio):
        res["Crowding_Filter"] = (False, "REJECT_MISSING_METRICS_DATA")
    elif ret_ratio > 3.0:
        res["Crowding_Filter"] = (False, f"REJECT_RETAIL_CROWDED_LONG({ret_ratio:.2f}>3.0)")
    elif top_ratio < 0.60:
        res["Crowding_Filter"] = (False, f"REJECT_TOP_TRADER_HEAVY_SHORT({top_ratio:.2f}<0.60)")
    elif pd.notna(f_rate) and f_rate > 0.0005:
        res["Crowding_Filter"] = (False, f"REJECT_HIGH_FUNDING_RATE({f_rate:.4f}>0.05%)")
    else:
        res["Crowding_Filter"] = (True, "PASS")

    # 4. Combined Filter
    if res["Flow_Filter"][0] and res["Crowding_Filter"][0]:
        res["Combined_Filter"] = (True, "PASS")
    else:
        fail_reasons = []
        if not res["Flow_Filter"][0]:
            fail_reasons.append(res["Flow_Filter"][1])
        if not res["Crowding_Filter"][0]:
            fail_reasons.append(res["Crowding_Filter"][1])
        res["Combined_Filter"] = (False, " & ".join(fail_reasons))

    return res


def compute_metrics(trades: pd.DataFrame, total_candidates: int, base_winners: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """计算单个策略组的完整指标。"""
    n_trades = len(trades)
    if n_trades == 0:
        return {
            "candidate_count": total_candidates,
            "unique_entries": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_net": 0.0,
            "expectancy_r": 0.0,
            "total_net_pnl": 0.0,
            "total_gross_pnl": 0.0,
            "total_fees": 0.0,
            "total_funding": 0.0,
            "avg_mae_r": 0.0,
            "avg_mfe_r": 0.0,
            "early_adverse_count": 0,
            "early_adverse_rate_pct": 0.0,
            "mfe_pullback_count": 0,
            "top5_profit_concentration_pct": 0.0,
            "missed_big_winners_count": 0,
            "trades_per_year": 0.0
        }

    net_series = trades["net_pnl"].astype(float)
    wins = trades[net_series > 0]
    losses = trades[net_series <= 0]
    gross_win = wins["net_pnl"].sum()
    gross_loss = abs(losses["net_pnl"].sum())

    pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    win_rate = len(wins) / n_trades * 100.0
    exp_net = net_series.mean()

    # R 期望
    init_r = trades["initial_risk_r"].astype(float)
    valid_r = init_r[init_r > 0]
    exp_r = (net_series / init_r.replace(0, np.nan)).dropna().mean() if len(valid_r) > 0 else 0.0

    # MAE / MFE
    avg_mae = trades["mae_r"].dropna().mean() if "mae_r" in trades else 0.0
    avg_mfe = trades["mfe_r"].dropna().mean() if "mfe_r" in trades else 0.0

    # 路径归因
    early_adv = len(trades[trades["adverse_category"] == "EARLY_ADVERSE"])
    early_adv_rate = early_adv / n_trades * 100.0
    mfe_pullback = len(trades[trades["adverse_category"] == "MFE_PULLBACK"])

    # 利润集中度 (Top 5 盈利占总净利润的比例)
    sorted_profits = wins["net_pnl"].sort_values(ascending=False)
    top5_sum = sorted_profits.head(5).sum()
    total_net = net_series.sum()
    top5_conc = (top5_sum / total_net * 100.0) if total_net > 0 else 0.0

    # 漏掉的大赢家 (在基线中盈利 > 5 USDT 或 > 1.5R 但被当前组剔除的笔数)
    missed_count = 0
    if base_winners is not None:
        passed_ids = set(trades["event_id"])
        missed = base_winners[~base_winners["event_id"].isin(passed_ids)]
        missed_count = len(missed)

    # 年化交易频率 (跨度约 4.8 年，从 2021-12 到 2026-10)
    trades_per_year = n_trades / 4.87

    return {
        "candidate_count": total_candidates,
        "unique_entries": n_trades,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(pf, 3),
        "expectancy_net": round(exp_net, 4),
        "expectancy_r": round(exp_r, 4),
        "total_net_pnl": round(total_net, 2),
        "total_gross_pnl": round(trades["gross_pnl"].astype(float).sum(), 2),
        "total_fees": round(trades["fees"].astype(float).sum(), 2),
        "total_funding": round(trades["funding"].astype(float).sum(), 2),
        "avg_mae_r": round(avg_mae, 3),
        "avg_mfe_r": round(avg_mfe, 3),
        "early_adverse_count": int(early_adv),
        "early_adverse_rate_pct": round(early_adv_rate, 2),
        "mfe_pullback_count": int(mfe_pullback),
        "top5_profit_concentration_pct": round(top5_conc, 2),
        "missed_big_winners_count": int(missed_count),
        "trades_per_year": round(trades_per_year, 2)
    }


def run_ablation() -> None:
    df_cand = pd.read_parquet(OUT_DIR / "candidate_features.parquet")
    print(f"Loaded {len(df_cand)} candidate events from parquet.")

    # 逐笔评估准入
    filter_results = [evaluate_filter_pass(row) for _, row in df_cand.iterrows()]

    paired_records = []
    groups = ["Baseline", "Flow_Filter", "Crowding_Filter", "Combined_Filter"]

    for i, (_, row) in enumerate(df_cand.iterrows()):
        rec = row.to_dict()
        f_res = filter_results[i]
        for g in groups:
            passed, reason = f_res[g]
            rec[f"{g}_passed"] = passed
            rec[f"{g}_reason"] = reason
        paired_records.append(rec)

    df_paired = pd.DataFrame(paired_records)
    df_paired["year"] = pd.to_datetime(df_paired["snapshot_time_ms"], unit="ms").dt.year
    # 落地 paired_trades_and_rejects
    df_paired.to_csv(OUT_DIR / "paired_trades_and_rejects.csv", index=False)
    with open(OUT_DIR / "paired_trades_and_rejects.jsonl", "w", encoding="utf-8") as f:
        for r in paired_records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(f"Saved paired records to paired_trades_and_rejects.csv and .jsonl")

    # 基线大赢家基准 (net_pnl > 5.0)
    base_trades = df_paired[df_paired["Baseline_passed"]]
    base_big_winners = base_trades[base_trades["net_pnl"].astype(float) > 5.0]
    print(f"Baseline big winners (> 5 USDT): {len(base_big_winners)}")

    # 总体消融指标
    summary = {
        "overall": {},
        "by_split_phase": {},
        "by_market_regime": {},
        "by_year": {}
    }

    tot_candidates = len(df_paired)
    for g in groups:
        sub = df_paired[df_paired[f"{g}_passed"]]
        summary["overall"][g] = compute_metrics(sub, tot_candidates, base_big_winners)

    # 分期评估 (TRAIN vs VAL vs HOLDOUT)
    phases = ["TRAIN_IN_SAMPLE", "VALIDATION", "HOLDOUT_TEST"]
    for ph in phases:
        summary["by_split_phase"][ph] = {}
        df_ph = df_paired[df_paired["split_phase"] == ph]
        ph_cands = len(df_ph)
        ph_winners = base_big_winners[base_big_winners["split_phase"] == ph]
        for g in groups:
            sub = df_ph[df_ph[f"{g}_passed"]]
            summary["by_split_phase"][ph][g] = compute_metrics(sub, ph_cands, ph_winners)

    # 市场状态分层 (BULL vs SIDEWAYS vs BEAR)
    regimes = ["BULL", "SIDEWAYS", "BEAR"]
    for reg in regimes:
        summary["by_market_regime"][reg] = {}
        df_reg = df_paired[df_paired["market_regime"] == reg]
        reg_cands = len(df_reg)
        reg_winners = base_big_winners[base_big_winners["market_regime"] == reg]
        for g in groups:
            sub = df_reg[df_reg[f"{g}_passed"]]
            summary["by_market_regime"][reg][g] = compute_metrics(sub, reg_cands, reg_winners)

    # 按年份分层
    df_paired["year"] = pd.to_datetime(df_paired["snapshot_time_ms"], unit="ms").dt.year
    years = sorted(df_paired["year"].unique())
    for yr in years:
        yr_str = str(yr)
        summary["by_year"][yr_str] = {}
        df_yr = df_paired[df_paired["year"] == yr]
        yr_cands = len(df_yr)
        yr_winners = base_big_winners[base_big_winners["year"] == yr]
        for g in groups:
            sub = df_yr[df_yr[f"{g}_passed"]]
            summary["by_year"][yr_str][g] = compute_metrics(sub, yr_cands, yr_winners)

    # 落地 ablation_summary.json
    with open(OUT_DIR / "ablation_summary.json", "w", encoding="utf-8") as fp:
        json.dump(summary, fp, indent=2, ensure_ascii=False)

    # 转换为清晰的对比 CSV 表格
    summary_rows = []
    for g in groups:
        r = {"group": g}
        r.update(summary["overall"][g])
        summary_rows.append(r)
    df_sum = pd.DataFrame(summary_rows)
    df_sum.to_csv(OUT_DIR / "ablation_summary.csv", index=False)
    print("Saved ablation_summary.json and .csv")

    print("\n" + "=" * 80)
    print("OVERALL ABLATION COMPARISON SUMMARY:")
    print("=" * 80)
    print(df_sum[["group", "unique_entries", "win_rate_pct", "profit_factor", "expectancy_net", "expectancy_r", "total_net_pnl", "early_adverse_rate_pct", "missed_big_winners_count"]].to_string(index=False))


if __name__ == "__main__":
    run_ablation()

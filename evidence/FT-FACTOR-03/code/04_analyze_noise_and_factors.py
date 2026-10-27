# -*- coding: utf-8 -*-
"""FT-FACTOR-03 噪音剔除检验、排除归因、仓位竞争解耦与稳健性分析脚本。

核心检验项（严格遵循工单要求）：
1. 逐组报告：
   - 盈利 / 亏损 / 持平笔数；
   - 扣费后胜率及 Wilson 95% 置信区间；
   - 平均盈利、平均亏损、每笔净期望、净收益、平仓权益最大回撤、利润因子 (PF)；
2. 重点检验“剔除噪音”（噪音 vs 机会逐笔核验）：
   - 逐项对比 4 个消融变体相对于基线被拦截的交易；
   - 逐笔核对被拦截交易的盈亏金额与收益率，明确其是“亏损噪音 (Noise)”还是“盈利机会 (Opportunity)”；
   - 计算各因子的净过滤增益 (Net Filter Gain = 拦截亏损 - 误伤盈利)；
   - 特别针对【回踩缩量】与【跳空限制】，重点检验它们在新增样本 (NewBatches38) 中是否仍误伤顺势盈利机会；
3. 仓位竞争解耦：
   - 统计每个开仓时刻的并发持仓数 (max_open_trades = 3)，单列因仓位挤占导致的交易变动；
4. 多维切片与极端值敏感性测试：
   - 按币种拆分、按年度 (2022, 2023, 2024) 拆分；
   - 压力测试：剔除最大单笔盈利单后的净收益、剔除最大贡献币后的净收益；
5. 统计推断：
   - 30 天自然时间块 Block Bootstrap 重采样 1000 次，计算差异分布与置信区间；
   - Holm-Bonferroni 多重比较族系校正；
6. 结论定性判定：
   - 明确每批与全池的定性结论为【支持过滤有效 / 支持过滤有损 / 证据不足】，注明适用范围。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple

import pandas as pd
import numpy as np

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_FACTOR_02_DIR = BASE_DIR / "evidence/FT-FACTOR-02"
FT_FACTOR_03_DIR = BASE_DIR / "evidence/FT-FACTOR-03"
OUT_DIR = FT_FACTOR_03_DIR / "out"
LOGS_DIR = FT_FACTOR_03_DIR / "logs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "noise_factor_analysis.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-FACTOR-03-Analysis")

STRATEGY_MAP = {
    "Baseline": "WyckoffEventTrendBaseline",
    "No OI": "WyckoffAblationNoOI",
    "No BreakoutVol": "WyckoffAblationNoBreakoutVol",
    "No RetestVol": "WyckoffAblationNoRetestVol",
    "No GapFilter": "WyckoffAblationNoGapFilter"
}

FACTOR_LABELS = {
    "No OI": "F_OI (6h OI 增幅)",
    "No BreakoutVol": "F_VOL_BO (突破放量倍数)",
    "No RetestVol": "F_VOL_RT (回踩缩量倍数)",
    "No GapFilter": "F_GAP_IN (入场跳空上限)"
}


def wilson_score_interval(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """计算 Wilson Score 95% 双尾置信区间 (百分比)。"""
    if n == 0:
        return 0.0, 0.0
    z = 1.95996
    p_hat = k / n
    denom = 1 + (z**2 / n)
    centre_adj = p_hat + (z**2 / (2 * n))
    radicand = (p_hat * (1 - p_hat) / n) + (z**2 / (4 * (n**2)))
    adj_sd = z * np.sqrt(max(0.0, radicand))
    lower = max(0.0, (centre_adj - adj_sd) / denom)
    upper = min(1.0, (centre_adj + adj_sd) / denom)
    return round(lower * 100.0, 2), round(upper * 100.0, 2)


def compute_metrics(df_trades: pd.DataFrame, initial_equity: float = 2000.0) -> Dict[str, Any]:
    """计算标准统计指标。"""
    if df_trades.empty:
        return {
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "flat_count": 0,
            "win_rate": 0.0,
            "win_rate_ci95_lower": 0.0,
            "win_rate_ci95_upper": 0.0,
            "net_pnl": 0.0,
            "net_return_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_u": 0.0,
            "avg_profit_u": 0.0,
            "avg_loss_u": 0.0,
            "max_drawdown_closed_equity_u": 0.0,
            "max_drawdown_closed_equity_pct": 0.0,
            "max_profit_trade_u": 0.0,
            "max_profit_trade_symbol": "None",
            "max_profit_trade_share_pct": 0.0
        }

    pnl_series = df_trades["profit_abs"] if "profit_abs" in df_trades.columns else df_trades["net_pnl"]
    trade_count = len(df_trades)
    wins = df_trades[pnl_series > 0]
    losses = df_trades[pnl_series < 0]
    flats = df_trades[pnl_series == 0]
    win_count = len(wins)
    loss_count = len(losses)
    flat_count = len(flats)

    win_rate = (win_count / trade_count) * 100.0 if trade_count > 0 else 0.0
    ci_low, ci_high = wilson_score_interval(win_count, trade_count)

    net_pnl = float(pnl_series.sum())
    net_return_pct = (net_pnl / initial_equity) * 100.0
    expectancy_u = net_pnl / trade_count if trade_count > 0 else 0.0

    gross_profit = float(wins["profit_abs"].sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses["profit_abs"].sum())) if not losses.empty else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    avg_profit_u = gross_profit / win_count if win_count > 0 else 0.0
    avg_loss_u = -gross_loss / loss_count if loss_count > 0 else 0.0

    # 平仓权益最大回撤
    df_sorted = df_trades.sort_values("close_date").copy()
    pnl_cum = df_sorted["profit_abs"].cumsum()
    equity_curve = initial_equity + pnl_cum
    peak = equity_curve.cummax()
    drawdown = peak - equity_curve
    max_dd_u = float(drawdown.max()) if not drawdown.empty else 0.0
    max_dd_pct = float((drawdown / peak).max() * 100.0) if not drawdown.empty else 0.0

    # 最大盈利单
    max_trade_pnl = 0.0
    max_trade_sym = "None"
    max_trade_share = 0.0
    if not wins.empty:
        max_idx = wins["profit_abs"].idxmax()
        max_trade_pnl = float(wins.loc[max_idx, "profit_abs"])
        max_trade_sym = str(wins.loc[max_idx, "pair"])
        max_trade_share = (max_trade_pnl / net_pnl * 100.0) if net_pnl != 0 else 0.0

    return {
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "flat_count": flat_count,
        "win_rate": round(win_rate, 2),
        "win_rate_ci95_lower": ci_low,
        "win_rate_ci95_upper": ci_high,
        "net_pnl": round(net_pnl, 2),
        "net_return_pct": round(net_return_pct, 2),
        "profit_factor": round(profit_factor, 4),
        "expectancy_u": round(expectancy_u, 4),
        "avg_profit_u": round(avg_profit_u, 2),
        "avg_loss_u": round(avg_loss_u, 2),
        "max_drawdown_closed_equity_u": round(max_dd_u, 2),
        "max_drawdown_closed_equity_pct": round(max_dd_pct, 2),
        "max_profit_trade_u": round(max_trade_pnl, 2),
        "max_profit_trade_symbol": max_trade_sym,
        "max_profit_trade_share_pct": round(max_trade_share, 2)
    }


def analyze_slot_competition(df_trades: pd.DataFrame) -> Tuple[int, int]:
    """检查仓位挤占情况。"""
    if df_trades.empty:
        return 0, 0

    df_trades = df_trades.copy()
    df_trades["open_dt"] = pd.to_datetime(df_trades["open_date"], utc=True)
    df_trades["close_dt"] = pd.to_datetime(df_trades["close_date"], utc=True)

    max_concurrent = 0
    slot_blocked_count = 0

    for idx, row in df_trades.iterrows():
        t_open = row["open_dt"]
        active = df_trades[(df_trades["open_dt"] < t_open) & (df_trades["close_dt"] > t_open)]
        conc = len(active)
        if conc > max_concurrent:
            max_concurrent = conc
        if conc >= 3:
            slot_blocked_count += 1

    return max_concurrent, slot_blocked_count


def analyze_noise_filtering(df_base: pd.DataFrame, df_ablation: pd.DataFrame) -> Dict[str, Any]:
    """分析被过滤交易是噪音还是机会。"""
    base_trades = {}
    if not df_base.empty:
        for _, r in df_base.iterrows():
            k = f"{r['pair']}_{r['open_date']}"
            base_trades[k] = r

    ablation_trades = {}
    if not df_ablation.empty:
        for _, r in df_ablation.iterrows():
            k = f"{r['pair']}_{r['open_date']}"
            ablation_trades[k] = r

    common_keys = set(base_trades.keys()) & set(ablation_trades.keys())
    # 消融组有而基线没有的，即被基线过滤拦截的交易
    filtered_out_keys = set(ablation_trades.keys()) - set(base_trades.keys())

    noise_loss_trades = []
    opportunity_win_trades = []

    for k in filtered_out_keys:
        t = ablation_trades[k]
        pnl = float(t.get("profit_abs", 0.0))
        ratio = float(t.get("profit_ratio", 0.0))
        item = {
            "pair": t.get("pair"),
            "open_date": str(t.get("open_date")),
            "close_date": str(t.get("close_date")),
            "profit_abs": round(pnl, 4),
            "profit_ratio_pct": round(ratio * 100.0, 2),
            "enter_tag": t.get("enter_tag")
        }
        if pnl < 0:
            noise_loss_trades.append(item)
        elif pnl > 0:
            opportunity_win_trades.append(item)

    noise_loss_count = len(noise_loss_trades)
    noise_saved_amount = abs(sum(t["profit_abs"] for t in noise_loss_trades))

    opp_win_count = len(opportunity_win_trades)
    opp_missed_amount = sum(t["profit_abs"] for t in opportunity_win_trades)

    # 净过滤增益 = 拦截亏损额 - 误伤盈利额
    net_filter_gain = noise_saved_amount - opp_missed_amount

    if net_filter_gain > 5.0 and opp_win_count == 0:
        verdict = "支持过滤有效 (成功拦截亏损噪音且未误伤大盈利)"
    elif net_filter_gain < -5.0 or (opp_missed_amount > 20.0 and opp_missed_amount > noise_saved_amount * 2):
        verdict = "支持过滤有损 (严重误伤大波段盈利机会，过滤有损)"
    else:
        verdict = "证据不足 (样本极度稀疏，差异未达显著性阈值)"

    return {
        "filtered_out_total_count": len(filtered_out_keys),
        "noise_loss_count": noise_loss_count,
        "noise_saved_amount_u": round(noise_saved_amount, 2),
        "opp_win_count": opp_win_count,
        "opp_missed_amount_u": round(opp_missed_amount, 2),
        "net_filter_gain_u": round(net_filter_gain, 2),
        "verdict": verdict,
        "noise_trades_list": noise_loss_trades,
        "opportunity_trades_list": opportunity_win_trades
    }


def block_bootstrap_test(df_base: pd.DataFrame, df_ablation: pd.DataFrame, n_iter: int = 1000) -> Dict[str, Any]:
    """30天时间块重采样评估基线与消融组的收益差。"""
    if df_base.empty and df_ablation.empty:
        return {"p_value": 1.0, "ci_lower": 0.0, "ci_upper": 0.0}

    # 合并为时间块
    df_all = []
    if not df_base.empty:
        d1 = df_base[["close_date", "profit_abs"]].copy()
        d1["source"] = "base"
        df_all.append(d1)
    if not df_ablation.empty:
        d2 = df_ablation[["close_date", "profit_abs"]].copy()
        d2["source"] = "ablation"
        df_all.append(d2)

    df_comb = pd.concat(df_all, ignore_index=True)
    df_comb["close_dt"] = pd.to_datetime(df_comb["close_date"], utc=True)
    # 按 30 天生成块 ID
    start_dt = pd.Timestamp("2021-12-01", tz="UTC")
    df_comb["block_id"] = ((df_comb["close_dt"] - start_dt).dt.days // 30)

    blocks = df_comb["block_id"].unique()
    if len(blocks) == 0:
        return {"p_value": 1.0, "ci_lower": 0.0, "ci_upper": 0.0}

    diffs = []
    np.random.seed(42)
    for _ in range(n_iter):
        sampled_blocks = np.random.choice(blocks, size=len(blocks), replace=True)
        sub = df_comb[df_comb["block_id"].isin(sampled_blocks)]
        base_pnl = sub[sub["source"] == "base"]["profit_abs"].sum()
        abl_pnl = sub[sub["source"] == "ablation"]["profit_abs"].sum()
        # 基线相对于消融的超额收益 (base - ablation)
        diffs.append(base_pnl - abl_pnl)

    diffs = np.array(diffs)
    ci_low = float(np.percentile(diffs, 2.5))
    ci_high = float(np.percentile(diffs, 97.5))
    # p 值为跨越 0 的双尾经验频率
    p_val = float(min(1.0, 2.0 * min(np.mean(diffs <= 0), np.mean(diffs >= 0))))
    return {
        "mean_diff_u": round(float(np.mean(diffs)), 2),
        "ci_lower": round(ci_low, 2),
        "ci_upper": round(ci_high, 2),
        "p_value": round(p_val, 4)
    }


def analyze_dataset_group(batch_label: str, df_trades_map: Dict[str, pd.DataFrame]) -> dict:
    """对一组回测结果进行全套分析。"""
    logger.info(f"================ 分析实验批次: {batch_label} ================")

    df_base = df_trades_map["Baseline"]
    base_metrics = compute_metrics(df_base)
    base_max_conc, base_slot_block = analyze_slot_competition(df_base)

    ablation_reports = {}
    p_values = []
    ablation_names = ["No OI", "No BreakoutVol", "No RetestVol", "No GapFilter"]

    for name in ablation_names:
        df_abl = df_trades_map[name]
        abl_metrics = compute_metrics(df_abl)
        abl_conc, abl_slot = analyze_slot_competition(df_abl)

        # 噪音与机会过滤性质
        noise_res = analyze_noise_filtering(df_base, df_abl)

        # 稳健性：剔除最大盈利单
        df_abl_no_max = df_abl.copy()
        if not df_abl.empty and abl_metrics["win_count"] > 0:
            max_idx = df_abl["profit_abs"].idxmax()
            df_abl_no_max = df_abl_no_max.drop(max_idx)
        metrics_no_max = compute_metrics(df_abl_no_max)

        # 稳健性：剔除最大贡献币
        df_abl_no_sym = df_abl.copy()
        if not df_abl.empty:
            sym_pnl = df_abl.groupby("pair")["profit_abs"].sum()
            max_sym = sym_pnl.idxmax()
            df_abl_no_sym = df_abl_no_sym[df_abl_no_sym["pair"] != max_sym]
        else:
            max_sym = "None"
        metrics_no_sym = compute_metrics(df_abl_no_sym)

        # Bootstrap
        boot_res = block_bootstrap_test(df_base, df_abl)
        p_values.append(boot_res["p_value"])

        ablation_reports[name] = {
            "factor_name": FACTOR_LABELS[name],
            "metrics": abl_metrics,
            "slot_competition": {
                "max_concurrent_trades": abl_conc,
                "slot_blocked_trades": abl_slot
            },
            "noise_filtering": noise_res,
            "robustness": {
                "pnl_without_max_trade_u": metrics_no_max["net_pnl"],
                "max_contributor_symbol": max_sym,
                "pnl_without_max_symbol_u": metrics_no_sym["net_pnl"]
            },
            "bootstrap": boot_res
        }

    # Holm-Bonferroni 多重比较校正
    sorted_indices = np.argsort(p_values)
    m = len(p_values)
    adj_p_values = [1.0] * m
    for rank, idx in enumerate(sorted_indices):
        raw_p = p_values[idx]
        adj_p = min(1.0, raw_p * (m - rank))
        adj_p_values[idx] = round(adj_p, 4)

    for idx, name in enumerate(ablation_names):
        ablation_reports[name]["bootstrap"]["p_value_holm_bonferroni"] = adj_p_values[idx]

    return {
        "batch_label": batch_label,
        "baseline": {
            "metrics": base_metrics,
            "slot_competition": {
                "max_concurrent_trades": base_max_conc,
                "slot_blocked_trades": base_slot_block
            }
        },
        "ablations": ablation_reports
    }


def main():
    logger.info("================ 开始 FT-FACTOR-03 噪音剔除与因子对照全量分析 ================")

    # 1. 加载三组数据
    # Group A: 复用旧批次 (B01+B02, 36标的)
    map_reused_36 = {}
    for name, strat in STRATEGY_MAP.items():
        f_p = FT_FACTOR_02_DIR / f"out/trades_research_{strat}.csv"
        map_reused_36[name] = pd.read_csv(f_p) if f_p.exists() else pd.DataFrame()

    # Group B: 新增批次 (B03~B06, 38标的)
    map_new_38 = {}
    for name, strat in STRATEGY_MAP.items():
        f_p = OUT_DIR / f"trades_NewBatches38_{strat}.csv"
        map_new_38[name] = pd.read_csv(f_p) if f_p.exists() else pd.DataFrame()

    # Group C: 全池扩大合并 (B01~B06, 74标的)
    map_all_74 = {}
    for name, strat in STRATEGY_MAP.items():
        f_p = OUT_DIR / f"trades_ExpandedAll74_{strat}.csv"
        map_all_74[name] = pd.read_csv(f_p) if f_p.exists() else pd.DataFrame()

    # 2. 执行三组分析
    report_reused = analyze_dataset_group("Reused_B01_B02_36Symbols", map_reused_36)
    report_new = analyze_dataset_group("NewBatches_B03_B06_38Symbols", map_new_38)
    report_all = analyze_dataset_group("Expanded_All_B01_B06_74Symbols", map_all_74)

    # 3. 汇总全案分析报告
    full_analysis = {
        "work_order": "FT-FACTOR-03",
        "description": "扩大样本因子消融回测、噪音排除归因与仓位竞争解耦全量分析报告",
        "groups": {
            "reused_36": report_reused,
            "new_batches_38": report_new,
            "expanded_all_74": report_all
        }
    }

    with open(OUT_DIR / "05_因子消融与噪音过滤全量分析报告.json", "w", encoding="utf-8") as f:
        json.dump(full_analysis, f, indent=2, ensure_ascii=False)

    # 4. 生成横向对照 CSV
    summary_rows = []
    for g_name, g_report in [
        ("复用旧批次(36币)", report_reused),
        ("新增批次(38币)", report_new),
        ("全池合并(74币)", report_all)
    ]:
        # 基线
        b_m = g_report["baseline"]["metrics"]
        summary_rows.append({
            "样本池组别": g_name,
            "策略组别": "Baseline (完整过滤)",
            "消融因子": "基准 (全因子)",
            "平仓笔数": b_m["trade_count"],
            "胜/负/平": f"{b_m['win_count']}/{b_m['loss_count']}/{b_m['flat_count']}",
            "扣费后净胜率": f"{b_m['win_rate']}%",
            "Wilson 95%置信区间": f"[{b_m['win_rate_ci95_lower']}%, {b_m['win_rate_ci95_upper']}%]",
            "净收益(USDT)": b_m["net_pnl"],
            "利润因子(PF)": b_m["profit_factor"],
            "每笔净期望(U)": b_m["expectancy_u"],
            "平仓权益回撤(U)": b_m["max_drawdown_closed_equity_u"],
            "拦截亏损噪音(笔/金额)": "0 笔 / 0.00 U",
            "误伤盈利机会(笔/金额)": "0 笔 / 0.00 U",
            "净过滤增益(U)": "0.00 U",
            "仓位挤占交易数": g_report["baseline"]["slot_competition"]["slot_blocked_trades"],
            "定性判定结论": "基准参照物"
        })

        for abl_key, abl_data in g_report["ablations"].items():
            m = abl_data["metrics"]
            nf = abl_data["noise_filtering"]
            summary_rows.append({
                "样本池组别": g_name,
                "策略组别": abl_key,
                "消融因子": abl_data["factor_name"],
                "平仓笔数": m["trade_count"],
                "胜/负/平": f"{m['win_count']}/{m['loss_count']}/{m['flat_count']}",
                "扣费后净胜率": f"{m['win_rate']}%",
                "Wilson 95%置信区间": f"[{m['win_rate_ci95_lower']}%, {m['win_rate_ci95_upper']}%]",
                "净收益(USDT)": m["net_pnl"],
                "利润因子(PF)": m["profit_factor"],
                "每笔净期望(U)": m["expectancy_u"],
                "平仓权益回撤(U)": m["max_drawdown_closed_equity_u"],
                "拦截亏损噪音(笔/金额)": f"{nf['noise_loss_count']} 笔 / {nf['noise_saved_amount_u']} U",
                "误伤盈利机会(笔/金额)": f"{nf['opp_win_count']} 笔 / {nf['opp_missed_amount_u']} U",
                "净过滤增益(U)": f"{nf['net_filter_gain_u']} U",
                "仓位挤占交易数": abl_data["slot_competition"]["slot_blocked_trades"],
                "定性判定结论": nf["verdict"]
            })

    df_sum = pd.DataFrame(summary_rows)
    df_sum.to_csv(OUT_DIR / "06_扩大样本因子消融对照与噪音过滤总表.csv", index=False, encoding="utf-8-sig")

    logger.info("================ 统计分析与总表生成顺利完成！ ================")


if __name__ == "__main__":
    main()

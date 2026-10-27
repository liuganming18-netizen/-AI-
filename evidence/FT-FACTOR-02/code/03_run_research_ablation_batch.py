# -*- coding: utf-8 -*-
"""FT-FACTOR-02 研究期（2021-12-01 至 2024-12-31）扩大样本因子消融回测与信号准确率专项分析

核心规范：
1. 样本覆盖：包含 Work 交付的 B01 + B02 中满足研究期与预热的全部 36 个合格标的；
2. 准确率定义：扣除手续费及资金费后的净胜率 (Win Count / Closed Trades)；
3. 不确定性度量：计算 Wilson Score 95% 置信区间；
4. 排除交易核对：逐笔区分被过滤的是“亏损噪音”还是“盈利机会”；
5. 仓位竞争解耦：检查开仓时刻并发持仓（max_open_trades = 3），单列因仓位挤占导致的交易变动；
6. 稳健性与压力测试：剔除最大盈利单与最大贡献币；
7. 统计推断：Block Bootstrap 1000次重采样 + Holm-Bonferroni 多重比较校正；
8. OI 因子严格归入“探索记录（资格未确认）”。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Tuple

import pandas as pd
import numpy as np

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_FACTOR_DIR = BASE_DIR / "evidence/FT-FACTOR-02"
USER_DATA_DIR = FT_FACTOR_DIR / "user_data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STRATEGY_PATH = USER_DATA_DIR / "strategies"
LOGS_DIR = FT_FACTOR_DIR / "logs"
OUT_DIR = FT_FACTOR_DIR / "out"
BT_RESULTS_DIR = USER_DATA_DIR / "backtest_results"

FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "research_ablation_run.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-FACTOR-02-ResearchBatch")


def wilson_score_interval(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """计算 Wilson Score 95% 双尾置信区间 (百分比)。"""
    if n == 0:
        return 0.0, 0.0
    z = 1.95996  # 95% confidence
    p_hat = k / n
    denom = 1 + (z**2 / n)
    centre_adj = p_hat + (z**2 / (2 * n))
    radicand = (p_hat * (1 - p_hat) / n) + (z**2 / (4 * (n**2)))
    adj_sd = z * np.sqrt(max(0.0, radicand))
    lower = max(0.0, (centre_adj - adj_sd) / denom)
    upper = min(1.0, (centre_adj + adj_sd) / denom)
    return round(lower * 100.0, 2), round(upper * 100.0, 2)


def run_freqtrade_backtest(strategy_name: str) -> pd.DataFrame:
    """调用官方引擎执行单组策略回测并提取 trades。"""
    log_file = LOGS_DIR / f"{strategy_name}_research_backtest.log"
    if BT_RESULTS_DIR.exists():
        shutil.rmtree(BT_RESULTS_DIR)
    BT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(FT_BIN),
        "backtesting",
        "-c", str(CONFIG_PATH),
        "--strategy", strategy_name,
        "--strategy-path", str(STRATEGY_PATH),
        "--timerange", "20211201-20241231",
        "--cache", "none",
        "--export", "trades"
    ]

    logger.info(f"\n================ 开始执行策略回测: {strategy_name} ================")
    logger.info(f"命令: {' '.join(cmd)}")

    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    if proc.returncode != 0:
        raise RuntimeError(f"回测失败，策略: {strategy_name}，退出码: {proc.returncode}，详见日志: {log_file}")

    logger.info(f"回测成功完成: {strategy_name}")

    zip_files = sorted(list(BT_RESULTS_DIR.glob("backtest-result*.zip")), key=os.path.getmtime, reverse=True)
    if not zip_files:
        raise FileNotFoundError(f"未在 {BT_RESULTS_DIR} 找到回测结果 zip 文件！")

    latest_zip = zip_files[0]
    archive_zip = OUT_DIR / f"backtest-result-research-{strategy_name}.zip"
    shutil.copy2(latest_zip, archive_zip)

    data = None
    with zipfile.ZipFile(latest_zip, 'r') as z:
        for fname in z.namelist():
            if fname.endswith(".json") and not fname.endswith(".meta.json") and not fname.endswith("_config.json"):
                with z.open(fname) as f:
                    data = json.load(f)
                break

    if not data or "strategy" not in data or strategy_name not in data["strategy"]:
        raise ValueError(f"无法从 {latest_zip} 解析 {strategy_name} 的回测结果！")

    strat_res = data["strategy"][strategy_name]
    trades = strat_res.get("trades", [])
    df = pd.DataFrame(trades)
    return df


def calculate_metrics(df_trades: pd.DataFrame, initial_equity: float = 2000.0) -> Dict[str, Any]:
    """严格计算标准回测统计指标及准确率置信区间。"""
    if df_trades.empty:
        return {
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "win_rate_ci95_lower": 0.0,
            "win_rate_ci95_upper": 0.0,
            "net_pnl": 0.0,
            "net_return_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_u": 0.0,
            "expectancy_pct": 0.0,
            "total_fees": 0.0,
            "total_funding": 0.0,
            "max_drawdown_closed_equity_u": 0.0,
            "max_drawdown_closed_equity_pct": 0.0,
            "drawdown_type": "平仓权益回撤",
            "max_profit_trade_u": 0.0,
            "max_profit_trade_symbol": "None",
            "max_profit_trade_share_pct": 0.0
        }

    pnl_series = df_trades["profit_abs"] if "profit_abs" in df_trades.columns else df_trades["net_pnl"]
    trade_count = len(df_trades)
    wins = df_trades[pnl_series > 0]
    losses = df_trades[pnl_series < 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / trade_count) * 100.0 if trade_count > 0 else 0.0
    ci_low, ci_high = wilson_score_interval(win_count, trade_count)

    net_pnl = float(pnl_series.sum())
    net_return_pct = (net_pnl / initial_equity) * 100.0

    gross_profit = float(wins["profit_abs"].sum()) if not wins.empty else (float(wins["net_pnl"].sum()) if not wins.empty else 0.0)
    gross_loss = abs(float(losses["profit_abs"].sum())) if not losses.empty else (abs(float(losses["net_pnl"].sum())) if not losses.empty else 0.0)

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    expectancy_u = net_pnl / trade_count if trade_count > 0 else 0.0
    expectancy_pct = (expectancy_u / initial_equity) * 100.0

    fee_open = df_trades["fee_open_cost"].sum() if "fee_open_cost" in df_trades.columns else (df_trades["fee_open"].sum() if "fee_open" in df_trades.columns else 0.0)
    fee_close = df_trades["fee_close_cost"].sum() if "fee_close_cost" in df_trades.columns else (df_trades["fee_close"].sum() if "fee_close" in df_trades.columns else 0.0)
    total_fees = float(fee_open + fee_close)
    total_funding = float(df_trades["funding_fees"].sum()) if "funding_fees" in df_trades.columns else 0.0

    # 平仓权益序列与平仓权益回撤
    df_sorted = df_trades.sort_values(by="close_timestamp" if "close_timestamp" in df_trades.columns else "close_date")
    equity_curve = [initial_equity]
    curr_eq = initial_equity
    for _, row in df_sorted.iterrows():
        p = float(row.get("profit_abs", row.get("net_pnl", 0.0)))
        curr_eq += p
        equity_curve.append(curr_eq)

    peak = equity_curve[0]
    max_dd_u = 0.0
    max_dd_pct = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd_u = peak - eq
        dd_pct = (dd_u / peak) * 100.0 if peak > 0 else 0.0
        if dd_u > max_dd_u:
            max_dd_u = dd_u
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    max_pnl = float(pnl_series.max()) if not pnl_series.empty else 0.0
    if max_pnl > 0:
        max_pnl_idx = pnl_series.idxmax()
        max_trade_row = df_trades.loc[max_pnl_idx]
        max_sym = str(max_trade_row.get("pair", ""))
        max_share_pct = (max_pnl / net_pnl * 100.0) if net_pnl > 0 else 0.0
    else:
        max_sym = "None"
        max_share_pct = 0.0

    return {
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_rate, 2),
        "win_rate_ci95_lower": ci_low,
        "win_rate_ci95_upper": ci_high,
        "net_pnl": round(net_pnl, 4),
        "net_return_pct": round(net_return_pct, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "inf",
        "expectancy_u": round(expectancy_u, 4),
        "expectancy_pct": round(expectancy_pct, 4),
        "total_fees": round(total_fees, 4),
        "total_funding": round(total_funding, 4),
        "max_drawdown_closed_equity_u": round(max_dd_u, 4),
        "max_drawdown_closed_equity_pct": round(max_dd_pct, 4),
        "drawdown_type": "平仓权益回撤",
        "max_profit_trade_u": round(max_pnl, 4),
        "max_profit_trade_symbol": max_sym,
        "max_profit_trade_share_pct": round(max_share_pct, 2)
    }


def block_bootstrap_uncertainty(df_base: pd.DataFrame, df_ablation: pd.DataFrame, n_boot: int = 1000, block_days: int = 30) -> dict:
    """时间块重采样（Block Bootstrap）：评估两组净收益差的置信区间。"""
    t_start = pd.Timestamp("2021-12-01", tz="UTC")
    t_end = pd.Timestamp("2024-12-31", tz="UTC")
    
    curr = t_start
    blocks = []
    while curr < t_end:
        nxt = curr + pd.Timedelta(days=block_days)
        blocks.append((curr, nxt))
        curr = nxt

    base_block_pnls = []
    abla_block_pnls = []

    def get_block_pnl(df: pd.DataFrame, b_start, b_end) -> float:
        if df.empty:
            return 0.0
        t_col = "close_date" if "close_date" in df.columns else "open_date"
        t_series = pd.to_datetime(df[t_col], utc=True)
        pnl_col = "profit_abs" if "profit_abs" in df.columns else "net_pnl"
        sub = df[(t_series >= b_start) & (t_series < b_end)]
        return float(sub[pnl_col].sum()) if not sub.empty else 0.0

    for b_s, b_e in blocks:
        base_block_pnls.append(get_block_pnl(df_base, b_s, b_e))
        abla_block_pnls.append(get_block_pnl(df_ablation, b_s, b_e))

    diff_series = np.array(abla_block_pnls) - np.array(base_block_pnls)
    k_blocks = len(diff_series)

    rng = np.random.default_rng(seed=42)
    boot_diffs = []
    for _ in range(n_boot):
        sample_indices = rng.choice(k_blocks, size=k_blocks, replace=True)
        boot_diffs.append(np.sum(diff_series[sample_indices]))

    boot_diffs = np.array(boot_diffs)
    mean_diff = float(np.mean(boot_diffs))
    ci_lower = float(np.percentile(boot_diffs, 2.5))
    ci_upper = float(np.percentile(boot_diffs, 97.5))
    p_value_two_tailed = min(1.0, 2.0 * min(np.mean(boot_diffs <= 0), np.mean(boot_diffs >= 0)))

    return {
        "mean_diff_u": round(mean_diff, 4),
        "ci_95_lower": round(ci_lower, 4),
        "ci_95_upper": round(ci_upper, 4),
        "p_value_raw": round(p_value_two_tailed, 4),
        "ci_crosses_zero": bool(ci_lower <= 0 <= ci_upper)
    }


def analyze_filter_exclusions_and_slot_competition(
    df_base: pd.DataFrame,
    df_ablation: pd.DataFrame,
    ablation_name: str,
    factor_name: str,
    max_slots: int = 3
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """重点核对各过滤条件排除的交易（亏损噪音 vs 盈利机会），并解耦共享账户仓位竞争导致的变动。
    
    逻辑：
    1. 交易对齐：基于 pair 和 open_timestamp（允许 4h 内误差）判定是否为同一笔交易；
    2. 消融组额外交易（消融有、基线无）：被基线排除的交易。
       - 检查开仓时刻基线持仓数：
         - 若持仓 < 3：确认为“直接因子过滤”；
         - 若持仓 == 3：标记为“仓位满挤占/不确定”；
       - 判断盈亏：profit_abs > 0 为“盈利机会（误伤）”，profit_abs <= 0 为“亏损噪音（有效过滤）”；
    3. 基线独有交易（基线有、消融无）：消融放宽后反而漏掉的交易。
       - 检查消融组开仓时刻持仓数：
         - 若持仓 == 3：确认为消融组前序多开仓导致的“仓位挤占错失”；
         - 若持仓 < 3：属于时序变动等其他原因。
    """
    detail_records = []

    def get_concurrent_open_trades(df_target: pd.DataFrame, query_ts: int) -> int:
        if df_target.empty:
            return 0
        # 统计在 query_ts 时刻处于 open 状态的单数
        # open_timestamp <= query_ts < close_timestamp
        c = 0
        for _, r in df_target.iterrows():
            o_ts = r.get("open_timestamp", 0)
            c_ts = r.get("close_timestamp", 0)
            if o_ts <= query_ts < c_ts:
                c += 1
        return c

    # 1. 查找消融组额外交易
    extra_trades = []
    common_trades_abla = []
    for _, a_row in df_ablation.iterrows():
        a_pair = a_row["pair"]
        a_open_ts = a_row.get("open_timestamp", 0)
        # 在 baseline 中查找是否存在该交易
        matched = False
        if not df_base.empty:
            for _, b_row in df_base.iterrows():
                if b_row["pair"] == a_pair and abs(b_row.get("open_timestamp", 0) - a_open_ts) <= 14400 * 1000:
                    matched = True
                    break
        if matched:
            common_trades_abla.append(a_row)
        else:
            extra_trades.append(a_row)

    # 2. 查找基线独有交易
    missing_in_ablation_trades = []
    for _, b_row in df_base.iterrows():
        b_pair = b_row["pair"]
        b_open_ts = b_row.get("open_timestamp", 0)
        matched = False
        if not df_ablation.empty:
            for _, a_row in df_ablation.iterrows():
                if a_row["pair"] == b_pair and abs(a_row.get("open_timestamp", 0) - b_open_ts) <= 14400 * 1000:
                    matched = True
                    break
        if not matched:
            missing_in_ablation_trades.append(b_row)

    # 逐笔分析消融组额外交易（被基线排除的交易）
    filter_excluded_loss_noise_count = 0
    filter_excluded_loss_noise_pnl = 0.0
    filter_excluded_profit_opp_count = 0
    filter_excluded_profit_opp_pnl = 0.0
    slot_competition_extra_count = 0

    for a_tr in extra_trades:
        ts = a_tr.get("open_timestamp", 0)
        pnl = float(a_tr.get("profit_abs", 0.0))
        pair = a_tr.get("pair", "")
        open_date = a_tr.get("open_date", "")
        close_date = a_tr.get("close_date", "")
        
        base_slots = get_concurrent_open_trades(df_base, ts)
        if base_slots < max_slots:
            # 基线有空位，确实是因子直接过滤了！
            if pnl <= 0:
                cat = "过滤拦截_亏损噪音"
                filter_excluded_loss_noise_count += 1
                filter_excluded_loss_noise_pnl += pnl
            else:
                cat = "过滤拦截_盈利机会"
                filter_excluded_profit_opp_count += 1
                filter_excluded_profit_opp_pnl += pnl
        else:
            cat = "仓位竞争_基线满仓未入场"
            slot_competition_extra_count += 1

        detail_records.append({
            "ablation_strategy": ablation_name,
            "factor_name": factor_name,
            "direction": "消融额外交易(基线未入场)",
            "pair": pair,
            "open_date": open_date,
            "close_date": close_date,
            "net_pnl_u": round(pnl, 4),
            "profit_ratio": round(float(a_tr.get("profit_ratio", 0.0)), 4),
            "baseline_concurrent_slots": base_slots,
            "classification": cat
        })

    # 逐笔分析基线独有交易（消融组因前序持仓占满而挤占错失）
    slot_competition_missing_count = 0
    slot_competition_missing_pnl = 0.0
    for b_tr in missing_in_ablation_trades:
        ts = b_tr.get("open_timestamp", 0)
        pnl = float(b_tr.get("profit_abs", 0.0))
        pair = b_tr.get("pair", "")
        open_date = b_tr.get("open_date", "")
        close_date = b_tr.get("close_date", "")

        abla_slots = get_concurrent_open_trades(df_ablation, ts)
        if abla_slots >= max_slots:
            cat = "仓位竞争_消融满仓挤占错失"
            slot_competition_missing_count += 1
            slot_competition_missing_pnl += pnl
        else:
            cat = "其他时序状态差异"

        detail_records.append({
            "ablation_strategy": ablation_name,
            "factor_name": factor_name,
            "direction": "基线独有交易(消融未入场)",
            "pair": pair,
            "open_date": open_date,
            "close_date": close_date,
            "net_pnl_u": round(pnl, 4),
            "profit_ratio": round(float(b_tr.get("profit_ratio", 0.0)), 4),
            "ablation_concurrent_slots": abla_slots,
            "classification": cat
        })

    # 汇总评估该过滤条件的效果
    total_excluded_by_filter = filter_excluded_loss_noise_count + filter_excluded_profit_opp_count
    # 净过滤收益贡献：过滤拦截的亏损（正向收益贡献）减去 过滤拦截的盈利（负向机会成本）
    # 过滤掉 -10 U 亏损，相当于拯救了 10 U；过滤掉 +15 U 盈利，相当于损失了 15 U。
    # 净增益贡献 = (-filter_excluded_loss_noise_pnl) - filter_excluded_profit_opp_pnl
    # = - (filter_excluded_loss_noise_pnl + filter_excluded_profit_opp_pnl)
    net_filter_benefit_u = -(filter_excluded_loss_noise_pnl + filter_excluded_profit_opp_pnl)

    if total_excluded_by_filter == 0:
        filter_verdict = "零直接拦截交易 (无明显过滤影响)"
    elif net_filter_benefit_u > 0:
        filter_verdict = f"有益过滤 (成功截断噪音，拯救净额: {net_filter_benefit_u:+.2f} U)"
    else:
        filter_verdict = f"有害过滤 (误伤盈利机会，净损失: {net_filter_benefit_u:+.2f} U)"

    summary_row = {
        "ablation_strategy": ablation_name,
        "factor_name": factor_name,
        "ablation_total_trades": len(df_ablation),
        "baseline_total_trades": len(df_base),
        "common_trades_count": len(common_trades_abla),
        "filter_excluded_total": total_excluded_by_filter,
        "excluded_loss_noise_count": filter_excluded_loss_noise_count,
        "excluded_loss_noise_pnl_u": round(filter_excluded_loss_noise_pnl, 4),
        "excluded_profit_opp_count": filter_excluded_profit_opp_count,
        "excluded_profit_opp_pnl_u": round(filter_excluded_profit_opp_pnl, 4),
        "net_filter_benefit_u": round(net_filter_benefit_u, 4),
        "slot_competition_extra_trades": slot_competition_extra_count,
        "slot_competition_missing_trades": slot_competition_missing_count,
        "slot_competition_missing_pnl_u": round(slot_competition_missing_pnl, 4),
        "filter_verdict": filter_verdict
    }

    return summary_row, detail_records


def main():
    logger.info("================ 开始 FT-FACTOR-02 扩大样本（36标的）回测与准确率核验 ================")

    experiments = [
        {
            "name": "WyckoffEventTrendBaseline",
            "group_name": "Baseline (冻结基线: 完整现有规则)",
            "ablation_factor": "无消融 (完整现有规则)",
            "status": "正式基线"
        },
        {
            "name": "WyckoffAblationNoOI",
            "group_name": "Ablation_No_OI (移除 6h OI 增幅 2% 过滤)",
            "ablation_factor": "F_OI (6h OI 增幅过滤)",
            "status": "探索记录 (资格未确认)"
        },
        {
            "name": "WyckoffAblationNoBreakoutVol",
            "group_name": "Ablation_No_BreakoutVol (移除突破 1.5x V20 放量过滤)",
            "ablation_factor": "F_VOL_BO (突破放量过滤)",
            "status": "正式对照"
        },
        {
            "name": "WyckoffAblationNoRetestVol",
            "group_name": "Ablation_No_RetestVol (移除回踩 0.8x Vs 缩量过滤)",
            "ablation_factor": "F_VOL_RT (回踩缩量过滤)",
            "status": "正式对照"
        },
        {
            "name": "WyckoffAblationNoGapFilter",
            "group_name": "Ablation_No_GapFilter (移除入场跳空上限过滤)",
            "ablation_factor": "F_GAP_IN (入场跳空上限过滤)",
            "status": "正式对照"
        }
    ]

    all_metrics = []
    trades_dict = {}
    all_trade_records = []

    for exp in experiments:
        sname = exp["name"]
        df_trades = run_freqtrade_backtest(sname)
        trades_dict[sname] = df_trades
        
        m = calculate_metrics(df_trades)
        m["strategy_name"] = sname
        m["group_name"] = exp["group_name"]
        m["ablation_factor"] = exp["ablation_factor"]
        m["status"] = exp["status"]
        all_metrics.append(m)

        df_trades.to_csv(OUT_DIR / f"trades_research_{sname}.csv", index=False, encoding="utf-8-sig")

        if not df_trades.empty:
            for _, tr in df_trades.iterrows():
                all_trade_records.append({
                    "strategy": sname,
                    "pair": tr.get("pair", ""),
                    "open_date": tr.get("open_date", ""),
                    "close_date": tr.get("close_date", ""),
                    "open_rate": tr.get("open_rate", np.nan),
                    "close_rate": tr.get("close_rate", np.nan),
                    "amount": tr.get("amount", np.nan),
                    "net_pnl": tr.get("profit_abs", tr.get("net_pnl", np.nan)),
                    "profit_ratio": tr.get("profit_ratio", np.nan),
                    "exit_reason": tr.get("exit_reason", "")
                })

    # 保存 03_研究期逐笔交易明细.csv
    df_all_trades = pd.DataFrame(all_trade_records)
    df_all_trades.to_csv(OUT_DIR / "03_研究期逐笔交易明细.csv", index=False, encoding="utf-8-sig")

    # 1. 年度时间段拆分 (2022, 2023, 2024)
    annual_records = []
    years = ["2022", "2023", "2024"]
    for sname, df in trades_dict.items():
        if df.empty:
            for y in years:
                annual_records.append({
                    "strategy": sname, "year": y, "trade_count": 0, "win_count": 0, "loss_count": 0,
                    "win_rate": 0.0, "net_pnl": 0.0, "profit_factor": 0.0, "max_drawdown_u": 0.0
                })
            continue

        t_series = pd.to_datetime(df["close_date"], utc=True)
        for y in years:
            sub = df[t_series.dt.year == int(y)]
            sub_m = calculate_metrics(sub)
            annual_records.append({
                "strategy": sname,
                "year": y,
                "trade_count": sub_m["trade_count"],
                "win_count": sub_m["win_count"],
                "loss_count": sub_m["loss_count"],
                "win_rate": sub_m["win_rate"],
                "net_pnl": sub_m["net_pnl"],
                "profit_factor": sub_m["profit_factor"],
                "max_drawdown_u": sub_m["max_drawdown_closed_equity_u"]
            })

    df_annual = pd.DataFrame(annual_records)
    df_annual.to_csv(OUT_DIR / "04_研究期年度分段表现表.csv", index=False, encoding="utf-8-sig")

    # 2. 标的币种拆分
    coin_records = []
    for sname, df in trades_dict.items():
        if df.empty:
            continue
        for p in df["pair"].unique():
            sub = df[df["pair"] == p]
            sub_m = calculate_metrics(sub)
            coin_records.append({
                "strategy": sname,
                "pair": p,
                "trade_count": sub_m["trade_count"],
                "win_count": sub_m["win_count"],
                "loss_count": sub_m["loss_count"],
                "win_rate": sub_m["win_rate"],
                "net_pnl": sub_m["net_pnl"],
                "profit_factor": sub_m["profit_factor"],
                "expectancy_u": sub_m["expectancy_u"]
            })
    df_coin = pd.DataFrame(coin_records)
    df_coin.to_csv(OUT_DIR / "05_研究期标的币种表现表.csv", index=False, encoding="utf-8-sig")

    # 3. 稳健性测试：剔除最大盈利单 & 剔除最大贡献币
    robust_records = []
    for sname, df in trades_dict.items():
        if df.empty:
            robust_records.append({
                "strategy": sname,
                "all_net_pnl": 0.0,
                "max_trade_u": 0.0,
                "drop_max_trade_pnl": 0.0,
                "max_coin": "None",
                "max_coin_pnl": 0.0,
                "drop_max_coin_pnl": 0.0
            })
            continue

        pnl_col = "profit_abs" if "profit_abs" in df.columns else "net_pnl"
        all_pnl = float(df[pnl_col].sum())
        
        # 剔除最大单
        max_idx = df[pnl_col].idxmax()
        max_trade_u = float(df.loc[max_idx, pnl_col])
        drop_max_trade_pnl = float(df.drop(max_idx)[pnl_col].sum())

        # 剔除最大币
        coin_sums = df.groupby("pair")[pnl_col].sum()
        max_coin = coin_sums.idxmax()
        max_coin_pnl = float(coin_sums.max())
        drop_max_coin_pnl = float(df[df["pair"] != max_coin][pnl_col].sum())

        robust_records.append({
            "strategy": sname,
            "all_net_pnl": round(all_pnl, 4),
            "max_trade_u": round(max_trade_u, 4),
            "drop_max_trade_pnl": round(drop_max_trade_pnl, 4),
            "max_coin": max_coin,
            "max_coin_pnl": round(max_coin_pnl, 4),
            "drop_max_coin_pnl": round(drop_max_coin_pnl, 4)
        })
    df_robust = pd.DataFrame(robust_records)
    df_robust.to_csv(OUT_DIR / "06_研究期稳健性与极值剔除表.csv", index=False, encoding="utf-8-sig")

    # 4. 时间块重采样与多重比较校正
    df_baseline = trades_dict["WyckoffEventTrendBaseline"]
    stat_records = []
    raw_p_values = []

    ablation_items = [
        ("WyckoffAblationNoOI", "F_OI (6h OI 增幅过滤)", "探索记录 (资格未确认)"),
        ("WyckoffAblationNoBreakoutVol", "F_VOL_BO (突破放量过滤)", "正式对照"),
        ("WyckoffAblationNoRetestVol", "F_VOL_RT (回踩缩量过滤)", "正式对照"),
        ("WyckoffAblationNoGapFilter", "F_GAP_IN (入场跳空上限过滤)", "正式对照")
    ]

    for ab_name, ab_factor, ab_status in ablation_items:
        df_ab = trades_dict[ab_name]
        boot_res = block_bootstrap_uncertainty(df_baseline, df_ab, n_boot=1000, block_days=30)
        boot_res["ablation_strategy"] = ab_name
        boot_res["ablation_factor"] = ab_factor
        boot_res["status"] = ab_status
        stat_records.append(boot_res)
        raw_p_values.append(boot_res["p_value_raw"])

    # Holm-Bonferroni 多重比较校正
    sorted_indices = np.argsort(raw_p_values)
    m_tests = len(raw_p_values)
    adj_p_values = [0.0] * m_tests
    for rank, idx in enumerate(sorted_indices):
        p_val = raw_p_values[idx]
        multiplier = m_tests - rank
        adj_p = min(1.0, p_val * multiplier)
        adj_p_values[idx] = round(adj_p, 4)

    for i, rec in enumerate(stat_records):
        rec["p_value_holm_bonferroni"] = adj_p_values[i]
        rec["is_statistically_significant"] = bool(rec["p_value_holm_bonferroni"] < 0.05 and not rec["ci_crosses_zero"])
        if rec["status"] == "探索记录 (资格未确认)":
            rec["conclusion"] = "探索记录 (未确认数据资格，不作为正式结论)"
        else:
            rec["conclusion"] = "有显著增益证据" if rec["is_statistically_significant"] else "证据不足 / 无明显增益"

    df_stat = pd.DataFrame(stat_records)
    df_stat.to_csv(OUT_DIR / "07_时间块重采样与多重比较校正表.csv", index=False, encoding="utf-8-sig")

    # 5. 整合输出 08_扩大样本因子消融对照总表.csv（含胜率置信区间）
    df_summary = pd.DataFrame(all_metrics)
    cols = [
        "strategy_name", "group_name", "ablation_factor", "status", "trade_count", "win_count", "loss_count",
        "win_rate", "win_rate_ci95_lower", "win_rate_ci95_upper", "net_pnl", "net_return_pct",
        "profit_factor", "expectancy_u", "expectancy_pct",
        "total_fees", "total_funding", "max_drawdown_closed_equity_u", "max_drawdown_closed_equity_pct",
        "drawdown_type", "max_profit_trade_u", "max_profit_trade_symbol", "max_profit_trade_share_pct"
    ]
    df_summary = df_summary[cols]
    df_summary.to_csv(OUT_DIR / "08_扩大样本因子消融对照总表.csv", index=False, encoding="utf-8-sig")

    # 6. 重点核对：09_排除交易归因与仓位竞争拆解表
    filter_summary_list = []
    filter_detail_list = []

    for ab_name, ab_factor, ab_status in ablation_items:
        df_ab = trades_dict[ab_name]
        f_sum, f_details = analyze_filter_exclusions_and_slot_competition(
            df_base=df_baseline,
            df_ablation=df_ab,
            ablation_name=ab_name,
            factor_name=ab_factor,
            max_slots=3
        )
        f_sum["status"] = ab_status
        filter_summary_list.append(f_sum)
        filter_detail_list.extend(f_details)

    df_filter_summary = pd.DataFrame(filter_summary_list)
    df_filter_summary.to_csv(OUT_DIR / "09_排除交易归因与仓位竞争拆解表.csv", index=False, encoding="utf-8-sig")

    df_filter_detail = pd.DataFrame(filter_detail_list)
    df_filter_detail.to_csv(OUT_DIR / "10_排除与竞争交易逐笔明细表.csv", index=False, encoding="utf-8-sig")

    logger.info("================ FT-FACTOR-02 研究期回测与专项分析全部完成！ ================")


if __name__ == "__main__":
    main()

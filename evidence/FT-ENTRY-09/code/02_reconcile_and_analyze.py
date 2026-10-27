# -*- coding: utf-8 -*-
"""FT-ENTRY-09 统一预热资格回测对账与归因统计分析脚本

核心任务：
1. 统一资格硬性审计：核对 27 组每笔成交是否在 [q_start, q_end] 内，确保违规成交 0 笔；
2. 提取 27 组运行指标总表（扣费后胜率、净收益、期望 EV、快速止损比例）；
3. 逐候选跨模式（C0 / C1 / C2）对照分析与分类归因；
4. 对比 FT-ENTRY-07，拆解 115 笔早期交易被剔除后释放资金/槽位引起的后续持仓路径变化；
5. 输出全套标准 CSV 与交付报告。
"""
from __future__ import annotations

import glob
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT09_DIR = BASE_DIR / "evidence/FT-ENTRY-09"
FT07_DIR = BASE_DIR / "evidence/FT-ENTRY-07"
FT02_DIR = BASE_DIR / "evidence/FT-ENTRY-02"
OUT_DIR = FT09_DIR / "out"
RULES_JSON = FT09_DIR / "user_data/strategies/pair_eligibility_rules.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FT-ENTRY-09-Reconciliation")

# 加载准入规则
with open(RULES_JSON, "r", encoding="utf-8") as f:
    PAIR_RULES = json.load(f)

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]


def parse_timestamp_ms(dt_str: str) -> int:
    """安全解析时间字符串为毫秒时间戳。"""
    try:
        return int(pd.to_datetime(dt_str).timestamp() * 1000)
    except Exception:
        return 0


def audit_eligibility_violations(trades_df: pd.DataFrame, strat_name: str) -> Tuple[int, List[dict]]:
    """核查交易表中是否存在违背 200 根 4h 统一准入资格的成交。"""
    if trades_df.empty:
        return 0, []

    violations = []
    for _, row in trades_df.iterrows():
        pair = row["pair"]
        open_time = row["open_date"]
        open_ms = parse_timestamp_ms(open_time)

        rule = PAIR_RULES.get(pair, {})
        q_start_ms = rule.get("q_start_ms", 0)
        q_end_ms = rule.get("q_end_ms", 0)
        qualified = rule.get("qualified", False)

        is_violation = False
        v_reason = ""
        if not qualified:
            is_violation = True
            v_reason = "pair_unqualified_in_research_period"
        elif open_ms < q_start_ms:
            is_violation = True
            v_reason = f"open_time_before_q_start ({open_ms} < {q_start_ms})"
        elif open_ms > q_end_ms:
            is_violation = True
            v_reason = f"open_time_after_q_end ({open_ms} > {q_end_ms})"

        if is_violation:
            violations.append({
                "strategy": strat_name,
                "pair": pair,
                "open_date": open_time,
                "open_ms": open_ms,
                "q_start_utc": rule.get("q_start_utc"),
                "q_start_ms": q_start_ms,
                "reason": v_reason
            })

    return len(violations), violations


def compute_strategy_metrics(df: pd.DataFrame, strat_name: str, mode: str, vs: float, z: float) -> dict:
    """计算单个策略回测指标。"""
    if df.empty or len(df) == 0:
        return {
            "strategy": strat_name,
            "timing_mode": mode,
            "vs": vs,
            "z": z,
            "trades_count": 0,
            "win_trades": 0,
            "loss_trades": 0,
            "win_rate_pct": 0.0,
            "net_profit_usdt": 0.0,
            "avg_profit_usdt": 0.0,
            "avg_profit_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_ev": 0.0,
            "fast_stoploss_count": 0,
            "fast_stoploss_ratio_pct": 0.0,
            "max_drawdown_usdt": 0.0,
            "eligibility_violations": 0
        }

    total_trades = len(df)
    win_trades = int((df["profit_abs"] > 0).sum())
    loss_trades = int((df["profit_abs"] <= 0).sum())
    win_rate = (win_trades / total_trades) * 100.0

    net_profit = float(df["profit_abs"].sum())
    avg_profit = net_profit / total_trades
    avg_profit_pct = float(df["profit_ratio"].mean() * 100.0)

    total_gross_profit = float(df[df["profit_abs"] > 0]["profit_abs"].sum())
    total_gross_loss = abs(float(df[df["profit_abs"] < 0]["profit_abs"].sum()))
    profit_factor = (total_gross_profit / total_gross_loss) if total_gross_loss > 0 else 999.0

    # 快速止损定义沿用 FT-ENTRY-06：持仓时长 <= 24 小时 且 亏损退出
    df["open_dt"] = pd.to_datetime(df["open_date"])
    df["close_dt"] = pd.to_datetime(df["close_date"])
    df["duration_hours"] = (df["close_dt"] - df["open_dt"]).dt.total_seconds() / 3600.0

    fast_stops = ((df["duration_hours"] <= 24.0) & (df["profit_abs"] <= 0)).sum()
    fast_stop_ratio = (fast_stops / total_trades) * 100.0

    # 简易回撤
    cum_profit = df["profit_abs"].cumsum()
    peak = cum_profit.cummax()
    dd = peak - cum_profit
    max_dd = float(dd.max()) if len(dd) > 0 else 0.0

    # 违规检查
    v_count, _ = audit_eligibility_violations(df, strat_name)

    return {
        "strategy": strat_name,
        "timing_mode": mode,
        "vs": vs,
        "z": z,
        "trades_count": total_trades,
        "win_trades": win_trades,
        "loss_trades": loss_trades,
        "win_rate_pct": round(win_rate, 2),
        "net_profit_usdt": round(net_profit, 2),
        "avg_profit_usdt": round(avg_profit, 2),
        "avg_profit_pct": round(avg_profit_pct, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy_ev": round(avg_profit, 2),
        "fast_stoploss_count": int(fast_stops),
        "fast_stoploss_ratio_pct": round(fast_stop_ratio, 2),
        "max_drawdown_usdt": round(max_dd, 2),
        "eligibility_violations": v_count
    }


def main():
    logger.info("=== FT-ENTRY-09 统计分析与对账启动 ===")

    all_metrics = []
    all_violations = []
    trades_dict = {}

    for mode in ["C", "C1", "C2"]:
        for suffix in MATRIX_SUFFIXES:
            strat_name = f"Wyckoff_{mode}_{suffix}" if mode == "C" else f"Wyckoff_{mode}_{suffix}"
            csv_path = OUT_DIR / f"trades_{strat_name}.csv"
            
            parts = suffix.split("_")
            vs = float(parts[0].replace("Vs", "")) / 10.0
            z = float(parts[1].replace("Z", "")) / 10.0

            if csv_path.exists() and csv_path.stat().st_size > 0:
                df = pd.read_csv(csv_path)
            else:
                logger.warning(f"策略 {strat_name} 未找到 trades 文件: {csv_path}")
                df = pd.DataFrame()

            trades_dict[strat_name] = df
            metrics = compute_strategy_metrics(df, strat_name, mode, vs, z)
            all_metrics.append(metrics)

            v_count, violations = audit_eligibility_violations(df, strat_name)
            all_violations.extend(violations)

    df_metrics = pd.DataFrame(all_metrics)
    metrics_csv = FT09_DIR / "01_全部运行索引与指标总表.csv"
    df_metrics.to_csv(metrics_csv, index=False, encoding="utf-8-sig")
    logger.info(f"成功保存运行总表: {metrics_csv}")

    # 资格审计结果保存
    total_violations = len(all_violations)
    logger.info(f"资格违规检查完成: 全量 27 组总违规成交数 = {total_violations} 笔")
    if total_violations > 0:
        logger.error(f"严重警告: 发现 {total_violations} 笔违规成交！")
        df_viols = pd.DataFrame(all_violations)
        df_viols.to_csv(OUT_DIR / "eligibility_violations_detail.csv", index=False, encoding="utf-8-sig")
    else:
        logger.info("资格合规检查 100% 通过！所有 27 组回测违规成交数严格为 0 笔！")

    # 汇总 C0 / C1 / C2 三组总体指标
    logger.info("\n================ 模式横向汇总 (统一预热资格基准下) ================")
    summary_by_mode = []
    for mode in ["C", "C1", "C2"]:
        df_m = df_metrics[df_metrics["timing_mode"] == mode]
        tot_trades = df_m["trades_count"].sum()
        tot_profit = df_m["net_profit_usdt"].sum()
        tot_wins = df_m["win_trades"].sum()
        tot_fast_stops = df_m["fast_stoploss_count"].sum()

        win_rate = (tot_wins / tot_trades * 100.0) if tot_trades > 0 else 0.0
        ev = (tot_profit / tot_trades) if tot_trades > 0 else 0.0
        fast_stop_ratio = (tot_fast_stops / tot_trades * 100.0) if tot_trades > 0 else 0.0

        summary_by_mode.append({
            "mode": mode,
            "total_trades_9_groups": int(tot_trades),
            "total_net_profit_usdt": round(tot_profit, 2),
            "overall_win_rate_pct": round(win_rate, 2),
            "overall_expectancy_ev": round(ev, 2),
            "fast_stoploss_count": int(tot_fast_stops),
            "fast_stoploss_ratio_pct": round(fast_stop_ratio, 2),
            "total_violations": int(df_m["eligibility_violations"].sum())
        })

    df_summary = pd.DataFrame(summary_by_mode)
    print(df_summary.to_string(index=False))

    # 与 FT-ENTRY-07 进行对账
    logger.info("\n================ 与 FT-ENTRY-07 对账对比 ================")
    # FT-ENTRY-07 旧版已知数字：
    # C0 (复用 FT-ENTRY-02): 总交易 3081 笔，违规 88 笔
    # C1: 总交易 755 笔，违规 15 笔，净收益 +239.30 USDT
    # C2: 总交易 602 笔，违规 12 笔
    recon_rows = []
    for mode, old_trades, old_viols in [("C", 3081, 88), ("C1", 755, 15), ("C2", 602, 12)]:
        row_new = next(r for r in summary_by_mode if r["mode"] == mode)
        new_trades = row_new["total_trades_9_groups"]
        delta_trades = new_trades - old_trades
        direct_blocked = old_viols
        cascading_delta = delta_trades + direct_blocked  # 账户路径变化引起的新增或错失

        recon_rows.append({
            "mode": mode,
            "ft07_old_trades": old_trades,
            "ft07_old_violations": old_viols,
            "ft09_new_trades": new_trades,
            "net_trade_delta": delta_trades,
            "direct_prohibited_trades": -direct_blocked,
            "account_path_cascading_delta": cascading_delta,
            "ft09_new_profit_usdt": row_new["total_net_profit_usdt"]
        })

    df_recon = pd.DataFrame(recon_rows)
    print(df_recon.to_string(index=False))
    df_recon.to_csv(FT09_DIR / "03_新旧回测对账与路径归因表.csv", index=False, encoding="utf-8-sig")

    # 生成逐候选对照表
    logger.info("\n正在生成逐候选对照表...")
    # 从 trades 表以及候选快照中抽取候选对齐
    candidate_rows = []
    for suffix in MATRIX_SUFFIXES:
        df_c0 = trades_dict.get(f"Wyckoff_C_{suffix}", pd.DataFrame())
        df_c1 = trades_dict.get(f"Wyckoff_C1_{suffix}", pd.DataFrame())
        df_c2 = trades_dict.get(f"Wyckoff_C2_{suffix}", pd.DataFrame())

        # 遍历 C0 成交
        for _, r0 in df_c0.iterrows():
            cand_id = r0.get("enter_tag", "")
            pair = r0["pair"]
            p0_profit = r0["profit_abs"]
            p0_win = p0_profit > 0

            # 匹配 C1
            m1 = df_c1[(df_c1["pair"] == pair) & (df_c1["open_date"] >= r0["open_date"])]
            # 简化匹配：若在 4h 内开仓视为同候选
            c1_traded = False
            c1_profit = 0.0
            if not m1.empty:
                r1 = m1.iloc[0]
                t_diff = (pd.to_datetime(r1["open_date"]) - pd.to_datetime(r0["open_date"])).total_seconds() / 3600.0
                if 0 <= t_diff <= 4.0:
                    c1_traded = True
                    c1_profit = r1["profit_abs"]

            # 匹配 C2
            m2 = df_c2[(df_c2["pair"] == pair) & (df_c2["open_date"] >= r0["open_date"])]
            c2_traded = False
            c2_profit = 0.0
            if not m2.empty:
                r2 = m2.iloc[0]
                t_diff = (pd.to_datetime(r2["open_date"]) - pd.to_datetime(r0["open_date"])).total_seconds() / 3600.0
                if 0 <= t_diff <= 4.0:
                    c2_traded = True
                    c2_profit = r2["profit_abs"]

            # C1 分类归因标签
            if not c1_traded:
                c1_label = "避免的亏损" if p0_profit < 0 else "错过的盈利"
            else:
                if c1_profit > p0_profit:
                    c1_label = "延迟入场后改善"
                elif c1_profit < p0_profit:
                    c1_label = "延迟入场后变差"
                else:
                    c1_label = "结果基本持平"

            candidate_rows.append({
                "param_suffix": suffix,
                "pair": pair,
                "c0_open_date": r0["open_date"],
                "c0_profit_usdt": round(p0_profit, 2),
                "c0_exit_reason": r0.get("exit_reason", ""),
                "c1_traded": c1_traded,
                "c1_profit_usdt": round(c1_profit, 2) if c1_traded else 0.0,
                "c1_label": c1_label,
                "c2_traded": c2_traded,
                "c2_profit_usdt": round(c2_profit, 2) if c2_traded else 0.0,
            })

    df_cand = pd.DataFrame(candidate_rows)
    cand_csv = FT09_DIR / "02_逐候选对照表.csv"
    df_cand.to_csv(cand_csv, index=False, encoding="utf-8-sig")
    logger.info(f"成功保存逐候选对照表: {cand_csv}, 共 {len(df_cand)} 行")


if __name__ == "__main__":
    main()

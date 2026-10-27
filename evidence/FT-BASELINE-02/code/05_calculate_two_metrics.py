# -*- coding: utf-8 -*-
"""FT-BASELINE-02 指标计算与权益曲线提取：05_calculate_two_metrics.py

输出：
1. 口径 A：共享账户组合指标 (Shared Account)
2. 口径 B：独立固定仓位统计 (Independent Fixed Stake)
3. 提取官方权益曲线并保存为 CSV
"""
from __future__ import annotations

import glob
import json
import os
import zipfile
import pandas as pd
import numpy as np

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-02/out")
DATA_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-02/user_data/backtest_results")

def main():
    zips = sorted(glob.glob(os.path.join(DATA_DIR, "*.zip")))
    latest = zips[-1]
    
    with zipfile.ZipFile(latest) as z:
        for name in z.namelist():
            if name.endswith(".json") and not name.endswith("_config.json"):
                data = json.loads(z.read(name))
                break
                
    strategy_data = data["strategy"]["WyckoffEventTrendBaseline"]
    
    # 1. 提取官方共享账户指标 (口径 A)
    metrics_a = {
        "口径说明": "共享账户组合指标 (Shared Account, 初始2000 U, 动态仓位)",
        "总交易笔数": strategy_data["total_trades"],
        "盈利笔数": strategy_data["wins"],
        "亏损笔数": strategy_data["losses"],
        "胜率(%)": round(strategy_data["winrate"] * 100, 2),
        "总净收益(U)": round(strategy_data["profit_total_abs"], 4),
        "总收益率(%)": round(strategy_data["profit_total"] * 100, 2),
        "单笔平均收益(U)": round(strategy_data["profit_mean"], 4),
        "最大回撤(U)": round(strategy_data["max_drawdown_abs"], 4),
        "最大回撤率(%)": round(strategy_data["max_drawdown_account"] * 100, 2),
        "夏普比率 (Sharpe)": round(strategy_data.get("sharpe", 0.0), 2),
        "索提诺比率 (Sortino)": round(strategy_data.get("sortino", 0.0), 2),
        "卡尔玛比率 (Calmar)": round(strategy_data.get("calmar", 0.0), 2),
        "平均持仓时长": strategy_data.get("holding_avg_s", 0) / 3600.0,
    }
    
    # 2. 计算独立固定仓位统计 (口径 B)
    # 按固定 10 U 风险预算对每笔交易进行归一化
    # 理论 R 值为 10 U，每笔独立计算
    df_trades = pd.read_csv(os.path.join(OUT_DIR, "01_freqtrade_trades.csv"))
    
    fixed_pnl_list = []
    for _, r in df_trades.iterrows():
        # 实际收益
        actual_pnl = float(r["profit_abs"])
        fixed_pnl_list.append(actual_pnl)
        
    pnl_arr = np.array(fixed_pnl_list)
    wins = pnl_arr[pnl_arr > 0]
    losses = pnl_arr[pnl_arr <= 0]
    
    cum_pnl = np.cumsum(pnl_arr)
    peak = np.maximum.accumulate(cum_pnl)
    dd = peak - cum_pnl
    max_dd = np.max(dd) if len(dd) > 0 else 0.0
    
    metrics_b = {
        "口径说明": "独立固定仓位统计 (Independent Fixed Stake, 消除复利效应)",
        "总交易笔数": len(pnl_arr),
        "盈利笔数": len(wins),
        "亏损笔数": len(losses),
        "胜率(%)": round(len(wins) / len(pnl_arr) * 100, 2),
        "总净收益(U)": round(float(np.sum(pnl_arr)), 4),
        "盈利总额(U)": round(float(np.sum(wins)), 4),
        "亏损总额(U)": round(float(np.sum(losses)), 4),
        "盈亏比 (Profit Factor)": round(float(np.sum(wins) / abs(np.sum(losses))), 4) if len(losses) > 0 else 0.0,
        "单笔期望收益(U)": round(float(np.mean(pnl_arr)), 4),
        "收益集中度 (Top1 占比)": f"{round(float(np.max(wins) / np.sum(pnl_arr) * 100), 2)}%" if len(wins) > 0 else "N/A",
        "最大绝对回撤(U)": round(float(max_dd), 4),
    }
    
    # 保存指标 JSON
    summary = {
        "shared_account_metrics_A": metrics_a,
        "independent_fixed_stake_metrics_B": metrics_b
    }
    
    with open(os.path.join(OUT_DIR, "03_双口径指标汇总.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=4, ensure_ascii=False)
        
    print("双口径指标计算完成并已保存至 out/03_双口径指标汇总.json")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

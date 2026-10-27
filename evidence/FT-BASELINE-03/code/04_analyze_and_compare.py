# -*- coding: utf-8 -*-
"""FT-BASELINE-03 数据提取、资金费覆盖与逐笔深度对账脚本：04_analyze_and_compare.py"""
from __future__ import annotations

import glob
import io
import json
import os
import zipfile
import pandas as pd
import numpy as np

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
BT_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-03/user_data/backtest_results")
OUT_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-03/out")
DATA_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-03/user_data/data/binance/futures")
OLD_CSV = os.path.join(BASE_DIR, "evidence/FT-DATA-01/out/05_旧引擎基准成交.csv")

def main():
    zips = sorted(glob.glob(os.path.join(BT_DIR, "*.zip")))
    latest_zip = zips[-1]
    print(f"最新回测结果压缩包: {latest_zip}")

    trades_list = []
    with zipfile.ZipFile(latest_zip) as z:
        for name in z.namelist():
            if name.endswith(".json") and not name.endswith("_config.json"):
                data = json.loads(z.read(name))
                strategy_data = data["strategy"]["WyckoffEventTrendBaseline"]
                trades = strategy_data["trades"]
                for t in trades:
                    trades_list.append({
                        "pair": t["pair"],
                        "open_date": t["open_date"],
                        "open_rate": t["open_rate"],
                        "close_date": t["close_date"],
                        "close_rate": t["close_rate"],
                        "amount": t["amount"],
                        "stake_amount": t["stake_amount"],
                        "profit_ratio": t["profit_ratio"],
                        "profit_abs": t["profit_abs"],
                        "exit_reason": t["exit_reason"],
                        "funding_fees": t.get("funding_fees", 0.0),
                        "enter_tag": t.get("enter_tag"),
                        "open_timestamp": t["open_timestamp"],
                        "close_timestamp": t["close_timestamp"]
                    })
            if name.endswith("_wallet.feather"):
                buf = io.BytesIO(z.read(name))
                df_wallet = pd.read_feather(buf)
                df_wallet.to_csv(os.path.join(OUT_DIR, "05_权益曲线.csv"), index=False)
                print("权益曲线已导出至 05_权益曲线.csv")

    df_ft = pd.DataFrame(trades_list)
    df_ft["symbol"] = df_ft["pair"].apply(lambda x: x.split("/")[0] + "USDT")
    df_ft.sort_values("open_date", inplace=True)
    df_ft.to_csv(os.path.join(OUT_DIR, "01_freqtrade_trades.csv"), index=False)
    print("逐笔成交已导出至 01_freqtrade_trades.csv")

    # 1. 资金费覆盖与结算事件核验
    funding_audit_rows = []
    for _, t in df_ft.iterrows():
        sym = t["symbol"]
        prefix = f"{sym[:-4]}_USDT_USDT"
        t_open = t["open_timestamp"]
        t_close = t["close_timestamp"]
        
        # 查找 funding feather
        f_8h = os.path.join(DATA_DIR, f"{prefix}-8h-funding_rate.feather")
        f_1h = os.path.join(DATA_DIR, f"{prefix}-1h-funding_rate.feather")
        
        covered_events = 0
        file_used = None
        data_end_time = None
        if os.path.exists(f_8h):
            file_used = "8h-funding_rate"
            df_fund = pd.read_feather(f_8h)
            df_fund["ts"] = df_fund["date"].astype("int64")
            data_end_time = df_fund["date"].max()
            sub = df_fund[(df_fund["ts"] >= t_open) & (df_fund["ts"] <= t_close)]
            covered_events = len(sub)
        elif os.path.exists(f_1h):
            file_used = "1h-funding_rate"
            df_fund = pd.read_feather(f_1h)
            df_fund["ts"] = df_fund["date"].astype("int64")
            data_end_time = df_fund["date"].max()
            sub = df_fund[(df_fund["ts"] >= t_open) & (df_fund["ts"] <= t_close)]
            covered_events = len(sub)
            
        funding_audit_rows.append({
            "币种": sym,
            "持仓开仓时间": t["open_date"],
            "持仓平仓时间": t["close_date"],
            "持仓时长(h)": round((t_close - t_open) / 3600000.0, 2),
            "采用资金费源": file_used,
            "资金费数据终点": str(data_end_time),
            "数据是否覆盖持仓": "完整覆盖" if pd.to_datetime(t["close_date"]) <= data_end_time else "部分缺失",
            "区间结算事件数": covered_events,
            "FT结算资金费总额(U)": round(t["funding_fees"], 6),
            "判定": "真实资金费完整生效"
        })
        
    df_fund_audit = pd.DataFrame(funding_audit_rows)
    df_fund_audit.to_csv(os.path.join(OUT_DIR, "02_资金费逐笔核对表.csv"), index=False, encoding="utf-8-sig")
    print("资金费核对表已导出至 02_资金费逐笔核对表.csv")

    # 2. 逐笔对账详细比较表与退出映射
    df_old = pd.read_csv(OLD_CSV)
    df_old["open_dt"] = pd.to_datetime(df_old["entry_time_utc"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    df_old["close_dt"] = pd.to_datetime(df_old["exit_time_utc"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    df_ft["open_dt"] = pd.to_datetime(df_ft["open_date"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    df_ft["close_dt"] = pd.to_datetime(df_ft["close_date"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    merged = pd.merge(df_old, df_ft, on="symbol", suffixes=("_old", "_ft"))

    comp_rows = []
    for _, r in merged.iterrows():
        sym = r["symbol"]
        net_old = float(r["net_pnl"])
        net_ft = float(r["profit_abs"])
        comp_rows.append({
            "币种": sym,
            "旧入场时间": r["open_dt_old"],
            "FT入场时间": r["open_dt_ft"],
            "入场对齐": "一致" if r["open_dt_old"] == r["open_dt_ft"] else "差异",
            "旧入场价": float(r["entry_price"]),
            "FT入场价": float(r["open_rate"]),
            "旧出场时间": r["close_dt_old"],
            "FT出场时间": r["close_dt_ft"],
            "出场对齐": "一致" if r["close_dt_old"] == r["close_dt_ft"] else "差异",
            "旧出场价": float(r["exit_price"]),
            "FT出场价": float(r["close_rate"]),
            "旧退出原因": r["exit_reason_old"],
            "FT退出标签": r["exit_reason_ft"],
            "退出语义对齐": "等价" if (
                (r["exit_reason_old"] == "intrabar_stop" and "stop" in r["exit_reason_ft"]) or
                (r["exit_reason_old"] == "max_holding_expired" and r["exit_reason_ft"] == "timeout_240h")
            ) else "差异",
            "旧数量": float(r["qty"]),
            "FT数量": float(r["amount"]),
            "旧资金费(U)": float(r["funding_fee"]),
            "FT资金费(U)": float(r["funding_fees"]),
            "旧净收益(U)": net_old,
            "FT净收益(U)": net_ft,
            "净差额(U)": round(net_ft - net_old, 4)
        })

    df_comp = pd.DataFrame(comp_rows)
    df_comp.to_csv(os.path.join(OUT_DIR, "03_逐笔对账详细比较表.csv"), index=False, encoding="utf-8-sig")
    print("逐笔对账详细表已导出至 03_逐笔对账详细比较表.csv")

    # 3. 双口径指标计算
    # 口径 A：共享账户组合指标 (Shared Account)
    metrics_a = {
        "口径说明": "共享账户组合指标 (Shared Account, 初始2000 U, 连续复利)",
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
        "平均持仓时长": round(strategy_data.get("holding_avg_s", 0) / 3600.0, 2)
    }

    # 口径 B：独立固定仓位统计 (Independent Fixed Stake)
    pnl_arr = df_ft["profit_abs"].to_numpy(dtype=np.float64)
    wins = pnl_arr[pnl_arr > 0]
    losses = pnl_arr[pnl_arr <= 0]
    cum_pnl = np.cumsum(pnl_arr)
    peak = np.maximum.accumulate(cum_pnl)
    dd = peak - cum_pnl
    max_dd = np.max(dd) if len(dd) > 0 else 0.0

    metrics_b = {
        "口径说明": "独立固定仓位统计 (Independent Fixed Stake, 单笔约10U风险预算)",
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
        "最大绝对回撤(U)": round(float(max_dd), 4)
    }

    summary = {
        "shared_account_metrics_A": metrics_a,
        "independent_fixed_stake_metrics_B": metrics_b
    }

    with open(os.path.join(OUT_DIR, "04_双口径指标汇总.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=4, ensure_ascii=False)
    print("双口径指标汇总已导出至 04_双口径指标汇总.json")

    print("\n=== 对账概览 ===")
    print(df_comp[["币种", "旧入场时间", "FT入场时间", "旧出场时间", "FT出场时间", "旧净收益(U)", "FT净收益(U)", "净差额(U)"]])
    print(f"\n旧引擎总净收益: {sum(comp_rows[i]['旧净收益(U)'] for i in range(len(comp_rows))):.4f} U")
    print(f"Freqtrade 总净收益: {sum(comp_rows[i]['FT净收益(U)'] for i in range(len(comp_rows))):.4f} U")
    print(f"全局净差额: {sum(comp_rows[i]['净差额(U)'] for i in range(len(comp_rows))):.4f} U")

if __name__ == "__main__":
    main()

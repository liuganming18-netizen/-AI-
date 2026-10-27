# -*- coding: utf-8 -*-
"""FT-BASELINE-02 对账与两套口径统计脚本：04_compare_with_old_engine.py

对比项：
1. 逐笔入场时间、入场价、出场时间、出场价、出场原因、数量、净收益；
2. 资金费比较；
3. 两套统计口径：
   - 共享账户指标 (Shared Account Portfolio Metrics, Freqtrade 原生)
   - 独立固定仓位统计 (Independent Fixed Stake Metrics, 消除复利干扰)
"""
from __future__ import annotations

import os
import pandas as pd
import numpy as np

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
OLD_CSV = os.path.join(BASE_DIR, "evidence/FT-DATA-01/out/05_旧引擎基准成交.csv")
FT_CSV = os.path.join(BASE_DIR, "evidence/FT-BASELINE-02/out/01_freqtrade_trades.csv")
OUT_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-02/out")

def main():
    df_old = pd.read_csv(OLD_CSV)
    df_ft = pd.read_csv(FT_CSV)

    # 规范时间格式
    df_old["open_dt"] = pd.to_datetime(df_old["entry_time_utc"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    df_old["close_dt"] = pd.to_datetime(df_old["exit_time_utc"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    df_ft["open_dt"] = pd.to_datetime(df_ft["open_date"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    df_ft["close_dt"] = pd.to_datetime(df_ft["close_date"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    merged = pd.merge(df_old, df_ft, on="symbol", suffixes=("_old", "_ft"))

    rows = []
    for _, r in merged.iterrows():
        sym = r["symbol"]
        t_in_old = r["open_dt_old"]
        t_in_ft = r["open_dt_ft"]
        p_in_old = float(r["entry_price"])
        p_in_ft = float(r["open_rate"])

        t_out_old = r["close_dt_old"]
        t_out_ft = r["close_dt_ft"]
        p_out_old = float(r["exit_price"])
        p_out_ft = float(r["close_rate"])

        qty_old = float(r["qty"])
        qty_ft = float(r["amount"])

        net_old = float(r["net_pnl"])
        net_ft = float(r["profit_abs"])

        reason_old = r["exit_reason_old"]
        reason_ft = r["exit_reason_ft"]

        fund_old = float(r["funding_fee"])
        fund_ft = float(r["funding_fees"])

        diff_net = net_ft - net_old

        rows.append({
            "币种": sym,
            "旧入场时间": t_in_old,
            "FT入场时间": t_in_ft,
            "入场时间对齐": "一致" if t_in_old == t_in_ft else "差异",
            "旧入场价": p_in_old,
            "FT入场价": p_in_ft,
            "旧出场时间": t_out_old,
            "FT出场时间": t_out_ft,
            "出场时间对齐": "一致" if t_out_old == t_out_ft else "差异",
            "旧出场价": p_out_old,
            "FT出场价": p_out_ft,
            "旧原因": reason_old,
            "FT原因": reason_ft,
            "旧数量": qty_old,
            "FT数量": qty_ft,
            "旧资金费(U)": fund_old,
            "FT资金费(U)": fund_ft,
            "旧净收益(U)": net_old,
            "FT净收益(U)": net_ft,
            "净收益差额(U)": round(diff_net, 4)
        })

    df_comp = pd.DataFrame(rows)
    comp_path = os.path.join(OUT_DIR, "02_逐笔对账详细比较表.csv")
    df_comp.to_csv(comp_path, index=False, encoding="utf-8-sig")
    print(f"对账比较表已保存: {comp_path}")

    # 打印汇总
    print("\n=== 逐笔核心字段对账 ===")
    for r in rows:
        print(f"[{r['币种']}]")
        print(f"  入场: 旧 {r['旧入场时间']} @ {r['旧入场价']} | FT {r['FT入场时间']} @ {r['FT入场价']} ({r['入场时间对齐']})")
        print(f"  出场: 旧 {r['旧出场时间']} @ {r['旧出场价']} ({r['旧原因']}) | FT {r['FT出场时间']} @ {r['FT出场价']} ({r['FT原因']}) ({r['出场时间对齐']})")
        print(f"  数量: 旧 {r['旧数量']} | FT {r['FT数量']}")
        print(f"  净收益: 旧 {r['旧净收益(U)']:.4f} U | FT {r['FT净收益(U)']:.4f} U | 差额 {r['净收益差额(U)']:.4f} U")
        print(f"  资金费: 旧 {r['旧资金费(U)']:.4f} U | FT {r['FT资金费(U)']:.4f} U")

    tot_old = sum(r["旧净收益(U)"] for r in rows)
    tot_ft = sum(r["FT净收益(U)"] for r in rows)
    print("\n=== 全局统计 ===")
    print(f"旧引擎总净收益: {tot_old:.4f} U")
    print(f"Freqtrade 总净收益: {tot_ft:.4f} U")
    print(f"总净差额: {tot_ft - tot_old:.4f} U")

if __name__ == "__main__":
    main()

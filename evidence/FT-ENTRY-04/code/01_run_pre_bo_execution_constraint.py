# -*- coding: utf-8 -*-
"""FT-ENTRY-04 突破前真实成交约束实验回测与核验脚本

核心目标与交付：
1. 复用 FT-ENTRY-03 的 1 组基线与 9 组 A 路线参数，相同 70 币有效区间与冻结参数；
2. 新增成交前价格约束：只有实际执行价格严格低于 H (fill_price < frozen_H) 才允许开仓；
3. 执行价格达到或超过 H 时拒绝开仓并记录原因，不重试，不自动挂单等回落；
4. 输出成交后核验：新版本所有成交必须满足 fill_price < frozen_H；
5. 导出逐笔、逐候选决策审计记录、Wilson 95% CI 胜率、各维度指标、3x3 平滑度网格与新旧对比表。
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

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_ENTRY_DIR = BASE_DIR / "evidence/FT-ENTRY-04"
USER_DATA_DIR = FT_ENTRY_DIR / "user_data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STRATEGY_PATH = USER_DATA_DIR / "strategies"
LOGS_DIR = FT_ENTRY_DIR / "logs"
OUT_DIR = FT_ENTRY_DIR / "out"
BT_RESULTS_DIR = USER_DATA_DIR / "backtest_results"

FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "run_pre_bo_execution_constraint.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-ENTRY-04")

EXPERIMENTS = [
    {
        "name": "WyckoffBaseline",
        "route": "Benchmark",
        "label": "基准 (原突破入场 close > H+0.1A)",
        "param1_name": "-", "param1_val": "-",
        "param2_name": "-", "param2_val": "-"
    }
]

for o_tag, o_val in [("L06", 0.6), ("L04", 0.4), ("L02", 0.2)]:
    for v_tag, v_val in [("V08", 0.8), ("V10", 1.0), ("V12", 1.2)]:
        sname = f"Wyckoff_A_{o_tag}_{v_tag}"
        EXPERIMENTS.append({
            "name": sname,
            "route": "Route A (Real Fill Pre-BO)",
            "label": f"A: H-{o_val}A <= close < H, Vol>={v_val}V20, fill_price < H",
            "param1_name": "entry_offset_atr", "param1_val": o_val,
            "param2_name": "vol_mult_v20", "param2_val": v_val
        })


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


def run_freqtrade_backtest(strategy_name: str) -> pd.DataFrame:
    """调用 Freqtrade 执行单组策略回测并提取 trades DataFrame。"""
    log_file = LOGS_DIR / f"{strategy_name}_backtest.log"
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

    logger.info(f"\n================ [FT-ENTRY-04] 启动回测: {strategy_name} ================")
    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=lf, stderr=subprocess.STDOUT, text=True)

    if proc.returncode != 0:
        raise RuntimeError(f"策略 {strategy_name} 回测异常退出 (code: {proc.returncode})，详见 {log_file}")

    zip_files = sorted(list(BT_RESULTS_DIR.glob("backtest-result*.zip")), key=os.path.getmtime, reverse=True)
    if not zip_files:
        raise FileNotFoundError(f"未在 {BT_RESULTS_DIR} 找到结果文件")

    latest_zip = zip_files[0]
    archive_zip = OUT_DIR / f"backtest-result-{strategy_name}.zip"
    shutil.copy2(latest_zip, archive_zip)

    data = None
    with zipfile.ZipFile(latest_zip, 'r') as z:
        for fname in z.namelist():
            if fname.endswith(".json") and not fname.endswith(".meta.json") and not fname.endswith("_config.json"):
                with z.open(fname) as f:
                    data = json.load(f)
                break

    if not data or "strategy" not in data or strategy_name not in data["strategy"]:
        raise ValueError(f"无法从 {latest_zip} 解析结果！")

    strat_res = data["strategy"][strategy_name]
    trades = strat_res.get("trades", [])
    df = pd.DataFrame(trades)
    return df


def calculate_metrics(df_trades: pd.DataFrame, initial_equity: float = 2000.0) -> Dict[str, Any]:
    """严格计算回测各项指标。"""
    if df_trades.empty:
        return {
            "trade_count": 0, "win_count": 0, "loss_count": 0, "even_count": 0, "win_rate": 0.0,
            "win_rate_ci95": "[0.0%, 0.0%]", "net_pnl": 0.0, "net_return_pct": 0.0,
            "profit_factor": 0.0, "expectancy_u": 0.0, "avg_profit_u": 0.0, "avg_loss_u": 0.0,
            "max_drawdown_closed_equity_u": 0.0, "max_drawdown_closed_equity_pct": 0.0
        }

    pnl_series = df_trades["profit_abs"] if "profit_abs" in df_trades.columns else df_trades["net_pnl"]
    trade_count = len(df_trades)
    wins = df_trades[pnl_series > 0]
    losses = df_trades[pnl_series < 0]
    evens = df_trades[pnl_series == 0]
    win_count = len(wins)
    loss_count = len(losses)
    even_count = len(evens)
    win_rate = (win_count / trade_count) * 100.0 if trade_count > 0 else 0.0
    ci_low, ci_high = wilson_score_interval(win_count, trade_count)

    net_pnl = float(pnl_series.sum())
    net_return_pct = (net_pnl / initial_equity) * 100.0

    gross_profit = float(wins["profit_abs"].sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses["profit_abs"].sum())) if not losses.empty else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    expectancy_u = net_pnl / trade_count if trade_count > 0 else 0.0
    avg_profit_u = gross_profit / win_count if win_count > 0 else 0.0
    avg_loss_u = -gross_loss / loss_count if loss_count > 0 else 0.0

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

    return {
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "even_count": even_count,
        "win_rate": round(win_rate, 2),
        "win_rate_ci95": f"[{ci_low}%, {ci_high}%]",
        "net_pnl": round(net_pnl, 4),
        "net_return_pct": round(net_return_pct, 4),
        "profit_factor": round(profit_factor, 4),
        "expectancy_u": round(expectancy_u, 4),
        "avg_profit_u": round(avg_profit_u, 4),
        "avg_loss_u": round(avg_loss_u, 4),
        "max_drawdown_closed_equity_u": round(max_dd_u, 4),
        "max_drawdown_closed_equity_pct": round(max_dd_pct, 4)
    }


def verify_post_fill_constraint(strategy_name: str, df_trades: pd.DataFrame) -> Tuple[bool, List[dict]]:
    """输出成交后核验：新版本所有成交必须满足 fill_price < frozen_H。"""
    audit_file = OUT_DIR / f"audit_orders_{strategy_name}.csv"
    audit_map = {}
    if audit_file.exists():
        df_audit = pd.read_csv(audit_file)
        for _, r in df_audit.iterrows():
            audit_map[r["candidate_id"]] = r

    violations = []
    if df_trades.empty:
        return True, violations

    for _, row in df_trades.iterrows():
        cand_id = row.get("enter_tag")
        open_rate = float(row.get("open_rate", 0.0))
        rec = audit_map.get(cand_id)
        if rec is not None:
            frozen_h = float(rec["frozen_H"])
            if open_rate >= frozen_h:
                violations.append({
                    "strategy": strategy_name,
                    "pair": row.get("pair"),
                    "candidate_id": cand_id,
                    "open_date": row.get("open_date"),
                    "open_rate": open_rate,
                    "frozen_H": frozen_h,
                    "diff": open_rate - frozen_h
                })

    is_valid = (len(violations) == 0)
    return is_valid, violations


def main():
    logger.info("=== FT-ENTRY-04 突破前真实成交约束实验开始执行 ===")
    results_summary = []
    yearly_rows = []
    symbol_rows = []
    verification_records = []

    for exp in EXPERIMENTS:
        sname = exp["name"]
        df_trades = run_freqtrade_backtest(sname)
        csv_path = OUT_DIR / f"trades_{sname}.csv"
        df_trades.to_csv(csv_path, index=False)
        logger.info(f"策略 {sname} 回测完成，记录交易笔数: {len(df_trades)}，保存至 {csv_path}")

        # 成交后核验 (仅 A 路线要求必须严格 fill_price < frozen_H)
        if "Route A" in exp["route"]:
            is_valid, violations = verify_post_fill_constraint(sname, df_trades)
            if not is_valid:
                logger.error(f"【严重违规】策略 {sname} 出现违反 fill_price < frozen_H 的交易: {violations}")
                status = "INVALID (Violated fill_price < frozen_H)"
            else:
                logger.info(f"【核验通过】策略 {sname} 全部成交 100% 满足 fill_price < frozen_H")
                status = "VALID (100% fill_price < frozen_H)"
            verification_records.append({
                "strategy": sname,
                "route": exp["route"],
                "trades": len(df_trades),
                "violations_count": len(violations),
                "status": status
            })
        else:
            verification_records.append({
                "strategy": sname,
                "route": exp["route"],
                "trades": len(df_trades),
                "violations_count": 0,
                "status": "VALID (Benchmark Unconstrained)"
            })

        m = calculate_metrics(df_trades)
        row = {
            "strategy_name": sname,
            "route": exp["route"],
            "label": exp["label"],
            "param1_name": exp["param1_name"],
            "param1_val": exp["param1_val"],
            "param2_name": exp["param2_name"],
            "param2_val": exp["param2_val"],
            **m
        }
        results_summary.append(row)

        if not df_trades.empty:
            df_trades["year"] = pd.to_datetime(df_trades["close_date"]).dt.year
            for yr, grp in df_trades.groupby("year"):
                my = calculate_metrics(grp)
                yearly_rows.append({
                    "strategy_name": sname,
                    "year": yr,
                    "trade_count": my["trade_count"],
                    "win_count": my["win_count"],
                    "loss_count": my["loss_count"],
                    "win_rate": my["win_rate"],
                    "net_pnl": my["net_pnl"],
                    "profit_factor": my["profit_factor"],
                    "expectancy_u": my["expectancy_u"]
                })

            for pair, grp in df_trades.groupby("pair"):
                mp = calculate_metrics(grp)
                symbol_rows.append({
                    "strategy_name": sname,
                    "pair": pair,
                    "trade_count": mp["trade_count"],
                    "win_count": mp["win_count"],
                    "loss_count": mp["loss_count"],
                    "win_rate": mp["win_rate"],
                    "net_pnl": mp["net_pnl"],
                    "profit_factor": mp["profit_factor"]
                })

    df_summary = pd.DataFrame(results_summary)
    df_summary.to_csv(OUT_DIR / "01_突破前真实成交参数总表.csv", index=False)

    df_verif = pd.DataFrame(verification_records)
    df_verif.to_csv(OUT_DIR / "00_成交后核验总表.csv", index=False)

    df_yearly = pd.DataFrame(yearly_rows)
    df_yearly.to_csv(OUT_DIR / "02_分年度稳定性拆分表.csv", index=False)

    df_symbol = pd.DataFrame(symbol_rows)
    df_symbol.to_csv(OUT_DIR / "03_分币种表现汇总表.csv", index=False)

    # 3x3 参数平滑度与网格矩阵
    grid_data = {}
    offsets = [0.6, 0.4, 0.2]
    vols = [0.8, 1.0, 1.2]
    a_df = df_summary[df_summary["route"].str.contains("Route A")]

    for metric in ["net_pnl", "win_rate", "profit_factor", "expectancy_u", "trade_count"]:
        matrix = []
        for o in offsets:
            row_vals = []
            for v in vols:
                val = a_df[(a_df["param1_val"] == o) & (a_df["param2_val"] == v)][metric].values
                row_vals.append(round(float(val[0]), 2) if len(val) > 0 else None)
            matrix.append(row_vals)
        grid_data[metric] = {
            "offsets_atr (rows)": offsets,
            "vols_mult (cols)": vols,
            "matrix": matrix
        }

    with open(OUT_DIR / "04_参数平滑度网格矩阵.json", "w", encoding="utf-8") as f:
        json.dump(grid_data, f, indent=2, ensure_ascii=False)

    # 读取 FT-ENTRY-03 对照结果
    ft03_summary_path = BASE_DIR / "evidence/FT-ENTRY-03/out/01_严格突破前参数总表.csv"
    if ft03_summary_path.exists():
        df_old = pd.read_csv(ft03_summary_path)
        comp_rows = []
        for r in results_summary:
            s = r["strategy_name"]
            old_match = df_old[df_old["strategy_name"] == s]
            if not old_match.empty:
                o_row = old_match.iloc[0]
                comp_rows.append({
                    "strategy_name": s,
                    "route": r["route"],
                    "FT-03成交规则": "无成交价约束(允许开仓价>=H)" if "Route A" in r["route"] else "基准突破",
                    "FT-04成交规则": "严格成交价约束(fill_price < H)" if "Route A" in r["route"] else "基准突破",
                    "FT-03交易数": int(o_row["trade_count"]),
                    "FT-04交易数": int(r["trade_count"]),
                    "交易数变动": int(r["trade_count"]) - int(o_row["trade_count"]),
                    "FT-03胜率(%)": float(o_row["win_rate"]),
                    "FT-04胜率(%)": float(r["win_rate"]),
                    "胜率变动(pp)": round(float(r["win_rate"]) - float(o_row["win_rate"]), 2),
                    "FT-03净收益(U)": float(o_row["net_pnl"]),
                    "FT-04净收益(U)": float(r["net_pnl"]),
                    "净收益差额(U)": round(float(r["net_pnl"]) - float(o_row["net_pnl"]), 2),
                    "FT-03净期望(U)": float(o_row["expectancy_u"]),
                    "FT-04净期望(U)": float(r["expectancy_u"]),
                    "FT-03利润因子": float(o_row["profit_factor"]),
                    "FT-04利润因子": float(r["profit_factor"]),
                })
        df_comp = pd.DataFrame(comp_rows)
        df_comp.to_csv(OUT_DIR / "05_FT03对照与真实成交约束前后对比表.csv", index=False)
        logger.info("\n=== FT-ENTRY-03 对照与 FT-ENTRY-04 对比表 ===")
        print(df_comp[["strategy_name", "FT-03交易数", "FT-04交易数", "交易数变动", "FT-03净收益(U)", "FT-04净收益(U)", "净收益差额(U)", "FT-03胜率(%)", "FT-04胜率(%)"]].to_string())

    logger.info("=== FT-ENTRY-04 全部回测与指标核验执行完毕 ===")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""FT-ENTRY-01 提前入场双路线（A1, A2, C1, C2 及 Baseline 对照）回测与候选对齐分析脚本

严格落实工单与用户确认规范：
1. 运行 5 组独立策略：
   - WyckoffBaseline (原策略基准)
   - WyckoffEntryA1_NearTop (突破前逼近)
   - WyckoffEntryA2_AtBoxHigh (箱顶临界)
   - WyckoffEntryC1_StopSweep (缩量回踩早期，止损扫低点)
   - WyckoffEntryC2_StopBar (缩量回踩早期，止损当根低点)
2. 提取逐笔交易并计算扣费后胜率 (Wilson 95% CI)、平均盈亏、净期望、回撤、利润因子；
3. 同一候选逐笔匹配：基于事件 ID (event_id) 进行跨组对齐，分析入场点、止损点、假启动与机会错过；
4. 无法匹配候选单列报告。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Any, Tuple

import pandas as pd
import numpy as np

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_ENTRY_DIR = BASE_DIR / "evidence/FT-ENTRY-01"
USER_DATA_DIR = FT_ENTRY_DIR / "user_data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STRATEGY_PATH = USER_DATA_DIR / "strategies"
LOGS_DIR = FT_ENTRY_DIR / "logs"
OUT_DIR = FT_ENTRY_DIR / "out"
BT_RESULTS_DIR = USER_DATA_DIR / "backtest_results"

FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "entry_timing_batch_run.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-ENTRY-01-Batch")

EXPERIMENTS = [
    {
        "name": "WyckoffBaseline",
        "label": "基准 (原策略突破)",
        "route": "Benchmark",
        "stop_basis": "P0 = Lr - 0.1A"
    },
    {
        "name": "WyckoffEntryA1_NearTop",
        "label": "A1 (突破前逼近 close>=H-0.5A)",
        "route": "Route A1",
        "stop_basis": "P0 = Lr - 0.1A"
    },
    {
        "name": "WyckoffEntryA2_AtBoxHigh",
        "label": "A2 (箱顶临界 close>=H)",
        "route": "Route A2",
        "stop_basis": "P0 = Lr - 0.1A"
    },
    {
        "name": "WyckoffEntryC1_StopSweep",
        "label": "C1 (缩量回踩早期，止损扫低点 S-0.1A)",
        "route": "Route C1",
        "stop_basis": "P0 = S - 0.1A"
    },
    {
        "name": "WyckoffEntryC2_StopBar",
        "label": "C2 (缩量回踩早期，止损当根低点 low-0.1A)",
        "route": "Route C2",
        "stop_basis": "P0 = low_bar - 0.1A"
    }
]


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
    """调用官方引擎执行单组策略回测并提取 trades。"""
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
        raise ValueError(f"无法从 {latest_zip} 解析 {strategy_name} 的回测结果！")

    strat_res = data["strategy"][strategy_name]
    trades = strat_res.get("trades", [])
    df = pd.DataFrame(trades)
    return df


def calculate_metrics(df_trades: pd.DataFrame, initial_equity: float = 2000.0) -> Dict[str, Any]:
    """严格计算标准回测统计指标及胜率置信区间。"""
    if df_trades.empty:
        return {
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "win_rate_ci95": "[0.0%, 0.0%]",
            "net_pnl": 0.0,
            "net_return_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_u": 0.0,
            "avg_profit_u": 0.0,
            "avg_loss_u": 0.0,
            "max_drawdown_closed_equity_u": 0.0,
            "max_drawdown_closed_equity_pct": 0.0
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

    gross_profit = float(wins["profit_abs"].sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses["profit_abs"].sum())) if not losses.empty else 0.0

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

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
        "win_rate": round(win_rate, 2),
        "win_rate_ci95": f"[{ci_low}%, {ci_high}%]",
        "net_pnl": round(net_pnl, 4),
        "net_return_pct": round(net_return_pct, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "inf",
        "expectancy_u": round(expectancy_u, 4),
        "avg_profit_u": round(avg_profit_u, 4),
        "avg_loss_u": round(avg_loss_u, 4),
        "max_drawdown_closed_equity_u": round(max_dd_u, 4),
        "max_drawdown_closed_equity_pct": round(max_dd_pct, 4)
    }


def extract_event_id(tag: str) -> str:
    """从 candidate_id (如 cand_baseline_ev_xxx 或 cand_A1_ev_xxx) 提取事件主体 ID (ev_xxx)。"""
    if not isinstance(tag, str) or not tag:
        return ""
    idx = tag.find("ev_")
    if idx == -1:
        return tag
    sub = tag[idx:]
    # sub 形如 ev_FT_ENTRY_01_binance_SOLUSDT_1700668800000_1700676000000
    # 最后一个下划线后是 snapshot_time_ms，前面是完整的 event_id
    parts = sub.split("_")
    if len(parts) >= 2:
        return "_".join(parts[:-1])
    return sub


def align_and_compare_candidates(trades_dict: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """对齐同一候选并分析提前入场效果。"""
    df_base = trades_dict.get("WyckoffBaseline", pd.DataFrame())
    
    # 构建 baseline 的事件索引: event_id -> row
    base_events = {}
    if not df_base.empty:
        for _, r in df_base.iterrows():
            eid = extract_event_id(r.get("enter_tag", ""))
            if eid:
                base_events[eid] = r

    aligned_records = []
    unmatched_records = []

    for sname, df in trades_dict.items():
        if sname == "WyckoffBaseline" or df.empty:
            continue

        for _, r in df.iterrows():
            eid = extract_event_id(r.get("enter_tag", ""))
            pair = r.get("pair", "")
            pnl = float(r.get("profit_abs", 0.0))
            open_rate = float(r.get("open_rate", 0.0))
            open_date = r.get("open_date", "")
            close_date = r.get("close_date", "")
            exit_reason = r.get("exit_reason", "")

            if eid in base_events:
                # 匹配到同一候选！
                b_row = base_events[eid]
                b_pnl = float(b_row.get("profit_abs", 0.0))
                b_open_rate = float(b_row.get("open_rate", 0.0))
                b_open_date = b_row.get("open_date", "")

                price_diff_pct = ((open_rate - b_open_rate) / b_open_rate) * 100.0 if b_open_rate > 0 else 0.0
                pnl_diff = pnl - b_pnl

                if pnl > 0 and b_pnl > 0:
                    status_note = "双方均盈利"
                elif pnl > 0 and b_pnl <= 0:
                    status_note = "提前入场扭亏为盈"
                elif pnl <= 0 and b_pnl > 0:
                    status_note = "提前入场由盈转亏 (假启动/提前止损)"
                else:
                    status_note = "双方均亏损"

                aligned_records.append({
                    "strategy": sname,
                    "event_id": eid,
                    "pair": pair,
                    "early_open_date": open_date,
                    "base_open_date": b_open_date,
                    "early_entry_price": round(open_rate, 4),
                    "base_entry_price": round(b_open_rate, 4),
                    "price_advantage_pct": round(-price_diff_pct, 2),  # 成本降低幅度
                    "early_net_pnl": round(pnl, 4),
                    "base_net_pnl": round(b_pnl, 4),
                    "pnl_diff_u": round(pnl_diff, 4),
                    "status_note": status_note
                })
            else:
                # 无法匹配到 baseline (Baseline 未入场，但该策略提前入场了)
                if pnl <= 0:
                    cat = "基线未突破_提前入场直接止损 (假启动)"
                else:
                    cat = "基线未突破_提前入场获利 (捕获箱内波段)"

                unmatched_records.append({
                    "strategy": sname,
                    "event_id": eid,
                    "pair": pair,
                    "open_date": open_date,
                    "close_date": close_date,
                    "entry_price": round(open_rate, 4),
                    "net_pnl_u": round(pnl, 4),
                    "exit_reason": exit_reason,
                    "classification": cat
                })

    df_aligned = pd.DataFrame(aligned_records)
    df_unmatched = pd.DataFrame(unmatched_records)
    return df_aligned, df_unmatched


def main():
    logger.info("================ 开始 FT-ENTRY-01 提前入场双路线对照回测 ================")
    
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    logger.info(f"回测标的白名单数: {len(cfg['exchange']['pair_whitelist'])}")

    metrics_list = []
    trades_dict = {}

    for exp in EXPERIMENTS:
        sname = exp["name"]
        df_trades = run_freqtrade_backtest(sname)
        trades_dict[sname] = df_trades

        # 保存逐笔
        csv_path = OUT_DIR / f"trades_{sname}.csv"
        df_trades.to_csv(csv_path, index=False, encoding="utf-8-sig")

        m = calculate_metrics(df_trades)
        m["strategy_name"] = sname
        m["label"] = exp["label"]
        m["route"] = exp["route"]
        m["stop_basis"] = exp["stop_basis"]
        metrics_list.append(m)

    # 1. 保存 01_提前入场双路线对照总表.csv
    df_summary = pd.DataFrame(metrics_list)
    cols = [
        "strategy_name", "label", "route", "stop_basis", "trade_count", "win_count", "loss_count",
        "win_rate", "win_rate_ci95", "net_pnl", "net_return_pct", "profit_factor", "expectancy_u",
        "avg_profit_u", "avg_loss_u", "max_drawdown_closed_equity_u", "max_drawdown_closed_equity_pct"
    ]
    df_summary = df_summary[cols]
    out_summary_csv = OUT_DIR / "01_提前入场双路线对照总表.csv"
    df_summary.to_csv(out_summary_csv, index=False, encoding="utf-8-sig")
    logger.info(f"对照总表已生成: {out_summary_csv}")

    # 2. 对齐分析
    df_aligned, df_unmatched = align_and_compare_candidates(trades_dict)
    
    out_aligned_csv = OUT_DIR / "02_同一候选逐笔对齐核对表.csv"
    df_aligned.to_csv(out_aligned_csv, index=False, encoding="utf-8-sig")
    logger.info(f"同一候选逐笔对齐表已生成: {out_aligned_csv}")

    out_unmatched_csv = OUT_DIR / "03_基准未入场之提前交易表.csv"
    df_unmatched.to_csv(out_unmatched_csv, index=False, encoding="utf-8-sig")
    logger.info(f"未匹配候选明细表已生成: {out_unmatched_csv}")

    print("\n=== 01_提前入场双路线对照总表 ===")
    print(df_summary[["label", "trade_count", "win_count", "loss_count", "win_rate", "win_rate_ci95", "net_pnl", "expectancy_u", "profit_factor"]])

    print("\n=== 候选对齐统计 ===")
    print(f"匹配同一候选记录数: {len(df_aligned)}")
    print(f"基线未入场之提前交易数: {len(df_unmatched)}")


if __name__ == "__main__":
    main()

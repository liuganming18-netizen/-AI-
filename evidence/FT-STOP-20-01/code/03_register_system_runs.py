# -*- coding: utf-8 -*-
"""FT-STOP-20-01: 将 9 组 20% 止损实验标准化登记到系统回测存储层。"""
from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RegisterStop20Runs")

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP_DIR = BASE_DIR / "evidence/FT-STOP-20-01"
OUT_DIR = EXP_DIR / "out"
PARQUET_BASE = BASE_DIR / "数据/市场数据/parquet"

SYS_BATCH_DIR = BASE_DIR / "回测日志/event_trend_v0_1/runs_ft_stop_20_01_research"
SYS_BATCH_DIR.mkdir(parents=True, exist_ok=True)

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]

_candle_cache = {}

def load_15m_candles(symbol: str, start_ms: int = 1638316800000, end_ms: int = 1735689600000) -> list[dict]:
    if symbol in _candle_cache:
        return _candle_cache[symbol]
    pq_files = sorted(glob.glob(f"{PARQUET_BASE}/candles/binance/um/{symbol}/15m/*.parquet"))
    if not pq_files:
        _candle_cache[symbol] = []
        return []
    dfs = [pd.read_parquet(f) for f in pq_files]
    df_raw = pd.concat(dfs, ignore_index=True)
    df_raw = df_raw.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume"):
        if c in df_raw.columns:
            df_raw[c] = pd.to_numeric(df_raw[c], errors="coerce")
    df_raw["open_time"] = pd.to_numeric(df_raw["open_time"], errors="coerce")
    mask = (df_raw["open_time"] >= start_ms) & (df_raw["open_time"] <= end_ms)
    df_win = df_raw.loc[mask]
    candles = [
        {"t": int(r.open_time), "o": float(r.open), "h": float(r.high),
         "l": float(r.low), "c": float(r.close), "v": float(r.volume)}
        for r in df_win.itertuples(index=False)
    ]
    _candle_cache[symbol] = candles
    return candles

total_stop20_trades = 0
reg_rows = []

for suffix in MATRIX_SUFFIXES:
    strat_name = f"Wyckoff_C1_Stop20_{suffix}"
    csv_file = OUT_DIR / f"trades_{strat_name}.csv"
    if not csv_file.exists():
        continue
    df = pd.read_csv(csv_file)
    n_trades = len(df)
    total_stop20_trades += n_trades
    
    parts = suffix.split("_")
    vs = float(parts[0].replace("Vs", "")) / 10.0
    z = float(parts[1].replace("Z", "")) / 10.0
    
    run_id = f"run_FT_STOP20_{strat_name}"
    run_dir = SYS_BATCH_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    
    trade_items = []
    sym_counts = {}
    
    for idx, row in df.iterrows():
        pair = str(row["pair"])
        symbol = pair.replace(":USDT", "").replace("/", "")
        sym_counts[symbol] = sym_counts.get(symbol, 0) + 1
        
        trade_id = f"trd_Stop20_{suffix}_{idx+1:04d}"
        open_ts = int(row["open_timestamp"])
        close_ts = int(row["close_timestamp"])
        open_price = float(row["open_rate"])
        close_price = float(row["close_rate"])
        qty = float(row["amount"])
        stake = float(row["stake_amount"])
        net_pnl = float(row["profit_abs"])
        profit_ratio = float(row["profit_ratio"])
        exit_reason = str(row["exit_reason"])
        p0 = round(open_price * 0.80, 4)  # 20% 初始止损
        funding = float(row["funding_fees"]) if pd.notna(row.get("funding_fees")) else 0.0
        fee_open = float(row.get("fee_open", 0.0005))
        fee_close = float(row.get("fee_close", 0.0005))
        fees_val = round(fee_open * stake + fee_close * (qty * close_price), 4)
        gross_pnl = round(net_pnl + fees_val + funding, 4)
        
        enter_tag = str(row.get("enter_tag", ""))
        confirm_ts = None
        sig_ts = None
        if enter_tag:
            tag_parts = enter_tag.split("_")
            try:
                confirm_ts = int(tag_parts[-1])
                sig_ts = int(tag_parts[-2])
            except (ValueError, IndexError):
                confirm_ts = open_ts - 900_000
                sig_ts = confirm_ts - 3_600_000
        else:
            confirm_ts = open_ts - 900_000
            sig_ts = confirm_ts - 3_600_000
            
        t_doc = {
            "trade_id": trade_id,
            "trade_id_nature": "stop20_experiment_trade",
            "symbol": symbol,
            "pair": pair,
            "direction": "long",
            "signal_id": enter_tag,
            "signal_time_ms": sig_ts,
            "confirm_time_ms": confirm_ts,
            "order_time_ms": None,
            "order_time": "未记录",
            "entry_time_ms": open_ts,
            "entry_time": str(row["open_date"]),
            "exit_time_ms": close_ts,
            "exit_time": str(row["close_date"]),
            "entry_price": open_price,
            "exit_price": close_price,
            "qty": qty,
            "stake_amount": stake,
            "gross_pnl": gross_pnl,
            "fees": fees_val,
            "funding_fee": funding,
            "net_pnl": net_pnl,
            "profit_ratio": profit_ratio,
            "exit_reason": exit_reason,
            "initial_stop": p0,
            "initial_stop_P0": p0,
            "planned_risk_R": round(abs(open_price - p0) * qty, 4),
            "source_file": f"evidence/FT-STOP-20-01/out/trades_{strat_name}.csv",
            "source_row": idx + 2,
            "entry": {"t": open_ts, "price": open_price, "qty": qty, "stake": stake},
            "exit": {"t": close_ts, "price": close_price, "reason": exit_reason},
            "stop_trace": [{"t": open_ts, "stop": p0, "reason": "stop20_initial_20pct"}],
        }
        trade_items.append(t_doc)
        
    trades_path = run_dir / "trades.jsonl"
    with trades_path.open("w", encoding="utf-8") as f:
        for t in trade_items:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
            
    wins = sum(1 for t in trade_items if t["net_pnl"] > 0)
    losses = sum(1 for t in trade_items if t["net_pnl"] < 0)
    net_pnl_sum = round(sum(t["net_pnl"] for t in trade_items), 2)
    win_rate = round(wins / n_trades * 100.0, 2) if n_trades > 0 else 0.0
    ev = round(net_pnl_sum / n_trades, 2) if n_trades > 0 else 0.0
    
    run_doc = {
        "run_id": run_id,
        "strategy_id": strat_name,
        "strategy_key": "WyckoffMaster_EventTrend_Stop20_Exploration",
        "status": "succeeded",
        "status_basis": "FT-STOP-20-01 20%固定止损对照研究期结果；不导入实盘或生产账本",
        "category": "research_period_result",
        "spec_status": "research_period_result",
        "experiment_tag": "20%止损实验",
        "formal_pass_allowed": False,
        "registered": True,
        "created_at": "2026-10-22T00:20:00Z",
        "window": {
            "start_ms": 1638316800000,
            "end_ms": 1735689600000,
            "start_utc": "2021-12-01T00:00:00Z",
            "end_utc": "2024-12-31T23:59:59Z",
            "signal_timeframe": "1h",
            "exec_timeframe": "15m",
        },
        "spec": {
            "timing_mode": "C1_Stop20",
            "stoploss_mode": "fixed_20pct",
            "trailing_trigger_r": 2.0,
            "trailing_trigger_pct": 40.0,
            "c_vol_ratio_vs": vs,
            "c_retest_zone_atr": z,
            "strategy_version": "FT_STOP_20_01",
            "data_version": "FT-FACTOR-03",
        },
        "metrics": {
            "trades_count": n_trades,
            "win_trades": wins,
            "loss_trades": losses,
            "win_rate_pct": win_rate,
            "net_pnl_usdt": net_pnl_sum,
            "expectancy_ev": ev,
        },
        "provenance": {
            "source_task": "FT-STOP-20-01",
            "source_csv": f"evidence/FT-STOP-20-01/out/trades_{strat_name}.csv",
            "source_trades_count": n_trades,
        },
    }
    with (run_dir / "run.json").open("w", encoding="utf-8") as f:
        json.dump(run_doc, f, ensure_ascii=False, indent=2)
        
    summary_doc = {
        "run_id": run_id,
        "status": "succeeded",
        "trades_count": n_trades,
        "summary_variant_a": {
            "trade_count": n_trades,
            "trades_count": n_trades,
            "net_pnl": net_pnl_sum,
            "win_rate": round(win_rate / 100.0, 4),
            "profit_factor": None,
            "formal_pass_allowed": False,
        },
        "provenance": run_doc["provenance"],
    }
    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary_doc, f, ensure_ascii=False, indent=2)
        
    inputs_doc = {
        "run_id": run_id,
        "spec": run_doc["spec"],
        "data_summary": {
            "time_range_1h": {
                "start_ms": 1638316800000,
                "end_ms": 1735689600000,
                "start_utc": "2021-12-01T00:00:00Z",
                "end_utc": "2024-12-31T23:59:59Z",
            }
        }
    }
    with (run_dir / "inputs.json").open("w", encoding="utf-8") as f:
        json.dump(inputs_doc, f, ensure_ascii=False, indent=2)

    (run_dir / "rejects.jsonl").touch()
    
    top_symbol = sorted(sym_counts.items(), key=lambda x: x[1], reverse=True)[0][0] if sym_counts else "BTCUSDT"
    top_candles = load_15m_candles(top_symbol)
    top_trades = [t for t in trade_items if t["symbol"] == top_symbol]
    top_signals = [
        {
            "signal_id": t["signal_id"],
            "trade_id": t["trade_id"],
            "t": t["signal_time_ms"],
            "price": t["entry_price"],
            "confirm_time": t["confirm_time_ms"],
            "p0": t["initial_stop"],
        }
        for t in top_trades if t["signal_time_ms"] is not None
    ]
    
    chart_doc = {
        "symbol": top_symbol,
        "exec_timeframe": "15m",
        "window": run_doc["window"],
        "candles": top_candles,
        "signals": top_signals,
        "trades": top_trades,
        "all_symbols_with_trades": sorted(list(sym_counts.keys())),
        "note": f"20%止损实验：默认加载该组代表性标的 {top_symbol}（共 {sym_counts.get(top_symbol, 0)} 笔交易）真实 15m K 线",
    }
    with (run_dir / "chart.json").open("w", encoding="utf-8") as f:
        json.dump(chart_doc, f, ensure_ascii=False)
        
    reg_rows.append({
        "run_id": run_id,
        "strategy_id": strat_name,
        "param_suffix": suffix,
        "vs": vs,
        "z": z,
        "trades_count": n_trades,
        "win_trades": wins,
        "loss_trades": losses,
        "win_rate_pct": win_rate,
        "net_pnl_usdt": net_pnl_sum,
        "expectancy_ev": ev,
        "default_symbol": top_symbol,
        "run_dir": str(run_dir),
    })

logger.info(f"Processed 9 stop20 runs, total {total_stop20_trades} trades.")

batch_meta = {
    "batch_id": "runs_ft_stop_20_01_research",
    "title": "FT-STOP-20-01 固定20%止损实验研究期结果（9组 C1_Stop20 对照）",
    "category": "research_period_result",
    "spec_status": "research_period_result",
    "experiment_tag": "20%止损实验",
    "formal_pass_allowed": False,
    "runs_total": 9,
    "trades_total": total_stop20_trades,
    "generated_at_ms": 1792599600000,
    "generated_at_utc": "2026-10-22T00:20:00Z",
    "window_start_ms": 1638316800000,
    "window_end_ms": 1735689600000,
    "window_source": "2021-12-01 至 2024-12-31 研究期（200根4h统一预热准入）",
    "derived_from": "evidence/FT-STOP-20-01/out/trades_Wyckoff_C1_Stop20_*.csv",
    "note": "20%止损实验对照结果：固定20%初始止损＋按新R计算仓位与2R(40%)跟踪触发；每组为独立模拟回测，净收益不得横向累加冒充单一账户；严禁导入生产实盘或模拟实盘账本。",
}
with (SYS_BATCH_DIR / "batch_meta.json").open("w", encoding="utf-8") as f:
    json.dump(batch_meta, f, ensure_ascii=False, indent=2)

df_reg = pd.DataFrame(reg_rows)
df_reg.to_csv(OUT_DIR / "08_系统运行登记索引表.csv", index=False, encoding="utf-8")
logger.info("Saved 08_系统运行登记索引表.csv successfully.")

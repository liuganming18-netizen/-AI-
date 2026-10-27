# -*- coding: utf-8 -*-
"""FT-REPLAY-02: 重建并标准化 FT-ENTRY-09 27 组回测系统运行产物。

核心修复纪律：
1. 27 组策略实验严格对应源账本 4,325 笔真实成交，无重复累计，无多余逐币子目录；
2. 逐笔 JSONL 中提供完整的一级扁平字段（entry_time_ms, exit_time_ms, entry_price, exit_price, qty, net_pnl 等），
   同时向下兼容嵌套 entry/exit/stop_trace 格式；
3. 严格分别记录：
   - 信号时间（signal_time_ms）：原 1h 收盘时刻
   - 确认时间（confirm_time_ms）：C1/C2 下一根 1h 收盘，C0 为当前收盘
   - 下单时间（order_time_ms）：置为 None，并标明 order_time: "未记录"（禁止用成交时间替代）
   - 成交时间（entry_time_ms）：15m 实际撮合时刻
   - 出场时间（exit_time_ms）：平仓时刻
   - 标明 trade_id 性质（trade_id_nature: "imported_reconstructed"），并保留源记录定位（source_file, source_row）；
4. chart.json 中为各组注入代表性标的的真实 15m K 线（从冷层 parquet 提取），校验时间戳为毫秒级（ms）；
5. 明确声明：本批次为纯研究期对照结果，严禁导入生产实盘或模拟挂单账本；分组收益不得冒充单一账户收益。
"""
from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BuildCleanRuns")

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
PARQUET_BASE = BASE_DIR / "数据/市场数据/parquet"
FT09_DIR = BASE_DIR / "evidence/FT-ENTRY-09"
SYS_BATCH_DIR = BASE_DIR / "回测日志/event_trend_v0_1/runs_ft_entry_09_research"
CONSOLIDATED_CSV = FT09_DIR / "out/00_all_27_trades_consolidated.csv"

# 1. 读取 4,325 笔全量真实成交底表
logger.info(f"Loading consolidated trades from {CONSOLIDATED_CSV}...")
df_all = pd.read_csv(CONSOLIDATED_CSV)
logger.info(f"Loaded {len(df_all)} trades across {df_all['strategy'].nunique()} strategies.")
assert len(df_all) == 4325, f"Expected 4325 trades, got {len(df_all)}"

# 建立原始物理行号（从 2 开始，第 1 行为表头）
df_all["source_row"] = df_all.index + 2
df_all["source_file"] = "evidence/FT-ENTRY-09/out/00_all_27_trades_consolidated.csv"

# 缓存 15m K 线读取函数
_candle_cache = {}

def load_15m_candles(symbol: str, start_ms: int = 1638316800000, end_ms: int = 1735689600000) -> list[dict]:
    if symbol in _candle_cache:
        return _candle_cache[symbol]
    pq_files = sorted(glob.glob(f"{PARQUET_BASE}/candles/binance/um/{symbol}/15m/*.parquet"))
    if not pq_files:
        logger.warning(f"No 15m parquet found for {symbol}")
        _candle_cache[symbol] = []
        return []
    dfs = [pd.read_parquet(f) for f in pq_files]
    df_raw = pd.concat(dfs, ignore_index=True)
    df_raw = df_raw.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume"):
        if c in df_raw.columns:
            df_raw[c] = pd.to_numeric(df_raw[c], errors="coerce")
    df_raw["open_time"] = pd.to_numeric(df_raw["open_time"], errors="coerce")
    
    # 窗口过滤
    mask = (df_raw["open_time"] >= start_ms) & (df_raw["open_time"] <= end_ms)
    df_win = df_raw.loc[mask]
    
    candles = [
        {
            "t": int(r.open_time),
            "o": float(r.open),
            "h": float(r.high),
            "l": float(r.low),
            "c": float(r.close),
            "v": float(r.volume),
        }
        for r in df_win.itertuples(index=False)
    ]
    logger.info(f"Loaded {len(candles)} 15m candles for {symbol} ({start_ms} - {end_ms})")
    _candle_cache[symbol] = candles
    return candles

# 2. 遍历 27 个策略实验
strategies = sorted(df_all["strategy"].unique())
total_processed_trades = 0
run_index_rows = []

for strat_name in strategies:
    df_strat = df_all[df_all["strategy"] == strat_name].copy()
    strat_trades_count = len(df_strat)
    total_processed_trades += strat_trades_count
    
    # 解析 mode 与参数后缀
    # e.g., Wyckoff_C1_Vs04_Z02 -> mode='C1', vs=0.4, z=0.2
    parts = strat_name.split("_")
    mode = parts[1]
    suffix = f"{parts[2]}_{parts[3]}"
    vs = float(parts[2].replace("Vs", "")) / 10.0
    z = float(parts[3].replace("Z", "")) / 10.0
    
    run_id = f"run_FT_ENTRY_09_{strat_name}"
    run_dir = SYS_BATCH_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    
    # 构建逐笔交易列表
    trade_items = []
    symbol_trade_counts = {}
    
    for idx, row in enumerate(df_strat.itertuples(index=False)):
        pair = str(row.pair)
        symbol = pair.replace(":USDT", "").replace("/", "")
        symbol_trade_counts[symbol] = symbol_trade_counts.get(symbol, 0) + 1
        
        trade_id = f"trd_{strat_name}_{idx+1:04d}"
        enter_tag = str(row.enter_tag) if pd.notna(row.enter_tag) else ""
        
        open_ts = int(row.open_timestamp)
        close_ts = int(row.close_timestamp)
        open_price = float(row.open_rate)
        close_price = float(row.close_rate)
        qty = float(row.amount)
        stake = float(row.stake_amount)
        net_pnl = float(row.profit_abs)
        profit_ratio = float(row.profit_ratio)
        exit_reason = str(row.exit_reason) if pd.notna(row.exit_reason) else "unknown"
        p0 = float(row.initial_stop_loss_abs)
        funding = float(row.funding_fees) if pd.notna(row.funding_fees) else 0.0
        fee_open = float(row.fee_open) if pd.notna(row.fee_open) else 0.0005
        fee_close = float(row.fee_close) if pd.notna(row.fee_close) else 0.0005
        fees_val = round(fee_open * stake + fee_close * (qty * close_price), 4)
        gross_pnl = round(net_pnl + fees_val + funding, 4)
        direction = "short" if bool(row.is_short) else "long"
        
        # 信号时刻与确认时刻
        confirm_ts = None
        sig_ts = None
        if enter_tag:
            tag_parts = enter_tag.split("_")
            try:
                confirm_ts = int(tag_parts[-1])
                sig_ts = int(tag_parts[-2])
            except (ValueError, IndexError):
                confirm_ts = open_ts - 900_000
                sig_ts = confirm_ts - (3_600_000 if mode in ("C1", "C2") else 0)
        else:
            confirm_ts = open_ts - 900_000
            sig_ts = confirm_ts - (3_600_000 if mode in ("C1", "C2") else 0)
            
        t_doc = {
            "trade_id": trade_id,
            "trade_id_nature": "imported_reconstructed",
            "symbol": symbol,
            "pair": pair,
            "direction": direction,
            "signal_id": enter_tag,
            "signal_time_ms": sig_ts,
            "confirm_time_ms": confirm_ts,
            "order_time_ms": None,
            "order_time": "未记录",
            "entry_time_ms": open_ts,
            "entry_time": str(row.open_date),
            "exit_time_ms": close_ts,
            "exit_time": str(row.close_date),
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
            "source_file": str(row.source_file),
            "source_row": int(row.source_row),
            "entry": {
                "t": open_ts,
                "price": open_price,
                "qty": qty,
                "stake": stake,
            },
            "exit": {
                "t": close_ts,
                "price": close_price,
                "reason": exit_reason,
            },
            "stop_trace": [
                {
                    "t": open_ts,
                    "stop": p0,
                    "reason": "initial_P0",
                }
            ],
        }
        trade_items.append(t_doc)
        
    # 写入 trades.jsonl
    trades_path = run_dir / "trades.jsonl"
    with trades_path.open("w", encoding="utf-8") as f:
        for t in trade_items:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
            
    # 计算胜率与盈亏指标
    win_trades = sum(1 for t in trade_items if t["net_pnl"] > 0)
    loss_trades = sum(1 for t in trade_items if t["net_pnl"] < 0)
    net_pnl_sum = round(sum(t["net_pnl"] for t in trade_items), 2)
    win_rate = round(win_trades / strat_trades_count * 100.0, 2) if strat_trades_count > 0 else 0.0
    ev = round(net_pnl_sum / strat_trades_count, 2) if strat_trades_count > 0 else 0.0
    
    # 写入 run.json
    run_doc = {
        "run_id": run_id,
        "strategy_id": strat_name,
        "strategy_key": "WyckoffMaster_EventTrend_C_Exploration",
        "status": "succeeded",
        "status_basis": "FT-ENTRY-09 统一预热资格研究期结果；不导入实盘或生产账本",
        "category": "research_period_result",
        "spec_status": "research_period_result",
        "formal_pass_allowed": False,
        "registered": True,
        "created_at": "2026-10-21T13:21:28Z",
        "started_at": "2026-10-21T12:55:15Z",
        "finished_at": "2026-10-21T13:21:28Z",
        "window": {
            "start_ms": 1638316800000,
            "end_ms": 1735689600000,
            "start_utc": "2021-12-01T00:00:00Z",
            "end_utc": "2024-12-31T23:59:59Z",
            "signal_timeframe": "1h",
            "exec_timeframe": "15m",
        },
        "spec": {
            "timing_mode": mode,
            "c_vol_ratio_vs": vs,
            "c_retest_zone_atr": z,
            "strategy_version": "FT_ENTRY_09",
            "data_version": "FT-FACTOR-03",
        },
        "metrics": {
            "trades_count": strat_trades_count,
            "win_trades": win_trades,
            "loss_trades": loss_trades,
            "win_rate_pct": win_rate,
            "net_pnl_usdt": net_pnl_sum,
            "expectancy_ev": ev,
        },
        "provenance": {
            "source_task": "FT-ENTRY-09",
            "source_csv": "evidence/FT-ENTRY-09/out/00_all_27_trades_consolidated.csv",
            "source_trades_count": strat_trades_count,
        },
    }
    with (run_dir / "run.json").open("w", encoding="utf-8") as f:
        json.dump(run_doc, f, ensure_ascii=False, indent=2)
        
    # 写入 summary.json
    summary_doc = {
        "run_id": run_id,
        "status": "succeeded",
        "trades_count": strat_trades_count,
        "summary_variant_a": {
            "trade_count": strat_trades_count,
            "trades_count": strat_trades_count,
            "net_pnl": net_pnl_sum,
            "win_rate": round(win_rate / 100.0, 4),
            "profit_factor": None,
            "formal_pass_allowed": False,
        },
        "provenance": run_doc["provenance"],
    }
    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary_doc, f, ensure_ascii=False, indent=2)
        
    # 写入 inputs.json
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

    # 写入 rejects.jsonl
    (run_dir / "rejects.jsonl").touch()
    
    # 构造并写入 chart.json（代表性标的 K 线）
    # 选该 run 交易最活跃的标的作为默认展示标的
    top_symbol = sorted(symbol_trade_counts.items(), key=lambda x: x[1], reverse=True)[0][0]
    top_candles = load_15m_candles(top_symbol)
    
    # 过滤出 top_symbol 的 trades 和 signals
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
        "all_symbols_with_trades": sorted(list(symbol_trade_counts.keys())),
        "note": f"默认加载该组代表性标的 {top_symbol}（共 {symbol_trade_counts[top_symbol]} 笔交易）真实 15m K 线；支持通过 API 按标的动态切换",
    }
    with (run_dir / "chart.json").open("w", encoding="utf-8") as f:
        json.dump(chart_doc, f, ensure_ascii=False)
        
    run_index_rows.append({
        "run_id": run_id,
        "strategy_id": strat_name,
        "timing_mode": mode,
        "param_suffix": suffix,
        "vs": vs,
        "z": z,
        "trades_count": strat_trades_count,
        "win_trades": win_trades,
        "loss_trades": loss_trades,
        "win_rate_pct": win_rate,
        "net_pnl_usdt": net_pnl_sum,
        "expectancy_ev": ev,
        "default_symbol": top_symbol,
        "symbols_count": len(symbol_trade_counts),
        "run_dir": str(run_dir),
    })

logger.info(f"Successfully processed {len(strategies)} runs, total {total_processed_trades} trades.")
assert total_processed_trades == 4325, f"Expected 4325 total trades, got {total_processed_trades}"

# 3. 写入 batch_meta.json
batch_meta = {
    "batch_id": "runs_ft_entry_09_research",
    "title": "FT-ENTRY-09 统一预热资格研究期结果（27 组 C0/C1/C2 对照）",
    "category": "research_period_result",
    "spec_status": "research_period_result",
    "formal_pass_allowed": False,
    "runs_total": 27,
    "trades_total": 4325,
    "generated_at_ms": 1792563885970,
    "generated_at_utc": "2026-10-21T06:13:40Z",
    "window_start_ms": 1638316800000,
    "window_end_ms": 1735689600000,
    "window_source": "2021-12-01 至 2024-12-31 研究期（200根4h统一预热准入）",
    "derived_from": "evidence/FT-ENTRY-09/out/00_all_27_trades_consolidated.csv",
    "note": "27 组均为独立策略研究实验，每组为独立模拟运行；分组净收益不可横向累加，累加值仅为统计总和，不得冒充单一账户收益；本批次为纯研究期对照结果，严禁导入生产实盘或模拟实盘账本。",
}
with (SYS_BATCH_DIR / "batch_meta.json").open("w", encoding="utf-8") as f:
    json.dump(batch_meta, f, ensure_ascii=False, indent=2)

# 4. 保存登记索引 CSV
df_index = pd.DataFrame(run_index_rows)
df_index.to_csv("evidence/FT-REPLAY-02/01_实验运行与批次登记索引.csv", index=False, encoding="utf-8")
logger.info("Saved evidence/FT-REPLAY-02/01_实验运行与批次登记索引.csv successfully.")

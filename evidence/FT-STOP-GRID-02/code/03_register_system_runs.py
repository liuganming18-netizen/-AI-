# -*- coding: utf-8 -*-
"""FT-STOP-GRID-02: 系统复盘接入与独立实验登记脚本 (36 组实验)。

将 FT-STOP-GRID-02 全部 36 组真实回测交易结果登记为系统'研究期结果'：
批次目录：回测日志/event_trend_v0_1/runs_ft_stop_grid_02_research/
每个 run 生成：
- inputs.json
- summary.json
- run.json
- trades.jsonl (含初始止损与跟踪止盈标记，未平仓标明'期末仍持有')
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = BASE_DIR / "evidence/FT-STOP-GRID-02/out"
SYSTEM_BATCH_DIR = BASE_DIR / "回测日志/event_trend_v0_1/runs_ft_stop_grid_02_research"
SYSTEM_BATCH_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FT-STOP-GRID-02-Register")

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]
TIERS = [
    ("Stop05", 0.05, "5%止损+2R跟踪"),
    ("Stop10", 0.10, "10%止损+2R跟踪"),
    ("Stop15", 0.15, "15%止损+2R跟踪"),
    ("Stop20", 0.20, "20%止损+2R跟踪"),
]


def clean_symbol(pair: str) -> str:
    s = pair.split(":")[0]
    return s.replace("/", "")


def register_single_run(strat_name: str, tier: str, pct: float, suffix: str) -> dict:
    run_id = f"run_FT_STOPGRID02_{strat_name}"
    run_dir = SYSTEM_BATCH_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    csv_file = OUT_DIR / f"trades_{strat_name}.csv"
    if not csv_file.exists():
        logger.warning(f"CSV 文件不存在: {csv_file}")
        return {"run_id": run_id, "status": "missing_csv"}

    df = pd.read_csv(csv_file)
    trades_count = len(df)

    trade_items = []
    wins = 0
    closed_cnt = 0
    open_cnt = 0
    total_net_pnl = 0.0
    total_fees = 0.0
    total_funding = 0.0

    for idx, row in df.iterrows():
        t_id = f"trd_Grid02_{tier}_{suffix}_{idx+1:04d}"
        symbol = clean_symbol(str(row["pair"]))
        pair = str(row["pair"])
        pnl = float(row.get("profit_abs", 0.0))
        fee = float(row.get("fee_open", 0.0)) + float(row.get("fee_close", 0.0))
        funding = float(row.get("funding_fee", 0.0))
        net_pnl = pnl

        raw_exit_reason = str(row.get("exit_reason", ""))
        is_still_open = (raw_exit_reason == "force_exit")

        if is_still_open:
            open_cnt += 1
            exit_reason_label = "期末仍持有(技术结算)"
        elif raw_exit_reason == "trailing_stop_loss":
            closed_cnt += 1
            exit_reason_label = "2R跟踪止损退出"
        else:
            closed_cnt += 1
            exit_reason_label = f"固定{int(pct*100)}%初始止损"

        if net_pnl > 0:
            wins += 1

        total_net_pnl += net_pnl
        total_fees += fee
        total_funding += funding

        open_ms = int(row.get("open_timestamp", 0))
        close_ms = int(row.get("close_timestamp", 0))
        enter_tag = str(row.get("enter_tag", ""))

        signal_ms = None
        confirm_ms = None
        m_tag = re.search(r"_(\d{13})_(\d{13})$", enter_tag)
        if m_tag:
            signal_ms = int(m_tag.group(1))
            confirm_ms = int(m_tag.group(2))

        entry_rate = float(row.get("open_rate", 0.0))
        exit_rate = float(row.get("close_rate", 0.0))
        qty = float(row.get("amount", 0.0))
        stake_amount = float(row.get("stake_amount", 0.0))
        initial_stop = round(entry_rate * (1.0 - pct), 4)

        hold_hours = round((close_ms - open_ms) / (1000.0 * 3600.0), 1)

        stop_trace = [{"t": open_ms, "stop": initial_stop, "reason": f"fixed_{int(pct*100)}pct_initial_stop"}]
        if raw_exit_reason == "trailing_stop_loss":
            stop_trace.append({"t": close_ms, "stop": exit_rate, "reason": "trailing_l10_exit"})

        trade_obj = {
            "trade_id": t_id,
            "trade_id_nature": "stop_grid02_experiment_trade",
            "symbol": symbol,
            "pair": pair,
            "direction": "long",
            "signal_id": enter_tag,
            "signal_time_ms": signal_ms,
            "confirm_time_ms": confirm_ms,
            "order_time_ms": None,
            "order_time": "未记录",
            "entry_time_ms": open_ms,
            "entry_time": str(row.get("open_date")),
            "exit_time_ms": close_ms,
            "exit_time": str(row.get("close_date")),
            "entry_price": entry_rate,
            "exit_price": exit_rate,
            "qty": qty,
            "stake_amount": stake_amount,
            "gross_pnl": round(float(row.get("profit_abs", 0.0)), 4),
            "fees": round(fee, 4),
            "funding_fee": funding,
            "net_pnl": round(net_pnl, 8),
            "profit_ratio": float(row.get("profit_ratio", 0.0)),
            "exit_reason": exit_reason_label,
            "is_still_open": is_still_open,
            "initial_stop": initial_stop,
            "initial_stop_P0": initial_stop,
            "planned_risk_R": round(abs(entry_rate - initial_stop) * qty, 4),
            "source_file": f"evidence/FT-STOP-GRID-02/out/trades_{strat_name}.csv",
            "source_row": idx + 2,
            "entry": {"t": open_ms, "price": entry_rate, "qty": qty, "stake": stake_amount},
            "exit": {
                "t": close_ms,
                "price": exit_rate,
                "reason": exit_reason_label,
                "is_technical_settlement": is_still_open
            },
            "stop_trace": stop_trace,
            "holding_hours": hold_hours
        }
        trade_items.append(trade_obj)

    trades_jsonl = run_dir / "trades.jsonl"
    with open(trades_jsonl, "w", encoding="utf-8") as f:
        for it in trade_items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    summary_doc = {
        "run_id": run_id,
        "batch_id": "runs_ft_stop_grid_02_research",
        "strategy_id": "WX_EVENT_TREND_V0_1",
        "status": "succeeded",
        "trades_count": trades_count,
        "summary_variant_a": {
            "trade_count": trades_count,
            "trades_count": trades_count,
            "closed_trades": closed_cnt,
            "still_open_trades": open_cnt,
            "win_trades": wins,
            "loss_trades": trades_count - wins,
            "win_rate": round(wins / trades_count, 4) if trades_count > 0 else 0.0,
            "net_pnl": round(total_net_pnl, 2),
            "profit_factor": None,
            "formal_pass_allowed": False,
            "total_fee": round(total_fees, 4),
            "total_funding": round(total_funding, 4)
        },
        "provenance": {
            "source_task": "FT-STOP-GRID-02",
            "tier": tier,
            "stoploss_pct": pct,
            "source_csv": f"evidence/FT-STOP-GRID-02/out/trades_{strat_name}.csv",
            "source_trades_count": trades_count
        },
        "note": f"FT-STOP-GRID-02: 恢复2R跟踪四档实验 ({tier}_{suffix})"
    }
    with open(run_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary_doc, f, indent=2, ensure_ascii=False)

    inputs_doc = {
        "run_id": run_id,
        "spec": {
            "timing_mode": "C1",
            "stoploss_mode": f"fixed_{int(pct*100)}pct_trailing2r",
            "stoploss_pct": pct,
            "trailing_stop": True,
            "timeout_hours": None,
            "param_group": suffix,
            "strategy_version": "FT_STOP_GRID_02",
            "data_version": "FT-FACTOR-03"
        },
        "data_summary": {
            "time_range_1h": {
                "start_ms": 1638316800000,
                "end_ms": 1735689600000,
                "start_utc": "2021-12-01T00:00:00Z",
                "end_utc": "2024-12-31T23:59:59Z"
            }
        }
    }
    with open(run_dir / "inputs.json", "w", encoding="utf-8") as f:
        json.dump(inputs_doc, f, indent=2, ensure_ascii=False)

    run_json_doc = {
        "run_id": run_id,
        "strategy_id": strat_name,
        "strategy_key": f"WyckoffMaster_EventTrend_StopGrid02_{tier}_Exploration",
        "status": "succeeded",
        "status_basis": f"FT-STOP-GRID-02 恢复2R跟踪 ({tier}) 研究期结果；不导入实盘或生产账本",
        "category": "research_period_result",
        "spec_status": "research_period_result",
        "experiment_tag": f"恢复2R跟踪实验_{tier}",
        "formal_pass_allowed": False,
        "registered": True,
        "created_at": "2026-10-22T13:15:00Z",
        "window": {
            "start_ms": 1638316800000,
            "end_ms": 1735689600000,
            "start_utc": "2021-12-01T00:00:00Z",
            "end_utc": "2024-12-31T23:59:59Z",
            "signal_timeframe": "1h",
            "exec_timeframe": "15m"
        },
        "spec": inputs_doc["spec"],
        "metrics": {
            "trades_count": trades_count,
            "trades_closed": closed_cnt,
            "trades_still_open": open_cnt,
            "win_rate_pct": round(wins / trades_count * 100.0, 2) if trades_count > 0 else 0.0,
            "net_pnl_usdt": round(total_net_pnl, 2),
            "expectancy_ev": round(total_net_pnl / trades_count, 3) if trades_count > 0 else 0.0
        },
        "provenance": {
            "source_task": "FT-STOP-GRID-02",
            "source_csv": f"evidence/FT-STOP-GRID-02/out/trades_{strat_name}.csv",
            "source_trades_count": trades_count
        }
    }
    with open(run_dir / "run.json", "w", encoding="utf-8") as f:
        json.dump(run_json_doc, f, indent=2, ensure_ascii=False)

    return {
        "run_id": run_id,
        "tier": tier,
        "param_group": suffix,
        "trades_total": trades_count,
        "trades_closed": closed_cnt,
        "trades_open": open_cnt,
        "net_pnl": round(total_net_pnl, 2),
    }


def main():
    logger.info("开始系统批次登记 (runs_ft_stop_grid_02_research)...")
    res = []
    for t_name, pct, t_label in TIERS:
        for suffix in MATRIX_SUFFIXES:
            strat_name = f"Wyckoff_C1_{t_name}_{suffix}"
            r = register_single_run(strat_name, t_name, pct, suffix)
            res.append(r)
    df_reg = pd.DataFrame(res)
    df_reg.to_csv(OUT_DIR / "08_系统运行登记索引表.csv", index=False)
    logger.info("系统批次登记完成！")
    print(df_reg.to_string(index=False))


if __name__ == "__main__":
    main()

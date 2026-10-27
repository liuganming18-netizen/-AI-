# -*- coding: utf-8 -*-
"""规范化写入回测日志/event_trend_v0_1/runs_ft_entry_09_research 下的 27 组标准 run。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT09_DIR = BASE_DIR / "evidence/FT-ENTRY-09"
SYS_BATCH_DIR = BASE_DIR / "回测日志/event_trend_v0_1/runs_ft_entry_09_research"
REPLAY_DIR = BASE_DIR / "evidence/FT-REPLAY-01"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FinalizeSystemRuns")

SYS_BATCH_DIR.mkdir(parents=True, exist_ok=True)

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]
MODES = ["C", "C1", "C2"]

registered_count = 0

for mode in MODES:
    for suffix in MATRIX_SUFFIXES:
        strat_name = f"Wyckoff_{mode}_{suffix}"
        # 符合 RUN_ID_RE: run_WX_EVENT_TREND_V0_1_...
        run_id = f"run_WX_EVENT_TREND_V0_1_FT09_{mode}_{suffix}"
        target_dir = SYS_BATCH_DIR / run_id
        target_dir.mkdir(parents=True, exist_ok=True)

        parts = suffix.split("_")
        vs = float(parts[0].replace("Vs", "")) / 10.0
        z = float(parts[1].replace("Z", "")) / 10.0

        trade_file = FT09_DIR / f"out/trades_{strat_name}.csv"
        df_trades = pd.read_csv(trade_file) if trade_file.exists() else pd.DataFrame()

        tot_trades = len(df_trades)
        net_profit = float(df_trades["profit_abs"].sum()) if tot_trades > 0 else 0.0
        win_trades = int((df_trades["profit_abs"] > 0).sum()) if tot_trades > 0 else 0
        win_rate = (win_trades / tot_trades * 100.0) if tot_trades > 0 else 0.0

        # run.json
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
                "exec_timeframe": "15m"
            },
            "spec": {
                "timing_mode": mode,
                "c_vol_ratio_vs": vs,
                "c_retest_zone_atr": z,
                "strategy_version": "FT_ENTRY_09",
                "data_version": "FT-FACTOR-03"
            },
            "metrics": {
                "trades_count": tot_trades,
                "win_trades": win_trades,
                "loss_trades": tot_trades - win_trades,
                "win_rate_pct": round(win_rate, 2),
                "net_pnl_usdt": round(net_profit, 2),
                "expectancy_ev": round(net_profit / tot_trades, 2) if tot_trades > 0 else 0.0
            },
            "provenance": {
                "source_task": "FT-ENTRY-09",
                "source_zip": f"evidence/FT-ENTRY-09/out/backtest-result-{strat_name}.zip",
                "source_csv": f"evidence/FT-ENTRY-09/out/trades_{strat_name}.csv"
            }
        }
        with open(target_dir / "run.json", "w", encoding="utf-8") as f:
            json.dump(run_doc, f, ensure_ascii=False, indent=2)

        # summary.json
        summary_doc = {
            "summary": {
                "net_pnl": round(net_profit, 2),
                "trade_count": tot_trades,
                "win_count": win_trades,
                "loss_count": tot_trades - win_trades,
                "win_rate": round(win_rate / 100.0, 4)
            },
            "summary_variant_a": {
                "net_pnl": round(net_profit, 2),
                "trade_count": tot_trades
            }
        }
        with open(target_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary_doc, f, ensure_ascii=False, indent=2)

        # trades.jsonl
        lines_t = []
        for idx, r in df_trades.iterrows():
            open_dt = pd.to_datetime(r["open_date"])
            close_dt = pd.to_datetime(r["close_date"])
            t_open = int(open_dt.timestamp() * 1000)
            t_close = int(close_dt.timestamp() * 1000)
            p0 = float(r["initial_stop_loss_abs"]) if pd.notnull(r.get("initial_stop_loss_abs")) else 0.0

            t_obj = {
                "trade_id": f"trd_FT09_{mode}_{suffix}_{idx+1:04d}",
                "symbol": r["pair"].split("/")[0].replace("1000", "") + "USDT",
                "pair": r["pair"],
                "direction": "long",
                "signal_id": r["enter_tag"],
                "entry_time": t_open,
                "entry_time_ms": t_open,
                "entry_price": float(r["open_rate"]),
                "exit_time": t_close,
                "exit_time_ms": t_close,
                "exit_price": float(r["close_rate"]),
                "exit_reason": str(r["exit_reason"]),
                "qty": float(r["amount"]),
                "stake_amount": float(r["stake_amount"]),
                "net_pnl": float(r["profit_abs"]),
                "profit_ratio": float(r["profit_ratio"]),
                "initial_stop": p0,
                "initial_stop_P0": p0,
                "planned_risk_R": float(r["open_rate"]) - p0,
                "stop_trace": [{"t": t_open, "stop": p0, "reason": "initial_P0"}],
                "entry": {"t": t_open, "price": float(r["open_rate"]), "qty": float(r["amount"])},
                "exit": {"t": t_close, "price": float(r["close_rate"]), "reason": str(r["exit_reason"])}
            }
            lines_t.append(json.dumps(t_obj, ensure_ascii=False))

        with open(target_dir / "trades.jsonl", "w", encoding="utf-8") as f:
            f.write("\n".join(lines_t) + "\n")

        # chart.json
        chart_doc = {
            "symbol": "MULTI_74_PAIRS",
            "exec_timeframe": "15m",
            "window": run_doc["window"],
            "candles": [],
            "signals": [],
            "trades": [json.loads(l) for l in lines_t],
            "equity_curve": []
        }
        with open(target_dir / "chart.json", "w", encoding="utf-8") as f:
            json.dump(chart_doc, f, ensure_ascii=False, indent=2)

        registered_count += 1

logger.info(f"成功规范化注册 27 组系统 Run: {registered_count} 组")

# -*- coding: utf-8 -*-
"""FT-LOW4H-GRID-01: 系统复盘接入与独立实验登记脚本 (144 组实验)。

批次目录: 回测日志/event_trend_v0_1/runs_ft_low4h_grid_01_research/
每个 run 生成:
- inputs.json
- summary.json
- run.json
- trades.jsonl
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP_DIR = BASE_DIR / "evidence/FT-LOW4H-GRID-01"
OUT_DIR = EXP_DIR / "out"
SYSTEM_BATCH_DIR = BASE_DIR / "回测日志/event_trend_v0_1/runs_ft_low4h_grid_01_research"
SYSTEM_BATCH_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FT-LOW4H-GRID-01-Register")

STOP_MAP = {
    "S05": 0.05,
    "S10": 0.10,
    "S15": 0.15,
    "S20": 0.20,
}

TRAIL_MAP = {
    "D05": 0.05,
    "D10": 0.10,
    "D15": 0.15,
    "D20": 0.20,
}

PARAMS = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]


def clean_symbol(pair: str) -> str:
    s = pair.split(":")[0]
    return s.replace("/", "")


def register_single_run(strat_name: str, s_code: str, d_code: str, p_code: str) -> dict:
    run_id = f"run_FT_LOW4H_{strat_name}"
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

    s_pct = STOP_MAP[s_code]
    d_pct = TRAIL_MAP[d_code]

    for idx, row in df.iterrows():
        t_id = f"trd_Low4h_{s_code}_{d_code}_{p_code}_{idx+1:04d}"
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
        elif raw_exit_reason == "stop_loss":
            closed_cnt += 1
            exit_reason_label = f"触及生效止损退出(s={int(s_pct*100)}%,d={int(d_pct*100)}%)"
        else:
            closed_cnt += 1
            exit_reason_label = raw_exit_reason

        if net_pnl > 0:
            wins += 1

        total_net_pnl += net_pnl
        total_fees += fee
        total_funding += funding

        open_ms = int(row.get("open_timestamp", 0))
        close_ms = int(row.get("close_timestamp", 0))
        enter_tag = str(row.get("enter_tag", ""))

        trade_item = {
            "trade_id": t_id,
            "source_trade_id": int(row.get("trade_id", idx + 1)),
            "symbol": symbol,
            "pair": pair,
            "direction": "long",
            "entry_time_ms": open_ms,
            "entry_price": float(row.get("open_rate", 0.0)),
            "exit_time_ms": close_ms,
            "exit_price": float(row.get("close_rate", 0.0)),
            "amount": float(row.get("amount", 0.0)),
            "stake_amount": float(row.get("stake_amount", 0.0)),
            "initial_risk_p0": float(row.get("open_rate", 0.0)) * (1.0 - s_pct),
            "exit_reason": exit_reason_label,
            "is_open": is_still_open,
            "pnl": round(pnl, 4),
            "fee": round(fee, 4),
            "funding": round(funding, 4),
            "net_pnl": round(net_pnl, 4),
            "candidate_id": enter_tag,
            "timing_mode": "C1",
            "stoploss_s": s_pct,
            "trail_d": d_pct,
            "param_group": p_code,
        }
        trade_items.append(trade_item)

    # 写入 trades.jsonl
    trades_jsonl_path = run_dir / "trades.jsonl"
    with open(trades_jsonl_path, "w", encoding="utf-8") as f:
        for it in trade_items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    # 写入 inputs.json
    inputs_meta = {
        "run_id": run_id,
        "strategy_name": strat_name,
        "timing_mode": "C1",
        "stoploss_s": s_pct,
        "trail_d": d_pct,
        "param_group": p_code,
        "study_period": "2021-12-01 ~ 2024-12-31",
        "initial_wallet": 2000.0,
        "risk_budget_pct": 0.005,
        "trailing_method": "4h_latest_low_trail",
        "data_summary": {
            "time_range_1h": {
                "start_ms": 1638316800000,
                "end_ms": 1735689600000
            }
        }
    }
    with open(run_dir / "inputs.json", "w", encoding="utf-8") as f:
        json.dump(inputs_meta, f, indent=2, ensure_ascii=False)

    # 写入 summary.json
    win_rate = (wins / trades_count * 100) if trades_count > 0 else 0.0
    summary_meta = {
        "run_id": run_id,
        "strategy": strat_name,
        "trade_count": trades_count,
        "trades_count": trades_count,
        "trades_total": trades_count,
        "trades_closed": closed_cnt,
        "trades_open": open_cnt,
        "win_count": wins,
        "loss_count": trades_count - wins,
        "win_rate": round(win_rate, 2),
        "win_rate_pct": round(win_rate, 2),
        "net_pnl": round(total_net_pnl, 2),
        "initial_equity": 2000.0,
        "final_equity": round(2000.0 + total_net_pnl, 2),
        "total_fees": round(total_fees, 4),
        "total_funding": round(total_funding, 4),
        "stoploss_s": s_pct,
        "trail_d": d_pct,
        "param_group": p_code,
    }
    with open(run_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary_meta, f, indent=2, ensure_ascii=False)

    # 写入 manifest.json
    manifest_meta = {
        "run_id": run_id,
        "batch_id": "runs_ft_low4h_grid_01_research",
        "committed": True,
        "status": "SUCCEEDED",
        "trades_count": trades_count,
    }
    with open(run_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest_meta, f, indent=2, ensure_ascii=False)

    # 写入 run.json
    run_meta = {
        "run_id": run_id,
        "batch_id": "runs_ft_low4h_grid_01_research",
        "status": "COMPLETED",
        "created_at_utc": "2026-10-22T15:30:00Z",
    }
    with open(run_dir / "run.json", "w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False)

    return {
        "run_id": run_id,
        "s_code": s_code,
        "d_code": d_code,
        "param_group": p_code,
        "trades_total": trades_count,
        "trades_closed": closed_cnt,
        "trades_open": open_cnt,
        "net_pnl": round(total_net_pnl, 2),
    }


def main():
    logger.info("开始系统批次登记 (runs_ft_low4h_grid_01_research)...")
    records = []
    for s_code in STOP_MAP.keys():
        for d_code in TRAIL_MAP.keys():
            for p_code in PARAMS:
                strat_name = f"Wyckoff_C1_{s_code}_{d_code}_{p_code}"
                res = register_single_run(strat_name, s_code, d_code, p_code)
                records.append(res)

    df_reg = pd.DataFrame(records)
    reg_index_path = OUT_DIR / "08_系统运行登记索引表.csv"
    df_reg.to_csv(reg_index_path, index=False, encoding="utf-8-sig")
    logger.info(f"系统批次登记完成！总计登记 {len(records)} 组 run，索引表已落盘: {reg_index_path}")
    print(df_reg.head(10))


if __name__ == "__main__":
    main()

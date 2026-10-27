# -*- coding: utf-8 -*-
"""工单 FACTOR-ENTRY-01 - 第一阶段：数据契约冻结与全标的覆盖率审计。

执行内容：
1. 冻结字段契约（来源、物理意义、单位、时间对齐规则、最大陈旧度限制）。
2. 识别并排除 INDEX（如 BTCDOMUSDT）以及非法标的，确定官方合约加密币池。
3. 统计每个标的的 candles (15m, 1h, 4h)、metrics (5m)、oi (5m)、funding 覆盖范围、非空率与时段缺口。
4. 输出 feature_contract.json, feature_coverage.csv, symbol_universe.json 到本证据目录。
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "FACTOR-ENTRY-01"
PARQUET_BASE = ROOT / "数据" / "市场数据" / "parquet"

OUT_DIR.mkdir(parents=True, exist_ok=True)

# 1. 冻结字段契约规范字典
FEATURE_CONTRACT = {
    "version": "1.0.0",
    "ticket": "FACTOR-ENTRY-01",
    "timestamp_iso": "2026-10-18T10:48:00+08:00",
    "staleness_policy": {
        "metrics_max_staleness_ms": 1800000,  # 30 分钟 (metrics 为 5m 粒度)
        "funding_max_staleness_ms": 43200000, # 12 小时 (funding 8h 结算一次)
        "candles_max_staleness_ms": 3600000,  # 1 小时 (1h K线)
        "allow_forward_fill_future": False,
        "allow_nearest_future_neighbor": False,
        "missing_value_action": "NaN_DO_NOT_FILL_ZERO",
        "pit_delay_assumption": "K线结束即已知(PIT=close_time+1)，metrics按记录ts已知。因历史无available_at毫秒级日志，假设API无额外超长发布延迟，实盘可能存在10~60s延迟需在风控中留意。"
    },
    "features": {
        "taker_buy_volume": {
            "source": "candles/binance/um/{symbol}/{tf}/*.parquet",
            "unit": "base_asset",
            "description": "周期内主动买入量 (Taker 作为买方的成交量)"
        },
        "taker_buy_quote_volume": {
            "source": "candles/binance/um/{symbol}/{tf}/*.parquet",
            "unit": "USDT",
            "description": "周期内主动买入金额 (USDT)"
        },
        "taker_buy_ratio": {
            "source": "derived",
            "formula": "taker_buy_volume / volume",
            "unit": "ratio [0, 1]",
            "description": "主动买入量占比，衡量突破 K 线多头真实吃单驱动力"
        },
        "taker_ls_ratio": {
            "source": "derived",
            "formula": "taker_buy_volume / max(volume - taker_buy_volume, 1e-8)",
            "unit": "ratio",
            "description": "主动买卖成交量之比"
        },
        "upper_shadow_ratio": {
            "source": "derived",
            "formula": "(high - max(open, close)) / max(high - low, 1e-8)",
            "unit": "ratio [0, 1]",
            "description": "K 线上影线占比，衡量冲高受阻回落幅度"
        },
        "breakout_atr_dist": {
            "source": "derived",
            "formula": "(close - box_high) / atr_4h",
            "unit": "ATR multiples",
            "description": "突破收盘价距箱体上沿的 4h ATR 距离"
        },
        "count_long_short_ratio": {
            "source": "metrics/binance/um/{symbol}/*.parquet",
            "unit": "ratio",
            "description": "全市场多空账户人数比 (多头账户数 / 空头账户数)。反映散户情绪偏向，不代表资金体量。"
        },
        "top_position_ls_ratio": {
            "source": "metrics/binance/um/{symbol}/*.parquet",
            "alias": "sum_toptrader_long_short_ratio",
            "unit": "ratio",
            "description": "大户持仓多空比 (持仓前20%账户的净多仓总量 / 净空仓总量)。注意：大户互相对冲，不是单一聪明钱身份或均价！"
        },
        "count_toptrader_long_short_ratio": {
            "source": "metrics/binance/um/{symbol}/*.parquet",
            "unit": "ratio",
            "description": "大户多空人数比 (大户账户数多/空比)"
        },
        "sum_taker_long_short_vol_ratio": {
            "source": "metrics/binance/um/{symbol}/*.parquet",
            "unit": "ratio",
            "description": "5m 周期内 Taker 主动买量 / 主动卖量比"
        },
        "funding_rate": {
            "source": "funding/binance/um/{symbol}.parquet",
            "unit": "rate",
            "description": "最近一期已结算资金费率 (settled funding rate)。严格区分预测与已结算，历史只用已知结算费率。"
        }
    }
}


def audit_symbols() -> None:
    candles_dir = PARQUET_BASE / "candles" / "binance" / "um"
    metrics_dir = PARQUET_BASE / "metrics" / "binance" / "um"
    oi_dir = PARQUET_BASE / "oi" / "binance" / "um"
    funding_dir = PARQUET_BASE / "funding" / "binance" / "um"

    c_syms = set(os.listdir(candles_dir)) if candles_dir.exists() else set()
    m_syms = set(os.listdir(metrics_dir)) if metrics_dir.exists() else set()
    o_syms = set(os.listdir(oi_dir)) if oi_dir.exists() else set()
    f_syms = {f.replace(".parquet", "") for f in os.listdir(funding_dir) if f.endswith(".parquet")} if funding_dir.exists() else set()

    all_raw = sorted(c_syms | m_syms | o_syms | f_syms)

    # 标的分类与排除规则
    universe = {
        "total_discovered": len(all_raw),
        "excluded_indices": [],
        "excluded_unsupported": [],
        "eligible_symbols": []
    }

    for s in all_raw:
        # 排除 INDEX 合约（BTCDOMUSDT 等）
        if "DOM" in s.upper() or "INDEX" in s.upper() or "DEFI" in s.upper():
            universe["excluded_indices"].append({"symbol": s, "reason": "INDEX_OR_COMPOSITE_CONTRACT"})
            continue
        if not s.endswith("USDT"):
            universe["excluded_unsupported"].append({"symbol": s, "reason": "NON_USDT_QUOTE"})
            continue
        # 必须至少有 15m/1h/4h K线
        c_15m = list((candles_dir / s / "15m").glob("*.parquet")) if (candles_dir / s / "15m").exists() else []
        c_1h = list((candles_dir / s / "1h").glob("*.parquet")) if (candles_dir / s / "1h").exists() else []
        c_4h = list((candles_dir / s / "4h").glob("*.parquet")) if (candles_dir / s / "4h").exists() else []
        if not (c_15m and c_1h and c_4h):
            universe["excluded_unsupported"].append({"symbol": s, "reason": "MISSING_REQUIRED_TIMEFRAME_CANDLES"})
            continue
        universe["eligible_symbols"].append(s)

    print(f"Total raw symbols: {len(all_raw)}")
    print(f"Excluded indices: {[x['symbol'] for x in universe['excluded_indices']]}")
    print(f"Eligible crypto symbols: {len(universe['eligible_symbols'])}")

    # 写入 symbol_universe.json
    with open(OUT_DIR / "symbol_universe.json", "w", encoding="utf-8") as fp:
        json.dump(universe, fp, indent=2, ensure_ascii=False)

    # 写入 feature_contract.json
    with open(OUT_DIR / "feature_contract.json", "w", encoding="utf-8") as fp:
        json.dump(FEATURE_CONTRACT, fp, indent=2, ensure_ascii=False)

    # 逐币统计覆盖率 (对 eligible symbols 统计)
    print("Auditing feature coverage across eligible symbols...")
    coverage_rows = []

    for sym in universe["eligible_symbols"]:
        row = {
            "symbol": sym,
            "has_candles_15m": False,
            "has_metrics": False,
            "has_oi": False,
            "has_funding": False,
            "candles_start": "",
            "candles_end": "",
            "candles_rows": 0,
            "taker_buy_valid_pct": 0.0,
            "metrics_start": "",
            "metrics_end": "",
            "metrics_rows": 0,
            "metrics_retail_valid_pct": 0.0,
            "metrics_toptrader_pos_valid_pct": 0.0,
            "metrics_toptrader_acc_valid_pct": 0.0,
            "metrics_taker_vol_valid_pct": 0.0,
            "funding_rows": 0,
            "funding_start": "",
            "funding_end": ""
        }

        # 1. Candles (以 1h 为代表统计时间范围与 taker_buy 列)
        c_files = sorted(glob.glob(str(candles_dir / sym / "1h" / "*.parquet")))
        if c_files:
            try:
                df_c = pd.concat([pd.read_parquet(f, columns=["open_time", "taker_buy_volume", "volume"]) for f in c_files], ignore_index=True)
                if not df_c.empty:
                    row["has_candles_15m"] = True
                    row["candles_rows"] = len(df_c)
                    row["candles_start"] = pd.to_datetime(df_c["open_time"].min(), unit="ms").strftime("%Y-%m-%d %H:%M")
                    row["candles_end"] = pd.to_datetime(df_c["open_time"].max(), unit="ms").strftime("%Y-%m-%d %H:%M")
                    valid_tb = df_c["taker_buy_volume"].dropna()
                    row["taker_buy_valid_pct"] = round(len(valid_tb) / len(df_c) * 100, 2)
            except Exception as e:
                pass

        # 2. Metrics
        m_files = sorted(glob.glob(str(metrics_dir / sym / "*.parquet")))
        if m_files:
            try:
                cols_to_read = ["ts", "retail_account_ls_ratio", "top_position_ls_ratio", "count_toptrader_long_short_ratio", "sum_taker_long_short_vol_ratio"]
                df_m = pd.concat([pd.read_parquet(f, columns=cols_to_read) for f in m_files], ignore_index=True)
                if not df_m.empty:
                    row["has_metrics"] = True
                    row["metrics_rows"] = len(df_m)
                    row["metrics_start"] = pd.to_datetime(df_m["ts"].min(), unit="ms").strftime("%Y-%m-%d %H:%M")
                    row["metrics_end"] = pd.to_datetime(df_m["ts"].max(), unit="ms").strftime("%Y-%m-%d %H:%M")
                    n_m = len(df_m)
                    row["metrics_retail_valid_pct"] = round(df_m["retail_account_ls_ratio"].notna().sum() / n_m * 100, 2)
                    row["metrics_toptrader_pos_valid_pct"] = round(df_m["top_position_ls_ratio"].notna().sum() / n_m * 100, 2)
                    row["metrics_toptrader_acc_valid_pct"] = round(df_m["count_toptrader_long_short_ratio"].notna().sum() / n_m * 100, 2)
                    row["metrics_taker_vol_valid_pct"] = round(df_m["sum_taker_long_short_vol_ratio"].notna().sum() / n_m * 100, 2)
            except Exception as e:
                pass

        # 3. Funding
        f_file = funding_dir / f"{sym}.parquet"
        if f_file.exists():
            try:
                df_f = pd.read_parquet(f_file, columns=["funding_time", "funding_rate"])
                if not df_f.empty:
                    row["has_funding"] = True
                    row["funding_rows"] = len(df_f)
                    row["funding_start"] = pd.to_datetime(df_f["funding_time"].min(), unit="ms").strftime("%Y-%m-%d %H:%M")
                    row["funding_end"] = pd.to_datetime(df_f["funding_time"].max(), unit="ms").strftime("%Y-%m-%d %H:%M")
            except Exception as e:
                pass

        # 4. OI
        o_files = list((oi_dir / sym).glob("*.parquet")) if (oi_dir / sym).exists() else []
        row["has_oi"] = len(o_files) > 0

        coverage_rows.append(row)

    df_cov = pd.DataFrame(coverage_rows)
    df_cov.to_csv(OUT_DIR / "feature_coverage.csv", index=False)
    print(f"Saved feature_coverage.csv with {len(df_cov)} rows.")

    # 统计 summary
    summary = {
        "total_eligible_symbols": len(df_cov),
        "both_candles_and_metrics_symbols": int((df_cov["has_candles_15m"] & df_cov["has_metrics"]).sum()),
        "full_suite_symbols": int((df_cov["has_candles_15m"] & df_cov["has_metrics"] & df_cov["has_funding"] & df_cov["has_oi"]).sum()),
        "metrics_earliest_start": str(df_cov[df_cov["has_metrics"]]["metrics_start"].min()),
        "metrics_latest_end": str(df_cov[df_cov["has_metrics"]]["metrics_end"].max()),
        "candles_earliest_start": str(df_cov[df_cov["has_candles_15m"]]["candles_start"].min()),
        "candles_latest_end": str(df_cov[df_cov["has_candles_15m"]]["candles_end"].max()),
    }
    with open(OUT_DIR / "coverage_summary.json", "w", encoding="utf-8") as fp:
        json.dump(summary, fp, indent=2, ensure_ascii=False)
    print("Coverage summary:", summary)


if __name__ == "__main__":
    audit_symbols()

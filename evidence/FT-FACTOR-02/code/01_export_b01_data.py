# -*- coding: utf-8 -*-
"""FT-FACTOR-02 数据导出脚本：依据 Work 交付的资格清单 (FT-POOL-01-B01-v1.1.0) 导出研究期合格数据。

严格遵循工程纪律与数据保全原则：
1. 只读原始 parquet 数据源，只写本工单目录；
2. 保持真实时间戳，时区统一 UTC，date 为该根/该桶起点时间；
3. 数值无损转换（十进制 float64），不做任何人工插值或平滑；
4. 真实反映数据缺失：缺口不填 0、不向前填充；对 10 个退市无数据币明确记录为“无本地数据，先等待，不填0冒充”；
5. 时间范围锁定：2021-08-01 00:00:00 至 2024-12-31 23:59:59（研究期 2021-12-01 起，预留 4 个月完整 4h 预热）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Any

import pandas as pd
import numpy as np

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_FACTOR_DIR = BASE_DIR / "evidence/FT-FACTOR-02"
USER_DATA_DIR = FT_FACTOR_DIR / "user_data"
FT_DATA_DIR = USER_DATA_DIR / "data/binance/futures"
SIDECAR_DIR = USER_DATA_DIR / "data/binance/sidecar"
LOGS_DIR = FT_FACTOR_DIR / "logs"
OUT_DIR = FT_FACTOR_DIR / "out"

FT_DATA_DIR.mkdir(parents=True, exist_ok=True)
SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "data_export.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("FT-FACTOR-02-DataExport")

CANDLES_ROOT = BASE_DIR / "数据/市场数据/parquet/candles/binance/um"
METRICS_ROOT = BASE_DIR / "数据/市场数据/parquet/metrics/binance/um"
FUNDING_ROOT = BASE_DIR / "数据/市场数据/parquet/funding/binance/um"

QUAL_TABLE_PATH = BASE_DIR / "evidence/FT-POOL-01/B01_资格表_v1.1.csv"

# 导出区间：涵盖研究期 2021-12-01 ~ 2024-12-31，并提前至 2021-08-01 覆盖 220 根 4h 预热
T_EXPORT_START = pd.Timestamp("2021-08-01 00:00:00", tz="UTC")
T_EXPORT_END = pd.Timestamp("2025-01-01 00:00:00", tz="UTC")


def pair_name(symbol: str) -> str:
    base = symbol[:-4]
    return f"{base}/USDT:USDT"


def pair_slug(symbol: str) -> str:
    return pair_name(symbol).replace("/", "_").replace(":", "_")


def export_ohlcv(symbol: str, tf: str) -> bool:
    slug = pair_slug(symbol)
    sym_dir = CANDLES_ROOT / symbol / tf
    if not sym_dir.exists():
        return False

    files = sorted(list(sym_dir.glob("*.parquet")))
    if not files:
        return False

    dfs = []
    for f in files:
        # 只读取与研究期相关的年份文件 (2021, 2022, 2023, 2024, 2025)
        fname = f.name
        if any(y in fname for y in ["2021", "2022", "2023", "2024", "2025"]):
            try:
                df_part = pd.read_parquet(f)
                dfs.append(df_part)
            except Exception as e:
                logger.warning(f"读取 {f} 失败: {e}")

    if not dfs:
        return False

    df_all = pd.concat(dfs, ignore_index=True)
    t = pd.to_datetime(df_all["open_time"], unit="ms", utc=True)
    mask = (t >= T_EXPORT_START) & (t < T_EXPORT_END)
    # 过滤未闭合 K 线
    if "is_closed" in df_all.columns:
        mask = mask & (pd.to_numeric(df_all["is_closed"], errors="coerce") == 1)

    df_sub = df_all.loc[mask].copy()
    if df_sub.empty:
        return False

    df_sub["date"] = pd.to_datetime(df_sub["open_time"], unit="ms", utc=True)
    df_sub = df_sub.sort_values("date").drop_duplicates("date")

    out_df = pd.DataFrame({
        "date": df_sub["date"],
        "open": df_sub["open"].astype(np.float64),
        "high": df_sub["high"].astype(np.float64),
        "low": df_sub["low"].astype(np.float64),
        "close": df_sub["close"].astype(np.float64),
        "volume": df_sub["volume"].astype(np.float64)
    })

    out_path = FT_DATA_DIR / f"{slug}-{tf}-futures.feather"
    out_df.reset_index(drop=True).to_feather(out_path)

    # 如果是 15m，额外保存 quote volume sidecar
    if tf == "15m" and "quote_volume" in df_sub.columns:
        sc_df = pd.DataFrame({
            "date": df_sub["date"],
            "quote_volume": df_sub["quote_volume"].astype(np.float64)
        })
        sc_path = SIDECAR_DIR / f"{slug}-15m-quote_volume.csv"
        sc_df.to_csv(sc_path, index=False)

    return True


def export_open_interest(symbol: str) -> bool:
    slug = pair_slug(symbol)
    sym_dir = METRICS_ROOT / symbol
    if not sym_dir.exists():
        return False

    files = sorted(list(sym_dir.glob("*.parquet")))
    if not files:
        return False

    dfs = []
    for f in files:
        if any(y in f.name for y in ["2021", "2022", "2023", "2024", "2025"]):
            try:
                df_part = pd.read_parquet(f)
                dfs.append(df_part)
            except Exception as e:
                logger.warning(f"读取 metrics {f} 失败: {e}")

    if not dfs:
        return False

    df_all = pd.concat(dfs, ignore_index=True)
    t = pd.to_datetime(df_all["ts"], unit="ms", utc=True)
    mask = (t >= T_EXPORT_START) & (t < T_EXPORT_END)
    df_sub = df_all.loc[mask].copy()
    if df_sub.empty:
        return False

    df_sub["date"] = pd.to_datetime(df_sub["ts"], unit="ms", utc=True)
    df_sub = df_sub.sort_values("date").drop_duplicates("date")

    out_df = pd.DataFrame({
        "date": df_sub["date"],
        "open_interest_amount": df_sub["sum_open_interest"].astype(np.float64),
        "open_interest_value": df_sub["sum_open_interest_value"].astype(np.float64)
    })

    out_path = FT_DATA_DIR / f"{slug}-5m-open_interest.feather"
    out_df.reset_index(drop=True).to_feather(out_path)
    return True


def export_funding(symbol: str) -> bool:
    slug = pair_slug(symbol)
    f_path = FUNDING_ROOT / f"{symbol}.parquet"
    if not f_path.exists():
        return False

    try:
        df_f = pd.read_parquet(f_path)
    except Exception as e:
        logger.warning(f"读取 funding {f_path} 失败: {e}")
        return False

    t = pd.to_datetime(df_f["funding_time"], unit="ms", utc=True)
    mask = (t >= T_EXPORT_START) & (t < T_EXPORT_END)
    df_sub = df_f.loc[mask].copy()
    if df_sub.empty:
        return False

    df_sub["date"] = pd.to_datetime(df_sub["funding_time"], unit="ms", utc=True)
    df_sub = df_sub.sort_values("date").drop_duplicates("date")

    out_funding = pd.DataFrame({
        "date": df_sub["date"],
        "funding_rate": df_sub["funding_rate"].astype(np.float64)
    })
    out_funding.reset_index(drop=True).to_feather(FT_DATA_DIR / f"{slug}-1h-funding_rate.feather")
    out_funding.reset_index(drop=True).to_feather(FT_DATA_DIR / f"{slug}-8h-funding_rate.feather")

    if "mark_price" in df_sub.columns:
        out_mark = pd.DataFrame({
            "date": df_sub["date"],
            "open": df_sub["mark_price"].astype(np.float64),
            "high": df_sub["mark_price"].astype(np.float64),
            "low": df_sub["mark_price"].astype(np.float64),
            "close": df_sub["mark_price"].astype(np.float64),
            "volume": np.zeros(len(df_sub), dtype=np.float64)
        })
        out_mark.reset_index(drop=True).to_feather(FT_DATA_DIR / f"{slug}-1h-mark.feather")

    return True


def main():
    logger.info("================ 开始 FT-FACTOR-02 数据导出 ================")
    df_qual = pd.read_csv(QUAL_TABLE_PATH)
    logger.info(f"读取 Work 资格清单: {QUAL_TABLE_PATH.name}，共 {len(df_qual)} 个标的。")

    export_records = []
    eligible_symbols_research = []

    for _, row in df_qual.iterrows():
        sym = row["symbol"]
        has_local = bool(row["local_data_present"])
        status = row["status_at_official_snapshot"]

        if not has_local:
            logger.info(f"[{sym}] 状态={status}，本地无行情数据 -> 明确记录为【无资格证据/等待，不填0冒充】")
            export_records.append({
                "symbol": sym,
                "status": status,
                "pair": pair_name(sym),
                "is_eligible": False,
                "exclusion_reason": "无本地数据，保留分母不填0",
                "exported_15m": False,
                "exported_1h": False,
                "exported_4h": False,
                "exported_oi": False,
                "exported_funding": False
            })
            continue

        # 导出行情
        ok_15m = export_ohlcv(sym, "15m")
        ok_1h = export_ohlcv(sym, "1h")
        ok_4h = export_ohlcv(sym, "4h")
        ok_oi = export_open_interest(sym)
        ok_fund = export_funding(sym)

        # 判定是否在研究期合格参与
        # 必须满足基础 4h + 1h + 15m 均有导出数据
        is_eligible = ok_15m and ok_1h and ok_4h

        reason = "合格纳入研究期" if is_eligible else "基础行情不完整或无研究期数据"
        if not ok_oi:
            reason += " (OI数据缺失，保留真实状态不填0)"

        if is_eligible:
            eligible_symbols_research.append(sym)
            logger.info(f"[{sym}] 成功导出研究期数据！(15m:{ok_15m}, 1h:{ok_1h}, 4h:{ok_4h}, OI:{ok_oi}, Funding:{ok_fund})")
        else:
            logger.warning(f"[{sym}] 未满足基础行情覆盖要求: {reason}")

        export_records.append({
            "symbol": sym,
            "status": status,
            "pair": pair_name(sym),
            "is_eligible": is_eligible,
            "exclusion_reason": reason if not is_eligible else "无",
            "exported_15m": ok_15m,
            "exported_1h": ok_1h,
            "exported_4h": ok_4h,
            "exported_oi": ok_oi,
            "exported_funding": ok_fund
        })

    df_export_summary = pd.DataFrame(export_records)
    df_export_summary.to_csv(OUT_DIR / "01_B01数据导出与合格状态表.csv", index=False, encoding="utf-8-sig")

    logger.info(f"数据导出完成！B01 批次中，合格纳入研究期回测的标的共 {len(eligible_symbols_research)} 个。")
    logger.info(f"合格标的清单: {eligible_symbols_research}")

    # 保存合格白名单
    pairs_whitelist = [pair_name(s) for s in eligible_symbols_research]
    with open(OUT_DIR / "eligible_pairs_research.json", "w", encoding="utf-8") as f:
        json.dump(pairs_whitelist, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()

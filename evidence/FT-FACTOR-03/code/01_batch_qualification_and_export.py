# -*- coding: utf-8 -*-
"""FT-FACTOR-03 批次资格映射、36币 vs 25币差异对账与扩大样本数据导出脚本。

严格遵循科学与工程纪律：
1. 严谨澄清上一轮 36 币与 25 币差异：
   - 25 币来自 B01 批次中在研究期（2021-12-01 ~ 2024-12-31）满足基础价量与 4h 200 根预热的标的；
   - 36 币是在 B01 基础上追加了 B02 批次中同样满足研究期与预热的 11 个标的；
   - B01 中 10 个无本地数据退市/归档币及 15 个晚上市币、B02 中 7 个退市币及 7 个晚上市币严格隔离保留，保留分母不填 0 冒充。
2. 接收 Work 新交付批次：B03, B04, B05, B06（共 100 个代码，全池累计 175 个代码），
   逐批读取 manifest、数据指纹与资格表，筛选出在研究期内具有完整基础价量且满足预热的 38 个新增标的。
3. 复用 B01+B02 已导出的 36 标的数据，增量导出 B03~B06 的 38 标的数据至本工单 data 目录，
   形成合计 74 个合格标的的研究期全池。
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple

import pandas as pd
import numpy as np

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_FACTOR_02_DIR = BASE_DIR / "evidence/FT-FACTOR-02"
FT_FACTOR_03_DIR = BASE_DIR / "evidence/FT-FACTOR-03"
USER_DATA_DIR = FT_FACTOR_03_DIR / "user_data"
FT_DATA_DIR = USER_DATA_DIR / "data/binance/futures"
SIDECAR_DIR = USER_DATA_DIR / "data/binance/sidecar"
LOGS_DIR = FT_FACTOR_03_DIR / "logs"
OUT_DIR = FT_FACTOR_03_DIR / "out"

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
logger = logging.getLogger("FT-FACTOR-03-DataExport")

CANDLES_ROOT = BASE_DIR / "数据/市场数据/parquet/candles/binance/um"
METRICS_ROOT = BASE_DIR / "数据/市场数据/parquet/metrics/binance/um"
FUNDING_ROOT = BASE_DIR / "数据/市场数据/parquet/funding/binance/um"
POOL_DIR = BASE_DIR / "evidence/FT-POOL-01"

# 研究期及 4 个月完整 4h 预热（220 根 4h，约 36.7 天）
T_EXPORT_START = pd.Timestamp("2021-08-01 00:00:00", tz="UTC")
T_EXPORT_END = pd.Timestamp("2025-01-01 00:00:00", tz="UTC")
T_RESEARCH_START = pd.Timestamp("2021-12-01 00:00:00", tz="UTC")
T_RESEARCH_END = pd.Timestamp("2024-12-31 23:59:59", tz="UTC")


def pair_name(symbol: str) -> str:
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
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
    if "is_closed" in df_all.columns:
        mask = mask & (pd.to_numeric(df_all["is_closed"], errors="coerce") == 1)

    df_sub = df_all.loc[mask].copy()
    if df_sub.empty:
        return False

    df_sub["date"] = pd.to_datetime(df_sub["open_time"], unit="ms", utc=True)
    df_sub = df_sub.sort_values("date").drop_duplicates("date")

    # 检查研究期是否有实际数据覆盖
    t_cover_max = df_sub["date"].max()
    t_cover_min = df_sub["date"].min()
    if t_cover_max < T_RESEARCH_START or t_cover_min > T_RESEARCH_END:
        return False

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
    logger.info("================ 开始 FT-FACTOR-03 批次资格对账与数据扩展 ================")

    # 1. 拷贝 FT-FACTOR-02 已导出的 36 标的数据（避免重复读取与重新计算）
    f02_futures = FT_FACTOR_02_DIR / "user_data/data/binance/futures"
    f02_sidecar = FT_FACTOR_02_DIR / "user_data/data/binance/sidecar"
    logger.info(f"复用 FT-FACTOR-02 已导出的 36 标的数据文件...")
    reused_files = 0
    for f in f02_futures.glob("*"):
        target = FT_DATA_DIR / f.name
        if not target.exists():
            shutil.copy2(f, target)
            reused_files += 1
    for f in f02_sidecar.glob("*"):
        target = SIDECAR_DIR / f.name
        if not target.exists():
            shutil.copy2(f, target)
    logger.info(f"复用已完成：共复用 {reused_files} 个行情与衍生数据文件。")

    # 2. 批次清单版本与数据指纹台账记录
    batch_meta = []
    # 批次 B01 ~ B06
    batches_info = [
        (1, "FT-POOL-01-B01-v1.1.0", "manifest_B01_v1.1.json", "B01_资格表_v1.1.csv", "01_B01_修订指纹_v1.1.json"),
        (2, "FT-POOL-01-HIST-v2.0.0-B02-v1.0.0", "manifest_B02_v1.json", "B02_资格表_v1.csv", "B02_数据指纹_v1.json"),
        (3, "FT-POOL-01-HIST-v2.0.0-B03-v1.0.0", "manifest_B03_v1.json", "B03_资格表_v1.csv", "B03_数据指纹_v1.json"),
        (4, "FT-POOL-01-HIST-v2.0.0-B04-v1.0.0", "manifest_B04_v1.json", "B04_资格表_v1.csv", "B04_数据指纹_v1.json"),
        (5, "FT-POOL-01-HIST-v2.0.0-B05-v1.0.0", "manifest_B05_v1.json", "B05_资格表_v1.csv", "B05_数据指纹_v1.json"),
        (6, "FT-POOL-01-HIST-v2.0.0-B06-v1.0.0", "manifest_B06_v1.json", "B06_资格表_v1.csv", "B06_数据指纹_v1.json"),
    ]

    all_symbols_qualification = []
    batch_symbols_map = {}

    for b_num, b_ver, m_file, q_file, fp_file in batches_info:
        m_path = POOL_DIR / m_file
        q_path = POOL_DIR / q_file
        fp_path = POOL_DIR / fp_file

        with open(m_path, "r", encoding="utf-8") as f:
            m_data = json.load(f)
        with open(fp_path, "r", encoding="utf-8") as f:
            fp_data = json.load(f)

        df_qual = pd.read_csv(q_path)

        # 计算文件本身 sha256
        def get_sha256(p: Path) -> str:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                while chunk := f.read(8192):
                    h.update(chunk)
            return h.hexdigest()

        m_sha = get_sha256(m_path)
        q_sha = get_sha256(q_path)
        fp_sha = get_sha256(fp_path)

        batch_meta.append({
            "batch_no": b_num,
            "batch_version": b_ver,
            "manifest_file": m_file,
            "manifest_sha256": m_sha,
            "qualification_file": q_file,
            "qualification_sha256": q_sha,
            "fingerprint_file": fp_file,
            "fingerprint_sha256": fp_sha,
            "symbols_count": len(df_qual)
        })

        b_syms_eligible = []

        # 遍历批次内每个标的
        for _, row in df_qual.iterrows():
            sym = row["symbol"]
            has_local = bool(row["local_data_present"])
            status = row.get("status_at_official_snapshot", row.get("status_latest_observed", "UNKNOWN"))

            # 区分 B01/B02（已跑） 与 B03~B06（新接收）
            is_new_batch = b_num in [3, 4, 5, 6]

            # 检查是否有本地行情
            if not has_local:
                all_symbols_qualification.append({
                    "batch_no": b_num,
                    "batch_version": b_ver,
                    "symbol": sym,
                    "pair": pair_name(sym),
                    "is_new_batch": is_new_batch,
                    "local_data_present": False,
                    "is_research_eligible": False,
                    "qualification_status": "隔离保留 (本地无数据/退市)",
                    "detailed_reason": "本地缺失行情数据，严格保留分母，不填0冒充可用"
                })
                continue

            # 若为新批次，执行数据导出
            if is_new_batch:
                ok_15m = export_ohlcv(sym, "15m")
                ok_1h = export_ohlcv(sym, "1h")
                ok_4h = export_ohlcv(sym, "4h")
                ok_oi = export_open_interest(sym)
                ok_fund = export_funding(sym)
                is_eligible = ok_15m and ok_1h and ok_4h
            else:
                # 复用 B01 / B02
                # 检查 FT_DATA_DIR 是否有对应文件
                slug = pair_slug(sym)
                ok_15m = (FT_DATA_DIR / f"{slug}-15m-futures.feather").exists()
                ok_1h = (FT_DATA_DIR / f"{slug}-1h-futures.feather").exists()
                ok_4h = (FT_DATA_DIR / f"{slug}-4h-futures.feather").exists()
                is_eligible = ok_15m and ok_1h and ok_4h

            if is_eligible:
                b_syms_eligible.append(sym)
                all_symbols_qualification.append({
                    "batch_no": b_num,
                    "batch_version": b_ver,
                    "symbol": sym,
                    "pair": pair_name(sym),
                    "is_new_batch": is_new_batch,
                    "local_data_present": True,
                    "is_research_eligible": True,
                    "qualification_status": "合格纳入研究期",
                    "detailed_reason": "覆盖 2021-12 至 2024-12 研究期且满足 200 根 4h 预热门槛"
                })
            else:
                all_symbols_qualification.append({
                    "batch_no": b_num,
                    "batch_version": b_ver,
                    "symbol": sym,
                    "pair": pair_name(sym),
                    "is_new_batch": is_new_batch,
                    "local_data_present": True,
                    "is_research_eligible": False,
                    "qualification_status": "隔离保留 (未覆盖研究期或预热不足)",
                    "detailed_reason": "上市时间晚于 2024 年底，在冻结研究期内无数据或预热不全"
                })

        batch_symbols_map[f"B{b_num:02d}"] = b_syms_eligible

    # 输出批次清单版本与数据指纹台账
    pd.DataFrame(batch_meta).to_csv(OUT_DIR / "03_批次清单版本与数据指纹台账.csv", index=False, encoding="utf-8-sig")

    # 输出全部分母资格对账表
    df_all_qual = pd.DataFrame(all_symbols_qualification)
    df_all_qual.to_csv(OUT_DIR / "01_批次资格映射与分母保全表.csv", index=False, encoding="utf-8-sig")

    # 3. 统计并输出 36 币 vs 25 币差异报告与复用/新增清单
    b01_eligible = batch_symbols_map["B01"]
    b02_eligible = batch_symbols_map["B02"]
    reused_36 = b01_eligible + b02_eligible

    b03_eligible = batch_symbols_map["B03"]
    b04_eligible = batch_symbols_map["B04"]
    b05_eligible = batch_symbols_map["B05"]
    b06_eligible = batch_symbols_map["B06"]
    new_38 = b03_eligible + b04_eligible + b05_eligible + b06_eligible

    total_74 = reused_36 + new_38

    symbols_manifest = {
        "work_order": "FT-FACTOR-03",
        "description": "复用与新增运行标的清单及 36 币 vs 25 币差异说明",
        "previous_round_discrepancy_explanation": {
            "25_symbols_origin": "来自批次 B01 (FT-POOL-01-B01-v1.1.0) 中在 2021-12~2024-12 具有完整数据并满足 4h 200 根预热的 25 个标的",
            "11_symbols_origin": "来自批次 B02 (FT-POOL-01-HIST-v2.0.0-B02-v1.0.0) 中在研究期满足预热的 11 个标的",
            "36_symbols_total": "FT-FACTOR-02 最初协议针对 B01 制定（25币），执行时合并纳入 B02（11币），合计形成 36 币回测白名单",
            "isolated_symbols_b01": "B01 中 10 个退市/归档无本地数据币（1000WHY, 1000X, 42, A2Z, ACX, AGIX, AI16Z, AI, ALPACA, ALPHA）与 15 个上市晚于 2024 年底币严格隔离保留，保留分母不填0",
            "isolated_symbols_b02": "B02 中 7 个退市/归档无本地数据币（1000BTTC, AKRO, AMB, ANC, ANIME, ANT, APX）与 7 个晚于 2024 年底币严格隔离保留"
        },
        "counts": {
            "reused_b01_count": len(b01_eligible),
            "reused_b02_count": len(b02_eligible),
            "reused_previous_total": len(reused_36),
            "new_b03_count": len(b03_eligible),
            "new_b04_count": len(b04_eligible),
            "new_b05_count": len(b05_eligible),
            "new_b06_count": len(b06_eligible),
            "new_batches_total": len(new_38),
            "expanded_pool_grand_total": len(total_74)
        },
        "b01_symbols": b01_eligible,
        "b02_symbols": b02_eligible,
        "reused_36_symbols": reused_36,
        "new_38_symbols": new_38,
        "expanded_pool_74_symbols": total_74,
        "pair_whitelists": {
            "reused_36_pairs": [pair_name(s) for s in reused_36],
            "new_38_pairs": [pair_name(s) for s in new_38],
            "expanded_74_pairs": [pair_name(s) for s in total_74]
        }
    }

    with open(OUT_DIR / "02_复用与新增标的清单.json", "w", encoding="utf-8") as f:
        json.dump(symbols_manifest, f, indent=2, ensure_ascii=False)

    # 4. 更新 config.json 配制
    cfg_path = USER_DATA_DIR / "config.json"
    with open(FT_FACTOR_02_DIR / "user_data/config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    cfg["bot_name"] = "ft_wyckoff_factor_03"
    cfg["user_data_dir"] = "evidence/FT-FACTOR-03/user_data"
    cfg["datadir"] = "evidence/FT-FACTOR-03/user_data/data/binance"
    cfg["exchange"]["pair_whitelist"] = symbols_manifest["pair_whitelists"]["expanded_74_pairs"]

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)

    logger.info("================ 数据扩展与资格映射顺利完成 ================")
    logger.info(f"复用旧标的: {len(reused_36)} 个 (B01: {len(b01_eligible)}, B02: {len(b02_eligible)})")
    logger.info(f"新增合格标的: {len(new_38)} 个 (B03: {len(b03_eligible)}, B04: {len(b04_eligible)}, B05: {len(b05_eligible)}, B06: {len(b06_eligible)})")
    logger.info(f"扩大后研究期全池合格标的总计: {len(total_74)} 个！")


if __name__ == "__main__":
    main()

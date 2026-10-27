# -*- coding: utf-8 -*-
"""为复盘工作台预切片导出典型交易与候选的 15m / 1h K 线数据。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
REPLAY_DIR = BASE_DIR / "evidence/FT-REPLAY-01"
WEB_DIR = REPLAY_DIR / "web"
CANDLES_DIR = WEB_DIR / "candles"
FEATHER_DIR = BASE_DIR / "evidence/FT-FACTOR-03/user_data/data/binance/futures"

CANDLES_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ExportCandleSlices")

df_all = pd.read_csv(REPLAY_DIR / "out/00_ft_entry_09_full_replay_records.csv")

# 选取典型代表性候选：
# 1. 真实成交且包含大盈、大亏、快速止损的代表性样本
# 2. 确认放弃避免亏损的代表性样本
# 3. 执行拒单的代表性样本
# 覆盖 27 组实验中的核心币种 (如 BTC, ETH, SOL, BCH, COMP, CHZ, 1000BONK, AGLD, CATI, CFX, 1000FLOKI 等)
selected_pairs = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BCH/USDT:USDT",
    "COMP/USDT:USDT", "CHZ/USDT:USDT", "1000BONK/USDT:USDT", "AGLD/USDT:USDT",
    "CATI/USDT:USDT", "CFX/USDT:USDT", "1000FLOKI/USDT:USDT", "APT/USDT:USDT",
    "BEAMX/USDT:USDT", "CELO/USDT:USDT", "AXS/USDT:USDT", "NEAR/USDT:USDT"
]

logger.info(f"开始为 {len(selected_pairs)} 个重点币种提取 K 线切片...")

for pair in selected_pairs:
    slug = pair.replace("/", "_").replace(":", "_")
    f_15m = FEATHER_DIR / f"{slug}-15m-futures.feather"
    f_1h = FEATHER_DIR / f"{slug}-1h-futures.feather"

    if not f_15m.exists():
        logger.warning(f"15m 文件不存在: {f_15m}")
        continue

    try:
        df_15m = pd.read_feather(f_15m)
        df_15m["t"] = (pd.to_datetime(df_15m["date"]).astype("int64") // 10**6).astype(int)
        
        # 提取 2021-12 到 2024-12 研究期内该币产生交易的代表性区间
        # 为控制静态文件大小，按季度/典型波段抽样或全量精简字段
        candles_15m = []
        for r in df_15m.itertuples():
            candles_15m.append({
                "t": int(r.t),
                "o": round(float(r.open), 6),
                "h": round(float(r.high), 6),
                "l": round(float(r.low), 6),
                "c": round(float(r.close), 6),
                "v": round(float(r.volume), 2)
            })

        clean_sym = pair.split("/")[0].replace("1000", "") + "USDT"
        out_file = CANDLES_DIR / f"{clean_sym}_15m.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(candles_15m, f, ensure_ascii=False)
        logger.info(f"已导出 {clean_sym} 15m K线: {len(candles_15m)} 根 -> {out_file.name} ({out_file.stat().st_size / 1024 / 1024:.2f} MB)")

    except Exception as e:
        logger.error(f"处理 {pair} 失败: {e}")

logger.info("重点币种 K 线切片提取完成！")

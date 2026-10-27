# -*- coding: utf-8 -*-
"""生成系统复盘前端所需的全量数据包与 K 线行情切片缓存。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import pandas as pd
import numpy as np

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
REPLAY_DIR = BASE_DIR / "evidence/FT-REPLAY-01"
WEB_DIR = REPLAY_DIR / "web"
DATA_DIR = WEB_DIR / "data"
FEATHER_DIR = BASE_DIR / "evidence/FT-FACTOR-03/user_data/data/binance/futures"

DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BuildReplayWebData")

# 1. 加载全量复盘底表
df_all = pd.read_csv(REPLAY_DIR / "out/00_ft_entry_09_full_replay_records.csv")
logger.info(f"加载底表: {len(df_all)} 条记录")

# 2. 导出前端 JSON 数据集
records = df_all.replace({np.nan: None}).to_dict(orient="records")

# 统计摘要
summary = {
    "total_records": len(df_all),
    "filled_trades": int((df_all["execution_status"] == "FILLED").sum()),
    "rejected_orders": int((df_all["execution_status"] == "REJECTED").sum()),
    "failed_confirms": int((df_all["execution_status"] == "CONFIRMATION_FAILED").sum()),
    "unique_pairs": df_all["pair"].nunique(),
    "timing_modes": ["C", "C1", "C2"],
    "param_suffixes": sorted(df_all["param_suffix"].unique().tolist()),
    "run_ids": sorted(df_all["run_id"].unique().tolist())
}

web_bundle = {
    "summary": summary,
    "records": records
}

web_data_json = DATA_DIR / "replay_data.json"
with open(web_data_json, "w", encoding="utf-8") as f:
    json.dump(web_bundle, f, ensure_ascii=False)
logger.info(f"保存前端全量数据包: {web_data_json} ({web_data_json.stat().st_size / 1024 / 1024:.2f} MB)")


# -*- coding: utf-8 -*-
"""FT-BASELINE-03 数据准备：01_prepare_data.py
从 FT-DATA-01 读取 35 个 feather 文件，转换 date 为 UTC aware 并存入 FT-BASELINE-03 数据目录，
保证 FT-DATA-01 原始数据只读，写入完全隔离在 FT-BASELINE-03 内部。
"""
from __future__ import annotations

import glob
import os
import pandas as pd

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
SRC_DIR = os.path.join(BASE_DIR, "evidence/FT-DATA-01/data/freqtrade/futures")
DST_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-03/user_data/data/binance/futures")

os.makedirs(DST_DIR, exist_ok=True)

def main():
    src_files = sorted(glob.glob(os.path.join(SRC_DIR, "*.feather")))
    print(f"找到源 feather 文件数: {len(src_files)}")
    
    for sf in src_files:
        fn = os.path.basename(sf)
        df_src = pd.read_feather(sf)
        df_dst = df_src.copy()
        if "date" in df_dst.columns:
            df_dst["date"] = pd.to_datetime(df_dst["date"], utc=True)
            
        df_dst_path = os.path.join(DST_DIR, fn)
        df_dst.to_feather(df_dst_path)
        assert len(df_dst) == len(df_src)
        
    print(f"数据准备完成，成功写入 {len(src_files)} 个文件至 {DST_DIR}")

if __name__ == "__main__":
    main()

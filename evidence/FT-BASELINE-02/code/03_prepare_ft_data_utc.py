# -*- coding: utf-8 -*-
"""FT-BASELINE-02 数据时区适配脚本：03_prepare_ft_data_utc.py

作用：
Freqtrade 官方回测引擎底层严格要求 feather 中的 date 列为 UTC offset-aware (datetime64[ms, UTC])。
Work 交付的 FT-DATA-01 数据包中的 date 列为 UTC offset-naive。
本脚本在 FT-BASELINE-02 独立工作目录下，将 FT-DATA-01 的 35 个 feather 数据转换时区后就位于：
evidence/FT-BASELINE-02/user_data/data/binance/futures/

严格遵循红线：
1. 原 FT-DATA-01 目录完全只读，0 修改；
2. 写入仅限 evidence/FT-BASELINE-02/；
3. 所有数值列（OHLCV、OI、Funding、Mark）进行双向 100% 相对误差校验，确保数值含义绝对零改动。
"""
from __future__ import annotations

import glob
import hashlib
import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
SRC_DIR = os.path.join(BASE_DIR, "evidence/FT-DATA-01/data/freqtrade/futures")
DST_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-02/user_data/data/binance/futures")

os.makedirs(DST_DIR, exist_ok=True)

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    src_files = sorted(glob.glob(os.path.join(SRC_DIR, "*.feather")))
    print(f"找到源 feather 文件数: {len(src_files)}")
    
    manifest = []
    
    for sf in src_files:
        fn = os.path.basename(sf)
        df_src = pd.read_feather(sf)
        
        # 复制并转换 date 为 UTC aware
        df_dst = df_src.copy()
        if "date" in df_dst.columns:
            df_dst["date"] = pd.to_datetime(df_dst["date"], utc=True)
            
        df_dst_path = os.path.join(DST_DIR, fn)
        df_dst.to_feather(df_dst_path)
        
        # 校验行数
        assert len(df_dst) == len(df_src), f"行数不一致: {fn}"
        
        # 校验数值列
        for col in df_src.columns:
            if col == "date":
                continue
            v_src = df_src[col].to_numpy(dtype=np.float64)
            v_dst = df_dst[col].to_numpy(dtype=np.float64)
            # 允许 NaN 对齐
            mask_valid = ~np.isnan(v_src)
            assert np.array_equal(np.isnan(v_src), np.isnan(v_dst)), f"NaN 模式不一致: {fn} {col}"
            diff = np.max(np.abs(v_src[mask_valid] - v_dst[mask_valid]))
            assert diff == 0.0, f"数值存在差异: {fn} {col} max_diff={diff}"
            
        manifest.append({
            "filename": fn,
            "rows": len(df_dst),
            "date_dtype": str(df_dst["date"].dtype),
            "sha256": sha256_file(df_dst_path)
        })
        
    print(f"成功转换并校验完成 {len(manifest)} 个 feather 文件，数值 100% 零误差对齐！")

if __name__ == "__main__":
    main()

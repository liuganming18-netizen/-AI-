"""
FT-BASELINE-02 任务脚本 1: 核对 FT-DATA-01 交接单与全部 45 个文件实际指纹
"""

import os
import hashlib
import pandas as pd

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
DATA_DIR = os.path.join(BASE_DIR, "evidence/FT-DATA-01")

def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def verify_all_files():
    print("=== 开始核对 FT-DATA-01 全部 45 个数据文件指纹 ===")
    fingerprints_csv = os.path.join(DATA_DIR, "out/03_导出文件指纹.csv")
    df_fp = pd.read_csv(fingerprints_csv)
    print(f"预期文件数: {len(df_fp)}")
    
    passed = 0
    failed = 0
    for idx, row in df_fp.iterrows():
        rel_file = row["文件"]
        expected_sha = str(row["sha256"]).strip()
        expected_rows = int(row["行数"])
        
        full_path = os.path.join(DATA_DIR, rel_file)
        if not os.path.exists(full_path):
            print(f"[MISSING] {rel_file}")
            failed += 1
            continue
            
        actual_sha = sha256_file(full_path)
        if actual_sha != expected_sha:
            print(f"[MISMATCH] {rel_file}: {actual_sha} != {expected_sha}")
            failed += 1
        else:
            passed += 1

    print(f"核对结果: 通过 {passed} / 异常 {failed}")
    assert failed == 0, f"发现 {failed} 项文件哈希不匹配！"
    assert passed == 45, f"通过文件数不等于 45: {passed}"
    print("=== FT-DATA-01 全部 45 个数据与 sidecar 文件指纹 100% 匹配闭环 ===")

if __name__ == "__main__":
    verify_all_files()

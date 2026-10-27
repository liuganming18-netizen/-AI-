"""
RESEARCH-CLOSE-01 数据与证据一致性自动核验脚本
核对统一研究总结报告中引用的所有关键指标及文件哈希
"""

import os
import json
import hashlib
from decimal import Decimal

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
TARGET_DIR = os.path.join(BASE_DIR, "evidence/RESEARCH-CLOSE-01")

def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def verify_all():
    print("=== RESEARCH-CLOSE-01 证据一致性核验 ===")
    
    # 1. 核验 BREAKEVEN-ROBUST-03 固定仓位口径
    robust_path = os.path.join(BASE_DIR, "evidence/BREAKEVEN-ROBUST-03/out/independent_results_fixed.json")
    with open(robust_path, "r", encoding="utf-8") as f:
        robust_data = json.load(f)
    ev_robust = robust_data["event_201_fixed_margin"]
    
    assert round(Decimal(str(ev_robust["A_net_U"])), 2) == Decimal("105.44")
    assert round(Decimal(str(ev_robust["B_1R_net_U"])), 2) == Decimal("52.43")
    assert round(Decimal(str(ev_robust["B_1R_delta_U"])), 2) == Decimal("-53.01")
    assert round(Decimal(str(ev_robust["C_2R_net_U"])), 2) == Decimal("108.49")
    assert round(Decimal(str(ev_robust["C_2R_delta_U"])), 2) == Decimal("3.05")
    assert ev_robust["1R_breakdown"]["improved_count"] == 11
    assert ev_robust["1R_breakdown"]["worsened_count"] == 16
    assert ev_robust["1R_breakdown"]["unchanged_count"] == 174
    print("[PASS] 1R/2R 保本数据源指标严格匹配")

    # 2. 核验 ENTRY-VOLUME-02 缩量数据
    entry_json = os.path.join(BASE_DIR, "evidence/VOLUME-BE-02/out/summary_1r.json")
    with open(entry_json, "r", encoding="utf-8") as f:
        vbe2_1r = json.load(f)
    ev_c = vbe2_1r["event_level_201_dedup"]["attribution_C_vs_A"]
    assert ev_c["n_filtered"] == 14
    assert round(Decimal(str(ev_c["saved_loss"])), 2) == Decimal("27.45")
    assert round(Decimal(str(ev_c["forgone_profit"])), 2) == Decimal("77.98")
    assert round(Decimal(str(ev_c["net_delta"])), 2) == Decimal("-50.53")
    print("[PASS] 入场缩量过滤指标严格匹配")

    # 3. 核验联合规则数据
    ev_d1 = vbe2_1r["event_level_201_dedup"]["D_combined"]
    assert round(Decimal(str(ev_d1["net_sum"])), 2) == Decimal("18.53")
    assert round(Decimal(str(vbe2_1r["event_level_201_dedup"]["attribution_D_vs_A"]["net_delta"])), 2) == Decimal("-86.91")
    
    entry_json_2r = os.path.join(BASE_DIR, "evidence/VOLUME-BE-02/out/summary_2r.json")
    with open(entry_json_2r, "r", encoding="utf-8") as f:
        vbe2_2r = json.load(f)
    ev_d2 = vbe2_2r["event_level_201_dedup"]["D_combined"]
    assert round(Decimal(str(ev_d2["net_sum"])), 2) == Decimal("57.96")
    assert round(Decimal(str(vbe2_2r["event_level_201_dedup"]["attribution_D_vs_A"]["net_delta"])), 2) == Decimal("-47.48")
    print("[PASS] 联合规则 1R/2R 指标严格匹配")

    # 4. 生成版本指纹
    dependencies = [
        "evidence/RESEARCH-CLOSE-01/统一研究总结.md",
        "evidence/ENTRY-VOLUME-02/报告.md",
        "evidence/ENTRY-VOLUME-02/最终回执_第1轮.md",
        "evidence/BREAKEVEN-ROBUST-03/closure/最终报告.md",
        "evidence/BREAKEVEN-ROBUST-03/closure/最终指纹.json",
        "evidence/VOLUME-BE-02/依赖核查.md",
        "evidence/VOLUME-BE-02/out/summary_1r.json",
        "evidence/VOLUME-BE-02/out/summary_2r.json",
        "evidence/BREAKEVEN-02/out/paired_fixed.csv"
    ]
    
    fingerprints = {}
    for rel in dependencies:
        fpath = os.path.join(BASE_DIR, rel)
        if os.path.exists(fpath):
            fingerprints[rel] = {
                "sha256": sha256_file(fpath),
                "bytes": os.path.getsize(fpath)
            }
        else:
            raise FileNotFoundError(f"文件不存在: {fpath}")

    out_fp = os.path.join(TARGET_DIR, "out/versions.json")
    with open(out_fp, "w", encoding="utf-8") as f:
        json.dump(fingerprints, f, indent=2, ensure_ascii=False)
    print(f"[PASS] 固化版本指纹至: {out_fp}")
    print("=== RESEARCH-CLOSE-01 全量证据核验通过 ===")

if __name__ == "__main__":
    verify_all()

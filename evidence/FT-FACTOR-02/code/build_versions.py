# -*- coding: utf-8 -*-
"""FT-FACTOR-02 固化版本清单与哈希文件"""
import hashlib
import json
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP_DIR = BASE_DIR / "evidence/FT-FACTOR-02"


def get_sha256(filepath: Path) -> str:
    if not filepath.exists():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def main():
    manifest = {
        "work_order": "FT-FACTOR-02",
        "title": "扩大样本的因子对照回测",
        "timestamp_utc": "2026-10-20T10:32:00Z",
        "referenced_qualification_manifest": {
            "batch_version": "FT-POOL-01-B01-v1.1.0",
            "historical_scope_version": "FT-POOL-01-HIST-v2.0.0",
            "manifest_file": "evidence/FT-POOL-01/manifest_B01_v1.1.json",
            "manifest_sha256": get_sha256(BASE_DIR / "evidence/FT-POOL-01/manifest_B01_v1.1.json"),
            "scope_denominator": 682,
            "b01_scanned_symbols": 50,
            "research_period_eligible_symbols": 25,
            "symbols_without_local_data_waiting": 10
        },
        "engine": {
            "name": "Freqtrade",
            "version": "2026.9",
            "python": "3.11.16",
            "environment_venv": "evidence/FT-BASELINE-01/.venv"
        },
        "period_partitioning": {
            "research_period": "2021-12-01 00:00:00 ~ 2024-12-31 23:59:59 (正式回测与消融对照期)",
            "validation_period": "2025-01-01 00:00:00 ~ 2025-12-31 23:59:59 (已披露: 2025年5币已看，不作完全独立验证)",
            "strict_holdout_period": "2026-01-01 00:00:00 ~ 2026-10-18 12:00:00 (本轮保持绝对封存，严禁选因子/调参)"
        },
        "deliverables": {
            "00_冻结方案": "evidence/FT-FACTOR-02/00_冻结方案与扩大样本实验协议.md",
            "01_偏差与防线报告": "evidence/FT-FACTOR-02/01_防线与未来数据泄漏检验报告.md",
            "05_收尾报告": "evidence/FT-FACTOR-02/05_扩大样本因子贡献回测与收尾报告.md",
            "b01_export_table": {
                "path": "evidence/FT-FACTOR-02/out/01_B01数据导出与合格状态表.csv",
                "sha256": get_sha256(EXP_DIR / "out/01_B01数据导出与合格状态表.csv")
            },
            "bias_check_json": {
                "path": "evidence/FT-FACTOR-02/out/02_未来数据泄漏与偏差检查报告.json",
                "sha256": get_sha256(EXP_DIR / "out/02_未来数据泄漏与偏差检查报告.json")
            },
            "trades_detail_csv": {
                "path": "evidence/FT-FACTOR-02/out/03_研究期逐笔交易明细.csv",
                "sha256": get_sha256(EXP_DIR / "out/03_研究期逐笔交易明细.csv")
            },
            "annual_table_csv": {
                "path": "evidence/FT-FACTOR-02/out/04_研究期年度分段表现表.csv",
                "sha256": get_sha256(EXP_DIR / "out/04_研究期年度分段表现表.csv")
            },
            "coin_table_csv": {
                "path": "evidence/FT-FACTOR-02/out/05_研究期标的币种表现表.csv",
                "sha256": get_sha256(EXP_DIR / "out/05_研究期标的币种表现表.csv")
            },
            "robust_table_csv": {
                "path": "evidence/FT-FACTOR-02/out/06_研究期稳健性与极值剔除表.csv",
                "sha256": get_sha256(EXP_DIR / "out/06_研究期稳健性与极值剔除表.csv")
            },
            "stat_bootstrap_table_csv": {
                "path": "evidence/FT-FACTOR-02/out/07_时间块重采样与多重比较校正表.csv",
                "sha256": get_sha256(EXP_DIR / "out/07_时间块重采样与多重比较校正表.csv")
            },
            "summary_table_csv": {
                "path": "evidence/FT-FACTOR-02/out/08_扩大样本因子消融对照总表.csv",
                "sha256": get_sha256(EXP_DIR / "out/08_扩大样本因子消融对照总表.csv")
            }
        }
    }

    out_file = EXP_DIR / "out/versions.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print("固化 versions.json 成功:", out_file)


if __name__ == "__main__":
    main()

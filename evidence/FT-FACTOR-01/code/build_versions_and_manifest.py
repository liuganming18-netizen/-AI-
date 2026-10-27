# -*- coding: utf-8 -*-
"""FT-FACTOR-01 版本清单与哈希固化脚本"""
import hashlib
import json
import os
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_FACTOR_DIR = BASE_DIR / "evidence/FT-FACTOR-01"


def get_file_sha256(filepath: Path) -> str:
    if not filepath.exists():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def main():
    manifest = {
        "work_order": "FT-FACTOR-01",
        "title": "分批全池基线与现有因子贡献回测",
        "timestamp_utc": "2026-10-20T08:30:00Z",
        "engine": {
            "name": "Freqtrade",
            "version": "2026.9",
            "python": "3.11.16",
            "environment_venv": "evidence/FT-BASELINE-01/.venv"
        },
        "data_input": {
            "version": "WX-FT-IN-1.0",
            "source_order": "FT-DATA-01",
            "manifest_path": "evidence/FT-DATA-01/manifest.json",
            "manifest_sha256": get_file_sha256(BASE_DIR / "evidence/FT-DATA-01/manifest.json"),
            "data_root": "evidence/FT-DATA-01/data/freqtrade/futures",
            "pairs": [
                "AR/USDT:USDT",
                "BANANA/USDT:USDT",
                "BAT/USDT:USDT",
                "CTSI/USDT:USDT",
                "PUMPBTC/USDT:USDT"
            ],
            "timerange": "20250801-20251231"
        },
        "baseline_reuse": {
            "source_order": "FT-BASELINE-05",
            "file": "evidence/FT-BASELINE-05/out/04_freqtrade_trades.csv",
            "sha256": get_file_sha256(BASE_DIR / "evidence/FT-BASELINE-05/out/04_freqtrade_trades.csv"),
            "reconciliation_source": "evidence/FT-BASELINE-06/out/03_资金费与持仓数量对照表.csv",
            "reconciliation_sha256": get_file_sha256(BASE_DIR / "evidence/FT-BASELINE-06/out/03_资金费与持仓数量对照表.csv"),
            "status": "直接复用（未重复重跑）"
        },
        "experiment_deliverables": {
            "00_冻结方案与实验协议": {
                "path": "evidence/FT-FACTOR-01/00_冻结方案与实验协议.md"
            },
            "01_因子定义与代码位置": {
                "path": "evidence/FT-FACTOR-01/01_因子定义与代码位置.md"
            },
            "strategy_baseline": {
                "path": "evidence/FT-FACTOR-01/user_data/strategies/WyckoffEventTrendBaseline.py",
                "sha256": get_file_sha256(FT_FACTOR_DIR / "user_data/strategies/WyckoffEventTrendBaseline.py")
            },
            "ablation_no_oi": {
                "path": "evidence/FT-FACTOR-01/user_data/strategies/WyckoffAblationNoOI.py",
                "sha256": get_file_sha256(FT_FACTOR_DIR / "user_data/strategies/WyckoffAblationNoOI.py")
            },
            "ablation_no_breakout_vol": {
                "path": "evidence/FT-FACTOR-01/user_data/strategies/WyckoffAblationNoBreakoutVol.py",
                "sha256": get_file_sha256(FT_FACTOR_DIR / "user_data/strategies/WyckoffAblationNoBreakoutVol.py")
            },
            "ablation_no_retest_vol": {
                "path": "evidence/FT-FACTOR-01/user_data/strategies/WyckoffAblationNoRetestVol.py",
                "sha256": get_file_sha256(FT_FACTOR_DIR / "user_data/strategies/WyckoffAblationNoRetestVol.py")
            },
            "ablation_no_gap_filter": {
                "path": "evidence/FT-FACTOR-01/user_data/strategies/WyckoffAblationNoGapFilter.py",
                "sha256": get_file_sha256(FT_FACTOR_DIR / "user_data/strategies/WyckoffAblationNoGapFilter.py")
            },
            "config_json": {
                "path": "evidence/FT-FACTOR-01/user_data/config.json",
                "sha256": get_file_sha256(FT_FACTOR_DIR / "user_data/config.json")
            },
            "table_ablation_summary": {
                "path": "evidence/FT-FACTOR-01/out/02_因子消融对照结果表.csv",
                "sha256": get_file_sha256(FT_FACTOR_DIR / "out/02_因子消融对照结果表.csv")
            },
            "table_trades_detail": {
                "path": "evidence/FT-FACTOR-01/out/03_逐笔交易对账明细.csv",
                "sha256": get_file_sha256(FT_FACTOR_DIR / "out/03_逐笔交易对账明细.csv")
            },
            "table_coin_attribution": {
                "path": "evidence/FT-FACTOR-01/out/04_单币与最大盈利单贡献拆解.csv",
                "sha256": get_file_sha256(FT_FACTOR_DIR / "out/04_单币与最大盈利单贡献拆解.csv")
            }
        }
    }

    out_path = FT_FACTOR_DIR / "out/versions.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"固化 versions.json 成功: {out_path}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""FT-FACTOR-03 生成版本与产物校验台账脚本 (versions.json)。"""
import hashlib
import json
import os
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_FACTOR_03_DIR = BASE_DIR / "evidence/FT-FACTOR-03"
OUT_DIR = FT_FACTOR_03_DIR / "out"


def get_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def main():
    versions = {
        "work_order": "FT-FACTOR-03",
        "timestamp_utc": "2026-10-20T19:35:00Z",
        "engine": {
            "name": "freqtrade",
            "version": "2026.9",
            "python": "3.11.16",
            "ccxt": "4.5.88"
        },
        "protocols": {
            "research_period": "2021-12-01 00:00:00 to 2024-12-31 23:59:59 (UTC)",
            "validation_period": "2025-01-01 to 2025-12-31 (已看样本披露)",
            "holdout_period": "2026-01-01 to 2026-10-18 (封存未动)",
            "fees": "Taker 5 bps, Slippage 3 bps, Real Funding Settlements",
            "position_sizing": "0.5% Risk per trade, 30% Max stake, Max 3 open trades"
        },
        "batches_included": [
            "FT-POOL-01-B01-v1.1.0",
            "FT-POOL-01-HIST-v2.0.0-B02-v1.0.0",
            "FT-POOL-01-HIST-v2.0.0-B03-v1.0.0",
            "FT-POOL-01-HIST-v2.0.0-B04-v1.0.0",
            "FT-POOL-01-HIST-v2.0.0-B05-v1.0.0",
            "FT-POOL-01-HIST-v2.0.0-B06-v1.0.0"
        ],
        "artifacts_sha256": {}
    }

    # 遍历 out/ 目录下所有文件
    for f in sorted(OUT_DIR.glob("*")):
        if f.is_file() and f.name != "versions.json":
            rel = f.name
            versions["artifacts_sha256"][rel] = {
                "bytes": f.stat().st_size,
                "sha256": get_sha256(f)
            }

    with open(OUT_DIR / "versions.json", "w", encoding="utf-8") as out_f:
        json.dump(versions, out_f, indent=2, ensure_ascii=False)

    print(f"versions.json generated with {len(versions['artifacts_sha256'])} artifacts.")


if __name__ == "__main__":
    main()

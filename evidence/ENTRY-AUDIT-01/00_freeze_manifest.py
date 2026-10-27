# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-01 / 00 冻结输入指纹。

只读：不写入任何被审计目录，只写 evidence/ENTRY-AUDIT-01/。
产出：
  freeze_manifest.json   逐文件 sha256 / size / mtime（UTC+本地）
  freeze_manifest.md     人读版摘要
  freeze_versions.json   同名文件多版本检测（若对方中途更新，可区分）
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-01"
OUT.mkdir(parents=True, exist_ok=True)

AUDITED = [
    # 被审计方（FACTOR-ENTRY-03）产物 —— 只读
    "evidence/FACTOR-ENTRY-03/结论.md",
    "evidence/FACTOR-ENTRY-03/evaluation_definitions_and_plan.md",
    "evidence/FACTOR-ENTRY-03/code_fingerprints.json",
    "evidence/FACTOR-ENTRY-03/candidate_features_enriched.csv",
    "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet",
    "evidence/FACTOR-ENTRY-03/case_studies.json",
    "evidence/FACTOR-ENTRY-03/missing_data_bias_analysis.csv",
    "evidence/FACTOR-ENTRY-03/indicator_correlation_matrix.csv",
    "evidence/FACTOR-ENTRY-03/indicator_tertile_monotonicity.csv",
    "evidence/FACTOR-ENTRY-03/attribution_analysis.py",
    "evidence/FACTOR-ENTRY-03/reproduce.sh",
    # 上游输入
    "evidence/FACTOR-ENTRY-01/candidate_features.parquet",
    "evidence/FACTOR-ENTRY-01/candidate_features.csv",
    "evidence/FACTOR-ENTRY-02/trade_mapping_296_to_205.csv",
    "evidence/FACTOR-ENTRY-02/trade_mapping_296_to_205.json",
    "evidence/FACTOR-ENTRY-02/common_sample_ablation.csv",
    "evidence/FACTOR-ENTRY-02/crowding_trades_micro_attribution.csv",
    "evidence/FULL_SWEEP_200x100/trades_rich.jsonl",
    "evidence/FULL_SWEEP_200x100/trades.jsonl",
    "evidence/FULL_SWEEP_200x100/runs.jsonl",
    # 策略/内核（只用于确认口径，不修改）
    "策略/WyckoffMaster/event_trend_v0_1/runner.py",
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "策略/WyckoffMaster/trading_core/engine.py",
]


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def rec(rel: str) -> dict:
    p = ROOT / rel
    if not p.exists():
        return {"path": rel, "exists": False}
    st = p.stat()
    return {
        "path": rel,
        "exists": True,
        "size": st.st_size,
        "mtime_unix": round(st.st_mtime, 6),
        "mtime_local": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "sha256": sha256(p),
    }


def main() -> None:
    started = time.time()
    files = [rec(r) for r in AUDITED]

    # 行情数据：只登记本轮实际会读的标的
    import pandas as pd

    cand = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-01/candidate_features.parquet")
    symbols = sorted(cand["symbol"].unique().tolist())
    base = ROOT / "数据" / "市场数据" / "parquet" / "candles" / "binance" / "um"
    kline = []
    for sym in symbols:
        for tf in ("15m", "4h"):
            for f in sorted(glob.glob(str(base / sym / tf / "*.parquet"))):
                kline.append(rec(os.path.relpath(f, ROOT)))

    manifest = {
        "ticket": "ENTRY-AUDIT-01",
        "frozen_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "frozen_at_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "python": os.sys.version.split()[0],
        "audited_side_files": files,
        "kline_files_count": len(kline),
        "kline_symbols_count": len(symbols),
        "kline_files": kline,
        "read_policy": "只读；不写入 FACTOR-ENTRY-*、FULL_SWEEP_*、策略/、框架/、UI界面/",
    }
    (OUT / "freeze_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 同名多版本探测：同目录下是否有 .bak / 副本
    ver = {}
    for d in ["evidence/FACTOR-ENTRY-03", "evidence/FACTOR-ENTRY-01", "evidence/FACTOR-ENTRY-02"]:
        dd = ROOT / d
        ver[d] = sorted([p.name for p in dd.glob("*") if p.is_file()]) if dd.exists() else []
    (OUT / "freeze_versions.json").write_text(
        json.dumps(ver, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    lines = [
        "# ENTRY-AUDIT-01 输入冻结清单",
        "",
        f"- 冻结时刻（本地）：{manifest['frozen_at_local']}",
        f"- 冻结时刻（UTC）：{manifest['frozen_at_utc']}",
        f"- 行情文件：{len(kline)} 个（{len(symbols)} 个标的 × 15m/4h）",
        "",
        "## 被审计方产物与上游输入",
        "",
        "| 文件 | 大小 | 修改时间 | sha256 前16位 |",
        "| :--- | ---: | :--- | :--- |",
    ]
    for f in files:
        if not f["exists"]:
            lines.append(f"| {f['path']} | — | **不存在** | — |")
        else:
            lines.append(
                f"| {f['path']} | {f['size']:,} | {f['mtime_local']} | `{f['sha256'][:16]}` |"
            )
    (OUT / "freeze_manifest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"frozen {len(files)} audit files + {len(kline)} kline files in {time.time()-started:.1f}s")
    for f in files:
        if f["exists"]:
            print(f"  {f['sha256'][:16]}  {f['mtime_local']}  {f['path']}")


if __name__ == "__main__":
    main()

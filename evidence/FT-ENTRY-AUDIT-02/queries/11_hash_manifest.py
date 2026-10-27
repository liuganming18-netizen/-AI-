#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11_hash_manifest.py — 复现哈希清单

目的：
  1. 为本轮全部产物（queries/*.py、out/*、logs/*、报告）生成 SHA-256
  2. 为上游只读输入（FT-ENTRY-01 / FT-ENTRY-02 的策略、配置、逐笔、归档）生成 SHA-256
     —— 用于证明「审计期间未修改对方文件」
  3. 输出机器可核对的 CSV，便于第三方重算后逐行比对

不写任何上游文件，只读。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ROOT = BASE.parent.parent
E1 = ROOT / "evidence" / "FT-ENTRY-01"
E2 = ROOT / "evidence" / "FT-ENTRY-02"
OUT = BASE / "out"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def row(scope: str, rel: str, p: Path) -> dict:
    st = p.stat()
    return {
        "scope": scope,
        "path": rel,
        "bytes": st.st_size,
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "sha256": sha256(p),
    }


def collect() -> list[dict]:
    rows: list[dict] = []

    # 1) 本轮脚本
    for p in sorted((BASE / "queries").glob("*.py")):
        rows.append(row("AUDIT_SCRIPT", f"queries/{p.name}", p))

    # 2) 本轮产物
    for p in sorted(OUT.glob("*")):
        if p.is_file():
            rows.append(row("AUDIT_OUTPUT", f"out/{p.name}", p))

    # 3) 本轮日志
    for p in sorted((BASE / "logs").glob("*.log")):
        rows.append(row("AUDIT_LOG", f"logs/{p.name}", p))

    # 4) 本轮报告
    for name in ("00_报告.md", "01_问题清单_v1.md"):
        p = BASE / name
        if p.exists():
            rows.append(row("AUDIT_REPORT", name, p))

    # 5) 上游只读 —— FT-ENTRY-01
    up1 = [
        "00_提前入场双路线规则与冻结方案.md",
        "05_提前入场双路线回测与对齐分析报告.md",
        "code/01_run_entry_timing_comparison.py",
        "user_data/config.json",
        "user_data/strategies/WyckoffEntryTimingBase.py",
        "user_data/strategies/WyckoffBaseline.py",
        "user_data/strategies/WyckoffEntryA1_NearTop.py",
        "user_data/strategies/WyckoffEntryA2_AtBoxHigh.py",
        "user_data/strategies/WyckoffEntryC1_StopSweep.py",
        "user_data/strategies/WyckoffEntryC2_StopBar.py",
    ]
    for rel in up1:
        p = E1 / rel
        if p.exists():
            rows.append(row("UPSTREAM_E1", f"FT-ENTRY-01/{rel}", p))

    for p in sorted((E1 / "out").glob("trades_*.csv")):
        rows.append(row("UPSTREAM_E1_TRADES", f"FT-ENTRY-01/out/{p.name}", p))
    for p in sorted((E1 / "out").glob("backtest-result-*.zip")):
        rows.append(row("UPSTREAM_E1_ARCHIVE", f"FT-ENTRY-01/out/{p.name}", p))

    # 6) 上游只读 —— FT-ENTRY-02
    up2 = [
        "00_有限阈值探索参数表与冻结方案.md",
        "code/02_verify_no_lookahead.py",
        "user_data/config.json",
        "user_data/strategies/WyckoffParamExplorationBase.py",
        "out/01_参数探索总表.csv",
        "out/02_分年度稳定性拆分表.csv",
        "out/03_极值敏感性检验表.csv",
        "out/04_参数平滑度网格矩阵.json",
    ]
    for rel in up2:
        p = E2 / rel
        if p.exists():
            rows.append(row("UPSTREAM_E2", f"FT-ENTRY-02/{rel}", p))

    for p in sorted((E2 / "out").glob("trades_*.csv")):
        rows.append(row("UPSTREAM_E2_TRADES", f"FT-ENTRY-02/out/{p.name}", p))
    for p in sorted((E2 / "out").glob("backtest-result-*.zip")):
        rows.append(row("UPSTREAM_E2_ARCHIVE", f"FT-ENTRY-02/out/{p.name}", p))

    return rows


def main() -> None:
    rows = collect()
    import csv

    dst = OUT / "39_复现哈希清单_v1.csv"
    with dst.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["scope", "path", "bytes", "mtime_utc", "sha256"])
        w.writeheader()
        w.writerows(rows)

    by_scope: dict[str, int] = {}
    for r in rows:
        by_scope[r["scope"]] = by_scope.get(r["scope"], 0) + 1

    summary = {
        "generated_by": "queries/11_hash_manifest.py",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "total_files": len(rows),
        "by_scope": by_scope,
        "key_hashes": {
            r["path"]: r["sha256"]
            for r in rows
            if r["path"]
            in (
                "FT-ENTRY-01/user_data/strategies/WyckoffEntryTimingBase.py",
                "FT-ENTRY-02/user_data/strategies/WyckoffParamExplorationBase.py",
                "FT-ENTRY-01/user_data/config.json",
                "FT-ENTRY-02/user_data/config.json",
            )
        },
        "note": "上游文件只读；本清单用于证明审计期间未改动对方产物（比对 mtime 与 sha256）。",
    }
    (OUT / "40_复现哈希摘要_v1.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"共 {len(rows)} 个文件：{by_scope}")
    print("关键哈希：")
    for k, v in summary["key_hashes"].items():
        print(f"  {k}\n    {v}")


if __name__ == "__main__":
    main()

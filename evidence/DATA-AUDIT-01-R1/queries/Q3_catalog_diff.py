#!/usr/bin/env python3
"""Q3 · 冷档索引双向差集（全量，无抽样）

结论：磁盘 42,482 个 parquet / 索引 32,920 条；
      「盘上有·索引无」11,471（其中 live.parquet 9,298）；
      「索引有·盘上无」1,909。
统计时点（本单那次运行）：2026-10-26T02:52:50Z

用法：
  /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python \
    evidence/DATA-AUDIT-01-R1/queries/Q3_catalog_diff.py

只读：只枚举目录、只读 _catalog.json，不写冷档、不写数据库。
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARCHIVE = ROOT / "数据/市场数据/parquet"
CATALOG = ARCHIVE / "_catalog.json"


def main() -> None:
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    shards: dict = cat.get("shards") or {}

    disk: set[str] = set()
    for dp, _dns, fns in os.walk(ARCHIVE):
        for fn in fns:
            if fn.endswith(".parquet"):
                disk.add(os.path.relpath(os.path.join(dp, fn), ARCHIVE).replace(os.sep, "/"))

    disk_only = sorted(disk - set(shards))
    index_only = sorted(set(shards) - disk)

    def kind(rel: str) -> str:
        return rel.split("/", 1)[0]

    def form(rel: str) -> str:
        base = os.path.basename(rel)
        return "live" if base == "live.parquet" else "year_or_flat"

    print(f"索引 shards        : {len(shards):>7}   updated_at={cat.get('updated_at')}")
    print(f"磁盘 parquet 文件  : {len(disk):>7}")
    print(f"盘上有·索引无      : {len(disk_only):>7}")
    print(f"索引有·盘上无      : {len(index_only):>7}")
    print()
    print("未登记文件按「类型|形态」拆开（live.parquet 是滚动分片，本就不该进年度索引）：")
    for k, v in sorted(Counter(f"{kind(r)}|{form(r)}" for r in disk_only).items()):
        print(f"  {k:<28} {v:>6}")
    print()
    print("悬空条目按类型：")
    for k, v in sorted(Counter(kind(r) for r in index_only).items()):
        print(f"  {k:<28} {v:>6}")
    print()
    print("资金费路径口径：")
    fd = [r for r in disk if r.startswith("funding/")]
    fi = [r for r in shards if r.startswith("funding/")]
    print(f"  磁盘 {len(fd)} 个，路径深度分布 {dict(Counter(r.count('/') for r in fd))}")
    print(f"  索引 {len(fi)} 条，路径深度分布 {dict(Counter(r.count('/') for r in fi))}")
    print(f"  磁盘样例 {fd[:2]}")
    print(f"  索引样例 {fi[:2]}")


if __name__ == "__main__":
    main()

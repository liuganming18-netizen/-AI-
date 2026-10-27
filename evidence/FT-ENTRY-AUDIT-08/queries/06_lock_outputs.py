# -*- coding: utf-8 -*-
"""06 本轮产物指纹：对 AUDIT-08 目录内全部交付物做 SHA-256 锁定。

指纹在本轮全部文件落盘后生成；指纹清单自身与摘要不计入清单（避免自引用）。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import AUDIT, jdump, sha256_file, write_csv  # noqa: E402

EXCLUDE = {
    "out/06_本轮产物指纹_v1.csv",
    "out/06_产物锁定摘要_v1.json",
    "logs/06_lock_outputs.log",  # 与锁定同时写入，避免自引用
}


def main() -> None:
    rows = []
    for root, _dirs, files in os.walk(AUDIT):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, AUDIT).replace(os.sep, "/")
            if rel in EXCLUDE or "__pycache__" in rel or rel.endswith(".pyc"):
                continue
            rows.append({"file": rel, "size_bytes": os.path.getsize(p),
                         "sha256": sha256_file(p)})
    df = pd.DataFrame(rows).sort_values("file")
    write_csv(df, "06_本轮产物指纹_v1.csv")
    summary = {
        "files": int(len(df)),
        "total_bytes": int(df["size_bytes"].sum()),
        "categories": df["file"].str.split("/").str[0].value_counts().to_dict(),
        "note": "指纹清单自身与摘要不计入；上游输入指纹见 01_输入指纹锁定_v1.csv",
    }
    jdump(os.path.join(AUDIT, "out/06_产物锁定摘要_v1.json"), summary)
    print(f"锁定 {len(df)} 个文件，共 {summary['total_bytes']} 字节")
    print(summary["categories"])


if __name__ == "__main__":
    main()

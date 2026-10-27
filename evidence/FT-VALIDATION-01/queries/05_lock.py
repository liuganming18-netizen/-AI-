# -*- coding: utf-8 -*-
"""05 交付锁定：冻结方案/版本清单/判据/启动状态 + 输入对象指纹。

不读验证收益、不运行策略。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import AUDIT, DATA_PKG, ENTRY10, OUT, jdump, sha256_file, write_csv  # noqa: E402

SELF = "05_产物指纹_v1.csv"
SELF_JSON = "05_锁定摘要_v1.json"
SELF_LOG = "logs/05_lock.log"


def main() -> None:
    # 1) 本轮产物
    rows = []
    for root, _dirs, files in os.walk(AUDIT):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, AUDIT).replace(os.sep, "/")
            if rel in (f"out/{SELF}", f"out/{SELF_JSON}", SELF_LOG,
                       "out/05_数据覆盖包指纹_v1.csv", "out/05_FT10归因包指纹_v1.csv") \
                    or "__pycache__" in rel:
                continue
            rows.append({"file": rel, "size_bytes": os.path.getsize(p), "sha256": sha256_file(p)})
    df = pd.DataFrame(rows).sort_values("file")
    write_csv(df, SELF)

    # 2) 冻结对象核对（以 01_版本清单_v1.csv 为准）
    ver = pd.read_csv(os.path.join(OUT, "01_版本清单_v1.csv"), encoding="utf-8-sig")
    frozen = ver[ver["status"] == "FROZEN"]
    mismatch = []
    for r in frozen.itertuples():
        p = os.path.join(os.path.dirname(os.path.dirname(AUDIT)), r.path)
        if sha256_file(p) != r.sha256:
            mismatch.append(r.path)

    # 3) 区间包
    pkg = []
    if os.path.isdir(ENTRY10):
        for root, _dirs, files in os.walk(ENTRY10):
            for fn in files:
                p = os.path.join(root, fn)
                pkg.append({"file": os.path.relpath(p, os.path.dirname(ENTRY10)),
                            "sha256": sha256_file(p), "size_bytes": os.path.getsize(p)})
    pkg_df = pd.DataFrame(pkg)
    if len(pkg_df):
        write_csv(pkg_df, "05_FT10归因包指纹_v1.csv")

    # 3b) Work 数据覆盖包
    dpkg = []
    if os.path.isdir(DATA_PKG):
        for root, _dirs, files in os.walk(DATA_PKG):
            for fn in files:
                p = os.path.join(root, fn)
                dpkg.append({"file": os.path.relpath(p, os.path.dirname(DATA_PKG)),
                             "sha256": sha256_file(p), "size_bytes": os.path.getsize(p)})
    dpkg_df = pd.DataFrame(dpkg)
    if len(dpkg_df):
        write_csv(dpkg_df, "05_数据覆盖包指纹_v1.csv")

    summary = {
        "output_files_locked": int(len(df)),
        "frozen_objects": int(len(frozen)),
        "frozen_mismatch": mismatch,
        "ft10_files": int(len(pkg_df)),
        "ft10_status": "NOT_AN_INTERVAL_PACKAGE" if len(pkg_df) else "PENDING",
        "data_package_files": int(len(dpkg_df)),
        "data_package_status": "RECEIVED_INTERVAL_QUALIFICATION" if len(dpkg_df) else "PENDING",
        "interval_qualification_package": "RECEIVED_NO_USABLE_INTERVAL",
        "no_validation_returns_read": True,
        "ft10_returns_used_for_selection": False,
        "no_backtest_run": True,
        "note": "FT-ENTRY-10 为研究期归因包，不是区间包；Work 已交付数据覆盖/接触/区间资格/结论；"
                "合格独立验证区间数=0；前向方案未启动。",
    }
    jdump(os.path.join(OUT, SELF_JSON), summary)
    print(f"本轮锁定 {len(df)} 个文件；冻结对象 {len(frozen)} 个，不一致 {len(mismatch)}；"
          f"区间包 {len(pkg_df)} 个（{summary['ft10_status']}）")
    if mismatch:
        print("不一致：", mismatch[:10])


if __name__ == "__main__":
    main()

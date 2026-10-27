# -*- coding: utf-8 -*-
"""06 文件指纹锁定：上游 FT-ENTRY-09 输入复核 + 本轮产物锁定。

- 逐项复核 FT-ENTRY-09 `00_版本指纹清单.csv`（交付自报指纹）；
- 确认 27 组 trades + backtest zip 完整；
- 对本轮 AUDIT-09 全部产物做 SHA-256（落盘后生成，排除清单自身与 06 日志）。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import AUDIT, ENTRY09, GRIDS, OUT, OUT09, jdump, sha256_file, write_csv  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(AUDIT))
SELF = "06_本轮产物指纹_v1.csv"
SELF_JSON = "06_产物锁定摘要_v1.json"
SELF_LOG = "logs/06_lock.log"


def main() -> None:
    # 1) 上游自报指纹复核
    man = pd.read_csv(os.path.join(ENTRY09, "00_版本指纹清单.csv"), encoding="utf-8-sig")
    rows = []
    for r in man.itertuples():
        p = os.path.join(ROOT, r.relative_path)
        got = sha256_file(p)
        rows.append({"relative_path": r.relative_path, "declared_sha256": r.sha256,
                     "actual_sha256": got, "match": got == r.sha256,
                     "size_declared": int(r.size_bytes),
                     "size_actual": os.path.getsize(p) if os.path.isfile(p) else None})
    inp = pd.DataFrame(rows)
    write_csv(inp, "06_输入指纹复核_v1.csv")

    # 2) 27 组完整性
    comp = []
    for g, s0, s1, s2, _vs, _z in GRIDS:
        for mode, strat in (("C", s0), ("C1", s1), ("C2", s2)):
            p = os.path.join(OUT09, f"trades_{strat}.csv")
            z = os.path.join(OUT09, f"backtest-result-{strat}.zip")
            n = len(pd.read_csv(p, encoding="utf-8-sig")) if os.path.isfile(p) else None
            comp.append({"grid": g, "mode": mode, "strategy": strat,
                         "trades_exists": os.path.isfile(p), "trades": n,
                         "trades_sha256": sha256_file(p),
                         "zip_exists": os.path.isfile(z), "zip_sha256": sha256_file(z)})
    cdf = pd.DataFrame(comp)
    write_csv(cdf, "06_27组完整性与指纹_v1.csv")

    # 2b) FT-ENTRY-09 全量交付指纹（含后续落盘的汇总表/审计/日志）
    full = []
    for root, dirs, files in os.walk(ENTRY09):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "data")
                   and not d.startswith("backtest_results_")]
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            if rel.endswith(".pyc"):
                continue
            full.append({"relative_path": rel, "size_bytes": os.path.getsize(p),
                         "sha256": sha256_file(p)})
    fdf = pd.DataFrame(full).sort_values("relative_path")
    write_csv(fdf, "06_FT09全量指纹_v1.csv")

    # 3) 本轮产物
    outs = []
    for root, _dirs, files in os.walk(AUDIT):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, AUDIT).replace(os.sep, "/")
            if rel in (f"out/{SELF}", f"out/{SELF_JSON}", SELF_LOG) or "__pycache__" in rel:
                continue
            outs.append({"file": rel, "size_bytes": os.path.getsize(p),
                         "sha256": sha256_file(p)})
    odf = pd.DataFrame(outs).sort_values("file")
    write_csv(odf, SELF)

    summary = {
        "inputs_declared": int(len(inp)),
        "inputs_match": int(inp["match"].sum()),
        "inputs_mismatch": inp.loc[~inp["match"], "relative_path"].tolist(),
        "cells": 27,
        "trades_present": int(cdf["trades_exists"].sum()),
        "zip_present": int(cdf["zip_exists"].sum()),
        "trades_total": int(cdf["trades"].fillna(0).sum()),
        "ft09_files_locked": int(len(fdf)),
        "ft09_total_bytes": int(fdf["size_bytes"].sum()),
        "output_files_locked": int(len(odf)),
        "note": "指纹清单自身、摘要与 06 日志不计入；上游输入以交付自报清单逐项复核。",
    }
    jdump(os.path.join(OUT, SELF_JSON), summary)
    print(f"上游指纹 {summary['inputs_match']}/{summary['inputs_declared']} 一致；"
          f"27 组 trades {summary['trades_present']}/27、zip {summary['zip_present']}/27；"
          f"本轮锁定 {summary['output_files_locked']} 个文件")
    if summary["inputs_mismatch"]:
        print("不一致：", summary["inputs_mismatch"][:10])


if __name__ == "__main__":
    main()

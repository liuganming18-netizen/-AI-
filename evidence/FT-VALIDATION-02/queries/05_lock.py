# -*- coding: utf-8 -*-
"""05 交付锁定：FT-VALIDATION-02 产物 + 输入引用 + 系统记录。"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import E07, E08, E09, OUT, ROOT, V01, V02, VDATA, jdump, sha256_file, write_csv  # noqa: E402

SELF = "05_产物指纹_v1.csv"
SELF_JSON = "05_锁定摘要_v1.json"
SELF_LOG = "logs/05_lock.log"

INPUTS = [
    ("V01 冻结方案", os.path.join(V01, "00_冻结验证方案.md")),
    ("V01 版本清单", os.path.join(V01, "01_版本清单.md")),
    ("V01 判据说明", os.path.join(V01, "02_判据说明.md")),
    ("V01 启动状态", os.path.join(V01, "03_启动状态.md")),
    ("Work 资格结论13:47", os.path.join(VDATA, "out/05_hharness_资格结论.json")),
    ("Work 自检", os.path.join(VDATA, "out/06_自检结果.json")),
    ("Work 区间资格表", os.path.join(VDATA, "out/03_区间资格表.csv")),
    ("Work 段级汇总", os.path.join(VDATA, "out/04_段级资格汇总.csv")),
    ("Work OI探针", os.path.join(VDATA, "out/04_OI发布时点探针.json")),
    ("复盘选样表", os.path.join(E08, "01_选样清单表.csv")),
    ("复盘报告", os.path.join(E08, "02_案例复盘与决策视图.md")),
    ("源账本逐候选", os.path.join(E07, "02_逐候选对照表.csv")),
    ("策略基类", os.path.join(E09, "user_data/strategies/WyckoffParamExplorationBase.py")),
    ("交易config", os.path.join(E09, "user_data/config.json")),
    ("系统记录", os.path.join(ROOT, ".workbuddy-ai/memory/2026-10-21.md")),
    ("系统记录索引", os.path.join(ROOT, ".workbuddy-ai/memory/MEMORY.md")),
]


def main() -> None:
    rows = []
    for root, _dirs, files in os.walk(V02):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, V02).replace(os.sep, "/")
            if rel in (f"out/{SELF}", f"out/{SELF_JSON}", SELF_LOG,
                       "out/05_输入引用指纹_v1.csv") or "__pycache__" in rel:
                continue
            rows.append({"file": rel, "size_bytes": os.path.getsize(p), "sha256": sha256_file(p)})
    df = pd.DataFrame(rows).sort_values("file")
    write_csv(df, SELF)

    inp = pd.DataFrame([{"name": n, "path": os.path.relpath(p, ROOT), "sha256": sha256_file(p),
                         "exists": os.path.isfile(p)} for n, p in INPUTS])
    write_csv(inp, "05_输入引用指纹_v1.csv")

    summary = {
        "output_files": int(len(df)),
        "input_refs": int(len(inp)),
        "input_missing": inp.loc[~inp["exists"], "name"].tolist(),
        "no_validation_run": True,
        "no_new_param_search": True,
        "no_validation_returns_read": True,
        "note": "本轮未运行独立验证、未新增参数搜索；区间资格 0 可用；系统记录已更新。",
    }
    jdump(os.path.join(OUT, SELF_JSON), summary)
    print(f"锁定 {len(df)} 个产物；输入引用 {len(inp)} 个，缺失 {summary['input_missing']}")


if __name__ == "__main__":
    main()

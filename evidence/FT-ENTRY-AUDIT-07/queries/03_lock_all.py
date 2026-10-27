# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-07 · 03：本轮全部产物与上游依赖落盘后指纹锁定。

规则（用户明确要求）：
- 待文件落盘后再生成指纹，避免提前锁定空文件；
- 本轮自己的产物（out/queries/logs/报告）与上游依赖（FT-ENTRY-02 C 逐笔、
  03/04 A 逐笔、04 audit_orders、FT-ENTRY-05/06 交付物）分别指纹；
- 反重力「企稳确认」三入口新交付（C0/C1/C2）当前不存在，
  在指纹表中以「等待」登记占位，到达后由后续脚本补锁，不预填哈希。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (AUDIT, ENTRY02, ENTRY05, ENTRY06, OUT, OUT03, OUT04,  # noqa: E402
                     CELLS_A, CELLS_C, sha256_file, jdump)


def iter_files(root: str):
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if fn == ".DS_Store":
                continue
            yield os.path.join(dirpath, fn)


def main() -> None:
    rows = []

    # 1) 本轮产物
    for sub in ("out", "queries", "logs"):
        d = os.path.join(AUDIT, sub)
        for p in iter_files(d):
            rows.append({"类别": f"本轮产物/{sub}", "文件": os.path.relpath(p, AUDIT),
                         "sha256": sha256_file(p)})
    for fn in sorted(os.listdir(AUDIT)):
        p = os.path.join(AUDIT, fn)
        if os.path.isfile(p):
            rows.append({"类别": "本轮产物/报告", "文件": fn, "sha256": sha256_file(p)})

    # 2) 上游依赖（只读锁定）
    for _code, strat, _vr, _z in CELLS_C:
        p = os.path.join(OUT02 := os.path.join(ENTRY02, "out"), f"trades_{strat}.csv")
        if os.path.isfile(p):
            rows.append({"类别": "上游/FT-ENTRY-02_C逐笔", "文件": f"trades_{strat}.csv",
                         "sha256": sha256_file(p)})
    for _code, strat, _d, _k in CELLS_A:
        for src, tag in ((OUT03, "03"), (OUT04, "04")):
            p = os.path.join(src, f"trades_{strat}.csv")
            if os.path.isfile(p):
                rows.append({"类别": f"上游/FT-ENTRY-{tag}_A逐笔", "文件": f"trades_{strat}.csv",
                             "sha256": sha256_file(p)})
        p = os.path.join(OUT04, f"audit_orders_{strat}.csv")
        if os.path.isfile(p):
            rows.append({"类别": "上游/FT-ENTRY-04_审计账本", "文件": f"audit_orders_{strat}.csv",
                         "sha256": sha256_file(p)})
    for d, tag in ((ENTRY05, "FT-ENTRY-05"), (ENTRY06, "FT-ENTRY-06")):
        for p in iter_files(d):
            rows.append({"类别": f"上游/{tag}", "文件": os.path.relpath(p, d),
                         "sha256": sha256_file(p)})
    # C0 基线代码
    for tag, d in (("FT-ENTRY-02", ENTRY02), ("FT-ENTRY-03", os.path.dirname(OUT03)),
                   ("FT-ENTRY-04", os.path.dirname(OUT04))):
        p = os.path.join(d, "user_data/strategies/WyckoffParamExplorationBase.py")
        if os.path.isfile(p):
            rows.append({"类别": f"上游/{tag}_基类代码", "文件": "WyckoffParamExplorationBase.py",
                         "sha256": sha256_file(p)})

    # 3) 等待占位（不预填哈希）
    waiting = [
        "反重力企稳确认三入口交付（C0/C1/C2）：策略代码（只改确认条件）、config、"
        "9/多组逐笔 trades_*.csv、运行日志 —— evidence/FT-ENTRY-07（反重力侧命名）",
    ]
    for w in waiting:
        rows.append({"类别": "等待(不预填哈希)", "文件": w, "sha256": "PENDING_到达后补锁"})

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "03_本轮与上游指纹_v1.csv"), index=False, encoding="utf-8-sig")

    summary = {
        "本轮产物文件数": int((df["类别"].str.startswith("本轮产物")).sum()),
        "上游锁定文件数": int((df["类别"].str.startswith("上游")).sum()),
        "等待项": waiting,
        "规则": "指纹在本轮全部文件落盘后生成；空文件/未交付项不预填哈希",
    }
    jdump(os.path.join(OUT, "03_指纹锁定摘要_v1.json"), summary)
    print(f"指纹完成：本轮 {summary['本轮产物文件数']} 个，上游 {summary['上游锁定文件数']} 个，"
          f"等待 {len(waiting)} 项")


if __name__ == "__main__":
    main()

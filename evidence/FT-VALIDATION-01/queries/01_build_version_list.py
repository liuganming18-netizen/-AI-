# -*- coding: utf-8 -*-
"""01 冻结版本清单：C1 验证对象、对照、参数、评价代码、数据/区间资格包。

只读；不运行策略、不读取验证收益。区间资格包未到达时对应行标 PENDING。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    AUDIT09, ENTRY09, OUT, ROOT, jdump, sha256_file, write_csv,
)

BASE = "evidence/FT-ENTRY-09/user_data/strategies/WyckoffParamExplorationBase.py"
CFG = "evidence/FT-ENTRY-09/user_data/config.json"
RULES = "evidence/FT-ENTRY-09/user_data/strategies/pair_eligibility_rules.json"
GRIDS = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06", "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
         "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]


def main() -> None:
    rows = []

    def add(cat, path, note, status="FROZEN"):
        p = os.path.join(ROOT, path)
        rows.append({"category": cat, "path": path, "sha256": sha256_file(p),
                     "exists": os.path.isfile(p), "status": status, "note": note})

    add("策略基类", BASE, "C0/C1 共用；仅准入/审计变化")
    add("交易 config", CFG, "74 币池、3 槽位、2000 钱包、5bps")
    add("准入规则", RULES, "74 币 q_start/q_end/qualified")
    for s in GRIDS:
        add("C0 对照", f"evidence/FT-ENTRY-09/user_data/strategies/Wyckoff_C_{s}.py",
            f"C0 {s}")
    for s in GRIDS:
        add("C1 验证", f"evidence/FT-ENTRY-09/user_data/strategies/Wyckoff_C1_{s}.py",
            f"C1 {s}")
    # 评价方法基线（AUDIT-09）
    for f, note in [
        ("queries/_common.py", "块级统计/事件面板/准入核对（冻结常数）"),
        ("queries/04_stats.py", "18+9 族/CI/置换/Holm/maxT/分解"),
        ("queries/01_方法与校正预注册.md", "AUDIT-09 结果前冻结判据"),
        ("00_合格样本下确认机制审核报告.md", "AUDIT-09 完整结果"),
    ]:
        add("评价基线", f"evidence/FT-ENTRY-AUDIT-09/{f}", note)
    # Work 数据覆盖包（只读元数据，不含收益/区间判定）
    dpkg = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-01")
    if os.path.isdir(dpkg):
        for r, _d, fs in os.walk(dpkg):
            for fn in fs:
                p = os.path.relpath(os.path.join(r, fn), ROOT)
                rows.append({"category": "数据覆盖包", "path": p, "sha256": sha256_file(os.path.join(ROOT, p)),
                             "exists": True, "status": "RECEIVED",
                             "note": "Work 逐币数据覆盖（只读元数据）；不是区间资格判定"})

    # 区间资格包：Work 尚未交付；FT-ENTRY-10 是研究期归因包，不是区间包
    pkg_dir = os.path.join(ROOT, "evidence/FT-ENTRY-10")
    pkg_files = []
    if os.path.isdir(pkg_dir):
        for r, _d, fs in os.walk(pkg_dir):
            for fn in fs:
                pkg_files.append(os.path.relpath(os.path.join(r, fn), ROOT))
    if pkg_files:
        rows.append({"category": "非验证输入", "path": "evidence/FT-ENTRY-10/",
                     "sha256": None, "exists": True, "status": "NOT_AN_INTERVAL_PACKAGE",
                     "note": "FT-ENTRY-10 为研究期逐候选对照/收益分解/残差对账包，不是验证区间资格包；"
                             "其收益列不用于验证、不用于选参。"})
    rows.append({"category": "区间资格包", "path": "（Work 待交付）",
                 "sha256": None, "exists": False, "status": "PENDING",
                 "note": "OOS-READY-01 结论：现有数据暂无可证明的样本外范围；等待 Work 正式区间资格包"})
    # 数据快照（区间确认后逐文件）
    rows.append({"category": "数据快照", "path": "evidence/FT-ENTRY-09/user_data/data/binance/futures/",
                 "sha256": None, "exists": os.path.isdir(
                     os.path.join(ROOT, "evidence/FT-ENTRY-09/user_data/data/binance/futures")),
                 "status": "PENDING_INTERVAL", "note": "区间确认后对所用 15m/1h/4h/OI/mark/funding 逐文件 SHA"})

    df = pd.DataFrame(rows)
    write_csv(df, "01_版本清单_v1.csv")
    summary = {
        "objects": int(len(df)),
        "frozen": int((df["status"] == "FROZEN").sum()),
        "received": int((df["status"] == "RECEIVED").sum()),
        "pending": int(df["status"].str.startswith("PENDING").sum()),
        "all_frozen_present": bool(df[(df["status"] == "FROZEN")]["exists"].all()),
        "note": "区间资格包/数据快照在起算前补齐并重新计算 SHA；当前版本清单不含任何验证收益。",
    }
    jdump(os.path.join(OUT, "01_版本清单摘要_v1.json"), summary)
    print(df.groupby(["category", "status"]).size().to_string())
    print(summary)


if __name__ == "__main__":
    main()

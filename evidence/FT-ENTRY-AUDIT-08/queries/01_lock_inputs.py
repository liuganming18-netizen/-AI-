# -*- coding: utf-8 -*-
"""01 输入锁定：复用 AUDIT-07 已落盘的 FT-ENTRY-07 指纹清单逐文件复核。

- 以 AUDIT-07 `04_FT07交付指纹_v1.csv`（166 项）为基准，重算当前 FT-ENTRY-07 全量哈希；
- 另锁定本轮直接引用的 AUDIT-07 结论文件、FT-ENTRY-02 C0 逐笔、AUDIT-02 币池覆盖表、
  FT-ENTRY-03 资格说明；
- 不修改上游任何文件。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    AUDIT07, AUDIT02, ENTRY02, ENTRY03, ENTRY07, OUT, jdump, sha256_file, write_csv,
)

LOCK07 = os.path.join(AUDIT07, "out/04_FT07交付指纹_v1.csv")

EXTRA = [
    (os.path.join(AUDIT07, "00_企稳确认效果独立审核报告.md"), "AUDIT-07 独立审核报告"),
    (os.path.join(AUDIT07, "out/06_27组独立重算_v1.csv"), "AUDIT-07 27组独立重算"),
    (os.path.join(AUDIT07, "out/06_27组重算摘要_v1.json"), "AUDIT-07 重算摘要"),
    (os.path.join(AUDIT07, "out/05_T1-T12核对_v1.json"), "AUDIT-07 T1-T12"),
    (os.path.join(AUDIT07, "out/07_quickstop口径复现与正确重算_v1.csv"), "AUDIT-07 快速止损正确口径"),
    (os.path.join(AUDIT07, "out/07_事件级避免快速止损归因重算_v1.csv"), "AUDIT-07 事件级归因"),
    (os.path.join(AUDIT07, "out/04_交付锁定与C0复用证明_v1.json"), "AUDIT-07 C0复用证明"),
    (os.path.join(ENTRY03, "01_输入资格版本与样本核验.md"), "FT-03 输入资格与样本分层"),
    (os.path.join(AUDIT02, "out/02_币池与数据覆盖_v1.csv"), "AUDIT-02 74币数据覆盖"),
]
for _g, s0, _s1, _s2, _vs, _z in [
    ("Vs04_Z02", "Wyckoff_C_Vs04_Z02", "", "", 0, 0),
    ("Vs04_Z04", "Wyckoff_C_Vs04_Z04", "", "", 0, 0),
    ("Vs04_Z06", "Wyckoff_C_Vs04_Z06", "", "", 0, 0),
    ("Vs06_Z02", "Wyckoff_C_Vs06_Z02", "", "", 0, 0),
    ("Vs06_Z04", "Wyckoff_C_Vs06_Z04", "", "", 0, 0),
    ("Vs06_Z06", "Wyckoff_C_Vs06_Z06", "", "", 0, 0),
    ("Vs08_Z02", "Wyckoff_C_Vs08_Z02", "", "", 0, 0),
    ("Vs08_Z04", "Wyckoff_C_Vs08_Z04", "", "", 0, 0),
    ("Vs08_Z06", "Wyckoff_C_Vs08_Z06", "", "", 0, 0),
]:
    EXTRA.append((os.path.join(ENTRY02, "out", f"trades_{s0}.csv"), "FT-02 C0 逐笔（复用基线）"))


def main() -> None:
    base = pd.read_csv(LOCK07, encoding="utf-8-sig")
    rows = []
    mism = []
    for r in base.itertuples():
        p = os.path.join(ENTRY07, r.文件)
        got = sha256_file(p)
        ok = (got == r.sha256)
        rows.append({"层级": "FT-ENTRY-07 交付（AUDIT-07 锁定基准）", "文件": r.文件,
                     "锁定sha256": r.sha256, "当前sha256": got, "一致": bool(ok)})
        if not ok:
            mism.append(r.文件)
    for p, desc in EXTRA:
        rows.append({"层级": desc, "文件": os.path.relpath(p, os.path.dirname(OUT)),
                     "锁定sha256": None, "当前sha256": sha256_file(p), "一致": None})
    df = pd.DataFrame(rows)
    write_csv(df, "01_输入指纹锁定_v1.csv")

    extra_rows = df[df["层级"] != "FT-ENTRY-07 交付（AUDIT-07 锁定基准）"]
    summary = {
        "基准清单": os.path.relpath(LOCK07, os.path.dirname(OUT)),
        "基准项数": int(len(base)),
        "复核一致": int((df["层级"] == "FT-ENTRY-07 交付（AUDIT-07 锁定基准）").sum() - len(mism)),
        "不一致": mism,
        "追加锁定项": int(len(extra_rows)),
        "追加锁定": {r.文件: r.锁定sha256 if False else r.当前sha256 for r in extra_rows.itertuples()},
        "结论": ("FT-ENTRY-07 全部 166 项与 AUDIT-07 锁定指纹逐文件一致，输入未改动"
                 if not mism else "存在不一致，停止引用"),
    }
    jdump(os.path.join(OUT, "01_输入锁定摘要_v1.json"), summary)
    print(f"FT-ENTRY-07 基准 {len(base)} 项；不一致 {len(mism)} 项")
    print(f"追加锁定 {len(extra_rows)} 项")
    print("不一致:", mism[:10])


if __name__ == "__main__":
    main()

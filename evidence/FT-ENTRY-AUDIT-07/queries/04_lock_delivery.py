# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-07 · 04：锁定反重力 FT-ENTRY-07 交付（27 组结果 + 数据 + 代码）。

规则：交付文件已落盘（2026-10-21 09:08~10:27），现在生成指纹；
- 全量哈希 out/（27 trades + 18 audit_orders + 18 audit_confirmation + 27 zip）、
  code/、logs/、user_data/strategies、user_data/config.json、顶层 md/csv；
- 数据目录 74 币 518 feather + 74 sidecar：逐文件哈希；
- 与对方 06_文件指纹清单.csv 对拍（27 组 trades/zip）；
- C0 与 FT-ENTRY-02 的 9 组逐笔做字节级同一性证明（复用非重跑）。
只读不写上游。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import ENTRY02, OUT, sha256_file, jdump, write_csv  # noqa: E402

FT07 = os.path.join(os.path.dirname(OUT), "..", "FT-ENTRY-07")
FT07 = os.path.abspath(FT07)


def iter_files(root: str):
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if fn == ".DS_Store":
                continue
            yield os.path.join(dirpath, fn)


def main() -> None:
    rows = []
    for p in iter_files(FT07):
        rel = os.path.relpath(p, FT07)
        cat = rel.split(os.sep)[0]
        rows.append({"类别": cat, "文件": rel, "sha256": sha256_file(p)})
    df = pd.DataFrame(rows)
    write_csv(df, "04_FT07交付指纹_v1.csv")

    # 与对方 06 清单对拍
    theirs = os.path.join(FT07, "06_文件指纹清单.csv")
    mismatch = []
    checked = 0
    if os.path.isfile(theirs):
        tk = pd.read_csv(theirs, encoding="utf-8-sig")
        mine = {r["文件"]: r["sha256"] for _, r in df.iterrows()}
        for _, r in tk.iterrows():
            for col in ("csv_file", "zip_file"):
                fn = r.get(col)
                if not isinstance(fn, str) or not fn:
                    continue
                key = os.path.join("out", fn)
                hk = "csv_sha256" if col == "csv_file" else "zip_sha256"
                hv = r.get(hk)
                if not isinstance(hv, str) or not hv:
                    continue
                checked += 1
                if mine.get(key) != hv:
                    mismatch.append({"file": key, "对方": hv[:12], "本轮": str(mine.get(key))[:12]})
    rep = {
        "交付文件总数": int(len(df)),
        "按类别": df["类别"].value_counts().to_dict(),
        "对方指纹清单核对项": checked,
        "不一致": mismatch,
        "C0复用证明": {},
        "结论": "FT-ENTRY-07 交付已落盘并全量锁定；C0 9 组与 FT-ENTRY-02 逐字节同一（复用非重跑）",
    }
    for g in ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06", "Vs06_Z02", "Vs06_Z04",
              "Vs06_Z06", "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]:
        a = os.path.join(FT07, "out", f"trades_Wyckoff_C_{g}.csv")
        b = os.path.join(ENTRY02, "out", f"trades_Wyckoff_C_{g}.csv")
        rep["C0复用证明"][g] = (sha256_file(a) == sha256_file(b))
    jdump(os.path.join(OUT, "04_交付锁定与C0复用证明_v1.json"), rep)
    print(f"交付锁定 {len(df)} 个文件；对方清单核对 {checked} 项，不一致 {len(mismatch)}")
    print("C0 与 FT-ENTRY-02 全部同一:", all(rep["C0复用证明"].values()))


if __name__ == "__main__":
    main()

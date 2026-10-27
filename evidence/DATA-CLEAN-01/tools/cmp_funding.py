#!/usr/bin/env python3
"""比较 520 个 funding parquet 文件的实际内容差异（行数/列/数值）。"""
import json
import os
import sys

import pandas as pd

BASE = "/Users/boos/Desktop/威科夫因子共振趋势/数据/研究输入/WyckoffMaster"
S1 = os.path.join(BASE, "_backup/20261014T190050Z")
S2 = os.path.join(BASE, "_backup/20261014T192054Z")

cmp = json.load(open(sys.argv[1], encoding="utf-8"))
paths = [r["path"] for r in cmp["size_diff"]]
print(f"待比对 {len(paths)} 个文件\n")

rows = []
for i, rel in enumerate(paths):
    a = os.path.join(BASE, rel)
    b = os.path.join(S1, rel)
    c = os.path.join(S2, rel)
    try:
        da, db, dc = pd.read_parquet(a), pd.read_parquet(b), pd.read_parquet(c)
        rec = {
            "path": rel,
            "rows_live": len(da), "rows_s1": len(db), "rows_s2": len(dc),
            "cols_live": ",".join(map(str, da.columns)),
            "cols_s1": ",".join(map(str, db.columns)),
            "cols_s2": ",".join(map(str, dc.columns)),
            "live_eq_s2": da.equals(dc),
            "live_eq_s1": da.equals(db),
        }
        # 若行数不同，找出 s1 独有的行
        if len(db) != len(da):
            rec["row_delta_s1_minus_live"] = len(db) - len(da)
        rows.append(rec)
    except Exception as e:
        rows.append({"path": rel, "error": str(e)[:120]})
    if (i + 1) % 100 == 0:
        print(f"  已比对 {i+1}/{len(paths)}")

df = pd.DataFrame(rows)
out = sys.argv[2]
df.to_csv(out, index=False, encoding="utf-8-sig")
print(f"\n结果写入 {out}")
print("\n=== 汇总 ===")
print("live 与 s2 完全相等:", int(df["live_eq_s2"].sum()) if "live_eq_s2" in df else "n/a", "/", len(df))
print("live 与 s1 完全相等:", int(df["live_eq_s1"].sum()) if "live_eq_s1" in df else "n/a", "/", len(df))
if "row_delta_s1_minus_live" in df:
    d = df["row_delta_s1_minus_live"].dropna()
    print("s1 比 live 多出的行数分布:", d.value_counts().head(5).to_dict())
print("\n列是否一致:", df["cols_live"].equals(df["cols_s1"]) if "cols_live" in df else "n/a")
if "error" in df:
    print("读取失败:", df["error"].notna().sum())

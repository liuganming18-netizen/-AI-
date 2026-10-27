# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-01 / 04：OI 发布时点证据探针（只读来源字段，不读持仓量数值）。

目的：核对「OI 是否具备可用时点证据」——这决定任何区间能否宣称 PIT 隔离。
只读 oi/*.parquet 的 fetched_at 列（来源/采集时间字段），不读 open_interest 数值。

输出：out/04_OI发布时点探针.json
"""
from __future__ import annotations

import glob
import json
import os

import pyarrow.parquet as pq

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OI = os.path.join(ROOT, "数据/市场数据/parquet/oi/binance/um")
OUT = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-01/out/04_OI发布时点探针.json")


def main():
    syms = sorted(d for d in os.listdir(OI) if os.path.isdir(os.path.join(OI, d)))
    total_files = 0
    files_with_fetched = 0
    files_fetched_all_null = 0
    files_fetched_has_value = 0
    rows = 0
    nulls = 0
    detail = []
    for sym in syms:
        fs = sorted(glob.glob(os.path.join(OI, sym, "*.parquet")))
        for f in fs:
            total_files += 1
            pf = pq.ParquetFile(f)
            if "fetched_at" not in pf.schema_arrow.names:
                continue
            files_with_fetched += 1
            col = pq.read_table(f, columns=["fetched_at"]).column("fetched_at")
            n = col.null_count
            rows += col.length()
            nulls += n
            if n == col.length():
                files_fetched_all_null += 1
            elif n < col.length():
                files_fetched_has_value += 1
                if len(detail) < 10:
                    vals = [v.as_py() for v in col if v.as_py() is not None][:2]
                    detail.append({"symbol": sym, "file": os.path.basename(f), "sample": vals})
    res = {
        "币数": len(syms),
        "OI 文件数": total_files,
        "含 fetched_at 列的文件数": files_with_fetched,
        "fetched_at 全空的文件数": files_fetched_all_null,
        "fetched_at 有值的文件数": files_fetched_has_value,
        "fetched_at 总行数": rows,
        "fetched_at 空值行数": nulls,
        "空值占比": round(nulls / rows, 6) if rows else None,
        "有值样例": detail,
        "结论": ("全库 OI 的 fetched_at 均为空 ⇒ 没有任何一条 OI 观测能证明其发布/可得时点。"
                 "按 FRAME-PIT-01，只能声明“计算上不超前”（桶起点+5m≤决策时刻），"
                 "不能声明“已证明无未来函数”；OI 因子的正式 PIT 资格仍为 0。"),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2)[:1200])


if __name__ == "__main__":
    main()

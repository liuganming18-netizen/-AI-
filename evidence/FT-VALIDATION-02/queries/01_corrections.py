# -*- coding: utf-8 -*-
"""01 区间分类更正与资格包版本追溯。

- 读取 Work 最新 `FT-VALIDATION-DATA-01/out/05_hharness_资格结论.json`（13:47 版）
  与 `03_区间资格表.csv`，按 `判定` 前缀复核分层计数；
- 与 FT-VALIDATION-01 引用的 13:43 版（①618/④46/⑤59）逐项对照；
- 记录新版资格包文件 SHA，保证可追溯。
只读，不重跑、不读验证收益。
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import OUT, VDATA, V01, jdump, sha256_file, write_csv  # noqa: E402


def classify(s: str) -> str:
    s = str(s)
    if s.startswith("已用于"):
        return "①已接触"
    if s.startswith("接触历史不清楚"):
        return "④接触不清楚"
    if s.startswith("数据不足"):
        return "⑤数据不足"
    if s.startswith("不满足策略条件"):
        return "⑤x策略条件不满足"
    return "其他"


def main() -> None:
    concl_p = os.path.join(VDATA, "out/05_hharness_资格结论.json")
    table_p = os.path.join(VDATA, "out/03_区间资格表.csv")
    self_p = os.path.join(VDATA, "out/06_自检结果.json")
    concl = json.load(open(concl_p, encoding="utf-8"))
    table = pd.read_csv(table_p, encoding="utf-8-sig",
                        usecols=["symbol", "区间起", "区间止", "天数", "判定",
                                 "满足12个月", "段级接触"])
    table["cls"] = table["判定"].map(classify)
    counts = table["cls"].value_counts().to_dict()
    n_total = len(table)
    n_yi = counts.get("①已接触", 0)
    n_si = counts.get("④接触不清楚", 0)
    n_wu = counts.get("⑤数据不足", 0)
    n_wu_x = counts.get("⑤x策略条件不满足", 0)
    n_other = counts.get("其他", 0)
    # 未接触候选 = ④ + ⑤（不含策略条件不满足的特殊行）
    uncontacted = n_si + n_wu
    old = {"①": 618, "④": 46, "⑤": 59}
    new = {"①": n_yi, "④": n_si, "⑤": n_wu + n_wu_x}

    corr = pd.DataFrame([
        {"层级": "①已接触", "V01引用(13:43版)": old["①"], "V02复核(13:47版)": new["①"],
         "变化": new["①"] - old["①"], "说明": "不变"},
        {"层级": "④接触历史不清楚", "V01引用(13:43版)": old["④"], "V02复核(13:47版)": new["④"],
         "变化": new["④"] - old["④"],
         "说明": "Work 13:47 版把 3 段从⑤改判为④；自检确认④=数据合格未接触区间数=49"},
        {"层级": "⑤数据不足", "V01引用(13:43版)": old["⑤"], "V02复核(13:47版)": new["⑤"],
         "变化": new["⑤"] - old["⑤"],
         "说明": f"⑤=数据不足 {n_wu} + 策略条件不满足 {n_wu_x}（BTC 2020-09~2020-12 无真实 mark）"},
        {"层级": "合计", "V01引用(13:43版)": sum(old.values()), "V02复核(13:47版)": n_total,
         "变化": n_total - sum(old.values()), "说明": "723 行；①+④+⑤=723"},
        {"层级": "未接触候选", "V01引用(13:43版)": 104, "V02复核(13:47版)": uncontacted,
         "变化": uncontacted - 104,
         "说明": "104=④49+⑤(数据不足)55；另有 1 段'策略条件不满足'单列，故 723=618+104+1"},
        {"层级": "其他/未归类", "V01引用(13:43版)": 0, "V02复核(13:47版)": n_other,
         "变化": n_other, "说明": "应为 0"},
    ])
    write_csv(corr, "01_区间分类更正_v1.csv")

    files = [
        ("资格结论(13:47)", concl_p),
        ("自检结果", self_p),
        ("区间资格表", table_p),
        ("段级资格汇总", os.path.join(VDATA, "out/04_段级资格汇总.csv")),
        ("OI发布时点探针", os.path.join(VDATA, "out/04_OI发布时点探针.json")),
    ]
    refs = pd.DataFrame([{"名称": n, "path": os.path.relpath(p, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), "sha256": sha256_file(p),
        "size_bytes": os.path.getsize(p) if os.path.isfile(p) else None}
        for n, p in files])
    write_csv(refs, "01_资格包引用_v1.csv")

    summary = {
        "work_conclusion_generated_at": concl.get("生成时间"),
        "work_conclusion": concl.get("一句话结论"),
        "usable_intervals": concl.get("合格独立验证区间数"),
        "v01_counts": old,
        "v02_counts": new,
        "uncontacted_candidates": uncontacted,
        "special_unqualified_row": n_wu_x,
        "total_rows": n_total,
        "reconcile": {"①+④+⑤": n_yi + n_si + n_wu + n_wu_x,
                      "①+未接触候选+特殊行": n_yi + uncontacted + n_wu_x},
        "self_check_passed": json.load(open(self_p, encoding="utf-8")).get("通过"),
        "note": "V01 的 ④46/⑤59 是 13:43 读数；V02 以 13:47 版为准（④49/⑤56）。"
                "旧文件未单独保留，V01 文本即旧版引用证据。",
    }
    jdump(os.path.join(OUT, "01_更正摘要_v1.json"), summary)
    print(corr.to_string(index=False))
    print("\n", summary)


if __name__ == "__main__":
    main()

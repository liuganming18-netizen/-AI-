#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FT-FACTOR-AUDIT-01 / 02 冻结研究期输入清单

输入：out/01_全池统一索引_v1.csv、out/02_派生区间_200根4h预热_研究期交集_v1.csv（本工单上一步产物）
      evidence/FT-FACTOR-03/out/02_复用与新增标的清单.json（只读，反重力自报清单）
输出：out/04_冻结研究期输入清单_v1.csv、out/05_输入清单与反重力自报清单对账_v1.csv、out/06_分层资格汇总_v1.json

口径：
  L1 基础价量候选 = 4h(自派生, 200 根预热) ∩ 1h(交付候选) ∩ 15m(有效) ∩ [2021-12-01, 2024-12-31]
  L2 完整策略资格 = L1 + OI 1h 因子有合格区间（当前全池为 0）+ 资金费/mark 覆盖
不把 2026 留出期数据用于选因子：本清单所有区间一律裁剪在 2024-12-31 23:59:59 之前。
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone

csv.field_size_limit(10 ** 9)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(BASE))
OUT = os.path.join(BASE, "out")
AG = os.path.join(ROOT, "evidence", "FT-FACTOR-03", "out")

RESEARCH_START = datetime(2021, 12, 1, tzinfo=timezone.utc)
RESEARCH_END = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main():
    idx = load("01_全池统一索引_v1.csv")
    der = {d["symbol"]: d for d in load("02_派生区间_200根4h预热_研究期交集_v1.csv")}

    ag = json.load(open(os.path.join(AG, "02_复用与新增标的清单.json"), encoding="utf-8"))
    ag_reused = set(ag.get("reused_36_symbols", []))
    ag_new = set(ag.get("new_38_symbols", []))
    ag_all = ag_reused | ag_new

    rows = []
    for r in idx:
        sym = r["symbol"]
        d = der[sym]
        l1 = d["L1_price_volume_candidate"] == "是"
        rows.append(
            {
                "symbol": sym,
                "batch_no": r["batch_no"],
                "batch_version": r["batch_version"],
                "underlying_type": r["underlying_type"],
                "status_observed": r["status_observed"],
                "official_onboard_time_utc": r["official_onboard_time_utc"],
                "local_data_present": r["local_data_present"],
                "研究期_4h200预热后_根数": d["research_4h_after_200warm_bars"],
                "研究期_1h候选_根数": d["research_1h_after_20warm_bars"],
                "研究期_15m_根数": d["research_15m_bars"],
                "研究期_三层交集_段数": d["research_triple_intersection_segments"],
                "研究期_三层交集_1h根数": d["research_triple_intersection_bars_1h"],
                "研究期_三层交集_区间": d["research_triple_intersection_intervals"],
                "层级_L1基础价量候选": "是" if l1 else "否",
                "层级_L2完整策略资格": d["L2_full_strategy_qualified"],
                "L2未达成原因": d["L2_blocking_reason"],
                "OI_1h因子合格段数": d["oi_factor_1h_qualified_segments"],
                "OI_映射状态": d["oi_factor_mapping_status"],
                "资金费事件行数": d["funding_event_rows"],
                "mark1h有效行数": d["mark1h_valid_rows"],
                "反重力是否纳入回测": "是" if sym in ag_all else "否",
                "反重力分组": ("复用36" if sym in ag_reused else ("新增38" if sym in ag_new else "")),
                "数据指纹": r["source_input_fingerprint_sha256"],
            }
        )

    # 只输出研究期内有交集的 L1 清单作为"输入清单"，其余保留在索引与派生表中
    inputs = [x for x in rows if x["层级_L1基础价量候选"] == "是"]
    inputs.sort(key=lambda x: (-int(x["研究期_三层交集_1h根数"]), x["symbol"]))

    fields = list(rows[0].keys())
    with open(os.path.join(OUT, "04_冻结研究期输入清单_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for x in inputs:
            w.writerow(x)

    # 对账：反重力 74 币 vs 我的 L1
    l1_set = {x["symbol"] for x in inputs}
    recon = []
    for sym in sorted(ag_all):
        d = der.get(sym)
        recon.append(
            {
                "symbol": sym,
                "反重力分组": "复用36" if sym in ag_reused else "新增38",
                "在682分母内": "是" if sym in der else "否",
                "本地有数据": d["local_data_present"] if d else "",
                "4h有效根数": d["4h_valid_rows"] if d else "",
                "研究期_4h200预热后_根数": d["research_4h_after_200warm_bars"] if d else "",
                "研究期_三层交集_1h根数": d["research_triple_intersection_bars_1h"] if d else "",
                "本工单L1判定": "是" if sym in l1_set else "否",
                "一致性": "一致" if sym in l1_set else "不一致-反重力纳入但我方不认L1",
            }
        )
    # 反向：我方 L1 但反重力未纳入
    for sym in sorted(l1_set - ag_all):
        d = der[sym]
        recon.append(
            {
                "symbol": sym,
                "反重力分组": "",
                "在682分母内": "是",
                "本地有数据": d["local_data_present"],
                "4h有效根数": d["4h_valid_rows"],
                "研究期_4h200预热后_根数": d["research_4h_after_200warm_bars"],
                "研究期_三层交集_1h根数": d["research_triple_intersection_bars_1h"],
                "本工单L1判定": "是",
                "一致性": "不一致-我方认L1但反重力未纳入",
            }
        )
    with open(os.path.join(OUT, "05_输入清单与反重力自报清单对账_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(recon[0].keys()))
        w.writeheader()
        for x in recon:
            w.writerow(x)

    summary = {
        "work_order": "FT-FACTOR-AUDIT-01",
        "research_period": {"start_utc": "2021-12-01T00:00:00Z", "end_utc": "2024-12-31T23:59:59Z"},
        "denominator_682": len(rows),
        "L1_count": len(l1_set),
        "L1_total_bars": sum(int(x["研究期_三层交集_1h根数"]) for x in inputs),
        "L2_count": sum(1 for x in rows if x["层级_L2完整策略资格"] == "是"),
        "ag_declared_count": len(ag_all),
        "ag_declared_in_L1": sum(1 for x in recon if x["反重力分组"] and x["本工单L1判定"] == "是"),
        "ag_declared_not_in_L1": [x["symbol"] for x in recon if x["反重力分组"] and x["本工单L1判定"] == "否"],
        "L1_not_used_by_ag_count": sum(1 for x in recon if x["一致性"].startswith("不一致-我方")),
        "L1_not_used_by_ag_symbols": [x["symbol"] for x in recon if x["一致性"].startswith("不一致-我方")],
        "tier_definition": {
            "L1": "4h 自派生 200 根预热后区间 ∩ 1h 交付候选区间 ∩ 15m 有效区间 ∩ 冻结研究期，非空",
            "L2": "L1 + OI 1h 因子存在合格区间（全池为 0）+ 资金费与 mark 覆盖",
            "boundary": "L1 只代表价量数据可算，不代表信号会出现，也不代表正式 PIT 资格",
        },
    }
    with open(os.path.join(OUT, "06_分层资格汇总_v1.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print(json.dumps({k: v for k, v in summary.items() if k not in ("tier_definition",)}, ensure_ascii=False, indent=2)[:4000])
    print("输入清单行数 =", len(inputs))


if __name__ == "__main__":
    main()

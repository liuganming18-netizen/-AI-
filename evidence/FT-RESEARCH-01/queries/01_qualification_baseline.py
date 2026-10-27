# -*- coding: utf-8 -*-
"""01_资格基线｜复用现有资格清单，建立 FT-RESEARCH-01 的样本资格基线。

只读复用：
- FT-FACTOR-AUDIT-01/out/06_分层资格汇总_v1.json（682→526→241→L2=0 资格链）
- FT-FACTOR-AUDIT-01/out/04_冻结研究期输入清单_v1.csv（241 逐币区间）
- FT-POOL-01/06_全池核验摘要_v1.json（682/526/OI=0 资格清单）
- FT-ENTRY-03/user_data/config.json（70 币 A 路线白名单）

产出：out/01_资格清单基线_v1.csv、out/01_资格基线摘要_v1.json
不下载、不改时间戳、不跑回测。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _common import (CELLS_A, L1_CSV, L1_SUMMARY, POOL_SUMMARY, OUT,  # noqa: E402
                     jdump, load_l1, load_whitelist_70, read_csv, write_csv)


def main() -> None:
    l1 = load_l1()
    w70 = load_whitelist_70()

    with open(L1_SUMMARY, encoding="utf-8") as f:
        layer = json.load(f)
    with open(POOL_SUMMARY, encoding="utf-8") as f:
        pool = json.load(f)

    s1 = set(l1["symbol"])
    in70 = s1 & w70

    # 资格基线表（逐币）
    rows = []
    for _, r in l1.iterrows():
        sym = r["symbol"]
        rows.append({
            "symbol": sym,
            "batch_no": r.get("batch_no"),
            "batch_version": r.get("batch_version"),
            "underlying_type": r.get("underlying_type"),
            "官方上线时间UTC": r.get("official_onboard_time_utc"),
            "研究期_4h200预热后_根数": r.get("研究期_4h200预热后_根数"),
            "研究期_1h候选_根数": r.get("研究期_1h候选_根数"),
            "研究期_15m_根数": r.get("研究期_15m_根数"),
            "研究期_三层交集_段数": r.get("研究期_三层交集_段数"),
            "研究期_三层交集_1h根数": r.get("研究期_三层交集_1h根数"),
            "层级_L1基础价量候选": r.get("层级_L1基础价量候选"),
            "层级_L2完整策略资格": r.get("层级_L2完整策略资格"),
            "L2未达成原因": r.get("L2未达成原因"),
            "OI_1h因子合格段数": r.get("OI_1h因子合格段数"),
            "OI_映射状态": r.get("OI_映射状态"),
            "资金费事件行数": r.get("资金费事件行数"),
            "mark1h有效行数": r.get("mark1h有效行数"),
            "是否A路线70白名单": sym in w70,
        })
    base = read_csv(L1_CSV)  # noqa: F841  (保持与上游逐行一致的引用)
    df = __import__("pandas").DataFrame(rows)
    write_csv(df, "01_资格清单基线_v1.csv")

    # 汇总
    summary = {
        "工单": "FT-RESEARCH-01（后续样本与因果检查可行性）",
        "性质": "只读复用现有资格清单；不下载、不改时间戳、不跑回测、不打开 2026 留出",
        "资格链_上游口径照录": {
            "官方快照确认分母_682": pool["confirmed_denominator"],
            "本机有价格或指标数据_526": pool["symbols_with_local_price_or_factor_data_sum_across_batches"],
            "基础价量候选_526": pool["symbols_with_base_1h_15m_data_candidate_sum_across_batches"],
            "1h_OI因子正式资格_0": pool["symbols_with_1h_OI_factor_qualified"],
            "资金费事件文件覆盖_526": pool["symbols_with_local_funding_file_sum_across_batches"],
            "mark1h覆盖_521": pool["symbols_with_local_mark1h_file_sum_across_batches"],
            "归档独有_529": pool["archive_only_symbols"],
            "归档分类未确认_46": pool["archive_classification_unconfirmed_symbols"],
        },
        "资格链_FT-FACTOR-AUDIT-01口径照录": {
            "L1_冻结研究期基础价量候选": layer["L1_count"],
            "L1_total_bars": layer["L1_total_bars"],
            "L2_完整策略资格": layer["L2_count"],
            "反重力自报池": layer["ag_declared_count"],
            "反重力自报池在L1内": layer["ag_declared_in_L1"],
            "反重力自报池不在L1": layer["ag_declared_not_in_L1"],
        },
        "本单口径": {
            "L1_241": len(s1),
            "A路线70白名单": len(w70),
            "白名单⊆L1": len(in70),
            "L1内不在70白名单": len(s1 - w70),
        },
        "层级定义_上游原文照录": layer["tier_definition"],
        "关键边界": (
            "L1 只代表价量数据可算（4h 200 预热 ∩ 1h 候选 ∩ 15m 有效 ∩ 冻结研究期非空），"
            "不代表信号会出现，也不代表正式 PIT 资格；1h OI 因子正式资格全池为 0。"
            "『数据候选』不得冒充『完整策略资格』。"
        ),
        "上游限制照录": pool["limitations"],
    }
    jdump(os.path.join(OUT, "01_资格基线摘要_v1.json"), summary)

    print("== 资格基线（复用 FT-POOL-01 + FT-FACTOR-AUDIT-01）==")
    print(f"  682 官方快照确认分母 → 526 本机有数据/基础价量候选 → L1 241 → L2 {layer['L2_count']}")
    print(f"  1h OI 因子正式资格 = {pool['symbols_with_1h_OI_factor_qualified']}（全池）")
    print(f"  A 路线 70 白名单 ⊆ L1：{len(in70)}/{len(w70)}")
    print(f"  L1 内不在 70 白名单：{len(s1 - w70)}")
    print(f"  逐币基线表：out/01_资格清单基线_v1.csv（{len(df)} 行）")


if __name__ == "__main__":
    main()

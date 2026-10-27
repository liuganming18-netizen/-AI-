# -*- coding: utf-8 -*-
"""09 判据终版：回填 C6（块级 CI 下界）+ C7 符号口径复核 + 有效独立格数。

背景：
  03_robustness_A.py 运行在 04_stats_A.py 之前，C6 依赖 04 的 Bootstrap 结果，
  因此 03 阶段写出的 `03_稳定区判据_v1.csv` 中 C6 列是空值（null），
  既不是"通过"也不是"不通过"。

本脚本只做四件事，不改判据定义、不改上游数值、不新增对照：
  1. 把 04 的 boot_ci_lo 按 code 左连接回填，C6 = (boot_ci_lo > 0)；
  2. C7 在净收益 <= 0 的组上记 False（占比分母非正时无意义），朴素口径并列保留；
  3. 逐组两两同一性：完全相同的组对合并计数，报告"有效独立格数"；
  4. 输出终版判据表与摘要。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "out"

CRITERIA = [
    "C1_expectancy_gt_baseline",
    "C2_drop_top3_positive",
    "C3_drop_top_coin_positive",
    "C4_years_2of3_positive",
    "C5_neighbors_same_sign",
    "C6_block_ci_lower_gt0",
    "C7_top_coin_share_lt50",
]


def main() -> int:
    crit = pd.read_csv(OUT / "03_稳定区判据_v1.csv")
    stat = pd.read_csv(OUT / "04_时间块与多重比较_v1.csv")
    coin = pd.read_csv(OUT / "03_跨币集中度_v1.csv")[["code", "top_coin_share_pct", "net_positive"]]
    pairs = pd.read_csv(OUT / "07_组间逐笔同一性_v1.csv")

    print("=" * 78)
    print("09 判据终版")
    print("=" * 78)
    n_null = int(crit["C6_block_ci_lower_gt0"].isna().sum())
    print(f"03 阶段 C6 为空的行数 = {n_null} / {len(crit)}（预期 9，因为 04 尚未运行）")

    m = stat.set_index("code")[["boot_ci_lo", "boot_ci_hi", "boot_ci_excludes_0",
                                "perm_p", "holm_p", "maxt_fwer_p"]]
    crit = crit.drop(columns=["C6_block_ci_lower_gt0"])
    crit = crit.merge(m, left_on="code", right_index=True, how="left", validate="one_to_one")
    if crit["boot_ci_lo"].isna().any():
        raise SystemExit("C6 回填失败：存在无 04 统计的行")
    crit["C6_block_ci_lower_gt0"] = crit["boot_ci_lo"] > 0

    bad = crit["C6_block_ci_lower_gt0"] != crit["boot_ci_excludes_0"]
    if bad.any():
        print(f"⚠ C6 与 boot_ci_excludes_0 不一致 {int(bad.sum())} 行")
    else:
        print("✓ C6 与 04 的 boot_ci_excludes_0 逐行一致（定义等价性交叉校验通过）")

    # C7 符号口径：净收益 <= 0 时占比无意义
    crit = crit.merge(coin, on="code", how="left", validate="one_to_one")
    crit["C7_naive"] = crit["C7_top_coin_share_lt50"].astype(bool)
    crit["C7_strict"] = crit["C7_naive"] & crit["net_positive"].astype(bool)
    trap = crit[crit["C7_naive"] != crit["C7_strict"]]
    if len(trap):
        print(f"\n⚠ C7 符号陷阱 {len(trap)} 行（净收益<=0 时占比无意义）：")
        print(trap[["code", "top_coin_share_pct", "C7_naive", "C7_strict"]].to_string(index=False))
    crit["C7_top_coin_share_lt50"] = crit["C7_strict"]

    crit["pass_count"] = crit[CRITERIA].astype(bool).sum(axis=1)
    crit["pass_all_7"] = crit["pass_count"] == len(CRITERIA)
    crit["pass_count_naive_C7"] = (
        crit[[c for c in CRITERIA if c != "C7_top_coin_share_lt50"]].astype(bool).sum(axis=1)
        + crit["C7_naive"].astype(int)
    )

    # 有效独立格数：完全相同的组对视为同一格
    ident = pairs[pairs["fully_identical"]]
    redundant = []
    for _, r in ident.iterrows():
        redundant.append({"kept": r["code_a"], "redundant": r["code_b"], "trades": int(r["trades_a"])})
    effective_cells = len(crit) - len(redundant)

    cols = ["code", "strategy", "delta", "k_vol"] + CRITERIA + [
        "C5_neighbor_min_net", "pass_count", "pass_all_7",
        "C7_naive", "net_positive", "top_coin_share_pct", "pass_count_naive_C7",
        "boot_ci_lo", "boot_ci_hi", "perm_p", "holm_p", "maxt_fwer_p",
    ]
    crit = crit[cols].sort_values(["delta", "k_vol"], ascending=[False, True]).reset_index(drop=True)
    crit.to_csv(OUT / "03_稳定区判据_终版_v1.csv", index=False, encoding="utf-8-sig")

    print()
    print(crit[["code", "delta", "k_vol"] + CRITERIA + ["pass_count", "pass_all_7"]].to_string(index=False))

    n_pass_all = int(crit["pass_all_7"].sum())
    best = crit.loc[crit["pass_count"].idxmax()]
    failed_counts = {c: int((~crit[c].astype(bool)).sum()) for c in CRITERIA}

    summary = {
        "note": ("C6 由 09 脚本从 04 回填（03 阶段该列为 null）；C7 在净收益<=0 的组上记 False。"
                 "判据定义未改动。"),
        "n_groups": int(len(crit)),
        "n_effective_independent_cells": int(effective_cells),
        "redundant_pairs": redundant,
        "n_pass_all_7": n_pass_all,
        "n_pass_all_7_naive_C7": int((crit["pass_count_naive_C7"] == len(CRITERIA)).sum()),
        "best_group": {
            "code": str(best["code"]), "strategy": str(best["strategy"]),
            "delta": float(best["delta"]), "k_vol": float(best["k_vol"]),
            "pass_count": int(best["pass_count"]),
            "failed": [c for c in CRITERIA if not bool(best[c])],
            "boot_ci_lo": float(best["boot_ci_lo"]), "boot_ci_hi": float(best["boot_ci_hi"]),
            "maxt_fwer_p": float(best["maxt_fwer_p"]),
        },
        "fail_count_per_criterion": failed_counts,
        "c6_consistency_with_04": bool(not bad.any()),
        "conclusion": "0/9 通过全部 7 条判据" if n_pass_all == 0 else f"{n_pass_all}/9 通过全部 7 条判据",
    }
    (OUT / "03_判据终版摘要_v1.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")

    print()
    print(f"结论：{summary['conclusion']}")
    print(f"有效独立格数 = {effective_cells}（冗余：{redundant}）")
    print(f"最高通过数：{best['code']} {int(best['pass_count'])}/7，失手于 {summary['best_group']['failed']}")
    print("各判据未通过组数：", failed_counts)
    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())

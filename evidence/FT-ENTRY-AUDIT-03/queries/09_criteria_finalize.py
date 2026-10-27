# -*- coding: utf-8 -*-
"""
09_criteria_finalize.py — 把 04 轮的统计结果回填进 7 条判据表，产出终版。

背景（必须如实记录）：
  03_robustness_A.py 运行在 04_stats_A.py 之前，而 C6 判据依赖 04 的
  30 天块配对 Bootstrap 置信区间下界。因此 03 阶段写出的
  `03_稳定区判据_v1.csv` 中 C6 列是空的（null），不是"通过"也不是"不通过"。
  本脚本只做一件事：把 04 的 boot_ci_lo 按 code 左连接到判据表，
  计算 C6 = (boot_ci_lo > 0)，重算 7 条判据的通过数。

  不修改任何判据定义，不修改任何上游数值，不新增对照。
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
    crit_p = OUT / "03_稳定区判据_v1.csv"
    stat_p = OUT / "04_时间块与多重比较_v1.csv"
    for p in (crit_p, stat_p):
        if not p.exists():
            raise SystemExit(f"缺输入: {p}")

    crit = pd.read_csv(crit_p)
    stat = pd.read_csv(stat_p)

    print("=" * 78)
    print("09 判据终版：回填 C6")
    print("=" * 78)

    n_null_before = int(crit["C6_block_ci_lower_gt0"].isna().sum()) if "C6_block_ci_lower_gt0" in crit else len(crit)
    print(f"03 阶段 C6 为空的行数 = {n_null_before} / {len(crit)}（预期 9，因为 04 尚未运行）")

    # C6 的原始数值一并保留，便于人工复核
    m = stat.set_index("code")[["boot_ci_lo", "boot_ci_hi", "boot_ci_excludes_0", "perm_p", "holm_p", "maxt_fwer_p"]]
    crit = crit.drop(columns=[c for c in ["C6_block_ci_lower_gt0"] if c in crit.columns])
    crit = crit.merge(m, left_on="code", right_index=True, how="left", validate="one_to_one")

    missing = crit["boot_ci_lo"].isna().sum()
    if missing:
        raise SystemExit(f"C6 回填失败：{missing} 行无 04 统计")

    crit["C6_block_ci_lower_gt0"] = crit["boot_ci_lo"] > 0

    # 与 04 的 boot_ci_excludes_0 交叉校验（两者定义必须等价）
    bad = crit["C6_block_ci_lower_gt0"] != crit["boot_ci_excludes_0"]
    if bad.any():
        print(f"⚠ C6 与 boot_ci_excludes_0 不一致 {int(bad.sum())} 行：")
        print(crit.loc[bad, ["code", "boot_ci_lo", "boot_ci_hi", "C6_block_ci_lower_gt0", "boot_ci_excludes_0"]])
    else:
        print("✓ C6 与 04 的 boot_ci_excludes_0 逐行一致（定义等价性交叉校验通过）")

    # ---- C7 符号陷阱处理（必须显式记录，不能静默）----
    # 判据原文："最大贡献币占净收益 < 50%"。当净收益 < 0 时该比值没有意义
    # （A07 净 -6.10、最大币 +25.67 ⇒ 占比 -420.8%，朴素比较会判"通过"）。
    # 因此额外给一列 strict：净收益 <= 0 时 C7 直接记 False，并标注原因。
    conc = pd.read_csv(OUT / "03_跨币集中度_v1.csv")[["code", "top_coin_share_pct", "drop_top_coin_net"]]
    crit = crit.merge(conc, on="code", how="left", validate="one_to_one")
    if crit["drop_top_coin_net"].isna().any():
        raise SystemExit("C7 交叉校验失败：缺净收益")

    crit["C7_net_positive"] = crit["drop_top_coin_net"] > 0
    crit["C7_naive"] = crit["C7_top_coin_share_lt50"].astype(bool)
    crit["C7_strict"] = crit["C7_naive"] & crit["C7_net_positive"]
    trap = crit[crit["C7_naive"] != crit["C7_strict"]]
    if len(trap):
        print()
        print("⚠ C7 符号陷阱（净收益 <= 0 时占比无意义，朴素判据会误判为通过）：")
        print(trap[["code", "drop_top_coin_net", "top_coin_share_pct", "C7_naive", "C7_strict"]].to_string(index=False))
        print("  ⇒ 终版采用 strict 口径；朴素口径并列保留供复核。")

    crit["C7_top_coin_share_lt50"] = crit["C7_strict"]

    crit["pass_count"] = crit[CRITERIA].astype(bool).sum(axis=1)
    crit["pass_all_7"] = crit["pass_count"] == len(CRITERIA)

    crit["pass_count_naive_C7"] = (
        crit[[c for c in CRITERIA if c != "C7_top_coin_share_lt50"]].astype(bool).sum(axis=1)
        + crit["C7_naive"].astype(int)
    )
    crit["pass_all_7_naive_C7"] = crit["pass_count_naive_C7"] == len(CRITERIA)

    cols = ["code", "strategy", "delta", "k_vol"] + CRITERIA + [
        "C5_neighbor_min_net", "pass_count", "pass_all_7",
        "C7_naive", "C7_net_positive", "top_coin_share_pct", "drop_top_coin_net",
        "pass_count_naive_C7", "pass_all_7_naive_C7",
        "boot_ci_lo", "boot_ci_hi", "perm_p", "holm_p", "maxt_fwer_p",
    ]
    crit = crit[cols].sort_values(["delta", "k_vol"], ascending=[False, True]).reset_index(drop=True)
    crit.to_csv(OUT / "03_稳定区判据_终版_v1.csv", index=False, encoding="utf-8-sig")

    print()
    show = crit[["code", "delta", "k_vol"] + CRITERIA + ["pass_count", "pass_all_7"]]
    print(show.to_string(index=False))

    n_pass_all = int(crit["pass_all_7"].sum())
    best = crit.loc[crit["pass_count"].idxmax()]
    failed_counts = {c: int((~crit[c].astype(bool)).sum()) for c in CRITERIA}

    summary = {
        "note": (
            "C6 由 09 脚本从 04_时间块与多重比较_v1.csv 回填：03 运行时尚无 Bootstrap 结果，"
            "原表 C6 为 null。判据定义未改动。C7 在净收益<=0 的组上按 strict 口径记 False"
            "（占比分母为负时无意义），朴素口径并列保留在 C7_naive 列。"
        ),
        "n_groups": int(len(crit)),
        "n_pass_all_7": n_pass_all,
        "n_pass_all_7_naive_C7": int(crit["pass_all_7_naive_C7"].sum()),
        "c7_sign_trap": [
            {
                "code": str(r["code"]),
                "net": float(r["drop_top_coin_net"]),
                "top_coin_share_pct": float(r["top_coin_share_pct"]),
                "naive": bool(r["C7_naive"]),
                "strict": bool(r["C7_strict"]),
            }
            for _, r in trap.iterrows()
        ],
        "best_group": {
            "code": str(best["code"]),
            "strategy": str(best["strategy"]),
            "delta": float(best["delta"]),
            "k_vol": float(best["k_vol"]),
            "pass_count": int(best["pass_count"]),
            "failed": [c for c in CRITERIA if not bool(best[c])],
            "boot_ci_lo": float(best["boot_ci_lo"]),
            "boot_ci_hi": float(best["boot_ci_hi"]),
            "maxt_fwer_p": float(best["maxt_fwer_p"]),
        },
        "fail_count_per_criterion": failed_counts,
        "c6_consistency_with_04": bool(not bad.any()),
        "conclusion": "0/9 通过全部 7 条判据" if n_pass_all == 0 else f"{n_pass_all}/9 通过全部 7 条判据",
    }
    (OUT / "03_判据终版摘要_v1.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(f"结论：{summary['conclusion']}")
    print(f"最高通过数：{best['code']} {int(best['pass_count'])}/7，失手于 {summary['best_group']['failed']}")
    print("各判据未通过组数：", failed_counts)
    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())

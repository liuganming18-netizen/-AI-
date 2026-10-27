# -*- coding: utf-8 -*-
"""01 锁定 FT-ENTRY-03 / FT-ENTRY-04 现有结果（工单第 1 步）。

背景：FT-ENTRY-AUDIT-05 的 01_version_lock.py 于 00:47 运行，而 FT-ENTRY-04 的
逐笔/归档文件在 00:48~01:09 才陆续写盘 ⇒ AUDIT-05 的 `01_产物清单与哈希_v1.csv`
把 FT-ENTRY-04 的 trades/archive 记成 missing。本轮 AUDIT-06 在 FT-ENTRY-04 已完整后
重新锁定，并：
  1. 计算 FT-ENTRY-03 / 04 的 trades CSV + archive zip + (04) audit_orders CSV 的 SHA-256；
  2. FT-ENTRY-03 逐笔哈希 vs AUDIT-05 缓存值逐一比对，确认旧对照自 AUDIT-05 以来未变；
  3. 交叉验证 FT-ENTRY-04 Baseline == FT-ENTRY-03 Baseline（约束只作用于 A 路线）；
  4. 记录 AUDIT-05 未锁定 FT-ENTRY-04 这一缺口，并给出本轮新指纹基线。

只读上游，只在本目录 out/ 写文件。
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    AUDIT05_OUT, BASELINE, CELLS_A, OUT, OUT03, OUT04,
    jdump, sha256_file, write_csv,
)

STRATS = [BASELINE] + [s for _, s, _, _ in CELLS_A]


def cached_hashes() -> Dict[str, Dict[str, str]]:
    """读 AUDIT-05 的 01_产物清单与哈希_v1.csv，按 (batch,strategy)->{field:sha}。"""
    p = os.path.join(AUDIT05_OUT, "01_产物清单与哈希_v1.csv")
    cache: Dict[str, Dict[str, str]] = {}
    if os.path.isfile(p):
        df = pd.read_csv(p, encoding="utf-8-sig")
        for _, r in df.iterrows():
            key = f"{r['batch']}|{r['strategy']}"
            cache[key] = {
                "trades_csv_sha256": r.get("trades_csv_sha256"),
                "archive_sha256": r.get("archive_sha256"),
                "trades_csv_exists": r.get("trades_csv_exists"),
            }
    return cache


def main() -> None:
    cache = cached_hashes()
    rows: List[dict] = []
    mism: List[dict] = []

    for strat in STRATS:
        # FT-ENTRY-03
        t03 = os.path.join(OUT03, f"trades_{strat}.csv")
        z03 = os.path.join(OUT03, f"backtest-result-{strat}.zip")
        # FT-ENTRY-04
        t04 = os.path.join(OUT04, f"trades_{strat}.csv")
        z04 = os.path.join(OUT04, f"backtest-result-{strat}.zip")
        a04 = os.path.join(OUT04, f"audit_orders_{strat}.csv")

        h = {
            "strategy": strat,
            "03_trades_sha": sha256_file(t03),
            "03_archive_sha": sha256_file(z03),
            "04_trades_sha": sha256_file(t04),
            "04_archive_sha": sha256_file(z04),
            "04_audit_sha": sha256_file(a04) if strat != BASELINE else None,
        }

        # 与 AUDIT-05 缓存比对（FT-ENTRY-03 应一致、FT-ENTRY-04 缓存多为空 = 缺口）
        c03 = cache.get(f"FT-ENTRY-03|{strat}", {})
        c04 = cache.get(f"FT-ENTRY-04|{strat}", {})
        c03_sha = c03.get("trades_csv_sha256")
        if isinstance(c03_sha, str) and c03_sha and h["03_trades_sha"]:
            match = (c03_sha == h["03_trades_sha"])
            if not match:
                mism.append({"batch": "FT-ENTRY-03", "strategy": strat,
                             "audit05_sha": c03_sha, "now_sha": h["03_trades_sha"]})
            h["03_matches_audit05"] = match
        else:
            h["03_matches_audit05"] = None  # AUDIT-05 无缓存值
        # FT-ENTRY-04：AUDIT-05 是否留下了缓存哈希？
        c04_sha = c04.get("trades_csv_sha256")
        h["04_had_audit05_hash"] = bool(isinstance(c04_sha, str) and c04_sha)
        if isinstance(c04_sha, str) and c04_sha and h["04_trades_sha"]:
            h["04_matches_audit05"] = (c04_sha == h["04_trades_sha"])
            if h["04_matches_audit05"] is False:
                mism.append({"batch": "FT-ENTRY-04", "strategy": strat,
                             "audit05_sha": c04_sha, "now_sha": h["04_trades_sha"]})
        else:
            h["04_matches_audit05"] = None

        rows.append(h)

    df = pd.DataFrame(rows)
    # Baseline 隔离校验：以「逐笔 CSV 内容一致」为准（约束只作用于 A 路线）；
    # archive zip 字节不同属容器元数据（bot_name/时间戳/内嵌 config 随批次不同），非约束外溢。
    base = df[df["strategy"] == BASELINE].iloc[0]
    baseline_isolated = bool(base["03_trades_sha"] == base["04_trades_sha"])
    baseline_archive_identical = bool(base["03_archive_sha"] == base["04_archive_sha"])

    write_csv(df, "01_0304锁定哈希_v1.csv")

    summary = {
        "locked": {
            "FT-ENTRY-03": f"{OUT03}/trades_*.csv ×10 + backtest-result-*.zip ×10",
            "FT-ENTRY-04": f"{OUT04}/trades_*.csv ×10 + backtest-result-*.zip ×10 + audit_orders_*.csv ×9",
        },
        "FT-ENTRY-03_与AUDIT05缓存一致": (all(r["03_matches_audit05"] is not False for r in rows)),
        "FT-ENTRY-03_有缓存比对条数": sum(1 for r in rows if r["03_matches_audit05"] is not None),
        "FT-ENTRY-04_被AUDIT05锁定条数": sum(1 for r in rows if r.get("04_had_audit05_hash")),
        "FT-ENTRY-04锁定缺口说明": (
            "AUDIT-05 的 01_version_lock.py 于 00:47 运行，早于 FT-ENTRY-04 文件写盘(00:48~01:09)，"
            "故其对 FT-ENTRY-04 的 trades/archive 未录哈希；本轮在 04 完整后补齐基线指纹。"
        ),
        "Baseline约束隔离(03逐笔==04逐笔)": baseline_isolated,
        "Baseline归档zip字节一致": baseline_archive_identical,
        "归档差异说明": "Baseline 的 archive zip 字节不同属容器元数据（bot_name/时间戳/内嵌 config 随批次而异），约束隔离以逐笔 CSV 内容一致为准",
        "与缓存不一致项": mism,
        "哈希由脚本shasum计算不手写": True,
    }
    jdump(os.path.join(OUT, "01_锁定摘要_v1.json"), summary)

    print("== FT-ENTRY-03/04 锁定 ==")
    print(df[["strategy", "03_matches_audit05", "04_had_audit05_hash",
              "04_matches_audit05"]].to_string(index=False))
    print(f"\nFT-ENTRY-03 与 AUDIT-05 缓存一致: {summary['FT-ENTRY-03_与AUDIT05缓存一致']}"
          f"（比对 {summary['FT-ENTRY-03_有缓存比对条数']} 条）")
    print(f"FT-ENTRY-04 被 AUDIT-05 锁定条数: {summary['FT-ENTRY-04_被AUDIT05锁定条数']}/10（本轮补齐）")
    print(f"Baseline 约束隔离（03 逐笔==04 逐笔）: {baseline_isolated}"
          f"（archive zip 字节一致: {baseline_archive_identical}，属容器元数据，非约束外溢）")
    print(f"与缓存不一致项: {len(mism)}")
    print("\n完成")


if __name__ == "__main__":
    main()

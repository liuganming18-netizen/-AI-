#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BREAKEVEN-02 · 步骤六：候选级 vs 事件级去重敏感性对照

背景
----
`out/paired_trades.csv` 的 206 行是按 `signal_id`（候选编号）去重的结果。
但同一个 4h 箱体如果起点不同，会生成两个不同的候选编号，而它们指向
**同标的、同入场时刻、同一根 15m K 线**——本质是同一笔交易被数了两次。

本脚本只读 `out/paired_trades.csv`，按「同标的 + 同入场时刻」再收一层，
保留 `run_idx` 最小的那一行，输出两套口径的对照，写 `out/dedup_sensitivity.json`。
"""
from __future__ import annotations

import collections
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
SRC = OUT / "paired_trades.csv"
DST = OUT / "dedup_sensitivity.json"


def f(row: dict, key: str) -> float:
    return float(row[key])


def stats(rows: list[dict]) -> dict:
    n = len(rows)
    A = sum(f(r, "A_net") for r in rows)
    B = sum(f(r, "B_net") for r in rows)
    C = sum(f(r, "C_net") for r in rows)
    # 少亏 = 变好的正增量之和；代价 = 变坏的负增量绝对值之和
    b_saved = sum(max(0.0, f(r, "B_net") - f(r, "A_net")) for r in rows)
    b_cost = sum(max(0.0, f(r, "A_net") - f(r, "B_net")) for r in rows)
    c_saved = sum(max(0.0, f(r, "C_net") - f(r, "A_net")) for r in rows)
    c_cost = sum(max(0.0, f(r, "A_net") - f(r, "C_net")) for r in rows)

    def win(key: str) -> float:
        return round(100.0 * sum(1 for r in rows if f(r, key) > 0) / n, 2)

    return {
        "n": n,
        "A_net": round(A, 4),
        "B_net": round(B, 4),
        "C_net": round(C, 4),
        "B_delta": round(B - A, 4),
        "C_delta": round(C - A, 4),
        "B_triggered": sum(1 for r in rows if r["B_be_triggered"] == "True"),
        "C_triggered": sum(1 for r in rows if r["C_be_triggered"] == "True"),
        "B_saved_loss": round(b_saved, 2),
        "B_cost_forgone": round(b_cost, 2),
        "C_saved_loss": round(c_saved, 2),
        "C_cost_forgone": round(c_cost, 2),
        "win_rate_A": win("A_net"),
        "win_rate_B": win("B_net"),
        "win_rate_C": win("C_net"),
    }


def main() -> int:
    with SRC.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 206, f"预期 206 行，实际 {len(rows)}"
    assert len({r["signal_id"] for r in rows}) == 206, "signal_id 不唯一"

    groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in rows:
        groups[(r["symbol"], r["entry_time_utc"])].append(r)

    keep: list[dict] = []
    dropped: list[dict] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda r: int(r["run_idx"]))
        keep.append(members[0])
        dropped.extend(members[1:])

    payload = {
        "note": "候选级=按 signal_id 去重；事件级=再按(同标的+同入场时刻)去重，保留 run_idx 最小者",
        "duplicate_groups": len(dropped),
        "candidate_level": stats(rows),
        "event_level": stats(keep),
        "dropped_rows": [
            {
                "symbol": r["symbol"],
                "entry_time_utc": r["entry_time_utc"],
                "signal_id": r["signal_id"],
                "run_idx": int(r["run_idx"]),
                "A_net": round(f(r, "A_net"), 4),
                "B_net": round(f(r, "B_net"), 4),
                "C_net": round(f(r, "C_net"), 4),
            }
            for r in sorted(dropped, key=lambda r: (r["symbol"], r["entry_time_utc"]))
        ],
    }
    DST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    cl, el = payload["candidate_level"], payload["event_level"]
    print(f"候选级 n={cl['n']}  A {cl['A_net']:+.2f}  B {cl['B_net']:+.2f}  C {cl['C_net']:+.2f}"
          f"  B差 {cl['B_delta']:+.2f}  C差 {cl['C_delta']:+.2f}")
    print(f"事件级 n={el['n']}  A {el['A_net']:+.2f}  B {el['B_net']:+.2f}  C {el['C_net']:+.2f}"
          f"  B差 {el['B_delta']:+.2f}  C差 {el['C_delta']:+.2f}")
    print(f"重复组 {payload['duplicate_groups']} 组，剔除行净额合计 "
          f"{sum(d['A_net'] for d in payload['dropped_rows']):+.2f} U")
    print(f"→ {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

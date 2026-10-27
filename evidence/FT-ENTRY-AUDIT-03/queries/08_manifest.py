# -*- coding: utf-8 -*-
"""08 交付自检：上游只读证明 + 本目录产物哈希清单。

- 重算上游 19 个归档 zip 与 config 的 SHA-256，与 01 轮记录比对，证明审计期间未被改动；
- 列出本目录全部产物的 SHA-256 与行数。
输出：out/08_*.csv|json
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import AUDIT, BASELINE, CELLS_A, CELLS_C, ENTRY02, OUT, OUT02, jdump, write_csv  # noqa: E402


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    recorded = pd.read_csv(os.path.join(OUT, "00_归档元数据一致性_v1.csv"), encoding="utf-8-sig")
    recorded = recorded.set_index("strategy")

    rows = []
    for code, strat in [("BASE", BASELINE)] + [(c, s) for c, s, _, _ in CELLS_A] + [(c, s) for c, s, _, _ in CELLS_C]:
        zp = os.path.join(OUT02, f"backtest-result-{strat}.zip")
        cp = os.path.join(OUT02, f"trades_{strat}.csv")
        rows.append({
            "code": code, "strategy": strat,
            "zip_sha256_now": sha256(zp),
            "zip_sha256_at_01": recorded.loc[strat, "archive_sha256"] if strat in recorded.index else None,
            "zip_unchanged": (sha256(zp) == recorded.loc[strat, "archive_sha256"]) if strat in recorded.index else None,
            "trades_csv_sha256": sha256(cp),
            "trades_csv_mtime": pd.Timestamp(os.path.getmtime(cp), unit="s", tz="UTC").isoformat(),
        })
    up = pd.DataFrame(rows)
    write_csv(up, "08_上游只读证明_v1.csv")
    print(up[["code", "strategy", "zip_unchanged"]].to_string(index=False))

    cfg_now = sha256(os.path.join(ENTRY02, "user_data/config.json"))
    base_now = sha256(os.path.join(ENTRY02, "user_data/strategies/WyckoffParamExplorationBase.py"))
    prev = json.load(open(os.path.join(OUT, "00_样本一致性_v1.json"), encoding="utf-8"))
    proof = {
        "config_sha256_now": cfg_now,
        "config_sha256_at_01": prev["config_sha256"],
        "config_unchanged": cfg_now == prev["config_sha256"],
        "strategy_base_sha256_now": base_now,
        "strategy_base_sha256_at_01": prev["strategy_base_sha256"],
        "strategy_base_unchanged": base_now == prev["strategy_base_sha256"],
        "all_zip_unchanged": bool(up["zip_unchanged"].all()),
        "note": "01 轮与本轮之间的哈希比对；不主张审计开始前对方文件的状态",
    }
    jdump(os.path.join(OUT, "08_上游只读证明_v1.json"), proof)
    print(json.dumps(proof, ensure_ascii=False, indent=2))

    # 本目录产物清单
    own = []
    for root, _, files in os.walk(AUDIT):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, AUDIT)
            if rel.startswith("out/08_"):
                continue
            n_lines = None
            if fn.endswith((".csv", ".md", ".py", ".log", ".json")):
                try:
                    with open(p, encoding="utf-8", errors="ignore") as f:
                        n_lines = sum(1 for _ in f)
                except Exception:
                    n_lines = None
            own.append({"path": rel, "size_bytes": os.path.getsize(p), "lines": n_lines, "sha256": sha256(p)})
    own_df = pd.DataFrame(own).sort_values("path")
    write_csv(own_df, "08_本目录产物清单_v1.csv")
    print(f"本目录产物 {len(own_df)} 个")
    print("完成")


if __name__ == "__main__":
    main()

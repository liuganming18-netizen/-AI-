# -*- coding: utf-8 -*-
"""08 交付自检：上游只读证明 + 本目录产物哈希清单。

- 重算上游 10 个归档 zip、10 个逐笔 CSV、config.json、策略基类、9 个子类、
  event_trend_v0_1/config.py 的 SHA-256，与 01 轮记录比对，证明审计期间未被改动；
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
from _common import (  # noqa: E402
    AUDIT, BASELINE, CELLS_A, ENTRY03, OUT, OUT03, ROOT, jdump, write_csv,
)

CFG_JSON = os.path.join(ENTRY03, "user_data/config.json")
BASE_STRATEGY = os.path.join(ENTRY03, "user_data/strategies/WyckoffParamExplorationBase.py")
EVENT_CONFIG = os.path.join(ROOT, "策略/WyckoffMaster/event_trend_v0_1/config.py")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    recorded = pd.read_csv(os.path.join(OUT, "00_版本锁定表_v1.csv"), encoding="utf-8-sig")
    recorded = recorded.set_index("strategy")

    rows = []
    for code, strat in [("BASE", BASELINE)] + [(c, s) for c, s, _, _ in CELLS_A]:
        zp = os.path.join(OUT03, f"backtest-result-{strat}.zip")
        cp = os.path.join(OUT03, f"trades_{strat}.csv")
        sp = os.path.join(ENTRY03, "user_data/strategies", f"{strat}.py")
        rows.append({
            "code": code, "strategy": strat,
            "zip_sha256_now": sha256(zp),
            "zip_sha256_at_01": recorded.loc[strat, "archive_sha256"],
            "zip_unchanged": sha256(zp) == recorded.loc[strat, "archive_sha256"],
            "trades_csv_sha256_now": sha256(cp),
            "trades_csv_sha256_at_01": recorded.loc[strat, "trades_csv_sha256"],
            "trades_csv_unchanged": sha256(cp) == recorded.loc[strat, "trades_csv_sha256"],
            "strategy_src_sha256_now": sha256(sp),
            "strategy_src_unchanged": sha256(sp) == recorded.loc[strat, "strategy_src_sha256"],
            "mtime": pd.Timestamp(os.path.getmtime(cp), unit="s", tz="UTC").isoformat(),
        })
    up = pd.DataFrame(rows)
    write_csv(up, "08_上游只读证明_v1.csv")
    print(up[["code", "strategy", "zip_unchanged", "trades_csv_unchanged", "strategy_src_unchanged"]].to_string(index=False))

    prev = json.load(open(os.path.join(OUT, "00_样本一致性_v1.json"), encoding="utf-8"))
    proof = {
        "config_sha256_now": sha256(CFG_JSON),
        "config_sha256_at_01": prev["config_sha256"],
        "config_unchanged": sha256(CFG_JSON) == prev["config_sha256"],
        "strategy_base_sha256_now": sha256(BASE_STRATEGY),
        "strategy_base_sha256_at_01": prev["strategy_base_sha256"],
        "strategy_base_unchanged": sha256(BASE_STRATEGY) == prev["strategy_base_sha256"],
        "event_config_sha256_now": sha256(EVENT_CONFIG),
        "event_config_sha256_at_01": prev["event_config_sha256_now"],
        "event_config_unchanged_during_audit": sha256(EVENT_CONFIG) == prev["event_config_sha256_now"],
        "event_config_caveat": "该模块无批次运行时快照；只能证明审计期间未变，不能证明 FT-ENTRY-03 运行当时的状态",
        "all_zip_unchanged": bool(up["zip_unchanged"].all()),
        "all_trades_csv_unchanged": bool(up["trades_csv_unchanged"].all()),
        "all_strategy_src_unchanged": bool(up["strategy_src_unchanged"].all()),
        "note": "01 轮与本轮之间的哈希比对；不主张审计开始前对方文件的状态",
    }
    jdump(os.path.join(OUT, "08_上游只读证明_v1.json"), proof)
    print(json.dumps(proof, ensure_ascii=False, indent=2))

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

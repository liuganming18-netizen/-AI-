# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜从每组 meta_<策略>.json 重建运行索引。

背景（如实记录）：批量脚本跑完 45 组后，父进程在写 `02_运行索引.csv` 前被回收，
索引表停留在 5 行；但**每组自己的 `meta_<策略>.json` 与结果文件都在**。
本脚本只从这些**逐组落盘的事实**重建索引，不补造、不覆盖任何结果文件。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

EXP = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-01")
OUT = EXP / "out"
RAW = OUT / "raw_results"

COLS = ["run_id", "strategy", "group", "group_label", "param_group", "c_vol_ratio_vs",
        "c_retest_zone_atr", "stoploss_s", "d_spec", "timerange", "command", "cwd",
        "started_at", "finished_at", "elapsed_sec", "returncode", "trades",
        "net_pnl_realized_plus_open_mark", "log", "trades_csv", "result_zip",
        "strategy_sha256", "config_sha256", "note"]


def main() -> None:
    rows = []
    for p in sorted(RAW.glob("meta_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        d["command"] = json.dumps(d.get("command"), ensure_ascii=False)
        d["d_spec"] = json.dumps(d.get("d_spec"), ensure_ascii=False)
        rows.append(d)
    df = pd.DataFrame(rows)
    for c in COLS:
        if c not in df.columns:
            df[c] = None
    df = df[COLS]
    order = {"A": 0, "B": 1, "C": 2, "P1": 3, "P2": 4}
    df["_o"] = df["group"].map(order).fillna(9)
    df = df.sort_values(["_o", "param_group"]).drop(columns=["_o"])
    df.to_csv(OUT / "02_运行索引.csv", index=False, encoding="utf-8-sig")

    print(f"重建索引: {len(df)} 行")
    print("returncode 分布:", df["returncode"].value_counts().to_dict())
    print("零成交组:", int((df["trades"] == 0).sum()))
    print(df.groupby("group")["trades"].agg(["count", "sum", "min", "max"]).to_string())


if __name__ == "__main__":
    main()

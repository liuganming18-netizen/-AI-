#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FT-REPLAY-01：从 run 目录重新生成「研究期运行登记索引」CSV。

只读 `run.json` + `trades.jsonl`，逐 run 现算，不读任何中间汇总。
用法：
    $PY evidence/FT-REPLAY-01/code_r1/02_生成索引.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS = PROJECT_ROOT / "回测日志" / "event_trend_v0_1" / "runs"
OUT = PROJECT_ROOT / "evidence" / "FT-REPLAY-01" / "R1_01_研究期运行登记索引.csv"
PREFIX = "run_FT_ENTRY_09_"


def _trades(p: Path) -> List[Dict[str, Any]]:
    if not p.is_file():
        return []
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def main() -> int:
    rows: List[Dict[str, Any]] = []
    for d in sorted(RUNS.iterdir()):
        if not d.is_dir() or not d.name.startswith(PREFIX):
            continue
        try:
            run = json.loads((d / "run.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        spec = run.get("spec") or {}
        win = run.get("window") or {}
        params = spec.get("params") or {}
        ts = _trades(d / "trades.jsonl")
        net = sum(t.get("net_pnl") or 0 for t in ts)
        rows.append({
            "运行ID": run.get("run_id") or d.name,
            "模式": spec.get("timing_mode"),
            "参数组": spec.get("strategy_name"),
            "币种": (spec.get("symbols") or [""])[0],
            "vs": params.get("vs"),
            "z": params.get("z"),
            "成交笔数": len(ts),
            "净盈亏": round(net, 6),
            "窗口起(UTC ms)": win.get("win_start_ms"),
            "窗口止(UTC ms)": win.get("win_end_ms"),
            "研究期标记": run.get("research_period"),
            "登记来源": (run.get("registration") or {}).get("registered_by"),
            "数据来源": "evidence/FT-ENTRY-09/out/00_all_27_trades_consolidated.csv",
        })
    if not rows:
        print(f"没有 {PREFIX}* 的 run：{RUNS}")
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"已写入 {OUT}：{len(rows)} 行，成交合计 "
          f"{sum(r['成交笔数'] for r in rows)} 笔，净盈亏 {sum(r['净盈亏'] for r in rows):.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

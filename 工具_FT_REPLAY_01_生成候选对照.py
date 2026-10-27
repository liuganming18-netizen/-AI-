#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FT-REPLAY-01：把「同一候选在 C0 / C1 / C2 中的结果」汇总成一份只读索引。

为什么需要
----------
研究期 run 的粒度是「参数组 × 币种」，一个 run 只装一种模式的结果。可复盘时最常问的
是「**同一个候选**，C0 直接进了、C1 等确认后没进、C2 进了但更差」——这要跨 run 看。
每次请求去扫 1065 个目录（每个目录 3 个 jsonl）不现实，所以导入时算一次，落成索引，
读接口只查字典。

数据来源（**只读，不推算**）
--------------------------
· `trades.jsonl`      → 真实成交（filled）
· `rejected.jsonl`    → 候选被拒（rejected，带原因）
· `not_filled.jsonl`  → 确认通过但没成交（not_filled）
三者的 `event_id` 是同一个上游候选 ID，直接当键。

输出
----
`<批次目录>/_candidate_matrix.json`：`{event_id: [ {mode, strategy, symbol, status, …} ]}`。
它**不是交易账本**，只是「同一候选在各模式下的结果」的只读投影，不进任何实盘/模拟链路。

用法
----
    $PY 工具_FT_REPLAY_01_生成候选对照.py            # 体检，不写盘
    $PY 工具_FT_REPLAY_01_生成候选对照.py --apply    # 写 _candidate_matrix.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
BATCH_DIR = PROJECT_ROOT / "回测日志" / "event_trend_v0_1" / "runs_ft_replay_01"
OUT_NAME = "_candidate_matrix.json"


def _rows(p: Path) -> List[Dict[str, Any]]:
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _mode_of(strategy: str) -> Optional[str]:
    """`Wyckoff_C1_Vs04_Z02` → `C1`；解析不出给 None（不猜）。"""
    parts = (strategy or "").split("_")
    for p in parts:
        if p == "C" or (p.startswith("C") and p[1:].isdigit()):
            return p
    return None


def build_matrix() -> Dict[str, List[Dict[str, Any]]]:
    matrix: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    dirs = sorted(d for d in BATCH_DIR.iterdir() if d.is_dir()) if BATCH_DIR.is_dir() else []
    for d in dirs:
        run: Dict[str, Any] = {}
        try:
            run = json.loads((d / "run.json").read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            run = {}
        spec = run.get("spec") or {}
        strategy = str(spec.get("strategy_name") or "")
        mode = _mode_of(strategy)
        params = spec.get("params") or {}
        base = {
            "run_id": run.get("run_id") or d.name,
            "strategy": strategy or None,
            "mode": mode,
            "symbol": (spec.get("symbols") or [None])[0],
            "vs": params.get("vs"),
            "z": params.get("z"),
        }

        for t in _rows(d / "trades.jsonl"):
            ev = (t.get("source") or {}).get("event_id")
            if not ev:
                continue
            matrix[ev].append({
                **base,
                "status": "filled",
                "trade_id": t.get("trade_id"),
                "net_pnl": t.get("net_pnl"),
                "entry_time_ms": t.get("entry_time_ms"),
                "exit_time_ms": t.get("exit_time_ms"),
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "exit_reason": t.get("exit_reason"),
            })
        for r in _rows(d / "rejected.jsonl"):
            ev = r.get("event_id")
            if not ev:
                continue
            matrix[ev].append({
                **base,
                "status": "rejected",
                "reason": r.get("reason"),
                "signal_time_ms": r.get("signal_time_ms"),
                "confirm_time_ms": r.get("confirm_time_ms"),
            })
        for r in _rows(d / "not_filled.jsonl"):
            ev = r.get("event_id")
            if not ev:
                continue
            matrix[ev].append({
                **base,
                "status": "not_filled",
                "reason": r.get("reason"),
                "signal_time_ms": r.get("signal_time_ms"),
                "confirm_time_ms": r.get("confirm_time_ms"),
            })
    # 去重规则（实测出来的，不是猜的）：
    #   · **成交（filled）一律保留**：同一候选在同一参数组里可能多次入场
    #     （止损出场后同一箱体再次触发，实测 77 组共 86 笔，间隔 15–75 分钟）。
    #     把它们合并会把真实的多笔成交压成一笔，等于改账本。
    #   · 拒单 / 未成交按 (策略, 币种) 去重：同一候选在 1000BONK / BONK 两种命名下
    #     会各命中一条，那是同一件事，只留一条。
    rank = {"filled": 0, "rejected": 1, "not_filled": 2}
    for ev, items in matrix.items():
        filled = [it for it in items if it["status"] == "filled"]
        seen_fill = set()
        keep: List[Dict[str, Any]] = []
        for it in filled:
            k = str(it.get("trade_id") or "")
            if k in seen_fill:
                continue
            seen_fill.add(k)
            keep.append(it)
        best: Dict[tuple, Dict[str, Any]] = {}
        for it in items:
            if it["status"] == "filled":
                continue
            k = (str(it.get("strategy") or ""), str(it.get("symbol") or ""))
            cur = best.get(k)
            if cur is None or rank.get(it["status"], 9) < rank.get(cur["status"], 9):
                best[k] = it
        keep.extend(best.values())
        matrix[ev] = sorted(keep, key=lambda x: (rank.get(x["status"], 9), str(x.get("mode"))))
    return dict(matrix)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="生成候选跨模式对照索引")
    ap.add_argument("--apply", action="store_true", help="写盘")
    args = ap.parse_args(argv)

    if not BATCH_DIR.is_dir():
        print(f"批次目录不存在：{BATCH_DIR}")
        return 2
    m = build_matrix()
    total = sum(len(v) for v in m.values())
    multi = sum(1 for v in m.values() if len({x.get("mode") for x in v}) > 1)
    by_status: Dict[str, int] = defaultdict(int)
    for v in m.values():
        for x in v:
            by_status[x["status"]] += 1
    print(f"候选事件 {len(m)} 个｜结果条目 {total} 条｜跨模式可对照 {multi} 个")
    print(f"状态分布：{dict(by_status)}")

    if not args.apply:
        print("只体检，未写盘（加 --apply 才写）")
        return 0
    p = BATCH_DIR / OUT_NAME
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    print(f"已写入 {p}（{p.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

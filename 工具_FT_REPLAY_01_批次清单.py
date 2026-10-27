#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FT-REPLAY-01：为研究期批次生成清单（`runs.jsonl` + `batch_meta.json`）。

为什么还要清单
--------------
研究期 run 的**实体目录登记在主运行根** `回测日志/event_trend_v0_1/runs/`
（这样列表 / 详情 / 逐笔 / K 线 / 标记全部复用既有页面与接口，不另造一套）。
但「这批一共跑了多少次、多少钱」仍需一个批次级入口，所以批次目录里留一份
**清单型批次**：每个 run 一行，汇总字段全部由这 1065 个 run 的落盘文件现算，
不含任何估算、不含任何账本写入。

数据来源（只读）
--------------
· `<runs>/<run_id>/run.json`     → 窗口 / 标的 / 策略 / 参数 / 状态
· `<runs>/<run_id>/trades.jsonl` → 笔数 / 净盈亏（逐笔求和）

用法
----
    $PY 工具_FT_REPLAY_01_批次清单.py            # 体检
    $PY 工具_FT_REPLAY_01_批次清单.py --apply    # 写 runs.jsonl + batch_meta.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
ROOT = PROJECT_ROOT / "回测日志" / "event_trend_v0_1"
RUNS = ROOT / "runs"
BATCH_DIR = ROOT / "runs_ft_replay_01"
BATCH_ID = "runs_ft_replay_01"
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


def collect() -> List[Dict[str, Any]]:
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
            "run_id": run.get("run_id") or d.name,
            "status": run.get("status"),
            "strategy": spec.get("strategy_name"),
            "mode": spec.get("timing_mode"),
            "vs": params.get("vs"),
            "z": params.get("z"),
            "symbol": (spec.get("symbols") or [None])[0],
            "start_ms": win.get("win_start_ms"),
            "end_ms": win.get("win_end_ms"),
            "trades": len(ts),
            "net_pnl": round(net, 8),
            "research_period": bool(run.get("research_period")),
        })
    return rows


def build_meta(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    starts = [r["start_ms"] for r in rows if isinstance(r.get("start_ms"), int)]
    ends = [r["end_ms"] for r in rows if isinstance(r.get("end_ms"), int)]
    with_trades = [r for r in rows if (r.get("trades") or 0) > 0]
    return {
        "batch_id": BATCH_ID,
        "title": "FT-ENTRY-09 研究期结果（参数组 × 币种，只读复盘）",
        "generated_at_ms": int(time.time() * 1000),
        "runs_total": len(rows),
        "runs_ok": sum(1 for r in rows if r.get("status") == "succeeded"),
        "runs_no_data": sum(1 for r in rows if not (r.get("trades") or 0)),
        "runs_with_trades": len(with_trades),
        "runs_win": sum(1 for r in with_trades if (r.get("net_pnl") or 0) > 0),
        "runs_loss": sum(1 for r in with_trades if (r.get("net_pnl") or 0) < 0),
        "trades_total": sum(r.get("trades") or 0 for r in rows),
        "net_pnl_sum": round(sum(r.get("net_pnl") or 0 for r in rows), 8),
        "fee_total_sum": None,
        "funding_total_sum": None,
        "window_start_ms": min(starts) if starts else None,
        "window_end_ms": max(ends) if ends else None,
        "symbols_count": len({r.get("symbol") for r in rows if r.get("symbol")}),
        "groups": len({r.get("strategy") for r in rows if r.get("strategy")}),
        "manifest_batch": False,
        "run_dirs": "symlink",
        "derived_from": "各 run 的 run.json + trades.jsonl 逐笔现算（只读）",
        "source": {
            "runs_jsonl": str(BATCH_DIR / "runs.jsonl"),
            "run_dirs_root": str(RUNS),
            "note": ("run 实体目录登记在主运行根，按前缀 run_FT_ENTRY_09_ 检索；"
                     "本批次目录里的 run_* 全部是**指向实体目录的相对软链**，不复制、不占双份磁盘"),
        },
        "note": ("研究期结果：由 FT-ENTRY-09 落盘 CSV 重建，**非本引擎重跑**；"
                 "只读复盘用，不进生产订单 / 模拟实盘账本。"
                 "批次清单靠软链才能列出来（否则被当成「清单型批次」显示成 0 个 run）"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="生成研究期批次清单")
    ap.add_argument("--apply", action="store_true", help="写盘")
    args = ap.parse_args(argv)

    rows = collect()
    if not rows:
        print(f"主运行根里没有 {PREFIX}* 的 run：{RUNS}")
        return 2
    meta = build_meta(rows)
    print(f"run 数 {meta['runs_total']}｜有成交 {meta['runs_with_trades']}｜"
          f"成交 {meta['trades_total']} 笔｜净盈亏 {meta['net_pnl_sum']}")
    print(f"标的 {meta['symbols_count']} 个｜参数组 {meta['groups']} 个｜"
          f"窗口 {meta['window_start_ms']} ~ {meta['window_end_ms']}")
    if not args.apply:
        print("只体检，未写盘（加 --apply 才写）")
        return 0
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    p = BATCH_DIR / "runs.jsonl"
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(p)
    m = BATCH_DIR / "batch_meta.json"
    tmp = m.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(m)
    print(f"已写入 {p}（{p.stat().st_size} 字节）")
    print(f"已写入 {m}（{m.stat().st_size} 字节）")
    # 软链：批次端点按「批次目录下的 run 子目录」列清单；实体目录在主运行根，
    # 不建软链就会被当成「清单型批次」⇒ 页面显示 0 个 run（数字对但清单空）。
    made = skipped = 0
    for r in rows:
        rid = r.get("run_id")
        if not rid:
            continue
        src = RUNS / rid
        dst = BATCH_DIR / rid
        if not src.is_dir():
            continue
        if dst.is_symlink() or dst.exists():
            skipped += 1
            continue
        dst.symlink_to(os.path.relpath(src, BATCH_DIR), target_is_directory=True)
        made += 1
    print(f"软链：新建 {made}｜已存在 {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

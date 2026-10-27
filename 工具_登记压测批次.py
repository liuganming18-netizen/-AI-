# -*- coding: utf-8 -*-
"""把「不落 run 目录的批量压测」登记成系统里可查的批次（BACKFILL-01，2026-10-21）。

背景
────
`run_full_sweep_200x100.py` 跑了 19496 次回测（200 组 × 最多 100 标的），但它
**故意不写 run 目录**——20000 个目录约 36GB，本机磁盘不够。结果只落在
`evidence/FULL_SWEEP_200x100/runs.jsonl`（每次一行）。

后果：界面上完全看不到这 19496 笔，用户以为「没跑过」。

本工具的做法
────────────
在批次根下建 `runs_full_sweep_200x100/`，写一个 `batch_meta.json`（**清单型批次**：
没有 run 子目录，数字全部由 runs.jsonl 现算）。`backtest_batches` 认这个文件后，
批次列表/汇总就能显示真实条数与净盈亏，明细指回 runs.jsonl / trades.jsonl。

纪律
────
· 所有数字**现算自 runs.jsonl**，不抄报告、不估；
· 不建 19496 个空目录（避免磁盘与列表双重爆炸）；
· 不冒充「有逐笔账本」——明细入口如实指向 jsonl 原文件。

用法
────
    PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
    $PY 工具_登记压测批次.py            # 体检并打印统计
    $PY 工具_登记压测批次.py --apply    # 写 batch_meta.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
BATCH_ROOT = PROJECT_ROOT / "回测日志" / "event_trend_v0_1"
SWEEP_DIR = PROJECT_ROOT / "evidence" / "FULL_SWEEP_200x100"
BATCH_ID = "runs_full_sweep_200x100"


def _date_ms(s: str, *, end: bool = False) -> Optional[int]:
    try:
        d = datetime.strptime(str(s), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    if end:
        d = d.replace(hour=23, minute=59, second=59, microsecond=999000)
    return int(d.timestamp() * 1000)


def _iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute() -> Dict[str, Any]:
    rj = SWEEP_DIR / "runs.jsonl"
    if not rj.is_file():
        raise SystemExit(f"找不到逐次记录：{rj}")
    rows: List[Dict[str, Any]] = []
    with rj.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:                                 # noqa: BLE001
                continue
    if not rows:
        raise SystemExit("runs.jsonl 为空")

    ok = [r for r in rows if r.get("status") == "ok"]
    nod = [r for r in rows if r.get("status") == "no_data"]
    traded = [r for r in rows if (r.get("trades") or 0) > 0]
    net = sum(float(r.get("net") or 0.0) for r in rows)
    fees = sum(float(r.get("fees") or 0.0) for r in rows)
    fund = sum(float(r.get("funding") or 0.0) for r in rows)
    slip = sum(float(r.get("slip") or 0.0) for r in rows)
    trades_total = sum(int(r.get("trades") or 0) for r in rows)
    wins = sum(1 for r in rows if float(r.get("net") or 0.0) > 0)
    losses = sum(1 for r in rows if float(r.get("net") or 0.0) < 0)
    starts = [r["start"] for r in rows if r.get("start")]
    ends = [r["end"] for r in rows if r.get("end")]
    ws = _date_ms(min(starts)) if starts else None
    we = _date_ms(max(ends), end=True) if ends else None

    return {
        "batch_id": BATCH_ID,
        "kind": "manifest",
        "title": "全币种全时段错开压测（200 组 × 100 标的，仅变体 A）",
        "runs_total": len(rows),
        "runs_ok": len(ok),
        "runs_no_data": len(nod),
        "runs_with_trades": len(traded),
        "runs_win": wins,
        "runs_loss": losses,
        "trades_total": trades_total,
        "net_pnl_sum": round(net, 8),
        "fee_total_sum": round(fees, 8),
        "funding_total_sum": round(fund, 8),
        "slippage_total_sum": round(slip, 8),
        "symbols_count": len({r.get("symbol") for r in rows if r.get("symbol")}),
        "groups": len({r.get("group") for r in rows if r.get("group") is not None}),
        "window_start_ms": ws,
        "window_end_ms": we,
        "window_start_utc": _iso_ms(ws) if ws else None,
        "window_end_utc": _iso_ms(we) if we else None,
        "generated_at_ms": int((SWEEP_DIR / "runs.jsonl").stat().st_mtime * 1000),
        "source": {
            "runs_jsonl": str(rj),
            "trades_jsonl": str(SWEEP_DIR / "trades.jsonl"),
            "report_md": str(SWEEP_DIR / "report.md"),
            "plan_json": str(SWEEP_DIR / "plan.json"),
        },
        "derived_from": "evidence/FULL_SWEEP_200x100/runs.jsonl 逐行现算（每次回测一行）",
        "note": ("本批次**不落 run 目录**：20000 个目录约 36GB，磁盘不足 ⇒ 逐次记录只在 "
                 "runs.jsonl（一行一次回测），逐笔在 trades.jsonl。本批次记录由该文件现算，"
                 "不含单 run 账本与图表。"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="登记压测批次（清单型）")
    ap.add_argument("--apply", action="store_true", help="写 batch_meta.json（默认只打印）")
    args = ap.parse_args(argv)

    meta = compute()
    print(json.dumps({k: meta[k] for k in
                      ("batch_id", "runs_total", "runs_ok", "runs_no_data", "runs_with_trades",
                       "runs_win", "runs_loss", "trades_total", "net_pnl_sum",
                       "symbols_count", "groups", "window_start_utc", "window_end_utc")},
                     ensure_ascii=False, indent=1))
    if not args.apply:
        print("\n只体检，未写盘（加 --apply 才写）")
        return 0

    bdir = BATCH_ROOT / BATCH_ID
    bdir.mkdir(parents=True, exist_ok=True)
    target = bdir / "batch_meta.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    print(f"\n已写入：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

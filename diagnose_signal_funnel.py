# -*- coding: utf-8 -*-
"""信号漏斗诊断：296 笔成交之外，信号到底死在哪一层。

只读内核，不改任何源码。输出三个东西：
  1. 状态机漏斗（箱体 → 扫低 → 回踩 → 突破评估 → 候选），含四种失败分支计数
  2. 引擎拒单原因分布（候选 → 成交之间丢掉的部分）
  3. 每标的明细 CSV + 汇总 JSON

用法：
    PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
    $PY diagnose_signal_funnel.py                  # 默认 60 个标的
    $PY diagnose_signal_funnel.py --limit 200 --workers 8
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in (str(ROOT), str(ROOT / "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)

OUT_DIR = ROOT / "evidence" / "SIGNAL_FUNNEL"
PARQUET = ROOT / "数据" / "市场数据" / "parquet"

START = "2020-09-01"
END = "2026-10-18"

# 漏斗层级顺序（正常路径）
PATH_STATES = ["BOX", "SWEEP", "RETEST", "BREAKOUT_EVAL", "CANDIDATE"]
# 失败分支
FAIL_STATES = ["VOLUME_REJECTED", "OI_REJECTED", "OI_UNAVAILABLE",
               "MISSED_WITHOUT_RETEST", "INVALIDATED", "EXPIRED"]


def list_symbols(limit: int | None) -> list[str]:
    k = PARQUET / "candles" / "binance" / "um"
    o = PARQUET / "oi" / "binance" / "um"
    if not k.exists():
        raise SystemExit("K 线目录不存在：%s" % k)
    ks = {p.name for p in k.iterdir() if p.is_dir()}
    os_ = {p.name for p in o.iterdir() if p.is_dir()} if o.exists() else ks
    syms = sorted(ks & os_)
    if limit and limit < len(syms):
        rng = random.Random(20261018)
        syms = sorted(rng.sample(syms, limit))
    return syms


def run_one(sym: str) -> dict:
    """单标的：状态机漏斗 + 回测拒单。"""
    import pandas as pd

    from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    from WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
    from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner

    t0 = time.time()
    rec = {"symbol": sym, "status": "ok", "err": "",
           "bars_15m": 0, "bars_1h": 0, "bars_4h": 0,
           "candidates": 0, "trades": 0, "net": 0.0,
           "rejects_total": 0, "elapsed_s": 0.0}
    for s in PATH_STATES + FAIL_STATES:
        rec["st_" + s] = 0
    rec["rej_reasons"] = {}

    try:
        runner = EventTrendRunner()
        dfs = runner.load_symbol_data(sym, start_date=START, end_date=END,
                                     allow_auto_window=False)
        df_4h, df_1h, df_15m, oi_ts, oi_vals, funding = dfs
        rec["bars_15m"] = len(df_15m)
        rec["bars_1h"] = len(df_1h)
        rec["bars_4h"] = len(df_4h)

        if len(df_15m) < 500 or len(df_1h) < 120:
            rec["status"] = "no_data"
            rec["elapsed_s"] = round(time.time() - t0, 2)
            return rec

        sm = runner.run_state_machine_simulation(sym, df_4h, df_1h, oi_ts, oi_vals)
        rec["candidates"] = len(sm.candidates)

        cnt = Counter(str(t.to_state) for t in sm.transitions)
        for s in PATH_STATES + FAIL_STATES:
            rec["st_" + s] = int(cnt.get(s, 0))

        cfg = EventTrendConfig(initial_equity=2000.0)
        try:
            spec = runner._resolve_spec(sym, df_15m, cfg)
        except Exception:
            spec = None

        res = run_event_trend_backtest(
            symbol=sym, df_1h=df_1h, df_15m=df_15m, candidates=sm.candidates,
            funding_events=funding, df_4h=df_4h, variant="A",
            initial_equity=float(cfg.initial_equity), config=cfg,
            instrument_spec=spec,
            price_step=(None if spec is not None else "0.000000000001"))

        er = res.get("engine_result") or {}
        trades = er.get("trades") or []
        rec["trades"] = len(trades)
        rec["net"] = round(sum(float(t.get("net_pnl", 0.0)) for t in trades), 6)

        # 注意：策略主动取消记在 rejected_orders，执行有效性错误才在 precision.rejects。
        # 样本量损耗主要来自前者，两个都要看。
        prec = er.get("precision") or {}
        exec_rejects = prec.get("rejects") or []
        strat_rejects = er.get("rejected_orders") or []
        rec["rejects_total"] = len(strat_rejects) + len(exec_rejects)
        reasons = Counter()
        for r in strat_rejects:
            if str(r.get("phase", "")).startswith("C_entry"):
                reasons[str(r.get("reason", "?"))] += 1
        for r in exec_rejects:
            if r.get("stage") == "entry":
                reasons["exec:" + str(r.get("reason", "?"))] += 1
        rec["rej_reasons"] = dict(reasons.most_common())

    except Exception as exc:  # noqa: BLE001
        rec["status"] = "error"
        rec["err"] = ("%s: %s" % (type(exc).__name__, exc))[:200]

    rec["elapsed_s"] = round(time.time() - t0, 2)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60, help="抽样标的数；0 表示全量")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    syms = list_symbols(args.limit or None)
    print("=" * 78)
    print("信号漏斗诊断 ｜ 窗口 %s ~ %s ｜ 标的 %d 个 ｜ 进程 %d"
          % (START, END, len(syms), args.workers))
    print("=" * 78, flush=True)

    t_all = time.time()
    recs: list[dict] = []
    done = 0
    with cf.ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, s): s for s in syms}
        for fut in cf.as_completed(futs):
            try:
                recs.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                recs.append({"symbol": futs[fut], "status": "crash",
                             "err": str(exc)[:200], "trades": 0,
                             "candidates": 0, "rejects_total": 0,
                             "rej_reasons": {}, "elapsed_s": 0.0,
                             **{("st_" + s): 0 for s in PATH_STATES + FAIL_STATES}})
            done += 1
            if done % 10 == 0 or done == len(syms):
                el = time.time() - t_all
                print("  %4d/%d ｜ 已用 %5.1f 分 ｜ 剩余 %5.1f 分 ｜ 成交 %d 笔"
                      % (done, len(syms), el / 60, el / done * (len(syms) - done) / 60,
                         sum(r.get("trades", 0) for r in recs)), flush=True)

    ok = [r for r in recs if r.get("status") == "ok"]
    wall = time.time() - t_all

    # ── 汇总 ──
    def tot(key: str) -> int:
        return sum(int(r.get(key, 0) or 0) for r in ok)

    all_reasons = Counter()
    for r in ok:
        all_reasons.update(r.get("rej_reasons") or {})

    print()
    print("=" * 78)
    print("【漏斗】%d 个有效标的合计" % len(ok))
    print("=" * 78)
    print("  %-22s %10s" % ("层级", "数量"))
    for s in PATH_STATES:
        print("  %-22s %10d" % (s, tot("st_" + s)))
    print("  %-22s %10d" % ("→ 引擎成交", tot("trades")))
    print()
    print("  %-22s %10s" % ("失败分支", "次数"))
    for s in FAIL_STATES:
        print("  %-22s %10d" % (s, tot("st_" + s)))
    print()
    print("  %-22s %10s" % ("引擎入场拒单", tot("rejects_total")))
    for k, v in all_reasons.most_common():
        print("    %-30s %8d" % (k, v))

    # 转化率
    box = tot("st_BOX")
    cand = tot("st_CANDIDATE")
    trades = tot("trades")
    print()
    print("=" * 78)
    print("【转化率】")
    print("=" * 78)
    if box:
        print("  箱体 → 候选      %6d / %6d = %5.2f%%" % (cand, box, 100.0 * cand / box))
    if cand:
        print("  候选 → 成交      %6d / %6d = %5.2f%%" % (trades, cand, 100.0 * trades / cand))
    if box:
        print("  箱体 → 成交      %6d / %6d = %5.3f%%" % (trades, box, 100.0 * trades / box))

    # ── 落盘 ──
    cols = ["symbol", "status", "err", "bars_4h", "bars_1h", "bars_15m",
            "candidates", "trades", "net", "rejects_total", "elapsed_s"] \
        + ["st_" + s for s in PATH_STATES + FAIL_STATES]
    csv_path = OUT_DIR / "funnel_by_symbol.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        f.write(",".join(cols + ["rej_reasons"]) + "\n")
        for r in sorted(recs, key=lambda x: x.get("symbol", "")):
            row = []
            for c in cols:
                v = r.get(c, "")
                row.append('"%s"' % v if isinstance(v, str) else str(v))
            row.append('"%s"' % json.dumps(r.get("rej_reasons") or {}, ensure_ascii=False))
            f.write(",".join(row) + "\n")

    summary = {
        "window": [START, END],
        "symbols_requested": len(syms),
        "symbols_ok": len(ok),
        "wall_seconds": round(wall, 1),
        "funnel": {s: tot("st_" + s) for s in PATH_STATES + FAIL_STATES},
        "engine_trades": trades,
        "engine_rejects_total": tot("rejects_total"),
        "reject_reasons": dict(all_reasons.most_common()),
        "conversion": {
            "box_to_candidate_pct": round(100.0 * cand / box, 4) if box else None,
            "candidate_to_trade_pct": round(100.0 * trades / cand, 4) if cand else None,
            "box_to_trade_pct": round(100.0 * trades / box, 5) if box else None,
        },
        "trades_per_symbol_per_year": round(
            trades / max(1, len(ok)) / 6.13, 4),
    }
    (OUT_DIR / "funnel_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    print()
    print("明细 → %s" % csv_path.name)
    print("汇总 → funnel_summary.json")
    print("用时 %.1f 分" % (wall / 60))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

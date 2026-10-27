# -*- coding: utf-8 -*-
"""信号密度扫描：放宽哪个条件能把样本提上去、质量掉多少。

背景
────
漏斗诊断（diagnose_signal_funnel.py）发现：60 个标的、6.13 年，
7981 个箱体只有 23 个变成候选、18 笔成交，转化率 0.226%。
放量拒 9 次、持仓量拒 13 次，加起来 22 次 —— 不是主因。
主因是「回踩之后等不到突破」：1144 个回踩只触发 46 次突破评估，超时作废 3624 次。

本脚本做的事
────────────
单变量扫描：每次只放宽一个条件，其余保持基线，看
  ① 候选数和成交数涨多少（样本够不够）
  ② 每笔期望和胜率掉多少（质量损失）

不改任何内核源码：状态机驱动逻辑在下面 drive_sm() 里复刻自
runner.run_state_machine_simulation，唯一区别是 config 可传入。
撮合配置在所有变体里完全一致（费率、滑点、风控、出场规则都不动）。

用法：
    PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
    $PY scan_signal_density.py --limit 40 --workers 8
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import dataclasses
import json
import random
import statistics as st
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in (str(ROOT), str(ROOT / "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)

OUT_DIR = ROOT / "evidence" / "SIGNAL_DENSITY"
PARQUET = ROOT / "数据" / "市场数据" / "parquet"

START = "2020-09-01"
END = "2026-10-18"

# ── 变体表：只列与基线不同的字段 ──
#    键 = 变体名，值 = 覆盖到 EventTrendConfig 的字段
VARIANTS: dict[str, dict] = {
    "基线": {},
    "时间窗翻倍": {
        "retest_max_hours": 144,
        "breakout_max_hours_after_retest": 336,
        "breakout_max_total_hours": 672,
    },
    "箱体宽度放宽": {
        "box_min_width_atr": 1.5,
        "box_max_width_atr": 10.0,
    },
    "扫低窗口翻倍": {"sweep_max_hours": 336},
    "趋势度门槛降": {"er_threshold": 0.20},
    "放量门槛降": {"breakout_vol_multiplier": 1.20},
    "持仓量门槛降": {"oi_min_growth_pct": 0.01},
    "冷却期取消": {"cooling_hours": 0},
}

# 不参与撮合、只影响信号生成的字段（扫描时这些可变，其余必须一致）
SIGNAL_FIELDS = {
    "box_bars_4h", "er_threshold", "box_min_width_atr", "box_max_width_atr",
    "sweep_max_hours", "sweep_max_overshoot_atr", "sweep_min_recovery_atr",
    "retest_max_hours", "retest_min_zone_atr", "retest_max_zone_atr",
    "retest_recovery_atr", "retest_vol_multiplier", "retest_fail_buffer_atr",
    "breakout_max_hours_after_retest", "breakout_max_total_hours",
    "breakout_buffer_atr", "breakout_vol_lookback", "breakout_vol_multiplier",
    "oi_lookback_hours", "oi_min_growth_pct", "oi_max_time_distance_sec",
    "cooling_hours", "entry_delay_15m_bars", "max_gap_up_atr",
    "initial_stop_buffer_atr",
}


def list_symbols(limit: int | None) -> list[str]:
    k = PARQUET / "candles" / "binance" / "um"
    o = PARQUET / "oi" / "binance" / "um"
    ks = {p.name for p in k.iterdir() if p.is_dir()}
    os_ = {p.name for p in o.iterdir() if p.is_dir()} if o.exists() else ks
    syms = sorted(ks & os_)
    if limit and limit < len(syms):
        syms = sorted(random.Random(20261018).sample(syms, limit))
    return syms


def drive_sm(sym: str, df_4h, df_1h, oi_ts, oi_vals, cfg, enable_oi_filter: bool = True):
    """复刻 runner.run_state_machine_simulation 的驱动逻辑，唯一差别是 config 可传入。

    逐行对照 runner.py:308-350，不增加也不减少任何一步：
    每小时喂一根 1h bar；每逢 4h 闭合点、且历史够 42 根时喂一次 42 根窗口 + ATR。
    """
    import numpy as np

    from WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine

    sm = EventStateMachine(config=cfg, enable_oi_filter=enable_oi_filter)
    sm.symbol = sym

    for r in df_1h.to_dict("records"):
        t_open = int(r["timestamp"])
        t_close = t_open + 3600 * 1000

        if t_close % (4 * 3600 * 1000) == 0:
            sub_4h = df_4h[df_4h["close_time"] <= t_close]
            if len(sub_4h) >= 42:
                last_42 = sub_4h.tail(42)
                sm.check_and_form_box(
                    bar_4h_close_ms=t_close,
                    history_42_highs=last_42["high"].to_numpy(np.float64),
                    history_42_lows=last_42["low"].to_numpy(np.float64),
                    history_42_closes=last_42["close"].to_numpy(np.float64),
                    current_atr_a=float(sub_4h["atr"].iloc[-1]),
                )

        sm.process_1h_bar(
            open_time_ms=t_open,
            open_p=float(r["open"]), high_p=float(r["high"]),
            low_p=float(r["low"]), close_p=float(r["close"]),
            volume_v=float(r["volume"]),
            oi_timestamps=oi_ts, oi_values=oi_vals,
        )
    return sm


def run_one(task: tuple[str, str, dict]) -> dict:
    """单标的 × 单变体。"""
    sym, vname, overrides = task

    from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    from WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
    from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner

    t0 = time.time()
    rec = {"variant": vname, "symbol": sym, "status": "ok", "err": "",
           "boxes": 0, "sweeps": 0, "retests": 0, "candidates": 0, "trades": 0,
           "wins": 0, "net": 0.0, "elapsed_s": 0.0}

    try:
        base = EventTrendConfig(initial_equity=2000.0)
        # 安全闸门：变体只允许改信号字段，撮合/风控字段必须与基线一致
        for k in overrides:
            if k not in SIGNAL_FIELDS:
                raise ValueError("变体 %s 试图改非信号字段 %s" % (vname, k))
        cfg = dataclasses.replace(base, **overrides)

        runner = EventTrendRunner()
        dfs = runner.load_symbol_data(sym, start_date=START, end_date=END,
                                     allow_auto_window=False)
        df_4h, df_1h, df_15m, oi_ts, oi_vals, funding = dfs
        if len(df_15m) < 500 or len(df_1h) < 120:
            rec["status"] = "no_data"
            rec["elapsed_s"] = round(time.time() - t0, 2)
            return rec

        sm = drive_sm(sym, df_4h, df_1h, oi_ts, oi_vals, cfg)
        cnt = Counter(str(t.to_state) for t in sm.transitions)
        rec["boxes"] = int(cnt.get("BOX", 0))
        rec["sweeps"] = int(cnt.get("SWEEP", 0))
        rec["retests"] = int(cnt.get("RETEST", 0))
        rec["candidates"] = len(sm.candidates)

        try:
            spec = runner._resolve_spec(sym, df_15m, base)
        except Exception:
            spec = None

        res = run_event_trend_backtest(
            symbol=sym, df_1h=df_1h, df_15m=df_15m, candidates=sm.candidates,
            funding_events=funding, df_4h=df_4h, variant="A",
            initial_equity=float(cfg.initial_equity), config=cfg,
            instrument_spec=spec,
            price_step=(None if spec is not None else "0.000000000001"),
        )
        trades = (res.get("engine_result") or {}).get("trades") or []
        rec["trades"] = len(trades)
        rec["wins"] = sum(1 for t in trades if float(t.get("net_pnl", 0.0)) > 0)
        rec["net"] = round(sum(float(t.get("net_pnl", 0.0)) for t in trades), 6)

    except Exception as exc:  # noqa: BLE001
        rec["status"] = "error"
        rec["err"] = ("%s: %s" % (type(exc).__name__, exc))[:200]

    rec["elapsed_s"] = round(time.time() - t0, 2)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--variants", default="", help="逗号分隔；留空=全部")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    syms = list_symbols(args.limit or None)
    names = [v.strip() for v in args.variants.split(",") if v.strip()] or list(VARIANTS)
    bad = [n for n in names if n not in VARIANTS]
    if bad:
        raise SystemExit("未知变体：%s（可选：%s）" % (bad, list(VARIANTS)))

    tasks = [(s, n, VARIANTS[n]) for n in names for s in syms]
    print("=" * 82)
    print("信号密度扫描 ｜ 窗口 %s ~ %s ｜ 标的 %d ｜ 变体 %d ｜ 任务 %d ｜ 进程 %d"
          % (START, END, len(syms), len(names), len(tasks), args.workers))
    print("=" * 82, flush=True)

    t_all = time.time()
    recs: list[dict] = []
    done = 0
    with cf.ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(run_one, t) for t in tasks]
        for fut in cf.as_completed(futs):
            try:
                recs.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                recs.append({"variant": "?", "symbol": "?", "status": "crash",
                             "err": str(exc)[:200], "trades": 0, "candidates": 0,
                             "boxes": 0, "sweeps": 0, "retests": 0,
                             "wins": 0, "net": 0.0, "elapsed_s": 0.0})
            done += 1
            if done % 40 == 0 or done == len(tasks):
                el = time.time() - t_all
                print("  %4d/%d ｜ 已用 %5.1f 分 ｜ 剩余 %5.1f 分"
                      % (done, len(tasks), el / 60, el / done * (len(tasks) - done) / 60),
                      flush=True)

    # ── 汇总 ──
    print()
    print("=" * 82)
    print("【结果】每个变体：样本量 vs 质量")
    print("=" * 82)
    hdr = ("%-14s %6s %6s %7s %7s %7s %7s %9s %9s"
           % ("变体", "标的", "箱体", "候选", "成交", "胜率", "每笔", "总净", "候选/箱体"))
    print(hdr)
    print("-" * 82)

    rows = []
    for n in names:
        rs = [r for r in recs if r["variant"] == n and r["status"] == "ok"]
        if not rs:
            print("%-14s 无有效样本" % n)
            continue
        boxes = sum(r["boxes"] for r in rs)
        cands = sum(r["candidates"] for r in rs)
        trades = sum(r["trades"] for r in rs)
        wins = sum(r["wins"] for r in rs)
        net = sum(r["net"] for r in rs)
        per = net / trades if trades else 0.0
        wr = 100.0 * wins / trades if trades else 0.0
        conv = 100.0 * cands / boxes if boxes else 0.0
        rows.append({"variant": n, "symbols": len(rs), "boxes": boxes,
                     "candidates": cands, "trades": trades,
                     "win_rate": round(wr, 2), "per_trade": round(per, 4),
                     "net": round(net, 2),
                     "cand_per_box_pct": round(conv, 4),
                     "trades_per_symbol_year": round(
                         trades / max(1, len(rs)) / 6.13, 3)})
        print("%-14s %6d %6d %7d %7d %6.1f%% %+7.3f %+9.2f %8.3f%%"
              % (n, len(rs), boxes, cands, trades, wr, per, net, conv))

    (OUT_DIR / "scan_summary.json").write_text(
        json.dumps({"window": [START, END], "variants": rows,
                    "wall_seconds": round(time.time() - t_all, 1)},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    # 每变体逐标的明细
    cols = ["variant", "symbol", "status", "boxes", "sweeps", "retests",
            "candidates", "trades", "wins", "net", "elapsed_s", "err"]
    with (OUT_DIR / "scan_by_symbol.csv").open("w", encoding="utf-8-sig", newline="") as f:
        f.write(",".join(cols) + "\n")
        for r in sorted(recs, key=lambda x: (x.get("variant", ""), x.get("symbol", ""))):
            out = []
            for c in cols:
                v = r.get(c, "")
                out.append('"%s"' % v if isinstance(v, str) else str(v))
            f.write(",".join(out) + "\n")

    print()
    print("汇总 → %s" % (OUT_DIR / "scan_summary.json").name)
    print("明细 → %s" % (OUT_DIR / "scan_by_symbol.csv").name)
    print("用时 %.1f 分" % ((time.time() - t_all) / 60))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

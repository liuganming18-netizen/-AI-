#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取「唯一交易」的几何结构，并做反事实路径模拟。

为什么需要这个脚本：
    200 组 × 365 天窗口铺在 2021-2026 共 1750 天里，相邻两组起点只差 8~9 天，
    窗口重叠约 97%。同一笔交易会在几十个窗口里被反复记录，2604 行逐笔里
    只有 296 笔是真正不同的交易。直接对 2604 行做统计会把样本量虚增 8.8 倍，
    把「不显著」误判成「显著为负」。

本脚本做三件事，全部只读内核、不改内核：
    1. 从 trades.jsonl 去重出唯一交易，每个唯一交易只挑一个能复现它的窗口
    2. 重跑这 296 个回测，捞出 mfe（最大浮盈）、mae（最大浮亏）、
       stop_trace（初始止损价）、滑点与取整损失
    3. 用 15 分钟 K 线做路径模拟：换成「固定止盈 + 收紧止损」后，
       逐笔期望会变成多少（含 5bps 手续费 + 3bps 滑点）

用法：
    PY analyze_edge_geometry.py
    PY analyze_edge_geometry.py --workers 6
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "策略"))

OUT_DIR = ROOT / "evidence" / "FULL_SWEEP_200x100"
RUNS_JSONL = OUT_DIR / "runs.jsonl"
TRADES_JSONL = OUT_DIR / "trades.jsonl"
RICH_JSONL = OUT_DIR / "trades_rich.jsonl"
SUMMARY_JSON = OUT_DIR / "edge_geometry.json"
PATH_JSON = OUT_DIR / "exit_rule_counterfactual.json"

FEE_BPS = 5.0        # 与 EventTrendConfig.taker_fee_bps 一致
SLIP_BPS = 3.0       # 与 EventTrendConfig.slippage_bps 一致
MAX_HOLD_DAYS = 10   # 与 EventTrendConfig.max_holding_days 一致

_RUNNER = None


def trade_key(t: dict) -> tuple:
    return (t["symbol"], int(t["t_in"]), int(t["t_out"]),
            round(float(t["p_in"]), 12), round(float(t["net"]), 6))


def _init_worker():
    global _RUNNER
    from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
    from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    _RUNNER = (EventTrendRunner(), EventTrendConfig(initial_equity=2000.0))


def rerun(task: dict) -> dict:
    """复现一次回测，返回该次回测的全部逐笔细节。"""
    runner, cfg = _RUNNER
    out = {"group": task["group"], "symbol": task["symbol"], "status": "ok",
           "err": "", "trades": []}
    try:
        dfs = runner.load_symbol_data(task["symbol"], start_date=task["start"],
                                      end_date=task["end"], allow_auto_window=False)
        res = runner.run_variant_abc(
            symbol=task["symbol"], df_4h=dfs[0], df_1h=dfs[1], df_15m=dfs[2],
            oi_ts=dfs[3], oi_vals=dfs[4], funding_events=dfs[5],
            cfg=cfg, initial_equity=float(cfg.initial_equity),
            create_variant_runs=False)
        for t in (res["res_a"]["engine_result"].get("trades") or []):
            ep = float(t.get("entry_price") or 0.0)
            stp = t.get("stop_trace") or []
            stop_px = float(stp[0]["stop"]) if stp else None
            r_abs = (ep - stop_px) if (stop_px is not None and ep > 0) else None
            out["trades"].append({
                "group": task["group"], "symbol": task["symbol"],
                "t_in": int(t.get("entry_time") or 0),
                "t_out": int(t.get("exit_time") or 0),
                "p_in": ep, "p_out": float(t.get("exit_price") or 0.0),
                "qty": float(t.get("qty") or 0.0),
                "stop_px": stop_px, "r_abs": r_abs,
                "r_pct": (r_abs / ep) if (r_abs is not None and ep > 0) else None,
                "mfe": t.get("mfe"), "mae": t.get("mae"),
                "gross": float(t.get("gross_pnl") or 0.0),
                "fees": float(t.get("fees") or 0.0),
                "funding": float(t.get("funding_fee") or 0.0),
                "slip": float(t.get("slippage_loss") or 0.0),
                "rounding": float(t.get("rounding_loss") or 0.0),
                "net": float(t.get("net_pnl") or 0.0),
                "why": str(t.get("exit_reason") or ""),
                "n_stop_updates": len(stp),
            })
    except Exception as exc:  # noqa: BLE001
        out["status"] = "error"
        out["err"] = ("%s: %s" % (type(exc).__name__, str(exc)))[:200]
    return out


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(len(v) * p)))]


def load_unique_tasks() -> tuple[list[dict], int, int]:
    """去重出唯一交易，每个唯一交易绑定一个能复现它的回测任务。"""
    if not TRADES_JSONL.exists():
        raise SystemExit("缺 %s" % TRADES_JSONL)
    runs = [json.loads(l) for l in RUNS_JSONL.open(encoding="utf-8")]
    run_of = {(r["group"], r["symbol"]): r for r in runs if r.get("status") == "ok"}
    trs = [json.loads(l) for l in TRADES_JSONL.open(encoding="utf-8")]

    picked: dict[tuple, dict] = {}
    for t in trs:
        k = trade_key(t)
        if k in picked:
            continue
        r = run_of.get((t["group"], t["symbol"]))
        if r is None:
            continue
        picked[k] = {"group": r["group"], "symbol": r["symbol"],
                     "start": r["start"], "end": r["end"]}
    return list(picked.values()), len(trs), len(picked)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    tasks, n_raw, n_uniq = load_unique_tasks()
    print("逐笔行数 %d ｜ 唯一交易 %d ｜ 重复倍数 %.2fx" % (n_raw, n_uniq, n_raw / max(1, n_uniq)))
    print("需要重跑的回测 %d 个（每个唯一交易只取一个窗口）" % len(tasks), flush=True)

    t0 = time.time()
    got: dict[tuple, dict] = {}
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker) as ex:
        futs = [ex.submit(rerun, t) for t in tasks]
        for fut in as_completed(futs):
            r = fut.result()
            for t in r["trades"]:
                got.setdefault(trade_key(t), t)
            done += 1
            if done % 60 == 0 or done == len(tasks):
                print("  %4d/%d ｜ %.1f 分 ｜ 已取 %d 笔" %
                      (done, len(tasks), (time.time() - t0) / 60.0, len(got)), flush=True)

    rows = list(got.values())
    with RICH_JSONL.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("唯一交易明细 %d 笔 → %s" % (len(rows), RICH_JSONL.name))

    report(rows, n_raw)
    counterfactual(rows)
    return 0


def report(rows: list[dict], n_raw: int) -> None:
    n = len(rows)
    net = [r["net"] for r in rows]
    m, sd = st.mean(net), st.stdev(net)
    se = sd / math.sqrt(n)

    print("\n" + "=" * 78)
    print("【一】唯一交易的净盈亏（这才是真实样本）")
    print("=" * 78)
    print("  笔数 %d（原始 %d 行，重复 %.1fx）" % (n, n_raw, n_raw / n))
    print("  胜率 %.1f%% ｜ 净合计 %+.2f U ｜ 每笔期望 %+.4f U" %
          (sum(1 for x in net if x > 0) / n * 100, sum(net), m))
    print("  t 值 %.2f ｜ 95%% 置信区间 [%+.3f, %+.3f]" % (m / se, m - 1.96 * se, m + 1.96 * se))
    print("  判定：%s" % ("显著为负" if m / se < -1.96 else
                        ("显著为正" if m / se > 1.96 else "与 0 无法区分（样本不足以证明有优势，也不足以证明没有）")))

    rp = [r["r_pct"] * 100 for r in rows if r["r_pct"]]
    mfe = [r["mfe"] * 100 for r in rows if r["mfe"] is not None]
    mae = [r["mae"] * 100 for r in rows if r["mae"] is not None]

    print("\n" + "=" * 78)
    print("【二】止损距离（入场价 → 初始止损，占入场价百分比）")
    print("=" * 78)
    print("  中位 %.2f%% ｜ 均值 %.2f%% ｜ p10 %.2f%% ｜ p25 %.2f%% ｜ p75 %.2f%% ｜ p90 %.2f%%"
          % (q(rp, .5), st.mean(rp), q(rp, .1), q(rp, .25), q(rp, .75), q(rp, .9)))
    for thr in (5, 10, 15, 20):
        c = sum(1 for x in rp if x >= thr)
        print("  ≥%2d%% 的笔数：%3d / %d = %.1f%%" % (thr, c, len(rp), c / len(rp) * 100))

    print("\n" + "=" * 78)
    print("【三】最大浮盈 / 最大浮亏（持仓期内，按入场价）")
    print("=" * 78)
    print("  MFE 中位 %+6.2f%% ｜ p75 %+6.2f%% ｜ p90 %+6.2f%% ｜ p99 %+6.2f%%"
          % (q(mfe, .5), q(mfe, .75), q(mfe, .9), q(mfe, .99)))
    print("  MAE 中位 %+6.2f%% ｜ p10 %+6.2f%% ｜ p25 %+6.2f%% ｜ p90 %+6.2f%%"
          % (q(mae, .5), q(mae, .1), q(mae, .25), q(mae, .9)))

    print("\n" + "=" * 78)
    print("【四】以 R（止损距离）为单位看，这策略到底长什么样")
    print("=" * 78)
    hit1 = sum(1 for r in rows if r["r_pct"] and r["mfe"] is not None
               and r["r_pct"] > 0 and r["mfe"] >= r["r_pct"])
    hit2 = sum(1 for r in rows if r["r_pct"] and r["mfe"] is not None
               and r["r_pct"] > 0 and r["mfe"] >= 2 * r["r_pct"])
    tot = sum(1 for r in rows if r["r_pct"] and r["r_pct"] > 0)
    print("  浮盈到过 1R 的笔数：%3d / %d = %.1f%%" % (hit1, tot, hit1 / tot * 100))
    print("  浮盈到过 2R 的笔数：%3d / %d = %.1f%%  ← 移动止损触发线在 2R" % (hit2, tot, hit2 / tot * 100))
    print("  >> 结论：移动止损写在 2R，但价格几乎到不了 2R，等于没装这个功能")

    print("\n" + "=" * 78)
    print("【五】按出场原因（唯一交易）")
    print("=" * 78)
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["why"], []).append(r)
    for k, v in sorted(by.items(), key=lambda x: -len(x[1])):
        w = sum(1 for x in v if x["net"] > 0)
        print("  %-22s %3d 笔 (%4.1f%%) 胜率%5.1f%% 净%+8.2f 每笔%+7.2f 持有%.1f天" % (
            k, len(v), len(v) / n * 100, w / len(v) * 100,
            sum(x["net"] for x in v), sum(x["net"] for x in v) / len(v),
            st.mean((x["t_out"] - x["t_in"]) / 86400000.0 for x in v)))

    SUMMARY_JSON.write_text(json.dumps({
        "n_raw_rows": n_raw, "n_unique": n,
        "win_rate": sum(1 for x in net if x > 0) / n * 100,
        "net_sum": sum(net), "mean": m, "t_stat": m / se,
        "ci95": [m - 1.96 * se, m + 1.96 * se],
        "r_pct_median": q(rp, .5), "r_pct_p90": q(rp, .9),
        "mfe_median": q(mfe, .5), "mfe_p90": q(mfe, .9),
        "mae_median": q(mae, .5), "mae_p10": q(mae, .1),
        "hit_1r_pct": hit1 / tot * 100, "hit_2r_pct": hit2 / tot * 100,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n汇总 → %s" % SUMMARY_JSON.name)


# ---------------------------------------------------------------- 反事实路径模拟
def counterfactual(rows: list[dict]) -> None:
    """用 15m K 线逐笔走一遍，换不同的止盈/止损规则看期望值。

    规则：入场用实际成交价（含滑点取整），从入场那根 15m 起逐根走，
    同一根内先判止损再判止盈（保守，与引擎 intrabar_stop 口径一致）。
    手续费按 5bps 双边 + 3bps 滑点双边扣。
    """
    import duckdb
    import numpy as np
    import pandas as pd

    parquet = ROOT / "数据/市场数据/parquet/candles/binance/um"
    con = duckdb.connect()
    cost = (FEE_BPS + SLIP_BPS) * 2 / 10000.0   # 双边合计，按名义金额

    # 规则集合：(名称, 止损系数, 止盈系数)；系数均以原始 R 为单位
    rules = [
        ("原样（止损1R、无止盈、10天到期）", 1.00, None),
        ("止损1R + 止盈0.5R", 1.00, 0.5),
        ("止损1R + 止盈1R", 1.00, 1.0),
        ("止损1R + 止盈1.5R", 1.00, 1.5),
        ("止损1R + 止盈2R", 1.00, 2.0),
        ("止损收紧到0.5R + 无止盈", 0.50, None),
        ("止损收紧到0.5R + 止盈1R", 0.50, 1.0),
        ("止损收紧到0.5R + 止盈1.5R", 0.50, 1.5),
        ("止损收紧到0.75R + 止盈1R", 0.75, 1.0),
        ("止损收紧到0.75R + 止盈1.5R", 0.75, 1.5),
    ]
    stats = {name: {"pnl": [], "why": []} for name, _, _ in rules}

    by_sym: dict[str, list] = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)

    skipped = 0
    for sym, ts in by_sym.items():
        lo = min(t["t_in"] for t in ts) - 86400000
        hi = max(t["t_in"] for t in ts) + MAX_HOLD_DAYS * 86400000 + 2 * 86400000
        try:
            df = con.execute(
                "SELECT open_time, high, low, close FROM read_parquet(?) "
                "WHERE open_time >= ? AND open_time <= ? ORDER BY open_time",
                [str(parquet / sym / "15m" / "*.parquet"), lo, hi]).fetchdf()
        except Exception:  # noqa: BLE001
            skipped += len(ts)
            continue
        if df.empty:
            skipped += len(ts)
            continue
        ot = df["open_time"].to_numpy(np.int64)
        hi_a = df["high"].to_numpy(float)
        lo_a = df["low"].to_numpy(float)

        for t in ts:
            if not t["r_pct"] or t["r_pct"] <= 0 or t["qty"] <= 0:
                skipped += 1
                continue
            ep = t["p_in"]
            r_abs = t["r_abs"]
            i0 = int(np.searchsorted(ot, t["t_in"], side="left"))
            i1 = int(np.searchsorted(ot, t["t_in"] + MAX_HOLD_DAYS * 86400000, side="right"))
            if i0 >= len(ot):
                skipped += 1
                continue
            i1 = min(i1, len(ot))
            qty = t["qty"]

            for name, k_sl, k_tp in rules:
                sl = ep - k_sl * r_abs
                tp = (ep + k_tp * r_abs) if k_tp else None
                exit_px, why = None, "timeout"
                for j in range(i0, i1):
                    if lo_a[j] <= sl:                      # 先判止损
                        exit_px, why = sl, "stop"
                        break
                    if tp is not None and hi_a[j] >= tp:    # 再判止盈
                        exit_px, why = tp, "tp"
                        break
                if exit_px is None:
                    if i1 - 1 < len(ot):
                        exit_px = float(df["close"].to_numpy(float)[i1 - 1])
                    else:
                        skipped += 1
                        continue
                gross = (exit_px - ep) * qty
                net = gross - ep * qty * cost
                stats[name]["pnl"].append(net)
                stats[name]["why"].append(why)

    print("\n" + "=" * 78)
    print("【六】反事实：换止盈/止损规则后，逐笔期望会变成多少")
    print("=" * 78)
    print("  按 15 分钟 K 线逐根走，同根内先判止损再判止盈（保守）")
    print("  成本：手续费 5bps + 滑点 3bps，双边合计 %.2f%%\n" % (cost * 100))
    print("  %-34s %5s %8s %9s %9s %9s" % ("规则", "笔数", "胜率", "每笔均值", "中位", "合计"))
    out = {}
    for name, _, _ in rules:
        p = stats[name]["pnl"]
        if not p:
            continue
        w = sum(1 for x in p if x > 0)
        out[name] = {"n": len(p), "win_rate": w / len(p) * 100,
                     "mean": st.mean(p), "median": st.median(p), "sum": sum(p)}
        print("  %-34s %5d %7.1f%% %+9.3f %+9.3f %+9.1f" % (
            name, len(p), w / len(p) * 100, st.mean(p), st.median(p), sum(p)))
    if skipped:
        print("\n  跳过 %d 笔（缺 K 线或无有效止损距离）" % skipped)
    PATH_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n反事实明细 → %s" % PATH_JSON.name)


if __name__ == "__main__":
    raise SystemExit(main())

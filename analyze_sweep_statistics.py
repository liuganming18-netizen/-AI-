#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在唯一交易上做三件事：利润集中度、自助法区间、最长持有天数扫描。

输入只依赖 `trades_rich.jsonl`（由 analyze_edge_geometry.py 产出），
不重跑回测，约 1 分钟。所有数字落 `statistics.json`，供报告引用。

为什么单独一个脚本：
    结论里「利润来自 5 笔」「自助法区间含 0」「60 天持有均值 +4.31」
    这些数如果是终端里临时算的，下次没人能复现。必须落文件。
"""
from __future__ import annotations

import json
import math
import random
import statistics as st
from pathlib import Path

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "evidence" / "FULL_SWEEP_200x100"
RICH = OUT_DIR / "trades_rich.jsonl"
STAT_JSON = OUT_DIR / "statistics.json"
HOLD_JSON = OUT_DIR / "holding_period_sweep.json"

PARQUET = ROOT / "数据/市场数据/parquet/candles/binance/um"
COST = (5.0 + 3.0) * 2 / 10000.0     # 手续费 5bps + 滑点 3bps，双边
HOLDS = (5, 10, 15, 20, 30, 45, 60)
SEED = 20261018


def load() -> list[dict]:
    rows = [json.loads(l) for l in RICH.open(encoding="utf-8")]
    return [r for r in rows if r.get("r_pct") and r["r_pct"] > 0 and r["qty"] > 0]


def bootstrap_mean(xs: list[float], iters: int = 20000) -> dict:
    random.seed(SEED)
    n = len(xs)
    ms = []
    for _ in range(iters):
        ms.append(sum(xs[random.randrange(n)] for _ in range(n)) / n)
    ms.sort()
    return {"lo": ms[int(iters * 0.025)], "hi": ms[int(iters * 0.975)],
            "p_positive": sum(1 for x in ms if x > 0) / iters * 100}


def concentration(xs: list[float]) -> dict:
    s = sorted(xs)
    return {
        "n": len(s), "total": sum(s),
        "top1": s[-1], "top3": sum(s[-3:]), "top5": sum(s[-5:]), "top10": sum(s[-10:]),
        "ex_top5": sum(s[:-5]), "ex_top10": sum(s[:-10]),
    }


def holding_sweep(rows: list[dict]) -> dict:
    """止损不变、无止盈，只改最长持有天数。用 15m K 线逐根走。"""
    con = duckdb.connect()
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["symbol"], []).append(r)

    out: dict[int, list[float]] = {}
    for sym, ts in by.items():
        lo = min(t["t_in"] for t in ts) - 86400000
        hi = max(t["t_in"] for t in ts) + (max(HOLDS) + 1) * 86400000
        try:
            df = con.execute(
                "SELECT open_time, low, close FROM read_parquet(?) "
                "WHERE open_time >= ? AND open_time <= ? ORDER BY open_time",
                [str(PARQUET / sym / "15m" / "*.parquet"), lo, hi]).fetchdf()
        except Exception:  # noqa: BLE001
            continue
        if df.empty:
            continue
        ot = df["open_time"].to_numpy(np.int64)
        lo_a = df["low"].to_numpy(float)
        cl = df["close"].to_numpy(float)
        for t in ts:
            ep, qty = t["p_in"], t["qty"]
            sl = ep - t["r_abs"]
            i0 = int(np.searchsorted(ot, t["t_in"], side="left"))
            if i0 >= len(ot):
                continue
            for h in HOLDS:
                i1 = min(int(np.searchsorted(ot, t["t_in"] + h * 86400000, side="right")), len(ot))
                px = None
                for j in range(i0, i1):
                    if lo_a[j] <= sl:
                        px = sl
                        break
                if px is None:
                    px = float(cl[i1 - 1]) if i1 - 1 < len(cl) else None
                if px is None:
                    continue
                out.setdefault(h, []).append((px - ep) * qty - ep * qty * COST)

    res = {}
    for h, p in sorted(out.items()):
        m = st.mean(p)
        sd = st.stdev(p) if len(p) > 1 else 0.0
        se = sd / math.sqrt(len(p)) if len(p) > 1 else 0.0
        s = sorted(p)
        res[str(h)] = {
            "n": len(p), "win_rate": sum(1 for x in p if x > 0) / len(p) * 100,
            "mean": m, "median": st.median(p), "total": sum(p),
            "t_stat": (m / se) if se else 0.0,
            "top5": sum(s[-5:]), "ex_top5": sum(s[:-5]),
            "boot": bootstrap_mean(p, 8000),
        }
    return res


def main() -> int:
    rows = load()
    net = [r["net"] for r in rows]
    m, sd = st.mean(net), st.stdev(net)
    se = sd / math.sqrt(len(net))
    boot = bootstrap_mean(net)

    d = [((r["mfe"] or 0.0) - (r["mae"] or 0.0)) * 100 for r in rows
         if r["mfe"] is not None and r["mae"] is not None]
    dm = st.mean(d)
    dse = st.stdev(d) / math.sqrt(len(d))

    print("=" * 78)
    print("【一】净盈亏：均值、显著性、自助法")
    print("=" * 78)
    print("  笔数 %d ｜ 胜率 %.1f%% ｜ 合计 %+.2f U ｜ 每笔 %+.4f U"
          % (len(net), sum(1 for x in net if x > 0) / len(net) * 100, sum(net), m))
    print("  t = %.2f ｜ 95%%CI [%+.3f, %+.3f]" % (m / se, m - 1.96 * se, m + 1.96 * se))
    print("  自助法 95%%CI [%+.3f, %+.3f] ｜ P(期望>0) = %.0f%%"
          % (boot["lo"], boot["hi"], boot["p_positive"]))

    print("\n" + "=" * 78)
    print("【二】利润集中度")
    print("=" * 78)
    c = concentration(net)
    print("  合计 %+.2f ｜ 最好 1 笔 %+.2f ｜ 前 3 笔 %+.2f ｜ 前 5 笔 %+.2f ｜ 前 10 笔 %+.2f"
          % (c["total"], c["top1"], c["top3"], c["top5"], c["top10"]))
    print("  剔除前 5 笔 %+.2f ｜ 剔除前 10 笔 %+.2f" % (c["ex_top5"], c["ex_top10"]))

    print("\n" + "=" * 78)
    print("【三】方向性配对检验（最大浮盈 - 最大浮亏，同一笔相减）")
    print("=" * 78)
    print("  均值 %+.2f 点 ｜ 中位 %+.2f 点 ｜ t = %.2f"
          % (dm, st.median(d), dm / dse))
    print("  去掉最大 10 笔后均值 %+.2f 点  ← 均值由极少数暴涨拉起"
          % st.mean(sorted(d)[:-10]))

    print("\n" + "=" * 78)
    print("【四】最长持有天数扫描（止损不变、无止盈）")
    print("=" * 78)
    hs = holding_sweep(rows)
    print("  %-8s %5s %8s %10s %10s %9s %12s %12s" % (
        "持有", "笔数", "胜率", "每笔均值", "中位", "t值", "前5笔", "剔除前5笔"))
    for h in sorted(hs, key=lambda x: int(x)):
        v = hs[h]
        print("  %-8s %5d %7.1f%% %+10.3f %+10.3f %+9.2f %+12.1f %+12.1f" % (
            "%s 天" % h, v["n"], v["win_rate"], v["mean"], v["median"],
            v["t_stat"], v["top5"], v["ex_top5"]))

    STAT_JSON.write_text(json.dumps({
        "n": len(net), "win_rate": sum(1 for x in net if x > 0) / len(net) * 100,
        "total": sum(net), "mean": m, "t_stat": m / se,
        "ci95_normal": [m - 1.96 * se, m + 1.96 * se], "bootstrap": boot,
        "concentration": c,
        "mfe_minus_mae": {"mean": dm, "median": st.median(d), "t_stat": dm / dse,
                          "ex_top10_mean": st.mean(sorted(d)[:-10])},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    HOLD_JSON.write_text(json.dumps(hs, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n汇总 → %s" % STAT_JSON.name)
    print("持有期扫描 → %s" % HOLD_JSON.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""STEP1-A：把 `回测日志/event_trend_v0_1/runs` 这个混装目录按「创建时段 × 代码代际」拆开。

背景：该目录是多个批量脚本共用同一个 runs_base 写出来的，1198 个 run 目录里
混了 6 个代码代际、394 个窗口、438 个标的。不拆开就无法作为「一个」测试对象。

只读；输出 evidence/STEP1-A/runs/runs_pool_clusters.csv
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
SRC = os.path.join(ROOT, "evidence", "STEP1-A", "runs", "step1a_runs.csv")
OUT = os.path.join(ROOT, "evidence", "STEP1-A", "runs", "runs_pool_clusters.csv")


def main() -> int:
    rows = [r for r in csv.DictReader(open(SRC, encoding="utf-8")) if r["batch"] == "runs"]
    grp = defaultdict(list)
    for r in rows:
        name = r["run_dir_name"]
        ep = name.rsplit("_", 1)[-1]
        hour = "unknown"
        if ep.isdigit() and len(ep) == 10:
            import datetime as dt
            hour = dt.datetime.fromtimestamp(int(ep), dt.timezone.utc).strftime("%Y-%m-%d %H:00Z")
        gen = (r.get("code_combined_sha256") or "")[:12]
        grp[(hour, gen)].append(r)

    out = []
    for (hour, gen), rs in sorted(grp.items()):
        wins = {(r.get("win_start_utc"), r.get("win_end_utc")) for r in rs}
        syms = {r.get("symbol") for r in rs}
        eq = {r.get("initial_equity") for r in rs}
        out.append({
            "created_hour_utc": hour,
            "code_generation": gen,
            "runs": len(rs),
            "symbols": len(syms),
            "windows": len(wins),
            "win_sample": " | ".join(f"{a}~{b}" for a, b in sorted(wins)[:3]),
            "trades": sum(int(float(r.get("trade_count") or 0)) for r in rs),
            "net_pnl": round(sum(float(r.get("net_pnl") or 0) for r in rs), 2),
            "initial_equity": ",".join(sorted({str(x) for x in eq if x})),
            "formal_pass_true": sum(1 for r in rs if r.get("formal_pass_allowed") == "True"),
            "spec_mix": " ".join(f"{k}={v}" for k, v in sorted(
                {s: sum(1 for r in rs if r.get("spec_applicability") == s)
                 for s in {r.get("spec_applicability") for r in rs}}.items())),
        })

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"wrote {OUT} ({len(out)} 簇)")
    for o in out:
        print(f"{o['created_hour_utc']} gen={o['code_generation']} runs={o['runs']:4d} "
              f"sym={o['symbols']:3d} win={o['windows']:3d} trades={o['trades']:3d} "
              f"net={o['net_pnl']:8.2f} pass={o['formal_pass_true']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Q4 · 大户/多空比指标停更（全量，无抽样）

结论：526 币 / 127,973,455 行；全局 max(ts)=2026-10-14T23:55:00Z；
      池内停更 >7 天 526/526；停更天数 min/p50 11.13、max 18.13 天。
统计时点（本单那次运行）：2026-10-26T03:03:53Z

用法：
  /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python \
    evidence/DATA-AUDIT-01-R1/queries/Q4_metrics_staleness.py

只读：duckdb 只读 parquet；不写任何文件。
说明：metrics 冷档**没有采集时刻列、也没有发布时刻列**（只有 ts 槽位），
      所以「何时可得」只能由 ts 推断，不能自证。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[3]
ARCHIVE = ROOT / "数据/市场数据/parquet"
SCOPE = ROOT / "数据/contracts/旧标的池/scope_crypto.json"


def main() -> None:
    pool = [it["symbol"] for it in json.loads(SCOPE.read_text(encoding="utf-8"))["binance_items"]]
    con = duckdb.connect()          # 内存库，不落盘
    glob = str(ARCHIVE / "metrics/*/*/*/*.parquet")

    rows = con.execute(
        f"""
        SELECT symbol, COUNT(*) AS n, MIN(ts) AS min_ts, MAX(ts) AS max_ts
        FROM read_parquet('{glob}', union_by_name = true)
        GROUP BY symbol
        """).fetchall()
    print(f"metrics 文件数（glob）: {len(list(ARCHIVE.glob('metrics/*/*/*/*.parquet')))}")
    print(f"有数据的币            : {len(rows)}")
    print(f"总行数                : {sum(r[1] for r in rows):,}")

    mx = max(r[3] for r in rows)
    mn = min(r[2] for r in rows)

    # metrics 的 ts 也是**整数毫秒**（不是 ISO 字符串），必须统一换算
    def iso(v):
        return dt.datetime.fromtimestamp(v / 1000.0, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def parse(v):
        if isinstance(v, str):
            return dt.datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        return dt.datetime.fromtimestamp(v / 1000.0, dt.timezone.utc)

    print(f"时间跨度              : {iso(mn)} → {iso(mx)}")
    print(f"全局最新时刻 max(ts)  : {iso(mx)}")

    pool_set = set(pool)
    in_pool = [r for r in rows if r[0] in pool_set]
    missing = sorted(pool_set - {r[0] for r in rows})
    print(f"池内币数              : {len(in_pool)}/{len(pool)}；池内缺数据 {len(missing)} 个 {missing}")

    # 停更天数：以「本次运行时刻」为基准（与 scripts/10 同口径），
    # 因此数值会随时间增长——所以每条结论都必须带 stats_at。
    now = dt.datetime.now(dt.timezone.utc)
    print(f"本次运行时刻 stats_at : {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    stale = sorted((now - parse(r[3])).total_seconds() / 86400.0 for r in in_pool)
    if stale:
        print(f"池内停更天数          : min {stale[0]:.2f} / p50 {stale[len(stale)//2]:.2f} "
              f"/ max {stale[-1]:.2f}")
    print(f"停更 >1 天            : {sum(1 for s in stale if s > 1)}/{len(in_pool)}")
    print(f"停更 >7 天            : {sum(1 for s in stale if s > 7)}/{len(in_pool)}")
    print()
    print("说明：以运行时刻为基准，停更天数会随时间线性增长；"
          "本单那次运行（2026-10-26T03:03:53Z）得 min/p50 11.13、max 18.13 天。")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Q5 · 资金费冷读失败（全量，526 币逐个调生产读接口）

结论：磁盘平铺文件 526/526 都在；冷读返回 0 行 526/526；同批热读合计 261,350 行。
      对照：oi 冷读 500 行、candles 冷读 500 行、funding 热读 94 行
      ⇒ 坏的是 funding 这条**冷读路径**，不是冷档本身、也不是没有数据。
统计时点（本单那次运行）：2026-10-26T02:56:12Z

用法：
  /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python \
    evidence/DATA-AUDIT-01-R1/queries/Q5_funding_cold_read.py

只读：MarketDataRepository 自带 mode=ro + PRAGMA query_only=ON；不写库、不写冷档。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOT_DB = ROOT / "数据/市场数据/db/market_v2.sqlite3"
ARCHIVE = ROOT / "数据/市场数据/parquet"
SCOPE = ROOT / "数据/contracts/旧标的池/scope_crypto.json"


def load_repo():
    mod_path = ROOT / "框架/backend/market_repository.py"
    spec = importlib.util.spec_from_file_location("market_repository_ro", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                       # 该模块只读、无副作用
    return mod.MarketDataRepository.from_archive(HOT_DB, ARCHIVE)


def main() -> None:
    pool = [it["symbol"] for it in json.loads(SCOPE.read_text(encoding="utf-8"))["binance_items"]]
    repo = load_repo()
    print(f"读接口已装载：watermark_ms={repo.watermark_ms} "
          f"retention_days={repo.retention_days} min_bars={repo.min_bars}")

    disk_has = cold_zero = 0
    cold_total = hot_total = 0
    shards_nonzero = []
    for sym in pool:
        if (ARCHIVE / "funding/binance/um" / f"{sym}.parquet").exists():
            disk_has += 1
        sh = repo._flat_shards("binance", "um", sym, "funding")
        if sh:
            shards_nonzero.append((sym, len(sh)))
        c = repo.funding("binance", "um", sym, prefer="cold", limit=500)
        h = repo.funding("binance", "um", sym, prefer="hot", limit=500)
        cold_total += len(c)
        hot_total += len(h)
        if not c:
            cold_zero += 1

    print()
    print(f"池内币数              : {len(pool)}")
    print(f"磁盘平铺文件在        : {disk_has}/{len(pool)}")
    print(f"_flat_shards 找到分片 : {len(shards_nonzero)} 个币（期望 0 ⇒ 路径口径不匹配）")
    print(f"冷读返回 0 行的币     : {cold_zero}/{len(pool)}")
    print(f"冷读合计行数          : {cold_total}")
    print(f"同批热读合计行数      : {hot_total}")
    print()
    print("对照组（证明不是冷档坏了，是这条路径坏了）：")
    # 统一窗口：2024-01-01T00:00Z ~ 2024-02-01T00:00Z（与 scripts/30 的对照口径一致）
    T0_MS, T1_MS = 1704067200000, 1706745600000
    oi_cold = repo.oi("binance", "um", "BTCUSDT", period="5m", prefer="cold",
                      start=T0_MS, end=T1_MS, limit=500)
    cd_cold = repo.candles("binance", "um", "BTCUSDT", "1h", prefer="cold",
                           start=T0_MS, end=T1_MS, limit=500)
    fd_cold = repo.funding("binance", "um", "BTCUSDT", start=T0_MS, end=T1_MS,
                           limit=500, prefer="cold")
    fd_hot = repo.funding("binance", "um", "BTCUSDT", start=T0_MS, end=T1_MS,
                          limit=500, prefer="hot")
    print(f"  oi 5m 冷读（2024-01） : {len(oi_cold)} 行")
    print(f"  candles 1h 冷读       : {len(cd_cold)} 行")
    print(f"  funding 冷读          : {len(fd_cold)} 行")
    print(f"  funding 热读          : {len(fd_hot)} 行")
    print("  ⇒ 同类冷读 oi/candles 都读得通，只有 funding 读 0 行")
    print()
    print("根因：磁盘是 funding/binance/um/<SYMBOL>.parquet（深度 3 平铺，526 个），"
          "代码按 funding/binance/um/<SYMBOL>/<年>.parquet 目录式去找（深度 4）"
          "⇒ is_dir() 为假 ⇒ 分片恒空。")


if __name__ == "__main__":
    sys.exit(main())

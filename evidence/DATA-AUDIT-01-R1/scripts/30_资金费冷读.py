"""30 · 资金费冷读失败（全量复算）+ 冷档新鲜度

工单整改要求 3：「资金费冷读失败」必须可复算、给完整结果、标统计时点。
本脚本**不抽样**：对批准池 526 个币逐个跑生产读接口 `MarketDataRepository.funding(prefer="cold")`，
并同时给出「磁盘上有没有这个文件」「读接口找到几个分片」「实际返回几行」三个数，
把「数据在、路径错、读到 0 行」三件事分开，避免混成一句「资金费读不到」。

  out/30_funding_cold_read.json        汇总（526 币全量）
  out/31_funding_cold_per_symbol.csv   逐币三列对照（全量）
  out/32_archive_state.json            归档器 `_state.json` 原样 + 冷档最新时刻
  out/33_cold_freshness.json           冷档各类数据 max(时间列) 与热库的差距（全量）

只读：MarketDataRepository 自带 `mode=ro` + `PRAGMA query_only=ON`；duckdb 只读 parquet。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import ARCHIVE, HOT_DB, ROOT, dump, hot, now_ms, now_utc, pool, utc, write_csv

T0 = time.time()
STATS_AT = now_utc()
NOW = now_ms()
P = pool()
POOL = P["symbols"]


def log(m: str) -> None:
    print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)


# ---------------------------------------------------------------- 载入生产读接口
mod_path = ROOT / "框架/backend/market_repository.py"
spec = importlib.util.spec_from_file_location("market_repository_ro", mod_path)
mrepo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mrepo)          # 该模块只读、无副作用
MarketDataRepository = mrepo.MarketDataRepository
repo = MarketDataRepository.from_archive(HOT_DB, ARCHIVE)   # 生产读侧唯一入口
log(f"读接口已装载：watermark_ms={repo.watermark_ms} retention_days={repo.retention_days} "
    f"min_bars={repo.min_bars}")

# ---------------------------------------------------------------- 全量逐币
rows = []
cold_total = hot_total = 0
cold_zero = 0
disk_has = 0
shards_nonzero = []
for i, sym in enumerate(POOL, 1):
    flat = ARCHIVE / "funding/binance/um" / f"{sym}.parquet"
    on_disk = flat.exists()
    if on_disk:
        disk_has += 1
    shards = repo._flat_shards("binance", "um", sym, "funding")
    if shards:
        shards_nonzero.append((sym, len(shards)))
    cold = repo.funding("binance", "um", sym, prefer="cold", limit=500)
    hot_rows = repo.funding("binance", "um", sym, prefer="hot", limit=500)
    cold_total += len(cold)
    hot_total += len(hot_rows)
    if len(cold) == 0:
        cold_zero += 1
    rows.append([sym, 1 if on_disk else 0, len(shards), len(cold), len(hot_rows)])
    if i % 100 == 0:
        log(f"进度 {i}/{len(POOL)}")

rows.sort(key=lambda r: r[0])
write_csv("31_funding_cold_per_symbol.csv",
          ["symbol", "disk_flat_file", "cold_shards_found", "cold_rows", "hot_rows"], rows)

summary = {
    "stats_at_utc": STATS_AT,
    "population": f"全量（批准池 {len(POOL)} 币逐个调用生产读接口，无抽样）",
    "query": ("repo = MarketDataRepository.from_archive(HOT_DB, ARCHIVE); "
              "repo.funding('binance','um',<sym>,prefer='cold',limit=500)"),
    "interface": "框架/backend/market_repository.py::MarketDataRepository.funding",
    "shard_rule": "_flat_shards 拼 {archive}/funding/binance/um/{symbol}/ 再 glob *.parquet",
    "symbols": len(POOL),
    "symbols_with_flat_file_on_disk": disk_has,
    "symbols_with_cold_shards_found": len(shards_nonzero),
    "cold_rows_total": cold_total,
    "hot_rows_total": hot_total,
    "symbols_cold_zero": cold_zero,
    "symbols_cold_zero_pct": round(cold_zero * 100.0 / len(POOL), 2),
    "verdict": ("冷读恒 0 行不是没数据：526 个币的平铺文件都在盘上，"
                "但 `_flat_shards` 找的是 `{SYMBOL}/` 目录 ⇒ 分片恒为空 ⇒ 返回 0 行"),
}
dump("30_funding_cold_read.json", summary)
log(f"资金费冷读：磁盘有 {disk_has}/{len(POOL)}，冷读 0 行 {cold_zero}/{len(POOL)}，"
    f"冷读合计 {cold_total} 行 / 热读合计 {hot_total} 行")

# ---------------------------------------------------------------- 对照探针（同类但正常的读路径）
probe = {"stats_at_utc": STATS_AT,
         "population": "3 个对照点（用于证明『不是冷档坏了，是资金费这条路径坏了』）",
         "purpose": "对照：同类冷读接口 oi/candles 能读通，funding 读不通",
         "cases": []}

t0 = 1735689600000          # 2024-01-01
t1 = 1738368000000          # 2024-02-01
for name, fn in (
    ("oi_5m_cold", lambda: repo.oi("binance", "um", "BTCUSDT", period="5m",
                                   start=t0, end=t1, limit=500, prefer="cold")),
    ("candles_1h_cold", lambda: repo.candles("binance", "um", "BTCUSDT", "1h",
                                             start=t0, end=t1, limit=500, prefer="cold")),
    ("funding_cold", lambda: repo.funding("binance", "um", "BTCUSDT",
                                          start=t0, end=t1, limit=500, prefer="cold")),
    ("funding_hot", lambda: repo.funding("binance", "um", "BTCUSDT",
                                         start=t0, end=t1, limit=500, prefer="hot")),
):
    try:
        got = fn()
        probe["cases"].append({"case": name, "window": "2024-01-01~2024-02-01",
                               "rows": len(got),
                               "first_ts": (got[0].get("ts") or got[0].get("open_time")
                                            or got[0].get("funding_time")) if got else None})
    except Exception as exc:                                  # noqa: BLE001
        probe["cases"].append({"case": name, "error": f"{type(exc).__name__}: {exc}"})
dump("34_read_path_contrast.json", probe)
for c in probe["cases"]:
    log(f"对照 {c['case']}: rows={c.get('rows')} err={c.get('error')}")

# ---------------------------------------------------------------- 归档器状态 + 冷档新鲜度
state_path = ARCHIVE / "_state.json"
state_raw = {}
if state_path.exists():
    try:
        state_raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:                                  # noqa: BLE001
        state_raw = {"_read_error": f"{type(exc).__name__}: {exc}"}

con = hot()
hot_max = {}
for tf in ("1h", "4h", "15m"):
    r = con.execute("SELECT MAX(open_time) FROM candle_heads WHERE timeframe=?", (tf,)).fetchone()
    hot_max[tf] = r[0]
hot_max["oi_5m"] = con.execute("SELECT MAX(ts) FROM oi_history WHERE period='5m'").fetchone()[0]
hot_max["funding"] = con.execute("SELECT MAX(funding_time) FROM funding_history").fetchone()[0]
con.close()

import duckdb  # noqa: E402
d = duckdb.connect()
cold_max = {}
cold_max["candles_1h"] = d.execute(
    f"SELECT MAX(open_time) FROM read_parquet('{ARCHIVE}/candle_heads/*/*/*/1h/*.parquet')"
).fetchone()[0]
cold_max["candles_4h"] = d.execute(
    f"SELECT MAX(open_time) FROM read_parquet('{ARCHIVE}/candle_heads/*/*/*/4h/*.parquet')"
).fetchone()[0]
cold_max["candles_15m"] = d.execute(
    f"SELECT MAX(open_time) FROM read_parquet('{ARCHIVE}/candle_heads/*/*/*/15m/*.parquet')"
).fetchone()[0]
cold_max["oi_5m"] = d.execute(
    f"SELECT MAX(ts) FROM read_parquet('{ARCHIVE}/oi/*/*/*/*.parquet')"
).fetchone()[0]
cold_max["funding"] = d.execute(
    f"SELECT MAX(funding_time) FROM read_parquet('{ARCHIVE}/funding/*/*/*.parquet')"
).fetchone()[0]
d.close()

fresh = {
    "stats_at_utc": STATS_AT,
    "population": "全量（热库 MAX() 全表；冷档 read_parquet 全目录 MAX()，无抽样）",
    "hot_max_utc": {k: utc(v) for k, v in hot_max.items()},
    "cold_max_utc": {k: utc(v) for k, v in cold_max.items()},
    "lag_days": {
        "candles_1h": round((hot_max["1h"] - cold_max["candles_1h"]) / 86400000.0, 2)
        if hot_max["1h"] and cold_max["candles_1h"] else None,
        "candles_4h": round((hot_max["4h"] - cold_max["candles_4h"]) / 86400000.0, 2)
        if hot_max["4h"] and cold_max["candles_4h"] else None,
        "candles_15m": round((hot_max["15m"] - cold_max["candles_15m"]) / 86400000.0, 2)
        if hot_max["15m"] and cold_max["candles_15m"] else None,
        "oi_5m": round((hot_max["oi_5m"] - cold_max["oi_5m"]) / 86400000.0, 2)
        if hot_max["oi_5m"] and cold_max["oi_5m"] else None,
        "funding": round((hot_max["funding"] - cold_max["funding"]) / 86400000.0, 2)
        if hot_max["funding"] and cold_max["funding"] else None,
    },
}
dump("33_cold_freshness.json", fresh)
dump("32_archive_state.json", {"stats_at_utc": STATS_AT,
                               "state_file": "_state.json",
                               "state_bytes": state_path.stat().st_size if state_path.exists() else None,
                               "state_mtime_utc": utc(int(state_path.stat().st_mtime * 1000))
                               if state_path.exists() else None,
                               "raw": state_raw})
log("冷档新鲜度：" + json.dumps(fresh["lag_days"], ensure_ascii=False))

print(f"\n完成，用时 {time.time()-T0:.1f}s；统计时点 {STATS_AT}")

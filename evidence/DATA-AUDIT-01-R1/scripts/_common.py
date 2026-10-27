"""DATA-AUDIT-01-R1 公共工具（只读）。

纪律：
- 数据库一律 `mode=ro`，duckdb 一律 `read_only=True`；
- 不写任何业务目录，产物只落 `evidence/DATA-AUDIT-01-R1/out/`；
- 每个产物都必须带 `stats_at_utc`（统计时点）与 `population`（全量/抽样），
  否则下游无法判断结论适用范围。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # 项目根
# 产物目录可用 WX_OUT_DIR 覆盖：`reproduce.sh` 会指到 out_repro/，
# 这样复算不会覆盖已交付的 out/（交付证据必须冻结，不能被一次重跑刷掉统计时点）。
OUT = Path(os.environ.get("WX_OUT_DIR")
           or (Path(__file__).resolve().parents[1] / "out")).resolve()
HOT_DB = ROOT / "数据/市场数据/db/market_v2.sqlite3"
ARCHIVE = ROOT / "数据/市场数据/parquet"
POOL_JSON = ROOT / "数据/contracts/旧标的池/scope_crypto.json"

OUT.mkdir(parents=True, exist_ok=True)


def utc(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000.0, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ms() -> int:
    return int(time.time() * 1000)


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hot() -> sqlite3.Connection:
    """热库只读连接（URI mode=ro，绝不可写）。"""
    return sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True, timeout=30)


def duck():
    import duckdb
    return duckdb.connect(database=":memory:", read_only=False)


def pool() -> dict:
    d = json.load(open(POOL_JSON, encoding="utf-8"))
    syms = [x["symbol"] for x in d["binance_items"]]
    return {
        "scope_version": d.get("scope_version"),
        "generated_utc": d.get("generated_utc"),
        "symbols": syms,
        "n": len(syms),
        "okx_symbols": d.get("okx_symbols", []),
        "excluded_indices": d.get("excluded_indices"),
    }


def dump(name: str, obj) -> Path:
    p = OUT / name
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def write_csv(name: str, header: list[str], rows: list[list]) -> Path:
    import csv
    p = OUT / name
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(p)
    return p


def pct(a: int, b: int) -> float | None:
    if not b:
        return None
    return round(a * 100.0 / b, 2)


def quantiles(vals: list[float]) -> dict:
    if not vals:
        return {}
    s = sorted(vals)
    n = len(s)

    def q(p: float) -> float:
        i = min(n - 1, max(0, int(round(p * (n - 1)))))
        return s[i]

    return {
        "n": n,
        "min": s[0],
        "p10": q(0.10),
        "p50": q(0.50),
        "p90": q(0.90),
        "max": s[-1],
        "mean": round(sum(s) / n, 4),
    }

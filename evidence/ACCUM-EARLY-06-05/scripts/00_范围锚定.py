# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-05｜00 范围锚定与待复核主张登记。

本单只做三件事：**复核 05-F 的 G4、把三层分开、指出哪些能补哪些不能**。
所以第一步不是抄结论，而是把「要复核的主张」登记成可对撞的条目，并锁住数据身份。

纪律
----
· 05-F 报告里的数字一律**不**当断言常量：这里只从 05-F 的 `out/*.json` 读它自己写下的
  主张值（`claim`），实际值全部由本单脚本从原始数据重算（`actual`）。
· 不新建采集器、不补零、不跨交易所拼接（热库同时含 okx/swap，本单全部加
  `exchange='binance' AND market='um'` 过滤）、不伪造可得时间。
· 只读打开数据库；只写本单 `out/`。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import platform
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD_DIR = ROOT / "数据" / "市场数据" / "parquet" / "funding" / "binance" / "um"
E05C = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out"
E05F = ROOT / "evidence" / "ACCUM-EARLY-05-F" / "out"

TASK = "ACCUM-EARLY-06-05"


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def cold_manifest() -> dict:
    """冷档资金费分片指纹：对 (相对路径, 字节, sha256) 排序后逐行哈希。

    39 MB / 526 个文件，直接逐个算 sha256 是可行的（不像 12 GB 热库要分块）。
    """
    files = sorted(COLD_DIR.glob("*.parquet"))
    lines, total_bytes, total_rows_unknown = [], 0, True
    for f in files:
        sz = f.stat().st_size
        total_bytes += sz
        lines.append(f"{f.name}\t{sz}\t{sha256_file(f)}")
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return {"files": len(files), "bytes": total_bytes, "manifest_sha256": digest,
            "rule": "对 (文件名, 字节, 分片 sha256) 按文件名排序后逐行哈希"}


def load_json(p: Path):
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    t0 = time.time()
    w = load_json(E05C / "01_输入快照与版本锁定.json") or {}
    win = w.get("window") or {}
    mother = load_json(E05C / "04_统一观察母集.json") or {}
    syms48 = sorted((mother.get("per_symbol") or {}).keys())

    f3 = load_json(E05F / "03_读取链与可得时点.json") or {}
    f4 = load_json(E05F / "04_资金费口径.json") or {}
    f5 = load_json(E05F / "05_可用范围与缺口.json") or {}

    # ── 待复核主张：只取 05-F 自己写下的值，作为 claim 侧 ────────────────
    gap = (f3.get("funding_coverage_gap") or {})
    segs = gap.get("hot_gap_segments") or []
    q = (f4.get("quantified") or {})
    hold = (q.get("holdout_final") or {})
    claims = [
        {"编号": "C1", "主张": "热库资金费存在主缺口段", "claim": (
            {"from_utc": segs[0].get("from_utc"), "to_utc": segs[0].get("to_utc"),
             "symbols": segs[0].get("symbols"), "max_hours": segs[0].get("max_hours")}
            if segs else None), "来源": "05-F/out/03.funding_coverage_gap.hot_gap_segments[0]"},
        {"编号": "C2", "主张": "缺口段内冷档完整（热少冷多）", "claim": (
            {"window": gap.get("window"), "hot_rows": gap.get("hot_rows"),
             "cold_rows": gap.get("cold_rows"),
             "cold_more_symbols": gap.get("symbols_cold_more_than_hot"),
             "cold_more_rows": gap.get("cold_surplus_rows")} if gap else None),
         "来源": "05-F/out/03.funding_coverage_gap（注意：它的对比窗口是 2026-08-20→2026-10-01，"
               "不是 05-C 的 06-27→10-26；本单两个窗口都算）"},
        {"编号": "C3", "主张": "验证区间「该结算却无记录」的真实缺口笔数",
         "claim": {"fills": hold.get("fills"),
                   "zero_settlement": hold.get("funding_zero_settlement_trades"),
                   "zero_but_hold_lt_8h": hold.get("funding_zero_but_hold_lt_8h"),
                   "real_gap": hold.get("funding_real_gap_trades"),
                   "inside_hot_gap": hold.get("funding_gap_inside_hot_gap")},
         "来源": "05-F/out/04.quantified.holdout_final"},
        {"编号": "C4", "主张": "结算间隔中位数", "claim": (
            hold.get("settlements") or {}).get("median_interval_hours"),
         "来源": "05-F/out/04.quantified.holdout_final.settlements.median_interval_hours"},
        {"编号": "C5", "主张": "热库 fetched_at 事后回填占比",
         "claim": ((f3.get("non_bar_inputs") or {}).get("funding") or {}).get("fetch_lag"),
         "来源": "05-F/out/03.non_bar_inputs.funding.fetch_lag"},
        {"编号": "C6", "主张": "冷档是否有「当时可得」字段",
         "claim": ((f3.get("non_bar_inputs") or {}).get("funding") or {}).get("available_at_in_cold"),
         "来源": "05-F/out/03.non_bar_inputs.funding.available_at_in_cold"},
        {"编号": "C7", "主张": "G4 是唯一阻塞项",
         "claim": [g.get("编号") for g in (f5.get("gaps") or [])
                   if str(g.get("阻塞等级", "")).startswith("阻塞")],
         "来源": "05-F/out/05.gaps[].阻塞等级"},
    ]

    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    hot_stat = {
        "path": str(HOT.relative_to(ROOT)),
        "bytes": HOT.stat().st_size,
        "mtime_utc": iso(HOT.stat().st_mtime * 1000),
        "identity_caveat": ("采集器实时写入 ⇒ 字节数/mtime 只代表取锚点时刻；"
                            "12 GB 不做整库 sha256（本单只做只读统计，不声明库内容指纹）"),
        "funding_rows_all_exchanges": con.execute(
            "SELECT COUNT(*) FROM funding_history").fetchone()[0],
        "funding_rows_binance_um": con.execute(
            "SELECT COUNT(*) FROM funding_history WHERE exchange='binance' AND market='um'"
        ).fetchone()[0],
        "funding_rows_okx_swap": con.execute(
            "SELECT COUNT(*) FROM funding_history WHERE exchange='okx'").fetchone()[0],
        "跨交易所口径": ("热库同一张表混装 binance/um 与 okx/swap；本单所有查询强制 "
                    "exchange='binance' AND market='um'，不跨交易所拼接"),
    }
    con.close()

    cold = cold_manifest()
    cold.update({
        "dir": str(COLD_DIR.relative_to(ROOT)),
        "columns": ["funding_time", "funding_rate", "mark_price", "rate_type", "source"],
        "symbol_column": "无（标的名在文件名里）",
        "available_at_column": "无",
        "caveat": "冷档是只增归档，导出器在跑 ⇒ 分片数/指纹只代表取锚点时刻",
    })

    out = {
        "task": TASK,
        "probe": "00_范围锚定",
        "generated_at_utc": iso(int(time.time() * 1000)),
        "window": {
            "start_utc": win.get("start_utc"), "end_utc": win.get("end_utc"),
            "start_ms": win.get("start_ms"), "end_ms": win.get("end_ms"),
            "source": "05-C/out/01_输入快照与版本锁定.json#window（沿用 04-C 同一窗口）",
        },
        "universe": {
            "mother_48": len(syms48), "symbols": syms48,
            "note": ("主口径 = 05-C 统一观察母集（51 条候选涉及的标的去重）；"
                     "全池 526 只作对照，两者不混算"),
        },
        "data_identity": {"hot": hot_stat, "cold": cold},
        "claims_to_retest": claims,
        "env": {"python": sys.version.split()[0], "platform": platform.platform(),
                "duckdb": _duckdb_version()},
        "elapsed_sec": round(time.time() - t0, 2),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "00_范围锚定.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"window": out["window"], "mother_48": len(syms48),
                      "hot_rows_binance_um": hot_stat["funding_rows_binance_um"],
                      "hot_rows_okx": hot_stat["funding_rows_okx_swap"],
                      "cold": {k: cold[k] for k in ("files", "bytes", "manifest_sha256")},
                      "claims": [c["编号"] for c in claims]},
                     ensure_ascii=False, indent=2))


def _duckdb_version():
    try:
        import duckdb
        return duckdb.__version__
    except Exception as exc:  # 缺 duckdb 时如实记，不静默
        return f"不可用: {type(exc).__name__}"


if __name__ == "__main__":
    main()

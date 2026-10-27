#!/usr/bin/env python3
"""00 · 范围与数据身份锚定

本单（ACCUM-EARLY-06-06）的全部判断都必须能追到「哪个文件、哪一刻、什么范围」。
本脚本只读，产出 out/00_范围与身份锚定.json，作为后续 01–07 的唯一身份基准。

锚定内容：
  A 时间窗口 / 标的母集 / 交易所与合约
  B 热库与冷档的数据身份（含全量内容哈希 —— 后面 03 用它证明零写入）
  C 判别器前提 P2：热库 funding_history.fetched_at 是否全表非空
  D 冷热两侧实测覆盖边界（不是「声明覆盖」）
  E 登记 06-05 与本单要复核的 claim（数字不当常量）
"""

from __future__ import annotations

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
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out"
OUT.mkdir(parents=True, exist_ok=True)

HOT_DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
ARCHIVE = ROOT / "数据" / "市场数据" / "parquet"
FUNDING_DIR = ARCHIVE / "funding" / "binance" / "um"

EXCHANGE, MARKET = "binance", "um"


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def blake(path: Path, chunk: int = 8 << 20) -> str:
    """分块哈希。大库 read_bytes() 会 SIGKILL（06-04 踩过，见 MEMORY-OPS）。"""
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_file(path: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main() -> int:
    t0 = time.time()

    # ---------- A 窗口与母集 ----------
    src = json.loads((ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out"
                      / "04_统一观察母集.json").read_text(encoding="utf-8"))
    win = src["window"]
    symbols = list(src["universe"]["symbols"])
    start_ms = int(datetime.strptime(win["start_utc"], "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.strptime(win["end_utc"], "%Y-%m-%dT%H:%M:%SZ")
                 .replace(tzinfo=timezone.utc).timestamp() * 1000)

    # ---------- B 数据身份 ----------
    st = HOT_DB.stat()
    hot_ident = {
        "path": str(HOT_DB.relative_to(ROOT)),
        "bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "blake2b_16": blake(HOT_DB),
        "hash_algo": "blake2b-128 (分块 8MiB)",
    }

    shards = sorted(FUNDING_DIR.glob("*.parquet"))
    man_lines, tot = [], 0
    for p in shards:
        s = p.stat()
        tot += s.st_size
        man_lines.append(f"{p.name}\t{s.st_size}\t{s.st_mtime_ns}")
    cold_ident = {
        "dir": str(FUNDING_DIR.relative_to(ROOT)),
        "files": len(shards),
        "bytes": tot,
        "manifest_sha256": hashlib.sha256("\n".join(man_lines).encode()).hexdigest(),
        "manifest_note": "按文件名排序，逐行 name\\tsize\\tmtime_ns；不含内容哈希",
    }

    # ---------- C 判别器前提 P2 ----------
    con = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    q = lambda s, *p: con.execute(s, p).fetchall()

    hot_total = q("SELECT COUNT(*) FROM funding_history")[0][0]
    p2_nulls = q("SELECT COUNT(*) FROM funding_history WHERE fetched_at IS NULL")[0][0]
    p2 = {
        "hot_rows_total": hot_total,
        "fetched_at_null_rows": p2_nulls,
        "holds": p2_nulls == 0,
        "usage": "若 holds，则 `fetched_at is None` 唯一标识 prefer='hot' 里混进来的冷档行；"
                 "否则适配器的来源归属不成立，必须改用其它判别器。",
    }

    by_ex = [dict(r) for r in q("SELECT exchange,market,COUNT(*) n FROM funding_history "
                               "GROUP BY 1,2 ORDER BY n DESC")]
    hot_range = dict(zip(("lo", "hi"),
                         q("SELECT MIN(funding_time),MAX(funding_time) FROM funding_history")[0]))
    # 窗口统计必须带 exchange/market 过滤：热库同时存 binance/um 与 okx/swap，
    # 不滤会把 okx 的 mark 空值算进本单口径（okx 全部 60,035 行 mark 为空）。
    W = "exchange=? AND market=? AND funding_time>=? AND funding_time<=?"
    win_rows = q(f"SELECT COUNT(*) FROM funding_history WHERE {W}",
                 EXCHANGE, MARKET, start_ms, end_ms)[0][0]
    win_mark_null = q(f"SELECT COUNT(*) FROM funding_history WHERE {W} AND mark_price IS NULL",
                      EXCHANGE, MARKET, start_ms, end_ms)[0][0]
    win_rate_null = q(f"SELECT COUNT(*) FROM funding_history WHERE {W} AND funding_rate IS NULL",
                      EXCHANGE, MARKET, start_ms, end_ms)[0][0]
    mark_null_all = q("SELECT COUNT(*) FROM funding_history WHERE mark_price IS NULL")[0][0]
    mark_null_um = q("SELECT COUNT(*) FROM funding_history WHERE exchange='binance' "
                     "AND market='um' AND mark_price IS NULL")[0][0]
    mark_null_um_range = q("SELECT MIN(funding_time),MAX(funding_time),COUNT(DISTINCT symbol) "
                           "FROM funding_history WHERE exchange='binance' AND market='um' "
                           "AND mark_price IS NULL")[0]
    syms_ph = ",".join("?" * len(symbols))
    uni_hot_rows = q(f"SELECT COUNT(*) FROM funding_history WHERE symbol IN ({syms_ph})",
                     *symbols)[0][0]
    uni_mark_null = q(f"SELECT COUNT(*) FROM funding_history WHERE symbol IN ({syms_ph}) "
                      f"AND mark_price IS NULL", *symbols)[0][0]
    hot_cols = [r[1] for r in q("PRAGMA table_info(funding_history)")]
    con.close()

    # ---------- D 冷档实测覆盖 ----------
    import duckdb
    d = duckdb.connect()
    d.execute("PRAGMA threads=2")
    lit = "[" + ",".join("'" + str(p).replace("'", "''") + "'" for p in shards) + "]"
    cold_cols = [r[0] for r in d.execute(f"DESCRIBE SELECT * FROM read_parquet({lit})").fetchall()]
    c = d.execute(f"SELECT COUNT(*) n, MIN(funding_time) lo, MAX(funding_time) hi, "
                  f"COUNT(DISTINCT filename) f "
                  f"FROM read_parquet({lit}, filename=true)").fetchone()
    # 去重键 = (文件=symbol, funding_time)，不是全局 funding_time（跨标的同一时刻是正常的多事件）
    ck = d.execute(f"SELECT COUNT(*) FROM (SELECT DISTINCT filename, funding_time "
                   f"FROM read_parquet({lit}, filename=true))").fetchone()[0]
    cold_mark_null = d.execute(f"SELECT COUNT(*) FROM read_parquet({lit}) "
                               f"WHERE mark_price IS NULL").fetchone()[0]
    cold_rate_null = d.execute(f"SELECT COUNT(*) FROM read_parquet({lit}) "
                               f"WHERE funding_rate IS NULL").fetchone()[0]
    cold_win = d.execute(f"SELECT COUNT(*) FROM read_parquet({lit}) "
                         f"WHERE funding_time>=? AND funding_time<=?",
                         [start_ms, end_ms]).fetchone()[0]
    cold_src = d.execute(f"SELECT source, COUNT(*) n FROM read_parquet({lit}) "
                         f"GROUP BY 1 ORDER BY n DESC LIMIT 6").fetchall()
    d.close()

    # ---------- E 待复核 claim ----------
    claims = [
        {"id": "C1", "src": "06-05 §1/§4",
         "text": "热库缺口段 2026-09-02T20:00Z→2026-09-18T16:00Z（380h）",
         "how": "02 用适配器逐 symbol 重算缺口段并聚类"},
        {"id": "C2", "src": "06-05 §4",
         "text": "母集48 缺口 2,822 个（仅 39 个周期可靠标的）",
         "how": "04/05 逐事件对账后重算"},
        {"id": "C3", "src": "06-05 §6",
         "text": "holdout_final 1,523 笔里 104 笔资金费真缺口、冷档补回 104、残余 0",
         "how": "本单不复算交易层，只核事件层可补性；交易层结论转引并标未复核"},
        {"id": "C4", "src": "06-05 §2",
         "text": "冷档无 available_at / fetched_at 列",
         "how": "00 直接列冷档 schema 核对"},
        {"id": "C5", "src": "06-05 §5",
         "text": "同键冲突（rate/mark 不一致）全池 0",
         "how": "04 逐事件对账统计 conflicts"},
        {"id": "C6", "src": "06-05 §7",
         "text": "prefer='hot' 会混入冷档行",
         "how": "00 的 P2 + 01 的泄漏计数断言"},
    ]

    out = {
        "task": "ACCUM-EARLY-06-06",
        "probe": "00_范围与身份锚定",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "depends_on": ["ACCUM-EARLY-06-05", "ACCUM-EARLY-05-C", "ACCUM-EARLY-05-F"],
        "A_窗口与母集": {
            "start_utc": win["start_utc"], "end_utc": win["end_utc"],
            "start_ms": start_ms, "end_ms": end_ms,
            "warmup_start_utc": win["warmup_start_utc"],
            "span_hours": round((end_ms - start_ms) / 3600000, 3),
            "exchange": EXCHANGE, "market": MARKET,
            "symbols_source": "05-C 04_统一观察母集.json universe.symbols（51 笔候选涉及的标的，去重）",
            "symbol_count": len(symbols),
            "symbols": symbols,
        },
        "B_数据身份": {"热库": hot_ident, "冷档": cold_ident},
        "C_判别器前提P2": p2,
        "D_热库事实": {
            "rows_total": hot_total,
            "by_exchange_market": by_ex,
            "range": {"lo_ms": hot_range["lo"], "lo_utc": utc(hot_range["lo"]),
                      "hi_ms": hot_range["hi"], "hi_utc": utc(hot_range["hi"])},
            "columns": hot_cols,
            "window_rows": win_rows,
            "window_mark_null": win_mark_null,
            "window_rate_null": win_rate_null,
            "mark_null_all": mark_null_all,
            "mark_null_binance_um": mark_null_um,
            "mark_null_binance_um_range": {
                "lo_utc": utc(mark_null_um_range[0]), "hi_utc": utc(mark_null_um_range[1]),
                "symbols": mark_null_um_range[2],
            },
            "universe_rows_all_history": uni_hot_rows,
            "universe_mark_null": uni_mark_null,
        },
        "E_冷档事实": {
            "files": len(shards),
            "columns": cold_cols,
            "has_available_at": "available_at" in cold_cols,
            "has_fetched_at": "fetched_at" in cold_cols,
            "rows": c[0], "distinct_keys": ck, "distinct_filename_time": ck,
            "range": {"lo_ms": c[1], "lo_utc": utc(c[1]), "hi_ms": c[2], "hi_utc": utc(c[2])},
            "mark_null": cold_mark_null,
            "rate_null": cold_rate_null,
            "window_rows": cold_win,
            "top_source": [{"source": s, "n": n} for s, n in cold_src],
        },
        "F_待复核claim": claims,
        "env": {
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "platform": platform.platform(),
            "duckdb": duckdb.__version__ if "duckdb" in sys.modules else None,
        },
        "elapsed_sec": round(time.time() - t0, 1),
    }

    p = OUT / "00_范围与身份锚定.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写出 {p.relative_to(ROOT)}")
    print("P2 holds =", p2["holds"], "| fetched_at NULL =", p2_nulls)
    print("热库", hot_total, "行 | 窗口", win_rows, "行 | 窗口 mark 空", win_mark_null)
    print("冷档", c[0], "行 | 去重键", ck, "| 范围", utc(c[1]), "→", utc(c[2]))
    print("冷档 available_at 列:", "available_at" in cold_cols)
    print("elapsed", out["elapsed_sec"], "s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

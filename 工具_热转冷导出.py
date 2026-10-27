#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""热层 → 冷层 自动导出器

把热库 market_v2.sqlite3 里**已闭合**的 K 线、持仓量，增量导出到冷层 parquet，
让回测能读到最新数据。可以单次跑，也可以常驻循环。

落点（每个组合一个 live.parquet，只追加）：
    parquet/candles/{ex}/{mk}/{sym}/{tf}/live.parquet
    parquet/candle_heads/{ex}/{mk}/{sym}/{tf}/live.parquet   与上一行成对
    parquet/oi/{ex}/{mk}/{sym}/live.parquet

为什么这样设计
--------------
1. 读侧 market_repository._cold_candles 用 glob("*.parquet") + JOIN candle_heads
   USING(exchange,market,symbol,timeframe,open_time,source_id)，所以：
     · 文件名可任意，但 candles / candle_heads 必须成对；
     · 两边 source_id 必须逐行一致（本脚本直接沿用热库同一个 source_id）；
     · 同一 open_time 不能既在历史分片又在 live 里（本脚本用水位 + 写前去重双保险）。
2. **绝不改写任何既有 {YYYY}.parquet 历史分片**，只在旁边新增 live.parquet。
   回滚 = 删掉 live.parquet 与 _export_state.json，历史数据一个字节都没动。
3. 水位存在 _export_state.json，记每个组合已导出到的 max(open_time)。
   首次运行从冷层 parquet 的 row group statistics 读 max，不读全表。

用法
----
  $PY 工具_热转冷导出.py --once                 单次导出
  $PY 工具_热转冷导出.py --once --dry-run       只看会写多少，不落盘
  $PY 工具_热转冷导出.py --once --rebuild-state 重建水位（冷层变动后用）
  $PY 工具_热转冷导出.py --loop 600             常驻，每 600 秒一轮
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
DB = ROOT / "数据/市场数据/db/market_v2.sqlite3"
ARC = ROOT / "数据/市场数据/parquet"
STATE = ARC / "_export_state.json"
LOG = ROOT / "回测日志/热转冷导出.log"

# 回测必需的三条快车道周期；其余周期走慢车道
FAST_TF = ("15m", "1h", "4h")
SLOW_TF = ("5m", "30m", "8h", "12h", "1d")
SLOW_EVERY_S = 6 * 3600      # 慢车道周期：至少间隔 6 小时才导一次
OI_EVERY_S = 3600            # 持仓量：至少间隔 1 小时
MIN_FREE_GIB = 5.0           # 磁盘剩余低于这个数就停手
ONLY_SYMBOL: set[str] = set()  # 命令行 --symbol 限定（测试用）

# 注意：available_at / fetched_at 在冷层历史分片里是 **null 类型**（全空）。
# duckdb 读多个 parquet 时以第一个文件的 schema 为准，BIGINT 无法 cast 到 NULL，
# 所以这里必须同样写成 null 类型、值一律 None，否则读侧直接报错。
CANDLE_COLS = ["exchange", "market", "symbol", "timeframe", "open_time", "close_time",
               "open", "high", "low", "close", "volume", "quote_volume", "trade_count",
               "taker_buy_volume", "taker_buy_quote_volume", "source_id", "available_at",
               "is_closed"]
CANDLE_TYPES = [pa.string(), pa.string(), pa.string(), pa.string(), pa.int64(), pa.int64(),
                pa.string(), pa.string(), pa.string(), pa.string(), pa.string(), pa.string(),
                pa.int64(), pa.string(), pa.string(), pa.int64(), pa.null(), pa.int64()]
SCHEMA_C = pa.schema(list(zip(CANDLE_COLS, CANDLE_TYPES)))

HEAD_COLS = ["exchange", "market", "symbol", "timeframe", "open_time", "source_id"]
HEAD_TYPES = [pa.string(), pa.string(), pa.string(), pa.string(), pa.int64(), pa.int64()]
SCHEMA_H = pa.schema(list(zip(HEAD_COLS, HEAD_TYPES)))

OI_COLS = ["exchange", "market", "symbol", "period", "ts", "open_interest",
           "open_interest_value", "oi_ccy", "oi_usd", "source", "fetched_at"]
OI_TYPES = [pa.string(), pa.string(), pa.string(), pa.string(), pa.int64(), pa.string(),
            pa.string(), pa.string(), pa.string(), pa.string(), pa.null()]
SCHEMA_OI = pa.schema(list(zip(OI_COLS, OI_TYPES)))

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
         "2h": 7_200_000, "4h": 14_400_000, "8h": 28_800_000, "12h": 43_200_000,
         "1d": 86_400_000}


def log(msg: str) -> None:
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    # 只有前台（终端）才回显；后台运行时 stdout 已重定向到同一个日志文件，再 print 会写两遍
    if sys.stdout.isatty():
        print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def free_gib() -> float:
    return shutil.disk_usage(str(ROOT)).free / 2 ** 30


def acquire_lock():
    """防止两轮重叠（定时器在上一次没跑完时又触发）。拿不到锁就返回 None。"""
    ARC.mkdir(parents=True, exist_ok=True)
    fh = open(ARC / "_export.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


# ---------------------------------------------------------------- 水位

def load_state() -> dict:
    if STATE.exists():
        try:
            d = json.loads(STATE.read_text(encoding="utf-8"))
            d.setdefault("candles", {})
            d.setdefault("oi", {})
            d.setdefault("last_run", {})
            return d
        except Exception as exc:
            log("状态文件损坏，按空处理：%s" % exc)
    return {"candles": {}, "oi": {}, "last_run": {}, "created_at": time.time()}


def save_state(st: dict) -> None:
    st["updated_at"] = time.time()
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def _parquet_max(path: Path, col: str) -> int | None:
    """从 parquet 元数据取某列最大值，不读数据体。"""
    try:
        m = pq.read_metadata(path)
        i = m.schema.names.index(col)
        best = None
        for g in range(m.num_row_groups):
            v = m.row_group(g).column(i).statistics.max
            if v is not None and (best is None or v > best):
                best = v
        return int(best) if best is not None else None
    except Exception:
        return None


def rebuild_state(st: dict) -> dict:
    """遍历冷层既有分片，用元数据重建每个组合的水位。"""
    log("重建水位：扫描冷层 parquet 元数据……")
    n = 0
    base = ARC / "candles"
    for ex_dir in sorted(base.glob("*")):
        if not ex_dir.is_dir():
            continue
        for mk_dir in sorted(ex_dir.glob("*")):
            if not mk_dir.is_dir():
                continue
            for sym_dir in sorted(mk_dir.glob("*")):
                if not sym_dir.is_dir():
                    continue
                for tf_dir in sorted(sym_dir.glob("*")):
                    if not tf_dir.is_dir():
                        continue
                    mx = None
                    for f in sorted(tf_dir.glob("*.parquet")):
                        v = _parquet_max(f, "open_time")
                        if v is not None and (mx is None or v > mx):
                            mx = v
                    if mx is not None:
                        st["candles"]["%s/%s/%s/%s" % (ex_dir.name, mk_dir.name,
                                                        sym_dir.name, tf_dir.name)] = mx
                        n += 1

    obase = ARC / "oi"
    for ex_dir in sorted(obase.glob("*")):
        if not ex_dir.is_dir():
            continue
        for mk_dir in sorted(ex_dir.glob("*")):
            if not mk_dir.is_dir():
                continue
            for sym_dir in sorted(mk_dir.glob("*")):
                if not sym_dir.is_dir():
                    continue
                mx = None
                for f in sorted(sym_dir.glob("*.parquet")):
                    v = _parquet_max(f, "ts")
                    if v is not None and (mx is None or v > mx):
                        mx = v
                if mx is not None:
                    st["oi"]["%s/%s/%s" % (ex_dir.name, mk_dir.name, sym_dir.name)] = mx
                    n += 1
    log("重建完成：%d 个组合有水位" % n)
    return st


# ---------------------------------------------------------------- 写盘

def merge_write(path: Path, new_rows: list, cols: list, schema: pa.Schema,
                key: str) -> tuple[int, int]:
    """把 new_rows 合并进 path（若存在），原子替换。返回 (原有行数, 写入行数)。"""
    old = []
    if path.exists():
        try:
            t = pq.read_table(path)
            old = t.to_pylist()
        except Exception as exc:
            log("  ! 读取既有 live 失败，按空处理 %s：%s" % (path.name, exc))
    merged = {}
    for r in old:
        merged[int(r[key])] = r
    before = len(merged)
    for r in new_rows:
        merged[int(r[key])] = r          # 同 open_time 以新数据为准
    rows = [merged[k] for k in sorted(merged)]
    tbl = pa.Table.from_pylist(rows, schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(tbl, tmp, compression="zstd")
    os.replace(tmp, path)
    return before, len(rows)


def export_candles(st: dict, conn: sqlite3.Connection, dry: bool,
                   tfs: tuple, force: bool) -> dict:
    """导出 K 线。tfs 指定本次处理哪些周期。"""
    if not force and tfs == SLOW_TF:
        last = st["last_run"].get("slow_candles", 0)
        if time.time() - last < SLOW_EVERY_S:
            return {"skipped": "slow 车道未到时间"}

    pairs = conn.execute(
        "SELECT DISTINCT exchange, market, symbol FROM candle_heads ORDER BY 1,2,3"
    ).fetchall()
    if ONLY_SYMBOL:
        pairs = [p for p in pairs if p[2] in ONLY_SYMBOL]
    wm = st["candles"]
    # 每个标的用一个「最小水位」做粗过滤，再按周期精过滤
    tf_set = set(tfs)
    stat = {"组合": 0, "新增行": 0, "写文件": 0, "跳过无新增": 0}

    for ex, mk, sym in pairs:
        floors = [wm.get("%s/%s/%s/%s" % (ex, mk, sym, tf), 0) for tf in tfs]
        lo = min(floors) if floors else 0
        rows = conn.execute(
            """SELECT v.exchange, v.market, v.symbol, v.timeframe, v.open_time, v.close_time,
                      v.open, v.high, v.low, v.close, v.volume, v.quote_volume, v.trade_count,
                      v.taker_buy_volume, v.taker_buy_quote_volume, v.source_id,
                      v.available_at, v.is_closed
               FROM candle_versions v
               JOIN candle_heads h
                 ON v.exchange=h.exchange AND v.market=h.market AND v.symbol=h.symbol
                AND v.timeframe=h.timeframe AND v.open_time=h.open_time
                AND v.source_id=h.source_id
               WHERE v.exchange=? AND v.market=? AND v.symbol=? AND v.open_time>?
               ORDER BY v.timeframe, v.open_time""",
            (ex, mk, sym, lo)).fetchall()
        if not rows:
            continue
        by_tf: dict[str, list] = {}
        for r in rows:
            by_tf.setdefault(r[3], []).append(r)

        for tf, rs in by_tf.items():
            if tf not in tf_set:
                continue
            key = "%s/%s/%s/%s" % (ex, mk, sym, tf)
            w = wm.get(key, 0)
            rs = [r for r in rs if r[4] > w]
            if not rs:
                stat["跳过无新增"] += 1
                continue
            stat["组合"] += 1
            stat["新增行"] += len(rs)
            if dry:
                continue
            dicts = [dict(zip(CANDLE_COLS, r)) for r in rs]
            for d in dicts:
                d["available_at"] = None        # 冷层口径：该列恒空
            cpath = ARC / "candles" / ex / mk / sym / tf / "live.parquet"
            hpath = ARC / "candle_heads" / ex / mk / sym / tf / "live.parquet"
            merge_write(cpath, dicts, CANDLE_COLS, SCHEMA_C, "open_time")
            hdicts = [{c: d[c] for c in HEAD_COLS} for d in dicts]
            merge_write(hpath, hdicts, HEAD_COLS, SCHEMA_H, "open_time")
            stat["写文件"] += 2
            wm[key] = max(int(d["open_time"]) for d in dicts)

    if not dry and tfs == SLOW_TF:
        st["last_run"]["slow_candles"] = time.time()
    return stat


def export_oi(st: dict, conn: sqlite3.Connection, dry: bool, force: bool = False) -> dict:
    if not force and time.time() - st["last_run"].get("oi", 0) < OI_EVERY_S:
        return {"skipped": "持仓量未到时间"}
    pairs = conn.execute(
        "SELECT DISTINCT exchange, market, symbol FROM oi_history WHERE period='5m' "
        "ORDER BY 1,2,3").fetchall()
    if ONLY_SYMBOL:
        pairs = [p for p in pairs if p[2] in ONLY_SYMBOL]
    wm = st["oi"]
    stat = {"组合": 0, "新增行": 0, "写文件": 0}
    for ex, mk, sym in pairs:
        key = "%s/%s/%s" % (ex, mk, sym)
        w = wm.get(key, 0)
        rows = conn.execute(
            """SELECT exchange, market, symbol, period, ts, open_interest,
                      open_interest_value, oi_ccy, oi_usd, source, fetched_at
               FROM oi_history
               WHERE exchange=? AND market=? AND symbol=? AND period='5m' AND ts>?
               ORDER BY ts""", (ex, mk, sym, w)).fetchall()
        if not rows:
            continue
        stat["组合"] += 1
        stat["新增行"] += len(rows)
        if dry:
            continue
        dicts = [dict(zip(OI_COLS, r)) for r in rows]
        for d in dicts:
            d["fetched_at"] = None              # 冷层口径：该列恒空
        path = ARC / "oi" / ex / mk / sym / "live.parquet"
        merge_write(path, dicts, OI_COLS, SCHEMA_OI, "ts")
        stat["写文件"] += 1
        wm[key] = max(int(d["ts"]) for d in dicts)
    st["last_run"]["oi"] = time.time()
    return stat


# ---------------------------------------------------------------- 主流程

def run_once(args) -> int:
    if not DB.exists():
        log("热库不存在：%s" % DB)
        return 2
    lock = acquire_lock()
    if lock is None:
        log("上一轮还在跑，本轮跳过")
        return 0
    try:
        return _run_once_locked(args)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _run_once_locked(args) -> int:
    free = free_gib()
    if free < MIN_FREE_GIB:
        log("磁盘剩余 %.1f GiB 低于安全线 %.1f GiB，本轮不导出" % (free, MIN_FREE_GIB))
        return 3

    st = load_state()
    if args.rebuild_state or not st["candles"]:
        st = rebuild_state(st)
        if not args.dry_run:
            save_state(st)

    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=60)
    conn.execute("PRAGMA query_only=1")

    t0 = time.time()
    try:
        s1 = export_candles(st, conn, args.dry_run, FAST_TF, force=True)
        log("K线快车道 %s：%s" % (",".join(FAST_TF), json.dumps(s1, ensure_ascii=False)))
        if not args.fast_only:
            s2 = export_candles(st, conn, args.dry_run, SLOW_TF, force=args.force_slow)
            log("K线慢车道 %s：%s" % (",".join(SLOW_TF), json.dumps(s2, ensure_ascii=False)))
            s3 = export_oi(st, conn, args.dry_run, force=args.force_oi)
            log("持仓量：%s" % json.dumps(s3, ensure_ascii=False))
    finally:
        conn.close()

    if not args.dry_run:
        st["last_run"]["finished_at"] = time.time()
        st["last_run"]["duration_s"] = round(time.time() - t0, 1)
        save_state(st)

    log("本轮结束，用时 %.1f 秒，磁盘剩余 %.1f GiB%s"
        % (time.time() - t0, free_gib(), "（DRY-RUN，未落盘）" if args.dry_run else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="跑一轮就退出（默认）")
    ap.add_argument("--loop", type=int, default=0, help="常驻，每 N 秒一轮")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rebuild-state", action="store_true", help="从冷层元数据重建水位")
    ap.add_argument("--fast-only", action="store_true", help="只导 15m/1h/4h")
    ap.add_argument("--force-slow", action="store_true", help="忽略慢车道的时间间隔")
    ap.add_argument("--force-oi", action="store_true", help="忽略持仓量的时间间隔")
    ap.add_argument("--symbol", default="", help="只处理指定标的（逗号分隔，测试用）")
    args = ap.parse_args()

    global ONLY_SYMBOL
    ONLY_SYMBOL = {s.strip().upper() for s in args.symbol.split(",") if s.strip()}

    if args.loop > 0:
        log("常驻模式启动，间隔 %d 秒" % args.loop)
        while True:
            try:
                run_once(args)
            except Exception as exc:            # noqa: BLE001
                log("本轮异常：%s: %s" % (type(exc).__name__, exc))
            time.sleep(args.loop)
    return run_once(args)


if __name__ == "__main__":
    sys.exit(main())

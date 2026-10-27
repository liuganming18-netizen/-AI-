#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-A · 02 全池缺口分类

工单要求「全池列成功、仍旧、缺失及原因」。本脚本对**币安 UM 全部合约**逐个判定
四类状态，并给出**可复核的原始值**（时间戳、行数、OHLCV），不写结论性形容词。

三类原因必须分清（工单原文）：
  · 采集停更      —— 写入侧不再写（快照文件时间不推进 / K 线头不推进）
  · 读取错误      —— 数据在但读不到（文件在、接口空）
  · 标的停止交易  —— 交易所侧已交割/暂停（exchangeInfo.status 非 TRADING）

判定维度（逐标的）：
  D1 K 线水位：candle_heads(1h) 的最新 open_time 与「当前应到小时」的差
  D2 零成交冻结：若水位落后，取该标的最近 5 根 1h，检查 volume==0 且 OHLC 全等
  D3 交易所状态：exchangeInfo 的 status / deliveryDate / onboardDate
  D4 快照覆盖：快照文件是否存在、snapshot_refreshed_at、距采样时刻的秒数
  D5 指标归档：metrics 目录是否存在、该标的 parquet 最新行时间（抽样）

只读：不写库、不写快照、不写归档；exchangeInfo 仅 1 次 GET。
输出：out/pool_gap.json、out/pool_gap.csv
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.normpath(os.path.join(HERE, "..", "out"))
BACKEND = os.path.join(PROJ, "框架", "backend")
DB = os.path.join(PROJ, "数据", "市场数据", "db", "market_v2.sqlite3")
SNAP_DIR = os.path.join(BACKEND, "快照", "public_extras")
ARCHIVE = os.path.join(PROJ, "数据", "市场数据", "parquet")

FRESH_S = 900          # 与 public_snapshot.STALE_AFTER_S 同口径
HOUR_MS = 3_600_000


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ms():
    return int(time.time() * 1000)


def http_json(url, timeout=25.0, attempts=3):
    """直连（显式禁用代理）。SSL 偶发 EOF ⇒ 重试并留痕，不静默失败。"""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "accum-early-03a/1.0"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:                               # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(last)


def load_universe():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT symbol, status, contract_type, onboard_time, snapshot_utc "
            "FROM symbol_pool WHERE exchange='binance' AND market='um' ORDER BY symbol"
        ).fetchall()
    finally:
        con.close()
    return [
        {"symbol": r[0], "pool_status": r[1], "contract_type": r[2],
         "onboard_time": r[3], "pool_snapshot_utc": r[4]}
        for r in rows
    ]


def load_heads():
    """1h 最新 open_time（走 candle_heads，不扫 candles）。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT symbol, MAX(open_time) FROM candle_heads "
            "WHERE exchange='binance' AND market='um' AND timeframe='1h' GROUP BY symbol"
        ).fetchall()
    finally:
        con.close()
    return {r[0]: r[1] for r in rows}


def load_tail_bars(symbols, n=5):
    """取这些标的最近 n 根 1h 的 OHLCV（只在需要判定零成交冻结时调用）。"""
    if not symbols:
        return {}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {}
    try:
        q = ("SELECT symbol, open_time, open, high, low, close, volume FROM candles "
             "WHERE exchange='binance' AND market='um' AND timeframe='1h' AND symbol=? "
             "ORDER BY open_time DESC LIMIT ?")
        for s in symbols:
            rows = con.execute(q, (s, n)).fetchall()
            out[s] = [
                {"open_time": r[1], "open": r[2], "high": r[3], "low": r[4],
                 "close": r[5], "volume": r[6]}
                for r in rows
            ]
    finally:
        con.close()
    return out


def _f(x):
    """库里的 OHLCV 可能是 TEXT（实测 volume 为字符串），必须显式转数再比。"""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def zero_flat(bars):
    """连续零成交且 OHLC 全等的根数（从最新往回数）。"""
    if not bars:
        return 0
    run = 0
    for b in bars:
        o, h, l, c, v = (_f(b["open"]), _f(b["high"]), _f(b["low"]),
                         _f(b["close"]), _f(b["volume"]))
        if None in (o, h, l, c, v):
            break
        if v == 0 and o == h == l == c:
            run += 1
        else:
            break
    return run


def load_snapshots():
    """磁盘上全部快照：symbol -> {refreshed_at, age_s, available, file}"""
    out = {}
    if not os.path.isdir(SNAP_DIR):
        return out
    now = time.time()
    for n in os.listdir(SNAP_DIR):
        if not n.endswith(".json") or n.startswith("_"):
            continue
        p = os.path.join(SNAP_DIR, n)
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        sym = d.get("market_id")
        if not sym:
            continue
        out[sym] = {
            "file": n,
            "refreshed_at": d.get("snapshot_refreshed_at"),
            "age_s": round(now - os.path.getmtime(p)),
            "available": d.get("available"),
        }
    return out


def metrics_archive_state():
    """指标归档总体状态：目录数、BTC 最新行时间、样本标的数。"""
    root = os.path.join(ARCHIVE, "metrics", "binance", "um")
    res = {"root": root, "dirs": 0, "dir_mtime": None, "sample": {}}
    if not os.path.isdir(root):
        res["error"] = "metrics 归档根目录不存在"
        return res
    dirs = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
    res["dirs"] = len(dirs)
    res["dir_mtime"] = datetime.fromtimestamp(
        os.path.getmtime(root), timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 抽样读 5 个标的的当年 parquet，取最新 event 时间
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"):
        d = os.path.join(root, sym)
        if not os.path.isdir(d):
            res["sample"][sym] = {"error": "无目录"}
            continue
        files = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
        entry = {"files": len(files), "years": [f.replace(".parquet", "") for f in files]}
        last = None
        for f in files:
            p = os.path.join(d, f)
            entry[f"mtime_{f}"] = datetime.fromtimestamp(
                os.path.getmtime(p), timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        # 用 metrics_feed 的只读尾部读取（不新增读取器）
        try:
            sys.path.insert(0, BACKEND)
            import metrics_feed                                # noqa: PLC0415
            tail = metrics_feed.read_metrics_tail(sym, now_ms())
            if tail:
                # 该读取器返回的字段是 `ts`（整数毫秒），不是 event_time
                last = iso(max(int(r["ts"]) for r in tail))
                entry["tail_rows"] = len(tail)
            else:
                entry["tail_rows"] = 0
        except Exception as exc:                               # noqa: BLE001
            entry["read_error"] = f"{type(exc).__name__}: {exc}"
        entry["last_event_time"] = last
        res["sample"][sym] = entry
    return res


def main():
    os.makedirs(OUT, exist_ok=True)
    t_sampled = now_ms()
    expected_open = (t_sampled // HOUR_MS) * HOUR_MS   # 当前小时的起点

    uni = load_universe()
    heads = load_heads()
    snaps = load_snapshots()

    # ── exchangeInfo（1 次 GET，只读）───────────────────────────
    exch = {}
    exch_meta = {"url": "https://fapi.binance.com/fapi/v1/exchangeInfo", "ok": False}
    try:
        info = http_json(exch_meta["url"])
        for s in info.get("symbols", []):
            if s.get("contractType") != "PERPETUAL" and s.get("contractType") != "CURRENT_QUARTER":
                pass
            exch[s.get("symbol")] = {
                "status": s.get("status"),
                "contractType": s.get("contractType"),
                "deliveryDate": s.get("deliveryDate"),
                "onboardDate": s.get("onboardDate"),
                "quoteAsset": s.get("quoteAsset"),
            }
        exch_meta["ok"] = True
        exch_meta["symbols"] = len(exch)
    except Exception as exc:                                   # noqa: BLE001
        exch_meta["error"] = f"{type(exc).__name__}: {exc}"

    # ── 第一轮判定 ──────────────────────────────────────────────
    rows = []
    need_bars = []
    for it in uni:
        sym = it["symbol"]
        head = heads.get(sym)
        lag_ms = (expected_open - head) if head is not None else None
        lag_h = None if lag_ms is None else round(lag_ms / HOUR_MS, 2)
        e = exch.get(sym) or {}
        snap = snaps.get(sym)
        row = {
            "symbol": sym,
            "pool_status": it["pool_status"],
            "exch_status": e.get("status"),
            "exch_contract_type": e.get("contractType"),
            "exch_delivery_utc": iso(e.get("deliveryDate")),
            "head_1h_open_utc": iso(head),
            "lag_hours": lag_h,
            "snapshot_file": (snap or {}).get("file"),
            "snapshot_refreshed_at": (snap or {}).get("refreshed_at"),
            "snapshot_age_s": (snap or {}).get("age_s"),
            "snapshot_fresh": bool(snap and (snap.get("age_s") or 10**9) < FRESH_S),
        }
        rows.append(row)

    # 零成交冻结必须**全池**判定：交割合约的 K 线水位仍在推进（交易所持续返回
    # 零成交冻结价），只看「水位落后」会整批漏掉。528 标的 × 5 根，成本可接受。
    tails = load_tail_bars([r["symbol"] for r in rows], n=5)
    for row in rows:
        sym = row["symbol"]
        if sym in tails:
            run = zero_flat(tails[sym])
            row["zero_flat_run"] = run
            row["tail_bars"] = tails[sym]
        else:
            row["zero_flat_run"] = None

    # ── 分类 ────────────────────────────────────────────────────
    def classify(r):
        lag = r["lag_hours"]
        es = (r["exch_status"] or "").upper()
        zf = r["zero_flat_run"] or 0
        # 1) 交易所侧已不交易（官方 exchangeInfo 状态非 TRADING）
        if es and es != "TRADING":
            return ("EXCHANGE_NOT_TRADING",
                    f"交易所状态 {es}（交割时间 {r['exch_delivery_utc']}），"
                    f"K 线水位 {r['head_1h_open_utc']}，连续零成交 {zf} 根")
        # 2) 交易所仍在交易但 K 线不推进 ⇒ 采集停更
        if lag is not None and lag > 2:
            if zf >= 3:
                return ("BAR_FROZEN_ZERO_VOLUME",
                        f"连续 {zf} 根零成交冻结价，水位停在 {r['head_1h_open_utc']}")
            return ("COLLECTOR_STALLED",
                    f"无零成交特征，K 线落后 {lag} 小时 ⇒ 疑采集停更")
        if r["head_1h_open_utc"] is None:
            return ("NO_BAR", "热库无该标的 1h 头")
        # 3) 水位在推进，但成交全为零 ⇒ 合约已无成交（交割后交易所仍回零成交价）
        if zf >= 3:
            return ("BAR_ADVANCING_ALL_ZERO_VOLUME",
                    f"水位推进到 {r['head_1h_open_utc']} 但连续 {zf} 根零成交且 OHLC 全等 "
                    f"⇒ 合约已无真实成交（交易所侧停牌/交割后仍回冻结价），"
                    f"引擎按零成交质量闸门拒喂状态机")
        # 4) 行情正常，看快照覆盖
        if not r["snapshot_file"]:
            return ("OK_BAR_NO_SNAPSHOT",
                    "K 线正常；快照未覆盖（采集器只按有界集合刷新）")
        if not r["snapshot_fresh"]:
            return ("OK_BAR_SNAPSHOT_STALE",
                    f"K 线正常；快照陈旧 {r['snapshot_age_s']}s（曾进页首后被移出，遗留文件）")
        return ("OK", "K 线与快照均新鲜")

    buckets = {}
    for r in rows:
        code, why = classify(r)
        r["verdict"] = code
        r["verdict_reason"] = why
        buckets.setdefault(code, []).append(r["symbol"])

    archive = metrics_archive_state()

    result = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sampled_at_ms": t_sampled,
        "expected_1h_open_utc": iso(expected_open),
        "universe": {
            "source": "symbol_pool WHERE exchange='binance' AND market='um'",
            "count": len(uni),
            "with_1h_head": sum(1 for r in rows if r["head_1h_open_utc"]),
            "with_snapshot_file": sum(1 for r in rows if r["snapshot_file"]),
            "snapshot_fresh": sum(1 for r in rows if r["snapshot_fresh"]),
        },
        "exchange_info": exch_meta,
        "buckets": {k: {"count": len(v), "symbols": sorted(v)} for k, v in sorted(buckets.items())},
        "metrics_archive": archive,
        "fresh_threshold_s": FRESH_S,
    }
    with open(os.path.join(OUT, "pool_gap.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": result, "rows": rows}, f, ensure_ascii=False, indent=1)

    cols = ["symbol", "verdict", "pool_status", "exch_status", "exch_delivery_utc",
            "head_1h_open_utc", "lag_hours", "zero_flat_run",
            "snapshot_file", "snapshot_refreshed_at", "snapshot_age_s",
            "snapshot_fresh", "verdict_reason"]
    with open(os.path.join(OUT, "pool_gap.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["verdict"], x["symbol"])):
            w.writerow(r)

    print(json.dumps({
        "universe": result["universe"],
        "buckets": {k: v["count"] for k, v in result["buckets"].items()},
        "exchange_info_ok": exch_meta.get("ok"),
        "metrics_archive": {
            "dirs": archive.get("dirs"),
            "dir_mtime": archive.get("dir_mtime"),
            "BTC_last": (archive.get("sample", {}).get("BTCUSDT") or {}).get("last_event_time"),
        },
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

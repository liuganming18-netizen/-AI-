# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-F｜01 币池、退市与指数排除核对（只读）。

要回答的四件事
--------------
1. 「526」这个池是怎么来的，与数据目录/元数据表对不对得上；
2. 指数标的到底有几个、排除了几个、漏的那个有没有数据；
3. 已退市/已交割标的有没有历史 K 线（决定幸存者偏差能不能量化）；
4. 05-C 的 symbols 口径（528 / 814）与 04-A 的池（526）差在哪。

不改池、不补数据；只把偏差记清楚。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
CANDLES = ROOT / "数据" / "市场数据" / "parquet" / "candles"


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def main():
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # ── 1. 冷档目录 = 实际有 K 线归档的标的 ──────────────────────────────
    binance_um = sorted(p.name for p in (CANDLES / "binance" / "um").iterdir()
                        if p.is_dir())
    okx_swap = sorted(p.name for p in (CANDLES / "okx").iterdir()) if (
        CANDLES / "okx").exists() else []

    # ── 2. 元数据表：池 + 分类 ──────────────────────────────────────────
    pool_rows = [dict(r) for r in cur.execute(
        "SELECT exchange, market, status, contract_type, COUNT(*) n "
        "FROM symbol_pool GROUP BY 1,2,3,4")]
    cls_rows = [dict(r) for r in cur.execute(
        "SELECT exchange, market, instrument_category, in_active_range, "
        "COUNT(*) n FROM symbol_classification GROUP BY 1,2,3,4")]

    cls = {r["symbol"]: r for r in cur.execute(
        "SELECT symbol, instrument_category, in_active_range, excluded_reason, "
        "contract_type, snapshot_utc FROM symbol_classification "
        "WHERE exchange='binance' AND market='um'")}

    index_syms = sorted(s for s, r in cls.items()
                        if r["instrument_category"] == "INDEX")
    coin_active = sorted(s for s, r in cls.items()
                         if r["instrument_category"] == "COIN"
                         and r["in_active_range"] == 1)
    coin_inactive = sorted(s for s, r in cls.items()
                           if r["instrument_category"] == "COIN"
                           and r["in_active_range"] == 0)
    other_cats = {}
    for s, r in cls.items():
        c = r["instrument_category"]
        if c in ("COIN", "INDEX"):
            continue
        other_cats[c] = other_cats.get(c, 0) + 1

    # 冷档目录与分类的交叉
    dirs_set = set(binance_um)
    index_in_dirs = sorted(dirs_set & set(index_syms))
    index_missing = sorted(set(index_syms) - dirs_set)
    inactive_in_dirs = sorted(dirs_set & set(coin_inactive))
    active_missing_dirs = sorted(set(coin_active) - dirs_set)
    dirs_unclassified = sorted(dirs_set - set(cls))

    # ── 3. 已退市标的有多少历史 K 线（冷档 + 热库）──────────────────────
    # 冷档：目录都没有 ⇒ 0 行；这里给「目录数」作为可复核的最小证据
    # 热库：candles 视图里这些 symbol 是否有行
    hot_symbols = {r[0] for r in cur.execute(
        "SELECT DISTINCT symbol FROM candles WHERE exchange='binance' AND market='um'")}
    inactive_in_hot = sorted(set(coin_inactive) & hot_symbols)
    inactive_hot_detail = []
    for s in inactive_in_hot[:20]:
        row = cur.execute(
            "SELECT timeframe, COUNT(*) n, MIN(open_time) mn, MAX(open_time) mx "
            "FROM candles WHERE exchange='binance' AND market='um' AND symbol=? "
            "GROUP BY timeframe ORDER BY timeframe", (s,)).fetchone()
        # 逐周期更全，这里只取聚合
        r2 = cur.execute(
            "SELECT COUNT(*), MIN(open_time), MAX(open_time) FROM candles "
            "WHERE exchange='binance' AND market='um' AND symbol=?", (s,)).fetchone()
        inactive_hot_detail.append({"symbol": s, "rows": r2[0],
                                    "first_utc": iso(r2[1]), "last_utc": iso(r2[2])})

    # ── 4. 池分母的几种口径，逐一说清 ───────────────────────────────────
    denominators = {
        "冷档目录_binance_um": len(binance_um),
        "元数据_COIN_active": len(coin_active),
        "元数据_COIN_inactive_SETTLING": len(coin_inactive),
        "元数据_INDEX": len(index_syms),
        "元数据_其他类别(股票/商品/外汇/盘前)": sum(other_cats.values()),
        "04A_实际扫描池": len([s for s in binance_um if s not in ("ALLUSDT", "BTCDOMUSDT")]),
        "05C_symbols_口径": 528,
        "symbol_pool_全表": 814,
    }

    facts = {
        "generated_at_utc": iso(int(time.time() * 1000)),
        "cold_dirs": {
            "binance_um_count": len(binance_um),
            "okx_top_dirs": okx_swap,
            "binance_um_has_okx": bool(okx_swap),
        },
        "metadata": {
            "symbol_pool_rows": pool_rows,
            "classification_rows": cls_rows,
            "other_categories": other_cats,
        },
        "index_rule": {
            "index_symbols": index_syms,
            "in_cold_dirs": index_in_dirs,
            "excluded_by_04A": ["ALLUSDT", "BTCDOMUSDT"],
            "not_excluded_but_no_data": index_missing,
            "conclusion": ("指数共 3 个；04-A 只显式排除 2 个，第 3 个（无 K 线目录）"
                           "因无数据而未进入池 ⇒ 排除规则不完整但本次无实际影响"),
        },
        "delisted": {
            "count": len(coin_inactive),
            "reason_sample": [cls[s]["excluded_reason"] for s in coin_inactive[:5]],
            "in_cold_dirs": len(inactive_in_dirs),
            "in_hot_candles": len(inactive_in_hot),
            "hot_detail_sample": inactive_hot_detail,
            "delist_time_available": False,
            "delist_time_note": ("元数据只有上线时刻 onboard_time，没有下架/交割时刻字段；"
                                 "分类表的 excluded_reason 只写 status=SETTLING，"
                                 "所以「这些标的在观察窗口内是否还活着」无法从本地数据判定"),
        },
        "pool_cross_check": {
            "active_missing_dirs": active_missing_dirs,
            "dirs_unclassified": dirs_unclassified,
            "inactive_in_dirs": inactive_in_dirs,
        },
        "denominators": denominators,
        "survivorship": {
            "pool_used_by_04A": 526,
            "historical_union_upper_bound": 526 + len(coin_inactive),
            "excluded_fraction_pct": round(
                100.0 * len(coin_inactive) / (526 + len(coin_inactive)), 2),
            "direction": "对只做多策略，排除已退市标的会高估收益（幸存者偏差）",
            "magnitude": ("方向明确、大小不可量化：退市时刻未知，无法判断它们在窗口内"
                          "贡献过多少候选"),
        },
    }
    con.close()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "01_币池与退市.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "dirs": facts["cold_dirs"]["binance_um_count"],
        "index": facts["index_rule"],
        "delisted": {k: v for k, v in facts["delisted"].items()
                     if k not in ("reason_sample", "hot_detail_sample")},
        "denominators": denominators,
        "survivorship": facts["survivorship"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

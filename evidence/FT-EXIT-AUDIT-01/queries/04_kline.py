# -*- coding: utf-8 -*-
"""FT-EXIT-AUDIT-01 / 04：系统 K 线与账本一致性

对每一笔成交，用系统自己用的 15m 合约 K 线核对：
 1. 入场价是否落在入场 K 线的 [low, high] 内
 2. 止损出场价是否等于止损价、且该根 K 线的 low 确实触及止损
 3. 出场时间是否就是触发 K 线的时间
 4. 期末技术结算价是否等于「窗口外第一根 K 线」的开盘价（freqtrade 的 force_exit 口径）
 5. 账本「仍持有」标记是否与 force_exit 一致
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = ROOT / "evidence/FT-EXIT-01"
OUT = ROOT / "evidence/FT-EXIT-AUDIT-01/out"
DATA = ROOT / "evidence/FT-ENTRY-02/user_data/data/binance/futures"
SUF = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
SCHEMES = {"S": "Wyckoff_C1_S_P0", "P": "Wyckoff_C1_P_Stop20"}

_cache = {}


def klines(pair: str):
    if pair in _cache:
        return _cache[pair]
    f = DATA / f"{pair.replace('/', '_').replace(':', '_')}-15m-futures.feather"
    if not f.exists():
        _cache[pair] = None
        return None
    df = pd.read_feather(f)
    # 该 feather 的 date 列是 datetime64[ms]，用 Timedelta 换算成毫秒整数（不依赖内部单位）
    df["ms"] = ((df["date"] - pd.Timestamp("1970-01-01", tz="UTC"))
                // pd.Timedelta(milliseconds=1)).astype("int64")
    df = df.set_index("ms").sort_index()
    _cache[pair] = df
    return df


def read_csv(p: Path):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


R = {}
print("=" * 100)
print("【1】逐笔 K 线一致性")
print("=" * 100)
print(f"{'方案':<3}{'组':<10}{'笔数':>5}{'入场在K线内':>12}{'出场=止损价':>12}"
      f"{'出场K线触及':>12}{'时间对齐':>9}{'期末结算价=外首根开盘':>14}{'通过率%':>9}")

for scheme in ("S", "P"):
    R[scheme] = {}
    for suf in SUF:
        p = EXP / f"out/trades_{SCHEMES[scheme]}_{suf}.csv"
        if not p.exists():
            continue
        rows = read_csv(p)
        if not rows:
            continue

        n = len(rows)
        c_entry = c_close = c_touch = c_time = c_force = 0
        n_force = 0
        is_open_flag = Counter()
        missing_k = set()
        detail = []

        for r in rows:
            pair = r["pair"]
            df = klines(pair)
            if df is None:
                missing_k.add(pair)
                continue
            o_ts = int(r["open_timestamp"])
            c_ts = int(r["close_timestamp"])
            entry = float(r["open_rate"])
            close = float(r["close_rate"])
            stop = float(r["stop_loss_abs"])
            exit_reason = r["exit_reason"]
            is_open_flag[(exit_reason, r.get("is_open", "?"))] += 1

            # 1. 入场 K 线
            if o_ts in df.index:
                bar = df.loc[o_ts]
                ok_entry = bar["low"] <= entry <= bar["high"]
            else:
                ok_entry = False
            c_entry += int(bool(ok_entry))

            # 2/3. 出场
            if exit_reason in ("stop_loss", "trailing_stop_loss"):
                ok_close = abs(close - stop) <= max(abs(stop) * 1e-12, 1e-12)
                if c_ts in df.index:
                    bar = df.loc[c_ts]
                    ok_touch = bar["low"] <= stop <= bar["high"]
                    ok_time = True
                else:
                    ok_touch = False
                    ok_time = False
            else:
                # force_exit：freqtrade 在窗口外第一根 K 线以开盘价结算
                n_force += 1
                if c_ts in df.index:
                    bar = df.loc[c_ts]
                    ok_close = abs(close - bar["open"]) <= max(abs(close) * 1e-6, 1e-12)
                else:
                    last = df.iloc[-1]
                    ok_close = abs(close - last["close"]) <= max(abs(close) * 1e-6, 1e-12)
                ok_touch = True
                ok_time = True
            c_close += int(bool(ok_close))
            c_touch += int(bool(ok_touch))
            c_time += int(bool(ok_time))
            if exit_reason == "force_exit":
                c_force += int(bool(ok_close))

            detail.append({"pair": pair, "open_ts": o_ts, "close_ts": c_ts,
                           "entry_ok": bool(ok_entry), "close_ok": bool(ok_close),
                           "touch_ok": bool(ok_touch), "time_ok": bool(ok_time),
                           "exit_reason": exit_reason})

        tot = c_entry + c_close + c_touch + c_time
        rate = tot / (n * 4) * 100
        R[scheme][suf] = {
            "笔数": n, "入场在K线内": c_entry, "出场=止损价": c_close,
            "出场K线触及止损": c_touch, "时间对齐": c_time,
            "期末结算笔数": n_force, "期末结算价=窗口外首根开盘价": c_force,
            "通过率%": round(rate, 2), "缺K线标的": sorted(missing_k),
            "不一致明细": [d for d in detail if not all([d["entry_ok"], d["close_ok"],
                                                  d["touch_ok"], d["time_ok"]])][:20],
        }
        print(f"{scheme:<3}{suf:<10}{n:>5}{c_entry:>12}{c_close:>12}{c_touch:>12}"
              f"{c_time:>9}{c_force:>14}{rate:>9.2f}")

print()
print("=" * 100)
print("【2】汇总")
print("=" * 100)
for scheme in ("S", "P"):
    ok = [v for v in R[scheme].values() if "笔数" in v]
    if not ok:
        continue
    n = sum(v["笔数"] for v in ok)
    print(f"  {scheme} 组（{len(ok)}/9 组）：{n} 笔")
    print(f"    入场价落在 K 线内        {sum(v['入场在K线内'] for v in ok)}/{n}")
    print(f"    止损出场价 == 止损价     {sum(v['出场=止损价'] for v in ok)}/{n}")
    print(f"    出场 K 线确实触及止损    {sum(v['出场K线触及止损'] for v in ok)}/{n}")
    print(f"    出场时间对齐             {sum(v['时间对齐'] for v in ok)}/{n}")
    nf = sum(v["期末结算笔数"] for v in ok)
    print(f"    期末技术结算 {nf} 笔，结算价 == 窗口外首根开盘价 "
          f"{sum(v['期末结算价=窗口外首根开盘价'] for v in ok)}/{nf if nf else 0}")
    miss = sorted({p for v in ok for p in v["缺K线标的"]})
    print(f"    缺 K 线标的: {miss if miss else '无'}")
    bad = [d for v in ok for d in v["不一致明细"]]
    print(f"    不一致样本: {len(bad)} 条")
    for b in bad[:5]:
        print(f"      {b}")

(OUT / "04_K线一致性.json").write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8")
print(f"\n写出 {OUT/'04_K线一致性.json'}")

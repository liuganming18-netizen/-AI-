# -*- coding: utf-8 -*-
"""FT-DATA-01 步骤一：数据盘点与标的/窗口选择（只读）。

选择规则（不含任何收益信息）
────────────────────────────
1. 候选池 = 既有批次 `runs_2025aug100` 的 100 个标的
   （窗口 2025-08-01 00:00 ~ 2025-12-31 00:00 UTC）。
   选它只为「有旧引擎结果可逐笔比对」，不看其盈亏。
2. 导出区间 = [2025-06-25 00:00, 2025-12-31 00:00) UTC。
   起点比测试段早 37 天，覆盖 4h 的 220 根预热。
3. 缺口分两类，不混为一谈：
   - **全池共缺**：同一时刻被 ≥50% 有数据的标的同时缺 ⇒ 判为数据源侧缺口，
     保留、不补齐，两套引擎都会看到同一个洞。
   - **单标自身缺口**：只有该标的缺 ⇒ 判为该标的数据不合格，直接淘汰。
4. 资格 = 4h/1h/15m 单标自身缺口为 0 且全为已收盘 + OI 单标自身缺口为 0
   + 资金费有结算且 rate/mark 无空值。
5. 出两张清单：
   - 主清单：资格 + 旧引擎在测试段至少 1 笔成交（为了有逐笔可比对对象；
     只看有没有，不看盈亏）。
   - 备清单：只看资格，不看有无成交（供对方判断主清单是否有选择性偏倚）。
   两张都按「缺失总数 → 资金费间隔是否单一 → 标的名字」排序，取前 5。
   不引入成交额、收益、胜率等任何绩效字段。

输出：out/01_标的选择与完整性.csv、out/02_选择结论.csv、logs/00_inventory.json
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys

import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
PKG = os.path.join(ROOT, "evidence/FT-DATA-01")
OUT = os.path.join(PKG, "out")
LOGS = os.path.join(PKG, "logs")

CANDLES = os.path.join(ROOT, "数据/市场数据/parquet/candles/binance/um")
OI = os.path.join(ROOT, "数据/市场数据/parquet/oi/binance/um")
FUNDING = os.path.join(ROOT, "数据/市场数据/parquet/funding/binance/um")
INCLUDED = os.path.join(ROOT, "evidence/GATE-CLOSE-02/out/02_纳入清单.csv")
OLD_RUNS = os.path.join(ROOT, "回测日志/event_trend_v0_1/runs_2025aug100")

T0 = pd.Timestamp("2025-06-25 00:00:00", tz="UTC")
T_TEST = pd.Timestamp("2025-08-01 00:00:00", tz="UTC")
T1 = pd.Timestamp("2025-12-31 00:00:00", tz="UTC")

TF_STEP = {"4h": "4h", "1h": "1h", "15m": "15min", "5m": "5min"}

REC: list[dict] = []


def rec(name: str, ok: bool, detail) -> None:
    REC.append({"检查": name, "结果": "PASS" if ok else "FAIL", "明细": str(detail)})


def load_included() -> set[str]:
    with open(INCLUDED, encoding="utf-8-sig") as f:
        return {r["symbol"] for r in csv.DictReader(f)}


def grid(tf: str) -> pd.DatetimeIndex:
    """导出区间内该周期的完整时间格（左闭右开）。"""
    return pd.date_range(T0, T1, freq=TF_STEP[tf], inclusive="left")


def read_tf(symbol: str, tf: str) -> pd.DataFrame | None:
    p = os.path.join(CANDLES, symbol, tf, "2025.parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p, columns=["open_time", "is_closed"])
    t = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    m = (t >= T0) & (t < T1)
    return df.loc[m].assign(t=t[m])


def read_oi(symbol: str) -> pd.DataFrame | None:
    p = os.path.join(OI, symbol, "2025.parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p, columns=["ts", "open_interest"])
    t = pd.to_datetime(df["ts"], unit="ms", utc=True)
    m = (t >= T0) & (t < T1)
    return df.loc[m].assign(t=t[m])


def funding_stat(symbol: str) -> dict:
    p = os.path.join(FUNDING, f"{symbol}.parquet")
    if not os.path.exists(p):
        return {"结算次数": 0, "间隔组合": "", "空rate": 0, "空mark": 0,
                "间隔单一": False}
    df = pd.read_parquet(p, columns=["funding_time", "funding_rate", "mark_price"])
    t = pd.to_datetime(df["funding_time"], unit="ms", utc=True)
    m = (t >= T_TEST) & (t < T1)
    sub = df.loc[m].sort_values("funding_time")
    g = (sub["funding_time"].diff().dropna() / 3.6e6).round(3)
    mix = g.value_counts().sort_index()
    return {
        "结算次数": int(len(sub)),
        "间隔组合": "|".join(f"{k}h×{v}" for k, v in mix.items()),
        "空rate": int(sub["funding_rate"].isna().sum()),
        "空mark": int(sub["mark_price"].isna().sum()),
        "间隔单一": len(mix) == 1,
    }


def old_trades(symbol: str) -> int:
    n = 0
    for d in glob.glob(os.path.join(OLD_RUNS, f"run_*_{symbol}_*")):
        p = os.path.join(d, "trades.jsonl")
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            ts = r.get("entry_time_utc") or r.get("entry_time")
            if ts is None:
                n += 1
                continue
            try:
                tt = pd.Timestamp(str(ts), tz="UTC")
            except Exception:                                  # noqa: BLE001
                n += 1
                continue
            if T_TEST <= tt < T1:
                n += 1
    return n


def main() -> int:
    included = load_included()
    rec("纳入清单 526 个标的", len(included) == 526, len(included))

    cands = sorted({os.path.basename(p).split("_")[-2]
                    for p in glob.glob(os.path.join(OLD_RUNS, "run_*"))})
    rec("候选标的数（既有批次）", len(cands) >= 100, len(cands))
    not_in = [s for s in cands if s not in included]
    rec("候选全部在纳入清单内（无指数混入）", len(not_in) == 0, not_in)

    grids = {tf: grid(tf) for tf in ("4h", "1h", "15m", "5m")}
    exp = {tf: len(grids[tf]) for tf in grids}

    # ── 1. 逐标的取缺口集合 ──
    info: dict[str, dict] = {}
    for s in cands:
        d: dict = {"symbol": s, "miss": {}, "dup": {}, "unclosed": {}, "have": {}}
        ok_candle = True
        for tf in ("4h", "1h", "15m"):
            df = read_tf(s, tf)
            if df is None:
                d["miss"][tf] = set(grids[tf])
                d["dup"][tf] = 0
                d["unclosed"][tf] = 0
                d["have"][tf] = False
                ok_candle = False
                continue
            have = set(df["t"])
            d["miss"][tf] = set(grids[tf]) - have
            d["dup"][tf] = int(len(df) - df["open_time"].nunique())
            d["unclosed"][tf] = int((df["is_closed"] != 1).sum())
            d["have"][tf] = True
            if d["miss"][tf] or d["unclosed"][tf]:
                ok_candle = False
        o = read_oi(s)
        if o is None:
            d["miss"]["5m"] = set(grids["5m"])
            d["dup"]["5m"] = 0
            d["have"]["5m"] = False
        else:
            have = set(o["t"])
            d["miss"]["5m"] = set(grids["5m"]) - have
            d["dup"]["5m"] = int(len(o) - o["ts"].nunique())
            d["have"]["5m"] = True
            d["oi_null"] = int(o["open_interest"].isna().sum())
        d["candle_clean"] = ok_candle
        info[s] = d

    candle_clean = [s for s in cands if info[s]["candle_clean"]]
    rec("4h/1h/15m 无单标自身缺口的标的", len(candle_clean) > 0, len(candle_clean))

    # ── 2. 全池共缺：同一时刻被 ≥50% 有数据标的同时缺 ──
    pool_gap: dict[str, list] = {}
    for tf in ("4h", "1h", "15m", "5m"):
        base = candle_clean if tf != "5m" else candle_clean
        cnt: dict = {}
        for s in base:
            for ts in info[s]["miss"].get(tf, ()):  # type: ignore[union-attr]
                cnt[ts] = cnt.get(ts, 0) + 1
        thr = max(1, int(0.5 * len(base)))
        pool_gap[tf] = sorted(ts for ts, c in cnt.items() if c >= thr)
    rec("全池共缺时段已识别（4h）", True, len(pool_gap["4h"]))
    rec("全池共缺时段已识别（1h）", True, len(pool_gap["1h"]))
    rec("全池共缺时段已识别（15m）", True, len(pool_gap["15m"]))
    rec("全池共缺时段已识别（OI 5m）", True, len(pool_gap["5m"]))

    # ── 3. 单标自身缺口（扣掉全池共缺） ──
    rows = []
    for s in cands:
        d = info[s]
        own = {tf: sorted(set(d["miss"].get(tf, ())) - set(pool_gap[tf]))
               for tf in ("4h", "1h", "15m", "5m")}
        n_own = sum(len(v) for v in own.values())
        fu = funding_stat(s)
        tr = old_trades(s)
        ok = (d["candle_clean"] and n_own == 0
              and fu["结算次数"] > 0 and fu["空rate"] == 0 and fu["空mark"] == 0)
        rows.append({
            "symbol": s,
            "4h单标缺口": len(own["4h"]), "1h单标缺口": len(own["1h"]),
            "15m单标缺口": len(own["15m"]), "OI单标缺口": len(own["5m"]),
            "全池共缺4h": len(set(d["miss"].get("4h", ())) & set(pool_gap["4h"])),
            "全池共缺OI": len(set(d["miss"].get("5m", ())) & set(pool_gap["5m"])),
            "重复总数": sum(d["dup"].values()),
            "未收盘总数": sum(d["unclosed"].values()),
            "资金费结算次数": fu["结算次数"],
            "资金费间隔组合": fu["间隔组合"],
            "资金费间隔单一": "是" if fu["间隔单一"] else "否",
            "资金费空rate": fu["空rate"], "资金费空mark": fu["空mark"],
            "旧引擎成交笔数": tr,
            "单标缺口总数": n_own,
            "资格": "满足" if ok else "不满足",
            "不满足原因": ";".join(x for x in [
                "" if d["candle_clean"] else "K线有单标自身缺口或未收盘",
                "" if n_own == 0 else f"单标自身缺口{n_own}",
                "" if fu["结算次数"] > 0 else "无资金费结算",
                "" if fu["空rate"] == 0 else "资金费 rate 有空值",
                "" if fu["空mark"] == 0 else "资金费 mark 有空值",
            ] if x),
        })

    qualified = [r for r in rows if r["资格"] == "满足"]
    # 排序：缺口 → 间隔单一优先（降低转换歧义） → 名字
    qualified.sort(key=lambda r: (r["单标缺口总数"],
                                  r["资金费间隔单一"] != "是", r["symbol"]))
    main_pick = [r for r in qualified if r["旧引擎成交笔数"] >= 1][:5]
    alt_pick = qualified[:5]

    rec("满足数据资格的标的数 ≥5", len(qualified) >= 5, len(qualified))
    rec("主清单满 5 个（有旧成交可比对）", len(main_pick) == 5,
        [r["symbol"] for r in main_pick])
    rec("主清单每个标的旧引擎均有成交", all(r["旧引擎成交笔数"] >= 1 for r in main_pick),
        [(r["symbol"], r["旧引擎成交笔数"]) for r in main_pick])
    rec("主清单标的各不相同", len({r["symbol"] for r in main_pick}) == len(main_pick),
        len(main_pick))
    rec("备清单满 5 个", len(alt_pick) == 5, [r["symbol"] for r in alt_pick])

    cols = ["symbol", "4h单标缺口", "1h单标缺口", "15m单标缺口", "OI单标缺口",
            "全池共缺4h", "全池共缺OI", "重复总数", "未收盘总数", "资金费结算次数",
            "资金费间隔组合", "资金费间隔单一", "资金费空rate", "资金费空mark",
            "旧引擎成交笔数", "单标缺口总数", "资格", "不满足原因"]
    with open(os.path.join(OUT, "01_标的选择与完整性.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["资格"] != "满足",
                                             x["单标缺口总数"], x["symbol"])):
            w.writerow({k: r[k] for k in cols})

    with open(os.path.join(OUT, "02_选择结论.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["清单", "序号", "symbol", "配对名（Freqtrade）",
                    "旧引擎成交笔数", "资金费间隔", "入选理由（仅数据完整性）"])
        for i, r in enumerate(main_pick, 1):
            w.writerow(["主清单", i, r["symbol"],
                        f"{r['symbol'][:-4]}/USDT:USDT", r["旧引擎成交笔数"],
                        r["资金费间隔组合"],
                        "4h/1h/15m/OI 无单标自身缺口；资金费 rate/mark 无空值；"
                        "本窗口旧引擎有成交（只看有无，不看盈亏）"])
        for i, r in enumerate(alt_pick, 1):
            w.writerow(["备清单", i, r["symbol"],
                        f"{r['symbol'][:-4]}/USDT:USDT", r["旧引擎成交笔数"],
                        r["资金费间隔组合"],
                        "仅按数据完整性入选，不看有无成交（用于检验主清单是否选择性偏倚）"])

    summary = {
        "window_export_start_utc": str(T0),
        "window_test_start_utc": str(T_TEST),
        "window_test_end_utc_exclusive": str(T1),
        "expected_bars": exp,
        "candidates": len(rows),
        "candle_clean": len(candle_clean),
        "qualified": len(qualified),
        "pool_gap": {tf: [str(x) for x in v] for tf, v in pool_gap.items()},
        "main_pick": main_pick,
        "alt_pick": alt_pick,
        "checks": REC,
        "pass": sum(1 for r in REC if r["结果"] == "PASS"),
        "fail": sum(1 for r in REC if r["结果"] == "FAIL"),
    }
    with open(os.path.join(LOGS, "00_inventory.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    for r in REC:
        print(f"[{r['结果']}] {r['检查']} :: {r['明细'][:170]}")
    print("\n全池共缺：", {tf: len(v) for tf, v in pool_gap.items()})
    for tf, v in pool_gap.items():
        if v:
            print(f"  {tf}: {[str(x) for x in v][:6]}")
    print(f"\n候选 {len(rows)} / K线干净 {len(candle_clean)} / 合格 {len(qualified)}")
    print("主清单:", [(r["symbol"], r["旧引擎成交笔数"]) for r in main_pick])
    print("备清单:", [r["symbol"] for r in alt_pick])
    return 0 if summary["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
